"""Multi-user bot command."""

from __future__ import annotations

import os
import asyncio
from pathlib import Path

import typer

from ..db import get_db, User
from ..db.multiuser_handler import get_multiuser_handler, OnboardingMessage
from ..logging import get_logger, setup_logging
from ..telegram.client import TelegramClient
from ..telegram.types import TelegramIncomingMessage
from ..github.telegram_handlers import GitHubTelegramHandler
from ..github.workflow import GitHubWorkflow, WorkflowProgress
from ..github.actions_runner import GitHubActionsRunner, ActionResult
from ..sessions import get_session_manager
from ..workspaces import get_workspace_manager, WorkspaceConfig
from ..ui.keyboards import (
    MAIN_MENU_KEYBOARD,
    SETUP_API_KEYBOARD,
    SETTINGS_KEYBOARD,
    QUICK_ACTIONS_KEYBOARD,
    ONBOARD_WELCOME_KEYBOARD,
    ONBOARD_API_KEYBOARD,
    ONBOARD_GITHUB_KEYBOARD,
    make_model_keyboard,
    make_repos_keyboard,
)
from ..ui.messages import (
    welcome_message,
    setup_status_message,
    quick_start_message,
    api_key_setup_message,
    github_setup_message,
    ready_to_code_message,
    help_message,
    onboarding_api_prompt,
    onboarding_github_prompt,
    workspace_info_message,
)

logger = get_logger(__name__)

# Available models - Real API model names
AVAILABLE_MODELS = {
    "anthropic": [
        # Claude 4 series (Latest)
        ("claude-sonnet-4-20250514", "🟣 Claude Sonnet 4 (Best)"),
        ("claude-opus-4-20250514", "🧠 Claude Opus 4.5 (Most capable)"),
        # Claude 3 series
        ("claude-3-opus-20240229", "💎 Claude 3 Opus"),
        ("claude-3-sonnet-20240229", "⚡ Claude 3 Sonnet (Fast)"),
    ],
    "openai": [
        # GPT-5 series (Latest - Best for coding)
        ("gpt-5.2", "🌟 GPT-5.2 (Best for coding)"),
        ("gpt-5-mini", "⚡ GPT-5 Mini (Fast)"),
        ("gpt-5-nano", "🚀 GPT-5 Nano (Fastest)"),
        # GPT-4o series
        ("gpt-4o", "💚 GPT-4o"),
        ("gpt-4o-mini", "💨 GPT-4o Mini"),
        # o-series (Reasoning)
        ("o3", "🧠 o3 (Deep reasoning)"),
        ("o1", "🔮 o1 (Reasoning)"),
    ],
}


def get_model_selection_message(user: User) -> OnboardingMessage:
    """Get model selection message - shows ALL models with availability."""
    current_model = user.settings.default_model if hasattr(user.settings, 'default_model') else "gpt-5.2"
    
    has_anthropic = bool(user.api_keys.anthropic_key)
    has_openai = bool(user.api_keys.openai_key)
    
    buttons = []
    
    # Always show Anthropic models
    if has_anthropic:
        buttons.append([{"text": "── 🟣 ANTHROPIC (Claude) ──", "callback_data": "noop"}])
    else:
        buttons.append([{"text": "── 🔒 ANTHROPIC (Need key) ──", "callback_data": "setup:anthropic"}])
    
    for model_id, model_name in AVAILABLE_MODELS["anthropic"]:
        icon = "✅" if model_id == current_model else "⬜"
        if has_anthropic:
            buttons.append([{
                "text": f"{icon} {model_name}",
                "callback_data": f"model:{model_id}",
            }])
        else:
            buttons.append([{
                "text": f"🔒 {model_name}",
                "callback_data": "setup:anthropic",
            }])
    
    # Always show OpenAI models
    if has_openai:
        buttons.append([{"text": "── 🟢 OPENAI (GPT/Codex) ──", "callback_data": "noop"}])
    else:
        buttons.append([{"text": "── 🔒 OPENAI (Need key) ──", "callback_data": "setup:openai"}])
    
    for model_id, model_name in AVAILABLE_MODELS["openai"]:
        icon = "✅" if model_id == current_model else "⬜"
        if has_openai:
            buttons.append([{
                "text": f"{icon} {model_name}",
                "callback_data": f"model:{model_id}",
            }])
        else:
            buttons.append([{
                "text": f"🔒 {model_name}",
                "callback_data": "setup:openai",
            }])
    
    # Add back button
    buttons.append([{"text": "⬅️ Back", "callback_data": "action:settings"}])
    
    if not has_anthropic and not has_openai:
        return OnboardingMessage(
            text="❌ No API keys configured. Add a key to unlock models!",
            buttons=buttons,
        )
    
    return OnboardingMessage(
        text=f"""🤖 <b>Select AI Model</b>

Current: <code>{current_model}</code>

Choose your preferred model:""",
        buttons=buttons,
    )

app = typer.Typer()


async def send_onboarding_message(
    client: TelegramClient,
    chat_id: int,
    msg: OnboardingMessage,
    edit_message_id: int | None = None,
) -> None:
    """Send an onboarding-style message with buttons."""
    reply_markup = None
    if msg.buttons:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": btn["text"], **({k: v for k, v in btn.items() if k != "text"})}
                    for btn in row
                ]
                for row in msg.buttons
            ]
        }
    
    if edit_message_id:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=msg.text,
            parse_mode=msg.parse_mode,
            reply_markup=reply_markup,
        )
    else:
        await client.send_message(
            chat_id=chat_id,
            text=msg.text,
            parse_mode=msg.parse_mode,
            reply_markup=reply_markup,
        )


