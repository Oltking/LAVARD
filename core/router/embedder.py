"""Embeddings interface (provider-agnostic, spec §0.5) + a deterministic local fallback.

`LocalHashEmbedder` needs no network: it hashes tokens into a fixed-dim bag-of-words vector and
L2-normalizes, so lexically-overlapping paraphrases land near each other — enough to demonstrate
the semantic cache offline. When a model endpoint is configured, `provider_embedder` returns an
`OpenAiCompatibleEmbedder` instead. Nothing here hard-wires a vendor.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_DIM = 128
_TOKEN = re.compile(r"[a-z0-9]+")

# tiny stopword list so "what is the price of X" ~ "price of X"
_STOP = {"the", "a", "an", "of", "is", "are", "to", "for", "and", "or", "in", "on", "what",
         "how", "do", "does", "i", "we", "you", "please", "can", "should"}


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalHashEmbedder:
    dim = _DIM

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in _TOKEN.findall(text.lower()):
            if tok in _STOP:
                continue
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % _DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class OpenAiCompatibleEmbedder:  # pragma: no cover - needs a configured endpoint
    def __init__(self, endpoint: str, api_key: str, model: str = "text-embedding-3-small") -> None:
        self.endpoint, self.api_key, self.model = endpoint, api_key, model

    def embed(self, text: str) -> list[float]:
        import httpx

        resp = httpx.post(
            self.endpoint.rstrip("/") + "/embeddings",
            json={"model": self.model, "input": text},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))  # inputs are L2-normalized


def get_embedder() -> Embedder:
    from core.config import get_settings

    s = get_settings()
    if s.model_configured:
        return OpenAiCompatibleEmbedder(s.model_endpoint, s.model_api_key)
    return LocalHashEmbedder()
