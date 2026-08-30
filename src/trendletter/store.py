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


def save_raw(articles: List[Article], stamp: Optional[str] = None,
             partial: bool = False) -> Path:
    """수집 결과를 남긴다.

    --source 로 일부만 수집한 결과는 partial-* 로 따로 둔다. collect-* 만
    '마지막 수집본' 으로 잡히므로, 시험 삼아 몇 곳만 돌려 본 것이 다음 초안의
    자료로 둔갑하지 않는다.
    """
    cfg = load()
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
    prefix = "partial" if partial else "collect"
    return _dump(cfg.path("raw_dir") / ("%s-%s.json" % (prefix, stamp)),
                 [a.to_dict() for a in articles])


def load_raw(path: Path) -> List[Article]:
    return [Article.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def latest_raw() -> Optional[Path]:
    """마지막 '전체' 수집본. 일부만 돌린 partial-* 은 일부러 건너뛴다."""
    cfg = load()
    files = sorted(cfg.path("raw_dir").glob("collect-*.json"))
    return files[-1] if files else None


def draft_path(issue: Issue) -> Path:
    return load().path("draft_dir") / ("%s.json" % issue.slug)


def save_draft(issue: Issue) -> Path:
    payload = issue.to_dict()
    for key in ("_clusters", "_all_clusters", "_topics"):   # 직렬화 불가한 임시 참조
        payload.get("meta", {}).pop(key, None)
    path = draft_path(issue)
    _backup(path)
    return _dump(path, payload)


KEEP_BACKUPS = 20


def _backup(path: Path) -> None:
    """덮어쓰기 전에 직전 내용을 남긴다.

    편집기가 자동 저장을 하기 때문에, 잘못 지운 문단을 되찾을 방법이 없으면
    안 된다. 같은 내용이면 새 사본을 만들지 않는다.
    """
    if not path.exists():
        return
    hist = path.parent / "history"
    hist.mkdir(exist_ok=True)
    body = path.read_bytes()
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
    dest = hist / ("%s.%s.json" % (path.stem, stamp))
    if dest.exists():
        return
    prev = sorted(hist.glob(path.stem + ".*.json"))
    if prev and prev[-1].read_bytes() == body:     # 바뀐 게 없으면 그냥 둔다
        return
    dest.write_bytes(body)
    for old_file in prev[: max(0, len(prev) + 1 - KEEP_BACKUPS)]:
        old_file.unlink(missing_ok=True)


def backups(issue: Issue) -> list:
    """되돌릴 수 있는 사본 목록을 새 것부터 돌려준다."""
    hist = draft_path(issue).parent / "history"
    if not hist.exists():
        return []
    out = []
    for f in sorted(hist.glob(draft_path(issue).stem + ".*.json"), reverse=True):
        stamp = f.stem.rsplit(".", 1)[-1]
        try:
            when = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        out.append({"file": f.name, "when": when.strftime("%m/%d %H:%M:%S"),
                    "size": f.stat().st_size})
    return out


def load_backup(issue: Issue, name: str) -> Issue:
    hist = draft_path(issue).parent / "history"
    f = (hist / name).resolve()
    if f.parent != hist.resolve() or not f.exists():   # 경로 탈출 방지
        raise FileNotFoundError(name)
    return Issue.from_dict(json.loads(f.read_text(encoding="utf-8")))


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
