from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_REPO = PROJECT_ROOT / "autoresearch"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import RunConfig, build_subprocess_env, clear_proxy_env, load_project_env, resolve_runtime


def probe_command(command: tuple[str, ...]) -> dict[str, object]:
    script = (
        "import importlib\n"
        "mods = ['torch', 'numpy', 'requests', 'rustbpe', 'tiktoken', 'kernels']\n"
        "loaded = []\n"
        "for name in mods:\n"
        "    importlib.import_module(name)\n"
        "    loaded.append(name)\n"
        "print(','.join(loaded))\n"
    )
    proc = subprocess.run(
        [*command, "-c", script],
        cwd=str(TARGET_REPO),
        env=build_subprocess_env(updates={"PYTHONDONTWRITEBYTECODE": "1"}),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": list(command),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def main() -> int:
    load_project_env(PROJECT_ROOT)
    clear_proxy_env()
    config = RunConfig(
        run_tag="runtime-probe",
        worker_count=1,
        target_repo_path=TARGET_REPO,
        artifact_root=PROJECT_ROOT / "runs",
    )
    runtime = resolve_runtime(config)
    summary = {
        "repo_path": str(runtime.repo_path),
        "source": runtime.source,
        "python_command": list(runtime.python_command),
        "train_command": list(runtime.train_command),
        "import_check_command": list(runtime.import_check_command),
        "probe": probe_command(runtime.import_check_command),
    }
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
