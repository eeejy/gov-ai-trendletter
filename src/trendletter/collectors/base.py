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
