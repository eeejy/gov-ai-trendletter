"""파일 기반 저장소. 추세 데이터는 향후 SQLite 로 옮긴다."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load
from .models import Article, Issue


def _dump(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_raw(articles: List[Article], stamp: Optional[str] = None) -> Path:
    cfg = load()
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
    return _dump(cfg.path("raw_dir") / ("collect-%s.json" % stamp), [a.to_dict() for a in articles])


def load_raw(path: Path) -> List[Article]:
    return [Article.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def latest_raw() -> Optional[Path]:
    cfg = load()
    files = sorted(cfg.path("raw_dir").glob("collect-*.json"))
    return files[-1] if files else None


def draft_path(issue: Issue) -> Path:
    return load().path("draft_dir") / ("%s.json" % issue.slug)


def save_draft(issue: Issue) -> Path:
    payload = issue.to_dict()
    for key in ("_clusters", "_all_clusters", "_topics"):   # 직렬화 불가한 임시 참조
        payload.get("meta", {}).pop(key, None)
    return _dump(draft_path(issue), payload)


def load_draft(path: Path) -> Issue:
    return Issue.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_draft() -> Optional[Path]:
    files = sorted(load().path("draft_dir").glob("*.json"))
    return files[-1] if files else None


def next_issue_number(year: int) -> int:
    """설정에 번호가 없으면 발행본과 초안 중 가장 큰 번호 + 1 을 쓴다."""
    cfg = load()
    configured = cfg.get("issue.number")
    if configured:
        return int(configured)

    import re

    # 초안은 세지 않는다. 초안을 셀 경우 draft 를 다시 돌릴 때마다 호수가 밀린다.
    best = 0
    pattern = re.compile(r"제%d-(\d+)호" % year)
    from .config import ROOT

    for folder in (cfg.path("html_dir"), ROOT):
        for f in folder.glob("*"):
            if f.suffix.lower() not in (".html", ".pdf", ".hwp", ".hwpx"):
                continue
            if ".preview." in f.name:
                continue
            m = pattern.search(f.name)
            if m:
                best = max(best, int(m.group(1)))
    return best + 1


def list_issues() -> List[Dict[str, Any]]:
    out = []
    for f in sorted(load().path("html_dir").glob("*.html")):
        out.append({"name": f.name, "path": str(f), "mtime": f.stat().st_mtime})
    return out
