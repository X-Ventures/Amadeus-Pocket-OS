"""Session Manager - Persistent conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from amadeus.db import get_db, User
from amadeus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Message:
    """A single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """A conversation session."""
    id: str
    telegram_id: int
    repo: str | None
    messages: list[Message] = field(default_factory=list)
    model: str = "gpt-5.2"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def add_message(self, role: str, content: str, metadata: dict | None = None) -> None:
        """Add a message to the session."""
        self.messages.append(Message(
            role=role,
            content=content,
            metadata=metadata or {},
        ))
        self.updated_at = datetime.utcnow().isoformat()
    
    def get_context_for_ai(self, max_messages: int = 20) -> list[dict]:
        """Get messages formatted for AI API."""
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        return [
            {"role": msg.role, "content": msg.content}
            for msg in recent
        ]
    
    def to_dict(self) -> dict:
        """Convert session to dictionary."""
        return {
            "id": self.id,
            "telegram_id": self.telegram_id,
            "repo": self.repo,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "metadata": m.metadata,
                }
                for m in self.messages
            ],
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Create session from dictionary."""
        messages = [
            Message(
                role=m["role"],
                content=m["content"],
                timestamp=m.get("timestamp", ""),
                metadata=m.get("metadata", {}),
            )
            for m in data.get("messages", [])
        ]
        return cls(
            id=data["id"],
            telegram_id=data["telegram_id"],
            repo=data.get("repo"),
            messages=messages,
            model=data.get("model", "gpt-5.2"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class SessionManager:
    """Manage user conversation sessions."""
    
    def __init__(self):
        self.db = get_db()
        self._sessions: dict[str, Session] = {}  # In-memory cache
    
    def _session_key(self, telegram_id: int, repo: str | None) -> str:
        """Generate session key."""
        return f"{telegram_id}:{repo or 'default'}"
    
    def get_or_create_session(
        self,
        user: User,
        repo: str | None = None,
    ) -> Session:
        """Get existing session or create new one."""
        repo = repo or user.github.selected_repo
        key = self._session_key(user.telegram_id, repo)
        
        # Check cache first
        if key in self._sessions:
            return self._sessions[key]
        
        # Try to load from database
        session = self._load_session(user.telegram_id, repo)
        
        if session is None:
            # Create new session
            import secrets
            session = Session(
                id=secrets.token_hex(8),
                telegram_id=user.telegram_id,
                repo=repo,
                model=getattr(user.settings, 'default_model', 'claude-sonnet-4-20250514'),
            )
            self._save_session(session)
        
        self._sessions[key] = session
        return session
    
    def add_user_message(
        self,
        user: User,
        content: str,
        repo: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        """Add a user message to the session."""
        session = self.get_or_create_session(user, repo)
        session.add_message("user", content, metadata)
        self._save_session(session)
        return session
    
    def add_assistant_message(
        self,
        user: User,
        content: str,
        repo: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        """Add an assistant message to the session."""
        session = self.get_or_create_session(user, repo)
        session.add_message("assistant", content, metadata)
        self._save_session(session)
        return session
    
    def get_session_context(
        self,
        user: User,
        repo: str | None = None,
        max_messages: int = 20,
    ) -> list[dict]:
        """Get conversation context for AI."""
        session = self.get_or_create_session(user, repo)
        return session.get_context_for_ai(max_messages)
    
    def clear_session(self, user: User, repo: str | None = None) -> None:
        """Clear a session's messages."""
        repo = repo or user.github.selected_repo
        key = self._session_key(user.telegram_id, repo)
        
        if key in self._sessions:
            self._sessions[key].messages = []
            self._sessions[key].updated_at = datetime.utcnow().isoformat()
            self._save_session(self._sessions[key])
        
        # Also delete from database
        self._delete_session(user.telegram_id, repo)
    
    def get_session_summary(self, user: User, repo: str | None = None) -> str:
        """Get a summary of the current session."""
        session = self.get_or_create_session(user, repo)
        
        if not session.messages:
            return "No conversation history."
        
        msg_count = len(session.messages)
        user_msgs = sum(1 for m in session.messages if m.role == "user")
        assistant_msgs = sum(1 for m in session.messages if m.role == "assistant")
        
        # Get last few messages preview
        recent = session.messages[-3:]
        preview = []
        for m in recent:
            content = m.content[:50] + "..." if len(m.content) > 50 else m.content
            icon = "👤" if m.role == "user" else "🤖"
            preview.append(f"{icon} {content}")
        
        return f"""📊 <b>Session Info</b>

📁 Repo: <code>{session.repo or 'None'}</code>
💬 Messages: {msg_count} ({user_msgs} user, {assistant_msgs} AI)
🤖 Model: <code>{session.model}</code>
📅 Started: {session.created_at[:10]}

<b>Recent:</b>
{chr(10).join(preview)}"""
    
    def _ensure_table_exists(self) -> None:
        """Ensure sessions table exists in database."""
        if hasattr(self.db, 'client') and not hasattr(self, '_table_checked'):
            try:
                # Try to query the table
                self.db.client.table("sessions").select("id").limit(1).execute()
                self._table_checked = True
            except Exception:
                # Table might not exist - users need to create it in Supabase dashboard
                # SQL: CREATE TABLE sessions (
                #   id TEXT PRIMARY KEY,
                #   telegram_id BIGINT NOT NULL,
                #   repo TEXT DEFAULT '',
                #   messages JSONB DEFAULT '[]',
                #   model TEXT DEFAULT 'claude-sonnet-4-20250514',
                #   created_at TEXT,
                #   updated_at TEXT
                # );
                self._table_checked = True
                logger.warning("session.table_missing", hint="Create 'sessions' table in Supabase")
    
    def _load_session(self, telegram_id: int, repo: str | None) -> Session | None:
        """Load session from database."""
        try:
            self._ensure_table_exists()
            
            # Use Supabase if available
            if hasattr(self.db, 'client'):
                response = self.db.client.table("sessions").select("*").eq(
                    "telegram_id", telegram_id
                ).eq("repo", repo or "").execute()
                
                if response.data and len(response.data) > 0:
                    row = response.data[0]
                    messages_data = row.get("messages") or []
                    if isinstance(messages_data, str):
                        messages_data = json.loads(messages_data)
                    return Session.from_dict({
                        "id": row["id"],
                        "telegram_id": row["telegram_id"],
                        "repo": row.get("repo"),
                        "messages": messages_data,
                        "model": row.get("model", "gpt-5.2"),
                        "created_at": row.get("created_at", ""),
                        "updated_at": row.get("updated_at", ""),
                    })
            return None
        except Exception as e:
            logger.warning("session.load_error", error=str(e))
            return None
    
    def _save_session(self, session: Session) -> None:
        """Save session to database."""
        try:
            self._ensure_table_exists()
            
            if hasattr(self.db, 'client'):
                # Limit messages to avoid huge payloads
                messages_to_save = session.messages[-50:]  # Keep last 50 messages
                
                data = {
                    "id": session.id,
                    "telegram_id": session.telegram_id,
                    "repo": session.repo or "",
                    "messages": json.dumps([
                        {"role": m.role, "content": m.content[:2000], "timestamp": m.timestamp}
                        for m in messages_to_save
                    ]),
                    "model": session.model,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                }
                
                # Upsert
                self.db.client.table("sessions").upsert(data, on_conflict="id").execute()
        except Exception as e:
            logger.warning("session.save_error", error=str(e))
    
    def _delete_session(self, telegram_id: int, repo: str | None) -> None:
        """Delete session from database."""
        try:
            if hasattr(self.db, 'client'):
                self.db.client.table("sessions").delete().eq(
                    "telegram_id", telegram_id
                ).eq("repo", repo or "").execute()
        except Exception as e:
            logger.warning("session.delete_error", error=str(e))


# Global session manager
_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get or create session manager singleton."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
