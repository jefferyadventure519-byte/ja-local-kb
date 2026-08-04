"""Reranker provider boundary and strict SiliconFlow implementation."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from .errors import RerankerUnavailableError
from .models import RerankerConfig
from .secrets import SecretResolver


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class Reranker(Protocol):
    @property
    def fingerprint(self) -> str:
        """Identity of the provider and ranking model."""

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int,
    ) -> list[RerankResult]:
        """Return document indexes ordered by descending relevance."""


class ApiReranker:
    def __init__(
        self,
        config: RerankerConfig,
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

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int,
    ) -> list[RerankResult]:
        normalized_query = query.strip()
        inputs = list(documents)
        if not normalized_query:
            raise RerankerUnavailableError("Reranker query cannot be empty")
        if not inputs or any(not document.strip() for document in inputs):
            raise RerankerUnavailableError("Reranker documents cannot be empty")
        if top_n < 1 or top_n > len(inputs):
            raise RerankerUnavailableError(
                "Reranker top_n is outside the document range",
                details={"top_n": top_n, "document_count": len(inputs)},
            )
        payload: dict[str, object] = {
            "model": self.config.model,
            "query": normalized_query,
            "documents": inputs,
            "instruction": self.config.instruction,
            "return_documents": False,
            "top_n": top_n,
        }
        url = f"{self.config.base_url.rstrip('/')}/rerank"
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
                return self._validate_response(
                    response.json(),
                    document_count=len(inputs),
                    expected_count=top_n,
                )
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
        raise RerankerUnavailableError(
            "Reranker API request failed",
            details={
                "provider": self.config.provider,
                "model": self.config.model,
                "reason": last_reason,
            },
        )

    @staticmethod
    def _validate_response(
        payload: object,
        *,
        document_count: int,
        expected_count: int,
    ) -> list[RerankResult]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"),
            list,
        ):
            raise ValueError("missing rerank results array")
        results = payload["results"]
        if len(results) != expected_count:
            raise ValueError("rerank result count mismatch")
        ranked: list[RerankResult] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("invalid rerank result item")
            index = item["index"]
            score = item["relevance_score"]
            if not isinstance(index, int) or not 0 <= index < document_count:
                raise ValueError("rerank result index is outside document range")
            if index in seen:
                raise ValueError("rerank result indexes contain duplicates")
            if not isinstance(score, (int, float)):
                raise ValueError("rerank relevance score is not numeric")
            seen.add(index)
            ranked.append(RerankResult(index=index, score=float(score)))
        return sorted(ranked, key=lambda item: (-item.score, item.index))
