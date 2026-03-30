from __future__ import annotations

import json
from pathlib import Path

from .state import ExperimentResult, ExperimentStatus, GroupChatTurnStatus, RoundState

PROGRAM_EXPERIENCE_SECTIONS = (
    "Positive Directions",
    "Negative Directions",
    "Open Notes",
)
SEED_PROFILE_MAAR_FIXED_PRIORS = "maar_fixed_priors"
DEFAULT_MAX_ITEMS_PER_SECTION = 8
_FLOAT_EPSILON = 1e-12
_DIVERSIFY_KEY = "diversify"
_IDEA_FAMILY_RULES = (
    ("attention-window", ("window pattern", "short window", "long window", "attention window")),
    ("learning-rate-schedule", ("warmup", "warmdown", "final lr", "learning rate", "lr ", "schedule")),
    ("batch-geometry", ("batch size", "total_batch_size", "device_batch_size", "grad accumulation", "gradient accumulation")),
    ("optimizer", ("optimizer", "adam", "adamw", "muon", "weight decay", "momentum", "beta")),
    ("model-shape", ("depth", "aspect ratio", "head dim", "head_dim", "model shape", "model size", "width")),
    ("activation-mlp", ("activation", "relu", "gelu", "swiglu", "mlp")),
    ("residual-value", ("residual", "value embedding", "ve gate", "resformer", "x0 lambda", "resid lambda")),
)
_SEED_PROFILE_ENTRIES: dict[str, dict[str, list[str]]] = {
    SEED_PROFILE_MAAR_FIXED_PRIORS: {
        "Positive Directions": [],
        "Negative Directions": [
            "[model-shape] avoid obvious depth, width, head-count, or embedding inflation on the 3090 benchmark unless the edit explicitly preserves memory budget; a recent depth increase OOMed immediately",
            "[optimizer] avoid shape-changing structural edits that may violate Muon or optimizer assumptions unless the full update path is checked end-to-end",
            "[kernel-compatibility] avoid normalization or attention-path edits that may break FlashAttention dtype or kernel compatibility unless those constraints are preserved",
        ],
        "Open Notes": [
            "[open-notes] baseline single-agent runs showed that large architecture edits are often high-cost and low-value on this benchmark; prefer changes with a clear short-budget hypothesis",
        ],
    }
}


def classify_idea_family(idea_summary: str) -> str:
    lowered = idea_summary.lower()
    for family_key, markers in _IDEA_FAMILY_RULES:
        if any(marker in lowered for marker in markers):
            return family_key
    return ""