async def handle_user_message(
    user: User,
    msg: TelegramIncomingMessage,
    client: TelegramClient,
    github_handler: GitHubTelegramHandler | None = None,
) -> None:
    """Handle a message from an onboarded user."""
    text = msg.text or ""
    chat_id = msg.chat.id if msg.chat else user.telegram_id
    
    handler = get_multiuser_handler()
    text_lower = text.lower().strip()
    
    # Handle /start - Welcome menu for returning users
    if text_lower in ("/start", "/menu", "/home"):
        await handle_start_command(user, chat_id, client)
        return
    
    # Handle GitHub commands (always available, no API keys required)
    if text_lower in ("/github", "/gh", "/github disconnect"):
        if github_handler:
            response = await github_handler.handle_github_command(user)
            await send_onboarding_message(client, chat_id, response)
        else:
            await client.send_message(
                chat_id=chat_id,
                text="⚠️ GitHub integration not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.",
                parse_mode="HTML",
            )
        return
    
    if text_lower in ("/repos", "/repositories"):
        if github_handler:
            response = await github_handler.handle_repos_command(user)
            await send_onboarding_message(client, chat_id, response)
        return
    
    if text_lower.startswith("/repo "):
        repo_name = text[6:].strip()
        if github_handler and repo_name:
            response = await github_handler.handle_repo_selection(user, repo_name)
            await send_onboarding_message(client, chat_id, response)
        return
    
    if text_lower in ("/tree", "/files", "/ls"):
        if github_handler:
            response = await github_handler.handle_tree_command(user)
            await send_onboarding_message(client, chat_id, response)
        return
    
    if text_lower in ("/commits", "/log", "/history"):
        if github_handler:
            response = await github_handler.handle_commits_command(user)
            await send_onboarding_message(client, chat_id, response)
        return
    
    if text_lower in ("/model", "/models"):
        response = get_model_selection_message(user)
        await send_onboarding_message(client, chat_id, response)
        return
    
    if text_lower.startswith("/run "):
        command = text[5:].strip()
        if not command:
            await client.send_message(
                chat_id=chat_id,
                text="Usage: <code>/run npm test</code>",
                parse_mode="HTML",
            )
            return
        await handle_run_command(user, command, chat_id, client)
        return
    
    if text_lower in ("/run",):
        await client.send_message(
            chat_id=chat_id,
            text="""⚡ <b>Run Commands via GitHub Actions</b>

Usage: <code>/run &lt;command&gt;</code>

Examples:
• <code>/run npm test</code>
• <code>/run npm run build</code>
• <code>/run pip install -r requirements.txt</code>
• <code>/run python -m pytest</code>
• <code>/run ls -la</code>

The command runs in your repo via GitHub Actions (sandboxed & secure).""",
            parse_mode="HTML",
        )
        return
    
    if text_lower in ("/session", "/context"):
        session_mgr = get_session_manager()
        summary = session_mgr.get_session_summary(user)
        await client.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")
        return
    
    if text_lower in ("/clear", "/new", "/reset"):
        session_mgr = get_session_manager()
        session_mgr.clear_session(user)
        await client.send_message(
            chat_id=chat_id,
            text="🧹 <b>Session cleared!</b>\n\nStarting fresh conversation.",
            parse_mode="HTML",
        )
        return
    
    if text_lower in ("/history",):
        session_mgr = get_session_manager()
        session = session_mgr.get_or_create_session(user)
        
        if not session.messages:
            await client.send_message(
                chat_id=chat_id,
                text="📭 No conversation history yet.",
                parse_mode="HTML",
            )
            return
        
        # Show last 10 messages
        history_lines = ["📜 <b>Conversation History</b>\n"]
        for msg in session.messages[-10:]:
            icon = "👤" if msg.role == "user" else "🤖"
            content = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
            # Escape HTML
            content = content.replace("<", "&lt;").replace(">", "&gt;")
            history_lines.append(f"{icon} {content}\n")
        
        await client.send_message(
            chat_id=chat_id,
            text="\n".join(history_lines),
            parse_mode="HTML",
        )
        return
    
    # Workspace commands
    if text_lower in ("/workspace", "/ws"):
        await handle_workspace_command(user, chat_id, client)
        return
    
    if text_lower in ("/workspace destroy", "/ws destroy", "/ws stop"):
        ws_mgr = get_workspace_manager()
        destroyed = await ws_mgr.destroy_workspace(user)
        if destroyed:
            await client.send_message(
                chat_id=chat_id,
                text="🗑️ Workspace destroyed.",
                parse_mode="HTML",
            )
        else:
            await client.send_message(
                chat_id=chat_id,
                text="❌ No active workspace to destroy.",
                parse_mode="HTML",
            )
        return
    
    if text_lower.startswith("/exec ") or text_lower.startswith("/x "):
        prefix_len = 6 if text_lower.startswith("/exec ") else 3
        command = text[prefix_len:].strip()
        if not command:
            await client.send_message(
                chat_id=chat_id,
                text="Usage: <code>/exec npm test</code>",
                parse_mode="HTML",
            )
            return
        await handle_exec_command(user, command, chat_id, client)
        return
    
    # PR commands
    if text_lower in ("/pr", "/prs"):
        await handle_pr_list_command(user, chat_id, client)
        return
    
    if text_lower.startswith("/merge"):
        # /merge or /merge 123
        parts = text.split()
        pr_number = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        await handle_merge_command(user, pr_number, chat_id, client)
        return
    
    if text_lower.startswith("/continue"):
        # /continue or /continue 123
        parts = text.split()
        pr_number = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        await handle_continue_command(user, pr_number, chat_id, client)
        return
    
    if text_lower in ("/stop", "/new"):
        # Stop continuing on existing PR, create new PRs again
        clear_active_pr_branch(user.telegram_id)
        await client.send_message(
            chat_id=chat_id,
            text="✅ <b>New PR mode</b>\n\nYour next requests will create new PRs.",
            parse_mode="HTML",
        )
        return
    
    # Get the user's selected model (not engine)
    model = getattr(user.settings, 'default_model', 'gpt-5.2')
    prompt = text
    
    # Check for model override commands
    if text_lower.startswith("/claude "):
        model = "claude-sonnet-4-20250514"
        prompt = text[8:].strip()
    elif text_lower.startswith("/gpt "):
        model = "gpt-5.2"
        prompt = text[5:].strip()
    elif text_lower.startswith("/o3 "):
        model = "o3"
        prompt = text[4:].strip()
    elif text_lower.startswith("/help"):
        help_text = """🎭 <b>Amadeus Pocket - Commands</b>

<b>GitHub:</b>
• <code>/github</code> - Connect/manage GitHub
• <code>/repos</code> - List your repositories
• <code>/repo owner/name</code> - Select a repository
• <code>/tree</code> - View repo file structure
• <code>/commits</code> - View recent commits

<b>Pull Requests:</b>
• <code>/pr</code> - List open PRs
• <code>/continue</code> - Continue on latest PR
• <code>/continue 123</code> - Continue on PR #123
• <code>/merge</code> - Merge latest Amadeus PR
• <code>/merge 123</code> - Merge PR #123
• <code>/stop</code> - Stop continuing, create new PRs

<b>Sessions:</b>
• <code>/session</code> - View current session info
• <code>/history</code> - View conversation history
• <code>/clear</code> - Clear session & start fresh

<b>AI Models:</b>
• <code>/model</code> - Choose AI model (Claude, GPT-4, etc.)

<b>Settings:</b>
• <code>/settings</code> - Manage API keys
• <code>/start</code> - Restart onboarding

<b>Usage:</b>
Just send a message to code on your selected repo!
The AI remembers your conversation for context."""
        await client.send_message(chat_id=chat_id, text=help_text, parse_mode="HTML")
        return
    elif text_lower.startswith("/"):
        await client.send_message(
            chat_id=chat_id,
            text="Unknown command. Use /help to see available commands.",
            parse_mode="HTML",
        )
        return
    
    # Check if user has API keys configured (MANDATORY)
    if not user.has_any_api_key():
        await client.send_message(
            chat_id=chat_id,
            text="""⚠️ <b>API Key Required</b>

To use Amadeus Pocket, you need to configure an API key.

Use /settings to add your:
• 🟣 Anthropic API key (for Claude)
• 🟢 OpenAI API key (for GPT-4)

Your API key is stored securely and only used for your requests.""",
            parse_mode="HTML",
        )
        return
    
    # Check if user has GitHub connected and repo selected
    if user.github.is_connected and user.github.selected_repo:
        # Use GitHub workflow
        await handle_github_workflow(user, prompt, model, chat_id, client, handler)
    else:
            await client.send_message(
                chat_id=chat_id,
            text=f"📝 <b>Received:</b> {prompt[:200]}...\n\n"
                 f"💡 Connect GitHub with /github to start coding on your repos!",
            parse_mode="HTML",
        )


