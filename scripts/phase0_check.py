from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SCRIPT_COMMANDS = {
    "dev": "uv run --no-sync python scripts/phase0_check.py dev",
    "test": "pnpm run test:unit && pnpm run test:integration && pnpm run test:e2e && pnpm run test:smoke",
    "test:unit": "uv run --no-sync python scripts/phase0_check.py unit",
    "test:integration": "uv run --no-sync python scripts/phase0_check.py integration",
    "test:e2e": "uv run --no-sync python scripts/phase0_check.py e2e",
    "test:smoke": "uv run --no-sync python scripts/phase0_check.py smoke",
    "lint": "uv run --no-sync python scripts/phase0_check.py lint",
    "format": "uv run --no-sync python scripts/phase0_check.py format",
    "typecheck": "uv run --no-sync python scripts/phase0_check.py typecheck",
    "review": "pnpm run lint && pnpm run typecheck && pnpm run test",
}

REQUIRED_MAKEFILE_COMMANDS = {
    "dev": "pnpm run dev",
    "test": "pnpm run test",
    "test-unit": "pnpm run test:unit",
    "test-integration": "pnpm run test:integration",
    "test-e2e": "pnpm run test:e2e",
    "test-smoke": "pnpm run test:smoke",
    "lint": "pnpm run lint",
    "format": "pnpm run format",
    "typecheck": "pnpm run typecheck",
    "review": "pnpm run review",
    "python-install": "uv python install 3.14.6",
    "python-sync": "uv sync --python 3.14.6",
}

REQUIRED_FILES = {
    "AGENTS.md",
    "PLANS.md",
    "README.md",
    ".gitignore",
    ".python-version",
    "package.json",
    "pyproject.toml",
    "pnpm-workspace.yaml",
    "toolchain/python-downloads.json",
    "uv.toml",
    "uv.lock",
    "Makefile",
}

GITIGNORE_RULES = {
    "node_modules/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".hypothesis/",
    "playwright-report/",
    "test-results/",
    ".env",
    ".env.*",
    "postgres-data/",
    "docker-data/",
    "*.sqlite3",
    ".codex-supervisor/",
}

README_MARKERS = {
    "local-only",
    "Next.js",
    "FastAPI",
    "Postgres",
    "pnpm",
    "uv",
    "docker compose up --build",
    "feature/phase-0-project-control",
    "codex-supervisor",
    "uv python install 3.14.6",
    "uv sync --python 3.14.6",
    "codex exec --json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def check_required_files() -> None:
    missing = sorted(path for path in REQUIRED_FILES if not (ROOT / path).exists())
    require(not missing, f"Missing required files: {', '.join(missing)}")


def check_python_version() -> None:
    require(read_text(".python-version") == "3.14.6", ".python-version must contain exactly 3.14.6")


def check_package_scripts() -> None:
    package = json.loads(read_text("package.json"))
    scripts = package.get("scripts", {})
    missing = sorted(set(REQUIRED_SCRIPT_COMMANDS) - set(scripts))
    require(not missing, f"package.json missing scripts: {', '.join(missing)}")
    mismatched = [
        f"{name} must be {expected!r}"
        for name, expected in sorted(REQUIRED_SCRIPT_COMMANDS.items())
        if scripts.get(name) != expected
    ]
    require(not mismatched, "package.json script mismatches: " + "; ".join(mismatched))
    require(package.get("packageManager") == "pnpm@11.7.0", "packageManager must be pnpm@11.7.0")


def check_pnpm_workspace() -> None:
    workspace = read_text("pnpm-workspace.yaml")
    for workspace_glob in ('"apps/*"', '"services/*"', '"packages/*"'):
        require(workspace_glob in workspace, f"pnpm-workspace.yaml missing {workspace_glob}")


def check_pyproject() -> None:
    pyproject = tomllib.loads(read_text("pyproject.toml"))
    project = pyproject.get("project", {})
    require(project.get("requires-python") == "==3.14.6", "pyproject.toml must require Python ==3.14.6")
    tool_uv = pyproject.get("tool", {}).get("uv", {})
    require(tool_uv.get("package") is False, "pyproject.toml must set tool.uv.package = false")


def check_uv_config() -> None:
    uv_config = tomllib.loads(read_text("uv.toml"))
    require(uv_config.get("required-version") == "==0.11.7", "uv.toml must require uv ==0.11.7")
    require(
        uv_config.get("python-downloads-json-url") == "toolchain/python-downloads.json",
        "uv.toml must point uv at the project Python downloads manifest",
    )
    manifest = json.loads(read_text("toolchain/python-downloads.json"))
    download = manifest.get("cpython-3.14.6-windows-x86_64-none", {})
    require(download.get("major") == 3, "Python downloads manifest must describe Python major version 3")
    require(download.get("minor") == 14, "Python downloads manifest must describe Python minor version 14")
    require(download.get("patch") == 6, "Python downloads manifest must describe Python patch version 6")
    require("x86_64-pc-windows-msvc-install_only_stripped.tar.gz" in download.get("url", ""), "Python downloads manifest must point to the Windows x64 install-only archive")


def check_gitignore() -> None:
    rules = set()
    for line in read_text(".gitignore").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            rules.add(stripped)
    missing = sorted(GITIGNORE_RULES - rules)
    require(not missing, f".gitignore missing rules: {', '.join(missing)}")


def check_readme() -> None:
    readme = read_text("README.md")
    readme_lower = readme.lower()
    missing = sorted(marker for marker in README_MARKERS if marker.lower() not in readme_lower)
    require(not missing, f"README.md missing required content markers: {', '.join(missing)}")


def check_makefile() -> None:
    makefile = read_text("Makefile")
    makefile_with_guard = f"\n{makefile}"
    for target, command in sorted(REQUIRED_MAKEFILE_COMMANDS.items()):
        expected_block = f"\n{target}:\n\t{command}"
        require(expected_block in makefile_with_guard, f"Makefile target {target} must delegate to {command}")


def run_gate(gate: str) -> None:
    check_required_files()
    check_python_version()
    check_package_scripts()
    check_pnpm_workspace()
    check_pyproject()
    check_uv_config()
    check_gitignore()
    check_readme()
    check_makefile()
    print(f"phase0 {gate}: ok")


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) > 1 else "check"
    allowed_gates = {
        "check",
        "dev",
        "unit",
        "integration",
        "e2e",
        "smoke",
        "lint",
        "format",
        "typecheck",
    }
    if gate not in allowed_gates:
        print(f"Unknown phase0 gate: {gate}", file=sys.stderr)
        return 2
    run_gate(gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
