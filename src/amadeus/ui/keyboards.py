"""Telegram Inline Keyboard builders."""

from __future__ import annotations

from typing import Any


def make_button(text: str, callback_data: str, url: str | None = None) -> dict[str, Any]:
    """Create a single inline button."""
    btn: dict[str, Any] = {"text": text, "callback_data": callback_data}
    if url:
        btn["url"] = url
        del btn["callback_data"]
    return btn


def make_row(*buttons: dict[str, Any]) -> list[dict[str, Any]]:
    """Create a row of buttons."""
    return list(buttons)


def make_inline_keyboard(*rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Create an inline keyboard from rows."""
    return {"inline_keyboard": list(rows)}


# ============================================================
# PRE-BUILT KEYBOARDS
# ============================================================

# Main menu for fully onboarded users
MAIN_MENU_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🚀 Start Coding", "action:code"),
        make_button("📁 My Repos", "action:repos"),
    ),
    make_row(
        make_button("⚙️ Settings", "action:settings"),
        make_button("❓ Help", "action:help"),
    ),
)

# Quick actions for coding
QUICK_ACTIONS_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🌳 View Tree", "action:tree"),
        make_button("📜 Commits", "action:commits"),
    ),
    make_row(
        make_button("🏠 Main Menu", "action:menu"),
    ),
)

# Setup: API Key selection
SETUP_API_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🟣 Add Anthropic Key", "setup:anthropic"),
    ),
    make_row(
        make_button("🟢 Add OpenAI Key", "setup:openai"),
    ),
    make_row(
        make_button("⬅️ Back", "action:menu"),
    ),
)

# Setup: GitHub connection
SETUP_GITHUB_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🔗 Connect GitHub", "setup:github"),
    ),
    make_row(
        make_button("⬅️ Back", "action:menu"),
    ),
)

# Confirm action
CONFIRM_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("✅ Yes", "confirm:yes"),
        make_button("❌ No", "confirm:no"),
    ),
)

# Settings menu
SETTINGS_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🔑 API Keys", "settings:keys"),
        make_button("🐙 GitHub", "settings:github"),
    ),
    make_row(
        make_button("🤖 AI Model", "settings:model"),
        make_button("💬 Session", "settings:session"),
    ),
    make_row(
        make_button("🏠 Main Menu", "action:menu"),
    ),
)

# Onboarding step 1: Welcome
ONBOARD_WELCOME_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🚀 Let's Go!", "onboard:start"),
    ),
)

# Onboarding step 2: API Key
ONBOARD_API_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🟣 I have Anthropic", "onboard:api_anthropic"),
    ),
    make_row(
        make_button("🟢 I have OpenAI", "onboard:api_openai"),
    ),
    make_row(
        make_button("🤔 I need a key", "onboard:api_help"),
    ),
)

# Onboarding step 3: GitHub
ONBOARD_GITHUB_KEYBOARD = make_inline_keyboard(
    make_row(
        make_button("🔗 Connect GitHub", "onboard:github"),
    ),
    make_row(
        make_button("⏭️ Skip for now", "onboard:skip_github"),
    ),
)

# Model selection - Quick access to top models (full list via /model command)
def make_model_keyboard(has_anthropic: bool, has_openai: bool) -> dict[str, Any]:
    """Create model selection keyboard based on available keys.
    
    Shows top models per provider. Full list available via multiuser.py AVAILABLE_MODELS.
    """
    rows = []
    
    if has_anthropic:
        rows.append(make_row(
            make_button("🟣 Claude Sonnet 4", "model:claude-sonnet-4-20250514"),
            make_button("🧠 Opus 4.5", "model:claude-opus-4-20250514"),
        ))
        rows.append(make_row(
            make_button("⚡ Claude 3.5 Sonnet", "model:claude-sonnet-4-20250514"),
        ))
    
    if has_openai:
        rows.append(make_row(
            make_button("🌟 GPT-5.2 (Best)", "model:gpt-5.2"),
            make_button("⚡ GPT-5 Mini", "model:gpt-5-mini"),
        ))
        rows.append(make_row(
            make_button("💚 GPT-4o", "model:gpt-4o"),
            make_button("🧠 o3", "model:o3"),
        ))
    
    rows.append(make_row(
        make_button("📋 All Models", "action:all_models"),
        make_button("⬅️ Back", "action:settings"),
    ))
    
    return make_inline_keyboard(*rows)


# Repo selection (dynamic)
def make_repos_keyboard(repos: list[tuple[str, str]]) -> dict[str, Any]:
    """Create repo selection keyboard.
    
    Args:
        repos: List of (full_name, display_name) tuples
    """
    rows = []
    for full_name, display_name in repos[:8]:  # Limit to 8 repos
        rows.append(make_row(
            make_button(f"📁 {display_name}", f"repo:{full_name}"),
        ))
    
    rows.append(make_row(
        make_button("🏠 Main Menu", "action:menu"),
    ))
    
    return make_inline_keyboard(*rows)
