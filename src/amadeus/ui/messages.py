"""Pre-built messages for the Telegram bot."""

from __future__ import annotations

from amadeus.db import User


def welcome_message(user: User) -> str:
    """Welcome message for /start."""
    name = user.first_name or "there"
    
    return f"""🎭 <b>Welcome to Amadeus Pocket!</b>

Hey {name}! 👋

I'm your AI coding companion. I can:

✨ <b>Generate code</b> from natural language
📝 <b>Create PRs</b> directly on your repos  
🖥️ <b>Run commands</b> in secure environments
💬 <b>Remember context</b> across conversations

<i>Let's get you set up in 2 minutes!</i>"""


def setup_status_message(user: User) -> str:
    """Show current setup status."""
    has_api = user.has_any_api_key()
    has_github = user.github.is_connected
    has_repo = bool(user.github.selected_repo)
    
    # Status icons
    api_icon = "✅" if has_api else "⚪"
    github_icon = "✅" if has_github else "⚪"
    repo_icon = "✅" if has_repo else "⚪"
    
    # Progress
    steps_done = sum([has_api, has_github, has_repo])
    progress = "🟢" * steps_done + "⚪" * (3 - steps_done)
    
    text = f"""⚙️ <b>Setup Progress</b>  {progress}

{api_icon} <b>API Key</b> - {"Configured" if has_api else "Required"}
{github_icon} <b>GitHub</b> - {"Connected" if has_github else "Not connected"}
{repo_icon} <b>Repository</b> - {f"<code>{user.github.selected_repo}</code>" if has_repo else "Not selected"}"""
    
    if steps_done == 3:
        text += "\n\n✨ <b>You're all set! Start coding!</b>"
    else:
        text += "\n\n👇 <b>Complete setup to start coding</b>"
    
    return text


def quick_start_message(user: User) -> str:
    """Quick start message for fully onboarded users."""
    name = user.first_name or "there"
    repo = user.github.selected_repo or "No repo"
    model = getattr(user.settings, 'default_model', 'claude-3-5-sonnet')
    model_short = model.split('-')[0].title() if model else "Claude"
    
    return f"""🎭 <b>Hey {name}!</b>

📁 <code>{repo}</code>
🤖 {model_short}

<b>What would you like to build?</b>

Just type your request or use the buttons below.

<i>Examples:</i>
• "Add a dark mode toggle"
• "Create user authentication"
• "Fix the bug in login.js"
• "Add TypeScript types" """


def api_key_setup_message() -> str:
    """Message explaining API key setup."""
    return """🔑 <b>Add Your API Key</b>

To use AI coding features, you need an API key.

<b>Options:</b>

🟣 <b>Anthropic (Claude)</b>
Best for coding tasks
→ Get key: <a href="https://console.anthropic.com/">console.anthropic.com</a>

🟢 <b>OpenAI (GPT-4)</b>  
Great alternative
→ Get key: <a href="https://platform.openai.com/">platform.openai.com</a>

<b>How to add:</b>
Just paste your key in the chat after selecting a provider.

🔒 <i>Your key is encrypted and only used for your requests.</i>"""


def github_setup_message() -> str:
    """Message explaining GitHub setup."""
    return """🐙 <b>Connect GitHub</b>

Link your GitHub to:
• 📂 Access your repositories
• 🌿 Create branches automatically
• 📝 Commit code changes
• 🔀 Open Pull Requests

<b>How it works:</b>
1. Generate a Personal Access Token (PAT)
2. Paste it here

→ <a href="https://github.com/settings/tokens/new?scopes=repo">Create PAT with repo scope</a>

🔒 <i>Your token is encrypted and stored securely.</i>"""


def repo_select_message(repos: list[dict]) -> str:
    """Message for repo selection."""
    if not repos:
        return """📁 <b>No Repositories Found</b>

Make sure your GitHub token has access to your repos.

<a href="https://github.com/settings/tokens/new?scopes=repo">Create new token with repo scope</a>"""
    
    text = """📁 <b>Select a Repository</b>

Choose a repo to start coding on:"""
    
    return text


def ready_to_code_message(user: User) -> str:
    """Message when user is ready to code."""
    repo = user.github.selected_repo
    
    return f"""✅ <b>You're Ready!</b>

📁 Working on: <code>{repo}</code>

<b>Try these:</b>

💬 <i>"Add a login page with Google OAuth"</i>

💬 <i>"Create a REST API for todos"</i>

💬 <i>"Fix the performance issue in app.js"</i>

Just type what you want to build! 🚀"""


def workspace_info_message(info: dict | None) -> str:
    """Message showing workspace info."""
    if not info:
        return """🖥️ <b>Workspaces</b>

No active workspace.

A workspace gives you:
• 📂 Full project context
• ⚡ Run any command (npm, pip, etc.)
• 💾 Persistent environment for 30 min

Click below to create one!"""
    
    expires = info.get('expires_in_minutes', 0)
    repo = info.get('repo', 'Unknown')
    
    return f"""🖥️ <b>Active Workspace</b>

📁 Repo: <code>{repo}</code>
⏱️ Expires in: {expires} minutes

<b>Commands:</b>
• <code>/exec npm test</code>
• <code>/x ls -la</code>
• <code>/x python --version</code>"""


def help_message() -> str:
    """Full help message."""
    return """🎭 <b>Amadeus Pocket Help</b>

<b>🚀 Getting Started</b>
1. Add your API key (Claude or GPT-4)
2. Connect GitHub
3. Select a repo
4. Start coding!

<b>💬 Coding</b>
Just describe what you want:
• "Add user authentication"
• "Create a REST API"
• "Fix the bug in X"

<b>🖥️ Workspaces</b>
<code>/workspace</code> - Create dev environment
<code>/exec cmd</code> - Run commands
<code>/x cmd</code> - Short form

<b>📁 Repository</b>
<code>/repos</code> - List repos
<code>/tree</code> - View structure
<code>/commits</code> - Recent commits

<b>⚡ Quick Run</b>
<code>/run npm test</code> - Via GitHub Actions

<b>💬 Sessions</b>
<code>/session</code> - View session
<code>/clear</code> - Start fresh

<b>⚙️ Settings</b>
<code>/settings</code> - API keys & config
<code>/model</code> - Change AI model"""


def onboarding_api_prompt(provider: str) -> str:
    """Prompt for API key input."""
    if provider == "anthropic":
        return """🟣 <b>Enter Your Anthropic API Key</b>

Paste your key below. It looks like:
<code>sk-ant-api03-...</code>

→ <a href="https://console.anthropic.com/settings/keys">Get your key here</a>"""
    else:
        return """🟢 <b>Enter Your OpenAI API Key</b>

Paste your key below. It looks like:
<code>sk-proj-...</code>

→ <a href="https://platform.openai.com/api-keys">Get your key here</a>"""


def onboarding_github_prompt() -> str:
    """Prompt for GitHub token."""
    return """🐙 <b>Enter Your GitHub Token</b>

Paste your Personal Access Token below.
It looks like: <code>ghp_...</code>

→ <a href="https://github.com/settings/tokens/new?scopes=repo&description=Amadeus%20Pocket">Create token with repo access</a>

<i>Make sure to check the "repo" scope!</i>"""
