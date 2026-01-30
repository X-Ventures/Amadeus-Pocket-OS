-- Create sessions table for persistent conversation history
-- Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    repo TEXT DEFAULT '',
    messages JSONB DEFAULT '[]'::jsonb,
    model TEXT DEFAULT 'claude-3-5-sonnet-20241022',
    created_at TEXT,
    updated_at TEXT
);

-- Index for fast lookups by user
CREATE INDEX IF NOT EXISTS idx_sessions_telegram_id ON sessions(telegram_id);

-- Index for user+repo combination lookups
CREATE INDEX IF NOT EXISTS idx_sessions_telegram_repo ON sessions(telegram_id, repo);

-- Enable Row Level Security (optional but recommended)
-- ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
