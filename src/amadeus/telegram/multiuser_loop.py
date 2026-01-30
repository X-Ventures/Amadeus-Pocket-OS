"""Multi-user wrapper for Telegram bot loop."""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

from ..db import get_db, User
from ..db.multiuser_handler import get_multiuser_handler, OnboardingMessage
from ..logging import get_logger
from .client import TelegramClient
from .types import TelegramIncomingMessage, TelegramIncomingUpdate

logger = get_logger(__name__)


class MultiUserTelegramBot:
    """Multi-user aware Telegram bot wrapper."""
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.client = TelegramClient(bot_token)
        self.handler = get_multiuser_handler()
        self.db = get_db()
    
    async def close(self) -> None:
        """Close the bot client."""
        await self.client.close()
    
    async def process_update(
        self,
        update: TelegramIncomingUpdate,
        on_ready_message: Callable[[User, TelegramIncomingMessage], Awaitable[None]] | None = None,
    ) -> bool:
        """Process an incoming update.
        
        Returns True if the message was handled (onboarding/settings),
        False if it should be passed to the normal handler.
        """
        # Handle callback queries (button presses)
        if update.callback_query is not None:
            return await self._handle_callback(update)
        
        # Handle messages
        msg = update.message
        if msg is None:
            return False
        
        # Get sender info
        sender = msg.from_
        if sender is None:
            return False
        
        telegram_id = sender.id
        username = sender.username
        first_name = sender.first_name
        last_name = sender.last_name
        
        # Get or create user
        user, is_new = self.handler.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        
        text = msg.text or ""
        chat_id = msg.chat.id if msg.chat else telegram_id
        
        # Log new users
        if is_new:
            logger.info(
                "multiuser.new_user",
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
        
        text_lower = text.strip().lower()
        
        # Handle /settings command
        if text_lower == "/settings":
            await self._send_settings(user, chat_id)
            return True
        
        # Handle /start command or onboarding
        if self.handler.should_handle_onboarding(user, text):
            await self._handle_onboarding_message(user, chat_id, text)
            return True
        
        # Commands that don't require API keys - pass through to handler
        allowed_without_keys = (
            "/github", "/gh", "/repos", "/repo", "/help",
            "ghp_", "github_pat_"  # GitHub token input
        )
        if any(text_lower.startswith(cmd) for cmd in allowed_without_keys):
            if on_ready_message:
                await on_ready_message(user, msg)
            return False
        
        # Check if user can use the bot (for AI commands)
        if not user.has_any_api_key() and not user.github.is_connected:
            await self._send_message(
                chat_id,
                "⚠️ To get started:\n\n"
                "• <code>/github</code> - Connect GitHub (recommended)\n"
                "• <code>/settings</code> - Configure API keys\n\n"
                "Use /help to see all commands."
            )
            return True
        
        # User is ready - pass to normal handler
        if on_ready_message:
            await on_ready_message(user, msg)
        
        return False
    
    async def _handle_callback(self, update: TelegramIncomingUpdate) -> bool:
        """Handle callback query (button press)."""
        callback = update.callback_query
        if callback is None:
            return False
        
        sender = callback.from_
        if sender is None:
            return False
        
        telegram_id = sender.id
        callback_data = callback.data or ""
        message = callback.message
        chat_id = message.chat.id if message and message.chat else telegram_id
        
        # Get user
        user = self.db.get_user(telegram_id)
        if user is None:
            user, _ = self.handler.get_or_create_user(
                telegram_id=telegram_id,
                username=sender.username,
                first_name=sender.first_name,
            )
        
        logger.info(
            "multiuser.callback",
            telegram_id=telegram_id,
            callback_data=callback_data,
        )
        
        # Answer the callback to remove loading state
        await self.client.answer_callback_query(callback.id)
        
        # Handle special callbacks
        if callback_data == "clear_all_keys":
            self.handler.clear_all_keys(user)
            await self._send_message(
                chat_id,
                "🗑️ All API keys have been cleared.\n\nSend /start to set up new keys."
            )
            return True
        
        # Handle onboarding callbacks
        response = self.handler.handle_callback(user, callback_data)
        if response:
            await self._send_onboarding_message(chat_id, response, edit_message_id=message.message_id if message else None)
        
        return True
    
    async def _handle_onboarding_message(
        self,
        user: User,
        chat_id: int,
        text: str
    ) -> None:
        """Handle message during onboarding."""
        response = self.handler.handle_onboarding_message(user, text)
        if response:
            await self._send_onboarding_message(chat_id, response)
    
    async def _send_settings(self, user: User, chat_id: int) -> None:
        """Send settings message."""
        msg = self.handler.get_settings_message(user)
        await self._send_onboarding_message(chat_id, msg)
    
    async def _send_onboarding_message(
        self,
        chat_id: int,
        msg: OnboardingMessage,
        edit_message_id: int | None = None,
    ) -> None:
        """Send an onboarding message with optional buttons."""
        reply_markup = None
        if msg.buttons:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": btn["text"], "callback_data": btn["callback_data"]}
                        for btn in row
                    ]
                    for row in msg.buttons
                ]
            }
        
        if edit_message_id:
            await self.client.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=msg.text,
                parse_mode=msg.parse_mode,
                reply_markup=reply_markup,
            )
        else:
            await self.client.send_message(
                chat_id=chat_id,
                text=msg.text,
                parse_mode=msg.parse_mode,
                reply_markup=reply_markup,
            )
    
    async def _send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML"
    ) -> None:
        """Send a simple text message."""
        await self.client.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
        )


async def run_multiuser_bot(
    bot_token: str,
    on_message: Callable[[User, TelegramIncomingMessage, MultiUserTelegramBot], Awaitable[None]],
) -> None:
    """Run the multi-user bot loop."""
    bot = MultiUserTelegramBot(bot_token)
    logger.info("multiuser.bot.started")
    
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
                    # Process update - returns True if handled
                    handled = await bot.process_update(
                        update,
                        on_ready_message=lambda user, msg: on_message(user, msg, bot),
                    )
                    
                    if not handled and update.message:
                        # Message wasn't handled by onboarding
                        # Pass to regular message handler
                        sender = update.message.from_
                        if sender:
                            user = bot.db.get_user(sender.id)
                            if user:
                                await on_message(user, update.message, bot)
                
                except Exception as e:
                    logger.exception(
                        "multiuser.update.error",
                        update_id=update.update_id,
                        error=str(e),
                    )
    
    finally:
        await bot.close()
        logger.info("multiuser.bot.stopped")
