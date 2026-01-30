# Amadeus Pocket OS

> Self-hosted AI coding agent bridge - control your AI assistants from anywhere

Amadeus Pocket OS is the open-source, self-hostable version of Amadeus Pocket. Run your own instance to maintain complete control over your data and API keys.

## Why Self-Host?

- **Privacy**: Your API keys and conversations stay on your infrastructure
- **Control**: Customize the deployment to your needs
- **Security**: No third-party access to your credentials
- **Cost**: Use your own API keys directly with providers

## Features

- **Multi-engine support**: Works with Claude, OpenAI, OpenRouter, Codex, and more
- **Telegram integration**: Control AI agents from your phone via Telegram bot
- **GitHub integration**: Connect repositories, create branches, push changes
- **Multi-user support**: Host for yourself or a team
- **Session management**: Resume conversations across devices
- **File transfer**: Send and receive files via Telegram
- **Encrypted storage**: API keys are encrypted at rest

## Requirements

- Python 3.12+
- PostgreSQL or SQLite (for data storage)
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- At least one AI provider API key (Anthropic, OpenAI, or OpenRouter)

## Quick Start

### 1. Clone the Repository

```bash
git clone git@github.com:X-Ventures/Amadeus-Pocket-OS.git
cd Amadeus-Pocket-OS
```

### 2. Install Dependencies

Using uv (recommended):
```bash
uv sync
```

Or using pip:
```bash
pip install -e .
```

### 3. Configure Environment

Copy the example environment file and configure:

```bash
cp env.example .env
```

Edit `.env` with your settings:

```bash
# Required: Telegram Bot Token
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Database (choose one)
DATABASE_URL=postgresql://user:pass@host:5432/amadeus
# Or for SQLite, leave DATABASE_URL empty (uses ~/.amadeus/amadeus.db)

# Encryption key for stored API keys (generate with: openssl rand -hex 32)
AMADEUS_ENCRYPTION_KEY=your_encryption_key_here

# Optional: Supabase (alternative to direct PostgreSQL)
# SUPABASE_URL=https://your-project.supabase.co
# SUPABASE_KEY=your_supabase_anon_key

# Optional: Fly.io for remote workspaces
# FLY_API_TOKEN=your_fly_token
# FLY_APP_NAME=your_app_name

# Optional: GitHub OAuth (for OAuth flow instead of PAT)
# GITHUB_CLIENT_ID=your_client_id
# GITHUB_CLIENT_SECRET=your_client_secret
```

### 4. Create Telegram Bot

1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token to your `.env` file
4. Send `/setcommands` to BotFather and paste:
   ```
   start - Start the bot
   help - Show help message
   settings - View your settings
   engine - Change AI engine
   github - Manage GitHub connection
   repos - List GitHub repositories
   cancel - Cancel current operation
   ```

### 5. Run the Bot

```bash
# Multi-user mode (recommended for self-hosting)
amadeus multiuser

# Or single-user mode with config file
amadeus run
```

## Deployment Options

### Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template)

1. Fork this repository
2. Create a new Railway project
3. Add PostgreSQL database
4. Set environment variables
5. Deploy

### Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . .

RUN pip install -e .

CMD ["amadeus", "multiuser"]
```

Build and run:
```bash
docker build -t amadeus-pocket .
docker run -d --env-file .env amadeus-pocket
```

### Fly.io

```bash
fly launch
fly secrets set TELEGRAM_BOT_TOKEN=xxx DATABASE_URL=xxx AMADEUS_ENCRYPTION_KEY=xxx
fly deploy
```

### Manual (Systemd)

Create `/etc/systemd/system/amadeus.service`:

```ini
[Unit]
Description=Amadeus Pocket Bot
After=network.target

[Service]
Type=simple
User=amadeus
WorkingDirectory=/opt/amadeus
EnvironmentFile=/opt/amadeus/.env
ExecStart=/opt/amadeus/.venv/bin/amadeus multiuser
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable amadeus
sudo systemctl start amadeus
```

## Configuration

### Single User Mode

Create `~/.amadeus/config.toml`:

```toml
default_engine = "claude"

[transports.telegram]
bot_token = "your_bot_token"
chat_id = 123456789  # Your Telegram chat ID

[projects.myproject]
path = "~/dev/myproject"
default_engine = "codex"
```

### Multi-User Mode

Multi-user mode uses the database to store per-user configurations. Users configure their settings via Telegram bot commands.

## Architecture

```
src/amadeus/
├── cli/           # Command-line interface
├── db/            # Database models and encryption
├── github/        # GitHub API client and workflows
├── runners/       # AI engine backends (claude, codex, openai)
├── schemas/       # Response parsing schemas
├── telegram/      # Telegram bot and handlers
├── sessions/      # Session management
└── utils/         # Utilities (git, paths, streams)
```

## Security Considerations

1. **API Key Encryption**: All stored API keys are encrypted using Fernet symmetric encryption
2. **Environment Variables**: Never commit `.env` files to version control
3. **Database Security**: Use strong passwords for PostgreSQL
4. **Telegram Security**: Only allow trusted user IDs in production

## Database Schema

Tables are auto-created on first run:

- `users`: User profiles and encrypted API keys
- `user_sessions`: AI conversation sessions
- `usage_logs`: Token usage tracking

For Supabase, you may need to create tables manually via the dashboard.

## Troubleshooting

### Bot not responding
- Check `TELEGRAM_BOT_TOKEN` is correct
- Verify bot is running: `ps aux | grep amadeus`
- Check logs for errors

### Database connection failed
- Verify `DATABASE_URL` format
- Check PostgreSQL is running
- Ensure database exists

### API key decryption failed
- `AMADEUS_ENCRYPTION_KEY` must match the key used for encryption
- If key is lost, users need to re-enter their API keys

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details.

---

*Self-host your AI assistant bridge with Amadeus Pocket OS*
