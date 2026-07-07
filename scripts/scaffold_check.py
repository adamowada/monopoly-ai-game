from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PNPM = "pnpm.cmd" if os.name == "nt" else "pnpm"

ROOT_SCRIPT_COMMANDS = {
    "dev": "pnpm --recursive --parallel --filter @monopoly-ai-game/web --filter @monopoly-ai-game/api run dev",
    "test:scaffold": "uv run --no-sync python scripts/scaffold_check.py check",
    "test:web": "pnpm --filter @monopoly-ai-game/web run test",
    "test:api": "pnpm --filter @monopoly-ai-game/api run test",
}

WEB_PACKAGE_SCRIPTS = {
    "dev": "node scripts/dev.mjs",
    "build": "next build",
    "start": "node scripts/start.mjs",
    "test": "pnpm run test:unit && pnpm run test:e2e",
    "test:unit": "vitest run",
    "test:e2e": "playwright test --project=chrome",
    "test:e2e:chrome": "playwright test --project=chrome",
    "lint": "tsc --noEmit",
    "typecheck": "tsc --noEmit",
}

API_PACKAGE_SCRIPTS = {
    "dev": "uv run --directory ../.. --project services/api --python 3.14.6 python services/api/scripts/run_api.py",
    "start": "uv run --directory ../.. --project services/api --python 3.14.6 python services/api/scripts/run_api.py",
    "test": "uv run --directory ../.. --project services/api --python 3.14.6 pytest services/api/tests",
    "lint": "uv run --directory ../.. --project services/api --python 3.14.6 ruff check services/api",
    "typecheck": "uv run --directory ../.. --project services/api --python 3.14.6 basedpyright --project services/api",
}

REQUIRED_PATHS = {
    "apps/web/package.json",
    "apps/web/app/layout.tsx",
    "apps/web/app/page.tsx",
    "apps/web/app/globals.css",
    "apps/web/next.config.ts",
    "apps/web/tsconfig.json",
    "apps/web/scripts/dev.mjs",
    "apps/web/scripts/start.mjs",
    "apps/web/scripts/scaffold-check.mjs",
    "services/api/package.json",
    "services/api/pyproject.toml",
    "services/api/app/main.py",
    "services/api/scripts/run_api.py",
    "services/api/tests/test_scaffold.py",
    "packages/schemas/README.md",
    "packages/schemas/package.json",
    "content/rules/README.md",
    "assets/vector/README.md",
}

