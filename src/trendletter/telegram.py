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


def _creds(cfg: Config, admin: bool = False) -> tuple:
    """admin=True 면 담당자 알림용 방으로 보낸다.

    직원 배포용 방과 담당자 알림용 방을 나눠 두면,
    초안이 준비됐다는 알림이 전 직원에게 가지 않는다.
    admin_chat_id 가 없으면 같은 방을 쓴다.
    """
    token = cfg.secret("telegram.token")
    chat_id = (
        cfg.secret("telegram.admin_chat_id") if admin else None
    ) or cfg.secret("telegram.chat_id")
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
    html_file=None,
) -> Dict[str, Any]:
    """요약을 보낸다.

    전문을 여는 방법은 두 가지다.
      - `html_url`  주소가 있으면 버튼으로 붙인다. 누르면 바로 열려 가장 편하다
      - `html_file` 주소가 없으면 파일을 첨부한다. 호스팅이 없어도 되지만
                    휴대폰에서 열려면 내려받아 브라우저로 여는 단계가 하나 더 있다
    """
    cfg = cfg or load()
    body = text if text is not None else summarize(issue, cfg)

    if html_url:
        body += "\n\n📱 전문 보기 (휴대폰·PC 모두 최적화)\n%s" % html_url
    elif html_file:
        body += "\n\n📎 전문은 아래 파일입니다. 받아서 열면 그대로 보입니다."

    if dry_run:
        return {
            "ok": True, "dry_run": True, "text": body, "length": len(body),
            "attach": str(html_file) if html_file and not html_url else "",
        }

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
    out = {"ok": True, "message_id": result.get("message_id"), "text": body}

    # 주소가 없을 때만 파일을 붙인다. 둘 다 보내면 중복이다.
    if html_file and not html_url:
        from pathlib import Path as _P

        path = _P(html_file)
        if path.exists():
            # 파일명에 한글·괄호가 있으면 기기에 따라 내려받기나 열기가 막힌다.
            # 보낼 때만 영문 이름으로 바꾼다. (원본 파일명은 그대로 둔다)
            safe = "AI-trend-%d-%02d.html" % (issue.year, issue.number)
            with path.open("rb") as fp:
                r = requests.post(
                    API % (token, "sendDocument"),
                    data={
                        "chat_id": chat_id,
                        "caption": "%s 전문 — 받아서 열면 그대로 보입니다" % issue.label,
                    },
                    files={"document": (safe, fp, "text/html")},
                    timeout=120,
                )
            data = r.json()
            if data.get("ok"):
                out["file_message_id"] = data["result"].get("message_id")
            else:
                out["file_error"] = data.get("description")
    return out


def discover(token: str = "", cfg: Optional[Config] = None) -> Dict[str, Any]:
    """봇이 접근할 수 있는 방을 찾아 chat_id 를 알려준다.

    getUpdates 는 웹훅이 걸린 봇에서는 쓸 수 없다. 기존 시스템이 웹훅을 쓰고
    있을 수 있으므로, 그 경우에는 끄라고 하지 않고 다른 방법을 안내한다.
    """
    cfg = cfg or load()
    token = token or cfg.secret("telegram.token") or ""
    if not token:
        raise TelegramError("토큰이 없습니다. config/secrets.yaml 에 넣거나 --token 으로 주세요.")

    me = _call(token, "getMe")
    out: Dict[str, Any] = {"bot": me.get("username"), "chats": [], "webhook": False}

    hook = _call(token, "getWebhookInfo")
    if hook.get("url"):
        out["webhook"] = True
        out["webhook_url"] = hook["url"]
        return out

    r = requests.post(API % (token, "getUpdates"), data={"limit": 100}, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise TelegramError("getUpdates 실패: %s" % data.get("description"))

    seen = {}
    for upd in data.get("result", []):
        for key in ("message", "channel_post", "edited_channel_post", "my_chat_member"):
            node = upd.get(key)
            if not node:
                continue
            chat = node.get("chat") or {}
            cid = chat.get("id")
            if cid is None or cid in seen:
                continue
            seen[cid] = {
                "id": cid,
                "type": chat.get("type"),
                "title": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
                "username": chat.get("username"),
            }
    out["chats"] = list(seen.values())
    return out


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


def notify_admin(text: str, cfg: Optional[Config] = None) -> Dict[str, Any]:
    """담당자에게 짧은 알림을 보낸다 (초안 준비됨 등)."""
    cfg = cfg or load()
    token, chat_id = _creds(cfg, admin=True)
    result = _call(
        token, "sendMessage",
        chat_id=chat_id, text=text, disable_web_page_preview="true",
    )
    return {"ok": True, "message_id": result.get("message_id")}
