"""Stage 10.6 product smoke checks.

Coverage labels required by the phase verifier and operator output:
docker stack smoke, api health smoke, database migration smoke, game creation smoke,
several-turn scripted smoke, fake AI, RUN_LIVE_CODEX_AI, smoke failure, tier.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
LIVE_CODEX_ENV_VAR = "RUN_LIVE_CODEX_AI"
SMOKE_TIERS = (
    "docker stack",
    "database migration",
    "API health",
    "game creation",
    "scripted turn",
    "fake AI",
)

POSTGRES_USER = "monopoly"
POSTGRES_PASSWORD = "monopoly"
POSTGRES_DB = "monopoly_ai_game"

ACTION_PRIORITY = (
    "BUY_PROPERTY",
    "SETTLE_DEBT",
    "PAY_JAIL_FINE",
    "USE_GET_OUT_OF_JAIL_CARD",
    "ROLL_DICE",
    "START_AUCTION",
    "BID_AUCTION",
    "PASS_AUCTION",
    "MORTGAGE_PROPERTY",
    "BUY_HOUSE",
    "SELL_HOUSE",
    "UNMORTGAGE_PROPERTY",
    "DECLARE_BANKRUPTCY",
)


class SmokeFailure(RuntimeError):
    def __init__(self, tier: str, message: str) -> None:
        self.tier = tier
        super().__init__(f"smoke failure [tier={tier}]: {message}")


class SmokeContext:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.postgres_port = free_port()
        self.api_port = free_port()
        self.web_port = free_port()
        self.compose_project = f"monopoly_ai_game_smoke_{os.getpid()}"
        self.api_process: subprocess.Popen[str] | None = None
        self.api_log_path = temp_dir / "api.log"
        self.fake_codex_executable = create_fake_codex_executable(temp_dir)
        self.ai_runtime_dir = API_ROOT / "app" / "ai" / "runtime"
        self.existing_ai_runtime_files = set(self.ai_runtime_dir.glob("codex-ai-*.last-message.json"))

    @property
    def api_base_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{self.postgres_port}/{POSTGRES_DB}"
        )

    def compose_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "POSTGRES_USER": POSTGRES_USER,
                "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
                "POSTGRES_DB": POSTGRES_DB,
                "POSTGRES_PORT": str(self.postgres_port),
                "API_PORT": str(self.api_port),
                "WEB_PORT": str(self.web_port),
            }
        )
        return env

    def api_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "API_ENV": "smoke",
                "API_HOST": "127.0.0.1",
                "API_PORT": str(self.api_port),
                "CORS_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
                "DATABASE_URL": self.database_url,
                "CODEX_AI_EXECUTABLE": str(self.fake_codex_executable),
                "LOG_LEVEL": "WARNING",
            }
        )
        return env


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="monopoly-product-smoke-") as temp_name:
        context = SmokeContext(Path(temp_name))
        try:
            run_tier("docker stack", "docker stack smoke", lambda: docker_stack_smoke(context))
            run_tier(
                "database migration",
                "database migration smoke",
                lambda: database_migration_smoke(context),
            )
            start_api(context)
            run_tier("API health", "api health smoke", lambda: api_health_smoke(context))
            run_tier("game creation", "game creation smoke", lambda: game_creation_smoke(context))
            run_tier(
                "scripted turn",
                "several-turn scripted smoke",
                lambda: several_turn_scripted_smoke(context),
            )
            run_tier("fake AI", "fake AI smoke", lambda: fake_ai_smoke(context))
        except SmokeFailure as exc:
            print(str(exc), file=sys.stderr)
            return 1
        finally:
            stop_api(context)
            cleanup_ai_runtime_artifacts(context)
            cleanup_compose(context)

    print("product smoke: ok")
    return 0


def run_tier(tier: str, label: str, callback: object) -> None:
    if not callable(callback):
        raise TypeError("callback must be callable")
    try:
        callback()
    except SmokeFailure:
        raise
    except Exception as exc:
        raise SmokeFailure(tier, f"{type(exc).__name__}: {exc}") from exc
    print(f"{label}: ok")


def docker_stack_smoke(context: SmokeContext) -> None:
    require_executable("docker", "docker stack")
    config = run_command(
        "docker stack",
        [
            "docker",
            "compose",
            "--project-name",
            context.compose_project,
            "--env-file",
            ".env.example",
            "config",
            "--services",
        ],
        env=context.compose_environment(),
        timeout_seconds=120,
    )
    services = set(config.stdout.split())
    missing_services = {"postgres", "api", "web"} - services
    if missing_services:
        raise SmokeFailure(
            "docker stack",
            f"compose config missing services: {', '.join(sorted(missing_services))}",
        )

    run_command(
        "docker stack",
        [
            "docker",
            "compose",
            "--project-name",
            context.compose_project,
            "--env-file",
            ".env.example",
            "up",
            "-d",
            "postgres",
        ],
        env=context.compose_environment(),
        timeout_seconds=300,
    )
    wait_for_postgres_health(context)


def database_migration_smoke(context: SmokeContext) -> None:
    run_command(
        "database migration",
        [
            "uv",
            "run",
            "--no-sync",
            "--python",
            "3.14.6",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": context.database_url},
        timeout_seconds=180,
    )


def start_api(context: SmokeContext) -> None:
    api_log = context.api_log_path.open("w", encoding="utf-8")
    try:
        context.api_process = subprocess.Popen(
            [*api_python_command(), "scripts/run_api.py"],
            cwd=API_ROOT,
            env=context.api_environment(),
            stdout=api_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        api_log.close()


def api_health_smoke(context: SmokeContext) -> None:
    deadline = time.monotonic() + 90
    last_error = "API did not respond"
    while time.monotonic() < deadline:
        if context.api_process is not None and context.api_process.poll() is not None:
            raise SmokeFailure(
                "API health",
                f"API process exited with {context.api_process.returncode}\n{tail(context.api_log_path)}",
            )
        try:
            body = http_json(context, "GET", "/health", tier="API health")
            if body.get("status") == "ok" and body.get("service") == "api":
                return
            last_error = f"unexpected health payload: {body}"
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SmokeFailure("API health", f"{last_error}\n{tail(context.api_log_path)}")


def game_creation_smoke(context: SmokeContext) -> None:
    game = create_game(
        context,
        seed="stage-10-6-game-creation-smoke",
        players=(
            {"name": "Ada", "kind": "human"},
            {"name": "Grace", "kind": "ai"},
        ),
        tier="game creation",
    )
    if game.get("status") != "active":
        raise SmokeFailure("game creation", f"expected active game, got {game.get('status')!r}")
    players = game.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise SmokeFailure("game creation", "created game did not return exactly two players")
    if {player.get("controller_type") for player in players if isinstance(player, Mapping)} != {
        "human",
        "ai",
    }:
        raise SmokeFailure("game creation", "created game did not preserve human/AI controllers")

    state = http_json(context, "GET", f"/games/{game['id']}/state", tier="game creation")
    if state.get("event_sequence") != 0 or not state.get("state_hash"):
        raise SmokeFailure("game creation", f"unexpected initial state payload: {state}")


def several_turn_scripted_smoke(context: SmokeContext) -> None:
    game = create_game(
        context,
        seed="stage-10-6-several-turn-scripted-smoke",
        players=(
            {"name": "Ada", "kind": "human"},
            {"name": "Grace", "kind": "human"},
        ),
        tier="scripted turn",
    )
    game_id = str(game["id"])
    accepted_action_count = 0
    first_sequence = current_event_sequence(context, game_id, tier="scripted turn")

    for index in range(4):
        state = http_json(context, "GET", f"/games/{game_id}/state", tier="scripted turn")
        state_body = state.get("state")
        if not isinstance(state_body, Mapping):
            raise SmokeFailure("scripted turn", "state response did not include a state object")
        turn = state_body.get("turn")
        if not isinstance(turn, Mapping):
            raise SmokeFailure("scripted turn", "state response did not include turn data")
        actor_id = str(turn.get("current_player_id"))
        action = choose_scripted_action(
            legal_actions(context, game_id, actor_id, tier="scripted turn"),
            tier="scripted turn",
        )
        response = http_json(
            context,
            "POST",
            f"/games/{game_id}/actions",
            tier="scripted turn",
            payload=action,
            headers={"Idempotency-Key": f"stage-10-6-scripted-{index}"},
        )
        if response.get("status") != "accepted":
            raise SmokeFailure("scripted turn", f"scripted action was not accepted: {response}")
        accepted_action_count += 1

    final_sequence = current_event_sequence(context, game_id, tier="scripted turn")
    if accepted_action_count < 4 or final_sequence <= first_sequence:
        raise SmokeFailure(
            "scripted turn",
            f"game did not advance enough: actions={accepted_action_count}, "
            f"sequence {first_sequence}->{final_sequence}",
        )
    events = http_json(context, "GET", f"/games/{game_id}/events", tier="scripted turn")
    if not events.get("events"):
        raise SmokeFailure("scripted turn", "accepted scripted actions did not produce events")


def fake_ai_smoke(context: SmokeContext) -> None:
    game = create_game(
        context,
        seed="stage-10-6-default-fake-ai-smoke",
        players=(
            {"name": "Grace", "kind": "ai"},
            {"name": "Ada", "kind": "human"},
        ),
        tier="fake AI",
    )
    game_id = str(game["id"])
    ai_player = next(
        (
            player
            for player in game["players"]
            if isinstance(player, Mapping) and player.get("controller_type") == "ai"
        ),
        None,
    )
    if not isinstance(ai_player, Mapping):
        raise SmokeFailure("fake AI", "created game did not include an AI player")

    response = http_json(
        context,
        "POST",
        f"/games/{game_id}/ai/step",
        tier="fake AI",
        payload={
            "player_id": ai_player["id"],
            "decision_type": "action_decision",
            "mandatory": True,
            "request_context": {
                "mode": "default fake-AI smoke behavior for routine local runs",
                "smoke": "stage-10-6",
            },
        },
    )
    if response.get("status") != "accepted":
        raise SmokeFailure("fake AI", f"fake AI step was not accepted: {response}")
    if not response.get("accepted_events"):
        raise SmokeFailure("fake AI", "fake AI step did not commit accepted events")

    decisions = http_json(context, "GET", f"/games/{game_id}/ai/decisions", tier="fake AI")
    decision_rows = decisions.get("decisions")
    if not isinstance(decision_rows, list) or not decision_rows:
        raise SmokeFailure("fake AI", "fake AI step did not persist an AI decision record")
    latest = decision_rows[0]
    if not isinstance(latest, Mapping) or latest.get("status") != "accepted":
        raise SmokeFailure("fake AI", f"unexpected fake AI audit status: {latest}")


def create_game(
    context: SmokeContext,
    *,
    seed: str,
    players: Sequence[Mapping[str, str]],
    tier: str,
) -> Mapping[str, Any]:
    return http_json(
        context,
        "POST",
        "/games",
        tier=tier,
        payload={"seed": seed, "players": list(players)},
        expected_statuses=(201,),
    )


def current_event_sequence(context: SmokeContext, game_id: str, *, tier: str) -> int:
    state = http_json(context, "GET", f"/games/{game_id}/state", tier=tier)
    sequence = state.get("event_sequence")
    if not isinstance(sequence, int):
        raise SmokeFailure(tier, f"state response has invalid event_sequence: {state}")
    return sequence


def legal_actions(context: SmokeContext, game_id: str, actor_id: str, *, tier: str) -> list[Mapping[str, Any]]:
    body = http_json(
        context,
        "GET",
        f"/games/{game_id}/legal-actions?actor_player_id={actor_id}",
        tier=tier,
    )
    actions = body.get("legal_actions")
    if not isinstance(actions, list):
        raise SmokeFailure(tier, f"legal action response is malformed: {body}")
    return [action for action in actions if isinstance(action, Mapping)]


def choose_scripted_action(actions: Sequence[Mapping[str, Any]], *, tier: str) -> Mapping[str, Any]:
    if not actions:
        raise SmokeFailure(tier, "no legal actions were returned")
    for action_type in ACTION_PRIORITY:
        for action in actions:
            if action.get("type") == action_type:
                if action_type == "DECLARE_BANKRUPTCY" and len(actions) > 1:
                    continue
                return action
    raise SmokeFailure(tier, f"no usable legal action found in {actions}")


def http_json(
    context: SmokeContext,
    method: str,
    path: str,
    *,
    tier: str,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    expected_statuses: Sequence[int] = (200,),
) -> Mapping[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{context.api_base_url}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status_code = int(response.status)
            body_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(
            tier,
            f"{method} {path} returned HTTP {exc.code}; expected {tuple(expected_statuses)}; "
            f"body={body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(tier, f"{method} {path} failed: {exc}") from exc

    if status_code not in expected_statuses:
        raise SmokeFailure(
            tier,
            f"{method} {path} returned HTTP {status_code}; expected {tuple(expected_statuses)}; "
            f"body={body_text}",
        )
    try:
        body = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(tier, f"{method} {path} returned non-JSON body: {body_text}") from exc
    if not isinstance(body, Mapping):
        raise SmokeFailure(tier, f"{method} {path} returned non-object JSON: {body!r}")
    return body


def wait_for_postgres_health(context: SmokeContext) -> None:
    deadline = time.monotonic() + 90
    command = [
        "docker",
        "compose",
        "--project-name",
        context.compose_project,
        "--env-file",
        ".env.example",
        "exec",
        "-T",
        "postgres",
        "pg_isready",
        "-U",
        POSTGRES_USER,
        "-d",
        POSTGRES_DB,
    ]
    last_output = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=context.compose_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        last_output = completed.stdout
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise SmokeFailure("docker stack", f"postgres did not become healthy:\n{last_output}")


def cleanup_compose(context: SmokeContext) -> None:
    if shutil.which("docker") is None:
        return
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            context.compose_project,
            "--env-file",
            ".env.example",
            "down",
            "-v",
            "--remove-orphans",
        ],
        cwd=REPO_ROOT,
        env=context.compose_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=180,
    )


def cleanup_ai_runtime_artifacts(context: SmokeContext) -> None:
    if not context.ai_runtime_dir.exists():
        return
    for path in context.ai_runtime_dir.glob("codex-ai-*.last-message.json"):
        if path not in context.existing_ai_runtime_files:
            path.unlink(missing_ok=True)
    try:
        context.ai_runtime_dir.rmdir()
    except OSError:
        pass


def stop_api(context: SmokeContext) -> None:
    process = context.api_process
    if process is None:
        return
    if os.name == "nt" and process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        process.wait(timeout=20)
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)


def run_command(
    tier: str,
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=None if env is None else dict(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(tier, f"command timed out: {' '.join(command)}") from exc
    if completed.returncode != 0:
        raise SmokeFailure(
            tier,
            f"command failed with exit {completed.returncode}: {' '.join(command)}\n"
            f"{completed.stdout}",
        )
    return completed


def require_executable(name: str, tier: str) -> None:
    if shutil.which(name) is None:
        raise SmokeFailure(tier, f"required executable not found on PATH: {name}")


def api_python_command() -> list[str]:
    if os.name == "nt":
        candidate = API_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = API_ROOT / ".venv" / "bin" / "python"
    if candidate.is_file():
        return [str(candidate)]
    return ["uv", "run", "--no-sync", "--python", "3.14.6", "python"]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def create_fake_codex_executable(temp_dir: Path) -> Path:
    script_path = temp_dir / "fake_codex.py"
    script_path.write_text(FAKE_CODEX_SOURCE, encoding="utf-8", newline="\n")
    if os.name == "nt":
        executable = temp_dir / "fake_codex.cmd"
        executable.write_text(
            f'@echo off\n"{sys.executable}" "%~dp0fake_codex.py" %*\n',
            encoding="utf-8",
            newline="\r\n",
        )
        return executable

    executable = temp_dir / "fake_codex"
    executable.write_text(
        f"#!{sys.executable}\n" + FAKE_CODEX_SOURCE,
        encoding="utf-8",
        newline="\n",
    )
    executable.chmod(0o755)
    return executable


def tail(path: Path, line_count: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:])


FAKE_CODEX_SOURCE = r'''
from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


ACTION_PRIORITY = (
    "BUY_PROPERTY",
    "SETTLE_DEBT",
    "PAY_JAIL_FINE",
    "USE_GET_OUT_OF_JAIL_CARD",
    "ROLL_DICE",
    "START_AUCTION",
    "BID_AUCTION",
    "PASS_AUCTION",
    "MORTGAGE_PROPERTY",
    "BUY_HOUSE",
    "SELL_HOUSE",
    "UNMORTGAGE_PROPERTY",
    "DECLARE_BANKRUPTCY",
)


def main() -> int:
    prompt = sys.stdin.read()
    context = extract_prompt_context(prompt)
    legal_actions = context.get("legal_actions")
    if not isinstance(legal_actions, Sequence) or isinstance(legal_actions, (str, bytes, bytearray)):
        legal_actions = []
    legal_action = choose_action([action for action in legal_actions if isinstance(action, Mapping)])
    output = {
        "decision_type": "action_decision",
        "game_id": str(context["game_id"]),
        "player_id": str(context["player_id"]),
        "expected_state_hash": str(legal_action["expected_state_hash"]),
        "expected_event_sequence": int(legal_action["expected_event_sequence"]),
        "action": {
            "type": str(legal_action["type"]),
            "payload": dict(legal_action.get("payload") or {}),
        },
        "self_dialogue": {
            "status": "empty",
            "reason": "Default fake AI smoke behavior for routine local runs.",
        },
        "memory_updates": [],
        "confidence": 0.5,
        "rationale": "Default fake AI smoke selected a backend-provided legal action.",
    }
    output_text = json.dumps(output, sort_keys=True)
    write_last_message(output_text)
    print(json.dumps({"type": "session_configured", "model": "fake-codex-smoke"}))
    print(json.dumps({
        "type": "item_completed",
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": output_text}],
        },
    }))
    return 0


def extract_prompt_context(prompt: str) -> dict[str, object]:
    marker = "Caller-provided prompt context follows. Do not infer hidden context."
    start = prompt.find(marker)
    if start >= 0:
        start = prompt.find("{", start)
    else:
        start = prompt.find("{")
    if start < 0:
        raise SystemExit("fake Codex smoke could not find prompt context JSON")
    context, _ = json.JSONDecoder().raw_decode(prompt[start:])
    if not isinstance(context, dict):
        raise SystemExit("fake Codex smoke prompt context was not a JSON object")
    return context


def choose_action(actions: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    for action_type in ACTION_PRIORITY:
        for action in actions:
            if action.get("type") == action_type:
                if action_type == "DECLARE_BANKRUPTCY" and len(actions) > 1:
                    continue
                return action
    raise SystemExit("fake Codex smoke received no usable legal action")


def write_last_message(output_text: str) -> None:
    args = sys.argv[1:]
    for index, value in enumerate(args):
        if value == "--output-last-message" and index + 1 < len(args):
            path = Path(args[index + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output_text, encoding="utf-8")
            return


if __name__ == "__main__":
    raise SystemExit(main())
'''


if __name__ == "__main__":
    raise SystemExit(main())