DOCUMENTATION_MARKERS = {
    "packages/schemas/README.md": [
        "generated",
        "OpenAPI",
        "shared",
    ],
    "content/rules/README.md": [
        "rule",
        "card",
        "property",
        "source data",
    ],
    "assets/vector/README.md": [
        "original",
        "SVG",
        "local",
    ],
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_package_json(relative_path: str) -> dict[str, object]:
    return json.loads(read_text(relative_path))


def check_required_paths() -> None:
    missing = sorted(path for path in REQUIRED_PATHS if not (ROOT / path).exists())
    require(not missing, f"Missing scaffold paths: {', '.join(missing)}")


def check_root_scripts() -> None:
    package = read_package_json("package.json")
    scripts = package.get("scripts", {})
    require(isinstance(scripts, dict), "Root package.json must contain scripts")
    for name, expected in sorted(ROOT_SCRIPT_COMMANDS.items()):
        require(scripts.get(name) == expected, f"Root script {name} must be {expected!r}")


def check_workspace_packages() -> None:
    web_package = read_package_json("apps/web/package.json")
    api_package = read_package_json("services/api/package.json")
    schemas_package = read_package_json("packages/schemas/package.json")

    require(web_package.get("name") == "@monopoly-ai-game/web", "Web package must use the @monopoly-ai-game namespace")
    require(api_package.get("name") == "@monopoly-ai-game/api", "API package must use the @monopoly-ai-game namespace")
    require(schemas_package.get("name") == "@monopoly-ai-game/schemas", "Schemas package must use the @monopoly-ai-game namespace")

    for name, expected in sorted(WEB_PACKAGE_SCRIPTS.items()):
        scripts = web_package.get("scripts", {})
        require(isinstance(scripts, dict), "Web package must contain scripts")
        require(scripts.get(name) == expected, f"Web script {name} must be {expected!r}")

    for name, expected in sorted(API_PACKAGE_SCRIPTS.items()):
        scripts = api_package.get("scripts", {})
        require(isinstance(scripts, dict), "API package must contain scripts")
        require(scripts.get(name) == expected, f"API script {name} must be {expected!r}")


def check_documentation() -> None:
    for relative_path, markers in sorted(DOCUMENTATION_MARKERS.items()):
        body = read_text(relative_path).lower()
        missing = [marker for marker in markers if marker.lower() not in body]
        require(not missing, f"{relative_path} missing documentation markers: {', '.join(missing)}")

    readme = read_text("README.md").lower()
    for marker in [
        "phase 1 stage 1.1",
        "pnpm --filter @monopoly-ai-game/web run dev",
        "pnpm --filter @monopoly-ai-game/api run dev",
        "pnpm run test:scaffold",
        "pnpm run test:web",
        "pnpm run test:api",
    ]:
        require(marker in readme, f"README.md missing scaffold command marker: {marker}")


def check_web_surface() -> None:
    page = read_text("apps/web/app/page.tsx")
    dashboard = read_text("apps/web/app/dashboard-shell.tsx")
    for marker in [
        "Monopoly 2.0 Game Table",
        "readBackendHealth",
    ]:
        require(marker in page, f"apps/web/app/page.tsx missing {marker!r}")
    for marker in [
        "Rules referee",
        "Game table",
        "AI notebook",
        "referee-checked moves",
    ]:
        require(marker in dashboard, f"apps/web/app/dashboard-shell.tsx missing {marker!r}")


def check_api_surface() -> None:
    app = read_text("services/api/app/main.py")
    for marker in [
        "FastAPI",
        "create_app",
        "/health",
        "HealthResponse",
    ]:
        require(marker in app, f"services/api/app/main.py missing {marker!r}")


def request_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:
        require(response.status == 200, f"{url} returned HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def wait_for_response(url: str, validator: Callable[[str], None], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            validator(request_text(url))
            return
        except (AssertionError, OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {url}: {last_error}")


def stop_process_tree(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def run_server_check(
    *,
    name: str,
    command: list[str],
    url: str,
    validator: Callable[[str], None],
    env_updates: dict[str, str],
    timeout_seconds: float = 90,
) -> None:
    env = os.environ.copy()
    env.update(env_updates)
    env.setdefault("NEXT_TELEMETRY_DISABLED", "1")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        try:
            wait_for_response(url, validator, timeout_seconds)
        except Exception as error:
            if process.poll() is not None:
                log.seek(0)
                output = log.read().strip()
                raise AssertionError(f"{name} exited before responding. Output:\n{output}") from error
            raise
        finally:
            stop_process_tree(process)


def check_startup_smoke() -> None:
    run_server_check(
        name="api scaffold server",
        command=[PNPM, "--filter", "@monopoly-ai-game/api", "run", "dev"],
        url="http://127.0.0.1:18000/health",
        env_updates={"API_HOST": "127.0.0.1", "API_PORT": "18000"},
        validator=lambda body: require('"status":"ok"' in body.replace(" ", ""), "API health response must report ok"),
    )
    run_server_check(
        name="web scaffold server",
        command=[PNPM, "--filter", "@monopoly-ai-game/web", "run", "dev"],
        url="http://127.0.0.1:13000",
        env_updates={"HOSTNAME": "127.0.0.1", "PORT": "13000"},
        validator=lambda body: require("Local Game Research Console" in body, "Web page must render the scaffold console"),
    )


def run_gate(gate: str) -> None:
    check_required_paths()
    check_root_scripts()
    check_workspace_packages()
    check_documentation()
    check_web_surface()
    check_api_surface()
    if gate == "smoke":
        check_startup_smoke()
    print(f"phase1 scaffold {gate}: ok")


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) > 1 else "check"
    allowed_gates = {
        "check",
        "unit",
        "integration",
        "e2e",
        "smoke",
        "lint",
        "typecheck",
    }
    if gate not in allowed_gates:
        print(f"Unknown scaffold gate: {gate}", file=sys.stderr)
        return 2
    run_gate(gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
