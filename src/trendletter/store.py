"""파일 기반 저장소. 추세 데이터는 향후 SQLite 로 옮긴다."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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


# ── 일간 브리핑 발송 기록 ──────────────────────────────────
# 같은 소식을 이튿날 또 보내지 않기 위해 보낸 것의 지문을 남긴다.
# GitHub Actions 에서 돌 때는 이 파일을 커밋해 상태를 잇는다.
SEEN_KEEP_DAYS = 45


def seen_path() -> Path:
    return load().path("raw_dir").parent / "seen.json"


def load_seen() -> Dict[str, str]:
    f = seen_path()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 기록이 깨져도 발송을 막지 않는다
        return {}


def save_seen(seen: Dict[str, Any]) -> Path:
    """오래된 기록은 버리고 저장한다. 무한히 자라면 안 된다."""
    cut = (datetime.now() - timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y-%m-%d")
    kept = {k: v for k, v in seen.items() if _seen_date(v) >= cut}
    f = seen_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True),
                 encoding="utf-8")
    return f


def _seen_date(value) -> str:
    return value.get("date", "") if isinstance(value, dict) else str(value)


def seen_key(article) -> str:
    """제목에서 기호를 걷어낸 것을 지문으로 쓴다."""
    import re as _re
    return _re.sub(r"[^가-힣a-z0-9]", "", (article.title or "").lower())[:80]


def remember(seen: Dict[str, Any], article, day: str) -> None:
    """보낸 것을 기록한다. 제목도 함께 남겨 다음에 같은 사건을 알아본다."""
    seen[seen_key(article)] = {"date": day, "title": article.title or ""}


def already_sent(seen: Dict[str, Any], article, overlap: int = 2) -> bool:
    """이미 보낸 소식인지 본다.

    지문이 같으면 당연히 같은 것이고, 지문이 달라도 뜻을 지닌 낱말이 겹치면
    같은 사건으로 본다. 실측: 「경찰청 수사자료 분석 솔루션」 을 여덟 매체가
    제각각 제목으로 써서, 지문만으로는 이튿날 또 나갔다.
    """
    from .scoring import _shared, _tokens

    if seen_key(article) in seen:
        return True
    mine = _tokens(article.title or "")
    if not mine:
        return False
    for value in seen.values():
        title = value.get("title", "") if isinstance(value, dict) else ""
        if title and _shared(mine, _tokens(title)) >= overlap:
            return True
    return False
