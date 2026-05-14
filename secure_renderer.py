from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ALLOWED_IMPORTS = {"math", "numpy"}
ALLOWED_FROM_IMPORTS = {"manim", "math"}
FORBIDDEN_MODULE_NAMES = {
    "builtins",
    "ctypes",
    "importlib",
    "inspect",
    "io",
    "os",
    "pathlib",
    "pickle",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
}
FORBIDDEN_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
FORBIDDEN_NODE_TYPES = (
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.AsyncWith,
    ast.Await,
    ast.Delete,
    ast.Global,
    ast.Lambda,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)
SCENE_BASES = {
    "LinearTransformationScene",
    "MovingCameraScene",
    "Scene",
    "ThreeDScene",
    "VectorScene",
    "ZoomedScene",
}
SAFE_DOCKER_CLIENT_ENV_KEYS = (
    "DOCKER_CERT_PATH",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "HOME",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "XDG_RUNTIME_DIR",
)
SANDBOX_IMAGE = os.getenv("MANIM_SANDBOX_IMAGE", "manimcommunity/manim:v0.17.3")
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("MANIM_SANDBOX_TIMEOUT", "120"))


class ValidationError(ValueError):
    """Raised when generated code violates the safety policy."""


class SandboxUnavailableError(RuntimeError):
    """Raised when no container runtime is available."""


class RenderError(RuntimeError):
    """Raised when sandboxed rendering fails."""


@dataclass
class RenderResult:
    video_path: Path
    logs: str


def sanitize_title(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip())
    slug = slug.strip("._")
    return slug or "scene"


def strip_markdown_fences(code: str) -> str:
    text = code.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def validate_generated_code(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValidationError(f"Generated Python is not valid syntax: {exc.msg}.") from exc

    _validate_top_level(tree)
    _GeneratedSceneValidator().visit(tree)

    scene_names = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and _is_scene_class(node)
    ]
    if len(scene_names) != 1:
        raise ValidationError(
            "Generated code must define exactly one Manim scene class."
        )
    return scene_names[0]


def get_sandbox_runtime() -> str:
    for candidate in ("docker", "podman"):
        runtime = shutil.which(candidate)
        if runtime:
            return runtime
    raise SandboxUnavailableError(
        "Install Docker Desktop or Podman to render generated scenes safely."
    )


def build_sandbox_command(
    runtime: str,
    work_dir: Path,
    script_name: str,
    scene_name: str,
) -> list[str]:
    command = [
        runtime,
        "run",
        "--rm",
        "--network",
        "none",
        "--memory",
        "768m",
        "--cpus",
        "1.0",
        "--pids-limit",
        "256",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={work_dir},dst=/workspace",
        "--workdir",
        "/workspace",
        "-e",
        "HOME=/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
    ]

    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

    command.extend(
        [
            SANDBOX_IMAGE,
            "manim",
            "-ql",
            script_name,
            scene_name,
            "--media_dir",
            "/workspace/media",
        ]
    )
    return command


def render_scene_in_sandbox(code: str, title: str) -> RenderResult:
    scene_name = validate_generated_code(code)
    runtime = get_sandbox_runtime()
    _assert_runtime_ready(runtime)
    script_stem = f"manim_script_{sanitize_title(title)}"

    output_dir = Path("generated_videos")
    output_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="manim-sandbox-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        script_path = temp_dir / f"{script_stem}.py"
        script_path.write_text(code, encoding="utf-8")

        command = build_sandbox_command(
            runtime=runtime,
            work_dir=temp_dir.resolve(),
            script_name=script_path.name,
            scene_name=scene_name,
        )

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=_build_docker_client_env(),
                text=True,
                timeout=SANDBOX_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(
                f"Sandboxed rendering timed out after {SANDBOX_TIMEOUT_SECONDS} seconds."
            ) from exc

        logs = _combine_logs(completed.stdout, completed.stderr)
        if completed.returncode != 0:
            raise RenderError(
                "Sandboxed Manim rendering failed.\n\n"
                f"{logs or 'No renderer logs were captured.'}"
            )

        video_path = _find_rendered_video(temp_dir / "media")
        if video_path is None:
            raise RenderError(
                "Sandbox render completed but no MP4 artifact was produced."
            )

        final_path = output_dir / f"{script_stem}.mp4"
        shutil.copy2(video_path, final_path)
        return RenderResult(video_path=final_path, logs=logs)


