"""Jinja2 로 정적 HTML 을 만든다. 외부 자원을 참조하지 않는 단일 파일로 출력한다."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .config import ROOT, Config, load
from .models import Issue

TEMPLATE_DIR = ROOT / "templates"


def body_part(line: str) -> dict:
    """본문 한 줄을 줄머리 기호와 본문으로 나눈다.

    기호를 본문과 같은 흐름에 두면 줄이 넘어갈 때 기호 자리로 되돌아온다.
    기호와 본문을 갈라 두 칸으로 그려야 넘어간 줄이 본문 시작선에 맞는다.
    글꼴·화면 폭·글자 수와 무관하게 항상 맞으므로 회차마다 손볼 필요가 없다.
    """
    s = (line or "").strip()
    if s[:1] in ("*", "※"):
        # 한 줄에 각주가 둘이면 두 번째는 ** 로 적는다. 사이에 빈칸이 끼기도 한다.
        body = s[1:].lstrip()
        if body[:1] == "*":
            return {"level": 3, "marker": "**", "text": body[1:].strip()}
        return {"level": 3, "marker": "*", "text": body.strip()}
    if s[:1] in ("-", "–", "—"):
        return {"level": 2, "marker": "-", "text": s[1:].strip()}
    if s[:1] in ("ㅇ", "○", "•"):
        return {"level": 1, "marker": "ㅇ", "text": s[1:].strip()}
    return {"level": 1, "marker": "ㅇ", "text": s}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["body_part"] = body_part
    env.filters["tojson_safe"] = lambda v: Markup(
        __import__("json").dumps(v, ensure_ascii=False).replace("<", "\\u003c")
    )
    return env


def galaxy_data(issue: Issue, cfg: Config) -> dict:
    """성운 목차에 뿌릴 자료.

    - stars : 이번에 수집했지만 싣지 않은 이슈. 흐릿한 별로 그린다
    - picked: 실제로 실은 항목. 밝게 빛나고 눌러서 이동할 수 있다
    - words : 배경에 떠다닐 기관·업무·기술 낱말
    """
    picked = [
        {
            "no": it.no,
            "title": it.title,
            "short": it.title if len(it.title) <= 17 else it.title[:16] + "…",
            "field": it.field_label,
            "impact": it.impact,
            "track": it.track,
            "why": it.why,
        }
        for it in issue.items
    ]

    used = {t["title"] for t in picked}
    stars = []
    for c in issue.meta.get("candidates", []):
        arts = c.get("articles") or []
        if not arts:
            continue
        title = arts[0].get("title", "")
        if not title or title in used:
            continue
        stars.append(
            {
                "title": title[:60],
                "source": arts[0].get("source_name", ""),
                "track": arts[0].get("track", "industry"),
                "score": round(float(c.get("score") or 0), 1),
            }
        )

    # 트랙별 수집 건수 — 성운에서 색으로 구분하므로 범례에 함께 쓴다
    by_track = {"policy": 0, "industry": 0, "dev": 0}
    for c in issue.meta.get("candidates", []):
        arts = c.get("articles") or []
        if arts:
            by_track[arts[0].get("track", "industry")] = (
                by_track.get(arts[0].get("track", "industry"), 0) + 1
            )

    return {
        "picked": picked,
        "keywords": issue.meta.get("keywords", []),
        "summary": issue.meta.get("summary", {}),
        "stars": stars[:70],
        "collected": issue.meta.get("collected", 0),
        "clusters": issue.meta.get("clusters", 0),
        "ai_passed": issue.meta.get("ai_passed", 0),
        "sources": issue.meta.get("sources_used", 0),
        "by_track": by_track,
    }


def render(issue: Issue, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load()
    series = cfg.get("issue.series", "AI 정보동향지")
    publisher = cfg.get("issue.publisher", "해양경찰청")
    team = cfg.get("issue.team", "인공지능전환팀")
    return _env().get_template("issue.html.j2").render(
        fonts=fonts_for(issue, cfg),
        issue=issue,
        series=series,
        publisher=publisher,
        team=team,
        galaxy=galaxy_data(issue, cfg),
    )


def write(issue: Issue, cfg: Optional[Config] = None, preview: bool = False) -> Path:
    cfg = cfg or load()
    html = render(issue, cfg)
    name = issue.slug + (".preview.html" if preview else ".html")
    path = cfg.path("html_dir") / name
    path.write_text(html, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 글꼴
#
# AX360 은 표지부터 카드 목록까지 Paperlogy 한 벌로 통일한다(본문 400, 제목 700).
# 기관 PC 에 이 글꼴이 없어도 같아 보이도록, **이 회차에 실제로 쓰인 글자만**
# 잘라내 파일 안에 담는다. 세 굵기를 합쳐 100KB 남짓이다.
# Paperlogy 는 SIL OFL 이라 임베딩·재배포가 허용된다(assets/fonts/README.md).

FONT_DIR = ROOT / "assets" / "fonts"

# 표지·제목용(display)과 본문용(body)을 따로 고른다.
#   Paperlogy  : AX360 과 같은 글꼴. 발표용으로 만들어져 큰 글자에서 잘 읽힌다
#   Pretendard : 본문용. 숫자·괄호가 많은 개조식 문장에서 자간이 고르다
#                (AX360 도 body 기본값은 Pretendard 로 두고 있다)
FONT_SETS = {
    "paperlogy": {
        400: "Paperlogy-4Regular.ttf",
        700: "Paperlogy-7Bold.ttf",
        900: "Paperlogy-9Black.ttf",
    },
    "pretendard": {
        400: "Pretendard-Regular.woff",
        600: "Pretendard-SemiBold.woff",
        700: "Pretendard-Bold.woff",
    },
}

# 글자가 빠져 네모로 보이는 일이 없도록 늘 포함하는 기호·상용어
_ALWAYS = (
    "0123456789 .,·-–—()[]{}「」『』<>《》%℃~/:;!?*※＊|"
    "ㅇ○→←↑↓✓✔’‘“”\'\"…"
    "해양경찰청인공지능전환팀정보동향지제호발행수집기간이슈주제목차"
    "관련기사영상선정사유시점향후계획분야참고영향높은중간낮음"
    "정책산업개발자트랙매체건곳표시원문전체록없다위개통합확인게재"
    "월화수목금토일년요전후오"
)


def _document_text(issue: Issue, cfg: Config) -> str:
    """이 회차 발행물에 등장하는 모든 글자."""
    parts = [
        cfg.get("issue.series", ""),
        cfg.get("issue.publisher", ""),
        cfg.get("issue.team", ""),
        issue.slug,
        issue.label,
        issue.theme or "",
        _ALWAYS,
    ]
    for it in issue.items:
        parts += [
            it.title, it.source_label, it.field_label, it.audience,
            it.impact, it.note_kind,
        ]
        parts += it.body + list(it.notes.values()) + it.why + it.similar_to
        parts += [(l.get("label") or "") + (l.get("title") or "") for l in it.links]
    g = galaxy_data(issue, cfg)
    parts += [k["text"] for k in g["keywords"]]
    parts += [s["title"] + s["source"] for s in g["stars"]]
    parts += [p["short"] + p["field"] for p in g["picked"]]
    return "".join(parts)


# 같은 글자 묶음이면 결과가 같다. 미리보기를 누를 때마다 1.6초씩 다시 자르지 않도록
# 잘라 둔 것을 파일로 남긴다. 항목을 고쳐 글자가 바뀌면 자동으로 새로 만든다.
def _subset_cache_path(filename: str, text: str):
    import hashlib

    key = hashlib.sha1(("%s|%s" % (filename, "".join(sorted(set(text))))).encode("utf-8"))
    d = ROOT / "data" / "cache" / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d / ("%s.txt" % key.hexdigest()[:20])


def _subset(filename: str, text: str) -> str:
    """글꼴에서 쓰이는 글자만 남긴 woff2 를 data URI 로. 실패하면 빈 문자열."""
    path = FONT_DIR / filename
    if not path.exists():
        return ""

    cache = _subset_cache_path(filename, text)
    if cache.exists():
        try:
            return cache.read_text(encoding="ascii")
        except Exception:  # noqa: BLE001
            pass

    try:
        import base64
        import io

        from fontTools import subset
        from fontTools.ttLib import TTFont

        font = TTFont(str(path))
        options = subset.Options()
        options.flavor = "woff2"
        options.desubroutinize = True
        options.layout_features = ["*"]
        options.drop_tables += ["DSIG"]
        sub = subset.Subsetter(options=options)
        sub.populate(text="".join(sorted(set(text))))
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        uri = "data:font/woff2;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        try:
            cache.write_text(uri, encoding="ascii")
        except Exception:  # noqa: BLE001
            pass
        return uri
    except Exception:  # noqa: BLE001 - 글꼴은 있으면 좋은 것이지 필수가 아니다
        return ""


def fonts_for(issue: Issue, cfg: Config) -> dict:
    """@font-face 로 심을 글꼴 목록.

    표지·제목용과 본문용을 따로 고를 수 있다. 같은 글꼴을 고르면 한 번만 담는다.
    """
    text = _document_text(issue, cfg)
    display = str(cfg.get("output.display_font", "paperlogy")).lower()
    body = str(cfg.get("output.body_font", "pretendard")).lower()

    faces = []
    seen = set()

    def add(css_family: str, family_key: str, weights):
        table = FONT_SETS.get(family_key) or FONT_SETS["paperlogy"]
        for weight in weights:
            filename = table.get(weight)
            if not filename:
                continue
            key = (css_family, weight)
            if key in seen:
                continue
            uri = _subset(filename, text)
            if uri:
                seen.add(key)
                faces.append({"family": css_family, "weight": weight, "uri": uri})

    add("TLDisplay", display, [700, 900])
    add("TLBody", body, [400, 700])

    return {
        "faces": faces,
        "display_name": display,
        "body_name": body,
        "bytes": sum(len(f["uri"]) for f in faces),
    }
