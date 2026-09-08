"""수집기 공통 규약."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..http import Fetcher
from ..models import Article


class Collector:
    """수집기 1개 = 수집원 1개. collect() 만 구현하면 된다."""

    name = "base"

    def __init__(self, source: Dict[str, Any], fetcher: Fetcher) -> None:
        self.source = source
        self.params = source.get("params") or {}
        self.fetcher = fetcher

    # 하위 클래스가 구현
    def collect(self, since: datetime, limit: int) -> List[Article]:
        raise NotImplementedError

    # --- 도우미 ---------------------------------------------------------
    def fetch_summary(self, article: Article) -> str:
        """목록만으로 본문이 안 오는 수집원이 뒤늦게 요약을 가져오는 자리.

        수집 때 본문까지 다 받으면 느리다. 그래서 초안에 뽑힌 항목만 나중에
        채운다. 예전엔 이 일을 pipeline.py 가 특정 수집원의 주소와 id 를 직접
        알고 처리했다 — 수집원 하나가 바뀌면 파이프라인을 고쳐야 했다.
        """
        return ""

    def keep(self, text: str) -> bool:
        """수집기 단계에서 이 글을 받을지.

        예전엔 수집기 코드 안에 AI 낱말이 박혀 있었다. 그러면 분야를 국제협력으로
        바꿔도 해커뉴스는 계속 AI 글만 가져온다 — 설정을 아무리 고쳐도.
        이제 수집원의 params.include_pattern 이 정하고, 그것도 없으면 분야의
        핵심어를 쓴다. 둘 다 없으면 거르지 않는다.
        """
        rx = self._include_rx()
        return True if rx is None else bool(rx.search(text or ""))

    def _include_rx(self):
        if not hasattr(self, "_inc_rx"):
            pat = self.params.get("include_pattern")
            if not pat:
                from ..config import load
                cfg = load()
                parts = [str(x) for x in (cfg.prof("filter.core") or [])]
                en = cfg.prof("filter.core_en")
                if en:
                    parts.append(re.sub(r"\s*\n\s*", "", str(en)))
                pat = "|".join(p for p in parts if p)
            flat = re.sub(r"\s*\n\s*", "", str(pat or "")).strip()
            try:
                self._inc_rx = re.compile(flat, re.I) if flat else None
            except re.error:
                self._inc_rx = None
        return self._inc_rx

    def make(self, title: str, url: str, **kw) -> Article:
        return Article(
            source_id=self.source["id"],
            source_name=self.source["name"],
            track=self.source.get("track", "industry"),
            title=clean(title),
            url=url,
            **kw
        )


_WS = re.compile(r"\s+")


def clean(text: str) -> str:
    if not text:
        return ""
    text = text.replace("​", "").replace("&nbsp;", " ")
    return _WS.sub(" ", text).strip()


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})"), (1, 2, 3)),
    (re.compile(r"(\d{2})[-./](\d{1,2})[-./](\d{1,2})"), (1, 2, 3)),
]


def parse_date(text: str) -> Optional[datetime]:
    """한국 사이트에서 흔한 날짜 표기를 폭넓게 받아 datetime 으로 만든다."""
    if not text:
        return None
    text = text.strip()

    # 2026.08.28 PM 04:23 / 2026-08-29 21:16:38
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:\s+(AM|PM)?\s*(\d{1,2}):(\d{2}))?", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(5) or 0)
        mi = int(m.group(6) or 0)
        if m.group(4) == "PM" and hh < 12:
            hh += 12
        if m.group(4) == "AM" and hh == 12:
            hh = 0
        try:
            return datetime(y, mo, d, hh, mi)
        except ValueError:
            return None

    # RFC822: Sat, 29 Aug 2026 16:42:49 +0900
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt.replace(tzinfo=None) if dt else None
    except Exception:  # noqa: BLE001
        pass

    for pat, idx in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            y = int(m.group(idx[0]))
            if y < 100:
                y += 2000
            try:
                return datetime(y, int(m.group(idx[1])), int(m.group(idx[2])))
            except ValueError:
                return None
    return None


def within(dt: Optional[datetime], since: datetime, slack_days: int = 0) -> bool:
    """날짜를 못 읽은 항목은 버리지 않고 통과시킨다(사람이 편집기에서 판단)."""
    if dt is None:
        return True
    return dt >= since - timedelta(days=slack_days)
