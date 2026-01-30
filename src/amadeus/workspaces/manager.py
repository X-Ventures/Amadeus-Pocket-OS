"""Workspace Manager - Ephemeral coding environments per user."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Awaitable

from amadeus.db import get_db, User
from amadeus.logging import get_logger
from .flyio_client import FlyioClient, MachineConfig, MachineStatus, Machine

logger = get_logger(__name__)

# Default workspace image with coding tools
DEFAULT_WORKSPACE_IMAGE = "ghcr.io/amadeus-pocket/workspace:latest"

# Fallback to Ubuntu with basic tools
FALLBACK_IMAGE = "ubuntu:22.04"


@dataclass
class WorkspaceConfig:
    """Configuration for a user workspace."""
    cpus: int = 1
    memory_mb: int = 512  # 512MB default
    timeout_minutes: int = 30  # Auto-destroy after 30 min
    region: str = "iad"


@dataclass 
class Workspace:
    """Represents an active user workspace."""
    id: str
    telegram_id: int
    machine_id: str
    repo: str | None
    status: str
    private_ip: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    
    @property
    def is_expired(self) -> bool:
        """Check if workspace has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "telegram_id": self.telegram_id,
            "machine_id": self.machine_id,
            "repo": self.repo,
            "status": self.status,
            "private_ip": self.private_ip,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class CommandResult:
    """Result of running a command in workspace."""
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0


