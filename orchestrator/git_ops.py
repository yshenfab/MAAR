from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails."""


class GitRepo:
    """Small helper around git commands needed by the orchestrator."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).expanduser().resolve()

    def run(self, *args: str, cwd: Path | None = None) -> str:
        command = ["git", *args]
        proc = subprocess.run(
            command,
            cwd=str(cwd or self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            detail = stderr or stdout or f"git exited with code {proc.returncode}"
            raise GitError(f"{' '.join(command)} failed: {detail}")
        return proc.stdout.strip()

    def ensure_repo(self) -> None:
        self.run("rev-parse", "--show-toplevel")

    def require_clean(self) -> None:
        status = self.run("status", "--porcelain")
        if status:
            raise GitError("target repository has uncommitted changes")

    def current_commit(self) -> str:
        return self.run("rev-parse", "HEAD")

    def resolve_commit(self, rev: str) -> str:
        rev = rev.strip()
        if not rev:
            raise ValueError("rev must not be empty")
        return self.run("rev-parse", f"{rev}^{{commit}}")

    def branch_exists(self, branch_name: str) -> bool:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    def create_branch(self, branch_name: str, start_point: str) -> None:
        self.run("branch", "--no-track", branch_name, start_point)

    def force_branch(self, branch_name: str, start_point: str) -> None:
        self.run("branch", "-f", branch_name, start_point)

    def add_worktree(self, path: Path, branch_name: str) -> None:
        path = Path(path).expanduser().resolve()
        if path.exists() and any(path.iterdir()):
            raise GitError(f"worktree path is not empty: {path}")
        self.run("worktree", "add", str(path), branch_name)

    def reset_worktree(self, worktree_path: Path, commit: str) -> None:
        worktree_path = Path(worktree_path).expanduser().resolve()
        self.run("reset", "--hard", commit, cwd=worktree_path)
        self.run("clean", "-fd", cwd=worktree_path)

    def worktree_commit(self, worktree_path: Path) -> str:
        return self.run("rev-parse", "HEAD", cwd=Path(worktree_path).expanduser().resolve())

    def commit_paths(self, worktree_path: Path, message: str, paths: tuple[str, ...]) -> str:
        if not paths:
            raise ValueError("paths must not be empty")
        cwd = Path(worktree_path).expanduser().resolve()
        self.run("add", "--", *paths, cwd=cwd)
        self.run("commit", "-m", message, cwd=cwd)
        return self.worktree_commit(cwd)
