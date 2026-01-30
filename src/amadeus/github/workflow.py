"""GitHub workflow for AI-powered code changes - API-only, no local files."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Awaitable

import httpx

from amadeus.db import get_db, User
from amadeus.github.client import GitHubClient
from amadeus.logging import get_logger

logger = get_logger(__name__)

# AI API endpoints
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


@dataclass
class WorkflowProgress:
    """Progress update during workflow execution."""
    step: str
    message: str
    percentage: int
    details: str | None = None


@dataclass
class FileChange:
    """A file change to apply."""
    path: str
    content: str
    action: str = "update"  # "create", "update", "delete"


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""
    success: bool
    message: str
    commit_sha: str | None = None
    commit_url: str | None = None
    pr_url: str | None = None
    branch: str | None = None
    changed_files: list[str] | None = None
    error: str | None = None
    summary: str | None = None  # AI explanation of what was done
    next_steps: list[str] | None = None  # Suggested next steps


@dataclass
class StreamingUpdate:
    """Update during AI streaming."""
    status: str  # "thinking", "writing_file", "done"
    current_file: str | None = None
    files_so_far: list[str] | None = None
    partial_text: str | None = None


class GitHubWorkflow:
    """Orchestrate AI-powered code changes via GitHub API only.
    
    NO LOCAL FILES - everything goes through GitHub's API.
    This ensures users never touch the bot host's filesystem.
    """
    
    def __init__(self):
        self.db = get_db()
    
    async def execute(
        self,
        user: User,
        prompt: str,
        on_progress: Callable[[WorkflowProgress], Awaitable[None]] | None = None,
        on_stream: Callable[[StreamingUpdate], Awaitable[None]] | None = None,
        create_pr: bool = True,
        conversation_history: list[dict] | None = None,
        existing_branch: str | None = None,  # If set, commits to this branch instead of creating new
        existing_pr_number: int | None = None,  # The PR number if continuing
    ) -> WorkflowResult:
        """Execute workflow entirely via GitHub API.
        
        Flow:
        1. Get repo info and default branch SHA
        2. Create a new branch (or use existing_branch if continuing)
        3. Call AI to generate file changes
        4. Commit changes via API
        5. Create PR (or skip if continuing on existing PR)
        
        Args:
            conversation_history: Optional list of {"role": str, "content": str} for context
            existing_branch: If set, commits to this branch instead of creating new one
            existing_pr_number: The PR number if continuing on existing PR
        """
        # Validate GitHub connection
        if not user.github.is_connected:
            return WorkflowResult(
                success=False,
                message="GitHub not connected",
                error="Connect GitHub first with /github",
            )
        
        if not user.github.selected_repo:
            return WorkflowResult(
                success=False,
                message="No repository selected",
                error="Select a repo first with /repos",
            )
        
        repo_full_name = user.github.selected_repo
        access_token = user.github.access_token
        owner, repo_name = repo_full_name.split("/")
        
        async def progress(step: str, msg: str, pct: int, details: str | None = None):
            if on_progress:
                await on_progress(WorkflowProgress(step, msg, pct, details))
        
        try:
            async with GitHubClient(access_token) as client:
                # Step 1: Get repo info
                await progress("init", "📡 Connecting to repo...", 10)
                
                repo = await client.get_repo(owner, repo_name)
                if not repo:
                    return WorkflowResult(
                        success=False,
                        message="Repository not found",
                        error=f"Could not access {repo_full_name}",
                    )
                
                default_branch = repo.default_branch
                
                # Step 2: Get default branch SHA
                await progress("branch", "🔍 Fetching branch...", 20)
                
                base_sha = await client.get_branch_sha(owner, repo_name, default_branch)
                if not base_sha:
                    return WorkflowResult(
                        success=False,
                        message="Could not get branch SHA",
                        error=f"Could not read branch {default_branch}",
                    )
                
                # Step 3: Create feature branch (or use existing)
                if existing_branch:
                    # Use existing branch
                    branch_name = existing_branch
                    branch_created = True
                    await progress("branch", f"🌿 Using existing branch {branch_name}...", 30)
                else:
                    # Create new branch
                    branch_name = self._generate_branch_name(prompt)
                    await progress("branch", f"🌿 Creating branch {branch_name}...", 30)
                    
                    branch_created = await client.create_branch(
                        owner, repo_name, branch_name, base_sha
                    )
                    if not branch_created:
                        return WorkflowResult(
                            success=False,
                            message="Could not create branch",
                            error=f"Could not create branch {branch_name}",
                        )
                
                # Step 4: Get repo tree for context
                await progress("context", "📂 Reading repo structure...", 40)
                
                file_tree = await client.get_tree(owner, repo_name, default_branch) or []
                
                # Step 5: Generate changes with AI
                await progress("ai", "🤖 AI is thinking...", 50)
                
                # Check if user has API keys
                summary = None
                next_steps = None
                
                if not user.api_keys.anthropic_key and not user.api_keys.openai_key:
                    # No API key - use simple file creation
                    changes = self._simple_file_creation(prompt)
                else:
                    changes, summary, next_steps = await self._generate_ai_changes(
                        user, prompt, "", file_tree, conversation_history, on_stream
                    )
                
                if not changes:
                    changes = self._simple_file_creation(prompt)
                
                await progress("ai", f"📝 {len(changes)} file(s) to modify", 70)
                
                # Step 5: Commit each file via API
                await progress("commit", "💾 Commit des changements...", 80)
                
                commit = None
                changed_files = []
                
                for change in changes:
                    # Get existing file SHA if updating
                    existing_sha = None
                    if change.action == "update":
                        _, existing_sha = await client.get_file_content(
                            owner, repo_name, change.path, ref=branch_name
                        )
                    
                    commit = await client.create_or_update_file(
                        owner=owner,
                        repo=repo_name,
                        path=change.path,
                        content=change.content,
                        message=f"{'Create' if change.action == 'create' else 'Update'} {change.path}",
                        branch=branch_name,
                        sha=existing_sha,
                    )
                    
                    if commit:
                        changed_files.append(change.path)
                
                if not changed_files:
                    return WorkflowResult(
                        success=False,
                        message="No changes committed",
                        error="No files could be modified",
                    )
                
                commit_url = f"https://github.com/{repo_full_name}/commit/{commit.sha}" if commit else None
                
                # Step 6: Create PR (skip if continuing on existing PR)
                pr_url = None
                if existing_pr_number:
                    # We're continuing on an existing PR - just get the URL
                    await progress("pr", f"📝 Added commit to PR #{existing_pr_number}...", 90)
                    pr_url = f"https://github.com/{repo_full_name}/pull/{existing_pr_number}"
                elif create_pr:
                    await progress("pr", "🔀 Creating Pull Request...", 90)
                    
                    pr = await client.create_pull_request(
                        owner=owner,
                        repo=repo_name,
                        title=self._generate_pr_title(prompt),
                        body=self._generate_pr_body(prompt, changed_files),
                        head=branch_name,
                        base=default_branch,
                    )
                    
                    if pr:
                        pr_url = pr.get("html_url")
                
                await progress("done", "✅ Done!", 100)
                
                return WorkflowResult(
                    success=True,
                    message="Changes pushed successfully!",
                    commit_sha=commit.sha if commit else None,
                    commit_url=commit_url,
                    pr_url=pr_url,
                    branch=branch_name,
                    changed_files=changed_files,
                    summary=summary,
                    next_steps=next_steps,
                )
        
        except Exception as e:
            logger.exception("workflow.error", error=str(e))
            return WorkflowResult(
                success=False,
                message="Workflow failed",
                error=str(e),
            )
    
    def _generate_branch_name(self, prompt: str) -> str:
        """Generate a branch name from the prompt."""
        words = re.findall(r'\b[a-z]+\b', prompt.lower())
        keywords = [w for w in words if len(w) > 3][:3]
        
        if keywords:
            slug = "-".join(keywords)
        else:
            slug = "update"
        
        timestamp = datetime.utcnow().strftime("%m%d%H%M")
        random_suffix = secrets.token_hex(2)
        
        return f"amadeus/{slug}-{timestamp}-{random_suffix}"
    
    async def _generate_ai_changes(
        self, 
        user: User,
        prompt: str, 
        repo_context: str,
        file_tree: list[dict],
        conversation_history: list[dict] | None = None,
        on_stream: Callable[[StreamingUpdate], Awaitable[None]] | None = None,
    ) -> tuple[list[FileChange], str | None, list[str] | None]:
        """Generate actual code changes using AI with streaming.
        
        Uses the user's own API keys (BYOK).
        Includes conversation history for context continuity.
        
        Returns:
            Tuple of (changes, summary, next_steps)
        """
        # Build the system prompt with summary/next_steps
        system_prompt = """You are an expert coding assistant. You will be given a repository context and a user request.

