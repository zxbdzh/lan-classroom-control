"""Student v2 service-side building blocks.

The package is intentionally independent from the legacy ``StudentClient`` so
the service/desktop-agent architecture can be introduced without changing the
v1 runtime in-place.
"""

from .blacklist import BlacklistEnforcer, BlacklistMatcher, EnforcementResult
from .command import (
    Command,
    CommandExecution,
    CommandExecutor,
    CommandHandlerResult,
    CommandOutcome,
    OutcomePhase,
    OutcomeStatus,
)
from .ipc import (
    ChannelClosedError,
    MessageChannel,
    WindowsNamedPipeConnector,
    WindowsNamedPipeListener,
    create_memory_channel_pair,
)
from .lease import (
    LeaseRestoreResult,
    TemporaryControlKind,
    TemporaryControlLease,
)
from .power import (
    PowerAction,
    PowerController,
    PowerResult,
    WakeOnLanResult,
    WakeOnLanSender,
    build_magic_packet,
)
from .processes import (
    ProcessController,
    ProcessInfo,
    ProcessTerminationResult,
    ProcessTerminationStatus,
)
from .runtime import (
    AgentCallResult,
    SessionAgentProxy,
    SessionAgentRuntime,
    StudentServiceRuntime,
)
from .storage import ProcessBlacklistRule, StudentStateStore, default_state_database_path

__all__ = [
    "AgentCallResult",
    "BlacklistEnforcer",
    "BlacklistMatcher",
    "ChannelClosedError",
    "Command",
    "CommandExecution",
    "CommandExecutor",
    "CommandHandlerResult",
    "CommandOutcome",
    "EnforcementResult",
    "LeaseRestoreResult",
    "MessageChannel",
    "OutcomePhase",
    "OutcomeStatus",
    "PowerAction",
    "PowerController",
    "PowerResult",
    "ProcessBlacklistRule",
    "ProcessController",
    "ProcessInfo",
    "ProcessTerminationResult",
    "ProcessTerminationStatus",
    "SessionAgentProxy",
    "SessionAgentRuntime",
    "StudentServiceRuntime",
    "StudentStateStore",
    "TemporaryControlKind",
    "TemporaryControlLease",
    "WakeOnLanResult",
    "WakeOnLanSender",
    "WindowsNamedPipeConnector",
    "WindowsNamedPipeListener",
    "build_magic_packet",
    "create_memory_channel_pair",
    "default_state_database_path",
]
