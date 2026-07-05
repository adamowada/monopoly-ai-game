from __future__ import annotations

from pathlib import Path


REQUIRED_CONTENT_RULES_FILES: tuple[str, ...] = (
    "classic_monopoly.json",
    "house_rules_and_deviations.json",
    "contract_examples.json",
)


def resolve_content_rules_dir(start_path: Path | str = Path(__file__)) -> Path:
    """Resolve content/rules from either source checkout or packaged API layout."""

    start = Path(start_path).resolve()
    start_dir = start if start.is_dir() else start.parent
    searched: list[Path] = []

    for base_dir in (start_dir, *start_dir.parents):
        candidate = base_dir / "content" / "rules"
        searched.append(candidate)
        if candidate.exists():
            return require_content_rules_dir(candidate)

    expected = ", ".join(REQUIRED_CONTENT_RULES_FILES)
    searched_text = "; ".join(path.as_posix() for path in searched)
    raise FileNotFoundError(
        "Required rules corpus directory was not found. "
        f"Expected content/rules containing: {expected}. "
        f"Searched: {searched_text}"
    )


def require_content_rules_dir(content_rules_dir: Path | str) -> Path:
    rules_dir = Path(content_rules_dir).resolve()
    if not rules_dir.is_dir():
        raise FileNotFoundError(f"Required rules corpus directory does not exist: {rules_dir}")

    missing = [
        filename
        for filename in REQUIRED_CONTENT_RULES_FILES
        if not (rules_dir / filename).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Required rules corpus directory {rules_dir} is missing required file(s): "
            f"{', '.join(missing)}"
        )
    return rules_dir