Your task is to generate file changes to fulfill the user's request.

IMPORTANT: You MUST respond with ONLY a valid JSON object (not an array). No explanations, no markdown.

The JSON object must have this structure:
{
  "summary": "Brief explanation of what you did (1-2 sentences)",
  "next_steps": ["Step 1 the user should do next", "Step 2", ...],
  "files": [
    {"path": "src/main.py", "content": "file content here", "action": "create"}
  ]
}

Rules for files array:
- "path": the file path (e.g., "src/main.py")
- "content": the complete file content
- "action": either "create" for new files or "update" for existing files

For updates, include the FULL new file content, not just the changes.
Keep summary concise. next_steps should be 1-3 actionable items.

You may reference previous conversation context if provided to understand the full request."""

        # Build user message with context
        tree_summary = "\n".join([
            f"{'📁' if f.get('type') == 'tree' else '📄'} {f.get('path')}"
            for f in file_tree[:100]  # Limit to avoid token overflow
        ])
        
        # Build conversation context if available
        history_context = ""
        if conversation_history and len(conversation_history) > 0:
            history_parts = []
            for msg in conversation_history[-10:]:  # Last 10 messages for context
                role = "User" if msg["role"] == "user" else "Assistant"
                content = msg["content"][:500]  # Truncate long messages
                history_parts.append(f"{role}: {content}")
            history_context = "\n\n## Previous Conversation:\n" + "\n---\n".join(history_parts)
        
        user_message = f"""## Repository Structure:
{tree_summary}
{history_context}

