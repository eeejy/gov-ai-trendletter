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
    # 원본과 같은 들여쓰기로 쓴다. 이걸 안 맞추면 한 곳만 고쳐도 파일 전체가
    # 다시 써져 무엇이 바뀌었는지 볼 수 없게 된다.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _read(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fp:
        return _yaml().load(fp) or {}


def _write(path: Path, data: Any) -> None:
    """고치기 전 사본을 남긴다. 설정을 잘못 만지면 되돌릴 수 있어야 한다."""
    buf = io.StringIO()
    _yaml().dump(data, buf)
    text = buf.getvalue()
    # 한 곳을 저장하면 다른 파일도 함께 다시 쓰인다. 내용이 같으면 손대지 않는다 —
    # 안 바뀐 파일이 매번 새로 써지면 무엇을 고쳤는지 알 수 없다.
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    if path.exists():
        hist = path.parent / ".backup"
        hist.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(path, hist / ("%s.%s" % (path.name, stamp)))
        keep = sorted(hist.glob(path.name + ".*"))
        for old in keep[:-20]:
            old.unlink(missing_ok=True)
    path.write_text(text, encoding="utf-8")


def _profile_path(pid: str, name: str) -> Path:
    return PROFILES_DIR / pid / name


def _lines(value: Any) -> List[str]:
    """화면의 여러 줄 입력을 목록으로."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").replace(",", "\n").split("\n")
            if x.strip()]


def _merge_into(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """src 의 값을 dst 안에 제자리로 넣는다.

    통째로 바꿔 끼우면 그 자리에 달려 있던 주석이 함께 사라진다. 값이 그대로면
    아예 손대지 않는다 — 안 바뀐 줄은 다시 쓰이지 않아야 한다.
    """
    for key, val in src.items():
        cur = dst.get(key)
        if isinstance(cur, dict) and isinstance(val, dict):
            _merge_into(cur, val)
            for gone in [g for g in cur if g not in val]:
                del cur[gone]
        elif cur != val or key not in dst:
            dst[key] = val


def _merge_list(seq, rows: List[Dict[str, Any]], key: str = "id"):
    """목록을 제자리에서 고친다.

    통째로 갈아치우면 YAML 주석이 전부 날아간다. 수집원마다 "왜 이걸 보는지"
    적어 둔 줄이 한 번 저장에 사라지면 다음 사람은 이유를 알 수 없다.
    그래서 같은 항목은 값만 덮어쓰고, 지워진 것만 빼고, 새것만 덧붙인다.
    """
    old = {}
    for item in seq:
        if isinstance(item, dict) and item.get(key):
            old[str(item[key])] = item

    merged = []
    for row in rows:
        ident = str(row.get(key) or "")
        if not ident:
            continue
        item = old.get(ident)
        if item is None:
            item = dict(row)
        else:
            _merge_into(item, row)    # 주석은 item 에 붙어 있어 살아남는다
        merged.append((ident, item))

    keep = {i for i, _ in merged}
    for idx in range(len(seq) - 1, -1, -1):
        cur = seq[idx]
        if not (isinstance(cur, dict) and str(cur.get(key, "")) in keep):
            del seq[idx]
    have = {str(x.get(key, "")) for x in seq if isinstance(x, dict)}
    for ident, item in merged:
        if ident not in have:
            seq.append(item)
    return seq


def _merge_map(cmap, rows: Dict[str, Dict[str, Any]]):
    """사전을 제자리에서 고친다. 이유는 _merge_list 와 같다."""
    for name in [k for k in cmap if k not in rows]:
        del cmap[name]
    for name, row in rows.items():
        if name in cmap and isinstance(cmap[name], dict):
            _merge_into(cmap[name], row)
            for k in [k for k in cmap[name] if k not in row]:
                del cmap[name][k]
        else:
            cmap[name] = row
    return cmap


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
            "series": (prof.get("profile") or {}).get(
                "series", cfg.get("issue.series", "")),
            "name": (prof.get("profile") or {}).get("name", ""),
            "subject": (prof.get("profile") or {}).get("subject", ""),
        },
        "filter": {
            "core": list((prof.get("filter") or {}).get("core") or []),
            "adjacent": list((prof.get("filter") or {}).get("adjacent") or []),
            "core_en": str((prof.get("filter") or {}).get("core_en") or "").strip(),
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
            "platform": s.get("platform", ""), "enabled": bool(s.get("enabled")),
            "params": dict(s.get("params") or {}),
        } for s in srcs],
        "relevance": [{
            "group": k,
            "weight": (v or {}).get("weight", 1.0),
            "label": (v or {}).get("label", ""),
            "keywords": list((v or {}).get("keywords") or []),
            "context": list((v or {}).get("context") or []),
        } for k, v in (onto.get("work_relevance") or {}).items()],
        "collectors": COLLECTOR_HELP,
        "depts": sorted((_read(CONFIG_DIR / "korea_kr_depts.yaml").get("departments") or {})),
        "providers": providers.status_all(),
        "provider": cfg.get("llm.provider", "claude_cli"),
    })


# 수집기마다 필요한 것이 다르다. 화면이 알맞은 입력칸을 그리도록 알려 준다.
# 여기 없는 수집기는 설정값을 그대로(JSON) 고치게 한다.
COLLECTOR_HELP = [
    {"id": "koreakr_search", "label": "정책브리핑 (부처 보도자료)",
     "why": "부처를 고르고 검색어를 적으면 그 부처가 낸 자료만 봅니다. 가장 정확합니다.",
     "fields": [{"key": "depts", "label": "부처", "type": "depts"},
                {"key": "queries", "label": "검색어", "type": "lines"}]},
    {"id": "google_news", "label": "구글 뉴스 검색",
     "why": "검색어만 있으면 됩니다. 어느 분야에나 쓸 수 있어 처음 시작할 때 좋습니다.",
     "fields": [{"key": "queries", "label": "검색어", "type": "queries"}]},
    {"id": "rss", "label": "RSS 주소",
     "why": "전문지·기관 소식지 주소를 적습니다. 주소가 맞는지 시험으로 확인하세요.",
     "fields": [{"key": "feeds", "label": "주소", "type": "lines"}]},
    {"id": "govboard", "label": "기관 게시판",
     "why": "행정표준 게시판(주소에 selectNttList.do 가 있는 곳)을 통째로 봅니다.",
     "fields": [{"key": "url", "label": "목록 주소", "type": "text"},
                {"key": "pages", "label": "몇 쪽까지", "type": "number"}]},
    {"id": "koreakr_list", "label": "정책브리핑 (전체 목록)",
     "why": "부처를 가리지 않고 정책뉴스 목록을 훑습니다. 양이 많습니다.",
     "fields": [{"key": "pages", "label": "몇 쪽까지", "type": "number"}]},
]


def _collector_names() -> List[str]:
    from ..collectors import REGISTRY
    return list(REGISTRY.keys())


@bp.post("/api/setup/profile/delete")
def delete_profile():
    d = request.get_json(force=True)
    pid = str(d.get("id") or "").strip()
    if pid not in profiles():
        return jsonify({"ok": False, "error": "그런 분야가 없습니다"}), 400
    if len(profiles()) <= 1:
        return jsonify({"ok": False, "error": "마지막 분야는 지울 수 없습니다"}), 400
    shutil.rmtree(PROFILES_DIR / pid)
    if load().profile_id == pid:
        st = _read(CONFIG_DIR / "settings.yaml")
        st["profile"] = profiles()[0]
        _write(CONFIG_DIR / "settings.yaml", st)
    reload()
    return jsonify({"ok": True})


@bp.post("/api/setup/test/collect")
def test_collect():
    """지금 설정 그대로 한 번 돌려 본다. 설정이 맞는지는 돌려 봐야 안다."""
    from datetime import timedelta
    from .. import pipeline, scoring
    cfg = load()
    days = int((request.get_json(silent=True) or {}).get("days", 7))
    try:
        arts = pipeline.collect(cfg, days=days, progress=lambda m: None)
        clusters = pipeline.build_clusters(arts, cfg)
        picked = scoring.select(clusters, cfg)
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:220]})
    from collections import Counter
    per = Counter(a.source_id for a in arts)
    return jsonify({
        "ok": True, "articles": len(arts), "clusters": len(clusters),
        "picked": [{"track": c.lead.track, "score": round(c.score, 1),
                    "title": c.lead.title[:70],
                    "why": scoring.explain(c, cfg)[:3]} for c in picked],
        "sources": [{"id": s["id"], "name": s.get("name", s["id"]),
                     "count": per.get(s["id"], 0)} for s in cfg.enabled_sources()],
    })


# --- 저장 -------------------------------------------------------------------

@bp.post("/api/setup/org")
def save_org():
    """기관·부서·분야 이름."""
    d = request.get_json(force=True)
    cfg = load()
    st = _read(CONFIG_DIR / "settings.yaml")
    st.setdefault("issue", {})
    # 기관·부서는 앱 전역이다. 분야를 바꿔도 발행 기관은 같다.
    for k in ("publisher", "team"):
        if k in d:
            st["issue"][k] = str(d[k]).strip()
    # 동향지 이름은 분야마다 다르다. 전역에 두면 분야를 바꿔도 제목이 안 바뀐다.
    st.get("issue", {}).pop("series", None)
    _write(CONFIG_DIR / "settings.yaml", st)

    path = _profile_path(cfg.profile_id, "profile.yaml")
    prof = _read(path)
    prof.setdefault("profile", {})
    for k in ("name", "subject", "series"):
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
    fresh: Dict[str, Any] = {}
    if "core" in d:
        fresh["core"] = _lines(d["core"])
    if "adjacent" in d:
        fresh["adjacent"] = _lines(d["adjacent"])
    if "core_en" in d:
        # 빈 값이면 아예 지운다. 남겨 두면 안 보이는 정규식이 관문을 열어 준다.
        val = " ".join(str(d["core_en"] or "").split())
        if val:
            fresh["core_en"] = val
        elif "core_en" in f:
            del f["core_en"]
    if "min_focus" in d:
        fresh["min_focus"] = float(d["min_focus"])
    for k in ("require_topic", "adjacent_needs_core"):
        if k in d:
            fresh[k] = bool(d[k])
    _merge_into(f, fresh)
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
        was = next((x for x in (prof.get("tracks") or [])
                    if str(x.get("key")) == key), {})
        need = t.get("require_cross_platform") or was.get("require_cross_platform")
        if need:
            row["require_cross_platform"] = int(need)
        rows.append(row)
    if rows:
        _merge_list(prof.setdefault("tracks", []), rows, key="key")
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
    # 화면은 platform·role 같은 값을 보내지 않는다. 안 보냈다고 지우면
    # 교차검증(같은 소식이 여러 곳에서 나왔는지)이 조용히 죽는다.
    old = {s.get("id"): s for s in (doc.get("sources") or [])}
    rows = []
    for s in (d.get("sources") or []):
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        was = old.get(sid) or {}
        row = {"id": sid, "name": str(s.get("name") or sid),
               "track": str(s.get("track") or was.get("track") or "policy"),
               "role": str(s.get("role") or was.get("role") or "primary"),
               "collector": str(s.get("collector") or was.get("collector") or "rss"),
               "enabled": bool(s.get("enabled")),
               "weight": float(s.get("weight") or was.get("weight") or 1.0),
               "params": s.get("params") or {}}
        plat = s.get("platform") or was.get("platform")
        if plat:
            row["platform"] = str(plat)
        rows.append(row)
    _merge_list(doc.setdefault("sources", []), rows)
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
    fresh: Dict[str, Any] = {}
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
        fresh[name] = row
    _merge_map(wr, fresh)
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
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".backup", "__pycache__"))
    prof = _read(dest / "profile.yaml")
    prof.setdefault("profile", {})["id"] = pid
    if d.get("name"):
        prof["profile"]["name"] = str(d["name"])
    # 앞 분야의 낱말이 그대로 넘어오면 안 되는 것들을 비운다. 남겨 두면
    # 화면에 안 보이는 채로 관문과 병합에 계속 끼어든다.
    prof.get("filter", {}).pop("core_en", None)
    _write(dest / "profile.yaml", prof)
    lex = _read(dest / "lexicon.yaml")
    if lex.get("vendors"):
        lex["vendors"] = []          # 제품 이름은 분야마다 완전히 다르다
        _write(dest / "lexicon.yaml", lex)
    return jsonify({"ok": True, "id": pid,
                    "note": "%s 를 본떠 만들었습니다. 앞 분야의 영문 정규식과 "
                            "제품 이름은 비웠습니다." % src.name})


@bp.post("/api/setup/done")
def mark_done():
    st = _read(CONFIG_DIR / "settings.yaml")
    st["setup_done"] = True
    _write(CONFIG_DIR / "settings.yaml", st)
    reload()
    return jsonify({"ok": True})


# --- 말로 설정하기 ---------------------------------------------------------

@bp.post("/api/setup/suggest")
def suggest():
    """한 문장을 받아 설정을 제안한다. **바로 적용하지 않는다.**

    설정이 소리 없이 바뀌면 왜 그렇게 됐는지 아무도 모른다. 제안을 보여 주고
    사람이 누를 때 적용한다.
    """
    import json as _json
    from .. import llm

    ask = str((request.get_json(force=True) or {}).get("ask") or "").strip()
    if len(ask) < 4:
        return jsonify({"ok": False, "error": "무엇에 대한 동향지인지 한 문장 적어 주세요"})

    cfg = load()
    depts = _read(CONFIG_DIR / "korea_kr_depts.yaml").get("departments") or {}
    prompt = (llm._load_prompt("suggest_profile.md")
              .replace("{{DEPTS}}", ", ".join(sorted(depts)))
              .replace("{{PUBLISHER}}", str(cfg.get("issue.publisher", "")))
              .replace("{{TEAM}}", str(cfg.get("issue.team", "")))
              .replace("{{ASK}}", ask))
    try:
        data = llm._extract_json(llm.run(prompt, timeout=180))
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:220]})

    # 트랙 키가 없는 수집원은 첫 트랙으로 붙인다. 안 그러면 그 수집원이 죽는다.
    keys = [str(t.get("key")) for t in (data.get("tracks") or []) if t.get("key")]
    for s in (data.get("sources") or []):
        if str(s.get("track")) not in keys and keys:
            s["track"] = keys[0]
    return jsonify({"ok": True, "proposal": data})


@bp.post("/api/setup/apply")
def apply_proposal():
    """제안 중 사람이 고른 갈래만 적용한다."""
    d = request.get_json(force=True)
    p = d.get("proposal") or {}
    want = set(d.get("parts") or [])
    done = []

    if "keywords" in want and (p.get("core") or p.get("adjacent")):
        path = _profile_path(load().profile_id, "profile.yaml")
        prof = _read(path)
        f = prof.setdefault("filter", {})
        if p.get("core"):
            f["core"] = _lines(p["core"])
            # 복사해 만든 분야는 앞 분야의 core_en 을 그대로 이고 있다. 핵심어를
            # 통째로 바꾸면서 이걸 두면, 화면에 없는 정규식이 계속 관문을 열어
            # 준다 — 해양안전 분야가 영문 AI 기사를 통과시키고 있었다.
            if p.get("core_en"):
                f["core_en"] = " ".join(str(p["core_en"]).split())
            elif "core_en" in f:
                del f["core_en"]
        if p.get("adjacent"):
            f["adjacent"] = _lines(p["adjacent"])
        _write(path, prof)
        done.append("키워드")

    if "profile" in want and (p.get("name") or p.get("subject")):
        path = _profile_path(load().profile_id, "profile.yaml")
        prof = _read(path)
        pr = prof.setdefault("profile", {})
        if p.get("name"):
            pr["name"] = str(p["name"])
        if p.get("subject"):
            pr["subject"] = str(p["subject"])
        _write(path, prof)
        done.append("분야 이름")

    if "tracks" in want and p.get("tracks"):
        path = _profile_path(load().profile_id, "profile.yaml")
        prof = _read(path)
        prof["tracks"] = [{
            "key": str(t.get("key")), "label": str(t.get("label") or t.get("key")),
            "field_label": str(t.get("field_label") or t.get("key")),
            "color": str(t.get("color") or "#38E1FF"),
            "quota": [int(t.get("min") or 0), int(t.get("max") or 0)],
        } for t in p["tracks"] if t.get("key")]
        _write(path, prof)
        done.append("트랙")

    if "relevance" in want and p.get("relevance"):
        path = _profile_path(load().profile_id, "ontology.yaml")
        onto = _read(path)
        wr = onto.setdefault("work_relevance", {})
        wr.clear()
        for g in p["relevance"]:
            name = str(g.get("group") or "").strip()
            if not name:
                continue
            row = {"weight": float(g.get("weight") or 1.0),
                   "keywords": _lines(g.get("keywords"))}
            if g.get("label"):
                row["label"] = str(g["label"])
            if _lines(g.get("context")):
                row["context"] = _lines(g["context"])
            wr[name] = row
        _write(path, onto)
        done.append("관련도")

    if "sources" in want and p.get("sources"):
        path = _profile_path(load().profile_id, "sources.yaml")
        doc = _read(path)
        doc["sources"] = [{
            "id": str(s.get("id")), "name": str(s.get("name") or s.get("id")),
            "track": str(s.get("track") or "policy"),
            "role": str(s.get("role") or "primary"),
            "collector": str(s.get("collector") or "google_news"),
            "enabled": True, "weight": float(s.get("weight") or 1.0),
            "params": s.get("params") or {},
        } for s in p["sources"] if s.get("id")]
        _write(path, doc)
        done.append("수집원")

    reload()
    return jsonify({"ok": True, "applied": done})


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

    cfg = load()
    raw = store.latest_raw()
    if raw:
        arts, source = store.load_raw(raw), raw.name
    else:
        # 처음 쓰는 사람에게 "먼저 수집하세요"는 막다른 길이다. 키워드는 ③ 인데
        # 수집 시험은 ⑦ 이라 순서상 볼 수가 없다. 그러니 여기서 조금 모은다.
        from .. import pipeline
        quick = [s["id"] for s in cfg.enabled_sources()
                 if s.get("collector") in ("google_news", "koreakr_search", "rss")]
        if not quick:
            quick = [s["id"] for s in cfg.enabled_sources()]
        if not quick:
            return jsonify({"ok": False,
                            "error": "켜 둔 수집원이 없습니다. ② 에서 하나 켜세요."})
        try:
            arts = pipeline.collect(cfg, days=7, only=quick, progress=lambda m: None)
        except Exception as exc:                           # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)[:200]})
        source = "방금 모은 최근 7일"
    if not arts:
        return jsonify({"ok": False,
                        "error": "자료가 하나도 안 들어왔습니다. ② 에서 수집원을 시험해 보세요."})
    groups = make_clusters(arts, float(cfg.get("dedupe.title_similarity", 0.72)))
    floor = float(cfg.get("filter.min_focus", 3.5))
    passed = [g for g in groups if topic_focus(g, cfg) >= floor]
    return jsonify({"ok": True, "total": len(groups), "passed": len(passed),
                    "sample": [g.lead.title[:60] for g in passed[:5]],
                    "dropped": [g.lead.title[:60] for g in groups
                                if topic_focus(g, cfg) < floor][:3],
                    "raw": source})
