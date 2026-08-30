"""Claude Code CLI(`claude -p`) 를 통한 초안 작성.

별도 API 키를 두지 않고 담당자가 이미 쓰는 Claude Team 계정을 그대로 쓴다.
기관망에서 CLI 가 없거나 실패해도 파이프라인은 멈추지 않고 규칙 기반 뼈대를 남긴다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from .config import ROOT, load

PROMPT_DIR = ROOT / "prompts"


class LlmUnavailable(RuntimeError):
    """claude CLI 를 쓸 수 없는 상태."""


def available() -> bool:
    return shutil.which(load().get("llm.command", "claude")) is not None


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise LlmUnavailable("프롬프트 파일이 없습니다: %s" % path)
    return path.read_text(encoding="utf-8")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text: str) -> Dict[str, Any]:
    """모델이 코드펜스나 설명을 붙여도 JSON 만 건져낸다."""
    text = (text or "").strip()
    if not text:
        raise ValueError("빈 응답")

    for candidate in [text] + _FENCE.findall(text):
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 앞뒤에 말이 붙은 경우 가장 바깥 중괄호 구간을 잘라 본다.
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("JSON 을 찾지 못했습니다: %s" % text[:200])


def run(prompt: str, timeout: Optional[int] = None) -> str:
    cfg = load()
    command = cfg.get("llm.command", "claude")
    if shutil.which(command) is None:
        raise LlmUnavailable(
            "%s 명령을 찾을 수 없습니다. Claude Code 를 설치하거나 "
            "config/settings.yaml 의 llm.enabled 를 false 로 두세요." % command
        )

    args = [command, "-p", prompt, "--output-format", "text"]
    model = cfg.get("llm.model")
    if model:
        args += ["--model", model]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout or int(cfg.get("llm.timeout", 180)),
        )
    except subprocess.TimeoutExpired as exc:
        raise LlmUnavailable("모델 응답이 시간 안에 오지 않았습니다") from exc

    if proc.returncode != 0:
        raise LlmUnavailable(
            "claude 실행 실패(코드 %d): %s" % (proc.returncode, (proc.stderr or "")[:300])
        )
    return proc.stdout


_MARKER = re.compile(r"^\s*(ㅇ|○|-|–|\*|※)\s*")


def _normalize_body(lines: List[str]) -> List[str]:
    """줄머리 기호 단위로 정리한다.

    모델이 보기 좋게 줄을 접어 보내면 한 항목 안에 개행이 섞인다.
    그대로 두면 들여쓰기 판정이 깨지므로, 기호로 시작하는 줄에서만 새 항목을
    열고 나머지는 앞 줄에 이어 붙인다.
    """
    out: List[str] = []
    for raw in lines:
        for piece in str(raw).split("\n"):
            text = piece.rstrip()
            if not text.strip():
                continue
            if _MARKER.match(text) or not out:
                # 기호와 본문 사이 간격을 서식에 맞춰 통일한다.
                stripped = text.strip()
                if stripped.startswith(("ㅇ", "○")):
                    out.append("ㅇ " + _MARKER.sub("", stripped))
                elif stripped.startswith(("-", "–")):
                    out.append(" - " + _MARKER.sub("", stripped))
                elif stripped.startswith(("*", "※")):
                    out.append("  * " + _MARKER.sub("", stripped))
                else:
                    out.append("ㅇ " + stripped)
            else:
                out[-1] = out[-1].rstrip() + " " + text.strip()
    return out


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


# 항목마다 따로 호출하므로 서로 무엇을 썼는지 알 수 없다. 그대로 두면 여섯 항목이
# 모두 "우리청도 ~ 사전 ~ 필요" 한 가지 꼴로 나온다. 자리마다 먼저 따져 볼 각도를
# 달리 준다. 자료가 맞지 않으면 무시하라고 함께 일러 둔다.
_ANGLES = [
    "이 소식이 우리 업무 어디에 닿는지 한 곳을 지목한다 (틀 ①)",
    "판이 어느 쪽으로 움직이는지 먼저 짚는다. 우리청을 언급하지 않아도 된다 (틀 ②)",
    "우리청이 이미 하고 있는 일이 자료에 있으면 그것과 나란히 놓는다 (틀 ③)",
    "지켜야 할 선·위험이 걸렸는지 먼저 본다 (틀 ④)",
    "판이 어느 쪽으로 움직이는지 먼저 짚는다. 우리청을 언급하지 않아도 된다 (틀 ②)",
    "이 소식이 우리 업무 어디에 닿는지 한 곳을 지목한다 (틀 ①)",
]


def draft_item(cluster_payload: Dict[str, Any], slot: int = 0) -> Dict[str, Any]:
    """이슈 하나를 받아 항목 초안(본문·시사점·향후계획)을 만든다.

    slot 은 이번 호에서 이 항목이 놓일 자리다. 맺음말이 한 가지 꼴로 쏠리지 않도록
    자리마다 먼저 검토할 각도를 달리 준다.
    """
    angle = _ANGLES[slot % len(_ANGLES)]
    prompt = (
        _load_prompt("draft_item.md")
        .replace("{{VARIETY_HINT}}", angle)
        .replace("{{CLUSTER_JSON}}", json.dumps(cluster_payload, ensure_ascii=False, indent=1))
    )
    data = _extract_json(run(prompt))

    body = data.get("body") or []
    if isinstance(body, str):
        body = [line for line in body.split("\n") if line.strip()]

    notes = data.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    notes = {
        "시사점": _one_line(notes.get("시사점")),
        "향후계획": _one_line(notes.get("향후계획")),
    }

    return {
        "field_label": data.get("field_label"),
        "audience": data.get("audience"),
        "impact": data.get("impact"),
        "title": _one_line(data.get("title")),
        "source_label": _one_line(data.get("source_label")),
        "body": _normalize_body(body),
        "notes": notes,
    }


def synthesize_trend(term: str, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """한 기술·모델에 대한 여러 소식을 모아 동향지 항목 1건으로 정리한다."""
    prompt = (
        _load_prompt("trend_synthesis.md")
        .replace("{{TERM}}", term)
        .replace("{{ARTICLES_JSON}}", json.dumps(articles, ensure_ascii=False, indent=1))
    )
    data = _extract_json(run(prompt))

    body = data.get("body") or []
    if isinstance(body, str):
        body = [x for x in body.split("\n") if x.strip()]
    notes = data.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    return {
        "title": _one_line(data.get("title")),
        "source_label": _one_line(data.get("source_label")),
        "impact": (data.get("impact") or "중간").strip(),
        "audience": (data.get("audience") or "사업기획").strip(),
        "body": _normalize_body(body),
        "notes": {
            "시사점": _one_line(notes.get("시사점")),
            "향후계획": _one_line(notes.get("향후계획")),
        },
    }


def keyword_briefs(payload: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """이번 주 키워드를 직원이 알아들을 수 있게 풀어 쓴다."""
    prompt = _load_prompt("keyword_brief.md").replace(
        "{{KEYWORDS_JSON}}", json.dumps(payload, ensure_ascii=False, indent=1)
    )
    data = _extract_json(run(prompt))
    out = []
    for b in data.get("briefs") or []:
        term = str(b.get("term") or "").strip()
        if not term:
            continue
        out.append(
            {
                "term": term,
                "reading": str(b.get("reading") or "").strip(),
                "what": _one_line(b.get("what")),
                "why": _one_line(b.get("why")),
                "ours": _one_line(b.get("ours")),
                "level": str(b.get("level") or "알아두기").strip(),
            }
        )
    return out


def cluster_payload(cluster) -> Dict[str, Any]:
    """모델에 넘길 최소 정보. 원문 링크와 기관·날짜·요약만 준다."""
    return {
        "outlets": cluster.outlets,
        "ontology": cluster.onto,
        "articles": [
            {
                "source": a.source_name,
                "published": a.published.strftime("%Y-%m-%d") if a.published else "",
                "title": a.title,
                "summary": a.summary[:900],
                "url": a.url,
            }
            for a in cluster.articles[:4]
        ],
    }
