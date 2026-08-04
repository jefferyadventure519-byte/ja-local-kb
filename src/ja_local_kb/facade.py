"""Stable structured tool facade shared by MCP and tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import KnowledgeBaseError
from .runtime import ServiceManager


class ToolFacade:
    def __init__(self, manager: ServiceManager) -> None:
        self.manager = manager

    @staticmethod
    def _call(operation: Callable[[], dict]) -> dict:
        try:
            return {"ok": True, "result": operation()}
        except KnowledgeBaseError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            }
        except (KeyError, ValueError) as exc:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": str(exc),
                    "details": {},
                },
            }

    def search_knowledge(
        self,
        query: str,
        mode: str = "recall",
        top_k: int | None = None,
        project_ids: list[str] | None = None,
        include_candidates: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self.manager.get().search(
                query,
                mode=mode,
                top_k=top_k,
                project_ids=project_ids,
                include_candidates=include_candidates,
            )
        )

    def get_source(
        self,
        source_id: str | None = None,
        evidence_id: str | None = None,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self.manager.get().get_source(
                source_id=source_id,
                evidence_id=evidence_id,
                max_chars=max_chars,
            )
        )

    def get_knowledge_status(self) -> dict[str, Any]:
        return self._call(lambda: self.manager.get().status())
