"""Domain errors with stable machine-readable codes."""

from __future__ import annotations


class KnowledgeBaseError(RuntimeError):
    code = "knowledge_base_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ConfigurationError(KnowledgeBaseError):
    code = "configuration_error"


class IdentityConflictError(KnowledgeBaseError):
    code = "identity_conflict"


class FreshnessError(KnowledgeBaseError):
    code = "stale"


class EmbeddingUnavailableError(KnowledgeBaseError):
    code = "embedding_unavailable"


class RerankerUnavailableError(KnowledgeBaseError):
    code = "reranker_unavailable"


class SourceMissingError(KnowledgeBaseError):
    code = "missing"


class IndexIntegrityError(KnowledgeBaseError):
    code = "index_integrity_error"


class NotIndexedError(KnowledgeBaseError):
    code = "not_indexed"
