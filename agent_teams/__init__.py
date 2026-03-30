from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import (
    DEFAULT_ENGINEER_MODEL_NAME,
    DEFAULT_ENGINEER_PROMPT_PROFILE,
    DEFAULT_GROUPCHAT_LOG_FILENAME,
    DEFAULT_GROUPCHAT_MEMORY_FILENAME,
    DEFAULT_SPECIALIST_MODEL_NAME,
    DEFAULT_SPECIALIST_PROMPT_PROFILE,
    DEFAULT_SPECIALIST_ROLES,
    DEFAULT_TURN_ORDER,
    AgentGroupChatConfig,
)

__all__ = [
    "AgentGroupChatRoundRunResult",
    "AgentGroupChatRoundRunner",
    "AgentGroupChatConfig",
    "GroupChatMemoryStore",
    "DEFAULT_GROUPCHAT_LOG_FILENAME",
    "DEFAULT_GROUPCHAT_MEMORY_FILENAME",
    "DEFAULT_ENGINEER_MODEL_NAME",
    "DEFAULT_ENGINEER_PROMPT_PROFILE",
    "DEFAULT_SPECIALIST_MODEL_NAME",
    "DEFAULT_SPECIALIST_PROMPT_PROFILE",
    "DEFAULT_SPECIALIST_ROLES",
    "DEFAULT_TURN_ORDER",
]


if TYPE_CHECKING:
    from .memory import GroupChatMemoryStore
    from .runner import AgentGroupChatRoundRunResult, AgentGroupChatRoundRunner


def __getattr__(name: str) -> Any:
    if name == "GroupChatMemoryStore":
        from .memory import GroupChatMemoryStore

        return GroupChatMemoryStore
    if name in {"AgentGroupChatRoundRunResult", "AgentGroupChatRoundRunner"}:
        from .runner import AgentGroupChatRoundRunResult, AgentGroupChatRoundRunner

        return {
            "AgentGroupChatRoundRunResult": AgentGroupChatRoundRunResult,
            "AgentGroupChatRoundRunner": AgentGroupChatRoundRunner,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
