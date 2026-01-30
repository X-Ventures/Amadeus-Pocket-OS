"""Fly.io Machines API client for ephemeral workspaces."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from amadeus.logging import get_logger

logger = get_logger(__name__)

# Fly.io Machines API
FLYIO_API_BASE = "https://api.machines.dev/v1"


class MachineStatus(str, Enum):
    """Machine lifecycle states."""
    CREATED = "created"
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"


@dataclass
class MachineConfig:
    """Configuration for a Fly.io machine."""
    image: str = "ubuntu:22.04"
    cpus: int = 1
    memory_mb: int = 256
    env: dict[str, str] = field(default_factory=dict)
    cmd: list[str] | None = None
    auto_destroy: bool = True
    region: str = "iad"  # Default to US East
    
    def to_api_config(self) -> dict:
        """Convert to Fly.io API format."""
        config = {
            "image": self.image,
            "guest": {
                "cpu_kind": "shared",
                "cpus": self.cpus,
                "memory_mb": self.memory_mb,
            },
            "env": self.env,
            "auto_destroy": self.auto_destroy,
        }
        if self.cmd:
            config["init"] = {"cmd": self.cmd}
        return config


@dataclass
class Machine:
    """Represents a Fly.io machine."""
    id: str
    name: str
    state: MachineStatus
    region: str
    instance_id: str | None = None
    private_ip: str | None = None
    created_at: str | None = None
    
    @classmethod
    def from_api(cls, data: dict) -> "Machine":
        """Create from API response."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            state=MachineStatus(data.get("state", "created")),
            region=data.get("region", ""),
            instance_id=data.get("instance_id"),
            private_ip=data.get("private_ip"),
            created_at=data.get("created_at"),
        )


