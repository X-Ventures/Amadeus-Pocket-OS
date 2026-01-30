"""GitHub integration for Amadeus Pocket.

100% API-based - NO local files on the bot's machine.
"""

from amadeus.github.client import GitHubClient
from amadeus.github.workflow import GitHubWorkflow
from amadeus.github.telegram_handlers import GitHubTelegramHandler

__all__ = [
    "GitHubClient", 
    "GitHubWorkflow",
    "GitHubTelegramHandler",
]
