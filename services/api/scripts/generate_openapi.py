from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "services" / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def build_openapi_schema() -> dict[str, Any]:
    settings = Settings(api_env="contract")
    app = create_app(settings=settings)
    return app.openapi()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, sort_keys=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{serialized}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the FastAPI OpenAPI contract.")
    parser.add_argument("output", type=Path, help="Path to write the generated OpenAPI JSON.")
    args = parser.parse_args()

    output_path = args.output
    if not output_path.is_absolute():
        output_path = ROOT / output_path

    write_json(output_path, build_openapi_schema())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
