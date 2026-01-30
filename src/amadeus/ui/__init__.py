"""UI Components for Telegram bot - Buttons, Menus, Keyboards."""

from .keyboards import (
    make_inline_keyboard,
    make_button,
    make_row,
    # Pre-built keyboards
    MAIN_MENU_KEYBOARD,
    SETUP_API_KEYBOARD,
    SETUP_GITHUB_KEYBOARD,
    QUICK_ACTIONS_KEYBOARD,
    CONFIRM_KEYBOARD,
)

from .messages import (
    welcome_message,
    setup_status_message,
    quick_start_message,
    api_key_setup_message,
    github_setup_message,
    repo_select_message,
    ready_to_code_message,
)

__all__ = [
    # Keyboard builders
    "make_inline_keyboard",
    "make_button",
    "make_row",
    # Pre-built keyboards
    "MAIN_MENU_KEYBOARD",
    "SETUP_API_KEYBOARD",
    "SETUP_GITHUB_KEYBOARD",
    "QUICK_ACTIONS_KEYBOARD",
    "CONFIRM_KEYBOARD",
    # Message builders
    "welcome_message",
    "setup_status_message",
    "quick_start_message",
    "api_key_setup_message",
    "github_setup_message",
    "repo_select_message",
    "ready_to_code_message",
]
