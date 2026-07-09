"""Unified LLM client. Prefers Azure OpenAI (the configured provider); falls back to
Anthropic if that's what's keyed instead. Returns None-safe results so every caller
degrades gracefully when no provider is configured yet.

Azure OpenAI env:
  AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
  AZURE_OPENAI_API_VERSION (optional, default 2024-06-01)
Anthropic env:
  ANTHROPIC_API_KEY  (+ optional JOBHUNTER_MODEL)
"""
from __future__ import annotations

import json
import os
from typing import Optional


def _azure_ready() -> bool:
    return bool(os.environ.get("AZURE_OPENAI_API_KEY")
                and os.environ.get("AZURE_OPENAI_ENDPOINT")
                and os.environ.get("AZURE_OPENAI_DEPLOYMENT"))


def _anthropic_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class AIClient:
    def __init__(self):
        self.provider: Optional[str] = None
        self._client = None
        if _azure_ready():
            try:
                from openai import AzureOpenAI
                self._client = AzureOpenAI(
                    api_key=os.environ["AZURE_OPENAI_API_KEY"],
                    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
                )
                self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
                self.provider = "azure"
            except ImportError:
                pass
        elif _anthropic_ready():
            try:
                import anthropic
                self._client = anthropic.Anthropic()
                self.model = os.environ.get("JOBHUNTER_MODEL", "claude-sonnet-5")
                self.provider = "anthropic"
            except ImportError:
                pass

    def available(self) -> bool:
        return self.provider is not None

    def complete(self, user: str, system: str | None = None, *,
                 max_tokens: int = 600, temperature: float = 0.4) -> Optional[str]:
        """Return the model's text, or None on any failure (caller falls back)."""
        if not self.available():
            return None
        try:
            if self.provider == "azure":
                msgs = ([{"role": "system", "content": system}] if system else []) + \
                       [{"role": "user", "content": user}]
                resp = self._client.chat.completions.create(
                    model=self.deployment, messages=msgs,
                    max_tokens=max_tokens, temperature=temperature)
                return (resp.choices[0].message.content or "").strip()
            else:  # anthropic
                resp = self._client.messages.create(
                    model=self.model, max_tokens=max_tokens,
                    system=system or "", temperature=temperature,
                    messages=[{"role": "user", "content": user}])
                return resp.content[0].text.strip()
        except Exception:
            return None

    def complete_json(self, user: str, system: str | None = None, *,
                      max_tokens: int = 600) -> Optional[dict]:
        """Complete and parse a JSON object out of the reply. None on failure."""
        raw = self.complete(user, system, max_tokens=max_tokens, temperature=0.2)
        if not raw:
            return None
        try:
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None


_singleton: Optional[AIClient] = None


def client() -> AIClient:
    global _singleton
    if _singleton is None:
        _singleton = AIClient()
    return _singleton
