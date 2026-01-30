"""GitHub API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

GITHUB_API_URL = "https://api.github.com"


@dataclass
class Repository:
    """GitHub repository info."""
    id: int
    name: str
    full_name: str
    description: str | None
    private: bool
    default_branch: str
    clone_url: str
    ssh_url: str
    html_url: str


@dataclass
class Branch:
    """GitHub branch info."""
    name: str
    sha: str
    protected: bool


@dataclass 
class Commit:
    """GitHub commit info."""
    sha: str
    message: str
    html_url: str


class GitHubClient:
    """Client for GitHub API operations."""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self._client: httpx.AsyncClient | None = None
    
    async def __aenter__(self) -> "GitHubClient":
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30.0,
        )
        return self
    
    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> tuple[int, Any]:
        """Make API request."""
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context.")
        
        url = f"{GITHUB_API_URL}{endpoint}"
        response = await self._client.request(method, url, **kwargs)
        
        try:
            data = response.json()
        except Exception:
            data = None
        
        return response.status_code, data
    
    async def get_user(self) -> dict | None:
        """Get authenticated user info."""
        status, data = await self._request("GET", "/user")
        return data if status == 200 else None
    
    async def list_repos(
        self,
        sort: str = "updated",
        per_page: int = 30,
    ) -> list[Repository]:
        """List user's repositories."""
        status, data = await self._request(
            "GET",
            "/user/repos",
            params={"sort": sort, "per_page": per_page},
        )
        
        if status != 200 or not data:
            return []
        
        return [
            Repository(
                id=repo["id"],
                name=repo["name"],
                full_name=repo["full_name"],
                description=repo.get("description"),
                private=repo["private"],
                default_branch=repo.get("default_branch", "main"),
                clone_url=repo["clone_url"],
                ssh_url=repo["ssh_url"],
                html_url=repo["html_url"],
            )
            for repo in data
        ]
    
    async def get_repo(self, owner: str, repo: str) -> Repository | None:
        """Get repository info."""
        status, data = await self._request("GET", f"/repos/{owner}/{repo}")
        
        if status != 200 or not data:
            return None
        
        return Repository(
            id=data["id"],
            name=data["name"],
            full_name=data["full_name"],
            description=data.get("description"),
            private=data["private"],
            default_branch=data.get("default_branch", "main"),
            clone_url=data["clone_url"],
            ssh_url=data["ssh_url"],
            html_url=data["html_url"],
        )
    
    async def list_branches(self, owner: str, repo: str) -> list[Branch]:
        """List repository branches."""
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/branches",
        )
        
        if status != 200 or not data:
            return []
        
        return [
            Branch(
                name=branch["name"],
                sha=branch["commit"]["sha"],
                protected=branch.get("protected", False),
            )
            for branch in data
        ]
    
    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        from_sha: str,
    ) -> bool:
        """Create a new branch."""
        status, _ = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": from_sha,
            },
        )
        return status == 201
    
    async def get_branch_sha(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> str | None:
        """Get the SHA of a branch's HEAD."""
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
        )
        
        if status != 200 or not data:
            return None
        
        return data.get("object", {}).get("sha")
    
    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Get file content. Returns (content, sha)."""
        params = {}
        if ref:
            params["ref"] = ref
        
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
        )
        
        if status != 200 or not data:
            return None, None
        
        import base64
        content = base64.b64decode(data.get("content", "")).decode("utf-8")
        return content, data.get("sha")
    
    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> Commit | None:
        """Create or update a file."""
        import base64
        
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        
        if sha:
            payload["sha"] = sha
        
        status, data = await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{path}",
            json=payload,
        )
        
        if status not in (200, 201) or not data:
            return None
        
        commit_data = data.get("commit", {})
        return Commit(
            sha=commit_data.get("sha", ""),
            message=commit_data.get("message", message),
            html_url=commit_data.get("html_url", ""),
        )
    
    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict | None:
        """Create a pull request."""
        status, data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        
        return data if status == 201 else None
    
    async def get_tree(
        self,
        owner: str,
        repo: str,
        ref: str = "main",
        recursive: bool = True,
    ) -> list[dict] | None:
        """Get repository file tree."""
        # First get the commit SHA for the ref
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/ref/heads/{ref}",
        )
        
        if status != 200 or not data:
            return None
        
        tree_sha = data.get("object", {}).get("sha")
        if not tree_sha:
            return None
        
        # Get the tree
        params = {"recursive": "1"} if recursive else {}
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params=params,
        )
        
        if status != 200 or not data:
            return None
        
        return data.get("tree", [])
    
    async def get_recent_commits(
        self,
        owner: str,
        repo: str,
        branch: str = "main",
        per_page: int = 5,
    ) -> list[dict]:
        """Get recent commits on a branch."""
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits",
            params={"sha": branch, "per_page": per_page},
        )
        
        if status != 200 or not data:
            return []
        
        return data
    
    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 10,
    ) -> list[dict]:
        """List pull requests."""
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": per_page},
        )
        
        if status != 200 or not data:
            return []
        
        return data
    
    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict | None:
        """Get a specific pull request."""
        status, data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
        )
        
        return data if status == 200 else None
    
    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: str | None = None,
        merge_method: str = "squash",  # squash, merge, or rebase
    ) -> tuple[bool, str]:
        """Merge a pull request. Returns (success, message)."""
        payload = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        
        status, data = await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json=payload,
        )
        
        if status == 200:
            return True, data.get("message", "PR merged successfully")
        elif status == 405:
            return False, "PR cannot be merged (conflicts or not mergeable)"
        elif status == 404:
            return False, "PR not found"
        elif status == 403:
            return False, "You don't have permission to merge this PR"
        else:
            return False, data.get("message", f"Merge failed (status {status})")