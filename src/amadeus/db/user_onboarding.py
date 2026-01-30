"""User onboarding flow for new Telegram users."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .database import Database, get_db
from .models import User


class OnboardingStep(str, Enum):
    """Onboarding steps for new users."""
    WELCOME = "welcome"
    CHOOSE_ENGINE = "choose_engine"
    ENTER_ANTHROPIC_KEY = "enter_anthropic_key"
    ENTER_OPENAI_KEY = "enter_openai_key"
    ENTER_OPENROUTER_KEY = "enter_openrouter_key"
    CONFIRM_SETUP = "confirm_setup"
    COMPLETE = "complete"


@dataclass
class OnboardingMessage:
    """Message to send during onboarding."""
    text: str
    buttons: list[list[dict[str, str]]] | None = None
    parse_mode: str = "HTML"


# Onboarding messages
MESSAGES = {
    OnboardingStep.WELCOME: OnboardingMessage(
        text="""🎭 <b>Welcome to Amadeus Pocket!</b>

I'm your AI coding assistant bridge. I can connect you to powerful AI coding agents like:

• <b>Claude</b> - Anthropic's Claude Code
• <b>Codex</b> - OpenAI's Codex
• <b>OpenCode</b> - Open source alternative

To get started, you'll need to provide your own API keys (BYOK - Bring Your Own Keys).

<i>Your keys are stored securely and only used for your requests.</i>

Which AI engine would you like to set up first?""",
        buttons=[
            [
                {"text": "🟣 Claude (Anthropic)", "callback_data": "setup_anthropic"},
                {"text": "🟢 Codex (OpenAI)", "callback_data": "setup_openai"},
            ],
            [
                {"text": "🔵 OpenCode (OpenRouter)", "callback_data": "setup_openrouter"},
            ],
            [
                {"text": "⏭️ Skip for now", "callback_data": "skip_setup"},
            ],
        ]
    ),
    
    OnboardingStep.ENTER_ANTHROPIC_KEY: OnboardingMessage(
        text="""🟣 <b>Set up Claude (Anthropic)</b>

To use Claude, you need an Anthropic API key.

<b>How to get one:</b>
1. Go to <a href="https://console.anthropic.com/">console.anthropic.com</a>
2. Create an account or sign in
3. Go to API Keys and create a new key
4. Copy the key (starts with <code>sk-ant-...</code>)

<b>Send me your Anthropic API key:</b>

<i>⚠️ The key will be stored securely and never shared.</i>""",
        buttons=[
            [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
            [{"text": "⏭️ Skip Claude", "callback_data": "skip_anthropic"}],
        ]
    ),
    
    OnboardingStep.ENTER_OPENAI_KEY: OnboardingMessage(
        text="""🟢 <b>Set up Codex (OpenAI)</b>

To use Codex, you need an OpenAI API key.

<b>How to get one:</b>
1. Go to <a href="https://platform.openai.com/">platform.openai.com</a>
2. Create an account or sign in
3. Go to API Keys and create a new key
4. Copy the key (starts with <code>sk-...</code>)

<b>Send me your OpenAI API key:</b>

<i>⚠️ The key will be stored securely and never shared.</i>""",
        buttons=[
            [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
            [{"text": "⏭️ Skip OpenAI", "callback_data": "skip_openai"}],
        ]
    ),
    
    OnboardingStep.ENTER_OPENROUTER_KEY: OnboardingMessage(
        text="""🔵 <b>Set up OpenCode (OpenRouter)</b>

OpenCode can use OpenRouter for access to many models.

<b>How to get an OpenRouter key:</b>
1. Go to <a href="https://openrouter.ai/">openrouter.ai</a>
2. Create an account or sign in
3. Go to Keys and create a new key
4. Copy the key

<b>Send me your OpenRouter API key:</b>

<i>⚠️ The key will be stored securely and never shared.</i>""",
        buttons=[
            [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
            [{"text": "⏭️ Skip OpenRouter", "callback_data": "skip_openrouter"}],
        ]
    ),
    
    OnboardingStep.CONFIRM_SETUP: OnboardingMessage(
        text="""✅ <b>Setup Complete!</b>

