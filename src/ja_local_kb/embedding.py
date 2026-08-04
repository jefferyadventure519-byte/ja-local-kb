"""Embedding provider boundary and strict OpenAI-compatible implementation."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol

import httpx

from .errors import EmbeddingUnavailableError
from .models import EmbeddingConfig
from .secrets import SecretResolver


class Embedder(Protocol):
    @property
    def fingerprint(self) -> str:
        """Identity of the provider/model/vector space."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in input order."""


class ApiEmbedder:
    def __init__(
        self,
        config: EmbeddingConfig,
        secrets: SecretResolver,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.secrets = secrets
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)

    @property
    def fingerprint(self) -> str:
        return self.config.fingerprint

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = list(texts)
        if not inputs:
            return []
        if any(not value.strip() for value in inputs):
            raise EmbeddingUnavailableError("Embedding input cannot be empty")
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self.config.batch_size):
            vectors.extend(
                self._embed_batch(inputs[start : start + self.config.batch_size])
            )
        expected_dimension = len(vectors[0])
        if expected_dimension < 1 or any(
            len(vector) != expected_dimension for vector in vectors
        ):
            raise EmbeddingUnavailableError(
                "Embedding provider returned inconsistent dimensions"
            )
        if (
            self.config.dimensions is not None
            and expected_dimension != self.config.dimensions
        ):
            raise EmbeddingUnavailableError(
                "Embedding provider ignored the configured vector dimension",
                details={
                    "expected": self.config.dimensions,
                    "actual": expected_dimension,
                },
            )
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {
            "model": self.config.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.config.dimensions is not None:
            payload["dimensions"] = self.config.dimensions
        url = f"{self.config.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.secrets.get(self.config.secret_name)}",
            "Content-Type": "application/json",
        }
        last_reason = "unknown"
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    last_reason = f"http_{response.status_code}"
                    if attempt < self.config.max_retries:
                        time.sleep(min(0.5 * (2**attempt), 4.0))
                        continue
                response.raise_for_status()
                return self._validate_response(response.json(), len(texts))
            except (
                httpx.RequestError,
                httpx.HTTPStatusError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:
                last_reason = type(exc).__name__
                retryable = isinstance(exc, httpx.RequestError)
                if retryable and attempt < self.config.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 4.0))
                    continue
                break
        raise EmbeddingUnavailableError(
            "Embedding API request failed",
            details={
                "provider": self.config.provider,
                "model": self.config.model,
                "reason": last_reason,
            },
        )

    @staticmethod
    def _validate_response(
        payload: object,
        expected_count: int,
    ) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("missing data array")
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        if len(ordered) != expected_count:
            raise ValueError("embedding count mismatch")
        if [item["index"] for item in ordered] != list(range(expected_count)):
            raise ValueError("embedding indexes are incomplete")
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item["embedding"]
            if not isinstance(vector, list) or not vector:
                raise ValueError("invalid embedding vector")
            if not all(isinstance(value, (int, float)) for value in vector):
                raise ValueError("embedding vector is not numeric")
            vectors.append([float(value) for value in vector])
        return vectors
