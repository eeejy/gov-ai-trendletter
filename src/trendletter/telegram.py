"""텔레그램 발송.

동향지 전문 대신 20초짜리 요약을 보내고, 모바일에서 읽을 HTML 주소를 함께 준다.
토큰은 config/secrets.yaml 또는 환경변수(TRENDLETTER_TELEGRAM_TOKEN)에 둔다.
"""

from __future__ import annotations

import json
import re
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
def _clip(text: str, limit: int) -> str:
    """글자 수로 자르되 낱말 가운데를 끊지 않는다."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:          # 너무 많이 잘려 나가면 그냥 글자 수로
        cut = cut[:space]
    return cut.rstrip(" ,·-") + "…"


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
        lines.append("%s %s%s" % (mark, hot, _clip(it.title, 32)))
        # 본문 첫 줄에서 무슨 일인지만 뽑는다. 앞머리 기호(ㅇ - *)는 뗀다
        gist = next((ln.lstrip("ㅇ-*∙· ").strip() for ln in it.body if ln.strip()), "")
        if gist:
            lines.append("   %s" % _clip(gist, 40))
        note = it.note.strip()
        if note:
            lines.append("   → %s" % _clip(note, 38))
    return "\n".join(lines)


MARKS = ["1\u20e3", "2\u20e3", "3\u20e3", "4\u20e3", "5\u20e3",
         "6\u20e3", "7\u20e3", "8\u20e3", "9\u20e3", "\U0001F51F"]

# 텔레그램 한 메시지 상한은 4096자다. 여유를 두고 자른다.
DAILY_LIMIT = 3800


# 본문 앞머리에 자주 붙는 껍데기. 사람이 읽을 내용이 아니다.
_JUNK_PREFIX = re.compile(
    r"^(?:article detail|링크\s*복사|공유|AI\s*요약|기사\s*원문|입력|수정|"
    r"[A-Za-z0-9.-]+\.(?:net|com|kr|co\.kr)|"          # v.daum.net 같은 도메인
    r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?|"           # 2026. 8. 31.
    r"오전|오후|\d{1,2}:\d{2}|[Xx]|[·|\-–—,]|\s)+"
)
# 알맹이가 없는 상투 문구. 이런 본문은 아예 싣지 않는다.
_BOILERPLATE = (
    "자세한 내용은 첨부파일", "첨부파일을 참고", "붙임 참조", "관련 보도자료 내용입니다",
    "아래 첨부파일", "첨부 참조",
)


def _gist(article, limit: int = 150) -> str:
    """기사 앞머리에서 읽을 만한 부분만 뽑는다. 없으면 빈 문자열."""
    text = " ".join((article.summary or "").split())
    if not text:
        return ""
    title = " ".join((article.title or "").split()).strip("\u201c\u201d\"' ")
    # 껍데기와 되풀이된 제목이 번갈아 붙어 있어 두 번 훑는다
    for _ in range(2):
        text = _JUNK_PREFIX.sub("", text)
        head = text.lstrip("\u201c\u201d\"' ")
        if title and head.startswith(title):
            text = head[len(title):].lstrip(" .\u00b7-\u2013\u2014\u201c\u201d\"'")
    if any(k in text for k in _BOILERPLATE):
        return ""
    return _clip(text, limit) if len(text) > 25 else ""


def daily_text(clusters, collected: int, day) -> str:
    """일간 브리핑 문구. 요약을 지어내지 않고 기사 앞머리와 링크만 싣는다.

    사람 검토 없이 자동 발송되므로, 모델이 쓴 문장을 넣지 않는 것이 안전하다.
    판단은 받는 사람이 원문을 보고 한다.
    """
    head = "\U0001F4F0 %d.%d.(%s) AI 동향" % (
        day.month, day.day, "월화수목금토일"[day.weekday()])
    if not clusters:
        return ("%s\n\n어제는 담을 만한 소식이 없었습니다. (수집 %d건)"
                % (head, collected))

    lines = ["%s · %d건 (수집 %d건)" % (head, len(clusters), collected), ""]
    for i, c in enumerate(clusters):
        a = c.lead
        when = a.published.strftime("%-m.%-d") if a.published else ""
        gist = _gist(a)
        lines.append("%s %s" % (MARKS[i] if i < len(MARKS) else "\u25aa\ufe0f",
                                _clip(a.title, 60)))
        # 구글 뉴스로 들어온 건 실제 매체명이 raw.dept 에 있다.
        # '뉴스 검색' 보다 '중앙일보' 가 읽는 사람에게 쓸모 있다.
        where = (a.raw.get("dept") or "").strip() or a.source_name
        lines.append("   %s%s" % (where, " · " + when if when else ""))
        if gist:
            lines.append("   %s" % gist)
        if a.url:
            lines.append("   %s" % a.url)
        lines.append("")
        if sum(len(x) + 1 for x in lines) > DAILY_LIMIT:
            lines.append("(길이 제한으로 나머지는 생략)")
            break
    return "\n".join(lines).rstrip()


def send_text(text: str, cfg: Optional[Config] = None) -> Dict[str, Any]:
    """직원 채널로 짧은 글을 보낸다. 링크 미리보기는 끈다(6건이면 화면이 길어진다)."""
    cfg = cfg or load()
    token, chat_id = _creds(cfg)
    result = _call(token, "sendMessage", chat_id=chat_id, text=text,
                   disable_web_page_preview="true")
    return {"ok": True, "message_id": result.get("message_id")}


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
                # 무슨 일이 있었는지 한 줄을 쓰려면 본문이 있어야 한다.
                # 앞 세 줄이면 사건의 뼈대는 다 들어 있다.
                "body": [ln for ln in it.body[:3]],
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
        if not path.exists():
            out["file_error"] = "첨부할 파일이 없습니다 ([동향지 파일 만들기] 를 먼저 누르세요)"
        else:
            # 파일명에 한글·괄호가 있으면 기기에 따라 내려받기나 열기가 막힌다.
            # 보낼 때만 영문 이름으로 바꾼다. (원본 파일명은 그대로 둔다)
            safe = "AI-trend-%d-%02d.html" % (issue.year, issue.number)
            with path.open("rb") as fp:
                r = requests.post(
                    API % (token, "sendDocument"),
                    data={
                        "chat_id": chat_id,
                        # 안내 문구는 바로 위 메시지에 이미 있다. 여기선 무슨 파일인지만.
                        "caption": "%s 전문" % issue.label,
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