class WorkspaceManager:
    """Manage ephemeral workspaces for users.
    
    Each user can have one active workspace at a time.
    Workspaces are Fly.io machines that:
    - Clone the user's selected repo
    - Have full project context
    - Can run any command (npm, pip, cargo, etc.)
    - Auto-destroy after timeout
    """
    
    def __init__(self):
        self.db = get_db()
        self._workspaces: dict[int, Workspace] = {}  # telegram_id -> workspace
    
    async def get_or_create_workspace(
        self,
        user: User,
        config: WorkspaceConfig | None = None,
        on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> Workspace | None:
        """Get existing workspace or create new one.
        
        Args:
            user: User requesting workspace
            config: Optional workspace configuration
            on_progress: Progress callback (message, percentage)
            
        Returns:
            Active workspace or None on failure
        """
        config = config or WorkspaceConfig()
        
        async def progress(msg: str, pct: int):
            if on_progress:
                await on_progress(msg, pct)
        
        # Check for existing workspace
        existing = self._workspaces.get(user.telegram_id)
        if existing and not existing.is_expired:
            await progress("Using existing workspace", 100)
            return existing
        
        # Destroy expired workspace if exists
        if existing and existing.is_expired:
            await self.destroy_workspace(user)
        
        # Create new workspace
        await progress("🚀 Creating workspace...", 10)
        
        flyio_token = os.environ.get("FLY_API_TOKEN")
        flyio_app = os.environ.get("FLY_APP_NAME")
        
        if not flyio_token or not flyio_app:
            logger.error("workspace.flyio_not_configured")
            return None
        
        workspace_id = secrets.token_hex(8)
        machine_name = f"ws-{user.telegram_id}-{workspace_id[:8]}"
        
        # Build environment variables for the workspace
        env = {
            "WORKSPACE_ID": workspace_id,
            "TELEGRAM_ID": str(user.telegram_id),
        }
        
        # Add GitHub token if available
        if user.github.access_token:
            env["GITHUB_TOKEN"] = user.github.access_token
        
        if user.github.selected_repo:
            env["GITHUB_REPO"] = user.github.selected_repo
        
        machine_config = MachineConfig(
            image=FALLBACK_IMAGE,  # Using Ubuntu for now
            cpus=config.cpus,
            memory_mb=config.memory_mb,
            env=env,
            region=config.region,
            auto_destroy=True,
            cmd=["sleep", "infinity"],  # Keep container running
        )
        
        try:
            async with FlyioClient(flyio_token, flyio_app) as client:
                await progress("📦 Provisioning machine...", 30)
                
                machine = await client.create_machine(machine_name, machine_config)
                if not machine:
                    logger.error("workspace.create_failed", user=user.telegram_id)
                    return None
                
                await progress("⏳ Starting machine...", 50)
                
                # Wait for machine to start
                machine = await client.wait_for_state(
                    machine.id,
                    MachineStatus.STARTED,
                    timeout_seconds=60,
                )
                
                if not machine:
                    logger.error("workspace.start_timeout", user=user.telegram_id)
                    return None
                
                await progress("🔧 Setting up workspace...", 70)
                
                # Install basic tools
                await client.exec_command(
                    machine.id,
                    ["apt-get", "update", "-qq"],
                    timeout_seconds=60,
                )
                await client.exec_command(
                    machine.id,
                    ["apt-get", "install", "-y", "-qq", "git", "curl", "wget"],
                    timeout_seconds=120,
                )
                
                # Clone repo if selected
                if user.github.selected_repo and user.github.access_token:
                    await progress("📥 Cloning repository...", 85)
                    
                    repo_url = f"https://x-access-token:{user.github.access_token}@github.com/{user.github.selected_repo}.git"
                    exit_code, stdout, stderr = await client.exec_command(
                        machine.id,
                        ["git", "clone", "--depth", "1", repo_url, "/workspace"],
                        timeout_seconds=120,
                    )
                    
                    if exit_code != 0:
                        logger.warning(
                            "workspace.clone_failed",
                            repo=user.github.selected_repo,
                            stderr=stderr[:200],
                        )
                
                await progress("✅ Workspace ready!", 100)
                
                # Create workspace record
                workspace = Workspace(
                    id=workspace_id,
                    telegram_id=user.telegram_id,
                    machine_id=machine.id,
                    repo=user.github.selected_repo,
                    status="ready",
                    private_ip=machine.private_ip,
                    expires_at=datetime.utcnow() + timedelta(minutes=config.timeout_minutes),
                )
                
                self._workspaces[user.telegram_id] = workspace
                logger.info(
                    "workspace.created",
                    workspace_id=workspace_id,
                    machine_id=machine.id,
                    user=user.telegram_id,
                )
                
                return workspace
                
        except Exception as e:
            logger.exception("workspace.create_error", error=str(e))
            return None
    
    async def run_command(
        self,
        user: User,
        command: str,
        timeout_seconds: int = 300,
        on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> CommandResult:
        """Run a command in user's workspace.
        
        Args:
            user: User whose workspace to use
            command: Shell command to execute
            timeout_seconds: Command timeout
            on_progress: Progress callback
            
        Returns:
            Command result
        """
        import time
        start_time = time.time()
        
        async def progress(msg: str, pct: int):
            if on_progress:
                await on_progress(msg, pct)
        
        workspace = self._workspaces.get(user.telegram_id)
        
        if not workspace:
            return CommandResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr="No active workspace. Use /workspace to create one.",
            )
        
        if workspace.is_expired:
            return CommandResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr="Workspace expired. Use /workspace to create a new one.",
            )
        
        await progress("🔄 Running command...", 20)
        
        flyio_token = os.environ.get("FLY_API_TOKEN")
        flyio_app = os.environ.get("FLY_APP_NAME")
        
        try:
            async with FlyioClient(flyio_token, flyio_app) as client:
                # Run command in /workspace directory (where repo was cloned)
                full_command = ["bash", "-c", f"cd /workspace 2>/dev/null || true; {command}"]
                
                await progress("⚙️ Executing...", 50)
                
                exit_code, stdout, stderr = await client.exec_command(
                    workspace.machine_id,
                    full_command,
                    timeout_seconds=timeout_seconds,
                )
                
                duration = time.time() - start_time
                
                await progress("✅ Done!", 100)
                
                return CommandResult(
                    success=exit_code == 0,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=round(duration, 2),
                )
                
        except Exception as e:
            logger.exception("workspace.command_error", error=str(e))
            return CommandResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=time.time() - start_time,
            )
    
    async def get_workspace_info(self, user: User) -> dict | None:
        """Get info about user's workspace."""
        workspace = self._workspaces.get(user.telegram_id)
        if not workspace:
            return None
        
        remaining = None
        if workspace.expires_at:
            delta = workspace.expires_at - datetime.utcnow()
            remaining = max(0, int(delta.total_seconds() / 60))
        
        return {
            "id": workspace.id,
            "repo": workspace.repo,
            "status": workspace.status,
            "expires_in_minutes": remaining,
            "is_expired": workspace.is_expired,
        }
    
    async def destroy_workspace(self, user: User) -> bool:
        """Destroy user's workspace."""
        workspace = self._workspaces.get(user.telegram_id)
        if not workspace:
            return False
        
        flyio_token = os.environ.get("FLY_API_TOKEN")
        flyio_app = os.environ.get("FLY_APP_NAME")
        
        try:
            async with FlyioClient(flyio_token, flyio_app) as client:
                destroyed = await client.destroy_machine(workspace.machine_id, force=True)
                
                if destroyed:
                    del self._workspaces[user.telegram_id]
                    logger.info(
                        "workspace.destroyed",
                        workspace_id=workspace.id,
                        user=user.telegram_id,
                    )
                
                return destroyed
                
        except Exception as e:
            logger.exception("workspace.destroy_error", error=str(e))
            return False
    
    async def extend_workspace(self, user: User, minutes: int = 30) -> bool:
        """Extend workspace timeout."""
        workspace = self._workspaces.get(user.telegram_id)
        if not workspace:
            return False
        
        workspace.expires_at = datetime.utcnow() + timedelta(minutes=minutes)
        return True


# Global manager singleton
_manager: WorkspaceManager | None = None


def get_workspace_manager() -> WorkspaceManager:
    """Get or create workspace manager."""
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager
