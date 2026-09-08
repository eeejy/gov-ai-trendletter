"""OpenAI Chat Completions — 그리고 같은 규격을 쓰는 Ollama.

Ollama 는 http://localhost:11434/v1 에서 같은 모양을 낸다. 키가 필요 없고
설치된 모델 목록을 /api/tags 로 읽을 수 있다. 그래서 코드를 함께 쓴다.
"""

from __future__ import annotations

from typing import List, Optional

import requests

from .base import Provider, ProviderError

OLLAMA_BASE = "http://localhost:11434/v1"


class OpenAiApi(Provider):
    @property
    def is_local(self) -> bool:
        return self.name == "ollama"

    def base_url(self) -> str:
        return str(self.opts.get("base_url")
                   or (OLLAMA_BASE if self.is_local else "https://api.openai.com/v1")
                   ).rstrip("/")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.is_local:
            return h                                  # 로컬은 키가 없다
        key = self.key("openai.api_key")
        if not key:
            raise ProviderError(
                "OpenAI 키가 없습니다. config/secrets.yaml 의 openai.api_key")
        h["Authorization"] = "Bearer %s" % key
        return h

    def available(self) -> bool:
        if self.is_local:
            try:
                r = requests.get(self.base_url().replace("/v1", "") + "/api/tags",
                                 timeout=3)
                return r.status_code == 200
            except requests.RequestException:
                return False
        return bool(self.key("openai.api_key"))

    def why_not(self) -> str:
        if self.is_local:
            return ("Ollama 가 안 돌고 있습니다. 터미널에서 `ollama serve` 를 "
                    "켜거나 https://ollama.com 에서 설치하세요")
        return "config/secrets.yaml 의 openai.api_key 를 채우세요"

    def generate(self, prompt: str, timeout: Optional[int] = None) -> str:
        model = str(self.opts.get("model") or "")
        if not model:
            got = self.models()
            model = got[0] if got else ("llama3.1" if self.is_local else "gpt-5")
        body = {"model": model,
                "messages": [{"role": "user", "content": prompt}]}
        if self.opts.get("max_tokens"):
            body["max_tokens"] = int(self.opts["max_tokens"])
        try:
            r = requests.post(self.base_url() + "/chat/completions",
                              headers=self._headers(), json=body,
                              timeout=self.timeout(timeout))
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            raise ProviderError("%s 호출 실패: %s"
                                % (self.name, str(exc)[:200])) from exc
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("응답을 읽지 못했습니다: %s"
                                % str(data)[:200]) from exc

    def models(self) -> List[str]:
        """Ollama 는 설치된 모델을 알려 준다. 화면에서 고르게 한다."""
        if not self.is_local:
            return ["gpt-5", "gpt-5-mini"]
        try:
            r = requests.get(self.base_url().replace("/v1", "") + "/api/tags",
                             timeout=4)
            r.raise_for_status()
            return [m["name"] for m in (r.json().get("models") or []) if m.get("name")]
        except requests.RequestException:
            return []
