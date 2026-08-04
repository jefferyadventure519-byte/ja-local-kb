"""Runtime service construction and registry-aware caching."""

from __future__ import annotations

from pathlib import Path

from .config import load_settings
from .embedding import ApiEmbedder
from .reranker import ApiReranker
from .secrets import EnvironmentSecretResolver, KeyringSecretResolver, SecretResolver
from .service import KnowledgeService


def make_secret_resolver(kind: str) -> SecretResolver:
    if kind == "keyring":
        return KeyringSecretResolver()
    if kind == "environment":
        return EnvironmentSecretResolver()
    raise ValueError(f"Unknown secret resolver: {kind}")


class ServiceManager:
    def __init__(
        self,
        settings_path: Path,
        *,
        secret_resolver: str = "keyring",
    ) -> None:
        self.settings_path = settings_path.expanduser().resolve(strict=True)
        self.resolver = make_secret_resolver(secret_resolver)
        self._service: KnowledgeService | None = None
        self._fingerprint: tuple[int, int] | None = None

    def get(self) -> KnowledgeService:
        settings_stat = self.settings_path.stat()
        settings = load_settings(self.settings_path)
        registry_stat = settings.source_registry.stat()
        fingerprint = (
            settings_stat.st_mtime_ns,
            registry_stat.st_mtime_ns,
        )
        if self._service is None or fingerprint != self._fingerprint:
            embedder = ApiEmbedder(settings.embedding, self.resolver)
            reranker = (
                ApiReranker(settings.reranker, self.resolver)
                if settings.reranker is not None
                else None
            )
            self._service = KnowledgeService(settings, embedder, reranker)
            self._fingerprint = fingerprint
        return self._service
