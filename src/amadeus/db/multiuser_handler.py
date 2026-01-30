"""Multi-user handler for Telegram bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from .database import Database, get_db
from .models import User
from .user_onboarding import UserOnboardingHandler, OnboardingMessage


@dataclass
class MessageContext:
    """Context for processing a user message."""
    user: User
    telegram_id: int
    chat_id: int
    text: str
    is_callback: bool = False
    callback_data: str | None = None


class MultiUserHandler:
    """Handle multi-user logic for the Telegram bot."""
    
    def __init__(self, db: Database | None = None):
        self.db = db or get_db()
        self.onboarding = UserOnboardingHandler(self.db)
        # Track users awaiting API key input: telegram_id -> provider
        self._awaiting_api_key: dict[int, str] = {}
    
    def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[User, bool]:
        """Get or create a user. Returns (user, is_new)."""
        return self.db.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
    
    def should_handle_onboarding(self, user: User, text: str) -> bool:
        """Check if we should handle this message as onboarding."""
        # If user is onboarded, don't handle as onboarding
        if user.is_onboarded:
            return False
        
        # If it's a /start command, always trigger onboarding
        if text.strip().lower() == "/start":
            return True
        
        # If user is in middle of onboarding, continue
        if user.onboarding_step is not None:
            return True
        
        # New user, start onboarding
        return True
    
    def handle_onboarding_message(
        self,
        user: User,
        text: str
    ) -> OnboardingMessage | None:
        """Handle a message during onboarding. Returns message to send."""
        text = text.strip()
        
        # /start command
        if text.lower() == "/start":
            user.onboarding_step = "welcome"
            self.db.set_onboarding_step(user.telegram_id, "welcome")
            return self.onboarding.get_welcome_message(user)
        
        # If we're in a step that expects text input
        if user.onboarding_step in (
            "enter_anthropic_key",
            "enter_openai_key", 
            "enter_openrouter_key"
        ):
            return self.onboarding.process_text_input(user, text)
        
        # Default: show current step or welcome
        return self.onboarding.get_current_step_message(user)
    
    def handle_callback(
        self,
        user: User,
        callback_data: str
    ) -> OnboardingMessage | None:
        """Handle a callback button press."""
        if callback_data == "complete_setup":
            return self.onboarding._complete_onboarding(user)
        
        return self.onboarding.process_callback(user, callback_data)
    
    def get_user_env(self, user: User, engine: str) -> dict[str, str]:
        """Get environment variables for running an engine for this user."""
        env = os.environ.copy()
        user_env = user.api_keys.get_env_for_engine(engine)
        env.update(user_env)
        return env
    
    def can_user_use_engine(self, user: User, engine: str) -> tuple[bool, str | None]:
        """Check if user can use the specified engine.
        
        Returns (can_use, error_message).
        """
        if not user.is_onboarded and not user.has_any_api_key():
            return False, "Please complete setup first. Send /start to begin."
        
        if not user.can_use_engine(engine):
            available = user.available_engines()
            if available:
                return False, (
                    f"You don't have an API key for {engine}. "
                    f"Available engines: {', '.join(available)}. "
                    f"Use /settings to add more keys."
                )
            return False, (
                f"You don't have any API keys configured. "
                f"Use /settings to add your API keys."
            )
        
        return True, None
    
    def get_default_engine(self, user: User) -> str:
        """Get user's default engine."""
        # Check if user's preferred engine is available
        preferred = user.settings.default_engine
        if user.can_use_engine(preferred):
            return preferred
        
        # Fall back to first available
        available = user.available_engines()
        if available:
            return available[0]
        
        # Default fallback
        return "claude"
    
    def log_usage(
        self,
        user: User,
        engine: str,
        tokens: int = 0,
        request_type: str = "message"
    ) -> None:
        """Log usage for a user."""
        self.db.log_usage(
            telegram_id=user.telegram_id,
            engine=engine,
            tokens=tokens,
            request_type=request_type,
        )
    
    def get_settings_message(self, user: User) -> OnboardingMessage:
        """Get settings message for user."""
        engines = []
        
        if user.api_keys.anthropic_key:
            key_preview = user.api_keys.anthropic_key[:10] + "..."
            engines.append(f"✅ Claude: <code>{key_preview}</code>")
        else:
            engines.append("⬜ Claude: Not configured")
        
        if user.api_keys.openai_key:
            key_preview = user.api_keys.openai_key[:10] + "..."
            engines.append(f"✅ Codex: <code>{key_preview}</code>")
        else:
            engines.append("⬜ Codex: Not configured")
        
        if user.api_keys.openrouter_key:
            key_preview = user.api_keys.openrouter_key[:10] + "..."
            engines.append(f"✅ OpenCode: <code>{key_preview}</code>")
        elif user.api_keys.openai_key:
            engines.append("✅ OpenCode: Using OpenAI key")
        else:
            engines.append("⬜ OpenCode: Not configured")
        
        engines_status = "\n".join(f"• {e}" for e in engines)
        
        return OnboardingMessage(
            text=f"""⚙️ <b>Your Settings</b>

<b>Default Engine:</b> {user.settings.default_engine}

<b>API Keys:</b>
{engines_status}

<b>Usage:</b>
• Total requests: {user.total_requests}
• Total tokens: {user.total_tokens}

<b>Manage your keys:</b>""",
            buttons=[
                [
                    {"text": "🟣 Set Claude Key", "callback_data": "setup_anthropic"},
                    {"text": "🟢 Set OpenAI Key", "callback_data": "setup_openai"},
                ],
                [
                    {"text": "🔵 Set OpenRouter Key", "callback_data": "setup_openrouter"},
                ],
                [
                    {"text": "🗑️ Clear All Keys", "callback_data": "clear_all_keys"},
                ],
            ]
        )
    
    def clear_all_keys(self, user: User) -> None:
        """Clear all API keys for a user."""
        self.db.set_api_key(user.telegram_id, "openai", None)
        self.db.set_api_key(user.telegram_id, "anthropic", None)
        self.db.set_api_key(user.telegram_id, "openrouter", None)
        user.api_keys.openai_key = None
        user.api_keys.anthropic_key = None
        user.api_keys.openrouter_key = None
    
    def set_awaiting_api_key(self, telegram_id: int, provider: str) -> None:
        """Set user as awaiting API key input."""
        self._awaiting_api_key[telegram_id] = provider
    
    def is_awaiting_api_key(self, telegram_id: int) -> str | None:
        """Check if user is awaiting API key. Returns provider or None."""
        return self._awaiting_api_key.get(telegram_id)
    
    def clear_awaiting_api_key(self, telegram_id: int) -> None:
        """Clear awaiting API key state."""
        self._awaiting_api_key.pop(telegram_id, None)
    
    def handle_api_key_input(self, user: User, key: str) -> tuple[bool, str]:
        """Handle API key input from user.
        
        Returns (success, message).
        """
        provider = self._awaiting_api_key.get(user.telegram_id)
        if not provider:
            return False, "Not expecting an API key."
        
        # Validate key format
        if provider == "anthropic":
            if not key.startswith("sk-ant-"):
                return False, "Invalid Anthropic key format. Should start with sk-ant-"
            self.db.set_api_key(user.telegram_id, "anthropic", key)
            user.api_keys.anthropic_key = key
        elif provider == "openai":
            if not key.startswith("sk-"):
                return False, "Invalid OpenAI key format. Should start with sk-"
            self.db.set_api_key(user.telegram_id, "openai", key)
            user.api_keys.openai_key = key
        else:
            return False, f"Unknown provider: {provider}"
        
        self.clear_awaiting_api_key(user.telegram_id)
        return True, f"✅ {provider.title()} API key saved!"


# Global handler instance
_handler: MultiUserHandler | None = None


def get_multiuser_handler() -> MultiUserHandler:
    """Get or create the multi-user handler singleton."""
    global _handler
    if _handler is None:
        _handler = MultiUserHandler()
    return _handler
