"""제공자 공통."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ProviderError(RuntimeError):
    """제공자를 쓸 수 없는 상태."""


class Provider:
    """모든 제공자가 지켜야 할 것.

    generate(prompt) 하나면 파이프라인이 돈다. 나머지는 화면에서 상태를
    보여 주거나 모델을 고르기 위한 것이다.
    """

    def __init__(self, name: str, cfg) -> None:
        self.name = name
        self.cfg = cfg
        self.opts: Dict[str, Any] = dict(cfg.get("llm.%s" % name, {}) or {})

    # --- 부르는 쪽이 쓰는 것 ---------------------------------------------
    def available(self) -> bool:
        raise NotImplementedError

    def generate(self, prompt: str, timeout: Optional[int] = None) -> str:
        raise NotImplementedError

    # --- 화면용 ---------------------------------------------------------
    def check(self) -> str:
        """연결 시험. 성공하면 짧은 응답을 돌려준다."""
        return self.generate("한 단어로만 답하세요: 준비", timeout=40).strip()[:40]

    def models(self) -> List[str]:
        """고를 수 있는 모델. 모르면 빈 목록."""
        return []

    def status(self) -> Dict[str, Any]:
        try:
            ok = self.available()
        except Exception as exc:                      # noqa: BLE001
            return {"ready": False, "detail": str(exc)[:90]}
        return {"ready": ok, "model": self.opts.get("model", ""),
                "detail": "" if ok else self.why_not()}

    def why_not(self) -> str:
        return "쓸 수 없습니다"

    # --- 도우미 ---------------------------------------------------------
    def timeout(self, given: Optional[int] = None) -> int:
        return int(given or self.cfg.get("llm.timeout", 180))

    def key(self, dotted: str) -> str:
        return str(self.cfg.secret(dotted) or "")
