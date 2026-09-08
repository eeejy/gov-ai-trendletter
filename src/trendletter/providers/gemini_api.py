"""Google Gemini REST. SDK 없이 요청만 보낸다."""

from __future__ import annotations

from typing import List, Optional

import requests

from .base import Provider, ProviderError

API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"]


class GeminiApi(Provider):
    def available(self) -> bool:
        return bool(self.key("gemini.api_key"))

    def why_not(self) -> str:
        return ("config/secrets.yaml 의 gemini.api_key 를 채우세요. "
                "aistudio.google.com 에서 무료로 받습니다")

    def generate(self, prompt: str, timeout: Optional[int] = None) -> str:
        key = self.key("gemini.api_key")
        if not key:
            raise ProviderError(self.why_not())
        model = str(self.opts.get("model") or MODELS[0])
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": int(self.opts.get("max_tokens", 8192))}}
        try:
            r = requests.post(API % model,
                              headers={"x-goog-api-key": key,
                                       "Content-Type": "application/json"},
                              json=body, timeout=self.timeout(timeout))
            if r.status_code == 404:
                raise ProviderError(
                    "그 모델을 쓸 수 없습니다(%s). 설정에서 모델을 바꿔 보세요.\n  %s"
                    % (model, r.json().get("error", {}).get("message", "")[:160]))
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            raise ProviderError("Gemini 호출 실패: %s" % str(exc)[:200]) from exc
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            raise ProviderError("빈 응답 (%s)" % cand.get("finishReason", "?"))
        return text

    def models(self) -> List[str]:
        return list(MODELS)