async def handle_start_command(
    user: User,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /start - Show welcome menu with buttons."""
    has_api_key = user.has_any_api_key()
    has_github = user.github.is_connected
    has_repo = bool(user.github.selected_repo)
    
    # Fully setup - show main menu
    if has_api_key and has_github and has_repo:
        text = quick_start_message(user)
        keyboard = MAIN_MENU_KEYBOARD
    
    # Missing API key - show onboarding
    elif not has_api_key:
        text = welcome_message(user)
        keyboard = ONBOARD_WELCOME_KEYBOARD
    
    # Has API but no GitHub
    elif not has_github:
        text = setup_status_message(user) + "\n\n" + github_setup_message()
        keyboard = ONBOARD_GITHUB_KEYBOARD
    
    # Has API + GitHub but no repo
    else:
        text = setup_status_message(user)
        text += "\n\n👇 <b>Select a repository to continue</b>"
        keyboard = {"inline_keyboard": [
            [{"text": "📁 Select Repository", "callback_data": "action:repos"}],
            [{"text": "🏠 Main Menu", "callback_data": "action:menu"}],
        ]}
    
    await client.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_action_callback(
    user: User,
    action: str,
    chat_id: int,
    client: TelegramClient,
    github_handler: GitHubTelegramHandler | None,
    msg_id: int | None = None,
) -> None:
    """Handle action: callbacks - navigation and quick actions."""
    
    if action == "menu":
        # Show main menu
        await handle_start_command(user, chat_id, client)
    
    elif action == "code":
        # Prompt to start coding
        if not user.github.selected_repo:
            await client.send_message(
                chat_id=chat_id,
                text="⚠️ Select a repository first!",
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "📁 Select Repo", "callback_data": "action:repos"}],
                ]},
            )
        else:
            await client.send_message(
                chat_id=chat_id,
                text=f"""💻 <b>Ready to Code!</b>

📁 <code>{user.github.selected_repo}</code>

<b>Just type what you want to build:</b>

<i>Examples:</i>
• "Add user authentication with JWT"
• "Create a REST API for products"
• "Fix the bug in the login form"
• "Add dark mode to the settings page"

I'll analyze your repo and create a PR! 🚀""",
                parse_mode="HTML",
                reply_markup=QUICK_ACTIONS_KEYBOARD,
            )
    
    elif action == "repos":
        if github_handler:
            response = await github_handler.handle_repos_command(user)
            await send_onboarding_message(client, chat_id, response)
    
    elif action == "tree":
        if github_handler:
            response = await github_handler.handle_tree_command(user)
            await send_onboarding_message(client, chat_id, response)
    
    elif action == "commits":
        if github_handler:
            response = await github_handler.handle_commits_command(user)
            await send_onboarding_message(client, chat_id, response)
    
    elif action == "workspace":
        await handle_workspace_command(user, chat_id, client)
    
    elif action == "run":
        await client.send_message(
            chat_id=chat_id,
            text="""⚡ <b>Run Commands</b>

Use <code>/run</code> followed by your command:

<b>Examples:</b>
• <code>/run npm test</code>
• <code>/run npm run build</code>
• <code>/run pip install -r requirements.txt</code>
• <code>/run python -m pytest</code>

Commands run securely via GitHub Actions.""",
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "🏠 Main Menu", "callback_data": "action:menu"}],
            ]},
        )
    
    elif action == "settings":
        await client.send_message(
            chat_id=chat_id,
            text=f"""⚙️ <b>Settings</b>

<b>Current Configuration:</b>
🔑 API Key: {"✅ Set" if user.has_any_api_key() else "❌ Not set"}
🐙 GitHub: {"✅ Connected" if user.github.is_connected else "❌ Not connected"}
📁 Repo: {user.github.selected_repo or "None"}
🤖 Model: <code>{getattr(user.settings, 'default_model', 'claude-3-5-sonnet')}</code>""",
            parse_mode="HTML",
            reply_markup=SETTINGS_KEYBOARD,
        )
    
    elif action == "help":
        await client.send_message(
            chat_id=chat_id,
            text=help_message(),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "🏠 Main Menu", "callback_data": "action:menu"}],
            ]},
        )
    
    elif action == "all_models":
        # Show full model list from both providers
        response = get_model_selection_message(user)
        await client.send_message(
            chat_id=chat_id,
            text=response.text,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": response.buttons} if response.buttons else None,
        )


async def handle_onboard_callback(
    user: User,
    step: str,
    chat_id: int,
    client: TelegramClient,
    github_handler: GitHubTelegramHandler | None,
    msg_id: int | None = None,
) -> None:
    """Handle onboard: callbacks - onboarding flow."""
    from ..db.multiuser_handler import get_multiuser_handler
    handler = get_multiuser_handler()
    
    if step == "start":
        # Show API key selection
        await client.send_message(
            chat_id=chat_id,
            text=api_key_setup_message(),
            parse_mode="HTML",
            reply_markup=ONBOARD_API_KEYBOARD,
        )
    
    elif step == "api_anthropic":
        # Prompt for Anthropic key
        handler.set_awaiting_api_key(user.telegram_id, "anthropic")
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_api_prompt("anthropic"),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅️ Back", "callback_data": "onboard:start"}],
            ]},
        )
    
    elif step == "api_openai":
        # Prompt for OpenAI key
        handler.set_awaiting_api_key(user.telegram_id, "openai")
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_api_prompt("openai"),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅️ Back", "callback_data": "onboard:start"}],
            ]},
        )
    
    elif step == "api_help":
        # Help getting API key
        await client.send_message(
            chat_id=chat_id,
            text="""🆘 <b>Getting an API Key</b>

<b>Option 1: Anthropic (Recommended)</b>
1. Go to <a href="https://console.anthropic.com/">console.anthropic.com</a>
2. Create an account
3. Go to API Keys → Create Key
4. Copy the key (starts with <code>sk-ant-</code>)

<b>Option 2: OpenAI</b>
1. Go to <a href="https://platform.openai.com/">platform.openai.com</a>
2. Create an account
3. Go to API Keys → Create new secret key
4. Copy the key (starts with <code>sk-</code>)

💡 <i>Both offer free credits for new users!</i>""",
            parse_mode="HTML",
            reply_markup=ONBOARD_API_KEYBOARD,
        )
    
    elif step == "github":
        # Prompt for GitHub token
        if github_handler:
            github_handler.set_awaiting_token(user.telegram_id)
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_github_prompt(),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⏭️ Skip for now", "callback_data": "onboard:skip_github"}],
            ]},
        )
    
    elif step == "skip_github":
        # Skip GitHub for now
        await client.send_message(
            chat_id=chat_id,
            text="""✅ <b>Setup Complete!</b>

You can connect GitHub later with <code>/github</code>.

For now, you can:
• Chat with AI about coding
• Get code suggestions
• Learn about your projects

<i>Connect GitHub to push code to repos!</i>""",
            parse_mode="HTML",
            reply_markup=MAIN_MENU_KEYBOARD,
        )


