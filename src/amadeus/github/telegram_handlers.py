"""Telegram command handlers for GitHub integration (PAT-based)."""

from __future__ import annotations

from amadeus.db import get_db, User
from amadeus.db.multiuser_handler import OnboardingMessage
from amadeus.github.client import GitHubClient
from amadeus.github.workflow import GitHubWorkflow
from amadeus.logging import get_logger

logger = get_logger(__name__)

# GitHub token URL for instructions
GITHUB_TOKEN_URL = "https://github.com/settings/tokens/new?scopes=repo&description=Amadeus%20Pocket"


class GitHubTelegramHandler:
    """Handle GitHub-related Telegram commands using PAT tokens."""
    
    def __init__(self):
        self.db = get_db()
        self.workflow = GitHubWorkflow()
        # Track users waiting to input token
        self._awaiting_token: set[int] = set()
    
    def is_awaiting_token(self, telegram_id: int) -> bool:
        """Check if user is expected to send a token."""
        return telegram_id in self._awaiting_token
    
    async def handle_github_command(self, user: User) -> OnboardingMessage:
        """Handle /github command - show status or start token setup."""
        if user.github.is_connected:
            return self._github_status_message(user)
        else:
            return self._github_connect_message(user)
    
    def _github_connect_message(self, user: User) -> OnboardingMessage:
        """Message to start GitHub token setup."""
        self._awaiting_token.add(user.telegram_id)
        
        return OnboardingMessage(
            text=f"""🐙 <b>Connect GitHub</b>

To connect your GitHub, I need a <b>Personal Access Token</b>.

<b>How to create a token:</b>
1. Click the button below
2. Sign in to GitHub if needed
3. Click "Generate token"
4. Copy the token and send it to me here

⚠️ <i>Your token will be encrypted and stored securely.</i>""",
            buttons=[
                [{"text": "🔗 Create GitHub Token", "url": GITHUB_TOKEN_URL}],
                [{"text": "❓ Why do I need a token?", "callback_data": "github_why_token"}],
                [{"text": "❌ Cancel", "callback_data": "github_cancel"}],
            ],
        )
    
    def _github_status_message(self, user: User) -> OnboardingMessage:
        """Show GitHub connection status."""
        gh = user.github
        
        repo_line = ""
        if gh.selected_repo:
            repo_line = f"\n📁 <b>Active repo:</b> <code>{gh.selected_repo}</code>"
            if gh.selected_branch:
                repo_line += f"\n🌿 <b>Branch:</b> <code>{gh.selected_branch}</code>"
        
        return OnboardingMessage(
            text=f"""🐙 <b>GitHub Connected</b>

👤 <b>Account:</b> @{gh.github_username}{repo_line}

<b>Commands:</b>
• <code>/repos</code> - List your repositories
• <code>/repo owner/name</code> - Select a repository
• <code>/tree</code> - View repo file structure
• <code>/commits</code> - View recent commits
• <code>/github disconnect</code> - Disconnect GitHub""",
            buttons=[
                [{"text": "📂 My Repositories", "callback_data": "github_list_repos"}],
                [{"text": "🌳 View Tree", "callback_data": "github_tree"}],
                [{"text": "🔌 Disconnect", "callback_data": "github_disconnect"}],
            ],
        )
    
    async def handle_token_input(self, user: User, token: str) -> OnboardingMessage:
        """Handle when user sends a GitHub token."""
        token = token.strip()
        self._awaiting_token.discard(user.telegram_id)
        
        # Validate token format
        if not (token.startswith("ghp_") or token.startswith("github_pat_")):
            return OnboardingMessage(
                text="""❌ <b>Invalid token</b>

The token should start with <code>ghp_</code> or <code>github_pat_</code>

Try again with a valid token or /github to restart.""",
            )
        
        # Test the token by getting user info
        try:
            async with GitHubClient(token) as client:
                github_user = await client.get_user()
                
                if not github_user:
                    return OnboardingMessage(
                        text="""❌ <b>Invalid or expired token</b>

Could not connect to GitHub with this token.
Make sure it has <code>repo</code> permissions.

/github to try again.""",
                    )
                
                # Save to database
                self.db.set_github_connection(
                    telegram_id=user.telegram_id,
                    access_token=token,
                    github_id=github_user.get("id", 0),
                    github_username=github_user.get("login", "unknown"),
                    github_email=github_user.get("email"),
                )
                
                logger.info(
                    "github.connected",
                    telegram_id=user.telegram_id,
                    github_username=github_user.get("login"),
                )
                
                return OnboardingMessage(
                    text=f"""✅ <b>GitHub Connected!</b>

👤 Connected as <b>@{github_user.get("login")}</b>

Now select a repository to start coding!""",
                    buttons=[
                        [{"text": "📂 View my repos", "callback_data": "github_list_repos"}],
                    ],
                )
        
        except Exception as e:
            logger.exception("github.token_validation_error", error=str(e))
            return OnboardingMessage(
                text=f"""❌ <b>Connection error</b>

{str(e)[:200]}

/github to try again.""",
            )
    
    async def handle_repos_command(self, user: User) -> OnboardingMessage:
        """Handle /repos command - list user's repositories."""
        if not user.github.is_connected:
            return OnboardingMessage(
                text="❌ Connect GitHub first with /github",
            )
        
        try:
            async with GitHubClient(user.github.access_token) as client:
                repos = await client.list_repos(per_page=10)
            
            if not repos:
                return OnboardingMessage(
                    text="📂 No repositories found. Create one on GitHub first!",
                )
            
            # Build repo list with buttons
            buttons = []
            for repo in repos[:8]:
                icon = "🔒" if repo.private else "📂"
                buttons.append([{
                    "text": f"{icon} {repo.full_name}",
                    "callback_data": f"repo:{repo.full_name}",
                }])
            
            buttons.append([{"text": "🔄 Refresh", "callback_data": "github_list_repos"}])
            
            return OnboardingMessage(
                text="📂 <b>Your Repositories</b>\n\nSelect a repo to work with:",
                buttons=buttons,
            )
        
        except Exception as e:
            logger.exception("github.list_repos_error", error=str(e))
            return OnboardingMessage(
                text=f"❌ Error: {str(e)[:200]}",
            )
    
    async def handle_repo_selection(
        self,
        user: User,
        repo_full_name: str,
    ) -> OnboardingMessage:
        """Handle repository selection."""
        if not user.github.is_connected:
            return OnboardingMessage(text="❌ Connect GitHub first with /github")
        
        try:
            async with GitHubClient(user.github.access_token) as client:
                owner, name = repo_full_name.split("/", 1)
                repo = await client.get_repo(owner, name)
                
                if not repo:
                    return OnboardingMessage(
                        text=f"❌ Could not access <code>{repo_full_name}</code>",
                    )
                
                # Save selection
                self.db.set_github_repo(
                    user.telegram_id,
                    repo_full_name,
                    repo.default_branch,
                )
                
                return OnboardingMessage(
                    text=f"""✅ <b>Repository selected!</b>

📁 <code>{repo_full_name}</code>
📝 {repo.description or 'No description'}
🌿 Branch: <code>{repo.default_branch}</code>

<b>You're ready!</b> Just send me what you want to code:

<i>Examples:</i>
• "Add a login page"
• "Create a REST API for todos"
• "Fix the bug in auth.js"

I'll modify the code and create a PR! 🚀""",
                )
        
        except Exception as e:
            logger.exception("github.repo_selection_error", error=str(e))
            return OnboardingMessage(text=f"❌ Error: {str(e)[:200]}")
    
    async def handle_disconnect(self, user: User) -> OnboardingMessage:
        """Disconnect GitHub."""
        self.db.disconnect_github(user.telegram_id)
        self._awaiting_token.discard(user.telegram_id)
        
        return OnboardingMessage(
            text="""✅ <b>GitHub Disconnected</b>

Your GitHub account has been disconnected.
Use /github to reconnect.""",
        )
    
    async def handle_tree_command(self, user: User) -> OnboardingMessage:
        """Handle /tree command - show repository structure."""
        if not user.github.is_connected:
            return OnboardingMessage(text="❌ Connect GitHub first with /github")
        
        if not user.github.selected_repo:
            return OnboardingMessage(text="❌ Select a repository first with /repos")
        
        try:
            async with GitHubClient(user.github.access_token) as client:
                owner, repo = user.github.selected_repo.split("/", 1)
                branch = user.github.selected_branch or "main"
                
                tree = await client.get_tree(owner, repo, branch)
                
                if not tree:
                    return OnboardingMessage(text="❌ Could not fetch repository structure")
                
                # Build tree display (limit to avoid message too long)
                lines = [f"🌳 <b>{user.github.selected_repo}</b> ({branch})\n"]
                
                # Sort: directories first, then files
                dirs = [f for f in tree if f.get("type") == "tree"]
                files = [f for f in tree if f.get("type") == "blob"]
                
                # Show directory structure
                shown = 0
                max_items = 50
                
                for item in sorted(dirs, key=lambda x: x.get("path", "")):
                    if shown >= max_items:
                        break
                    path = item.get("path", "")
                    depth = path.count("/")
                    indent = "  " * depth
                    name = path.split("/")[-1]
                    lines.append(f"{indent}📁 <code>{name}/</code>")
                    shown += 1
                
                for item in sorted(files, key=lambda x: x.get("path", "")):
                    if shown >= max_items:
                        break
                    path = item.get("path", "")
                    depth = path.count("/")
                    indent = "  " * depth
                    name = path.split("/")[-1]
                    # Icon based on extension
                    ext = name.split(".")[-1] if "." in name else ""
                    icon = self._get_file_icon(ext)
                    lines.append(f"{indent}{icon} <code>{name}</code>")
                    shown += 1
                
                if len(tree) > max_items:
                    lines.append(f"\n<i>...and {len(tree) - max_items} more items</i>")
                
                lines.append(f"\n📊 <b>Total:</b> {len(dirs)} folders, {len(files)} files")
                
                return OnboardingMessage(text="\n".join(lines))
        
        except Exception as e:
            logger.exception("github.tree_error", error=str(e))
            return OnboardingMessage(text=f"❌ Error: {str(e)[:200]}")
    
    def _get_file_icon(self, ext: str) -> str:
        """Get icon for file extension."""
        icons = {
            "py": "🐍", "js": "📜", "ts": "📘", "jsx": "⚛️", "tsx": "⚛️",
            "html": "🌐", "css": "🎨", "scss": "🎨", "json": "📋",
            "md": "📝", "txt": "📄", "yml": "⚙️", "yaml": "⚙️",
            "sh": "🖥️", "bash": "🖥️", "sql": "🗃️",
            "png": "🖼️", "jpg": "🖼️", "svg": "🖼️", "gif": "🖼️",
            "go": "🐹", "rs": "🦀", "java": "☕", "rb": "💎",
            "toml": "⚙️", "lock": "🔒", "env": "🔐",
        }
        return icons.get(ext.lower(), "📄")
    
    async def handle_commits_command(self, user: User) -> OnboardingMessage:
        """Handle /commits command - show recent commits."""
        if not user.github.is_connected:
            return OnboardingMessage(text="❌ Connect GitHub first with /github")
        
        if not user.github.selected_repo:
            return OnboardingMessage(text="❌ Select a repository first with /repos")
        
        try:
            async with GitHubClient(user.github.access_token) as client:
                owner, repo = user.github.selected_repo.split("/", 1)
                branch = user.github.selected_branch or "main"
                
                commits = await client.get_recent_commits(owner, repo, branch, per_page=10)
                
                if not commits:
                    return OnboardingMessage(text="📭 No commits found")
                
                lines = [f"📜 <b>Recent commits on {branch}</b>\n"]
                
                for commit in commits:
                    sha = commit.get("sha", "")[:7]
                    msg = commit.get("commit", {}).get("message", "").split("\n")[0][:50]
                    author = commit.get("commit", {}).get("author", {}).get("name", "Unknown")
                    date = commit.get("commit", {}).get("author", {}).get("date", "")[:10]
                    
                    lines.append(f"• <code>{sha}</code> {msg}")
                    lines.append(f"  <i>by {author} on {date}</i>\n")
                
                return OnboardingMessage(text="\n".join(lines))
        
        except Exception as e:
            logger.exception("github.commits_error", error=str(e))
            return OnboardingMessage(text=f"❌ Error: {str(e)[:200]}")
    
    async def handle_callback(
        self,
        user: User,
        callback_data: str,
    ) -> OnboardingMessage | None:
        """Handle GitHub-related callbacks."""
        
        if callback_data == "github_why_token":
            return OnboardingMessage(
                text="""❓ <b>Why do I need a token?</b>

Amadeus Pocket uses your GitHub token to:

1. 📖 <b>Read your code</b> - Clone your repos
2. ✏️ <b>Modify files</b> - Apply AI changes
3. 📤 <b>Push commits</b> - Save to GitHub
4. 🔀 <b>Create PRs</b> - For review before merge

<b>Security:</b>
• Token is encrypted in the database
• Never shared with third parties
• You can revoke it on GitHub anytime

<b>Required permissions:</b>
• <code>repo</code> - Full repository access""",
                buttons=[
                    [{"text": "⬅️ Back", "callback_data": "github_back"}],
                ],
            )
        
        if callback_data == "github_back":
            return await self.handle_github_command(user)
        
        if callback_data == "github_cancel":
            self._awaiting_token.discard(user.telegram_id)
            return OnboardingMessage(
                text="❌ GitHub connection cancelled.\n\n/github to try again.",
            )
        
        if callback_data == "github_disconnect":
            return await self.handle_disconnect(user)
        
        if callback_data == "github_list_repos":
            return await self.handle_repos_command(user)
        
        if callback_data == "github_tree":
            return await self.handle_tree_command(user)
        
        if callback_data.startswith("repo:"):
            repo_name = callback_data[5:]  # Remove "repo:" prefix
            return await self.handle_repo_selection(user, repo_name)
        
        return None
