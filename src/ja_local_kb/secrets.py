"""Secret resolvers. Secret values never enter settings or logs."""

from __future__ import annotations

import os
from typing import Protocol

from .errors import ConfigurationError


class SecretResolver(Protocol):
    def get(self, name: str) -> str:
        """Return a secret value or raise a safe configuration error."""


class EnvironmentSecretResolver:
    """Development and CI resolver; production setup uses the OS keychain."""

    def get(self, name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigurationError(
                "Model API credential is unavailable",
                details={"secret_name": name, "resolver": "environment"},
            )
        return value


class KeyringSecretResolver:
    def __init__(self, service_name: str = "ja-local-kb") -> None:
        self.service_name = service_name

    def get(self, name: str) -> str:
        try:
            import keyring
        except ImportError as exc:
            raise ConfigurationError(
                "OS credential-store support is not installed",
                details={"required_extra": "secrets"},
            ) from exc
        value = keyring.get_password(self.service_name, name)
        if not value:
            raise ConfigurationError(
                "Model API credential is unavailable",
                details={
                    "secret_name": name,
                    "resolver": "os_credential_store",
                },
            )
        return value
