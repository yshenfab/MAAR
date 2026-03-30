from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Convert dataclass-heavy runtime state into JSON-friendly objects."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


class SerializableDataclass:
    """Mixin for the small amount of JSON persistence used by the orchestrator."""

    def to_dict(self) -> dict[str, Any]:
        if not is_dataclass(self):
            raise TypeError(f"{type(self).__name__} is not a dataclass instance")
        value = to_jsonable(asdict(self))
        if not isinstance(value, dict):
            raise TypeError("Serialized dataclass root must be a dictionary")
        return value