async def handle_setup_callback(
    user: User,
    setup_type: str,
    chat_id: int,
    client: TelegramClient,
    github_handler: GitHubTelegramHandler | None,
    msg_id: int | None = None,
) -> None:
    """Handle setup: callbacks - API key and GitHub setup."""
    from ..db.multiuser_handler import get_multiuser_handler
    handler = get_multiuser_handler()
    
    if setup_type == "anthropic":
        handler.set_awaiting_api_key(user.telegram_id, "anthropic")
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_api_prompt("anthropic"),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅️ Cancel", "callback_data": "action:settings"}],
            ]},
        )
    
    elif setup_type == "openai":
        handler.set_awaiting_api_key(user.telegram_id, "openai")
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_api_prompt("openai"),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅️ Cancel", "callback_data": "action:settings"}],
            ]},
        )
    
    elif setup_type == "github":
        if github_handler:
            github_handler.set_awaiting_token(user.telegram_id)
        await client.send_message(
            chat_id=chat_id,
            text=onboarding_github_prompt(),
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅️ Cancel", "callback_data": "action:settings"}],
            ]},
        )


async def handle_settings_callback(
    user: User,
    setting: str,
    chat_id: int,
    client: TelegramClient,
    github_handler: GitHubTelegramHandler | None,
    msg_id: int | None = None,
) -> None:
    """Handle settings: callbacks."""
    
    if setting == "keys":
        await client.send_message(
            chat_id=chat_id,
            text=api_key_setup_message(),
            parse_mode="HTML",
            reply_markup=SETUP_API_KEYBOARD,
        )
    
    elif setting == "github":
        if user.github.is_connected:
            await client.send_message(
                chat_id=chat_id,
                text=f"""🐙 <b>GitHub Connected</b>

👤 <code>{user.github.username}</code>
📁 Selected: <code>{user.github.selected_repo or 'None'}</code>""",
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "📁 Change Repo", "callback_data": "action:repos"}],
                    [{"text": "🔌 Disconnect", "callback_data": "github_disconnect"}],
                    [{"text": "⬅️ Back", "callback_data": "action:settings"}],
                ]},
            )
        else:
            await client.send_message(
                chat_id=chat_id,
                text=github_setup_message(),
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "🔗 Connect GitHub", "callback_data": "setup:github"}],
                    [{"text": "⬅️ Back", "callback_data": "action:settings"}],
                ]},
            )
    
    elif setting == "model":
        has_anthropic = bool(user.api_keys.anthropic_key)
        has_openai = bool(user.api_keys.openai_key)
        
        current = getattr(user.settings, 'default_model', 'gpt-5.2')
        
        await client.send_message(
            chat_id=chat_id,
            text=f"""🤖 <b>AI Model Selection</b>

Current: <code>{current}</code>

Choose your preferred model:""",
            parse_mode="HTML",
            reply_markup=make_model_keyboard(has_anthropic, has_openai),
        )
    
    elif setting == "session":
        session_mgr = get_session_manager()
        summary = session_mgr.get_session_summary(user)
        await client.send_message(
            chat_id=chat_id,
            text=summary,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [
                [{"text": "🧹 Clear Session", "callback_data": "session:clear"}],
                [{"text": "⬅️ Back", "callback_data": "action:settings"}],
            ]},
        )


async def handle_pr_list_command(
    user: User,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /pr - List open pull requests."""
    if not user.github.is_connected or not user.github.selected_repo:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Please connect GitHub and select a repo first.\nUse /github to get started.",
            parse_mode="HTML",
        )
        return
    
    from ..github.client import GitHubClient
    
    owner, repo_name = user.github.selected_repo.split("/")
    
    async with GitHubClient(user.github.access_token) as gh:
        prs = await gh.list_pull_requests(owner, repo_name, state="open")
    
    if not prs:
        await client.send_message(
            chat_id=chat_id,
            text=f"📭 <b>No open PRs</b>\n\n📁 Repo: <code>{user.github.selected_repo}</code>",
            parse_mode="HTML",
        )
        return
    
    # Build PR list with buttons
    text = f"📋 <b>Open Pull Requests</b>\n📁 Repo: <code>{user.github.selected_repo}</code>\n\n"
    
    buttons = []
    for pr in prs[:5]:  # Show max 5 PRs
        pr_num = pr["number"]
        title = pr["title"][:40] + "..." if len(pr["title"]) > 40 else pr["title"]
        user_login = pr["user"]["login"]
        
        text += f"<b>#{pr_num}</b> {title}\n"
        text += f"   by @{user_login}\n\n"
        
        # Add buttons for each PR
        buttons.append([
            {"text": f"📝 Continue #{pr_num}", "callback_data": f"pr:continue:{pr_num}"},
            {"text": f"✅ Merge #{pr_num}", "callback_data": f"pr:merge:{pr_num}"},
        ])
    
    buttons.append([{"text": "⬅️ Back", "callback_data": "action:menu"}])
    
    await client.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup={"inline_keyboard": buttons},
    )


async def handle_merge_command(
    user: User,
    pr_number: int | None,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /merge - Merge a pull request."""
    if not user.github.is_connected or not user.github.selected_repo:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Please connect GitHub and select a repo first.",
            parse_mode="HTML",
        )
        return
    
    from ..github.client import GitHubClient
    
    owner, repo_name = user.github.selected_repo.split("/")
    
    async with GitHubClient(user.github.access_token) as gh:
        # If no PR number, get the most recent Amadeus PR
        if pr_number is None:
            prs = await gh.list_pull_requests(owner, repo_name, state="open")
            amadeus_prs = [pr for pr in prs if pr["head"]["ref"].startswith("amadeus/")]
            
            if not amadeus_prs:
                await client.send_message(
                    chat_id=chat_id,
                    text="❌ No open Amadeus PRs found.\n\nUse <code>/pr</code> to see all PRs.",
                    parse_mode="HTML",
                )
                return
            
            pr_number = amadeus_prs[0]["number"]
        
        # Merge the PR
        success, message = await gh.merge_pull_request(owner, repo_name, pr_number)
    
    if success:
        await client.send_message(
            chat_id=chat_id,
            text=f"✅ <b>PR #{pr_number} Merged!</b>\n\n{message}",
            parse_mode="HTML",
        )
    else:
        await client.send_message(
            chat_id=chat_id,
            text=f"❌ <b>Merge Failed</b>\n\nPR #{pr_number}: {message}",
            parse_mode="HTML",
        )


