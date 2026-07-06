from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import tracemalloc
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.orchestrator import CodexExecTimeoutError, CodexSubprocessRunner
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    game_events,
    game_snapshots,
    games,
    metadata,
    players,
    rejected_actions,
    retrieval_records,
)
from app.db.persistence import (
    DEFAULT_SNAPSHOT_INTERVAL,
    AcceptedEventTemplate,
    EventPersistence,
)
from app.rules.simulation import run_random_legal_action_stress
from app.rules.state import PlayerSetup, create_initial_game_state


STAGE_11_4_DATABASE_NAME = "monopoly_ai_game_stage11_4"
STAGE_11_4_TEST_DATABASE_URL = os.getenv(
    "STAGE_11_4_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/"
    f"{STAGE_11_4_DATABASE_NAME}",
)
STAGE_11_4_ADMIN_DATABASE_URL = STAGE_11_4_TEST_DATABASE_URL.replace(
    f"/{STAGE_11_4_DATABASE_NAME}",
    "/postgres",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    await _ensure_stage_11_4_database()
    engine = create_async_engine(STAGE_11_4_TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def test_stage_11_4_query_index_review_covers_long_game_read_paths() -> None:
    required_indexes = {
        game_events.name: {
            "ix_game_events_game_sequence",
            "ix_game_events_game_created_at",
            "ix_game_events_actor_player_id",
        },
        game_snapshots.name: {
            "ix_game_snapshots_game_sequence",
            "ix_game_snapshots_last_event_id",
        },
        rejected_actions.name: {
            "ix_rejected_actions_game_actor_created_at",
            "ix_rejected_actions_game_phase",
        },
        ai_decisions.name: {
            "ix_ai_decisions_game_player_created_at",
            "ix_ai_decisions_prompt_context_hash",
        },
        ai_memory_entries.name: {
            "ix_ai_memory_entries_game_player_created_at",
            "ix_ai_memory_entries_source_decision_id",
        },
        retrieval_records.name: {
            "ix_retrieval_records_game_player_created_at",
            "ix_retrieval_records_decision_id",
        },
    }

    for table_name, expected_names in required_indexes.items():
        table = metadata.tables[table_name]
        actual_names = {index.name for index in table.indexes}
        assert expected_names <= actual_names


def test_stage_11_4_long_game_simulation_memory_growth_stays_bounded() -> None:
    speed_started_at = time.perf_counter()
    speed_result = run_random_legal_action_stress(
        seed="stage-11.4-long-game-simulation-speed",
        player_count=5,
        action_limit=750,
    )
    speed_elapsed_seconds = time.perf_counter() - speed_started_at

    tracemalloc.start()
    try:
        memory_result = run_random_legal_action_stress(
            seed="stage-11.4-long-game-simulation-memory",
            player_count=5,
            action_limit=250,
        )
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert speed_result.failure is None
    assert speed_result.actions_executed == 750
    assert speed_result.invariant_checks == speed_result.actions_executed + 1
    assert speed_elapsed_seconds < 30
    assert memory_result.failure is None
    assert memory_result.actions_executed == 250
    assert memory_result.invariant_checks == memory_result.actions_executed + 1
    assert current_bytes < 16 * 1024 * 1024
    assert peak_bytes < 48 * 1024 * 1024
    assert peak_bytes / memory_result.actions_executed < 96 * 1024


@pytest.mark.asyncio
async def test_stage_11_4_snapshot_interval_keeps_replay_tail_bounded(
    session_factory: async_sessionmaker,
) -> None:
    assert DEFAULT_SNAPSHOT_INTERVAL == 25
    game_id, player_ids = await _create_persisted_game(session_factory)
    service = EventPersistence(session_factory)

    templates = tuple(
        AcceptedEventTemplate(
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(player_ids[0]), "amount": 1 if index % 2 == 0 else -1},
        )
        for index in range(127)
    )
    append_result = await service.append_accepted_events(
        game_id=game_id,
        actor_player_id=player_ids[0],
        event_templates=templates,
        expected_base_sequence=0,
    )

    snapshot_rows = await _fetch_snapshots(session_factory, game_id)
    latest_snapshot_sequence = int(snapshot_rows[-1]["event_sequence"])
    tail_event_count = await _count_events_after(
        session_factory,
        game_id=game_id,
        sequence=latest_snapshot_sequence,
    )
    snapshot_check = await service.verify_game_snapshots(game_id)
    from_latest = await service.replay_from_latest_snapshot(game_id)

    assert append_result.state.event_sequence == 127
    assert [int(row["event_sequence"]) for row in snapshot_rows] == [25, 50, 75, 100, 125]
    assert tail_event_count == 2
    assert tail_event_count < DEFAULT_SNAPSHOT_INTERVAL
    assert snapshot_check.event_count == 127
    assert snapshot_check.snapshot_count == 5
    assert snapshot_check.replayed_state_hash == snapshot_check.latest_snapshot_state_hash
    assert from_latest.state_hash() == append_result.state.state_hash()


def test_stage_11_4_codex_subprocess_timeout_cleanup_terminates_child_tree(
    tmp_path: Path,
) -> None:
    # timeout cleanup: the CodexSubprocessRunner must not leave orphaned AI subprocesses.
    child_script = tmp_path / "child_worker.py"
    parent_script = tmp_path / "parent_worker.py"
    heartbeat_file = tmp_path / "child_heartbeat.txt"
    child_pid_file = tmp_path / "child.pid"

    child_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import os",
                "import sys",
                "import time",
                "from pathlib import Path",
                "heartbeat = Path(sys.argv[1])",
                "pid_file = Path(sys.argv[2])",
                "pid_file.write_text(str(os.getpid()), encoding='utf-8')",
                "for _ in range(1200):",
                "    heartbeat.write_text(str(time.time()), encoding='utf-8')",
                "    time.sleep(0.05)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import subprocess",
                "import sys",
                "import time",
                "from pathlib import Path",
                "child_script = Path(sys.argv[1])",
                "heartbeat = Path(sys.argv[2])",
                "pid_file = Path(sys.argv[3])",
                "child = subprocess.Popen([sys.executable, str(child_script), str(heartbeat), str(pid_file)])",
                "deadline = time.time() + 5",
                "while not pid_file.exists() and time.time() < deadline:",
                "    time.sleep(0.01)",
                "while True:",
                "    time.sleep(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CodexSubprocessRunner()
    child_pid: int | None = None
    try:
        with pytest.raises(CodexExecTimeoutError):
            runner.run(
                [sys.executable, str(parent_script), str(child_script), str(heartbeat_file), str(child_pid_file)],
                stdin="",
                timeout_seconds=1,
                output_last_message_path=None,
            )

        child_pid = _read_pid(child_pid_file)
        assert child_pid is not None
        assert _wait_for_pid_exit(child_pid, timeout_seconds=5)
    finally:
        if child_pid is None:
            child_pid = _read_pid(child_pid_file)
        if child_pid is not None and _pid_is_running(child_pid):
            _kill_pid_tree(child_pid)


def test_stage_11_4_codex_subprocess_failure_cleanup_terminates_child_tree(
    tmp_path: Path,
) -> None:
    # failed subprocess cleanup: a failed parent with returncode=7 must not leave a child.
    child_script = tmp_path / "failed_parent_child_worker.py"
    parent_script = tmp_path / "failed_parent_worker.py"
    heartbeat_file = tmp_path / "failed_parent_child_heartbeat.txt"
    child_pid_file = tmp_path / "failed_parent_child.pid"

    child_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import os",
                "import sys",
                "import time",
                "from pathlib import Path",
                "heartbeat = Path(sys.argv[1])",
                "pid_file = Path(sys.argv[2])",
                "pid_file.write_text(str(os.getpid()), encoding='utf-8')",
                "for _ in range(1200):",
                "    heartbeat.write_text(str(time.time()), encoding='utf-8')",
                "    time.sleep(0.05)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import subprocess",
                "import sys",
                "import time",
                "from pathlib import Path",
                "child_script = Path(sys.argv[1])",
                "heartbeat = Path(sys.argv[2])",
                "pid_file = Path(sys.argv[3])",
                "child = subprocess.Popen(",
                "    [sys.executable, str(child_script), str(heartbeat), str(pid_file)],",
                "    stdin=subprocess.DEVNULL,",
                "    stdout=subprocess.DEVNULL,",
                "    stderr=subprocess.DEVNULL,",
                ")",
                "deadline = time.time() + 5",
                "while not pid_file.exists() and time.time() < deadline:",
                "    time.sleep(0.01)",
                "print(child.pid, flush=True)",
                "sys.exit(7)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CodexSubprocessRunner()
    child_pid: int | None = None
    try:
        result = runner.run(
            [
                sys.executable,
                str(parent_script),
                str(child_script),
                str(heartbeat_file),
                str(child_pid_file),
            ],
            stdin="",
            timeout_seconds=5,
            output_last_message_path=None,
        )

        child_pid = _read_pid(child_pid_file)
        if child_pid is None and result.stdout.strip():
            child_pid = int(result.stdout.strip())
        assert result.returncode == 7
        assert child_pid is not None
        assert _wait_for_pid_exit(child_pid, timeout_seconds=5)
    finally:
        if child_pid is None:
            child_pid = _read_pid(child_pid_file)
        if child_pid is not None and _pid_is_running(child_pid):
            _kill_pid_tree(child_pid)


def test_stage_11_4_codex_subprocess_inherited_pipe_failure_preserves_parent_returncode_and_cleans_child_tree(
    tmp_path: Path,
) -> None:
    # inherited pipe regression: a failed parent must preserve returncode=7 even when
    # a child keeps inherited stdout/stderr pipes open.
    child_script = tmp_path / "inherited_pipe_child_worker.py"
    parent_script = tmp_path / "inherited_pipe_parent_worker.py"
    heartbeat_file = tmp_path / "inherited_pipe_child_heartbeat.txt"
    child_pid_file = tmp_path / "inherited_pipe_child.pid"

    child_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import os",
                "import sys",
                "import time",
                "from pathlib import Path",
                "heartbeat = Path(sys.argv[1])",
                "pid_file = Path(sys.argv[2])",
                "pid_file.write_text(str(os.getpid()), encoding='utf-8')",
                "for _ in range(1200):",
                "    heartbeat.write_text(str(time.time()), encoding='utf-8')",
                "    time.sleep(0.05)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import subprocess",
                "import sys",
                "import time",
                "from pathlib import Path",
                "child_script = Path(sys.argv[1])",
                "heartbeat = Path(sys.argv[2])",
                "pid_file = Path(sys.argv[3])",
                "child = subprocess.Popen([sys.executable, str(child_script), str(heartbeat), str(pid_file)])",
                "deadline = time.time() + 5",
                "while not pid_file.exists() and time.time() < deadline:",
                "    time.sleep(0.01)",
                "print('parent stdout before returncode 7', flush=True)",
                "print('parent stderr before returncode 7', file=sys.stderr, flush=True)",
                "sys.exit(7)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CodexSubprocessRunner()
    child_pid: int | None = None
    try:
        result = runner.run(
            [
                sys.executable,
                str(parent_script),
                str(child_script),
                str(heartbeat_file),
                str(child_pid_file),
            ],
            stdin="",
            timeout_seconds=5,
            output_last_message_path=None,
        )

        child_pid = _read_pid(child_pid_file)
        assert result.returncode == 7
        assert "parent stdout before returncode 7" in result.stdout
        assert "parent stderr before returncode 7" in result.stderr
        assert child_pid is not None
        assert _wait_for_pid_exit(child_pid, timeout_seconds=5)
    finally:
        if child_pid is None:
            child_pid = _read_pid(child_pid_file)
        if child_pid is not None and _pid_is_running(child_pid):
            _kill_pid_tree(child_pid)


async def _ensure_stage_11_4_database() -> None:
    admin_engine = create_async_engine(
        STAGE_11_4_ADMIN_DATABASE_URL,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        async with admin_engine.connect() as connection:
            result = await connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": STAGE_11_4_DATABASE_NAME},
            )
            if result.scalar_one_or_none() is None:
                await connection.execute(sa.text(f'CREATE DATABASE "{STAGE_11_4_DATABASE_NAME}"'))
    finally:
        await admin_engine.dispose()


async def _create_persisted_game(
    session_factory: async_sessionmaker,
) -> tuple[UUID, tuple[UUID, UUID]]:
    game_id = uuid4()
    player_ids = (uuid4(), uuid4())
    state = create_initial_game_state(
        seed="stage-11.4-snapshot-replay",
        game_id=str(game_id),
        players=(
            PlayerSetup(id=str(player_ids[0]), name="Ada", kind="human"),
            PlayerSetup(id=str(player_ids[1]), name="Grace", kind="human"),
        ),
    )
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                games.insert().values(
                    id=game_id,
                    status="active",
                    ruleset_version=state.ruleset_version,
                    seed=state.seed,
                    current_phase=state.turn.phase.value,
                    settings={"snapshot_interval": DEFAULT_SNAPSHOT_INTERVAL},
                    initial_state=state.model_dump(mode="json"),
                )
            )
            for seat_order, player_state in enumerate(state.players):
                await session.execute(
                    players.insert().values(
                        id=UUID(player_state.id),
                        game_id=game_id,
                        seat_order=seat_order,
                        name=player_state.name,
                        controller_type=player_state.kind,
                        state=player_state.model_dump(mode="json"),
                    )
                )
    return game_id, player_ids


async def _fetch_snapshots(
    session_factory: async_sessionmaker,
    game_id: UUID,
) -> list[Mapping[str, object]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(game_snapshots)
            .where(game_snapshots.c.game_id == game_id)
            .order_by(game_snapshots.c.event_sequence)
        )
        return [dict(row) for row in result.mappings().all()]


async def _count_events_after(
    session_factory: async_sessionmaker,
    *,
    game_id: UUID,
    sequence: int,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(game_events)
            .where(game_events.c.game_id == game_id, game_events.c.sequence > sequence)
        )
        return int(result.scalar_one())


def _read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.1)
    return not _pid_is_running(pid)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_pid_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
