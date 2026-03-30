from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .env import build_subprocess_env
from .git_ops import GitRepo
from .patcher import EDITABLE_FILE

PREFLIGHT_PROFILE_STANDARD = "standard"
PREFLIGHT_PROFILE_MAAR_STRICT = "maar_strict"
PREFLIGHT_PROFILE_BASELINE_LEGACY = "baseline_legacy"

_NN_MODULE_INHERITED_MEMBERS = {
    "training",
    "train",
    "eval",
    "parameters",
    "named_parameters",
    "state_dict",
    "load_state_dict",
    "to",
    "cuda",
    "cpu",
    "half",
    "bfloat16",
    "float",
    "double",
    "modules",
    "named_modules",
    "children",
    "named_children",
    "register_buffer",
    "register_parameter",
    "apply",
}


class PreflightError(RuntimeError):
    """Raised when a candidate fails preflight validation."""


@dataclass(slots=True)
class PreflightReport:
    workspace_path: Path
    changed_paths: list[str]
    imported_modules: list[str]


class PreflightChecker:
    """Validate the patched workspace before training."""

    _RISKY_ADDED_LINE_PATTERNS: tuple[tuple[str, str], ...] = (
        ("torch.save(", "torch.save checkpointing"),
        ("torch.load(", "torch.load checkpoint restore"),
        ("open(", "open(...) file I/O"),
        ("exit(", "process exit"),
        ("sys.exit(", "process exit"),
    )

    def __init__(
        self,
        editable_file: str = EDITABLE_FILE,
        import_check_command: tuple[str, ...] = ("python3",),
        check_imports: bool = True,
        profile: str = PREFLIGHT_PROFILE_STANDARD,
    ):
        self.editable_file = editable_file
        self.import_check_command = tuple(import_check_command)
        self.check_imports = check_imports
        self.profile = profile.strip() or PREFLIGHT_PROFILE_STANDARD
        if self.profile not in {
            PREFLIGHT_PROFILE_STANDARD,
            PREFLIGHT_PROFILE_MAAR_STRICT,
            PREFLIGHT_PROFILE_BASELINE_LEGACY,
        }:
            raise ValueError(f"unsupported preflight profile: {self.profile}")
        if self.check_imports and not self.import_check_command:
            raise ValueError("import_check_command must not be empty when check_imports is enabled")

    def run(self, workspace_path: Path) -> PreflightReport:
        workspace_path = Path(workspace_path).expanduser().resolve()
        target_path = workspace_path / self.editable_file
        if not target_path.exists():
            raise PreflightError(f"editable file does not exist: {target_path}")

        changed_paths = self._changed_paths(workspace_path)
        self._ensure_only_editable_file_changed(changed_paths)
        self._check_diff_hazards(workspace_path)
        self._check_syntax(target_path)
        if self.profile == PREFLIGHT_PROFILE_MAAR_STRICT:
            self._check_maar_structure_guards(target_path)
        imported_modules = self._extract_import_modules(target_path)
        self._check_imports(workspace_path, imported_modules)
        return PreflightReport(
            workspace_path=workspace_path,
            changed_paths=changed_paths,
            imported_modules=imported_modules,
        )

    def _changed_paths(self, workspace_path: Path) -> list[str]:
        repo = GitRepo(workspace_path)
        output = repo.run("status", "--porcelain", cwd=workspace_path)
        if not output:
            return []

        paths: list[str] = []
        for line in output.splitlines():
            if len(line) >= 4 and line[2] == " ":
                path = line[3:]
            else:
                path = line[2:].lstrip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path)
        return paths

    def _ensure_only_editable_file_changed(self, changed_paths: list[str]) -> None:
        if not changed_paths:
            raise PreflightError("expected a modified editable file, but the worktree is clean")
        unexpected = sorted({path for path in changed_paths if path != self.editable_file})
        if unexpected:
            joined = ", ".join(unexpected)
            raise PreflightError(f"unexpected modified paths outside {self.editable_file}: {joined}")
        if self.editable_file not in changed_paths:
            raise PreflightError(f"expected {self.editable_file} to be modified")

    def _check_syntax(self, target_path: Path) -> None:
        source = target_path.read_text(encoding="utf-8")
        try:
            compile(source, str(target_path), "exec")
        except SyntaxError as exc:
            raise PreflightError(f"syntax error in {target_path.name}: {exc.msg}") from exc

    def _check_diff_hazards(self, workspace_path: Path) -> None:
        repo = GitRepo(workspace_path)
        diff_text = repo.run("diff", "--unified=0", "--", self.editable_file, cwd=workspace_path)
        if not diff_text:
            return

        matched: list[str] = []
        for line in diff_text.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, label in self._RISKY_ADDED_LINE_PATTERNS:
                if pattern in line:
                    matched.append(label)

        if matched:
            unique = ", ".join(sorted(set(matched)))
            raise PreflightError(f"risky runtime edits are not allowed in {self.editable_file}: {unique}")

    def _extract_import_modules(self, target_path: Path) -> list[str]:
        source = target_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(target_path))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in modules:
                        modules.append(root)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root not in modules:
                    modules.append(root)
        return modules

    def _check_maar_structure_guards(self, target_path: Path) -> None:
        source = target_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(target_path))
        class_members = self._collect_class_members(tree)
        self._check_external_component_access(class_members, tree)

    def _collect_class_members(self, tree: ast.AST) -> dict[str, set[str]]:
        members: dict[str, set[str]] = {}
        for node in tree.body if isinstance(tree, ast.Module) else []:
            if not isinstance(node, ast.ClassDef):
                continue
            class_members: set[str] = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            for child in ast.walk(node):
                for target in self._iter_assignment_targets(child):
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        class_members.add(target.attr)
            members[node.name] = class_members
        return members

    def _iter_assignment_targets(self, node: ast.AST) -> tuple[ast.expr, ...]:
        if isinstance(node, ast.Assign):
            return tuple(node.targets)
        if isinstance(node, ast.AnnAssign):
            return (node.target,)
        if isinstance(node, ast.AugAssign):
            return (node.target,)
        return ()

    def _check_external_component_access(self, class_members: dict[str, set[str]], tree: ast.AST) -> None:
        component_map = {
            "mlp": "MLP",
            "attn": "CausalSelfAttention",
        }
        problems: list[str] = []
        for child in ast.walk(tree):
            if not isinstance(child, ast.Attribute):
                continue
            parent = child.value
            if not isinstance(parent, ast.Attribute):
                continue
            component_name = parent.attr
            class_name = component_map.get(component_name)
            if class_name is None:
                continue
            allowed = set(class_members.get(class_name, set())) | _NN_MODULE_INHERITED_MEMBERS
            if child.attr not in allowed and not child.attr.startswith("__"):
                problems.append(
                    f"external access to .{component_name}.{child.attr} is invalid because class {class_name} does not define {child.attr}"
                )
        if problems:
            raise PreflightError("; ".join(sorted(set(problems))))

    def _check_imports(self, workspace_path: Path, modules: list[str]) -> None:
        if not self.check_imports:
            return
        if not modules:
            return

        env = build_subprocess_env(updates={"PYTHONDONTWRITEBYTECODE": "1"})
        script = (
            "import importlib, sys\n"
            "for name in sys.argv[1:]:\n"
            "    importlib.import_module(name)\n"
        )
        proc = subprocess.run(
            [*self.import_check_command, "-c", script, *modules],
            cwd=str(workspace_path),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "unknown import error"
            raise PreflightError(f"import check failed: {detail}")
