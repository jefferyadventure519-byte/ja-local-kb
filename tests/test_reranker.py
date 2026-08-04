from __future__ import annotations

import json
import unittest

import httpx

from ja_local_kb.errors import RerankerUnavailableError
from ja_local_kb.models import RerankerConfig
from ja_local_kb.reranker import ApiReranker


class StaticSecrets:
    def get(self, name: str) -> str:
        return "test-secret"


class RerankerTests(unittest.TestCase):
    def make_reranker(self, handler) -> ApiReranker:
        config = RerankerConfig(
            provider="siliconflow",
            model="Qwen/Qwen3-Reranker-8B",
            max_retries=0,
        )
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return ApiReranker(config, StaticSecrets(), client=client)

    def test_returns_scores_in_descending_order(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.25},
                        {"index": 1, "relevance_score": 0.75},
                    ]
                },
            )

        reranker = self.make_reranker(handler)
        results = reranker.rerank(
            "query",
            ["first", "second"],
            top_n=2,
        )
        self.assertEqual([item.index for item in results], [1, 0])
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-secret")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["top_n"], 2)
        self.assertFalse(payload["return_documents"])
        self.assertIn("specific", payload["instruction"])

    def test_rejects_duplicate_indexes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.75},
                        {"index": 0, "relevance_score": 0.25},
                    ]
                },
            )

        with self.assertRaises(RerankerUnavailableError):
            self.make_reranker(handler).rerank(
                "query",
                ["first", "second"],
                top_n=2,
            )


if __name__ == "__main__":
    unittest.main()