# Store the active PR branch for users who want to continue
_active_pr_branches: dict[int, tuple[str, int]] = {}  # user_id -> (branch_name, pr_number)


async def handle_continue_command(
    user: User,
    pr_number: int | None,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /continue - Continue working on an existing PR."""
    if not user.github.is_connected or not user.github.selected_repo:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Please connect GitHub and select a repo first.",
            parse_mode="HTML",
        )
        return
    
    from ..github.client import GitHubClient
    
    owner, repo_name = user.github.selected_repo.split("/")
    
    async with GitHubClient(user.github.access_token) as gh:
        # If no PR number, get the most recent Amadeus PR
        if pr_number is None:
            prs = await gh.list_pull_requests(owner, repo_name, state="open")
            amadeus_prs = [pr for pr in prs if pr["head"]["ref"].startswith("amadeus/")]
            
            if not amadeus_prs:
                await client.send_message(
                    chat_id=chat_id,
                    text="❌ No open Amadeus PRs found.\n\nStart a new request to create one!",
                    parse_mode="HTML",
                )
                return
            
            pr = amadeus_prs[0]
            pr_number = pr["number"]
        else:
            pr = await gh.get_pull_request(owner, repo_name, pr_number)
            if not pr:
                await client.send_message(
                    chat_id=chat_id,
                    text=f"❌ PR #{pr_number} not found.",
                    parse_mode="HTML",
                )
                return
    
    # Store the active branch for this user
    branch_name = pr["head"]["ref"]
    _active_pr_branches[user.telegram_id] = (branch_name, pr_number)
    
    title = pr["title"][:50] + "..." if len(pr["title"]) > 50 else pr["title"]
    
    await client.send_message(
        chat_id=chat_id,
        text=f"""✅ <b>Continuing PR #{pr_number}</b>

📁 Repo: <code>{user.github.selected_repo}</code>
🌿 Branch: <code>{branch_name}</code>
📝 Title: {title}

<i>Your next requests will add commits to this PR instead of creating new ones.</i>

Use <code>/stop</code> to create new PRs again.""",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": [
            [{"text": "🔗 View PR", "url": pr["html_url"]}],
            [{"text": "⏹️ Stop Continuing", "callback_data": "pr:stop"}],
        ]},
    )


def get_active_pr_branch(user_id: int) -> tuple[str, int] | None:
    """Get the active PR branch for a user if they're in 'continue' mode."""
    return _active_pr_branches.get(user_id)


def clear_active_pr_branch(user_id: int) -> None:
    """Clear the active PR branch for a user."""
    _active_pr_branches.pop(user_id, None)


async def handle_workspace_command(
    user: User,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /workspace command - create/manage ephemeral workspace."""
    import os
    
    # Check if Fly.io is configured
    if not os.environ.get("FLY_API_TOKEN") or not os.environ.get("FLY_APP_NAME"):
        await client.send_message(
            chat_id=chat_id,
            text="""🏗️ <b>Workspaces</b>

Ephemeral coding environments are not yet configured.

<i>Fly.io integration coming soon!</i>

In the meantime, use:
• <code>/run &lt;cmd&gt;</code> - Run commands via GitHub Actions""",
            parse_mode="HTML",
        )
        return
        
    ws_mgr = get_workspace_manager()
    
    # Check for existing workspace
    info = await ws_mgr.get_workspace_info(user)
    
    if info and not info["is_expired"]:
        await client.send_message(
            chat_id=chat_id,
            text=f"""🖥️ <b>Active Workspace</b>

📁 Repo: <code>{info['repo'] or 'None'}</code>
⏱️ Expires in: {info['expires_in_minutes']} minutes
🔄 Status: {info['status']}

<b>Commands:</b>
• <code>/exec &lt;cmd&gt;</code> - Run a command
• <code>/workspace destroy</code> - Destroy workspace""",
            parse_mode="HTML",
        )
        return
    
    if not user.github.is_connected:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Connect GitHub first with /github",
            parse_mode="HTML",
        )
        return
    
    # Create new workspace
    msg = await client.send_message(
        chat_id=chat_id,
        text="🏗️ <b>Creating workspace...</b>\n\n⏳ Provisioning machine...",
        parse_mode="HTML",
    )
    
    async def on_progress(message: str, percentage: int):
        try:
            progress_bar = "▓" * (percentage // 10) + "░" * (10 - percentage // 10)
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=msg.message_id,
                text=f"🏗️ <b>{message}</b>\n\n[{progress_bar}] {percentage}%",
                parse_mode="HTML",
            )
        except Exception:
            pass
    
    workspace = await ws_mgr.get_or_create_workspace(
        user,
        config=WorkspaceConfig(
            cpus=1,
            memory_mb=512,
            timeout_minutes=30,
        ),
        on_progress=on_progress,
    )
    
    if workspace:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=f"""✅ <b>Workspace Ready!</b>

📁 Repo: <code>{workspace.repo or 'None'}</code>
⏱️ Expires in: 30 minutes
🔄 Status: ready

<b>Usage:</b>
• <code>/exec npm install</code>
• <code>/exec npm test</code>
• <code>/exec ls -la</code>
• <code>/x python --version</code> (short form)

Full project context available in /workspace""",
            parse_mode="HTML",
        )
    else:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text="❌ <b>Failed to create workspace</b>\n\nPlease try again later.",
            parse_mode="HTML",
        )


async def handle_exec_command(
    user: User,
    command: str,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /exec command - run in ephemeral workspace."""
    import os
    
    # Check if Fly.io is configured
    if not os.environ.get("FLY_API_TOKEN") or not os.environ.get("FLY_APP_NAME"):
        await client.send_message(
            chat_id=chat_id,
            text="❌ Workspaces not configured. Use <code>/run</code> for GitHub Actions instead.",
            parse_mode="HTML",
        )
        return
    
    ws_mgr = get_workspace_manager()
    info = await ws_mgr.get_workspace_info(user)
    
    if not info or info["is_expired"]:
        await client.send_message(
            chat_id=chat_id,
            text="❌ No active workspace. Use <code>/workspace</code> to create one first.",
            parse_mode="HTML",
        )
        return
    
    # Send initial message
    msg = await client.send_message(
        chat_id=chat_id,
        text=f"🔄 <b>Executing...</b>\n\n<code>{command}</code>",
        parse_mode="HTML",
    )
    
    async def on_progress(message: str, percentage: int):
        try:
            progress_bar = "▓" * (percentage // 10) + "░" * (10 - percentage // 10)
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=msg.message_id,
                text=f"🔄 <b>{message}</b>\n\n<code>{command}</code>\n\n[{progress_bar}] {percentage}%",
                parse_mode="HTML",
            )
        except Exception:
            pass
    
    result = await ws_mgr.run_command(user, command, on_progress=on_progress)
    
    # Format output
    output = result.stdout or result.stderr or "(no output)"
    output = output[:3000]  # Limit size
    
    if result.success:
        text = f"""✅ <b>Command completed!</b>

<code>{command}</code>

<b>Output:</b>
<pre>{output}</pre>

⏱️ Duration: {result.duration_seconds}s"""
    else:
        text = f"""❌ <b>Command failed</b> (exit code: {result.exit_code})

<code>{command}</code>

<b>Output:</b>
<pre>{output}</pre>

⏱️ Duration: {result.duration_seconds}s"""
    
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception:
        await client.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def handle_run_command(
    user: User,
    command: str,
    chat_id: int,
    client: TelegramClient,
) -> None:
    """Handle /run command - execute via GitHub Actions."""
    if not user.github.is_connected:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Connect GitHub first with /github",
            parse_mode="HTML",
        )
        return
    
    if not user.github.selected_repo:
        await client.send_message(
            chat_id=chat_id,
            text="❌ Select a repository first with /repos",
            parse_mode="HTML",
        )
        return
    
    # Send initial message
    msg = await client.send_message(
        chat_id=chat_id,
        text=f"⚡ <b>Running command...</b>\n\n<code>{command}</code>\n\n⏳ Setting up GitHub Actions...",
        parse_mode="HTML",
    )
    
    runner = GitHubActionsRunner()
    
    async def on_progress(message: str, percentage: int):
        try:
            progress_bar = "▓" * (percentage // 10) + "░" * (10 - percentage // 10)
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=msg.message_id,
                text=f"⚡ <b>{message}</b>\n\n<code>{command}</code>\n\n[{progress_bar}] {percentage}%",
                parse_mode="HTML",
            )
        except Exception:
            pass
    
    result = await runner.execute(user, [command], on_progress)
    
    if result.success:
        text = f"""✅ <b>Command completed!</b>

<code>{command}</code>

<b>Output:</b>
<pre>{result.output[:2000]}</pre>

⏱️ Duration: {result.duration_seconds}s"""
    else:
        text = f"""❌ <b>Command failed</b>

<code>{command}</code>

<b>Error:</b> {result.error or 'Unknown error'}

<b>Output:</b>
<pre>{result.output[:1500] if result.output else 'No output'}</pre>"""
    
    if result.run_url:
        text += f"\n\n🔗 <a href='{result.run_url}'>View full logs</a>"
    
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception:
        await client.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def handle_github_workflow(
    user: User,
    prompt: str,
    model: str,
    chat_id: int,
    client: TelegramClient,
    handler,
) -> None:
    """Handle a prompt using GitHub workflow (API-only, no local files)."""
    from amadeus.github.workflow import StreamingUpdate
    
    # Get session manager for persistent context
    session_mgr = get_session_manager()
    
    # Add user message to session
    session_mgr.add_user_message(user, prompt, metadata={"model": model})
    
    # Get conversation history for AI context
    conversation_history = session_mgr.get_session_context(user, max_messages=15)
    
    # Get a friendly model name for display
    model_display = model.split("-")[0].upper() if "-" in model else model.upper()
    if model.startswith("gpt-5"):
        model_display = "GPT-5.2"
    elif model.startswith("claude"):
        model_display = "Claude"
    elif model.startswith("o3"):
        model_display = "o3"
    
    # Send initial message
    working_msg = await client.send_message(
        chat_id=chat_id,
        text=f"🚀 <b>Starting...</b>\n\n"
             f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
             f"🤖 Model: {model_display}\n"
             f"💬 Context: {len(conversation_history)} messages\n\n"
             f"<i>Connecting to GitHub...</i>",
        parse_mode="HTML",
    )
    
    workflow = GitHubWorkflow()
    last_stream_update = [0.0]  # Use list to allow mutation in closure
    
    async def on_progress(progress: WorkflowProgress):
        """Update message with progress."""
        try:
            progress_bar = "▓" * (progress.percentage // 10) + "░" * (10 - progress.percentage // 10)
            
            text = f"🚀 <b>{progress.message}</b>\n\n"
            text += f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
            text += f"🤖 Model: {model_display}\n\n"
            text += f"[{progress_bar}] {progress.percentage}%"
            
            if progress.details:
                text += f"\n\n<code>{progress.details[:500]}</code>"
            
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=working_msg.message_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass  # Ignore edit errors
    
    async def on_stream(update: StreamingUpdate):
        """Update message during AI streaming."""
        import time
        try:
            # Rate limit updates to avoid Telegram API throttling (every 2s)
            now = time.time()
            if now - last_stream_update[0] < 2.0 and update.status != "done":
                return
            last_stream_update[0] = now
            
            if update.status == "thinking":
                text = f"🧠 <b>AI is thinking...</b>\n\n"
                text += f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
                text += f"🤖 Model: {model_display}\n\n"
                text += f"<i>Analyzing your request...</i>"
            elif update.status == "writing_file":
                text = f"✏️ <b>Writing code...</b>\n\n"
                text += f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
                text += f"🤖 Model: {model_display}\n\n"
                if update.files_so_far:
                    text += "<b>Files:</b>\n"
                    for f in update.files_so_far:
                        icon = "📝" if f == update.current_file else "✅"
                        text += f"{icon} <code>{f}</code>\n"
            elif update.status == "done":
                text = f"📦 <b>Preparing to commit...</b>\n\n"
                text += f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
                text += f"🤖 Model: {model_display}\n\n"
                if update.files_so_far:
                    text += f"<b>{len(update.files_so_far)} file(s) ready</b>"
            else:
                return
            
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=working_msg.message_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass  # Ignore edit errors
    
    try:
        # Check if user is in "continue" mode for an existing PR
        active_pr = get_active_pr_branch(user.telegram_id)
        existing_branch = active_pr[0] if active_pr else None
        existing_pr_number = active_pr[1] if active_pr else None
        
        # Execute workflow (100% via GitHub API, no local files)
        # Pass conversation history for context + streaming callback
        result = await workflow.execute(
            user=user,
            prompt=prompt,
            on_progress=on_progress,
            on_stream=on_stream,
            conversation_history=conversation_history,
            existing_branch=existing_branch,
            existing_pr_number=existing_pr_number,
        )
        
        if result.success:
            text = f"✅ <b>Done!</b>\n\n"
            
            # Show AI summary if available
            if result.summary:
                text += f"📋 <b>Summary:</b>\n<i>{result.summary}</i>\n\n"
            
            text += f"📁 Repo: <code>{user.github.selected_repo}</code>\n"
            text += f"🌿 Branch: <code>{result.branch}</code>\n\n"
            
            if result.changed_files:
                text += "<b>Files:</b>\n"
                for f in result.changed_files[:5]:
                    text += f"📝 <code>{f}</code>\n"
                if len(result.changed_files) > 5:
                    text += f"<i>...and {len(result.changed_files) - 5} more</i>\n"
            
            # Show next steps if available
            if result.next_steps:
                text += "\n<b>🔜 Next steps:</b>\n"
                for i, step in enumerate(result.next_steps[:3], 1):
                    text += f"{i}. {step}\n"
            
            text += "\n<b>Links:</b>\n"
            if result.commit_url:
                text += f"🔗 <a href='{result.commit_url}'>View Commit</a>\n"
            if result.pr_url:
                text += f"🔀 <a href='{result.pr_url}'>View Pull Request</a>\n"
            
            # Save assistant response to session (include summary)
            assistant_summary = result.summary or f"Changed {len(result.changed_files or [])} files: {', '.join(result.changed_files or [])}"
            session_mgr.add_assistant_message(user, assistant_summary, metadata={
                "commit_sha": result.commit_sha,
                "branch": result.branch,
                "changed_files": result.changed_files,
                "next_steps": result.next_steps,
            })
            
            # Extract PR number from URL for merge button
            pr_number = None
            if result.pr_url:
                import re
                pr_match = re.search(r'/pull/(\d+)', result.pr_url)
                if pr_match:
                    pr_number = int(pr_match.group(1))
        else:
            text = f"❌ <b>Failed</b>\n\n{result.error or result.message}"
            pr_number = None
            # Save error to session
            session_mgr.add_assistant_message(user, f"Failed: {result.error or result.message}")
        
        # Build reply markup with merge button if we have a PR
        reply_markup = None
        if result.success and pr_number:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "🚀 Merge to main", "callback_data": f"pr:merge:{pr_number}"},
                        {"text": "➕ Continue PR", "callback_data": f"pr:continue:{pr_number}"},
                    ]
                ]
            }
        
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=working_msg.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        
        # Log usage
        handler.log_usage(user, model, request_type="github_workflow")
    
    except Exception as e:
        logger.exception("github_workflow.error", error=str(e))
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=working_msg.message_id,
            text=f"❌ <b>Error:</b> {str(e)[:200]}",
            parse_mode="HTML",
        )


