"""Local-only retrieval corpus helpers for Stage 9."""

from app.rag.corpus import (
    CorpusDocument,
    SOURCE_TYPES,
    SourceType,
    build_ai_memory_corpus,
    build_contract_example_corpus,
    build_house_rule_corpus,
    build_negotiation_history_corpus,
    build_past_decision_corpus,
    build_rules_corpus,
    build_static_local_corpus,
)
from app.rag.retrieval import (
    RetrievalSearchResult,
    embed_text,
    refresh_rag_index_entries,
    search_retrieval,
)

__all__ = [
    "CorpusDocument",
    "SOURCE_TYPES",
    "SourceType",
    "build_ai_memory_corpus",
    "build_contract_example_corpus",
    "build_house_rule_corpus",
    "build_negotiation_history_corpus",
    "build_past_decision_corpus",
    "build_rules_corpus",
    "build_static_local_corpus",
    "RetrievalSearchResult",
    "embed_text",
    "refresh_rag_index_entries",
    "search_retrieval",
]