{engines_status}

<b>How to use Amadeus:</b>
• Just send a message to start coding
• Use <code>/claude</code>, <code>/codex</code>, or <code>/opencode</code> to pick an engine
• Use <code>/settings</code> to manage your keys
• Use <code>/help</code> for more commands

<b>Ready to code?</b> Send me your first request!""",
    ),
}


class UserOnboardingHandler:
    """Handle user onboarding flow."""
    
    def __init__(self, db: Database | None = None):
        self.db = db or get_db()
    
    def get_welcome_message(self, user: User) -> OnboardingMessage:
        """Get the welcome message for a new user."""
        return MESSAGES[OnboardingStep.WELCOME]
    
    def get_current_step_message(self, user: User) -> OnboardingMessage | None:
        """Get message for user's current onboarding step."""
        if user.is_onboarded:
            return None
        
        step = user.onboarding_step
        if step is None:
            return MESSAGES[OnboardingStep.WELCOME]
        
        try:
            return MESSAGES[OnboardingStep(step)]
        except (ValueError, KeyError):
            return MESSAGES[OnboardingStep.WELCOME]
    
    def process_callback(
        self, 
        user: User, 
        callback_data: str
    ) -> OnboardingMessage:
        """Process a callback button press."""
        
        if callback_data == "setup_anthropic":
            user.onboarding_step = OnboardingStep.ENTER_ANTHROPIC_KEY.value
            self.db.set_onboarding_step(user.telegram_id, user.onboarding_step)
            return MESSAGES[OnboardingStep.ENTER_ANTHROPIC_KEY]
        
        if callback_data == "setup_openai":
            user.onboarding_step = OnboardingStep.ENTER_OPENAI_KEY.value
            self.db.set_onboarding_step(user.telegram_id, user.onboarding_step)
            return MESSAGES[OnboardingStep.ENTER_OPENAI_KEY]
        
        if callback_data == "setup_openrouter":
            user.onboarding_step = OnboardingStep.ENTER_OPENROUTER_KEY.value
            self.db.set_onboarding_step(user.telegram_id, user.onboarding_step)
            return MESSAGES[OnboardingStep.ENTER_OPENROUTER_KEY]
        
        if callback_data == "back_to_welcome":
            user.onboarding_step = OnboardingStep.WELCOME.value
            self.db.set_onboarding_step(user.telegram_id, user.onboarding_step)
            return MESSAGES[OnboardingStep.WELCOME]
        
        if callback_data in ("skip_setup", "skip_anthropic", "skip_openai", "skip_openrouter"):
            return self._complete_onboarding(user)
        
        if callback_data == "add_more_keys":
            user.onboarding_step = OnboardingStep.WELCOME.value
            self.db.set_onboarding_step(user.telegram_id, user.onboarding_step)
            return MESSAGES[OnboardingStep.WELCOME]
        
        # Default: return to welcome
        return MESSAGES[OnboardingStep.WELCOME]
    
    def process_text_input(
        self,
        user: User,
        text: str
    ) -> OnboardingMessage:
        """Process text input during onboarding."""
        step = user.onboarding_step
        
        if step == OnboardingStep.ENTER_ANTHROPIC_KEY.value:
            return self._handle_anthropic_key(user, text)
        
        if step == OnboardingStep.ENTER_OPENAI_KEY.value:
            return self._handle_openai_key(user, text)
        
        if step == OnboardingStep.ENTER_OPENROUTER_KEY.value:
            return self._handle_openrouter_key(user, text)
        
        # Not in a text input step
        return MESSAGES[OnboardingStep.WELCOME]
    
    def _handle_anthropic_key(self, user: User, key: str) -> OnboardingMessage:
        """Handle Anthropic API key input."""
        key = key.strip()
        
        # Basic validation
        if not key.startswith("sk-ant-"):
            return OnboardingMessage(
                text="""❌ <b>Invalid Anthropic API key</b>

The key should start with <code>sk-ant-</code>

Please try again or skip this step.""",
                buttons=[
                    [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
                    [{"text": "⏭️ Skip Claude", "callback_data": "skip_anthropic"}],
                ]
            )
        
        # Save the key
        user.api_keys.anthropic_key = key
        self.db.set_api_key(user.telegram_id, "anthropic", key)
        
        return self._key_saved_message(user, "Claude", "anthropic")
    
    def _handle_openai_key(self, user: User, key: str) -> OnboardingMessage:
        """Handle OpenAI API key input."""
        key = key.strip()
        
        # Basic validation
        if not key.startswith("sk-"):
            return OnboardingMessage(
                text="""❌ <b>Invalid OpenAI API key</b>

The key should start with <code>sk-</code>

Please try again or skip this step.""",
                buttons=[
                    [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
                    [{"text": "⏭️ Skip OpenAI", "callback_data": "skip_openai"}],
                ]
            )
        
        # Save the key
        user.api_keys.openai_key = key
        self.db.set_api_key(user.telegram_id, "openai", key)
        
        return self._key_saved_message(user, "Codex", "openai")
    
    def _handle_openrouter_key(self, user: User, key: str) -> OnboardingMessage:
        """Handle OpenRouter API key input."""
        key = key.strip()
        
        if len(key) < 10:
            return OnboardingMessage(
                text="""❌ <b>Invalid OpenRouter API key</b>

Please enter a valid key or skip this step.""",
                buttons=[
                    [{"text": "⬅️ Back", "callback_data": "back_to_welcome"}],
                    [{"text": "⏭️ Skip OpenRouter", "callback_data": "skip_openrouter"}],
                ]
            )
        
        # Save the key
        user.api_keys.openrouter_key = key
        self.db.set_api_key(user.telegram_id, "openrouter", key)
        
        return self._key_saved_message(user, "OpenCode", "openrouter")
    
    def _key_saved_message(
        self, 
        user: User, 
        engine_name: str,
        provider: str
    ) -> OnboardingMessage:
        """Generate message after saving a key."""
        return OnboardingMessage(
            text=f"""✅ <b>{engine_name} API key saved!</b>

Your key has been securely stored.

Would you like to set up another engine or start using Amadeus?""",
            buttons=[
                [{"text": "➕ Add another key", "callback_data": "add_more_keys"}],
                [{"text": "🚀 Start using Amadeus", "callback_data": "complete_setup"}],
            ]
        )
    
    def _complete_onboarding(self, user: User) -> OnboardingMessage:
        """Complete the onboarding process."""
        self.db.complete_onboarding(user.telegram_id)
        user.is_onboarded = True
        user.onboarding_step = None
        
        # Build engines status
        engines = []
        if user.api_keys.anthropic_key:
            engines.append("✅ Claude (Anthropic)")
        else:
            engines.append("⬜ Claude (not configured)")
        
        if user.api_keys.openai_key:
            engines.append("✅ Codex (OpenAI)")
        else:
            engines.append("⬜ Codex (not configured)")
        
        if user.api_keys.openrouter_key:
            engines.append("✅ OpenCode (OpenRouter)")
        elif user.api_keys.openai_key:
            engines.append("✅ OpenCode (via OpenAI)")
        else:
            engines.append("⬜ OpenCode (not configured)")
        
        engines_status = "\n".join(f"• {e}" for e in engines)
        
        msg = MESSAGES[OnboardingStep.CONFIRM_SETUP]
        return OnboardingMessage(
            text=msg.text.format(engines_status=engines_status),
            buttons=None,
            parse_mode=msg.parse_mode
        )
    
    def is_in_onboarding(self, user: User) -> bool:
        """Check if user is currently in onboarding flow."""
        return not user.is_onboarded and user.onboarding_step is not None
    
    def needs_onboarding(self, user: User) -> bool:
        """Check if user needs to go through onboarding."""
        return not user.is_onboarded
