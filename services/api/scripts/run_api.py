from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def read_port() -> int:
    return int(os.environ.get("API_PORT", "8000"))


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=read_port(),
        log_level="info",
    )


if __name__ == "__main__":
    main()
