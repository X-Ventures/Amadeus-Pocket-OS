"""Database models for multi-user support."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class GitHubConnection:
    """User's GitHub connection info."""
    access_token: str | None = None
    github_id: int | None = None
    github_username: str | None = None
    github_email: str | None = None
    selected_repo: str | None = None  # format: "owner/repo"
    selected_branch: str | None = None
    
    @property
    def is_connected(self) -> bool:
        return self.access_token is not None


@dataclass
class UserAPIKeys:
    """User's API keys for different providers."""
    openai_key: str | None = None
    anthropic_key: str | None = None
    openrouter_key: str | None = None
    
    def has_key_for_engine(self, engine: str) -> bool:
        """Check if user has API key for the given engine."""
        engine_lower = engine.lower()
        if engine_lower in ("claude", "claude-code"):
            return self.anthropic_key is not None
        if engine_lower in ("codex", "openai", "gpt"):
            return self.openai_key is not None
        if engine_lower == "opencode":
            # OpenCode can use OpenRouter or OpenAI
            return self.openrouter_key is not None or self.openai_key is not None
        return False
    
    def get_env_for_engine(self, engine: str) -> dict[str, str]:
        """Get environment variables for the given engine."""
        env = {}
        engine_lower = engine.lower()
        
        if engine_lower in ("claude", "claude-code") and self.anthropic_key:
            env["ANTHROPIC_API_KEY"] = self.anthropic_key
        if engine_lower in ("codex", "openai", "gpt") and self.openai_key:
            env["OPENAI_API_KEY"] = self.openai_key
        if engine_lower == "opencode":
            if self.openrouter_key:
                env["OPENROUTER_API_KEY"] = self.openrouter_key
            elif self.openai_key:
                env["OPENAI_API_KEY"] = self.openai_key
        
        return env


@dataclass
class UserSettings:
    """User preferences and settings."""
    default_engine: str = "claude"
    default_model: str = "gpt-5.2"  # Default model (Codex 5.1)
    language: str = "en"
    notifications: bool = True
    

@dataclass
class User:
    """Represents a user in the system."""
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    
    api_keys: UserAPIKeys = field(default_factory=UserAPIKeys)
    settings: UserSettings = field(default_factory=UserSettings)
    github: GitHubConnection = field(default_factory=GitHubConnection)
    
    is_active: bool = True
    is_onboarded: bool = False
    onboarding_step: str | None = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    
    # Usage tracking
    total_requests: int = 0
    total_tokens: int = 0
    
    @property
    def display_name(self) -> str:
        """Get user's display name."""
        if self.first_name:
            if self.last_name:
                return f"{self.first_name} {self.last_name}"
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return f"User {self.telegram_id}"
    
    def has_any_api_key(self) -> bool:
        """Check if user has at least one API key configured."""
        return (
            self.api_keys.openai_key is not None or
            self.api_keys.anthropic_key is not None or
            self.api_keys.openrouter_key is not None
        )
    
    def can_use_engine(self, engine: str) -> bool:
        """Check if user can use the specified engine."""
        return self.api_keys.has_key_for_engine(engine)
    
    def available_engines(self) -> list[str]:
        """Get list of engines available to this user."""
        engines = []
        if self.api_keys.anthropic_key:
            engines.append("claude")
        if self.api_keys.openai_key:
            engines.append("codex")
        if self.api_keys.openrouter_key or self.api_keys.openai_key:
            engines.append("opencode")
        return engines
