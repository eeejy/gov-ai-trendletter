"""Anthropic Messages API. 공식 SDK 를 쓴다.

SDK 는 필요한 사람만 깔면 된다 — pip install anthropic
"""

from __future__ import annotations

from typing import List, Optional

from .base import Provider, ProviderError

MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


class AnthropicApi(Provider):
    def _client(self):
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "anthropic 패키지가 없습니다. pip install anthropic") from exc
        key = self.key("anthropic.api_key")
        if not key:
            raise ProviderError(
                "Anthropic 키가 없습니다. config/secrets.yaml 의 anthropic.api_key "
                "또는 환경변수 TRENDLETTER_ANTHROPIC_API_KEY")
        return anthropic.Anthropic(api_key=key, timeout=float(self.timeout()))

    def available(self) -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(self.key("anthropic.api_key"))

    def why_not(self) -> str:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "pip install anthropic 이 필요합니다"
        return "config/secrets.yaml 의 anthropic.api_key 를 채우세요"

    def generate(self, prompt: str, timeout: Optional[int] = None) -> str:
        client = self._client()
        model = str(self.opts.get("model") or MODELS[0])
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=int(self.opts.get("max_tokens", 16000)),
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:                      # noqa: BLE001
            raise ProviderError("Anthropic 호출 실패: %s" % str(exc)[:200]) from exc
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def models(self) -> List[str]:
        return list(MODELS)
