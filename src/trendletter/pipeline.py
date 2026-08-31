"""수집 → 통합 → 점수 → 초안 생성까지의 흐름."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from . import llm, store
from .collectors import build
from .config import Config, load
from .dedupe import cluster, merge_by_entity, near_duplicates, similarity
from .http import Fetcher
from .models import FIELD_LABELS, Article, Cluster, Issue, Item
from .scoring import (entities, entity_platforms, explain, is_ai_related, is_product,
                      keywords, newsiness, rank, select, tech_keywords)


def collect(
    cfg: Optional[Config] = None,
    days: Optional[int] = None,
    only: Optional[List[str]] = None,
    use_cache: bool = True,
    progress: Optional[Callable[[str], None]] = None,
    since: Optional[datetime] = None,
) -> List[Article]:
    cfg = cfg or load()
    if since is None:
        days = days or int(cfg.get("collect.days", 7))
        since = datetime.now() - timedelta(days=days)
    limit = int(cfg.get("collect.max_per_source", 60))
    fetcher = Fetcher(use_cache=use_cache)

    say = progress or (lambda m: None)
    articles: List[Article] = []
    health: List[dict] = []      # 수집기별 결과. 사이트가 바뀌어 조용히 0건이 되는 걸 잡는다
    for source in cfg.enabled_sources(only):
        # 수집 기간은 모든 수집원에 똑같이 적용한다.
        # 일부만 늘리면 같은 화면에서 몇 주 전 소식과 이번 주 소식이 뒤섞인다.
        # 더 넓게 보려면 편집기에서 수집 기간 자체를 늘린다.
        try:
            got = build(source, fetcher).collect(since, limit)
        except Exception as exc:  # noqa: BLE001 - 한 소스 실패가 전체를 막지 않는다
            say("  ! %s 수집 실패: %s" % (source["name"], exc))
            health.append({"name": source["name"], "n": -1, "error": str(exc)[:120]})
            continue
        for a in got:
            a.raw.setdefault("role", source.get("role", "primary"))
        articles.extend(got)
        health.append({"name": source["name"], "n": len(got), "error": ""})
        say("  · %-22s %3d건" % (source["name"], len(got)))

    LAST_HEALTH[:] = health
    for line in health_warnings(health):
        say("  ! " + line)
    return articles


# 마지막 수집의 수집기별 결과. 편집기와 주간 알림이 함께 본다.
LAST_HEALTH: List[dict] = []


def health_warnings(health: List[dict]) -> List[str]:
    """담당자가 손을 써야 하는 상황만 골라 말한다.

    수집기는 사이트 구조가 바뀌면 예외 없이 0건을 돌려준다. 그대로 두면
    몇 주 동안 특정 기관 소식이 통째로 빠진 채 발행된다.
    """
    if not health:
        return ["수집원이 하나도 실행되지 않았습니다"]
    dead = [h["name"] for h in health if h["n"] == 0]
    broken = [h for h in health if h["n"] < 0]
    total = sum(h["n"] for h in health if h["n"] > 0)
    out = []
    if broken:
        out.append("오류로 멈춘 수집원 %d개: %s"
                   % (len(broken), ", ".join(h["name"] for h in broken)))
    if dead:
        out.append("0건으로 끝난 수집원 %d개: %s  (사이트 구조가 바뀌었을 수 있습니다 — "
                   "python run.py sources 로 확인하세요)" % (len(dead), ", ".join(dead)))
    if len(dead) + len(broken) >= max(2, len(health) // 3):
        out.append("수집원 %d개 중 %d개가 아무것도 가져오지 못했습니다. 발행 전에 점검하세요."
                   % (len(health), len(dead) + len(broken)))
    if total < 30:
        out.append("전체 수집량이 %d건뿐입니다. 평소(150건 안팎)보다 크게 적습니다." % total)
    return out


def build_clusters(articles: List[Article], cfg: Optional[Config] = None) -> List[Cluster]:
    cfg = cfg or load()
    threshold = float(cfg.get("dedupe.title_similarity", 0.72))
    groups = cluster(articles, threshold)
    if cfg.get("dedupe.merge_by_entity", True):
        groups = merge_by_entity(
            groups, entities, newsiness,
            float(cfg.get("dedupe.entity_min_similarity", 0.30)), is_product,
        )
    return rank(groups, cfg, articles)


def _source_label(cl: Cluster) -> str:
    """PDF 서식의 <출처 · 26.8.24.> 형태."""
    lead = cl.lead
    name = lead.raw.get("dept") or lead.source_name
    if len(cl.outlets) > 1:
        name = "%s 외 %d" % (name, len(cl.outlets) - 1)
    if lead.published:
        d = lead.published
        return "%s · %s. %d. %d." % (name, str(d.year)[2:], d.month, d.day)
    return name


def _impact(cl: Cluster) -> str:
    if cl.score >= 12:
        return "높음"
    if cl.score >= 7:
        return "중간"
    return "낮음"


def _audience(cl: Cluster) -> str:
    onto = cl.onto or {}
    work = onto.get("업무 분야") or []
    plan = onto.get("정책·사업") or []
    if work and any(w != "행정·기획" for w in work):
        return "전 직원"
    if "전략·계획" in plan or "법·제도" in plan:
        return "정책기획"
    if "예산·사업" in plan or "조직·인력" in plan:
        return "사업기획"
    return "전 직원"


_SENT = re.compile(r"(?<=[.!?])\s+|(?<=다)\.\s*|(?<=음)\.\s*")

# 사진 설명·저작권 문구는 요약문에 섞여 들어오므로 뼈대에서 뺀다.
_NOISE = re.compile(r"사진\s*=|무단\s*전재|재배포\s*금지|저작권자|\(사진|ⓒ|촬영")


def _outline(summary: str, max_lines: int = 4):
    """요약문을 개조식 뼈대로 쪼갠다.

    Claude 초안 작성을 붙이기 전까지 담당자가 바로 손볼 수 있는 형태로 만든다.
    첫 문장은 ㅇ(1단계), 나머지는 -(2단계)로 둔다.
    """
    text = (summary or "").strip().lstrip(". ")
    if not text:
        return []
    parts = [
        s.strip(" .")
        for s in _SENT.split(text)
        if len(s.strip(" .")) > 8 and not _NOISE.search(s)
    ]
    if not parts:
        parts = [text]
    lines = ["ㅇ " + parts[0][:160]]
    for s in parts[1:max_lines]:
        lines.append(" - " + s[:150])
    return lines


def _draft_notes(cl: Cluster) -> dict:
    """시사점과 향후계획 초안을 둘 다 만든다.

    Claude 를 붙이기 전까지는 온톨로지에서 뽑은 업무 분야·기술·기관을 넣어
    담당자가 지우고 쓰기보다 고쳐 쓰도록 한다. 어느 쪽이 맞는지는 사람이 고른다.
    """
    onto = cl.onto or {}
    works = onto.get("업무 분야") or []
    techs = onto.get("기술·도구") or []
    orgs = onto.get("기관") or []

    work = works[0] if works else "관련"
    tech = techs[0] if techs else "해당 기술"
    org = orgs[0] if orgs else cl.lead.source_name

    return {
        "시사점": "우리청 %s 업무에 %s 적용 가능성과 필요 여건을 검토할 필요" % (work, tech),
        "향후계획": "%s의 후속 발표를 확인하고, 우리청 %s 업무 적용 방안을 사전 검토"
        % (org, work),
    }


def to_item(cl: Cluster, no: int) -> Item:
    """LLM 초안 작성 전의 뼈대. 본문·시사점은 사람 또는 Claude 가 채운다."""
    lead = cl.lead
    body = _outline(lead.summary)
    for extra in cl.articles[1:3]:
        if extra.summary and extra.source_name != lead.source_name:
            body.append(" - (%s) %s" % (extra.source_name, extra.summary.strip()[:120]))
    return Item(
        no=no,
        field_label=FIELD_LABELS.get(lead.track, "기관 동향"),
        audience=_audience(cl),
        impact=_impact(cl),
        title=lead.title,
        source_label=_source_label(cl),
        body=body or ["ㅇ (본문을 작성하세요)"],
        note_kind="시사점",          # 기본값은 시사점. 편집기에서 향후계획으로 바꿀 수 있다.
        notes=_draft_notes(cl),
        links=[
            {
                "label": a.source_name,
                "url": a.url,
                "title": a.title,
                "date": a.published.strftime("%Y-%m-%d") if a.published else "",
            }
            for a in cl.articles[:6]
        ],
        onto=cl.onto,
        origin_keys=[a.key for a in cl.articles],
        track=lead.track,
    )


def enrich_bodies(clusters: List[Cluster],
                  limit: int = 0,
                  progress: Optional[Callable[[str], None]] = None) -> int:
    """순위를 매기기 **전에** 본문이 빈 항목의 본문을 받아 온다.

    서울 AI 플랫폼·Reddit·Hacker News 는 목록에 제목만 준다. 그대로 두면
    제목만으로 점수가 매겨져 구조적으로 밀린다(측정: 본문 있는 클러스터의
    순위 중앙값 54위 대 제목뿐 124위). 클러스터 단위로 병렬 수집하면
    65건에 3초쯤 걸린다.
    """
    import json as _json

    say = progress or (lambda m: None)
    todo = [c for c in clusters if not (c.lead.summary or "").strip()]
    if limit:
        todo = todo[:limit]
    if not todo:
        return 0

    fetcher = Fetcher()
    seoul_url = "https://seoulai.saif.or.kr/hmpg/bpst/bpstPostSummary.do"

    def grab(cl: Cluster) -> int:
        a = cl.lead
        try:
            if a.source_id == "seoul_ai" and (a.raw.get("keys") or [None])[0]:
                mng, pst = a.raw["keys"]
                data = _json.loads(fetcher.post(
                    seoul_url, [("hmpg_mng_no", mng), ("pst_no", pst)]))
                text = " ".join((data.get("summary") or "").split())
            else:
                # 구글 뉴스 주소는 자바스크립트로 넘어가므로 서버에서 원문이
                # 안 열린다. 긁으면 구글 페이지가 통째로 들어온다.
                if not a.url or "news.google.com" in a.url:
                    return 0
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(fetcher.get(a.url), "lxml")
                for tag in soup(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                node = (soup.find("article") or soup.find("main")
                        or soup.find(attrs={"class": "content"}) or soup.body)
                text = " ".join((node.get_text(" ") if node else "").split())
        except Exception:  # noqa: BLE001 - 한 건 실패가 전체를 막지 않는다
            return 0
        if not text:
            return 0
        a.summary = text[:900]
        return 1

    with ThreadPoolExecutor(max_workers=10) as pool:
        got = sum(pool.map(grab, todo))
    say("  · 본문 없는 %d건 중 %d건 채움" % (len(todo), got))
    return got


def llm_rerank(clusters: List[Cluster], cfg: Config,
               pool: int = 40,
               progress: Optional[Callable[[str], None]] = None
               ) -> Optional[List[Cluster]]:
    """규칙 점수 상위 pool 개를 Claude 가 본문까지 읽고 다시 고른다.

    규칙 점수는 낱말이 몇 번 걸렸는지로 매겨진다. 본문을 채우면 좋은 정책
    자료도 오르지만 개발 잡음도 같이 오른다. 여기서 그 둘을 갈라 준다.

    규칙 단계가 '무엇이 후보인가' 를 정하고(재현·감사 가능), 이 단계는
    '그 안에서 무엇이 중요한가' 만 판단한다. 실패하면 None 을 돌려주고
    부르는 쪽이 규칙 결과를 그대로 쓴다.
    """
    say = progress or (lambda m: None)
    if not llm.available():
        return None
    head = clusters[:pool]
    if len(head) < 8:
        return None

    quota = cfg.get("compose.quota", {}) or {}
    payload = [
        {
            "index": i,
            "title": c.lead.title,
            "source": c.lead.source_name,
            "track": c.lead.track,
            "outlets": len(c.outlets),
            "rule_score": round(c.score, 1),
            "summary": (c.lead.summary or "")[:600],
        }
        for i, c in enumerate(head)
    ]
    prompt = (
        llm._load_prompt("select_rank.md")
        .replace("{{DAYS}}", str(cfg.get("collect.days", 7)))
        .replace("{{TOTAL_MAX}}", str(cfg.get("compose.total_max", 6)))
        .replace("{{POLICY_MIN}}", str(quota.get("policy", [3, 4])[0]))
        .replace("{{POLICY_MAX}}", str(quota.get("policy", [3, 4])[1]))
        .replace("{{INDUSTRY_MIN}}", str(quota.get("industry", [2, 3])[0]))
        .replace("{{INDUSTRY_MAX}}", str(quota.get("industry", [2, 3])[1]))
        .replace("{{CANDIDATES_JSON}}", json.dumps(payload, ensure_ascii=False, indent=1))
    )
    try:
        data = llm._extract_json(llm.run(prompt))
    except Exception as exc:  # noqa: BLE001 - 실패하면 규칙 결과를 쓴다
        say("  ! 재순위 실패(규칙 결과 사용): %s" % str(exc)[:90])
        return None

    picked, seen = [], set()
    for row in sorted(data.get("selected") or [],
                      key=lambda r: r.get("order", 99)):
        i = row.get("cluster_index")
        if not isinstance(i, int) or not 0 <= i < len(head) or i in seen:
            continue
        seen.add(i)
        head[i].llm_reason = (row.get("reason") or "").strip()
        picked.append(head[i])
    total_max = int(cfg.get("compose.total_max", 6))
    if len(picked) < total_max:
        say("  ! 재순위가 %d건만 골라 규칙 결과를 씁니다" % len(picked))
        return None
    picked = picked[:total_max]

    # 트랙 구성은 지난 11개 호를 세어 정한 값이다. 모델이 어겨도 그쪽이 이기게
    # 두지 않는다. 어기면 규칙 결과로 돌아간다.
    got: Dict[str, int] = {}
    for c in picked:
        got[c.lead.track] = got.get(c.lead.track, 0) + 1
    for track, bounds in quota.items():
        n = got.get(track, 0)
        if not int(bounds[0]) <= n <= int(bounds[1]):
            say("  ! 재순위의 %s 트랙이 %d건(허용 %d~%d)이라 규칙 결과를 씁니다"
                % (track, n, int(bounds[0]), int(bounds[1])))
            return None

    say("  · Claude 재순위: 상위 %d개 중 %d건 선정" % (len(head), len(picked)))
    return picked


def fill_summaries(clusters_by_key: Dict[str, Cluster],
                   progress: Optional[Callable[[str], None]] = None) -> None:
    """서울 AI 플랫폼 항목의 요약을 뒤늦게 채운다.

    이 소스는 목록에 제목만 있어, 초안을 쓸 때 Claude 에게 줄 재료가 없다.
    수집 단계에서 전부 받으면 90초가 들고 관문 판정은 바뀌지 않으므로,
    실제로 뽑힌 항목에 대해서만 가져온다.
    """
    import json

    say = progress or (lambda m: None)
    # Cluster 는 해시할 수 없으므로 id 로 중복을 없앤다
    seen_cluster, seen_url = set(), set()
    targets = []
    for c in clusters_by_key.values():
        if id(c) in seen_cluster:
            continue
        seen_cluster.add(id(c))
        for a in c.articles:
            if a.source_id != "seoul_ai" or a.summary or a.url in seen_url:
                continue
            if not (a.raw.get("keys") or [None])[0]:
                continue
            seen_url.add(a.url)
            targets.append(a)
    if not targets:
        return

    fetcher = Fetcher()
    url = "https://seoulai.saif.or.kr/hmpg/bpst/bpstPostSummary.do"
    filled = 0
    for a in targets:
        mng, pst = a.raw["keys"]
        try:
            data = json.loads(fetcher.post(url, [("hmpg_mng_no", mng), ("pst_no", pst)]))
        except Exception:  # noqa: BLE001
            continue
        text = " ".join((data.get("summary") or "").split())
        if text:
            a.summary = text[:700]
            filled += 1
    if filled:
        say("  · 서울 AI 플랫폼 요약 %d건 보충" % filled)


def polish(
    issue: Issue,
    clusters_by_key: Optional[Dict[str, Cluster]] = None,
    cfg: Optional[Config] = None,
    only: Optional[List[int]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Issue:
    """Claude 로 본문과 맺음말을 작성한다.

    - 잠근 항목(사람이 고친 항목)은 건드리지 않는다
    - 한 항목이 실패해도 나머지는 그대로 진행하고, 실패한 항목은 뼈대를 유지한다
    """
    cfg = cfg or load()
    say = progress or (lambda m: None)

    if not cfg.get("llm.enabled", True):
        say("  · 초안 작성(Claude) 이 꺼져 있습니다")
        return issue
    if not llm.available():
        say("  ! claude 명령을 찾을 수 없어 규칙 기반 뼈대를 그대로 둡니다")
        return issue

    clusters_by_key = clusters_by_key or {}
    fill_summaries(clusters_by_key, say)
    targets = [
        (i, it)
        for i, it in enumerate(issue.items)
        if (only is None or it.no in only) and not it.locked
    ][: int(cfg.get("llm.max_items", 8))]
    if not targets:
        return issue

    def work(pair):
        i, item = pair
        cl = None
        for key in item.origin_keys:
            if key in clusters_by_key:
                cl = clusters_by_key[key]
                break
        payload = (
            llm.cluster_payload(cl)
            if cl
            else {
                "outlets": [l.get("label") for l in item.links],
                "ontology": item.onto,
                "articles": [
                    {
                        "source": item.source_label,
                        "title": item.title,
                        "summary": " ".join(item.body),
                        "url": item.links[0]["url"] if item.links else "",
                    }
                ],
            }
        )
        return i, llm.draft_item(payload, slot=item.no - 1)

    say("  · Claude 로 %d개 항목 초안 작성 중…" % len(targets))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(work, pair): pair for pair in targets}
        for fut in as_completed(futures):
            i, item = futures[fut]
            try:
                idx, drafted = fut.result()
            except Exception as exc:  # noqa: BLE001 - 한 건 실패가 전체를 막지 않는다
                say("    ! %02d번 실패(뼈대 유지): %s" % (item.no, str(exc)[:90]))
                continue
            target = issue.items[idx]
            for key in ("field_label", "audience", "impact", "title", "source_label"):
                if drafted.get(key):
                    setattr(target, key, drafted[key])
            if drafted.get("body"):
                target.body = drafted["body"]
            notes = drafted.get("notes") or {}
            for kind in ("시사점", "향후계획"):
                if notes.get(kind):
                    target.notes[kind] = notes[kind]
            say("    ✓ %02d %s" % (target.no, target.title[:44]))
    return issue


def make_draft(
    articles: List[Article],
    cfg: Optional[Config] = None,
    days: Optional[int] = None,
    theme: str = "",
    period_from: Optional[date] = None,
    period_to: Optional[date] = None,
    published_on: Optional[date] = None,
    number: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Issue:
    cfg = cfg or load()
    days = days or int(cfg.get("collect.days", 7))
    threshold_ = float(cfg.get("dedupe.title_similarity", 0.72))
    raw_clusters = cluster(articles, threshold_)     # AI 관문 통과 전 전체 이슈 수
    if cfg.get("dedupe.merge_by_entity", True):
        raw_clusters = merge_by_entity(
            raw_clusters, entities, newsiness,
            float(cfg.get("dedupe.entity_min_similarity", 0.30)), is_product,
        )
    clusters = build_clusters(articles, cfg)

    # 순위를 매기기 전에 본문이 빈 항목을 채운다. 제목만으로 점수를 매기면
    # 서울 AI 플랫폼·HN 같은 목록형 수집원이 구조적으로 밀린다.
    if cfg.get("compose.enrich_before_rank", True):
        enrich_bodies(clusters, progress=progress)
        clusters = build_clusters(articles, cfg)     # 채운 본문으로 다시 채점

    rule_rank = {id(c): i + 1 for i, c in enumerate(clusters)}
    chosen = select(clusters, cfg)

    # 본문을 채우면 좋은 정책 자료도 오르지만 낱말이 많이 걸리는 개발 잡음도
    # 함께 오른다. Claude 가 상위 후보의 본문을 읽고 그 둘을 가른다.
    # 실패하면 규칙 결과를 그대로 쓴다.
    if cfg.get("compose.llm_rerank", True):
        picked = llm_rerank(clusters, cfg,
                            pool=int(cfg.get("compose.rerank_pool", 40)),
                            progress=progress)
        if picked:
            chosen = picked

    # 병합되지 않았지만 같은 사건일 수 있는 짝을 표시한다.
    # 자동 병합은 잘못 묶일 위험이 커서, 판단은 담당자에게 남긴다.
    threshold = float(cfg.get("dedupe.title_similarity", 0.72))
    warn = near_duplicates(chosen, threshold)
    items = [to_item(c, i + 1) for i, c in enumerate(chosen)]
    # 트랙 안에서 몇 번째로 중요한 이슈였는지도 사유에 넣는다
    track_rank, seen_track = {}, {}
    for c in clusters:
        seen_track[c.lead.track] = seen_track.get(c.lead.track, 0) + 1
        track_rank[id(c)] = seen_track[c.lead.track]
    for item, cl in zip(items, chosen):
        item.why = explain(
            cl, cfg, track_rank.get(id(cl), 0), seen_track.get(cl.lead.track, 0)
        )
        # 규칙이 몇 위로 봤는지도 남긴다. Claude 가 올린 항목을 되짚을 수 있어야 한다
        rank = rule_rank.get(id(cl))
        if rank:
            item.why.append("규칙 점수 %d위" % rank)
        if getattr(cl, "llm_reason", ""):
            item.why.append("Claude 선정: " + cl.llm_reason)
    for i, j, s in warn:
        items[i].similar_to.append("%02d번과 유사 (%.2f)" % (j + 1, s))
        items[j].similar_to.append("%02d번과 유사 (%.2f)" % (i + 1, s))

    # 뽑히지 않았지만 같은 사건을 다룬 기사도 '관련 기사' 로 붙인다.
    picked = {id(c) for c in chosen}
    for idx, item in enumerate(items):
        have = {l["url"] for l in item.links}
        for cand in clusters:
            if id(cand) in picked:
                continue
            if similarity(chosen[idx].lead.title, cand.lead.title) < 0.45:
                continue
            for a in cand.articles[:2]:
                if a.url in have:
                    continue
                have.add(a.url)
                item.links.append(
                    {
                        "label": a.source_name,
                        "url": a.url,
                        "title": a.title,
                        "date": a.published.strftime("%Y-%m-%d") if a.published else "",
                        "related": True,
                    }
                )
            if len(item.links) >= 8:
                break

    today = date.today()
    year = int(cfg.get("issue.year", today.year))
    # 이번 주 핫이슈는 개별 기사가 아니라 '종합 정리' 항목으로 싣는다.
    # 자리를 먼저 확보하고, 남은 칸을 일반 항목으로 채운다.
    topics = hot_topics(clusters, articles, cfg, int(cfg.get("compose.topic_slots", 1)))
    topic_keys = {k for tp in topics for a in tp["articles"] for k in [a.key]}

    by_key = {a.key: c for c in chosen for a in c.articles}
    # 종합 항목이 다룰 기사만으로 이루어진 일반 항목은 중복이므로 뺀다
    if topics:
        keep = []
        for it in items:
            covered = sum(1 for k in it.origin_keys if k in topic_keys)
            if it.origin_keys and covered == len(it.origin_keys):
                continue
            keep.append(it)
        items = keep[: max(0, int(cfg.get("compose.total_max", 6)) - len(topics))]
        for i, it in enumerate(items, 1):
            it.no = i

    issue = Issue(
        year=year,
        number=int(number) if number else store.next_issue_number(year),
        published_on=published_on or today,
        period_from=period_from or (today - timedelta(days=days)),
        period_to=period_to or today,
        theme=theme,
        items=items,
        meta={
            "collected": len(articles),
            "clusters": len(raw_clusters),
            "ai_passed": len(clusters),
            "candidates": [c.to_dict() for c in clusters[:60]],
            "keywords": keywords(clusters, chosen, 26),
            "topics": [
                {"term": tp["term"], "key": tp["key"], "count": len(tp["articles"]),
                 "platforms": tp["platforms"], "sources": tp["sources"]}
                for tp in topics
            ],
            "summary": _summary(articles, raw_clusters, clusters, chosen),
        },
    )
    issue.meta["_clusters"] = by_key      # polish() 에 넘기기 위한 임시 참조
    issue.meta["_all_clusters"] = clusters   # 참고용 임시 참조
    issue.meta["_topics"] = topics           # 종합 항목 작성에 쓴다
    return issue


def _summary(articles, raw_clusters, passed, chosen) -> Dict[str, Any]:
    """'무엇을 얼마나 보고 무엇을 골랐는지' 를 한눈에 보여줄 값."""
    from collections import Counter

    label = {"policy": "정책", "industry": "산업", "dev": "개발자"}
    by_track = Counter(c.lead.track for c in passed)
    picked_track = Counter(c.lead.track for c in chosen)
    return {
        "sources": len({a.source_id for a in articles}),
        "articles": len(articles),
        "issues": len(passed),
        "picked": len(chosen),
        "tracks": [
            {
                "key": k,
                "name": label.get(k, k),
                "issues": by_track.get(k, 0),
                "picked": picked_track.get(k, 0),
            }
            for k in ("policy", "industry", "dev")
        ],
        "merged": sum(1 for c in passed if len(c.articles) > 1),
        "multi_outlet": sum(1 for c in chosen if len(c.outlets) > 1),
    }


def hot_topics(clusters: List[Cluster], articles: List[Article], cfg: Config,
               limit: int = 2) -> List[Dict[str, Any]]:
    """이번 주 가장 많이 언급된 기술·모델과, 그것을 다룬 자료 전부를 모은다.

    개별 기사 하나를 뽑는 것이 아니라 흩어진 언급을 한자리에 모아
    '이게 뭔지' 를 조사해 정리할 재료로 쓴다.
    """
    cands = tech_keywords(clusters, cfg, limit * 8)
    heat = entity_platforms(articles)
    min_mentions = int(cfg.get("compose.topic_min_mentions", 3))

    def mentions(key: str, art: Article) -> bool:
        """영문 이름은 낱말 경계를 지킨다.

        'codex' 를 부분일치로 찾으면 fuxicodex 같은 저장소 이름까지 걸린다.
        """
        text = (art.title + " " + art.summary).lower()
        if re.fullmatch(r"[a-z0-9.\-]+", key):
            return bool(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(key), text))
        return key in text

    scored = []
    for cand in cands:
        key = cand["key"]
        hits = [a for a in articles if mentions(key, a)]
        if len(hits) < min_mentions:
            continue
        platforms = heat.get(key, set())
        sources = {a.source_name for a in hits}
        # 한 곳에서만 나온 이야기는 '동향' 이 아니다
        if len(sources) < 2:
            continue
        # 여러 플랫폼(HN·Reddit·GitHub·국내매체)에 걸친 것이 진짜 화제다.
        # 한 매체가 여러 번 쓴 것보다 여러 곳이 한 번씩 다룬 쪽을 위로 올린다.
        # 여러 플랫폼에 걸친 것이 진짜 화제다. 한 개념어가 여러 매체에 흩어져
        # 나오는 것보다, 같은 이름이 HN·Reddit·국내매체에 동시에 오르는 쪽을 본다.
        heat_score = (
            len(platforms) * 4
            + min(len(sources), 5) * 1.5
            + min(len(hits), 10) * 0.5
        )
        scored.append(
            {
                "term": cand["text"],
                "key": key,
                "count": len(hits),
                "heat": heat_score,
                "platforms": sorted(platforms),
                "sources": sorted(sources),
                "articles": sorted(
                    hits, key=lambda a: -(a.published.timestamp() if a.published else 0)
                ),
            }
        )

    scored.sort(key=lambda x: -x["heat"])
    return scored[:limit]


def make_trend_item(topic: Dict[str, Any], no: int,
                    progress: Optional[Callable[[str], None]] = None) -> Optional[Item]:
    """핫이슈 하나를 조사·종합한 동향 항목으로 만든다."""
    say = progress or (lambda m: None)
    payload = [
        {
            "source": a.source_name,
            "kind": "커뮤니티" if a.source_id in ("hackernews", "reddit") else "매체·저장소",
            "published": a.published.strftime("%Y-%m-%d") if a.published else "",
            "title": a.title,
            "summary": (a.summary or "")[:500],
            "url": a.url,
        }
        for a in topic["articles"][:14]
    ]
    drafted = None
    for attempt in range(2):
        try:
            drafted = llm.synthesize_trend(topic["term"], payload)
        except Exception as exc:  # noqa: BLE001
            say("    ! %s 종합 실패: %s" % (topic["term"], str(exc)[:80]))
            return None
        # 맺음말 두 종류가 다 있어야 편집기에서 골라 쓸 수 있다
        if all(drafted["notes"].get(k) for k in ("시사점", "향후계획")):
            break
        if attempt == 0:
            say("    · %s 맺음말이 비어 다시 요청" % topic["term"])
    if drafted is None:
        return None

    links, seen = [], set()
    for a in topic["articles"]:
        if a.url in seen:
            continue
        seen.add(a.url)
        links.append(
            {
                "label": a.source_name,
                "url": a.url,
                "title": a.title,
                "date": a.published.strftime("%Y-%m-%d") if a.published else "",
            }
        )
        if len(links) >= 8:
            break

    return Item(
        no=no,
        field_label="기술 동향",
        audience=drafted["audience"],
        impact=drafted["impact"],
        title=drafted["title"],
        source_label=drafted["source_label"],
        body=drafted["body"],
        note_kind="시사점",
        notes=drafted["notes"],
        links=links,
        track="dev",
        synthesis=True,
        why=[
            "%s 등 %d개 자료에서 반복 언급"
            % (" · ".join(topic["sources"][:3]), len(topic["articles"])),
            "%s %d곳에 걸친 화제"
            % (" · ".join(topic["platforms"][:3]), len(topic["platforms"]))
            if topic["platforms"] else "여러 매체가 함께 다룬 주제",
            "개별 기사가 아니라 흩어진 소식을 모아 정리한 항목",
        ],
        origin_keys=[a.key for a in topic["articles"]],
    )


def add_trend_items(issue: Issue, topics: List[Dict[str, Any]],
                    cfg: Optional[Config] = None,
                    progress: Optional[Callable[[str], None]] = None) -> Issue:
    """핫이슈 종합 항목을 만들어 앞쪽에 넣는다."""
    cfg = cfg or load()
    say = progress or (lambda m: None)
    if not topics or not cfg.get("llm.enabled", True) or not llm.available():
        return issue

    say("  · 이번 주 핫이슈 %d건 조사·종합 중…" % len(topics))
    made = []
    for tp in topics:
        item = make_trend_item(tp, 0, progress=say)
        if item:
            item.locked = True     # polish() 가 다시 쓰지 않도록
            made.append(item)
            say("    ◆ %s — %s" % (tp["term"], item.title[:44]))
    if not made:
        return issue

    total = int(cfg.get("compose.total_max", 6))
    issue.items = (issue.items[: total - len(made)]) + made
    for i, it in enumerate(issue.items, 1):
        it.no = i
    return issue