async def run_bot(bot_token: str, debug: bool = False) -> None:
    """Run the multi-user bot."""
    from ..telegram.multiuser_loop import MultiUserTelegramBot
    
    setup_logging(debug=debug)
    logger.info("multiuser.starting", debug=debug)
    
    bot = MultiUserTelegramBot(bot_token)
    
    # Initialize GitHub handler (PAT-based, no OAuth needed)
    github_handler = GitHubTelegramHandler()
    
    # Get bot info
    bot_info = await bot.client.get_me()
    if bot_info:
        logger.info(
            "multiuser.bot_info",
            username=bot_info.username,
            name=bot_info.first_name,
        )
        print(f"🤖 Bot started: @{bot_info.username}")
    
    # Set bot commands menu
    bot_commands = [
        {"command": "start", "description": "🏠 Main menu & quick start"},
        {"command": "help", "description": "❓ Show all commands"},
        {"command": "model", "description": "🤖 Choose AI model"},
        {"command": "github", "description": "🐙 Connect GitHub"},
        {"command": "repos", "description": "📁 List repositories"},
        {"command": "tree", "description": "🌳 View repo structure"},
        {"command": "pr", "description": "🔀 List open PRs"},
        {"command": "session", "description": "💬 View session info"},
        {"command": "clear", "description": "🧹 Clear session & start fresh"},
        {"command": "settings", "description": "⚙️ API keys & config"},
    ]
    await bot.client.set_my_commands(bot_commands)
    logger.info("multiuser.commands_set", count=len(bot_commands))
    
    print("📡 Listening for messages...")
    print("   Commands: /start, /help, /github, /repos, /settings")
    print("   Press Ctrl+C to stop")
    
    offset: int | None = None
    
    try:
        while True:
            updates = await bot.client.get_updates(
                offset=offset,
                timeout_s=30,
                allowed_updates=["message", "callback_query"],
            )
            
            if updates is None:
                continue
            
            for update in updates:
                offset = update.update_id + 1
                
                try:
                    # Handle callbacks
                    if update.callback_query:
                        callback_data = update.callback_query.data or ""
                        sender = update.callback_query.from_
                        
                        if not sender:
                            continue
                        
                        user = bot.db.get_or_create_user(
                            sender.id,
                            username=sender.username,
                            first_name=sender.first_name,
                            last_name=sender.last_name,
                        )[0]
                        
                        chat_id = (
                            update.callback_query.message.chat.id
                            if update.callback_query.message
                            else sender.id
                        )
                        msg_id = (
                            update.callback_query.message.message_id
                            if update.callback_query.message
                            else None
                        )
                        
                        # Answer callback immediately
                        await bot.client.answer_callback_query(update.callback_query.id)
                        
                        # Handle action: callbacks (navigation)
                        if callback_data.startswith("action:"):
                            action = callback_data[7:]
                            await handle_action_callback(
                                user, action, chat_id, bot.client, github_handler, msg_id
                            )
                            continue
                        
                        # Handle onboard: callbacks (onboarding flow)
                        if callback_data.startswith("onboard:"):
                            step = callback_data[8:]
                            await handle_onboard_callback(
                                user, step, chat_id, bot.client, github_handler, msg_id
                            )
                            continue
                        
                        # Handle setup: callbacks (API key setup)
                        if callback_data.startswith("setup:"):
                            setup_type = callback_data[6:]
                            await handle_setup_callback(
                                user, setup_type, chat_id, bot.client, github_handler, msg_id
                            )
                            continue
                        
                        # Handle settings: callbacks
                        if callback_data.startswith("settings:"):
                            setting = callback_data[9:]
                            await handle_settings_callback(
                                user, setting, chat_id, bot.client, github_handler, msg_id
                            )
                            continue
                        
                        # Handle noop (section headers)
                        if callback_data == "noop":
                            continue
                        
                        # Handle model selection
                        if callback_data.startswith("model:"):
                            try:
                                model_id = callback_data[6:]
                                user.settings.default_model = model_id
                                bot.db.update_user(user)
                                
                                # Find display name for confirmation
                                model_display = model_id
                                for provider in AVAILABLE_MODELS.values():
                                    for mid, mname in provider:
                                        if mid == model_id:
                                            model_display = mname
                                            break
                                
                                # Edit the message to show confirmation
                                await bot.client.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=msg_id,
                                    text=f"✅ <b>Model Updated!</b>\n\nNow using: <b>{model_display}</b>\n<code>{model_id}</code>",
                                    parse_mode="HTML",
                                    reply_markup={"inline_keyboard": [
                                        [{"text": "⬅️ Change Model", "callback_data": "settings:model"}],
                                        [{"text": "🏠 Main Menu", "callback_data": "action:menu"}],
                                    ]},
                                )
                            except Exception as e:
                                logger.exception("model_selection.error", error=str(e))
                                await bot.client.send_message(
                                    chat_id=chat_id,
                                    text=f"❌ Error updating model: {str(e)[:100]}",
                                    parse_mode="HTML",
                                )
                            continue
                        
                        # Handle PR callbacks
                        if callback_data.startswith("pr:"):
                            pr_action = callback_data[3:]
                            
                            if pr_action == "stop":
                                # Stop continuing on existing PR
                                clear_active_pr_branch(user.telegram_id)
                                await bot.client.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=msg_id,
                                    text="✅ <b>New PR mode</b>\n\nYour next requests will create new PRs.",
                                    parse_mode="HTML",
                                )
                            elif pr_action.startswith("continue:"):
                                pr_num = int(pr_action.split(":")[1])
                                await handle_continue_command(user, pr_num, chat_id, bot.client)
                            elif pr_action.startswith("merge:"):
                                pr_num = int(pr_action.split(":")[1])
                                await handle_merge_command(user, pr_num, chat_id, bot.client)
                            continue
                        
                        # Handle GitHub callbacks (repo selection, etc.)
                        if callback_data.startswith(("github_", "repo:")):
                            response = await github_handler.handle_callback(user, callback_data)
                            if response:
                                await send_onboarding_message(bot.client, chat_id, response, msg_id)
                            
                            # After repo selection, show ready message
                            if callback_data.startswith("repo:") and user.github.selected_repo:
                                await asyncio.sleep(0.5)  # Small delay
                                await bot.client.send_message(
                                    chat_id=chat_id,
                                    text=ready_to_code_message(user),
                                    parse_mode="HTML",
                                    reply_markup=MAIN_MENU_KEYBOARD,
                                )
                            continue
                    
                    # Check if user is inputting an API key or GitHub token
                    if update.message and update.message.text:
                        sender = update.message.from_
                        if sender:
                            text = update.message.text.strip()
                            chat_id = update.message.chat.id if update.message.chat else sender.id
                            user = bot.db.get_or_create_user(
                                sender.id,
                                username=sender.username,
                                first_name=sender.first_name,
                            )[0]
                            
                            multiuser_handler = get_multiuser_handler()
                            
                            # Check for API key input
                            if text.startswith("sk-"):
                                awaiting = multiuser_handler.is_awaiting_api_key(sender.id)
                                if awaiting:
                                    success, message = multiuser_handler.handle_api_key_input(user, text)
                                    
                                    if success:
                                        # Check if fully onboarded now
                                        if user.has_any_api_key() and user.github.is_connected and user.github.selected_repo:
                                            await bot.client.send_message(
                                                chat_id=chat_id,
                                                text=f"{message}\n\n" + ready_to_code_message(user),
                                                parse_mode="HTML",
                                                reply_markup=MAIN_MENU_KEYBOARD,
                                            )
                                        elif not user.github.is_connected:
                                            await bot.client.send_message(
                                                chat_id=chat_id,
                                                text=f"{message}\n\n<b>Next:</b> Connect GitHub to push code!",
                                                parse_mode="HTML",
                                                reply_markup=ONBOARD_GITHUB_KEYBOARD,
                                            )
                                        else:
                                            await bot.client.send_message(
                                                chat_id=chat_id,
                                                text=message,
                                                parse_mode="HTML",
                                                reply_markup={"inline_keyboard": [
                                                    [{"text": "🏠 Main Menu", "callback_data": "action:menu"}],
                                                ]},
                                            )
                                    else:
                                        await bot.client.send_message(
                                            chat_id=chat_id,
                                            text=f"❌ {message}",
                                            parse_mode="HTML",
                                        )
                                    continue
                            
                            # Check if it looks like a GitHub token
                            if (text.startswith("ghp_") or text.startswith("github_pat_")) and github_handler.is_awaiting_token(sender.id):
                                response = await github_handler.handle_token_input(user, text)
                                await send_onboarding_message(bot.client, chat_id, response)
                                
                                # After GitHub connected, prompt for repo selection
                                if user.github.is_connected:
                                    await asyncio.sleep(0.5)
                                    repos_response = await github_handler.handle_repos_command(user)
                                    await send_onboarding_message(bot.client, chat_id, repos_response)
                                continue
                    
                    # Process with multi-user handler
                    handled = await bot.process_update(
                        update,
                        on_ready_message=lambda user, msg: handle_user_message(
                            user, msg, bot.client, github_handler
                        ),
                    )
                    
                except Exception as e:
                    logger.exception(
                        "multiuser.update.error",
                        update_id=update.update_id,
                        error=str(e),
                    )
    
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    
    finally:
        await bot.close()
        logger.info("multiuser.stopped")


@app.command()
def main(
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Run Amadeus Pocket in multi-user mode."""
    # Get bot token from environment or config
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        # Try to load from config
        config_path = Path.home() / ".amadeus" / "amadeus.toml"
        if config_path.exists():
            import tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            bot_token = config.get("transports", {}).get("telegram", {}).get("bot_token")
    
    if not bot_token:
        print("❌ No bot token found!")
        print("   Set TELEGRAM_BOT_TOKEN environment variable")
        print("   Or configure in ~/.amadeus/amadeus.toml")
        raise typer.Exit(1)
    
    print("🎭 Amadeus Pocket - Multi-User Mode (BYOK + GitHub)")
    print("=" * 50)
    
    # Show stats
    db = get_db()
    user_count = db.get_user_count()
    print(f"📊 Registered users: {user_count}")
    print(f"🐙 GitHub: Via Personal Access Token (PAT)")
    print()
    
    asyncio.run(run_bot(bot_token, debug=debug))


if __name__ == "__main__":
    app()