class FlyioClient:
    """Client for Fly.io Machines API.
    
    Machines API allows creating ephemeral VMs that:
    - Start in ~2 seconds
    - Bill per-second of use
    - Can be destroyed programmatically
    
    Perfect for per-user coding workspaces.
    """
    
    def __init__(self, api_token: str | None = None, app_name: str | None = None):
        """Initialize client.
        
        Args:
            api_token: Fly.io API token (or FLY_API_TOKEN env var)
            app_name: Fly.io app name (or FLY_APP_NAME env var)
        """
        self.api_token = api_token or os.environ.get("FLY_API_TOKEN")
        self.app_name = app_name or os.environ.get("FLY_APP_NAME")
        
        if not self.api_token:
            logger.warning("flyio.no_token", hint="Set FLY_API_TOKEN environment variable")
        
        self._client: httpx.AsyncClient | None = None
    
    async def __aenter__(self) -> "FlyioClient":
        """Enter async context."""
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
        )
        return self
    
    async def __aexit__(self, *args) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
    
    @property
    def _base_url(self) -> str:
        """Get base URL for app."""
        return f"{FLYIO_API_BASE}/apps/{self.app_name}/machines"
    
    async def create_machine(
        self,
        name: str,
        config: MachineConfig,
    ) -> Machine | None:
        """Create a new machine.
        
        Args:
            name: Machine name (unique within app)
            config: Machine configuration
            
        Returns:
            Created machine or None on failure
        """
        if not self._client or not self.app_name:
            logger.error("flyio.not_configured")
            return None
        
        try:
            response = await self._client.post(
                self._base_url,
                json={
                    "name": name,
                    "region": config.region,
                    "config": config.to_api_config(),
                },
            )
            
            if response.status_code not in (200, 201):
                logger.error(
                    "flyio.create_error",
                    status=response.status_code,
                    body=response.text[:500],
                )
                return None
            
            data = response.json()
            machine = Machine.from_api(data)
            logger.info("flyio.machine_created", machine_id=machine.id, name=name)
            return machine
            
        except Exception as e:
            logger.exception("flyio.create_exception", error=str(e))
            return None
    
    async def get_machine(self, machine_id: str) -> Machine | None:
        """Get machine by ID."""
        if not self._client or not self.app_name:
            return None
        
        try:
            response = await self._client.get(f"{self._base_url}/{machine_id}")
            
            if response.status_code != 200:
                return None
            
            return Machine.from_api(response.json())
            
        except Exception as e:
            logger.exception("flyio.get_error", error=str(e))
            return None
    
    async def wait_for_state(
        self,
        machine_id: str,
        target_state: MachineStatus,
        timeout_seconds: int = 60,
    ) -> Machine | None:
        """Wait for machine to reach target state."""
        if not self._client or not self.app_name:
            return None
        
        try:
            response = await self._client.get(
                f"{self._base_url}/{machine_id}/wait",
                params={
                    "state": target_state.value,
                    "timeout": timeout_seconds,
                },
            )
            
            if response.status_code != 200:
                logger.warning(
                    "flyio.wait_timeout",
                    machine_id=machine_id,
                    target=target_state.value,
                )
                return None
            
            return Machine.from_api(response.json())
            
        except Exception as e:
            logger.exception("flyio.wait_error", error=str(e))
            return None
    
    async def start_machine(self, machine_id: str) -> bool:
        """Start a stopped machine."""
        if not self._client or not self.app_name:
            return False
        
        try:
            response = await self._client.post(f"{self._base_url}/{machine_id}/start")
            return response.status_code == 200
        except Exception:
            return False
    
    async def stop_machine(self, machine_id: str) -> bool:
        """Stop a running machine."""
        if not self._client or not self.app_name:
            return False
        
        try:
            response = await self._client.post(f"{self._base_url}/{machine_id}/stop")
            return response.status_code == 200
        except Exception:
            return False
    
    async def destroy_machine(self, machine_id: str, force: bool = False) -> bool:
        """Destroy a machine permanently.
        
        Args:
            machine_id: Machine ID to destroy
            force: Force destroy even if running
            
        Returns:
            True if destroyed successfully
        """
        if not self._client or not self.app_name:
            return False
        
        try:
            params = {"force": "true"} if force else {}
            response = await self._client.delete(
                f"{self._base_url}/{machine_id}",
                params=params,
            )
            
            if response.status_code in (200, 204):
                logger.info("flyio.machine_destroyed", machine_id=machine_id)
                return True
            
            logger.warning(
                "flyio.destroy_failed",
                machine_id=machine_id,
                status=response.status_code,
            )
            return False
            
        except Exception as e:
            logger.exception("flyio.destroy_error", error=str(e))
            return False
    
    async def exec_command(
        self,
        machine_id: str,
        command: list[str],
        timeout_seconds: int = 300,
    ) -> tuple[int, str, str]:
        """Execute a command on a machine.
        
        Args:
            machine_id: Target machine
            command: Command to execute as list
            timeout_seconds: Command timeout
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self._client or not self.app_name:
            return (-1, "", "Fly.io not configured")
        
        try:
            response = await self._client.post(
                f"{self._base_url}/{machine_id}/exec",
                json={
                    "cmd": command,
                    "timeout": timeout_seconds,
                },
                timeout=timeout_seconds + 10,
            )
            
            if response.status_code != 200:
                return (-1, "", f"Exec failed: {response.text[:500]}")
            
            data = response.json()
            return (
                data.get("exit_code", -1),
                data.get("stdout", ""),
                data.get("stderr", ""),
            )
            
        except httpx.TimeoutException:
            return (-1, "", "Command timed out")
        except Exception as e:
            logger.exception("flyio.exec_error", error=str(e))
            return (-1, "", str(e))
    
    async def list_machines(self) -> list[Machine]:
        """List all machines in the app."""
        if not self._client or not self.app_name:
            return []
        
        try:
            response = await self._client.get(self._base_url)
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            return [Machine.from_api(m) for m in data]
            
        except Exception as e:
            logger.exception("flyio.list_error", error=str(e))
            return []
