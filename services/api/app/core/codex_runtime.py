from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


CODEX_RUNTIME_REQUIRED_ENVS = frozenset({"local", "docker"})


@dataclass(frozen=True, slots=True)
class CodexRuntime:
    executable: str
    codex_home: Path
    auth_file: Path


def codex_runtime_required(settings: Settings) -> bool:
    return settings.api_env.strip().lower() in CODEX_RUNTIME_REQUIRED_ENVS


def verify_codex_runtime(settings: Settings) -> CodexRuntime:
    executable = _resolve_executable(settings.codex_ai_executable)
    codex_home = Path(settings.codex_home).expanduser()
    if not codex_home.is_dir():
        raise RuntimeError(
            "Codex runtime preflight failed: CODEX_HOME directory is missing "
            f"at {codex_home}"
        )

    auth_file = codex_home / "auth.json"
    if not auth_file.is_file():
        raise RuntimeError(
            "Codex runtime preflight failed: auth.json is missing "
            f"from CODEX_HOME at {auth_file}"
        )

    return CodexRuntime(
        executable=executable,
        codex_home=codex_home,
        auth_file=auth_file,
    )


def _resolve_executable(executable: str) -> str:
    candidate = executable.strip()
    if not candidate:
        raise RuntimeError("Codex runtime preflight failed: executable setting is empty")

    if _looks_like_path(candidate):
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
        raise RuntimeError(f"Codex runtime preflight failed: executable not found at {path}")

    resolved = shutil.which(candidate)
    if resolved is None:
        raise RuntimeError(
            "Codex runtime preflight failed: executable "
            f"{candidate!r} was not found on PATH"
        )
    return resolved


def _looks_like_path(value: str) -> bool:
    if Path(value).is_absolute():
        return True
    separators = [separator for separator in (os.sep, os.altsep) if separator]
    return any(separator in value for separator in separators)