class ProgramExperienceStore:
    """Maintain a compact per-run idea-only memory document."""

    def __init__(
        self,
        path: Path,
        max_items_per_section: int = DEFAULT_MAX_ITEMS_PER_SECTION,
        seed_profile: str = "",
    ):
        self.path = Path(path).expanduser().resolve()
        self.max_items_per_section = int(max_items_per_section)
        self.seed_profile = seed_profile.strip()
        if self.max_items_per_section < 1:
            raise ValueError("max_items_per_section must be >= 1")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_sections(self._seed_sections())

    def read_text(self) -> str:
        self.initialize()
        return self.path.read_text(encoding="utf-8")

    def record_round(self, round_state: RoundState) -> None:
        sections = self._load_sections()

        for result in round_state.worker_results:
            self._record_result(sections, round_state, result)

        if round_state.merge_result is not None:
            self._record_result(sections, round_state, round_state.merge_result)
            self._record_curator_note(sections, round_state.merge_result)

        self._write_sections(sections)

    def record_groupchat_round(self, round_state: RoundState) -> None:
        sections = self._load_sections()
        final_result = round_state.groupchat_engineer_result or round_state.groupchat_result

        for turn in round_state.groupchat_turns:
            payload = self._load_proposal_payload(turn.proposal_path)
            if payload is None:
                continue
            idea_summary = self._normalize_text(str(payload.get("idea_summary", "")).strip())
            if not idea_summary:
                continue

            if turn.status is GroupChatTurnStatus.ACCEPTED:
                if final_result is not None and final_result.status is ExperimentStatus.KEEP:
                    family_key = self._idea_family_key(idea_summary)
                    if family_key:
                        if self._has_family_entry(sections["Positive Directions"], family_key):
                            self._push_entry(
                                sections["Open Notes"],
                                "one idea family has already improved multiple times; prefer a different mechanism next unless you have a strong counter-hypothesis",
                                family_key=_DIVERSIFY_KEY,
                            )
                        self._push_entry(
                            sections["Positive Directions"],
                            self._family_entry_text(family_key, positive=True),
                            family_key=family_key,
                        )
                    else:
                        self._push_entry(sections["Positive Directions"], f"improved once | {idea_summary}")
                else:
                    self._push_entry(sections["Open Notes"], f"groupchat accepted once | {idea_summary}")
                continue

            family_key = self._idea_family_key(idea_summary)
            if turn.status is GroupChatTurnStatus.PREFLIGHT_FAILED:
                if family_key:
                    self._push_entry(
                        sections["Negative Directions"],
                        self._family_entry_text(family_key, positive=False, crashed=True),
                        family_key=family_key,
                    )
                else:
                    self._push_entry(sections["Negative Directions"], f"preflight failed once | {idea_summary}")
            elif turn.status is GroupChatTurnStatus.PATCH_FAILED:
                self._push_entry(sections["Open Notes"], f"patch failed once | {idea_summary}")
            elif turn.status is GroupChatTurnStatus.PROPOSAL_FAILED:
                self._push_entry(sections["Open Notes"], f"proposal failed once | {idea_summary}")

        if final_result is not None:
            accepted_roles = [
                turn.specialist_role for turn in round_state.groupchat_turns if turn.status is GroupChatTurnStatus.ACCEPTED
            ]
            if final_result.status is ExperimentStatus.DISCARD:
                role_summary = ", ".join(accepted_roles) if accepted_roles else "no accepted turns"
                self._push_entry(sections["Open Notes"], f"groupchat discard once | final candidate after {role_summary} did not beat the baseline")
            elif final_result.status is ExperimentStatus.CRASH:
                role_summary = ", ".join(accepted_roles) if accepted_roles else "no accepted turns"
                self._push_entry(sections["Negative Directions"], f"groupchat crashed once | final candidate after {role_summary} failed during execution")

        self._write_sections(sections)

    def _record_result(
        self,
        sections: dict[str, list[str]],
        round_state: RoundState,
        result: ExperimentResult,
    ) -> None:
        payload = self._load_proposal_payload(result.proposal_path)
        if payload is None:
            return

        idea_summary = self._normalize_text(str(payload.get("idea_summary", "")).strip())
        if not idea_summary:
            return

        if result.status is ExperimentStatus.KEEP:
            family_key = self._idea_family_key(idea_summary)
            if family_key:
                if self._has_family_entry(sections["Positive Directions"], family_key):
                    self._push_entry(
                        sections["Open Notes"],
                        "one idea family has already improved multiple times; prefer a different mechanism next unless you have a strong counter-hypothesis",
                        family_key=_DIVERSIFY_KEY,
                    )
                self._push_entry(
                    sections["Positive Directions"],
                    self._family_entry_text(family_key, positive=True),
                    family_key=family_key,
                )
            else:
                self._push_entry(sections["Positive Directions"], f"improved once | {idea_summary}")
            return

        if result.status is ExperimentStatus.DISCARD:
            if result.metrics.val_bpb is None or round_state.baseline_val_bpb is None:
                self._push_entry(sections["Open Notes"], f"discarded once | {idea_summary}")
                return
            if result.metrics.val_bpb < round_state.baseline_val_bpb - _FLOAT_EPSILON:
                family_key = self._idea_family_key(idea_summary)
                if family_key:
                    self._push_entry(
                        sections["Positive Directions"],
                        self._family_entry_text(family_key, positive=True),
                        family_key=family_key,
                    )
                else:
                    self._push_entry(sections["Positive Directions"], f"improved once | {idea_summary}")
                return
            if result.metrics.val_bpb > round_state.baseline_val_bpb + _FLOAT_EPSILON:
                family_key = self._idea_family_key(idea_summary)
                if family_key:
                    self._push_entry(
                        sections["Negative Directions"],
                        self._family_entry_text(family_key, positive=False),
                        family_key=family_key,
                    )
                else:
                    self._push_entry(sections["Negative Directions"], f"worse once | {idea_summary}")
            else:
                self._push_entry(sections["Open Notes"], f"no gain once | {idea_summary}")
            return

        if result.status is ExperimentStatus.CRASH:
            family_key = self._idea_family_key(idea_summary)
            if family_key:
                self._push_entry(
                    sections["Negative Directions"],
                    self._family_entry_text(family_key, positive=False, crashed=True),
                    family_key=family_key,
                )
            else:
                self._push_entry(sections["Negative Directions"], f"crashed once | {idea_summary}")
            return

        if result.status is ExperimentStatus.PREFLIGHT_FAILED:
            family_key = self._idea_family_key(idea_summary)
            if family_key:
                self._push_entry(
                    sections["Negative Directions"],
                    self._family_entry_text(family_key, positive=False, crashed=True),
                    family_key=family_key,
                )
            else:
                self._push_entry(sections["Negative Directions"], f"preflight failed once | {idea_summary}")
            return

        if result.status is ExperimentStatus.PROPOSAL_FAILED:
            self._push_entry(sections["Open Notes"], f"proposal failed once | {idea_summary}")

    def _record_curator_note(self, sections: dict[str, list[str]], result: ExperimentResult) -> None:
        payload = self._load_proposal_payload(result.proposal_path)
        if payload is None:
            return
        curator_note = self._normalize_text(str(payload.get("curator_note", "")).strip())
        if curator_note:
            self._push_entry(sections["Open Notes"], f"coordinator note | {curator_note}")

    def _load_sections(self) -> dict[str, list[str]]:
        self.initialize()
        text = self.path.read_text(encoding="utf-8")
        sections = {section: [] for section in PROGRAM_EXPERIENCE_SECTIONS}
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

    def _seed_sections(self) -> dict[str, list[str]]:
        sections = {section: [] for section in PROGRAM_EXPERIENCE_SECTIONS}
        seed = _SEED_PROFILE_ENTRIES.get(self.seed_profile)
        if not seed:
            return sections
        for section in PROGRAM_EXPERIENCE_SECTIONS:
            entries = seed.get(section, [])
            sections[section] = [self._format_seed_entry(item) for item in entries[: self.max_items_per_section]]
        return sections

    def _format_seed_entry(self, entry: str) -> str:
        text = self._normalize_text(entry)
        if text.startswith("[") and "]" in text:
            family, body = text[1:].split("]", 1)
            family = family.strip()
            body = body.strip()
            if family and body:
                return f"- [{family}] {body}"
        if text.startswith("- "):
            return text
        return f"- {text}"

    def _write_sections(self, sections: dict[str, list[str]]) -> None:
        lines = [
            "# Program Experience",
            "",
            "Keep this file short. Record idea-level lessons only. Do not write code, diffs, or long logs.",
            "",
        ]
        for section in PROGRAM_EXPERIENCE_SECTIONS:
            lines.append(f"## {section}")
            entries = sections.get(section, [])
            if entries:
                lines.extend(entries[: self.max_items_per_section])
            else:
                lines.append("- (none yet)")
            lines.append("")

        self.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _push_entry(self, items: list[str], entry_body: str, family_key: str = "") -> None:
        prefix = f"- [{family_key}] " if family_key else "- "
        entry = f"{prefix}{self._normalize_text(entry_body)}"
        if family_key:
            items[:] = [existing for existing in items if not existing.startswith(prefix) and existing != "- (none yet)"]
        else:
            items[:] = [existing for existing in items if existing != entry and existing != "- (none yet)"]
        items.insert(0, entry)
        del items[self.max_items_per_section :]

    def _load_proposal_payload(self, proposal_path: Path | None) -> dict[str, object] | None:
        if proposal_path is None or not proposal_path.exists():
            return None
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _normalize_text(self, text: str) -> str:
        collapsed = " ".join(text.split())
        return collapsed[:240].strip()

    def _idea_family_key(self, idea_summary: str) -> str:
        return classify_idea_family(idea_summary)

    def _has_family_entry(self, items: list[str], family_key: str) -> bool:
        prefix = f"- [{family_key}] "
        return any(item.startswith(prefix) for item in items)

    def _family_entry_text(self, family_key: str, *, positive: bool, crashed: bool = False) -> str:
        if family_key == "attention-window":
            if positive:
                return "attention window configuration has helped on this benchmark; treat that as one validated mechanism, then explore schedules, optimizer settings, batch geometry, and model shape too"
            if crashed:
                return "a recent attention-window attempt was unstable; avoid risky window rewrites without a very clear reason"
            return "a recent attention-window variant regressed; avoid undoing successful simplifications without a concrete reason"
        if family_key == "learning-rate-schedule":
            if positive:
                return "schedule changes have helped before; keep them in the search space, but continue exploring other mechanisms too"
            if crashed:
                return "a recent schedule edit was unstable; avoid brittle schedule logic without a strong reason"
            return "a recent schedule variant regressed; avoid schedule churn without a concrete hypothesis"
        if family_key == "batch-geometry":
            if positive:
                return "batch geometry can matter on this benchmark; treat it as one useful mechanism rather than the only direction"
            if crashed:
                return "a recent batch or accumulation change was unstable or too heavy; be conservative with geometry changes"
            return "a recent batch or accumulation change regressed; do not keep increasing geometry without a clear reason"
        if family_key == "optimizer":
            if positive:
                return "optimizer settings can help on this benchmark; keep them in the search space alongside model and schedule changes"
            if crashed:
                return "a recent optimizer change was unstable; avoid fragile optimizer rewrites without a clear reason"
            return "a recent optimizer tweak regressed; prefer clearer optimizer hypotheses over random retuning"
        if family_key == "model-shape":
            if positive:
                return "model shape changes can help under this budget; use that as one mechanism, not the only one"
            if crashed:
                return "a recent model-shape change was too unstable or too heavy; keep 3090 limits in mind"
            return "a recent model-shape change regressed; avoid obvious width, depth, or head-count inflation without a concrete reason"
        if family_key == "activation-mlp":
            if positive:
                return "activation or MLP design can matter on this benchmark; continue exploring beyond this family too"
            if crashed:
                return "a recent activation or MLP change was unstable; avoid speculative rewrites without a clear reason"
            return "a recent activation or MLP change regressed; avoid repeatedly swapping activations without a strong hypothesis"
        if family_key == "residual-value":
            if positive:
                return "residual or value-embedding mechanics can matter here; treat them as one viable direction among several"
            if crashed:
                return "a recent residual or value-embedding change was unstable; be careful with invasive state-path edits"
            return "a recent residual or value-embedding change regressed; avoid churning this mechanism without a concrete reason"
        return "use this as evidence, not as a reason to get stuck on the same mechanism"
