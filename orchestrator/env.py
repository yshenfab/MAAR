from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path

PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def load_env_file(path: Path, override: bool = False) -> dict[str, str]:
    """Load simple KEY=VALUE pairs from a dotenv-style file."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


def load_project_env(project_root: Path | None = None, override: bool = False) -> dict[str, str]:
    """Load `.env.local` first, then `.env.example` as a fallback for defaults."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    else:
        project_root = Path(project_root).expanduser().resolve()

    local_loaded = load_env_file(project_root / ".env.local", override=override)
    example_loaded = load_env_file(project_root / ".env.example", override=False)

    loaded: dict[str, str] = {}
    loaded.update(example_loaded)
    loaded.update(local_loaded)
    return loaded


def clear_proxy_env(env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Remove proxy-related environment variables from the given mapping."""
    target = os.environ if env is None else env
    for key in PROXY_ENV_VARS:
        target.pop(key, None)
    return dict(target)


def build_subprocess_env(
    base_env: Mapping[str, str] | None = None,
    updates: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child-process env that does not inherit local proxy forwarding."""
    env = dict(os.environ if base_env is None else base_env)
    clear_proxy_env(env)
    if updates:
        env.update(updates)
    return env
