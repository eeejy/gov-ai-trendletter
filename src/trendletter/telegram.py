"""텔레그램 발송.

동향지 전문 대신 20초짜리 요약을 보내고, 모바일에서 읽을 HTML 주소를 함께 준다.
토큰은 config/secrets.yaml 또는 환경변수(TRENDLETTER_TELEGRAM_TOKEN)에 둔다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

from . import llm
from .config import Config, load
from .models import Issue

API = "https://api.telegram.org/bot%s/%s"


class TelegramError(RuntimeError):
    pass


def _creds(cfg: Config) -> tuple:
    token = cfg.secret("telegram.token")
    chat_id = cfg.secret("telegram.chat_id")
    if not token or not chat_id:
        raise TelegramError(
            "텔레그램 토큰이 없습니다. config/secrets.yaml 에 아래처럼 적거나\n"
            "  telegram:\n    token: \"123:ABC\"\n    chat_id: \"-100...\"\n"
            "환경변수 TRENDLETTER_TELEGRAM_TOKEN / TRENDLETTER_TELEGRAM_CHAT_ID 를 쓰세요."
        )
    return token, chat_id


# ---------------------------------------------------------------- 요약문
def _fallback_summary(issue: Issue, cfg: Config) -> str:
    """Claude 를 못 쓸 때 쓰는 규칙 기반 요약."""
    lines = [
        "📰 %s · %d.%d~%d.%d"
        % (
            issue.label,
            issue.period_from.month, issue.period_from.day,
            issue.period_to.month, issue.period_to.day,
        )
    ]
    if issue.theme:
        lines += ["", issue.theme]
    lines.append("")
    # 키캡 이모지는 한 글자가 세 코드포인트라 문자열 인덱싱으로는 잘린다.
    marks = ["1\u20e3", "2\u20e3", "3\u20e3", "4\u20e3", "5\u20e3",
             "6\u20e3", "7\u20e3", "8\u20e3", "9\u20e3"]
    for i, it in enumerate(issue.items[: int(cfg.get("telegram.max_items", 6))]):
        mark = marks[i] if i < len(marks) else "\u25aa\ufe0f"
        hot = "🔴 " if it.impact == "높음" else ""
        lines.append("%s %s%s" % (mark, hot, it.title[:34]))
        note = it.note.strip()
        if note:
            lines.append("   → %s" % note[:44])
    return "\n".join(lines)


def summarize(issue: Issue, cfg: Optional[Config] = None) -> str:
    """Claude 로 20초짜리 요약을 만든다. 실패하면 규칙 기반으로 넘어간다."""
    cfg = cfg or load()
    if not cfg.get("llm.enabled", True) or not llm.available():
        return _fallback_summary(issue, cfg)

    payload = {
        "label": issue.label,
        "period": "%d.%d~%d.%d" % (
            issue.period_from.month, issue.period_from.day,
            issue.period_to.month, issue.period_to.day,
        ),
        "theme": issue.theme,
        "items": [
            {
                "no": it.no,
                "title": it.title,
                "impact": it.impact,
                "note_kind": it.note_kind,
                "note": it.note,
            }
            for it in issue.items
        ],
    }
    prompt = (
        llm._load_prompt("telegram.md")
        .replace("{{ISSUE_LABEL}}", issue.label)
        .replace("{{PERIOD}}", payload["period"])
        .replace("{{THEME}}", issue.theme or "")
        .replace("{{ISSUE_JSON}}", json.dumps(payload, ensure_ascii=False, indent=1))
    )
    try:
        text = (llm._extract_json(llm.run(prompt)).get("text") or "").strip()
    except Exception:  # noqa: BLE001 - 발송을 막지 않는다
        return _fallback_summary(issue, cfg)
    return text or _fallback_summary(issue, cfg)


# ---------------------------------------------------------------- 발송
def _call(token: str, method: str, **kw) -> Dict[str, Any]:
    r = requests.post(API % (token, method), data=kw, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise TelegramError("텔레그램 %s 실패: %s" % (method, data.get("description")))
    return data["result"]


def send(
    issue: Issue,
    html_url: str = "",
    cfg: Optional[Config] = None,
    dry_run: bool = False,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """요약을 보내고, 모바일에서 볼 HTML 주소를 버튼으로 붙인다."""
    cfg = cfg or load()
    body = text if text is not None else summarize(issue, cfg)

    if html_url:
        body += "\n\n📱 전문 보기 (모바일 최적화)\n%s" % html_url

    if dry_run:
        return {"ok": True, "dry_run": True, "text": body, "length": len(body)}

    token, chat_id = _creds(cfg)
    kw: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": body,
        "disable_web_page_preview": "false" if html_url else "true",
    }
    if html_url:
        kw["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "📖 동향지 전문 보기", "url": html_url}]]}
        )
    result = _call(token, "sendMessage", **kw)
    return {"ok": True, "message_id": result.get("message_id"), "text": body}


def check(cfg: Optional[Config] = None) -> Dict[str, Any]:
    """토큰과 대상 방이 살아 있는지 확인한다."""
    cfg = cfg or load()
    token, chat_id = _creds(cfg)
    me = _call(token, "getMe")
    chat = _call(token, "getChat", chat_id=chat_id)
    return {
        "bot": me.get("username"),
        "chat": chat.get("title") or chat.get("username") or chat_id,
        "chat_type": chat.get("type"),
    }
