"""Stable public API for Amadeus plugins."""

from __future__ import annotations

from amadeus.backends import EngineBackend, EngineConfig, SetupIssue
from amadeus.commands import (
    CommandBackend,
    CommandContext,
    CommandExecutor,
    CommandResult,
    RunMode,
    RunRequest,
    RunResult,
    get_command,
    list_command_ids,
)
from amadeus.config import ConfigError, HOME_CONFIG_PATH, read_config, write_config
from amadeus.context import RunContext
from amadeus.directives import DirectiveError
from amadeus.events import EventFactory
from amadeus.model import (
    Action,
    ActionEvent,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
)
from amadeus.presenter import Presenter
from amadeus.progress import ActionState, ProgressState, ProgressTracker
from amadeus.router import RunnerUnavailableError
from amadeus.runner import BaseRunner, JsonlSubprocessRunner, Runner
from amadeus.runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)
from amadeus.transport import MessageRef, RenderedMessage, SendOptions, Transport
from amadeus.transport_runtime import ResolvedMessage, ResolvedRunner, TransportRuntime
from amadeus.transports import SetupResult, TransportBackend

from amadeus.ids import RESERVED_COMMAND_IDS
from amadeus.logging import bind_run_context, clear_context, get_logger, suppress_logs
from amadeus.utils.paths import reset_run_base_dir, set_run_base_dir
from amadeus.scheduler import ThreadJob, ThreadScheduler
from amadeus.engines import list_backends
from amadeus.settings import load_settings
from amadeus.backends_helpers import install_issue

AMADEUS_PLUGIN_API_VERSION = 1

__all__ = [
    # Core types
    "Action",
    "ActionEvent",
    "BaseRunner",
    "CompletedEvent",
    "ConfigError",
    "CommandBackend",
    "CommandContext",
    "CommandExecutor",
    "CommandResult",
    "EngineBackend",
    "EngineConfig",
    "EngineId",
    "ExecBridgeConfig",
    "EventFactory",
    "IncomingMessage",
    "JsonlSubprocessRunner",
    "MessageRef",
    "DirectiveError",
    "Presenter",
    "ProgressState",
    "ProgressTracker",
    "ActionState",
    "RenderedMessage",
    "ResumeToken",
    "RunMode",
    "RunRequest",
    "RunResult",
    "ResolvedMessage",
    "ResolvedRunner",
    "RunContext",
    "Runner",
    "RunnerUnavailableError",
    "RunningTask",
    "RunningTasks",
    "SendOptions",
    "SetupIssue",
    "SetupResult",
    "StartedEvent",
    "AMADEUS_PLUGIN_API_VERSION",
    "Transport",
    "TransportBackend",
    "TransportRuntime",
    "handle_message",
    "HOME_CONFIG_PATH",
    "RESERVED_COMMAND_IDS",
    "read_config",
    "write_config",
    "get_logger",
    "bind_run_context",
    "clear_context",
    "suppress_logs",
    "set_run_base_dir",
    "reset_run_base_dir",
    "ThreadJob",
    "ThreadScheduler",
    "get_command",
    "list_command_ids",
    "list_backends",
    "load_settings",
    "install_issue",
]
