from __future__ import annotations

import json
import logging

from app.core.config import Settings
from app.core.logging import StructuredJsonFormatter


def test_structured_logging_formatter_emits_json_record() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
    )
    formatter = StructuredJsonFormatter(settings=settings)
    record = logging.LogRecord(
        name="monopoly.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="backend foundation ready",
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "monopoly.test"
    assert payload["message"] == "backend foundation ready"
    assert payload["service"] == "api"
    assert payload["environment"] == "test"
