from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_URL = "http://localhost:3000"
API_HEALTH_URL = "http://localhost:8000/health"
FINAL_SPEC = "e2e/final-local-acceptance.spec.ts"


class AcceptanceFailure(RuntimeError):
    pass


def main() -> int:
    started = False
    try:
        started = True
        run(["docker", "compose", "up", "--build", "--detach"], timeout=1800)
        wait_for_json(API_HEALTH_URL, expected={"status": "ok"}, timeout_seconds=180)
        wait_for_http(WEB_URL, timeout_seconds=180)
        run_final_browser_spec()
        print("final local acceptance passed")
        return 0
    except (AcceptanceFailure, subprocess.TimeoutExpired) as exc:
        print(f"final local acceptance failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if started:
            cleanup()


def run_final_browser_spec() -> None:
    env = os.environ.copy()
    env.update(
        {
            "PLAYWRIGHT_BASE_URL": WEB_URL,
            "PLAYWRIGHT_API_BASE_URL": "http://localhost:8000",
            "PLAYWRIGHT_FINAL_LOCAL_ACCEPTANCE": "1",
        }
    )
    run(
        [
            "pnpm",
            "--filter",
            "@monopoly-ai-game/web",
            "exec",
            "playwright",
            "test",
            FINAL_SPEC,
            "--project=chrome",
        ],
        timeout=900,
        env=env,
    )


def wait_for_json(url: str, *, expected: dict[str, Any], timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if all(payload.get(key) == value for key, value in expected.items()):
                print(f"ready: {url}")
                return
            last_error = f"unexpected payload {payload!r}"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AcceptanceFailure(f"timed out waiting for {url}: {last_error}")


def wait_for_http(url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 500:
                    print(f"ready: {url}")
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AcceptanceFailure(f"timed out waiting for {url}: {last_error}")


def cleanup() -> None:
    try:
        run(["docker", "compose", "down"], timeout=300, check=False)
    except subprocess.TimeoutExpired:
        print("docker compose down timed out", file=sys.stderr)


def run(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    effective = command
    if os.name == "nt" and command and command[0] in {"pnpm", "uv"}:
        effective = ["cmd", "/c", *command]
    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        effective,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, flush=True)
    if check and completed.returncode != 0:
        raise AcceptanceFailure(f"command failed with exit {completed.returncode}: {' '.join(command)}")
    return completed


if __name__ == "__main__":
    raise SystemExit(main())
