from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .git_ops import GitRepo
from .state import ExperimentProposal

EDITABLE_FILE = "train.py"


class MatchMode(str, Enum):
    EXACT = "exact"
    WHITESPACE_TOLERANT = "whitespace_tolerant"


class PatchApplyError(RuntimeError):
    """Raised when a proposal cannot be safely applied."""


@dataclass(slots=True)
class PatchResult:
    workspace_path: Path
    target_path: Path
    match_mode: MatchMode
    diff_text: str


class SearchReplacePatcher:
    """Apply Search/Replace proposals to the single editable file."""

    def __init__(self, editable_file: str = EDITABLE_FILE):
        self.editable_file = editable_file

    def apply(self, workspace_path: Path, proposal: ExperimentProposal) -> PatchResult:
        workspace_path = Path(workspace_path).expanduser().resolve()
        target_path = workspace_path / self.editable_file
        if not target_path.exists():
            raise PatchApplyError(f"editable file does not exist: {target_path}")

        original_text = target_path.read_text(encoding="utf-8")
        updated_text, match_mode = self._apply_to_text(original_text, proposal)
        target_path.write_text(updated_text, encoding="utf-8")
        diff_text = self.generate_diff(workspace_path)
        return PatchResult(
            workspace_path=workspace_path,
            target_path=target_path,
            match_mode=match_mode,
            diff_text=diff_text,
        )

    def export_diff(self, workspace_path: Path, output_path: Path) -> str:
        diff_text = self.generate_diff(workspace_path)
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(diff_text, encoding="utf-8")
        return diff_text

    def generate_diff(self, workspace_path: Path) -> str:
        repo = GitRepo(Path(workspace_path))
        return repo.run("diff", "--", self.editable_file, cwd=Path(workspace_path))

    def _apply_to_text(self, original_text: str, proposal: ExperimentProposal) -> tuple[str, MatchMode]:
        search_block = proposal.search_block
        replace_block = proposal.replace_block

        if not search_block.strip():
            raise PatchApplyError("search_block must not be empty")

        exact_matches = self._find_exact_matches(original_text, search_block)
        if len(exact_matches) == 1:
            idx = exact_matches[0]
            updated = original_text[:idx] + replace_block + original_text[idx + len(search_block):]
            return updated, MatchMode.EXACT
        if len(exact_matches) > 1:
            raise PatchApplyError("search_block matched multiple times exactly")

        return self._apply_whitespace_tolerant(original_text, proposal)

    def _find_exact_matches(self, text: str, pattern: str) -> list[int]:
        matches: list[int] = []
        start = 0
        while True:
            idx = text.find(pattern, start)
            if idx == -1:
                break
            matches.append(idx)
            start = idx + 1
        return matches

    def _apply_whitespace_tolerant(
        self,
        original_text: str,
        proposal: ExperimentProposal,
    ) -> tuple[str, MatchMode]:
        file_lines = original_text.splitlines()
        search_lines = proposal.search_block.splitlines()
        replace_lines = proposal.replace_block.splitlines()

        file_tokens = [(idx, self._normalize(line)) for idx, line in enumerate(file_lines)]
        file_tokens = [(idx, token) for idx, token in file_tokens if token]
        search_tokens = [self._normalize(line) for line in search_lines]
        search_tokens = [token for token in search_tokens if token]

        if not search_tokens:
            raise PatchApplyError("search_block is empty after whitespace normalization")
        if len(file_tokens) < len(search_tokens):
            raise PatchApplyError("search_block did not match the editable file")

        matches: list[tuple[int, int]] = []
        for start in range(len(file_tokens) - len(search_tokens) + 1):
            candidate = [token for _, token in file_tokens[start:start + len(search_tokens)]]
            if candidate == search_tokens:
                first_line = file_tokens[start][0]
                last_line = file_tokens[start + len(search_tokens) - 1][0]
                matches.append((first_line, last_line))

        if not matches:
            raise PatchApplyError("search_block did not match the editable file")
        if len(matches) > 1:
            raise PatchApplyError("search_block matched multiple times after whitespace normalization")

        first_line, last_line = matches[0]
        updated_lines = file_lines[:first_line] + replace_lines + file_lines[last_line + 1:]
        updated_text = "\n".join(updated_lines)
        if original_text.endswith("\n"):
            updated_text += "\n"
        return updated_text, MatchMode.WHITESPACE_TOLERANT

    def _normalize(self, line: str) -> str:
        return "".join(line.split())
