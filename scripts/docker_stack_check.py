from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_package_json(relative_path: str) -> dict[str, object]:
    return json.loads(read_text(relative_path))


def parse_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in read_text(".env.example").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_required_files() -> None:
    for relative_path in [
        "docker-compose.yml",
        ".env.example",
        "services/api/Dockerfile",
        "apps/web/Dockerfile",
    ]:
        require((ROOT / relative_path).exists(), f"missing Docker stack file: {relative_path}")


def check_no_future_stage_artifacts() -> None:
    future_stage_artifacts = {
        "apps/web/app/game": "Phase 5 frontend game surface",
        "apps/web/app/games": "Phase 5 frontend game surface",
        "apps/web/components/board": "Phase 5 frontend board surface",
        "apps/web/components/game": "Phase 5 frontend game surface",
        "apps/web/lib/game": "Phase 5 frontend game client",
        "services/api/app/contracts": "Phase 6 contracts",
        "services/api/app/deals": "Phase 6 deal primitives",
        "services/api/app/negotiations": "Phase 6 negotiations",
        "apps/web/components/contracts": "Phase 6 frontend contract surface",
        "apps/web/components/negotiations": "Phase 6 frontend negotiation surface",
        "services/api/app/ai": "Phase 7 AI runtime",
        "apps/web/components/ai": "Phase 7 frontend AI surface",
        "services/api/app/memory": "Phase 8 AI memory",
        "apps/web/components/ai-audit": "Phase 8 frontend AI audit surface",
        "services/api/app/rag": "Phase 9 RAG",
        "services/api/app/retrieval": "Phase 9 retrieval",
        "services/api/app/mcp": "Phase 9 MCP",
    }
    for relative_path, planned_stage in future_stage_artifacts.items():
        require(
            not (ROOT / relative_path).exists(),
            f"Docker stack gate found {planned_stage} artifact before its planned phase: {relative_path}",
        )


def check_compose_contract() -> None:
    compose = read_text("docker-compose.yml")
    for marker in [
        "pgvector/pgvector:pg17",
        "postgres:",
        "api:",
        "web:",
        "healthcheck:",
        "monopoly-postgres-data",
        "DATABASE_URL",
        "INTERNAL_API_BASE_URL",
        "NEXT_PUBLIC_API_BASE_URL",
    ]:
        require(marker in compose, f"docker-compose.yml missing marker: {marker}")


def check_env_contract() -> None:
    env_values = parse_env_example()
    for key in [
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL",
        "NEXT_PUBLIC_API_BASE_URL",
        "INTERNAL_API_BASE_URL",
    ]:
        require(key in env_values, f".env.example missing {key}")
    require(
        "postgres:5432" in env_values["DATABASE_URL"],
        "DATABASE_URL must target the compose postgres service",
    )
    require(
        env_values["INTERNAL_API_BASE_URL"] == "http://api:8000",
        "INTERNAL_API_BASE_URL must use the compose API service URL",
    )
    require(
        env_values["NEXT_PUBLIC_API_BASE_URL"] == "http://localhost:8000",
        "NEXT_PUBLIC_API_BASE_URL must use the browser-facing local API URL",
    )


def check_dockerfiles() -> None:
    api_dockerfile = read_text("services/api/Dockerfile")
    require(
        "python:3.14.6-slim" in api_dockerfile,
        "services/api/Dockerfile must use python:3.14.6-slim",
    )
    require("uv sync" in api_dockerfile, "services/api/Dockerfile must install with uv")
    require("app.main:app" in api_dockerfile, "services/api/Dockerfile must start the FastAPI app")

    web_dockerfile = read_text("apps/web/Dockerfile")
    for marker in [
        "node:24",
        "pnpm",
        "@monopoly-ai-game/web",
        "next build",
    ]:
        require(marker in web_dockerfile, f"apps/web/Dockerfile missing marker: {marker}")


def check_package_scripts() -> None:
    scripts = read_package_json("package.json").get("scripts", {})
    require(isinstance(scripts, dict), "Root package.json must contain scripts")
    require(
        scripts.get("test:stack") == "uv run --no-sync python scripts/docker_stack_check.py",
        "Root package.json must expose test:stack",
    )


def check_compose_config_if_available() -> None:
    if not (ROOT / "docker-compose.yml").exists():
        return
    completed = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "config", "--format", "json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )
    require(completed.returncode == 0, f"docker compose config failed:\n{completed.stdout}")
    config = json.loads(completed.stdout)
    services = config.get("services", {})
    for service in ["postgres", "api", "web"]:
        require(service in services, f"compose config missing service: {service}")
    volumes = config.get("volumes", {})
    require(
        "monopoly-postgres-data" in volumes,
        "compose config missing named Postgres volume",
    )


def run_gate(gate: str) -> None:
    check_required_files()
    check_no_future_stage_artifacts()
    check_compose_contract()
    check_env_contract()
    check_dockerfiles()
    check_package_scripts()
    if gate == "config":
        check_compose_config_if_available()
    print(f"docker stack {gate}: ok")


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) > 1 else "check"
    allowed_gates = {"check", "config"}
    if gate not in allowed_gates:
        print(f"Unknown Docker stack gate: {gate}", file=sys.stderr)
        return 2
    run_gate(gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
