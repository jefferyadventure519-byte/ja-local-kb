from __future__ import annotations

import unittest

import httpx

from ja_local_kb.embedding import ApiEmbedder
from ja_local_kb.errors import EmbeddingUnavailableError
from ja_local_kb.models import EmbeddingConfig


class StaticSecrets:
    def get(self, name: str) -> str:
        return "test-secret"


class EmbeddingTests(unittest.TestCase):
    def make_embedder(self, handler, **changes) -> ApiEmbedder:
        config = EmbeddingConfig(
            provider="openai",
            model="text-embedding-3-small",
            dimensions=3,
            batch_size=2,
            max_retries=0,
        ).model_copy(update=changes)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return ApiEmbedder(config, StaticSecrets(), client=client)

    def test_restores_provider_results_to_input_order(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0, 1, 0]},
                        {"index": 0, "embedding": [1, 0, 0]},
                    ]
                },
            )

        embedder = self.make_embedder(handler)
        self.assertEqual(
            embedder.embed(["first", "second"]),
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        )
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-secret")
        self.assertNotIn("test-secret", str(embedder.config))

    def test_batches_requests(self) -> None:
        sizes = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = __import__("json").loads(request.content)
            sizes.append(len(payload["input"]))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [1, 0, 0]}
                        for index, _ in enumerate(payload["input"])
                    ]
                },
            )

        embedder = self.make_embedder(handler)
        self.assertEqual(len(embedder.embed(["a", "b", "c"])), 3)
        self.assertEqual(sizes, [2, 1])

    def test_rejects_dimension_mismatch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1, 0]}]},
            )

        with self.assertRaises(EmbeddingUnavailableError):
            self.make_embedder(handler).embed(["first"])

    def test_rejects_missing_indexes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [1, 0, 0]},
                        {"index": 2, "embedding": [0, 1, 0]},
                    ]
                },
            )

        with self.assertRaises(EmbeddingUnavailableError):
            self.make_embedder(handler).embed(["first", "second"])


if __name__ == "__main__":
    unittest.main()
