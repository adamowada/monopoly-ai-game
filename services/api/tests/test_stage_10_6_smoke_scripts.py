from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_SMOKE_PATH = REPO_ROOT / "scripts" / "product_smoke.py"
LIVE_SMOKE_PATH = REPO_ROOT / "services" / "api" / "scripts" / "live_codex_ai_smoke.py"


def test_product_smoke_script_declares_required_tier_labels() -> None:
    module = _load_product_smoke_module()

    assert set(module.SMOKE_TIERS) >= {
        "docker stack",
        "database migration",
        "API health",
        "game creation",
        "scripted turn",
        "fake AI",
    }
    assert module.LIVE_CODEX_ENV_VAR == "RUN_LIVE_CODEX_AI"


def test_root_smoke_scripts_preserve_scaffold_checks_and_add_product_smoke() -> None:
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert "scripts/phase0_check.py smoke" in scripts["test:smoke"]
    assert "scripts/scaffold_check.py smoke" in scripts["test:smoke"]
    assert "scripts/product_smoke.py" in scripts["test:smoke"]
    assert "RUN_LIVE_CODEX_AI" in scripts["test:smoke:live"]
    assert "live_codex_ai_smoke.py" in scripts["test:smoke:live"]


def test_live_codex_smoke_stays_gated_and_uses_xhigh_exec_json() -> None:
    source = LIVE_SMOKE_PATH.read_text(encoding="utf-8")

    assert "RUN_LIVE_CODEX_AI" in source
    assert "codex exec" in source
    assert "model_reasoning_effort" in source
    assert "xhigh" in source
    assert "--json" in source


def _load_product_smoke_module():
    spec = importlib.util.spec_from_file_location("product_smoke", PRODUCT_SMOKE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
