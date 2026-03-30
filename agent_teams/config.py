from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SPECIALIST_ROLES = (
    "architecture",
    "optimizer_schedule",
    "efficiency_memory",
)
DEFAULT_TURN_ORDER = (
    "architecture",
    "optimizer_schedule",
    "efficiency_memory",
    "architecture",
    "optimizer_schedule",
    "efficiency_memory",
)
DEFAULT_SPECIALIST_MODEL_NAME = "glm-4.6v"
DEFAULT_SPECIALIST_PROMPT_PROFILE = "agent_groupchat_specialist"
DEFAULT_ENGINEER_MODEL_NAME = "glm-4.7"
DEFAULT_ENGINEER_PROMPT_PROFILE = "agent_groupchat_engineer"
DEFAULT_GROUPCHAT_MEMORY_FILENAME = "groupchat_memory.md"
DEFAULT_GROUPCHAT_LOG_FILENAME = "groupchat_log.jsonl"


@dataclass(slots=True)
class AgentGroupChatConfig:
    specialist_roles: tuple[str, ...] = DEFAULT_SPECIALIST_ROLES
    turn_order: tuple[str, ...] = DEFAULT_TURN_ORDER
    turns_per_round: int = len(DEFAULT_TURN_ORDER)
    specialist_model_name: str = DEFAULT_SPECIALIST_MODEL_NAME
    specialist_prompt_profile: str = DEFAULT_SPECIALIST_PROMPT_PROFILE
    engineer_model_name: str = DEFAULT_ENGINEER_MODEL_NAME
    engineer_prompt_profile: str = DEFAULT_ENGINEER_PROMPT_PROFILE
    groupchat_memory_filename: str = DEFAULT_GROUPCHAT_MEMORY_FILENAME
    groupchat_log_filename: str = DEFAULT_GROUPCHAT_LOG_FILENAME

    def __post_init__(self) -> None:
        self.specialist_roles = tuple(role.strip() for role in self.specialist_roles if role.strip())
        self.turn_order = tuple(role.strip() for role in self.turn_order if role.strip())
        self.specialist_model_name = self.specialist_model_name.strip()
        self.specialist_prompt_profile = self.specialist_prompt_profile.strip()
        self.engineer_model_name = self.engineer_model_name.strip()
        self.engineer_prompt_profile = self.engineer_prompt_profile.strip()
        self.groupchat_memory_filename = self.groupchat_memory_filename.strip()
        self.groupchat_log_filename = self.groupchat_log_filename.strip()

        if not self.specialist_roles:
            raise ValueError("specialist_roles must not be empty")
        if not self.turn_order:
            raise ValueError("turn_order must not be empty")
        if self.turns_per_round < 1:
            raise ValueError("turns_per_round must be >= 1")
        if self.turns_per_round != len(self.turn_order):
            raise ValueError("turns_per_round must match the length of turn_order")
        if not self.specialist_model_name:
            raise ValueError("specialist_model_name must not be empty")
        if not self.specialist_prompt_profile:
            raise ValueError("specialist_prompt_profile must not be empty")
        if not self.engineer_model_name:
            raise ValueError("engineer_model_name must not be empty")
        if not self.engineer_prompt_profile:
            raise ValueError("engineer_prompt_profile must not be empty")
        if not self.groupchat_memory_filename:
            raise ValueError("groupchat_memory_filename must not be empty")
        if not self.groupchat_log_filename:
            raise ValueError("groupchat_log_filename must not be empty")

        unknown_roles = [role for role in self.turn_order if role not in self.specialist_roles]
        if unknown_roles:
            raise ValueError(
                "turn_order may only reference roles declared in specialist_roles: "
                + ", ".join(unknown_roles)
            )

    @property
    def specialist_count(self) -> int:
        return len(self.specialist_roles)
