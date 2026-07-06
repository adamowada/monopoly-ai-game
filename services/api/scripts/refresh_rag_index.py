from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.db.session import create_database_engine, create_session_factory  # noqa: E402
from app.rag.retrieval import refresh_rag_index_entries  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the local database-backed RAG index."
    )
    parser.add_argument(
        "--game-id",
        type=UUID,
        default=None,
        help="Optional game UUID for game-scoped memory, negotiation, and decision documents.",
    )
    return parser.parse_args(argv)


async def refresh_index(game_id: UUID | None) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            async with session.begin():
                return await refresh_rag_index_entries(session, game_id=game_id)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    refreshed_count = asyncio.run(refresh_index(args.game_id))
    scope = f"game {args.game_id}" if args.game_id is not None else "global static corpus"
    print(f"refreshed {refreshed_count} database RAG index entries for {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
