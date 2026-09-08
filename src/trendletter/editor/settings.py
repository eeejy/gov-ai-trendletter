"""설정 화면 뒷단.

무엇을 모을지, 무엇을 중요하게 볼지를 **화면에서** 정하고 YAML 로 저장한다.
파일로 남기는 이유는 그래야 다른 사람에게 넘길 수 있기 때문이다.

주석을 지키며 고친다(ruamel). 설정 파일에 적어 둔 근거와 실측값이 화면에서
한 번 저장했다고 사라지면 안 된다.
"""

from __future__ import annotations

import io
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from .. import providers
from ..config import CONFIG_DIR, PROFILES_DIR, load, profiles, reload

bp = Blueprint("setup", __name__)


# --- YAML 읽고 쓰기 ---------------------------------------------------------

def _yaml():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 100
    return y


def _read(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fp:
        return _yaml().load(fp) or {}


def _write(path: Path, data: Any) -> None:
    """고치기 전 사본을 남긴다. 설정을 잘못 만지면 되돌릴 수 있어야 한다."""
    if path.exists():
        hist = path.parent / ".backup"
        hist.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(path, hist / ("%s.%s" % (path.name, stamp)))
        keep = sorted(hist.glob(path.name + ".*"))
        for old in keep[:-20]:
            old.unlink(missing_ok=True)
    buf = io.StringIO()
    _yaml().dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _profile_path(pid: str, name: str) -> Path:
    return PROFILES_DIR / pid / name


def _lines(value: Any) -> List[str]:
    """화면의 여러 줄 입력을 목록으로."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").replace(",", "\n").split("\n")
            if x.strip()]


# --- 현재 상태 --------------------------------------------------------------

@bp.get("/api/setup/state")
def state():
    cfg = load()
    pid = cfg.profile_id
    prof = _read(_profile_path(pid, "profile.yaml"))
    onto = _read(_profile_path(pid, "ontology.yaml"))
    srcs = _read(_profile_path(pid, "sources.yaml")).get("sources") or []

    return jsonify({
        "ok": True,
        "done": bool(cfg.settings.get("setup_done")),
        "profiles": profiles(),
        "profile_id": pid,
        "org": {
            "publisher": cfg.get("issue.publisher", ""),
            "team": cfg.get("issue.team", ""),
            "series": cfg.get("issue.series", ""),
            "name": (prof.get("profile") or {}).get("name", ""),
            "subject": (prof.get("profile") or {}).get("subject", ""),
        },
        "filter": {
            "core": list((prof.get("filter") or {}).get("core") or []),
            "adjacent": list((prof.get("filter") or {}).get("adjacent") or []),
            "min_focus": (prof.get("filter") or {}).get("min_focus", 3.5),
            "require_topic": (prof.get("filter") or {}).get("require_topic", True),
            "adjacent_needs_core": (prof.get("filter") or {}).get(
                "adjacent_needs_core", False),
        },
        "tracks": [dict(t) for t in (prof.get("tracks") or [])],
        "compose": {
            "total_max": cfg.get("compose.total_max", 8),
            "llm_rerank": cfg.get("compose.llm_rerank", True),
            "rerank_pool": cfg.get("compose.rerank_pool", 60),
            "rerank_min_score": cfg.get("compose.rerank_min_score", 8.0),
            "min_tool_news": cfg.get("compose.min_tool_news", 0),
            "drop_same_event": cfg.get("compose.drop_same_event", True),
        },
        "sources": [{
            "id": s.get("id"), "name": s.get("name", ""),
            "collector": s.get("collector"), "track": s.get("track"),
            "role": s.get("role", "primary"), "weight": s.get("weight", 1.0),
            "enabled": bool(s.get("enabled")),
            "params": dict(s.get("params") or {}),
        } for s in srcs],
        "relevance": [{
            "group": k,
            "weight": (v or {}).get("weight", 1.0),
            "label": (v or {}).get("label", k),
            "keywords": list((v or {}).get("keywords") or []),
            "context": list((v or {}).get("context") or []),
        } for k, v in (onto.get("work_relevance") or {}).items()],
        "collectors": sorted(_collector_names()),
        "providers": providers.status_all(),
        "provider": cfg.get("llm.provider", "claude_cli"),
    })


def _collector_names() -> List[str]:
    from ..collectors import REGISTRY
    return list(REGISTRY.keys())


# --- 저장 -------------------------------------------------------------------

@bp.post("/api/setup/org")
def save_org():
    """기관·부서·분야 이름."""
    d = request.get_json(force=True)
    cfg = load()
    st = _read(CONFIG_DIR / "settings.yaml")
    st.setdefault("issue", {})
    for k in ("publisher", "team", "series"):
        if k in d:
            st["issue"][k] = str(d[k]).strip()
    _write(CONFIG_DIR / "settings.yaml", st)

    path = _profile_path(cfg.profile_id, "profile.yaml")
    prof = _read(path)
    prof.setdefault("profile", {})
    for k in ("name", "subject"):
        if k in d:
            prof["profile"][k] = str(d[k]).strip()
    _write(path, prof)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/keywords")
def save_keywords():
    d = request.get_json(force=True)
    path = _profile_path(load().profile_id, "profile.yaml")
    prof = _read(path)
    f = prof.setdefault("filter", {})
    if "core" in d:
        f["core"] = _lines(d["core"])
    if "adjacent" in d:
        f["adjacent"] = _lines(d["adjacent"])
    for k in ("min_focus",):
        if k in d:
            f[k] = float(d[k])
    for k in ("require_topic", "adjacent_needs_core"):
        if k in d:
            f[k] = bool(d[k])
    _write(path, prof)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/tracks")
def save_tracks():
    d = request.get_json(force=True)
    path = _profile_path(load().profile_id, "profile.yaml")
    prof = _read(path)
    rows = []
    for t in (d.get("tracks") or []):
        key = str(t.get("key") or "").strip()
        if not key:
            continue
        row = {"key": key,
               "label": str(t.get("label") or key),
               "field_label": str(t.get("field_label") or key),
               "color": str(t.get("color") or "#38E1FF"),
               "quota": [int(t.get("min") or 0), int(t.get("max") or 0)]}
        if t.get("require_cross_platform"):
            row["require_cross_platform"] = int(t["require_cross_platform"])
        rows.append(row)
    if rows:
        prof["tracks"] = rows
        _write(path, prof)
        reload()
    return jsonify({"ok": True, "tracks": len(rows)})


@bp.post("/api/setup/compose")
def save_compose():
    """선별 방식 — 몇 건을 뽑을지, 모델에게 다시 고르게 할지."""
    d = request.get_json(force=True)
    st = _read(CONFIG_DIR / "settings.yaml")
    c = st.setdefault("compose", {})
    for k in ("total_max", "rerank_pool", "min_tool_news"):
        if k in d:
            c[k] = int(d[k])
    for k in ("rerank_min_score",):
        if k in d:
            c[k] = float(d[k])
    for k in ("llm_rerank", "drop_same_event"):
        if k in d:
            c[k] = bool(d[k])
    _write(CONFIG_DIR / "settings.yaml", st)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/sources")
def save_sources():
    d = request.get_json(force=True)
    path = _profile_path(load().profile_id, "sources.yaml")
    doc = _read(path)
    rows = []
    for s in (d.get("sources") or []):
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        row = {"id": sid, "name": str(s.get("name") or sid),
               "track": str(s.get("track") or "policy"),
               "role": str(s.get("role") or "primary"),
               "collector": str(s.get("collector") or "rss"),
               "enabled": bool(s.get("enabled")),
               "weight": float(s.get("weight") or 1.0),
               "params": s.get("params") or {}}
        if s.get("platform"):
            row["platform"] = str(s["platform"])
        rows.append(row)
    doc["sources"] = rows
    _write(path, doc)
    reload()
    return jsonify({"ok": True, "sources": len(rows)})


@bp.post("/api/setup/relevance")
def save_relevance():
    """관련도 — 무엇을 얼마나 중요하게 볼지."""
    d = request.get_json(force=True)
    path = _profile_path(load().profile_id, "ontology.yaml")
    onto = _read(path)
    wr = onto.setdefault("work_relevance", {})
    wr.clear()
    for g in (d.get("groups") or []):
        name = str(g.get("group") or "").strip()
        if not name:
            continue
        row = {"weight": float(g.get("weight") or 1.0),
               "keywords": _lines(g.get("keywords"))}
        if g.get("label"):
            row["label"] = str(g["label"])
        ctx = _lines(g.get("context"))
        if ctx:
            row["context"] = ctx
        wr[name] = row
    _write(path, onto)
    reload()
    return jsonify({"ok": True, "groups": len(wr)})


@bp.post("/api/setup/model")
def save_model():
    d = request.get_json(force=True)
    st = _read(CONFIG_DIR / "settings.yaml")
    llm = st.setdefault("llm", {})
    name = str(d.get("provider") or "claude_cli")
    llm["provider"] = name
    if d.get("model") is not None:
        llm.setdefault(name, {})
        llm[name]["model"] = str(d["model"])
    _write(CONFIG_DIR / "settings.yaml", st)
    if d.get("api_key"):
        sec = _read(CONFIG_DIR / "secrets.yaml")
        key_of = {"anthropic": "anthropic", "openai": "openai", "gemini": "gemini"}
        if name in key_of:
            sec.setdefault(key_of[name], {})
            sec[key_of[name]]["api_key"] = str(d["api_key"]).strip()
            _write(CONFIG_DIR / "secrets.yaml", sec)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/profile/use")
def use_profile():
    d = request.get_json(force=True)
    pid = str(d.get("id") or "").strip()
    if pid not in profiles():
        return jsonify({"ok": False, "error": "그런 분야가 없습니다"}), 400
    st = _read(CONFIG_DIR / "settings.yaml")
    st["profile"] = pid
    _write(CONFIG_DIR / "settings.yaml", st)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/profile/new")
def new_profile():
    """지금 분야를 복사해 새 분야를 만든다. 맨바닥에서 시작하지 않게."""
    d = request.get_json(force=True)
    pid = str(d.get("id") or "").strip()
    if not pid or not pid.replace("-", "").replace("_", "").isalnum():
        return jsonify({"ok": False,
                        "error": "영문·숫자·하이픈으로 된 이름을 적어 주세요"}), 400
    dest = PROFILES_DIR / pid
    if dest.exists():
        return jsonify({"ok": False, "error": "같은 이름의 분야가 이미 있습니다"}), 400
    src = PROFILES_DIR / (str(d.get("from") or load().profile_id))
    shutil.copytree(src, dest)
    prof = _read(dest / "profile.yaml")
    prof.setdefault("profile", {})["id"] = pid
    if d.get("name"):
        prof["profile"]["name"] = str(d["name"])
    _write(dest / "profile.yaml", prof)
    return jsonify({"ok": True, "id": pid})


@bp.post("/api/setup/done")
def mark_done():
    st = _read(CONFIG_DIR / "settings.yaml")
    st["setup_done"] = True
    _write(CONFIG_DIR / "settings.yaml", st)
    reload()
    return jsonify({"ok": True})


# --- 시험 -------------------------------------------------------------------

@bp.post("/api/setup/test/model")
def test_model():
    d = request.get_json(silent=True) or {}
    name = d.get("provider") or load().get("llm.provider")
    try:
        p = providers.get(name)
        if not p.available():
            return jsonify({"ok": False, "error": p.why_not()})
        return jsonify({"ok": True, "reply": p.check(),
                        "models": p.models()})
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:200]})


@bp.post("/api/setup/test/source")
def test_source():
    """수집원 하나를 실제로 불러 몇 건 오는지 본다."""
    from datetime import timedelta
    from ..collectors import build
    from ..http import Fetcher

    s = request.get_json(force=True)
    try:
        col = build(s, Fetcher(use_cache=False))
        got = col.collect(datetime.now() - timedelta(days=14), 30)
        return jsonify({"ok": True, "count": len(got),
                        "sample": [a.title[:70] for a in got[:3]]})
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:200]})


@bp.post("/api/setup/test/keywords")
def test_keywords():
    """지금 키워드로 지난 수집본에서 몇 건이 걸리는지 미리 본다."""
    from .. import store
    from ..scoring import topic_focus
    from ..dedupe import cluster as make_clusters

    raw = store.latest_raw()
    if not raw:
        return jsonify({"ok": False, "error": "지난 수집본이 없습니다. 먼저 한 번 수집하세요."})
    cfg = load()
    arts = store.load_raw(raw)
    groups = make_clusters(arts, float(cfg.get("dedupe.title_similarity", 0.72)))
    floor = float(cfg.get("filter.min_focus", 3.5))
    passed = [g for g in groups if topic_focus(g, cfg) >= floor]
    return jsonify({"ok": True, "total": len(groups), "passed": len(passed),
                    "sample": [g.lead.title[:60] for g in passed[:5]],
                    "raw": raw.name})
