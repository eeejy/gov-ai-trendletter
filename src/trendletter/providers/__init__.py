"""모델 제공자. 넷 중에서 고른다.

  claude_cli   Claude Code CLI (별도 키 없이 구독을 그대로 쓴다)
  anthropic    Anthropic API
  openai       OpenAI API
  ollama       내 컴퓨터에서 도는 모델 (키 불필요)
  gemini       Google Gemini API

어느 것을 고르든 `generate(prompt) -> str` 하나만 하면 된다.
파이프라인은 어느 모델이 붙었는지 모른다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import load
from .base import Provider, ProviderError

_REGISTRY: Dict[str, Any] = {}


def _load(name: str):
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name == "claude_cli":
        from .claude_cli import ClaudeCli as C
    elif name == "anthropic":
        from .anthropic_api import AnthropicApi as C
    elif name in ("openai", "ollama"):
        from .openai_api import OpenAiApi as C
    elif name == "gemini":
        from .gemini_api import GeminiApi as C
    else:
        raise ProviderError("모르는 제공자입니다: %s" % name)
    _REGISTRY[name] = C
    return C


NAMES = ("claude_cli", "anthropic", "openai", "ollama", "gemini")

# 문장 안에 넣을 짧은 이름. LABELS 는 설정 화면 표에 쓰는 긴 이름이라
# "Claude Code (설치된 CLI)가 본문을 써 줍니다" 처럼 어색해진다.
SHORT = {
    "claude_cli": "클로드",
    "anthropic": "클로드",
    "openai": "GPT",
    "ollama": "내 컴퓨터 모델",
    "gemini": "제미나이",
}

# 못 쓸 때 무엇을 하면 되는지. doctor 와 설정 화면이 같은 문구를 쓴다.
HINTS = {
    "claude_cli": "npm i -g @anthropic-ai/claude-code 로 설치한 뒤 claude 로 로그인",
    "anthropic": "console.anthropic.com 에서 키를 받아 설정 화면 ⑥ 에 넣으세요",
    "openai": "platform.openai.com 에서 키를 받아 설정 화면 ⑥ 에 넣으세요",
    "ollama": "ollama.com 에서 설치하고 ollama serve 로 띄운 뒤 모델을 하나 받으세요",
    "gemini": "aistudio.google.com 에서 키를 받아 설정 화면 ⑥ 에 넣으세요",
}

LABELS = {
    "claude_cli": "Claude Code (설치된 CLI)",
    "anthropic": "Claude (Anthropic API)",
    "openai": "GPT (OpenAI API)",
    "ollama": "내 컴퓨터 모델 (Ollama)",
    "gemini": "Gemini (Google API)",
}


def get(name: Optional[str] = None) -> Provider:
    cfg = load()
    name = name or cfg.get("llm.provider", "claude_cli")
    return _load(name)(name, cfg)


def status_all() -> List[Dict[str, Any]]:
    """화면에 뿌릴 제공자 목록과 각각의 상태."""
    out = []
    for n in NAMES:
        row = {"name": n, "label": LABELS[n], "hint": HINTS.get(n, "")}
        try:
            p = get(n)
            row.update(p.status())
        except Exception as exc:                      # noqa: BLE001
            row.update({"ready": False, "detail": str(exc)[:90]})
        out.append(row)
    return out
