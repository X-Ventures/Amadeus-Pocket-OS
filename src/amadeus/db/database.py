"""Database for multi-user support. Supports SQLite, PostgreSQL, and Supabase."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Any
from urllib.parse import urlparse

from .models import User, UserAPIKeys, UserSettings, GitHubConnection
from .encryption import encrypt_key, decrypt_key

# Check for PostgreSQL support
try:
    import psycopg2
    import psycopg2.extras
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

# Check for Supabase support
try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

# Default database path (for SQLite)
DEFAULT_DB_PATH = Path.home() / ".amadeus" / "amadeus.db"

# Singleton database instance
_db_instance: "Database | None" = None


def get_db(path: Path | None = None) -> "Database":
    """Get or create the database singleton."""
    global _db_instance
    if _db_instance is None:
        # Check for Supabase environment variables first
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_KEY")
        if supabase_url and supabase_key and HAS_SUPABASE:
            _db_instance = SupabaseDatabase(supabase_url, supabase_key)
        # Check for DATABASE_URL environment variable (PostgreSQL)
        elif os.environ.get("DATABASE_URL") and HAS_POSTGRES:
            database_url = os.environ.get("DATABASE_URL", "").strip()
            _db_instance = PostgresDatabase(database_url)
        else:
            _db_instance = SQLiteDatabase(path or DEFAULT_DB_PATH)
    return _db_instance


class Database:
    """Base database class."""
    
    def _init_db(self) -> None:
        raise NotImplementedError
    
    def get_user(self, telegram_id: int) -> User | None:
        raise NotImplementedError
    
    def get_or_create_user(self, telegram_id: int, username: str | None = None,
                          first_name: str | None = None, last_name: str | None = None) -> tuple[User, bool]:
        raise NotImplementedError
    
    def update_user(self, user: User) -> None:
        raise NotImplementedError
    
    def set_api_key(self, telegram_id: int, provider: str, api_key: str | None) -> bool:
        raise NotImplementedError


class SQLiteDatabase(Database):
    """SQLite database for user management."""
    
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    
                    -- API Keys (encrypted)
                    openai_key TEXT,
                    anthropic_key TEXT,
                    openrouter_key TEXT,
                    
                    -- GitHub Connection (encrypted)
                    github_token TEXT,
                    github_id INTEGER,
                    github_username TEXT,
                    github_email TEXT,
                    github_selected_repo TEXT,
                    github_selected_branch TEXT,
                    
                    -- Settings
                    default_engine TEXT DEFAULT 'claude',
                    default_model TEXT DEFAULT 'gpt-5.2',
                    language TEXT DEFAULT 'en',
                    notifications INTEGER DEFAULT 1,
                    
                    -- Status
                    is_active INTEGER DEFAULT 1,
                    is_onboarded INTEGER DEFAULT 0,
                    onboarding_step TEXT,
                    
                    -- Timestamps
                    created_at TEXT,
                    updated_at TEXT,
                    last_activity TEXT,
                    
                    -- Usage
                    total_requests INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0
                );
                
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    engine TEXT NOT NULL,
                    session_token TEXT,
                    context TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_sessions_user 
                ON user_sessions(telegram_id);
                
                CREATE TABLE IF NOT EXISTS usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    engine TEXT NOT NULL,
                    tokens_used INTEGER DEFAULT 0,
                    request_type TEXT,
                    created_at TEXT,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_usage_user 
                ON usage_logs(telegram_id);
            ''')
    
    def get_user(self, telegram_id: int) -> User | None:
        """Get a user by Telegram ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE telegram_id = ?',
                (telegram_id,)
            ).fetchone()
            
            if row is None:
                return None
            
            return self._row_to_user(row)
    
    def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[User, bool]:
        """Get existing user or create new one. Returns (user, created)."""
        user = self.get_user(telegram_id)
        if user is not None:
            # Update user info if changed
            if username != user.username or first_name != user.first_name:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                self.update_user(user)
            return user, False
        
        # Create new user
        now = datetime.utcnow().isoformat()
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
        )
        
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO users (
                    telegram_id, username, first_name, last_name,
                    created_at, updated_at, last_activity
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                telegram_id, username, first_name, last_name,
                now, now, now
            ))
        
        return user, True
    
    def update_user(self, user: User) -> None:
        """Update user in database."""
        now = datetime.utcnow().isoformat()
        
        # Encrypt API keys before storing
        openai_encrypted = encrypt_key(user.api_keys.openai_key) if user.api_keys.openai_key else None
        anthropic_encrypted = encrypt_key(user.api_keys.anthropic_key) if user.api_keys.anthropic_key else None
        openrouter_encrypted = encrypt_key(user.api_keys.openrouter_key) if user.api_keys.openrouter_key else None
        
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET
                    username = ?,
                    first_name = ?,
                    last_name = ?,
                    openai_key = ?,
                    anthropic_key = ?,
                    openrouter_key = ?,
                    default_engine = ?,
                    default_model = ?,
                    language = ?,
                    notifications = ?,
                    is_active = ?,
                    is_onboarded = ?,
                    onboarding_step = ?,
                    updated_at = ?,
                    last_activity = ?,
                    total_requests = ?,
                    total_tokens = ?
                WHERE telegram_id = ?
            ''', (
                user.username,
                user.first_name,
                user.last_name,
                openai_encrypted,
                anthropic_encrypted,
                openrouter_encrypted,
                user.settings.default_engine,
                getattr(user.settings, 'default_model', 'gpt-5.2'),
                user.settings.language,
                1 if user.settings.notifications else 0,
                1 if user.is_active else 0,
                1 if user.is_onboarded else 0,
                user.onboarding_step,
                now,
                now,
                user.total_requests,
                user.total_tokens,
                user.telegram_id,
            ))
    
    def set_api_key(
        self,
        telegram_id: int,
        provider: str,
        api_key: str | None
    ) -> bool:
        """Set an API key for a user (encrypted)."""
        column_map = {
            "openai": "openai_key",
            "anthropic": "anthropic_key",
            "openrouter": "openrouter_key",
        }
        
        column = column_map.get(provider.lower())
        if column is None:
            return False
        
        # Encrypt the key before storing
        encrypted_key = encrypt_key(api_key) if api_key else None
        
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            conn.execute(f'''
                UPDATE users SET {column} = ?, updated_at = ?
                WHERE telegram_id = ?
            ''', (encrypted_key, now, telegram_id))
        
        return True
    
    def complete_onboarding(self, telegram_id: int) -> None:
        """Mark user as onboarded."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET 
                    is_onboarded = 1, 
                    onboarding_step = NULL,
                    updated_at = ?
                WHERE telegram_id = ?
            ''', (now, telegram_id))
    
    def set_onboarding_step(self, telegram_id: int, step: str | None) -> None:
        """Set current onboarding step."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET onboarding_step = ?, updated_at = ?
                WHERE telegram_id = ?
            ''', (step, now, telegram_id))
    
    def log_usage(
        self,
        telegram_id: int,
        engine: str,
        tokens: int = 0,
        request_type: str = "message"
    ) -> None:
        """Log usage for a user."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            # Insert usage log
            conn.execute('''
                INSERT INTO usage_logs (telegram_id, engine, tokens_used, request_type, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (telegram_id, engine, tokens, request_type, now))
            
            # Update user totals
            conn.execute('''
                UPDATE users SET 
                    total_requests = total_requests + 1,
                    total_tokens = total_tokens + ?,
                    last_activity = ?
                WHERE telegram_id = ?
            ''', (tokens, now, telegram_id))
    
    def get_all_users(self, active_only: bool = True) -> list[User]:
        """Get all users."""
        with self._get_conn() as conn:
            if active_only:
                rows = conn.execute(
                    'SELECT * FROM users WHERE is_active = 1'
                ).fetchall()
            else:
                rows = conn.execute('SELECT * FROM users').fetchall()
            
            return [self._row_to_user(row) for row in rows]
    
    def get_user_count(self) -> int:
        """Get total number of users."""
        with self._get_conn() as conn:
            result = conn.execute('SELECT COUNT(*) FROM users').fetchone()
            return result[0] if result else 0
    
    def set_github_connection(
        self,
        telegram_id: int,
        access_token: str,
        github_id: int,
        github_username: str,
        github_email: str | None = None,
    ) -> None:
        """Save GitHub connection for a user."""
        encrypted_token = encrypt_key(access_token)
        now = datetime.utcnow().isoformat()
        
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET
                    github_token = ?,
                    github_id = ?,
                    github_username = ?,
                    github_email = ?,
                    updated_at = ?
                WHERE telegram_id = ?
            ''', (encrypted_token, github_id, github_username, github_email, now, telegram_id))
    
    def set_github_repo(
        self,
        telegram_id: int,
        repo: str,
        branch: str | None = None,
    ) -> None:
        """Set selected GitHub repo for a user."""
        now = datetime.utcnow().isoformat()
        
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET
                    github_selected_repo = ?,
                    github_selected_branch = ?,
                    updated_at = ?
                WHERE telegram_id = ?
            ''', (repo, branch, now, telegram_id))
    
    def disconnect_github(self, telegram_id: int) -> None:
        """Remove GitHub connection for a user."""
        now = datetime.utcnow().isoformat()
        
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE users SET
                    github_token = NULL,
                    github_id = NULL,
                    github_username = NULL,
                    github_email = NULL,
                    github_selected_repo = NULL,
                    github_selected_branch = NULL,
                    updated_at = ?
                WHERE telegram_id = ?
            ''', (now, telegram_id))
    
    def _row_to_user(self, row: sqlite3.Row) -> User:
        """Convert database row to User object."""
        # Decrypt API keys
        api_keys = UserAPIKeys(
            openai_key=decrypt_key(row['openai_key']) if row['openai_key'] else None,
            anthropic_key=decrypt_key(row['anthropic_key']) if row['anthropic_key'] else None,
            openrouter_key=decrypt_key(row['openrouter_key']) if row['openrouter_key'] else None,
        )
        
        settings = UserSettings(
            default_engine=row['default_engine'] or 'claude',
            default_model=row['default_model'] if 'default_model' in row.keys() else 'gpt-5.2',
            language=row['language'] or 'en',
            notifications=bool(row['notifications']),
        )
        
        # GitHub connection (decrypt token)
        github_token = None
        try:
            if row['github_token']:
                github_token = decrypt_key(row['github_token'])
        except (KeyError, TypeError):
            pass
        
        github = GitHubConnection(
            access_token=github_token,
            github_id=row['github_id'] if 'github_id' in row.keys() else None,
            github_username=row['github_username'] if 'github_username' in row.keys() else None,
            github_email=row['github_email'] if 'github_email' in row.keys() else None,
            selected_repo=row['github_selected_repo'] if 'github_selected_repo' in row.keys() else None,
            selected_branch=row['github_selected_branch'] if 'github_selected_branch' in row.keys() else None,
        )
        
        return User(
            telegram_id=row['telegram_id'],
            username=row['username'],
            first_name=row['first_name'],
            last_name=row['last_name'],
            api_keys=api_keys,
            settings=settings,
            github=github,
            is_active=bool(row['is_active']),
            is_onboarded=bool(row['is_onboarded']),
            onboarding_step=row['onboarding_step'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else datetime.utcnow(),
            last_activity=datetime.fromisoformat(row['last_activity']) if row['last_activity'] else datetime.utcnow(),
            total_requests=row['total_requests'] or 0,
            total_tokens=row['total_tokens'] or 0,
        )


class PostgresDatabase(Database):
    """PostgreSQL database for user management (Supabase compatible)."""
    
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._init_db()
    
    @contextmanager
    def _get_conn(self):
        """Get a database connection."""
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        telegram_id BIGINT PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        
                        openai_key TEXT,
                        anthropic_key TEXT,
                        openrouter_key TEXT,
                        
                        github_token TEXT,
                        github_id BIGINT,
                        github_username TEXT,
                        github_email TEXT,
                        github_selected_repo TEXT,
                        github_selected_branch TEXT,
                        
                        default_engine TEXT DEFAULT 'claude',
                        default_model TEXT DEFAULT 'gpt-5.2',
                        language TEXT DEFAULT 'en',
                        notifications INTEGER DEFAULT 1,
                        
                        is_active INTEGER DEFAULT 1,
                        is_onboarded INTEGER DEFAULT 0,
                        onboarding_step TEXT,
                        
                        created_at TEXT,
                        updated_at TEXT,
                        last_activity TEXT,
                        
                        total_requests INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0
                    );
                    
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT NOT NULL,
                        engine TEXT NOT NULL,
                        session_token TEXT,
                        context TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    
                    CREATE TABLE IF NOT EXISTS usage_logs (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT NOT NULL,
                        engine TEXT NOT NULL,
                        tokens_used INTEGER DEFAULT 0,
                        request_type TEXT,
                        created_at TEXT
                    );
                ''')
    
    def get_user(self, telegram_id: int) -> User | None:
        """Get a user by Telegram ID."""
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('SELECT * FROM users WHERE telegram_id = %s', (telegram_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_user(row)
    
    def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[User, bool]:
        """Get existing user or create new one."""
        user = self.get_user(telegram_id)
        if user is not None:
            if username != user.username or first_name != user.first_name:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                self.update_user(user)
            return user, False
        
        now = datetime.utcnow().isoformat()
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
        )
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO users (telegram_id, username, first_name, last_name, created_at, updated_at, last_activity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (telegram_id, username, first_name, last_name, now, now, now))
        
        return user, True
    
    def update_user(self, user: User) -> None:
        """Update user in database."""
        now = datetime.utcnow().isoformat()
        
        openai_encrypted = encrypt_key(user.api_keys.openai_key) if user.api_keys.openai_key else None
        anthropic_encrypted = encrypt_key(user.api_keys.anthropic_key) if user.api_keys.anthropic_key else None
        openrouter_encrypted = encrypt_key(user.api_keys.openrouter_key) if user.api_keys.openrouter_key else None
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE users SET
                        username = %s, first_name = %s, last_name = %s,
                        openai_key = %s, anthropic_key = %s, openrouter_key = %s,
                        default_engine = %s, language = %s, notifications = %s,
                        is_active = %s, is_onboarded = %s, onboarding_step = %s,
                        updated_at = %s, last_activity = %s,
                        total_requests = %s, total_tokens = %s
                    WHERE telegram_id = %s
                ''', (
                    user.username, user.first_name, user.last_name,
                    openai_encrypted, anthropic_encrypted, openrouter_encrypted,
                    user.settings.default_engine, user.settings.language,
                    1 if user.settings.notifications else 0,
                    1 if user.is_active else 0, 1 if user.is_onboarded else 0,
                    user.onboarding_step, now, now,
                    user.total_requests, user.total_tokens, user.telegram_id,
                ))
    
    def set_api_key(self, telegram_id: int, provider: str, api_key: str | None) -> bool:
        """Set an API key for a user."""
        column_map = {"openai": "openai_key", "anthropic": "anthropic_key", "openrouter": "openrouter_key"}
        column = column_map.get(provider.lower())
        if column is None:
            return False
        
        encrypted_key = encrypt_key(api_key) if api_key else None
        now = datetime.utcnow().isoformat()
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f'UPDATE users SET {column} = %s, updated_at = %s WHERE telegram_id = %s',
                           (encrypted_key, now, telegram_id))
        return True
    
    def complete_onboarding(self, telegram_id: int) -> None:
        """Mark user as onboarded."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE users SET is_onboarded = 1, onboarding_step = NULL, updated_at = %s WHERE telegram_id = %s',
                           (now, telegram_id))
    
    def set_onboarding_step(self, telegram_id: int, step: str | None) -> None:
        """Set current onboarding step."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE users SET onboarding_step = %s, updated_at = %s WHERE telegram_id = %s',
                           (step, now, telegram_id))
    
    def log_usage(self, telegram_id: int, engine: str, tokens: int = 0, request_type: str = "message") -> None:
        """Log usage for a user."""
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO usage_logs (telegram_id, engine, tokens_used, request_type, created_at) VALUES (%s, %s, %s, %s, %s)',
                           (telegram_id, engine, tokens, request_type, now))
                cur.execute('UPDATE users SET total_requests = total_requests + 1, total_tokens = total_tokens + %s, last_activity = %s WHERE telegram_id = %s',
                           (tokens, now, telegram_id))
    
    def get_all_users(self, active_only: bool = True) -> list[User]:
        """Get all users."""
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if active_only:
                    cur.execute('SELECT * FROM users WHERE is_active = 1')
                else:
                    cur.execute('SELECT * FROM users')
                return [self._row_to_user(row) for row in cur.fetchall()]
    
    def get_user_count(self) -> int:
        """Get total number of users."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM users')
                result = cur.fetchone()
                return result[0] if result else 0
    
    def _row_to_user(self, row: dict) -> User:
        """Convert database row to User object."""
        api_keys = UserAPIKeys(
            openai_key=decrypt_key(row['openai_key']) if row.get('openai_key') else None,
            anthropic_key=decrypt_key(row['anthropic_key']) if row.get('anthropic_key') else None,
            openrouter_key=decrypt_key(row['openrouter_key']) if row.get('openrouter_key') else None,
        )
        
        settings = UserSettings(
            default_engine=row.get('default_engine') or 'claude',
            default_model=row.get('default_model') or 'gpt-5.2',
            language=row.get('language') or 'en',
            notifications=bool(row.get('notifications', 1)),
        )
        
        github_token = None
        if row.get('github_token'):
            try:
                github_token = decrypt_key(row['github_token'])
            except Exception:
                pass
        
        github = GitHubConnection(
            access_token=github_token,
            github_id=row.get('github_id'),
            github_username=row.get('github_username'),
            github_email=row.get('github_email'),
            selected_repo=row.get('github_selected_repo'),
            selected_branch=row.get('github_selected_branch'),
        )
        
        return User(
            telegram_id=row['telegram_id'],
            username=row.get('username'),
            first_name=row.get('first_name'),
            last_name=row.get('last_name'),
            api_keys=api_keys,
            settings=settings,
            github=github,
            is_active=bool(row.get('is_active', 1)),
            is_onboarded=bool(row.get('is_onboarded', 0)),
            onboarding_step=row.get('onboarding_step'),
            created_at=datetime.fromisoformat(row['created_at']) if row.get('created_at') else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row.get('updated_at') else datetime.utcnow(),
            last_activity=datetime.fromisoformat(row['last_activity']) if row.get('last_activity') else datetime.utcnow(),
            total_requests=row.get('total_requests') or 0,
            total_tokens=row.get('total_tokens') or 0,
        )


class SupabaseDatabase(Database):
    """Supabase database for user management (via REST API)."""
    
    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self.client: Client = create_client(supabase_url, supabase_key)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database - tables should be created via Supabase dashboard."""
        # Try to access users table to verify connection
        try:
            self.client.table("users").select("telegram_id").limit(1).execute()
        except Exception:
            # Table doesn't exist - create it via SQL
            # Note: In production, you should create tables via Supabase dashboard
            pass
    
    def get_user(self, telegram_id: int) -> User | None:
        """Get a user by Telegram ID."""
        response = self.client.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if response.data and len(response.data) > 0:
            return self._row_to_user(response.data[0])
        return None
    
    def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[User, bool]:
        """Get existing user or create new one."""
        user = self.get_user(telegram_id)
        if user is not None:
            if username != user.username or first_name != user.first_name:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                self.update_user(user)
            return user, False
        
        now = datetime.utcnow().isoformat()
        user_data = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "default_engine": "claude",
            "language": "en",
            "notifications": 1,
            "is_active": 1,
            "is_onboarded": 0,
            "total_requests": 0,
            "total_tokens": 0,
            "created_at": now,
            "updated_at": now,
            "last_activity": now,
        }
        
        self.client.table("users").insert(user_data).execute()
        
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
        )
        return user, True
    
    def update_user(self, user: User) -> None:
        """Update user in database."""
        now = datetime.utcnow().isoformat()
        
        openai_encrypted = encrypt_key(user.api_keys.openai_key) if user.api_keys.openai_key else None
        anthropic_encrypted = encrypt_key(user.api_keys.anthropic_key) if user.api_keys.anthropic_key else None
        openrouter_encrypted = encrypt_key(user.api_keys.openrouter_key) if user.api_keys.openrouter_key else None
        
        update_data = {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "openai_key": openai_encrypted,
            "anthropic_key": anthropic_encrypted,
            "openrouter_key": openrouter_encrypted,
            "default_engine": user.settings.default_engine,
            "default_model": getattr(user.settings, 'default_model', 'gpt-5.2'),
            "language": user.settings.language,
            "notifications": 1 if user.settings.notifications else 0,
            "is_active": 1 if user.is_active else 0,
            "is_onboarded": 1 if user.is_onboarded else 0,
            "onboarding_step": user.onboarding_step,
            "updated_at": now,
            "last_activity": now,
            "total_requests": user.total_requests,
            "total_tokens": user.total_tokens,
        }
        
        self.client.table("users").update(update_data).eq("telegram_id", user.telegram_id).execute()
    
    def set_api_key(self, telegram_id: int, provider: str, api_key: str | None) -> bool:
        """Set an API key for a user."""
        column_map = {"openai": "openai_key", "anthropic": "anthropic_key", "openrouter": "openrouter_key"}
        column = column_map.get(provider.lower())
        if column is None:
            return False
        
        encrypted_key = encrypt_key(api_key) if api_key else None
        now = datetime.utcnow().isoformat()
        
        self.client.table("users").update({column: encrypted_key, "updated_at": now}).eq("telegram_id", telegram_id).execute()
        return True
    
    def complete_onboarding(self, telegram_id: int) -> None:
        """Mark user as onboarded."""
        now = datetime.utcnow().isoformat()
        self.client.table("users").update({"is_onboarded": 1, "onboarding_step": None, "updated_at": now}).eq("telegram_id", telegram_id).execute()
    
    def set_onboarding_step(self, telegram_id: int, step: str | None) -> None:
        """Set current onboarding step."""
        now = datetime.utcnow().isoformat()
        self.client.table("users").update({"onboarding_step": step, "updated_at": now}).eq("telegram_id", telegram_id).execute()
    
    def log_usage(self, telegram_id: int, engine: str, tokens: int = 0, request_type: str = "message") -> None:
        """Log usage for a user."""
        now = datetime.utcnow().isoformat()
        # Insert usage log
        self.client.table("usage_logs").insert({
            "telegram_id": telegram_id,
            "engine": engine,
            "tokens_used": tokens,
            "request_type": request_type,
            "created_at": now,
        }).execute()
        # Update user totals - get current values first
        user = self.get_user(telegram_id)
        if user:
            self.client.table("users").update({
                "total_requests": user.total_requests + 1,
                "total_tokens": user.total_tokens + tokens,
                "last_activity": now,
            }).eq("telegram_id", telegram_id).execute()
    
    def get_all_users(self, active_only: bool = True) -> list[User]:
        """Get all users."""
        query = self.client.table("users").select("*")
        if active_only:
            query = query.eq("is_active", 1)
        response = query.execute()
        return [self._row_to_user(row) for row in response.data]
    
    def get_user_count(self) -> int:
        """Get total number of users."""
        response = self.client.table("users").select("telegram_id", count="exact").execute()
        return response.count or 0
    
    def set_github_connection(
        self,
        telegram_id: int,
        access_token: str,
        github_id: int,
        github_username: str,
        github_email: str | None = None,
    ) -> None:
        """Save GitHub connection for a user."""
        encrypted_token = encrypt_key(access_token)
        now = datetime.utcnow().isoformat()
        
        self.client.table("users").update({
            "github_token": encrypted_token,
            "github_id": github_id,
            "github_username": github_username,
            "github_email": github_email,
            "updated_at": now,
        }).eq("telegram_id", telegram_id).execute()
    
    def set_github_repo(self, telegram_id: int, repo: str, branch: str | None = None) -> None:
        """Set selected GitHub repo for a user."""
        now = datetime.utcnow().isoformat()
        self.client.table("users").update({
            "github_selected_repo": repo,
            "github_selected_branch": branch,
            "updated_at": now,
        }).eq("telegram_id", telegram_id).execute()
    
    def disconnect_github(self, telegram_id: int) -> None:
        """Remove GitHub connection for a user."""
        now = datetime.utcnow().isoformat()
        self.client.table("users").update({
            "github_token": None,
            "github_id": None,
            "github_username": None,
            "github_email": None,
            "github_selected_repo": None,
            "github_selected_branch": None,
            "updated_at": now,
        }).eq("telegram_id", telegram_id).execute()
    
    def _row_to_user(self, row: dict) -> User:
        """Convert database row to User object."""
        api_keys = UserAPIKeys(
            openai_key=decrypt_key(row.get('openai_key')) if row.get('openai_key') else None,
            anthropic_key=decrypt_key(row.get('anthropic_key')) if row.get('anthropic_key') else None,
            openrouter_key=decrypt_key(row.get('openrouter_key')) if row.get('openrouter_key') else None,
        )
        
        settings = UserSettings(
            default_engine=row.get('default_engine') or 'claude',
            default_model=row.get('default_model') or 'gpt-5.2',
            language=row.get('language') or 'en',
            notifications=bool(row.get('notifications', 1)),
        )
        
        github_token = None
        if row.get('github_token'):
            try:
                github_token = decrypt_key(row['github_token'])
            except Exception:
                pass
        
        github = GitHubConnection(
            access_token=github_token,
            github_id=row.get('github_id'),
            github_username=row.get('github_username'),
            github_email=row.get('github_email'),
            selected_repo=row.get('github_selected_repo'),
            selected_branch=row.get('github_selected_branch'),
        )
        
        return User(
            telegram_id=row['telegram_id'],
            username=row.get('username'),
            first_name=row.get('first_name'),
            last_name=row.get('last_name'),
            api_keys=api_keys,
            settings=settings,
            github=github,
            is_active=bool(row.get('is_active', 1)),
            is_onboarded=bool(row.get('is_onboarded', 0)),
            onboarding_step=row.get('onboarding_step'),
            created_at=datetime.fromisoformat(row['created_at']) if row.get('created_at') else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row.get('updated_at') else datetime.utcnow(),
            last_activity=datetime.fromisoformat(row['last_activity']) if row.get('last_activity') else datetime.utcnow(),
            total_requests=row.get('total_requests') or 0,
            total_tokens=row.get('total_tokens') or 0,
        )