## Current Request:
{prompt}

Generate the file changes needed. Respond with ONLY a JSON object with summary, next_steps, and files."""

        # Get user's preferred model
        user_model = getattr(user.settings, 'default_model', None) or "gpt-5.2"
        
        # Check which API key is available and select appropriate model
        has_anthropic = bool(user.api_keys.anthropic_key)
        has_openai = bool(user.api_keys.openai_key)
        
        if not has_anthropic and not has_openai:
            raise ValueError("No API keys configured. Use /settings to add your Anthropic or OpenAI key.")
        
        result = None
        
        if user_model.startswith("claude"):
            if has_anthropic:
                result = await self._call_anthropic_streaming(
                    user.api_keys.anthropic_key, system_prompt, user_message, user_model, on_stream
                )
            elif has_openai:
                logger.info("workflow.fallback", from_model=user_model, to_model="gpt-5.2", reason="no_anthropic_key")
                result = await self._call_openai_streaming(
                    user.api_keys.openai_key, system_prompt, user_message, "gpt-5.2", on_stream
                )
        else:
            # OpenAI models (gpt-5.2, codex, o1, o3, etc.)
            if has_openai:
                result = await self._call_openai_streaming(
                    user.api_keys.openai_key, system_prompt, user_message, user_model, on_stream
                )
            elif has_anthropic:
                logger.info("workflow.fallback", from_model=user_model, to_model="claude-sonnet-4-20250514", reason="no_openai_key")
                result = await self._call_anthropic_streaming(
                    user.api_keys.anthropic_key, system_prompt, user_message, "claude-sonnet-4-20250514", on_stream
                )
        
        if not result or not result[0]:
            raise ValueError("AI returned no changes. Try a more specific request.")
        
        return result
    
    async def _call_anthropic_streaming(
        self, 
        api_key: str, 
        system: str, 
        user_msg: str, 
        model: str = "claude-sonnet-4-20250514",
        on_stream: Callable[[StreamingUpdate], Awaitable[None]] | None = None,
    ) -> tuple[list[FileChange], str | None, list[str] | None]:
        """Call Anthropic Claude API with streaming."""
        try:
            timeout = 300.0 if "opus" in model else 180.0
            full_content = ""
            files_detected: list[str] = []
            last_update_len = 0
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    ANTHROPIC_API_URL,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 16384,
                        "system": system,
                        "messages": [{"role": "user", "content": user_msg}],
                        "stream": True,
                    },
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        logger.warning("anthropic.error", status=response.status_code, body=error_body[:200])
                        return [], None, None
                    
                    # Notify we're starting
                    if on_stream:
                        await on_stream(StreamingUpdate(status="thinking"))
                    
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            event = json.loads(data_str)
                            
                            # Handle content_block_delta events
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    full_content += text
                                    
                                    # Check for new file paths in the stream
                                    new_files = re.findall(r'"path"\s*:\s*"([^"]+)"', full_content[last_update_len:])
                                    for f in new_files:
                                        if f not in files_detected:
                                            files_detected.append(f)
                                            if on_stream:
                                                await on_stream(StreamingUpdate(
                                                    status="writing_file",
                                                    current_file=f,
                                                    files_so_far=files_detected.copy(),
                                                ))
                                    last_update_len = len(full_content)
                        except json.JSONDecodeError:
                            continue
            
            # Notify done streaming
            if on_stream:
                await on_stream(StreamingUpdate(status="done", files_so_far=files_detected))
            
            return self._parse_ai_response_with_meta(full_content)
        
        except Exception as e:
            logger.exception("anthropic.call_error", error=str(e))
            return [], None, None
    
    async def _call_openai_streaming(
        self, 
        api_key: str, 
        system: str, 
        user_msg: str, 
        model: str = "gpt-5.2",
        on_stream: Callable[[StreamingUpdate], Awaitable[None]] | None = None,
    ) -> tuple[list[FileChange], str | None, list[str] | None]:
        """Call OpenAI API with streaming."""
        try:
            full_content = ""
            files_detected: list[str] = []
            last_update_len = 0
            
            is_gpt5 = model.startswith("gpt-5") or model.startswith("o3") or model.startswith("o1")
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "stream": True,
            }
            
            if is_gpt5:
                payload["max_completion_tokens"] = 16384
            else:
                payload["max_tokens"] = 16384
            
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    OPENAI_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        logger.warning("openai.error", status=response.status_code, body=error_body[:200])
                        return [], None, None
                    
                    # Notify we're starting
                    if on_stream:
                        await on_stream(StreamingUpdate(status="thinking"))
                    
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            event = json.loads(data_str)
                            choices = event.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                text = delta.get("content", "")
                                if text:
                                    full_content += text
                                    
                                    # Check for new file paths in the stream
                                    new_files = re.findall(r'"path"\s*:\s*"([^"]+)"', full_content[last_update_len:])
                                    for f in new_files:
                                        if f not in files_detected:
                                            files_detected.append(f)
                                            if on_stream:
                                                await on_stream(StreamingUpdate(
                                                    status="writing_file",
                                                    current_file=f,
                                                    files_so_far=files_detected.copy(),
                                                ))
                                    last_update_len = len(full_content)
                        except json.JSONDecodeError:
                            continue
            
            # Notify done streaming
            if on_stream:
                await on_stream(StreamingUpdate(status="done", files_so_far=files_detected))
            
            return self._parse_ai_response_with_meta(full_content)
        
        except Exception as e:
            logger.exception("openai.call_error", error=str(e))
            return [], None, None
    
    def _parse_ai_response_with_meta(self, content: str) -> tuple[list[FileChange], str | None, list[str] | None]:
        """Parse AI response with summary and next_steps."""
        try:
            content = content.strip()
            
            # Remove markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            
            data = json.loads(content)
            
            summary = data.get("summary")
            next_steps = data.get("next_steps", [])
            
            # Handle both new format (with "files" key) and old format (array directly)
            if "files" in data:
                files_data = data["files"]
            elif isinstance(data, list):
                files_data = data
            else:
                files_data = [data]
            
            if not isinstance(files_data, list):
                files_data = [files_data]
            
            changes = []
            for item in files_data:
                if isinstance(item, dict) and "path" in item and "content" in item:
                    changes.append(FileChange(
                        path=item["path"],
                        content=item["content"],
                        action=item.get("action", "create"),
                    ))
            
            return changes, summary, next_steps if next_steps else None
        
        except json.JSONDecodeError as e:
            logger.warning("ai.parse_error", error=str(e), content=content[:200])
            return [], None, None
    
    def _parse_ai_response(self, content: str) -> list[FileChange]:
        """Parse AI response into FileChange objects (legacy, no meta)."""
        changes, _, _ = self._parse_ai_response_with_meta(content)
        return changes
    
    def _simple_file_creation(self, prompt: str) -> list[FileChange]:
        """Fallback: create a simple file based on prompt patterns."""
        prompt_lower = prompt.lower()
        
        # Try to extract filename from prompt
        file_match = re.search(r'(?:file|create|add)\s+(?:named?\s+)?["\']?([a-zA-Z0-9_\-\.]+)["\']?', prompt_lower)
        
        if file_match:
            filename = file_match.group(1)
            # Ensure it has an extension
            if "." not in filename:
                filename += ".txt"
            
            return [FileChange(
                path=filename,
                content=f"# {filename}\n\nCreated by Amadeus Pocket 🎭\n\nPrompt: {prompt}\n",
                action="create",
            )]
        
        # Default fallback
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return [FileChange(
            path=f"amadeus_change_{timestamp}.md",
            content=f"# Change Request\n\n**Prompt:** {prompt}\n\n*Generated by Amadeus Pocket*\n",
            action="create",
        )]
    
    def _generate_pr_title(self, prompt: str) -> str:
        """Generate PR title from prompt."""
        title = prompt[:60]
        if len(prompt) > 60:
            title += "..."
        return f"🤖 {title}"
    
    def _generate_pr_body(
        self,
        prompt: str,
        changed_files: list[str],
    ) -> str:
        """Generate PR body/description."""
        files_list = "\n".join(f"- `{f}`" for f in changed_files)
        
        return f"""## 🤖 AI-Generated Changes

**Prompt:**
> {prompt}

### Changed Files
{files_list}

### How to Review
1. Check the changed files above
2. Review the diff for correctness
3. Test locally if needed
4. Approve and merge!

---
*This PR was automatically generated by [Amadeus Pocket](https://amadeus.dev) 🎭*
"""
