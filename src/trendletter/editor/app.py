"""로컬 웹 편집기.

127.0.0.1 에만 바인딩한다. 기관망에 노출할 목적이 아니다.

기능
  - 수집 기간·주제 설정 후 초안 생성
  - 항목 선택/제외/순서 변경
  - 제목·본문·시사점 직접 수정, 수정한 항목 잠금
  - 후보 목록에서 항목 추가
  - HTML 미리보기
  - 자동 저장 · 되돌리기, 배포용 HTML 파일 만들기
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, send_file

from .. import llm, pipeline, store
from ..config import load
from ..models import FIELD_LABELS, Article, Cluster, Issue, Item
from ..render import write

# ── 진행 상태 ──────────────────────────────────────────────
# 동향지 만들기는 3~5분 걸린다. 한 번의 긴 요청으로 처리하면 화면이 멈춘 것처럼
# 보이므로, 백그라운드로 돌리고 진행 상황을 따로 물어볼 수 있게 한다.
JOB = {
    "running": False,
    "phase": "",
    "detail": "",
    "done": 0,
    "total": 0,
    "steps": [],
    "started": 0.0,
    "error": None,
    "result": None,
}
JOB_LOCK = threading.Lock()

# 단계별 대략적인 비중 (진행률 막대에 쓴다)
PHASES = [
    ("collect", "자료 수집", 0.34),
    ("cluster", "중복 통합·본문 확보·선별", 0.18),
    ("topic", "핫이슈 종합", 0.10),
    ("draft", "Claude 초안 작성", 0.38),
]


def _job_set(**kw) -> None:
    with JOB_LOCK:
        JOB.update(kw)


def _job_log(line: str) -> None:
    with JOB_LOCK:
        JOB["steps"].append(line)
        JOB["steps"] = JOB["steps"][-40:]
        JOB["detail"] = line.strip()


def _job_ratio() -> float:
    """지금까지의 진행률(0~1)."""
    with JOB_LOCK:
        phase, done, total = JOB["phase"], JOB["done"], JOB["total"]
    base = 0.0
    for key, _name, weight in PHASES:
        if key == phase:
            inner = (done / total) if total else 0.0
            return min(base + weight * inner, 0.99)
        base += weight
    return 1.0 if phase == "finished" else base


app = Flask(__name__, template_folder="templates")
app.config["JSON_AS_ASCII"] = False
# 편집기 화면을 손볼 때 서버를 다시 띄우지 않아도 되도록 템플릿 캐시를 끈다.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


def _current() -> Issue:
    path = store.latest_draft()
    if not path:
        today = date.today()
        cfg = load()
        year = int(cfg.get("issue.year", today.year))
        days = int(cfg.get("collect.days", 7))
        return Issue(
            year=year,
            number=store.next_issue_number(year),
            published_on=today,
            period_from=today - timedelta(days=days),
            period_to=today,
        )
    return store.load_draft(path)


# 설정 파일의 영문 분류를 화면에서는 우리말로 보여준다.
# policy/primary 같은 배지는 처음 보는 담당자에게 아무 뜻도 전하지 못한다.
TRACK_KO = {"policy": "정책·공공", "industry": "산업", "dev": "개발자"}
ROLE_KO = {"primary": "일반", "must": "우리 기관", "verify": "교차 확인"}


@app.get("/")
def index():
    cfg = load()
    return render_template(
        "editor.html",
        sources=cfg.sources,
        settings=cfg.settings,
        field_labels=list(dict.fromkeys(FIELD_LABELS.values())),
        track_ko=lambda k: TRACK_KO.get(k, k),
        role_ko=lambda k: ROLE_KO.get(k, k),
    )


@app.get("/api/draft")
def api_draft():
    return jsonify(_current().to_dict())


def _as_date(value, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


@app.get("/api/progress")
def api_progress():
    """지금 어디까지 진행됐는지. 화면이 1초마다 물어본다."""
    with JOB_LOCK:
        snap = dict(JOB)
    snap["elapsed"] = round(time.time() - snap["started"], 1) if snap["started"] else 0
    snap["ratio"] = round(_job_ratio(), 3)
    snap["phase_name"] = next(
        (n for k, n, _w in PHASES if k == snap["phase"]),
        "완료" if snap["phase"] == "finished" else "",
    )
    snap.pop("result", None)      # 결과는 /api/draft 로 받는다
    return jsonify(snap)


@app.post("/api/collect")
def api_collect():
    """수집 + 초안 생성을 백그라운드로 시작한다.

    3~5분 걸리므로 한 번의 요청으로 처리하면 화면이 멈춘 것처럼 보인다.
    바로 돌려주고, 진행 상황은 /api/progress 로 알린다.
    """
    with JOB_LOCK:
        if JOB["running"]:
            return jsonify({"ok": False, "error": "이미 진행 중입니다"}), 409
    body: Dict[str, Any] = request.get_json(force=True) or {}
    _job_set(running=True, phase="collect", detail="시작하는 중…", done=0, total=0,
             steps=[], started=time.time(), error=None, result=None)
    threading.Thread(target=_run_collect, args=(body,), daemon=True).start()
    return jsonify({"ok": True, "started": True})


def _run_collect(body: Dict[str, Any]) -> None:
    try:
        issue = _collect_job(body)
        _job_set(running=False, phase="finished", detail="완료", result=issue.to_dict())
    except Exception as exc:  # noqa: BLE001
        _job_set(running=False, phase="", error=str(exc)[:200], detail="실패")


def _collect_job(body: Dict[str, Any]):
    cfg = load()
    today = date.today()
    days = int(cfg.get("collect.days", 7))

    period_to = _as_date(body.get("period_to"), today)
    period_from = _as_date(body.get("period_from"), period_to - timedelta(days=days))
    if period_from > period_to:
        period_from, period_to = period_to, period_from
    published_on = _as_date(body.get("published_on"), today)

    only = body.get("sources") or None
    _job_set(phase="collect", total=len(cfg.enabled_sources(only)), done=0)

    def on_source(line: str) -> None:
        _job_log(line)
        if line.strip().startswith(("·", "!")):
            with JOB_LOCK:
                JOB["done"] += 1

    articles = pipeline.collect(
        cfg,
        only=only,
        use_cache=not body.get("fresh"),
        since=datetime.combine(period_from, datetime.min.time()),
        progress=on_source,
    )
    store.save_raw(articles)
    _job_set(phase="cluster", detail="%d건을 중복 통합하는 중…" % len(articles),
             done=0, total=1)

    def on_rank(msg: str) -> None:
        _job_set(phase="cluster", detail=msg.strip(" ·!"))

    issue = pipeline.make_draft(
        articles,
        cfg,
        progress=on_rank,
        theme=body.get("theme", ""),
        period_from=period_from,
        period_to=period_to,
        published_on=published_on,
        number=body.get("number"),
    )
    issue.meta["sources_used"] = len({a.source_id for a in articles})
    clusters = issue.meta.pop("_clusters", {})
    issue.meta.pop("_all_clusters", None)
    topics = issue.meta.pop("_topics", [])

    if body.get("use_llm", True):
        _job_set(phase="topic", detail="이번 주 핫이슈를 조사·정리하는 중…",
                 done=0, total=max(len(topics), 1))
        pipeline.add_trend_items(issue, topics, cfg, progress=_job_log)
        _job_set(done=max(len(topics), 1))

        todo = [it for it in issue.items if not it.locked]
        _job_set(phase="draft", detail="Claude가 본문과 시사점을 쓰는 중…",
                 done=0, total=max(len(todo), 1))

        def on_item(line: str) -> None:
            _job_log(line)
            if "✓" in line:
                with JOB_LOCK:
                    JOB["done"] = min(JOB["done"] + 1, JOB["total"])

        pipeline.polish(issue, clusters, cfg, progress=on_item)

    store.save_draft(issue)
    return issue


@app.post("/api/rewrite")
def api_rewrite():
    """선택한 항목만 Claude 로 다시 작성한다.

    잠긴 항목은 polish() 가 건너뛰므로, 이 경로에서는 잠금을 잠시 풀고 부른다.
    """
    body = request.get_json(force=True) or {}
    issue = Issue.from_dict(body.get("issue") or {})
    numbers = body.get("numbers") or []
    if not numbers:
        return jsonify({"ok": False, "error": "다시 작성할 항목을 고르세요"}), 400
    if not llm.available():
        return jsonify({"ok": False, "error": "claude 명령을 찾을 수 없습니다"}), 400

    was_locked = {}
    for it in issue.items:
        if it.no in numbers:
            was_locked[it.no] = it.locked
            it.locked = False
    pipeline.polish(issue, {}, load(), only=numbers)
    for it in issue.items:
        if it.no in was_locked:
            it.locked = False      # 새로 작성했으므로 잠금을 해제한 상태로 둔다
    return jsonify(issue.to_dict())


@app.get("/api/defaults")
def api_defaults():
    """편집기가 처음 열릴 때 채워 넣을 발행일·호수·수집기간."""
    cfg = load()
    today = date.today()
    days = int(cfg.get("collect.days", 7))
    year = int(cfg.get("issue.year", today.year))
    return jsonify(
        {
            "published_on": today.isoformat(),
            "period_from": (today - timedelta(days=days)).isoformat(),
            "period_to": today.isoformat(),
            "number": store.next_issue_number(year),
            "year": year,
            "cache_hours": cfg.get("collect.cache_hours", 6),
            "llm": llm.available() and bool(cfg.get("llm.enabled", True)),
        }
    )


@app.post("/api/save")
def api_save():
    """편집 내용을 초안으로 저장한다. 발행하지 않는다."""
    issue = Issue.from_dict(request.get_json(force=True))
    issue.status = "draft"
    for i, it in enumerate(issue.items, 1):
        it.no = i
    path = store.save_draft(issue)
    return jsonify({"ok": True, "path": str(path)})


@app.get("/api/health")
def api_health():
    """마지막 수집에서 손봐야 할 게 있으면 알려준다."""
    return jsonify({
        "warnings": pipeline.health_warnings(pipeline.LAST_HEALTH)
                    if pipeline.LAST_HEALTH else [],
        "sources": pipeline.LAST_HEALTH,
    })


@app.get("/api/backups")
def api_backups():
    """자동 저장 직전 사본 목록. 새 것부터."""
    return jsonify({"items": store.backups(_current())})


@app.post("/api/backups/restore")
def api_restore():
    """사본 하나로 되돌린다. 되돌리기 직전 상태도 함께 남긴다."""
    name = (request.get_json(force=True) or {}).get("file", "")
    cur = _current()
    try:
        restored = store.load_backup(cur, name)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "사본을 찾을 수 없습니다"}), 404
    restored.status = "draft"
    store.save_draft(restored)         # 지금 상태는 _backup 이 알아서 남긴다
    return jsonify({"ok": True, "issue": restored.to_dict()})


@app.post("/api/preview")
def api_preview():
    issue = Issue.from_dict(request.get_json(force=True))
    for i, it in enumerate(issue.items, 1):
        it.no = i
    path = write(issue, preview=True)
    return jsonify({"ok": True, "path": str(path), "url": "/preview"})


@app.get("/preview")
def preview_file():
    issue = _current()
    path = load().path("html_dir") / (issue.slug + ".preview.html")
    if not path.exists():
        return "미리보기를 먼저 생성하세요.", 404
    return send_file(str(path))


@app.post("/api/publish")
def api_publish():
    issue = Issue.from_dict(request.get_json(force=True))
    for i, it in enumerate(issue.items, 1):
        it.no = i
    blanks = [it.no for it in issue.items if not it.note.strip()]  # 선택된 종류 기준
    if blanks and not request.args.get("force"):
        return jsonify({"ok": False, "error": "시사점이 비어 있는 항목: %s" % blanks}), 400
    issue.status = "published"
    store.save_draft(issue)
    path = write(issue)
    return jsonify({"ok": True, "path": str(path)})


@app.post("/api/reveal")
def api_reveal():
    """만들어 둔 동향지 파일이 있는 폴더를 탐색기/파인더로 연다.

    편집기는 담당자 PC 에서만 도는지라 파일은 이미 그 PC 에 있다.
    따로 내려받으면 사본만 하나 더 생기므로, 있는 자리를 열어 준다.
    """
    import subprocess
    import sys as _sys

    issue = _current()
    path = load().path("html_dir") / (issue.slug + ".html")
    if not path.exists():
        return jsonify({"ok": False, "error": "아직 만들어진 파일이 없습니다"}), 404
    try:
        if _sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        elif _sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except Exception as exc:  # noqa: BLE001 - 폴더를 못 열어도 파일은 거기 있다
        return jsonify({"ok": False, "error": str(exc)[:120], "path": str(path)}), 200
    return jsonify({"ok": True, "path": str(path)})


@app.post("/api/telegram/preview")
def api_telegram_preview():
    """보낼 문구를 미리 만들어 본다. 실제로 보내지 않는다."""
    from .. import telegram as tg

    issue = Issue.from_dict(request.get_json(force=True))
    cfg = load()
    base = (cfg.get("telegram.html_base") or "").strip()
    url = (base.rstrip("/") + "/" + issue.slug + ".html") if base else ""
    html_path = cfg.path("html_dir") / (issue.slug + ".html")
    attach = html_path if (not url and html_path.exists()) else None
    try:
        r = tg.send(issue, url, cfg, dry_run=True, html_file=attach)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:200]}), 400
    return jsonify({
        "ok": True, "text": r["text"], "length": r["length"], "url": url,
        "attach": html_path.name if attach else "",
        "need_publish": not url and not html_path.exists(),
    })


@app.post("/api/telegram/send")
def api_telegram_send():
    """확인한 문구를 실제로 보낸다."""
    from .. import telegram as tg

    body = request.get_json(force=True) or {}
    issue = Issue.from_dict(body.get("issue") or {})
    cfg = load()
    base = (cfg.get("telegram.html_base") or "").strip()
    url = (base.rstrip("/") + "/" + issue.slug + ".html") if base else ""
    # 전문 주소가 없으면 발행본 HTML 을 첨부한다.
    # 이걸 빠뜨리면 요약만 가고 전문을 볼 방법이 없다.
    html_path = cfg.path("html_dir") / (issue.slug + ".html")
    attach = html_path if (not url and html_path.exists()) else None
    if not url and attach is None:
        return jsonify({
            "ok": False,
            "error": "첨부할 파일이 없습니다. 먼저 [동향지 파일 만들기] 를 누르세요.",
        }), 400
    try:
        # 사람이 화면에서 고친 문구가 있으면 그대로 보낸다
        r = tg.send(issue, url, cfg, text=body.get("text"), html_file=attach)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:200]}), 400
    return jsonify(r)


@app.get("/api/candidates")
def api_candidates():
    """초안에 뽑히지 않은 후보. 편집기에서 직접 추가할 때 쓴다."""
    issue = _current()
    used = {k for it in issue.items for k in it.origin_keys}
    out = []
    for idx, c in enumerate(issue.meta.get("candidates", [])):
        keys = {a.get("key") for a in c.get("articles", [])}
        if keys & used:
            continue
        lead = c["articles"][0]
        out.append(
            {
                "index": idx,
                "title": lead["title"],
                "source": lead["source_name"],
                "track": lead["track"],
                "published": lead.get("published"),
                "url": lead["url"],
                "score": c.get("score"),
                "outlets": c.get("outlets", []),
                "onto": c.get("onto", {}),
            }
        )
    return jsonify(out)


@app.post("/api/candidates/<int:index>/add")
def api_add_candidate(index: int):
    issue = _current()
    cands = issue.meta.get("candidates", [])
    if index >= len(cands):
        return jsonify({"ok": False, "error": "없는 후보"}), 404
    raw = cands[index]
    cl = Cluster(articles=[Article.from_dict(a) for a in raw["articles"]])
    cl.score = raw.get("score", 0.0)
    cl.onto = raw.get("onto", {})
    item = pipeline.to_item(cl, len(issue.items) + 1)

    # to_item 은 수집 요약을 기계적으로 자른 뼈대일 뿐이다. 초안 생성 때 뽑힌
    # 항목은 뒤이어 polish() 가 Claude 로 다시 쓰는데, 나중에 손으로 추가한
    # 후보는 그 과정을 거치지 않아 혼자만 문체가 달랐다. 여기서 같이 맞춘다.
    if llm.available() and not request.args.get("skip_llm"):
        try:
            drafted = llm.draft_item(llm.cluster_payload(cl), slot=item.no - 1)
        except Exception as exc:  # noqa: BLE001 - 실패해도 뼈대는 돌려준다
            return jsonify(dict(item.to_dict(), llm_error=str(exc)[:160]))
        for key in ("field_label", "audience", "impact", "title", "source_label"):
            if drafted.get(key):
                setattr(item, key, drafted[key])
        if drafted.get("body"):
            item.body = drafted["body"]
        notes = drafted.get("notes") or {}
        for kind in ("시사점", "향후계획"):     # note 는 읽기 전용 프로퍼티다
            if notes.get(kind):
                item.notes[kind] = notes[kind]
    return jsonify(item.to_dict())


@app.post("/api/item/blank")
def api_blank_item():
    """우리청 내부 소식처럼 수집되지 않는 항목을 직접 입력할 때 쓰는 빈 틀."""
    item = Item(
        no=0,
        field_label="직접 개발형",
        audience="전 직원",
        impact="중간",
        title="",
        source_label="해양경찰청 · %s" % date.today().strftime("%y. %-m. %-d."),
        body=["ㅇ "],
        note_kind="시사점",
        track="policy",
    )
    return jsonify(item.to_dict())


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    url = "http://%s:%d/" % (host, port)
    print("로컬 편집기: %s  (Ctrl+C 로 종료)" % url)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    app.run(host=host, port=port, debug=False)
