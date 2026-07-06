from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.rag.corpus import build_static_local_corpus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic local JSONL RAG corpus without external services."
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path.")
    args = parser.parse_args()

    documents = build_static_local_corpus()
    rows = [document.to_json_dict() for document in documents]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
            handle.write("\n")

    print(f"wrote {len(rows)} local corpus documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
