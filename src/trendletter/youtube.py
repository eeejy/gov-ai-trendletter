"""동향 항목에 붙일 관련 영상 찾기.

API 키 없이 유튜브 검색 결과 페이지의 ytInitialData 를 읽는다.
공식 API 가 아니므로 언제든 구조가 바뀔 수 있다. 실패하면 조용히 빈 목록을 준다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import quote

from .config import load
from .http import Fetcher

# 기간 필터(sp=CAISBAgCEAE%3D)를 붙이면 결과가 0건이 된다(2026-08-30 확인).
# 기본 관련도 정렬이 가장 정확하고, 오래된 영상은 아래 제목 대조에서 걸러진다.
SEARCH = "https://www.youtube.com/results?search_query=%s"

_INITIAL = re.compile(r"ytInitialData\s*=\s*(\{.*?\});</script>", re.S)
_STOP = re.compile(r"[\[\]「」『』‘’“”\"'…·,()]")
# 질의로 쓸 때 방해가 되는 꾸밈말
_DROP_Q = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _blocks(data: Any):
    """중첩 구조를 훑어 videoRenderer 만 뽑는다."""
    if isinstance(data, dict):
        if "videoRenderer" in data:
            yield data["videoRenderer"]
        for v in data.values():
            yield from _blocks(v)
    elif isinstance(data, list):
        for v in data:
            yield from _blocks(v)


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    runs = node.get("runs") or []
    return "".join(r.get("text", "") for r in runs)


# "3일 전", "스트리밍 시간: 4주 전", "1년 전" 같은 상대 시각 표기
_AGE = re.compile(r"(\d+)\s*(분|시간|일|주|개월|년)\s*전")
_AGE_DAYS = {"분": 0, "시간": 0, "일": 1, "주": 7, "개월": 30, "년": 365}


def age_days(published: str) -> int:
    """올린 지 며칠 됐는지. 알 수 없으면 매우 큰 값을 준다."""
    m = _AGE.search(published or "")
    if not m:
        return 9999
    return int(m.group(1)) * _AGE_DAYS.get(m.group(2), 365)


# 동향지 제목에는 늘 나오는 말이라 겹쳐도 관련성을 뜻하지 않는다.
# 이 낱말만 겹친 영상은 십중팔구 다른 주제다.
# (2026-08-30: 'AI·행정안전부' 만 겹친 무관한 정책 영상,
#  'AI·모델·무료' 만 겹친 "24시간 돈 버는 에이전트" 영상이 붙어 있었다)
_COMMON = {
    "ai", "인공지능", "모델", "기술", "사용", "활용", "도입", "개발", "확산",
    "공개", "발표", "추진", "정부", "공공", "지원", "강화", "구축", "운영",
    "서비스", "사업", "정책", "데이터", "시스템", "플랫폼", "전환", "혁신",
    "개최", "시작", "완료", "결과", "방안", "계획", "관련", "위한", "통해",
    "새로운", "국내", "글로벌", "최초", "무료", "가이드", "리뷰", "총정리",
}


def _keywords(title: str) -> set:
    """제목에서 대조에 쓸 낱말. 기호·한 글자·상용어는 버린다."""
    cleaned = _STOP.sub(" ", title or "")
    return {
        w for w in cleaned.split()
        if len(w) > 1 and w.lower() not in _COMMON
    }


def _query_of(title: str) -> str:
    """제목을 검색어로 다듬는다. 괄호·대괄호 안 부연은 검색을 방해한다."""
    q = _DROP_Q.sub(" ", title or "")
    q = _STOP.sub(" ", q)
    return " ".join(q.split())[:80]


def search(
    query: str,
    fetcher: Fetcher,
    limit: int = 2,
    min_overlap: int = 3,
    max_age_days: int = 45,
    strong_overlap: int = 4,
) -> List[Dict[str, str]]:
    """제목으로 검색해 관련성이 확인된 영상만 돌려준다.

    검색 결과 상위라도 주제가 다른 영상이 섞이므로, 제목 낱말이 일정 수 이상
    겹칠 때만 채택한다. 엉뚱한 영상을 붙이는 것보다 안 붙이는 편이 낫다.
    """
    try:
        html = fetcher.get(SEARCH % quote(_query_of(query)))
    except Exception:  # noqa: BLE001
        return []

    m = _INITIAL.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    want = _keywords(query)
    out: List[Dict[str, str]] = []
    seen = set()
    for block in _blocks(data):
        vid = block.get("videoId")
        title = _text(block.get("title"))
        if not vid or not title or vid in seen:
            continue
        overlap = len(want & _keywords(title))
        if overlap < min_overlap:
            continue
        seen.add(vid)
        out.append(
            {
                "_overlap": overlap,
                "id": vid,
                "title": title,
                "channel": _text(block.get("ownerText")) or _text(block.get("longBylineText")),
                "published": _text(block.get("publishedTimeText")),
                "length": _text(block.get("lengthText")),
                "url": "https://www.youtube.com/watch?v=%s" % vid,
                "thumb": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid,
            }
        )
    # 영상은 필수가 아니다. 관련도가 확실하지 않으면 그냥 붙이지 않는다.
    # 억지로 채우면 주제가 다른 영상이 실려 오히려 신뢰를 깎는다.
    #
    # 다만 낱말이 아주 많이 겹치면(strong_overlap 이상) 같은 사업·같은 행사를
    # 다룬 영상일 가능성이 높아 오래돼도 인정한다.
    # (해양환경 지식나눔 특강은 겹침 4개인데 지난 회차라 나이로만 보면 탈락한다)
    picked = [
        v for v in out
        if age_days(v["published"]) <= max_age_days
        or v["_overlap"] >= strong_overlap
    ]
    picked.sort(key=lambda v: (-v["_overlap"], age_days(v["published"])))
    for v in picked:
        v["stale"] = age_days(v["published"]) > max_age_days
        v.pop("_overlap", None)
    return picked[:limit]


def enrich(items, fetcher: Fetcher = None, progress=None) -> None:
    """항목마다 관련 영상을 찾아 붙인다. 실패는 무시한다."""
    cfg = load()
    if not cfg.get("youtube.enabled", True):
        return
    fetcher = fetcher or Fetcher()
    limit = int(cfg.get("youtube.max_per_item", 2))
    overlap = int(cfg.get("youtube.min_overlap", 2))
    max_age = int(cfg.get("youtube.max_age_days", 45))
    strong = int(cfg.get("youtube.strong_overlap", 4))
    say = progress or (lambda m: None)

    for item in items:
        if item.videos:
            continue
        found = search(
            item.title, fetcher, limit=limit, min_overlap=overlap,
            max_age_days=max_age, strong_overlap=strong,
        )
        item.videos = found
        if found:
            say("    ▶ %02d %s" % (item.no, found[0]["title"][:46]))