def _build_docker_client_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in SAFE_DOCKER_CLIENT_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _assert_runtime_ready(runtime: str) -> None:
    completed = subprocess.run(
        [runtime, "info"],
        capture_output=True,
        check=False,
        env=_build_docker_client_env(),
        text=True,
    )
    if completed.returncode == 0:
        return

    logs = _combine_logs(completed.stdout, completed.stderr)
    raise SandboxUnavailableError(
        f"{Path(runtime).name.capitalize()} is installed but not ready.\n\n"
        f"{logs or 'Start the container runtime daemon and try again.'}"
    )


def _combine_logs(stdout: str, stderr: str) -> str:
    chunks = [chunk.strip() for chunk in (stdout, stderr) if chunk and chunk.strip()]
    return "\n\n".join(chunks)


def _find_rendered_video(media_dir: Path) -> Path | None:
    mp4_files = sorted(
        path
        for path in media_dir.rglob("*.mp4")
        if "partial_movie_files" not in path.parts
    )
    if not mp4_files:
        return None
    return mp4_files[0]


def _validate_top_level(tree: ast.Module) -> None:
    allowed_nodes = (
        ast.Assign,
        ast.AnnAssign,
        ast.ClassDef,
        ast.FunctionDef,
        ast.Import,
        ast.ImportFrom,
    )

    for node in tree.body:
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            raise ValidationError(
                "Top-level expressions are not allowed in generated code."
            )

        if not isinstance(node, allowed_nodes):
            raise ValidationError(
                "Only imports, helper definitions, and scene classes are allowed at module scope."
            )

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            _validate_top_level_assignment(node)


def _validate_top_level_assignment(node: ast.Assign | ast.AnnAssign) -> None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            if _is_dunder_name(target.id) or target.id in FORBIDDEN_CALL_NAMES:
                raise ValidationError(f"Unsafe top-level assignment target: {target.id}.")
            continue

        if _is_config_attribute(target):
            continue

        raise ValidationError(
            "Top-level assignments may only target simple names or manim config values."
        )

    if node.value and any(isinstance(child, ast.Call) for child in ast.walk(node.value)):
        raise ValidationError(
            "Top-level function calls are not allowed in generated code."
        )


def _is_config_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "config"
        and not _is_dunder_name(node.attr)
    )


def _is_scene_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in SCENE_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in SCENE_BASES:
            return True
    return False


def _is_dunder_name(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _GeneratedSceneValidator(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            raise ValidationError(
                f"Generated code uses a blocked construct: {type(node).__name__}."
            )
        super().generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.decorator_list:
            raise ValidationError("Decorators are not allowed in generated classes.")
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.decorator_list:
            raise ValidationError("Decorators are not allowed in generated functions.")
        if _is_dunder_name(node.name):
            raise ValidationError("Dunder methods are not allowed in generated code.")
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORTS:
                raise ValidationError(f"Import '{alias.name}' is not allowed.")
            if alias.name == "numpy" and alias.asname not in (None, "np"):
                raise ValidationError("numpy may only be imported as 'np'.")
            if alias.name == "math" and alias.asname is not None:
                raise ValidationError("math must not be imported with an alias.")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module not in ALLOWED_FROM_IMPORTS:
            raise ValidationError(f"Import from '{node.module}' is not allowed.")
        if node.level != 0:
            raise ValidationError("Relative imports are not allowed.")
        if node.module != "manim":
            for alias in node.names:
                if alias.name == "*":
                    raise ValidationError("Wildcard imports are only allowed for manim.")
        for alias in node.names:
            if _is_dunder_name(alias.name):
                raise ValidationError("Dunder imports are not allowed.")

    def visit_Name(self, node: ast.Name) -> None:
        if _is_dunder_name(node.id):
            raise ValidationError("Dunder names are not allowed in generated code.")
        if node.id in FORBIDDEN_CALL_NAMES or node.id in FORBIDDEN_MODULE_NAMES:
            raise ValidationError(f"Use of '{node.id}' is not allowed.")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _is_dunder_name(node.attr):
            raise ValidationError("Dunder attribute access is not allowed.")
        if isinstance(node.value, ast.Name) and node.value.id in FORBIDDEN_MODULE_NAMES:
            raise ValidationError(f"Attribute access on '{node.value.id}' is not allowed.")
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _call_name(node.func)
        if func_name in FORBIDDEN_CALL_NAMES:
            raise ValidationError(f"Calling '{func_name}' is not allowed.")
        super().generic_visit(node)
