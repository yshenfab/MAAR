from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GROUPCHAT_MEMORY_SECTIONS = (
    "Retained Patterns",
    "Rejected Turns",
    "Open Notes",
)
DEFAULT_MAX_ITEMS_PER_SECTION = 8


class GroupChatMemoryStore:
    """Maintain a compact per-run memory for cross-round groupchat coordination."""

    def __init__(self, path: Path, max_items_per_section: int = DEFAULT_MAX_ITEMS_PER_SECTION):
        self.path = Path(path).expanduser().resolve()
        self.max_items_per_section = int(max_items_per_section)
        if self.max_items_per_section < 1:
            raise ValueError("max_items_per_section must be >= 1")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_sections({section: [] for section in GROUPCHAT_MEMORY_SECTIONS})

    def read_text(self) -> str:
        self.initialize()
        return self.path.read_text(encoding="utf-8")

    def record_round(self, round_state: Any) -> None:
        sections = self._load_sections()

        groupchat_turns = list(getattr(round_state, "groupchat_turns", []))
        engineer_result = getattr(round_state, "groupchat_engineer_result", None)
        groupchat_result = engineer_result or getattr(round_state, "groupchat_result", None)

        for turn in groupchat_turns:
            payload = self._load_payload(turn.proposal_path)
            idea_summary = self._normalize_text(str(payload.get("idea_summary", "")).strip()) if payload else ""
            detail = idea_summary or self._normalize_text(turn.failure_reason) or "no summary"
            body = f"{turn.specialist_role} | {detail}"
            if self._status_value(turn.status) == "accepted":
                if groupchat_result and self._status_value(groupchat_result.status) == "keep":
                    self._push_entry(sections["Retained Patterns"], f"retained once | {body}")
                else:
                    self._push_entry(sections["Open Notes"], f"accepted turn without final keep | {body}")
            else:
                self._push_entry(sections["Rejected Turns"], f"{self._status_value(turn.status)} once | {body}")

        if groupchat_result is not None:
            accepted_roles = [
                turn.specialist_role
                for turn in groupchat_turns
                if self._status_value(turn.status) == "accepted"
            ]
            if self._status_value(groupchat_result.status) == "keep":
                if accepted_roles:
                    role_summary = ", ".join(accepted_roles)
                    self._push_entry(
                        sections["Open Notes"],
                        (
                            f"relay improved once | final shared candidate kept after accepted turns from {role_summary}"
                            if engineer_result is None
                            else f"relay improved once | engineer salvaged the final shared candidate after accepted turns from {role_summary}"
                        ),
                    )
            elif self._status_value(groupchat_result.status) == "discard":
                self._push_entry(sections["Open Notes"], "relay no gain once | final shared candidate did not beat the baseline")
            elif self._status_value(groupchat_result.status) == "crash":
                self._push_entry(sections["Open Notes"], "relay crashed once | final shared candidate failed during execution")

        self._write_sections(sections)

    def _load_sections(self) -> dict[str, list[str]]:
        self.initialize()
        text = self.path.read_text(encoding="utf-8")
        sections = {section: [] for section in GROUPCHAT_MEMORY_SECTIONS}
        current: str | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                heading = line[3:].strip()
                current = heading if heading in sections else None
                continue
            if current is None or not line.startswith("- "):
                continue
            sections[current].append(line)
        return sections

    def _write_sections(self, sections: dict[str, list[str]]) -> None:
        lines = [
            "# Group Chat Memory",
            "",
            "Keep this file short. Record team-level lessons only. Do not write code, diffs, or long logs.",
            "",
        ]
        for section in GROUPCHAT_MEMORY_SECTIONS:
            lines.append(f"## {section}")
            entries = sections.get(section, [])
            if entries:
                lines.extend(entries[: self.max_items_per_section])
            else:
                lines.append("- (none yet)")
            lines.append("")
        self.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _push_entry(self, items: list[str], entry_body: str) -> None:
        entry = f"- {self._normalize_text(entry_body)}"
        items[:] = [existing for existing in items if existing != entry and existing != "- (none yet)"]
        items.insert(0, entry)
        del items[self.max_items_per_section :]

    def _load_payload(self, proposal_path: Path | None) -> dict[str, object] | None:
        if proposal_path is None or not proposal_path.exists():
            return None
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.split())[:240].strip()

    def _status_value(self, status: object) -> str:
        value = getattr(status, "value", status)
        return str(value).strip().lower()
