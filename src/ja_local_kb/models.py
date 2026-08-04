"""Validated configuration and public data models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceStatus(StrEnum):
    NOT_INDEXED = "not_indexed"
    SYNCING = "syncing"
    FRESH = "fresh"
    STALE = "stale"
    FAILED = "failed"
    MISSING = "missing"
    IDENTITY_CONFLICT = "identity_conflict"
    DISABLED = "disabled"


class SourceSpec(StrictModel):
    source_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    project_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    client_id: str = ""
    document_role: str = Field(min_length=1)
    relative_path: str
    enabled: bool = True

    @field_validator("relative_path")
    @classmethod
    def validate_relative_markdown(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the selected Vault")
        if not normalized.lower().endswith(".md"):
            raise ValueError("only Markdown sources are supported")
        if ":" in path.parts[0]:
            raise ValueError("drive-qualified paths are forbidden")
        return normalized


class SourceRegistry(StrictModel):
    schema_version: Literal[1] = 1
    sources: list[SourceSpec]


class EmbeddingConfig(StrictModel):
    provider: Literal["openai", "openai_compatible"]
    model: str = Field(min_length=1)
    base_url: str = "https://api.openai.com/v1"
    secret_name: str = "embedding_api_key"
    dimensions: int | None = Field(default=None, ge=1)
    batch_size: int = Field(default=64, ge=1, le=2048)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)

    @property
    def fingerprint(self) -> str:
        dimensions = self.dimensions if self.dimensions is not None else "default"
        return f"{self.provider}:{self.base_url}:{self.model}:{dimensions}"


class RerankerConfig(StrictModel):
    provider: Literal["siliconflow"]
    model: str = Field(min_length=1)
    base_url: str = "https://api.siliconflow.cn/v1"
    secret_name: str = "embedding_api_key"
    instruction: str = Field(
        default=(
            "Rank each passage by whether it provides specific, current, "
            "directly citable evidence needed to answer the query. Prefer "
            "concrete decisions, rules, constraints, exceptions, state "
            "changes, and source facts over generic topical similarity. "
            "Treat a multi-part query as independent evidence needs: rank a "
            "passage highly when it directly supports any one named project, "
            "sub-question, boundary, or exception, even if it does not answer "
            "the whole query. Preserve complementary evidence."
        ),
        min_length=1,
    )
    timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.base_url}:{self.model}"


class ChunkingConfig(StrictModel):
    max_chars: int = Field(default=2200, ge=300, le=10000)
    overlap_chars: int = Field(default=120, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_overlap(self) -> ChunkingConfig:
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        return self


class RetrievalConfig(StrictModel):
    default_mode: Literal[
        "keyword",
        "vector",
        "hybrid",
        "smart",
        "recall",
        "quality",
    ] = "recall"
    simple_top_k: int = Field(default=8, ge=1, le=50)
    complex_candidate_k: int = Field(default=24, ge=8, le=100)
    complex_top_k: int = Field(default=24, ge=8, le=30)
    smart_candidate_k: int = Field(default=80, ge=24, le=200)
    smart_max_subqueries: int = Field(default=6, ge=2, le=8)
    smart_vector_subqueries: int = Field(default=6, ge=1, le=8)
    recall_candidate_k: int = Field(default=80, ge=24, le=200)
    quality_candidate_k: int = Field(default=80, ge=24, le=200)
    quality_top_k: int = Field(default=24, ge=8, le=60)
    evidence_min: int = Field(default=6, ge=1, le=20)
    evidence_max: int = Field(default=24, ge=1, le=60)


class AppSettings(StrictModel):
    schema_version: Literal[1] = 1
    vault_root: Path
    runtime_root: Path
    source_registry: Path
    state_path: Path | None = None
    lancedb_root: Path | None = None
    embedding: EmbeddingConfig
    reranker: RerankerConfig | None = None
    chunking: ChunkingConfig = ChunkingConfig()
    retrieval: RetrievalConfig = RetrievalConfig()

    @property
    def resolved_state_path(self) -> Path:
        return self.state_path or self.runtime_root / "state.sqlite3"

    @property
    def resolved_lancedb_root(self) -> Path:
        return self.lancedb_root or self.runtime_root / "lancedb"


class ParsedChunk(StrictModel):
    chunk_id: str
    source_id: str
    project_id: str
    project_name: str
    client_id: str
    document_role: str
    relative_path: str
    absolute_path: str
    file_name: str
    doc_type: str
    document_status: str
    heading: str
    source_text: str
    retrieval_text: str
    document_hash: str
    body_hash: str
    content_hash: str
    embedding_hash: str
    metadata_json: str
    wikilinks_json: str
    updated: str


class SearchEvidence(StrictModel):
    evidence_id: str
    chunk_id: str
    source_id: str
    project_id: str
    project_name: str
    client_id: str
    document_role: str
    relative_path: str
    heading: str
    source_text: str
    document_status: str
    updated: str
    lexical_rank: int | None = None
    vector_rank: int | None = None
    candidate_rank: int | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None
    fused_score: float
    index_version: int
    freshness: Literal["fresh"] = "fresh"
