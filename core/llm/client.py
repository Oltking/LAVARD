"""OpenAI-compatible chat client with cheapest-accurate tier routing (Router §4.4 seed).

Vendor-agnostic by construction: it speaks the OpenAI /chat/completions wire format to whatever
`LAVARD_MODEL_ENDPOINT` points at. If no endpoint/key is configured, `is_configured` is False and
callers fall back to the heuristic planner — so the whole system runs offline for demos.

The four tiers (trivial|routine|complex|critical) map to configurable model names; the Router
will later choose the tier per step. `critical` (Vetter verdicts, anything spending money) should
favor the strongest model regardless of cost.
"""

from __future__ import annotations

import json
from typing import Literal

from core.config import Settings, get_settings

Tier = Literal["trivial", "routine", "complex", "critical"]


class ModelClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return self.s.model_configured

    def _model_for(self, tier: Tier) -> str:
        return {
            "trivial": self.s.model_trivial,
            "routine": self.s.model_routine,
            "complex": self.s.model_complex,
            "critical": self.s.model_critical,
        }[tier]

    def complete_json(
        self,
        system: str,
        user: str,
        tier: Tier = "routine",
        timeout: float = 60.0,
    ) -> dict:
        """Call the model and parse a JSON object from its reply.

        Raises RuntimeError if not configured — callers must check `is_configured` first.
        """
        if not self.is_configured:
            raise RuntimeError("No model endpoint configured; use the heuristic fallback.")

        import httpx  # lazy: keeps `core` importable with zero third-party deps

        url = self.s.model_endpoint.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._model_for(tier),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.s.model_api_key}"}
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        return _loads_lenient(content)


def _loads_lenient(text: str) -> dict:
    """Parse JSON, tolerating a ```json fence or leading/trailing prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)
