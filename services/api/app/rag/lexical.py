from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.rag.corpus import CorpusDocument, SourceType


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class LexicalSearchResult:
    document: CorpusDocument
    score: int
    matched_terms: tuple[str, ...]


def search_corpus(
    documents: Sequence[CorpusDocument],
    query: str,
    *,
    limit: int = 5,
    source_types: Iterable[SourceType] | None = None,
) -> list[LexicalSearchResult]:
    """Search in-memory documents with deterministic token overlap scoring."""

    query_terms = tuple(_tokenize(query))
    if not query_terms or limit <= 0:
        return []

    allowed_source_types = set(source_types) if source_types is not None else None
    results: list[LexicalSearchResult] = []
    for document in documents:
        if allowed_source_types is not None and document.source_type not in allowed_source_types:
            continue

        title_counts = Counter(_tokenize(document.title))
        text_counts = Counter(_tokenize(document.text))
        matched_terms = tuple(term for term in dict.fromkeys(query_terms) if term in text_counts)
        score = sum((title_counts[term] * 3) + text_counts[term] for term in query_terms)
        if score <= 0:
            continue
        results.append(
            LexicalSearchResult(
                document=document,
                score=score,
                matched_terms=matched_terms,
            )
        )

    return sorted(
        results,
        key=lambda result: (
            -result.score,
            result.document.source_type,
            result.document.document_id,
        ),
    )[:limit]


def _tokenize(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.lower())
