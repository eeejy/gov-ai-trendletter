"""온톨로지 태깅과 중요도 점수.

점수는 '자동 선별'이 아니라 '사람이 볼 순서'를 정하기 위한 것이다.
role=must 소스는 점수와 무관하게 후보에 남는다.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List

from .config import Config, load
from .models import Cluster

# 예전에는 이 자리에 AI 낱말 정규식이 박혀 있었다. 키워드를 전부 바꿔도
# 영문 AI 낱말이 계속 관문을 통과시켜, 분야를 바꿀 수가 없었다.
# 이제 분야가 정한다 — profiles/<분야>/profile.yaml 의 filter.core_en.


def _agency_subject(cfg: Config):
    """제목이 기관 이름으로 시작하는지 보는 정규식.

    기관 목록을 코드에 두지 않고 **온톨로지의 기관 축에서 만든다.**
    분야가 바뀌면 그 분야의 기관들이 자동으로 들어온다.
    """
    key = "_rx_agency_subject"
    got = getattr(cfg, key, None)
    if got is not None:
        return got
    axis = (cfg.ontology.get("meta") or {}).get("org_axis", "기관")
    names = set()
    for group in ((cfg.ontology.get("axes") or {}).get(axis) or {}).values():
        for kw in group or []:
            w = str(kw).strip()
            if len(w) >= 2 and not w.isdigit():
                names.add(w)
    names.update(str(x) for x in (cfg.prof("penalties.agency_extra") or []))
    rx = (re.compile(r"^\s*(%s)" % "|".join(sorted(map(re.escape, names), key=len,
                                                   reverse=True)))
          if names else None)
    setattr(cfg, key, rx)
    return rx


def _penalty(cfg: Config, name: str, title: str) -> float:
    """분야가 정한 감점 규칙 하나를 적용한다."""
    rule = cfg.prof("penalties.%s" % name) or {}
    rx = cfg.rx("penalties.%s.pattern" % name)
    if not rx or not rx.search(title or ""):
        return 0.0
    unless = cfg.rx("penalties.%s.unless" % name)
    if unless and unless.search(title or ""):
        return 0.0
    keep = rule.get("unless_contains")
    if keep and str(keep) in (title or ""):
        return 0.0
    if rule.get("unless_agency_subject"):
        agency = _agency_subject(cfg)
        if agency and agency.search(title or ""):
            return 0.0
    return float(rule.get("score") or 0.0)


def _count(keywords, text_low) -> int:
    return sum(1 for kw in keywords if str(kw).lower() in text_low)


def topic_focus(cluster: Cluster, cfg: Config) -> float:
    """이 분야가 기사의 주제인지, 스쳐 지나가는 언급인지를 구분한다.

    '해양안전 콘텐츠 공모전' 본문에 '인공지능콘텐츠' 가 한 번 나온다고 해서
    AI 동향지 항목이 되지는 않는다. 제목 적중을 크게 본다.

    낱말과 가중치는 전부 분야가 정한다.
    """
    core = cfg.get("filter.core", []) or []
    adjacent = cfg.get("filter.adjacent", []) or []
    w = cfg.get("filter.weights", {}) or {}
    core_en = cfg.rx("filter.core_en")

    titles = " ".join(a.title for a in cluster.articles)
    bodies = " ".join(
        a.summary + " " + " ".join(str(t) for t in a.tags) for a in cluster.articles
    )
    tl, bl = titles.lower(), bodies.lower()

    core_in_title = bool(_count(core, tl)) or bool(core_en and core_en.search(titles))
    core_in_body = _count(core, bl)

    score_ = 0.0
    if core_in_title:
        score_ += float(w.get("title_core", 4.0))
    if _count(adjacent, tl):
        # adjacent 는 단독으로 부족하다는 것이 원래 설계였는데 코드는 그러지
        # 않았다. 어느 쪽으로 할지 분야가 고른다 (기존 동작은 false).
        if not cfg.get("filter.adjacent_needs_core", False) or core_in_body or core_in_title:
            score_ += float(w.get("title_adjacent", 1.5))
    score_ += min(core_in_body, int(w.get("body_core_cap", 3))) * float(w.get("body_core", 1.0))
    score_ += (min(_count(adjacent, bl), int(w.get("body_adjacent_cap", 2)))
               * float(w.get("body_adjacent", 0.3)))
    return score_


# 예전 이름. 다른 모듈이 부르고 있어 남겨 둔다.
ai_focus = topic_focus


def passes_gate(cluster: Cluster, cfg: Config) -> bool:
    """이 분야의 자료가 아닌 것을 후보에서 뺀다.

    기관 게시판처럼 전체를 가져오는 수집원이 있어 반드시 필요하다.
    빠뜨린 항목은 편집기의 후보 목록에서 사람이 직접 추가할 수 있다.
    """
    exclude = cfg.rx("filter.exclude_title")
    if exclude and exclude.search(cluster.lead.title or ""):
        return False
    if not cfg.get("filter.require_topic", cfg.get("filter.require_ai", True)):
        return True
    return topic_focus(cluster, cfg) >= float(cfg.get("filter.min_focus", 3.5))


# 예전 이름. pipeline 이 import 하고 있어 남겨 둔다.
is_ai_related = passes_gate


def tag(cluster: Cluster, ontology: Dict[str, Any]) -> Dict[str, List[str]]:
    """제목+요약에서 온톨로지 4개 축의 값을 찾아 붙인다."""
    text = " ".join(
        [a.title + " " + a.summary + " " + " ".join(a.tags) for a in cluster.articles]
    ).lower()
    found: Dict[str, List[str]] = {}
    for axis, groups in (ontology.get("axes") or {}).items():
        hits = []
        for label, keywords in groups.items():
            for kw in keywords:
                # YAML 이 119 같은 값을 정수로 읽는 경우가 있어 문자열로 맞춘다.
                if str(kw).lower() in text:
                    hits.append(label)
                    break
        if hits:
            found[axis] = hits
    return found


def priority_topic(cluster: Cluster, ontology: Dict[str, Any]) -> tuple:
    """최우선 주제(국가AI전략위 등)를 내용으로 판정한다.

    수집원 기준으로 가산하면, 검색으로 딸려 들어온 무관한 자료까지 우대받는다.
    반대로 다른 수집원(AI타임스·서울AI플랫폼)으로 들어온 전략위 자료는 놓친다.
    그래서 어디로 들어왔든 내용을 보고 판정한다.
    """
    title = " ".join(a.title for a in cluster.articles).lower()
    body = " ".join(a.summary for a in cluster.articles).lower()

    best, name = 0.0, ""
    for topic, spec in (ontology.get("priority_topics") or {}).items():
        kws = [str(k).lower() for k in spec.get("keywords", [])]
        if any(k in title for k in kws):
            score_ = float(spec.get("title_weight", 4.0))
        elif any(k in body for k in kws):
            score_ = float(spec.get("body_weight", 1.5))
        else:
            continue
        if score_ > best:
            best, name = score_, topic
    return best, name


# 숫자로만 된 낱말은 아무 데나 걸린다. 실측: 「교수 119명」 이 소방청의 '119' 에
# 걸려 대학 교육 기사가 유사기관 관련도를 받았다. 숫자 낱말은 앞뒤를 따진다.
# 실측: 「갤럭시 북6, 119만원부터」의 119 가 소방청으로 잡혀 노트북 기사
# 네 건이 10~13위를 차지했다. 수량·금액 단위를 넓게 잡는다.
_COUNTER = "명개건원년월일차호배만천억조원위대장권부점"


def _kw_hit(needle: str, hay: str) -> bool:
    if not needle:
        return False
    if not needle.isdigit():
        return needle in hay
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        before = hay[i - 1] if i else ""
        after = hay[i + len(needle)] if i + len(needle) < len(hay) else ""
        # 앞뒤가 숫자면 다른 수의 일부, 뒤가 단위면 개수를 센 것이다
        if not (before.isdigit() or after.isdigit() or after in _COUNTER):
            return True
        start = i + 1


def work_relevance(cluster: Cluster, ontology: Dict[str, Any]) -> tuple:
    """우리청이 이걸 쓸 자리가 있는지를 본다.

    '기사에 바다 낱말이 있나' 로 보면 AI 기사에서는 거의 걸리지 않는다.
    지난 11개호를 보면 실제로 고른 주제는 현업(수색·관제·방제)보다
    공공부문 AI 전환·인재교육·유사기관 사례가 훨씬 많았다. 그 성격을 그대로 옮겼다.

    돌려주는 값: (점수, 걸린 그룹 이름들)
    """
    title = " ".join(a.title for a in cluster.articles)
    body = " ".join(a.summary for a in cluster.articles)
    # 부처코드로 가져온 자료는 발행 기관이 정확히 남는다.
    # 제목에 '경찰청' 이 없고 '경찰' 만 있어도 기관을 알아볼 수 있다.
    orgs = " ".join(
        [(a.raw.get("dept") or "") + " " + a.source_name for a in cluster.articles]
    )
    tl, bl = (title + " " + orgs).lower(), body.lower()

    score = 0.0
    hits: List[str] = []
    found: List[tuple] = []
    for name, group in (ontology.get("work_relevance") or {}).items():
        weight = float(group.get("weight", 1.0))
        # context 가 있으면, 그 낱말이 함께 나올 때만 인정한다.
        # 수색·실종·구조 같은 일반어가 엉뚱한 기사에 걸리는 것을 막는다.
        context = group.get("context")
        if context and not any(str(c).lower() in tl or str(c).lower() in bl for c in context):
            continue
        in_title = any(_kw_hit(str(k).lower(), tl) for k in group.get("keywords", []))
        in_body = any(_kw_hit(str(k).lower(), bl) for k in group.get("keywords", []))
        if in_title:
            found.append((weight, name))
        elif in_body:
            found.append((weight * 0.4, name))

    # 여러 관점에 걸리면 그만큼 관련이 크지만, 그대로 더하면 기관명이 많이 나오는
    # 합동 보도자료가 과대평가된다. 가장 큰 것을 온전히 두고 나머지는 절반만 센다.
    found.sort(reverse=True)
    for i, (w, name) in enumerate(found):
        score += w if i == 0 else w * 0.5
        hits.append(name)
    return min(score, 12.0), hits


# 이전 이름을 쓰는 곳이 있어 남겨 둔다
def maritime_relevance(cluster: Cluster, ontology: Dict[str, Any]) -> float:
    return work_relevance(cluster, ontology)[0]


def score(cluster: Cluster, cfg: Config, heat: Dict[str, set] = None) -> Cluster:
    reasons: Dict[str, float] = {}

    # 1) 소스 가중치 — 대표 기사 기준
    weights = {s["id"]: float(s.get("weight", 1.0)) for s in cfg.sources}
    reasons["source"] = max(weights.get(a.source_id, 1.0) for a in cluster.articles) * 2.0

    # 2) 복수 매체 보도 — 기사 수가 아니라 매체 수
    outlets = len(cluster.outlets)
    reasons["outlets"] = min(outlets - 1, 3) * 1.5

    # 3) AI 중심성 — 관문 역할이 주된 목적이라 순위 기여는 절반만 반영한다
    #    (상위권 값이 4~9에 몰려 변별력이 낮았다)
    reasons["ai_focus"] = ai_focus(cluster, cfg) * 0.5

    # 4) 업무 관련도 — 동향지의 존재 이유이므로 크게 본다
    cluster.onto = tag(cluster, cfg.ontology)
    rel, groups = work_relevance(cluster, cfg.ontology)
    reasons["work"] = rel
    cluster.work_groups = groups

    # 5) 온톨로지 적중 폭 — 정책/기술/업무를 두루 건드리면 시사점이 크다
    reasons["ontology"] = len(cluster.onto) * 0.8

    # 6) 개발자 트랙 실측 지표
    #    별 수는 편차가 커서 나눗셈으로는 20만개와 3천개가 같은 값이 된다. 로그로 본다.
    dev = 0.0
    for a in cluster.articles:
        dev += min(a.raw.get("points", 0) / 400.0, 2.0)
        stars = a.raw.get("stars", 0)
        if stars > 500:
            dev += min(math.log10(stars / 500.0), 2.0)
        rank_ = a.raw.get("weekly_rank")
        if rank_:
            dev += max(0.0, 1.5 - (rank_ - 1) * 0.1)
    reasons["dev_signal"] = min(dev, 4.0)

    # 6-1) 여러 플랫폼에 걸친 이름 — 개발자 트랙에서 가장 믿을 만한 신호
    n_platform, name, platforms = dev_heat(cluster, heat or {})
    reasons["cross_platform"] = min(max(0, n_platform - 1) * 1.6, 3.5)
    if n_platform >= 2:
        cluster.hot_entity = {"name": name, "platforms": platforms}

    # 6-2) 시청 위주 행사 안내는 동향 가치가 낮다.
    #      다만 직원이 참여할 수 있는 경진대회·공모전·해커톤은 지난 호에서
    #      꾸준히 실렸으므로 감점하지 않는다.
    title_ = cluster.lead.title
    _done_groups = set()
    for _name in ("event_promo", "vendor_deal", "overseas", "local_peer"):
        _grp = (cfg.prof("penalties.%s.exclusive_group" % _name) or "")
        if _grp and _grp in _done_groups:
            continue          # 같은 갈래는 하나만 매긴다 (원래 elif 였다)
        _hit = _penalty(cfg, _name, title_)
        if _hit:
            reasons[_name] = _hit
            if _grp:
                _done_groups.add(_grp)

    reasons["must"] = 0.0

    # 8) 최우선 주제(국가AI전략위)는 내용 기준으로 가산한다.
    #    범정부 AI 정책의 최상위 방향이라 어느 매체로 들어오든 우대한다.
    pscore, pname = priority_topic(cluster, cfg.ontology)
    reasons["priority"] = pscore
    cluster.priority_topic = pname

    cluster.reasons = reasons
    cluster.score = sum(reasons.values())
    return cluster


# 개인 경험담은 그 주의 '뉴스'가 아니다. 같은 주제라면 발표·공개 소식을 대표로 쓴다.
_PERSONAL = re.compile(
    r"\b(i |i'|my |me |we built|i've|i made|i spent|i let|i tried|how i|"
    r"내가 |해봤|써봤|후기|경험담)", re.I
)
_ANNOUNCE = re.compile(
    r"\b(announce|confirm|release|launch|unveil|introduc|publish|open.?source|"
    r"available|beat|outperform|tops?|ranks?)|공개|발표|출시|확인|선정|제정|착수", re.I
)


def newsiness(title: str) -> float:
    """제목이 얼마나 '소식' 다운지. 대표 기사를 고를 때 쓴다."""
    score = 0.0
    if _ANNOUNCE.search(title or ""):
        score += 2.0
    if _PERSONAL.search(title or ""):
        score -= 3.0
    return score


def rank(clusters: List[Cluster], cfg: Config, articles=None) -> List[Cluster]:
    clusters = [c for c in clusters if is_ai_related(c, cfg)]
    if articles is None:
        articles = [a for c in clusters for a in c.articles]
    heat = entity_platforms(articles)
    scored = [score(c, cfg, heat) for c in clusters]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def dev_eligible(cluster: Cluster, cfg: Config) -> bool:
    """개발자 트랙 항목은 여러 플랫폼에서 확인될 때만 싣는다.

    한 곳에서만 보이는 저장소는 그 주의 '핫한 기술'이 아니라 그날의 화젯거리다.
    """
    need = int(cfg.get("compose.dev_min_platforms", 2))
    info = getattr(cluster, "hot_entity", None)
    return bool(info) and len(info.get("platforms", [])) >= need


def is_tool_news(cluster: Cluster) -> bool:
    """새 모델·도구가 나왔다는 소식인가.

    정책·공공 이슈만 채우면 '무슨 도구가 나왔는지' 를 아예 놓친다.
    실무자가 당장 써 볼 수 있는 정보라 매 호 한 건은 남긴다.
    """
    title = cluster.lead.title
    # 제품·모델 이름이 제목에 있어야 한다. 온톨로지 기술축까지 인정하면
    # '사이버보안 강화' 같은 정책 기사가 전부 도구 소식으로 잡힌다.
    if any(is_product(n) for n in entities(title)):
        return True
    # 개발자 트랙이면서 실제로 무언가가 공개·출시된 소식
    cfg = load()
    need = cfg.track(cluster.lead.track).get("require_cross_platform")
    return bool(need) and bool(_ANNOUNCE.search(title))


def select(clusters: List[Cluster], cfg: Config) -> List[Cluster]:
    """트랙별 최소·최대 건수를 지키면서 전체 상한까지 고른다.

    같은 사건을 여러 매체가 제각각 제목으로 쓴 것은 먼저 걸러 낸다.
    실측: 「경찰청 수사자료 분석 솔루션」이 2·3·5위를 차지했다.
    """
    # 트랙과 쿼터는 분야가 정한다 (profiles/<분야>/profile.yaml 의 tracks).
    quota = {k: cfg.quota(k) for k in cfg.track_keys()}
    total_max = int(cfg.get("compose.total_max", 6))
    if cfg.get("compose.drop_same_event", True):
        clusters = diversify(clusters, take=len(clusters))

    chosen: List[Cluster] = []
    used = set()

    def ok(c: Cluster) -> bool:
        # 여러 플랫폼 확인을 요구하는 트랙만 그 검사를 받는다.
        # 예전엔 트랙 이름 'dev' 가 코드에 박혀 있었다.
        need = cfg.track(c.lead.track).get("require_cross_platform")
        return not need or dev_eligible(c, cfg)

    # 1단계: 트랙별 최소 건수 채우기
    for track, bounds in quota.items():
        need = int(bounds[0])
        for c in clusters:
            if need <= 0:
                break
            if id(c) in used or c.lead.track != track or not ok(c):
                continue
            chosen.append(c)
            used.add(id(c))
            need -= 1

    # 1-2단계: 새 모델·도구 소식을 최소 건수만큼 확보한다
    need_tool = int(cfg.get("compose.min_tool_news", 1))
    have_tool = sum(1 for c in chosen if is_tool_news(c))
    for c in clusters:
        if have_tool >= need_tool:
            break
        if id(c) in used or not is_tool_news(c) or not ok(c):
            continue
        chosen.append(c)
        used.add(id(c))
        have_tool += 1

    # 2단계: 남은 자리를 점수순으로 채우되 트랙 상한을 넘지 않는다
    counts = {}
    for c in chosen:
        counts[c.lead.track] = counts.get(c.lead.track, 0) + 1
    for c in clusters:
        if len(chosen) >= total_max:
            break
        if id(c) in used:
            continue
        t = c.lead.track
        cap = int(quota.get(t, [0, total_max])[1])
        if counts.get(t, 0) >= cap or not ok(c):
            continue
        chosen.append(c)
        used.add(id(c))
        counts[t] = counts.get(t, 0) + 1

    # 트랙 순서는 프로파일이 적은 차례를 따른다
    _order = {k: i for i, k in enumerate(cfg.track_keys())}
    chosen.sort(key=lambda c: (_order.get(c.lead.track, 99), -c.score))
    return chosen[:total_max]


# ---------------------------------------------------------------- 개발자 트랙
#
# 새로 만들어진 저장소를 별 수로 줄세우면 'AI 워터마크 제거기' 같은 단발성
# 도구가 올라온다. 실제로 뜨는 기술은 여러 플랫폼에서 같은 이름이 동시에
# 오르내린다(2026-08-29: Ox Alpha·GLM 이 HN·Reddit·ZDNet 에 함께 등장).
# 그래서 '이름이 몇 개 플랫폼에 걸쳐 나오는가' 를 개발자 트랙의 신호로 쓴다.

# 제품·모델 이름은 분야가 정한다 (profiles/<분야>/lexicon.yaml 의 vendors).
# 비어 있어도 아래 범용 패턴이 남아 파이프라인이 죽지 않는다.
_GENERIC_ENTITY = re.compile(r"\b([A-Z][A-Za-z]{2,})[-\s](\d[\d.]*)\b")


def _entity_patterns(cfg: Config):
    key = "_rx_entities"
    got = getattr(cfg, key, None)
    if got is not None:
        return got
    vendors = [str(v).strip() for v in (cfg.lex("vendors") or []) if str(v).strip()]
    pats = []
    if vendors:
        joined = "|".join(sorted(map(re.escape, vendors), key=len, reverse=True))
        pats.append(re.compile(r"\b(%s)\b[-\s]?([A-Za-z]?\d[\d.]*)?" % joined, re.I))
    pats.append(_GENERIC_ENTITY)
    setattr(cfg, key, pats)
    return pats


# 소문자 기본형 → 실제 표기. 'ox alpha' 대신 'Ox Alpha' 로 보여주기 위한 것.
_DISPLAY: Dict[str, str] = {}


def entities(text: str, cfg: Config = None) -> set:
    """제목에서 모델·도구 이름을 뽑아 소문자 기본형으로 돌려준다.

    이름 목록은 분야가 정한다. 비어 있으면 범용 패턴(대문자+버전)만 남는다.
    """
    found = set()
    for pat in _entity_patterns(cfg or load()):
        for m in pat.finditer(text or ""):
            raw = (m.group(1) or "").strip()
            name = raw.lower()
            if len(name) >= 3:
                found.add(name)
                _DISPLAY.setdefault(name, raw)
    return found


def display_name(key: str) -> str:
    return _DISPLAY.get(key, key)


# 회사 이름은 같아도 서로 다른 사건인 경우가 많다.
# (앤트로픽의 표준 공개·칩 계약·법원 판결은 전부 별개 소식이다)
# 제품·모델 이름이 같을 때만 같은 사건으로 볼 수 있다.
def is_product(name: str, cfg: Config = None) -> bool:
    """회사 이름은 제품이 아니다. 이것만으로는 같은 사건으로 묶지 않는다."""
    cfg = cfg or load()
    only = {str(x).lower() for x in (cfg.lex("company_only") or [])}
    return name.lower() not in only


def entity_platforms(articles, cfg: Config = None) -> Dict[str, set]:
    """이름별로 어떤 플랫폼에서 언급됐는지 모은다.

    수집원 id → 플랫폼 짝은 sources.yaml 의 platform 필드에서 온다.
    예전엔 이 사전이 코드에 박혀 있어 수집원 id 를 바꾸면 점수가 0이 됐다.
    """
    platforms = (cfg or load()).platforms()
    table: Dict[str, set] = {}
    for a in articles:
        platform = platforms.get(a.source_id)
        if not platform:
            continue
        for name in entities(a.title + " " + a.summary):
            table.setdefault(name, set()).add(platform)
    return table


def dev_heat(cluster: Cluster, table: Dict[str, set]) -> tuple:
    """이 이슈가 몇 개 플랫폼에 걸쳐 있는지와, 그 근거가 된 이름."""
    best_name, best = "", set()
    for a in cluster.articles:
        for name in entities(a.title + " " + a.summary):
            seen = table.get(name, set())
            if len(seen) > len(best):
                best_name, best = name, seen
    return len(best), best_name, sorted(best)


def explain(cluster: Cluster, cfg: Config, rank_: int = 0, total: int = 0) -> List[str]:
    """점수를 사람이 읽을 수 있는 선정 사유로 바꾼다.

    담당자와 독자가 '왜 이게 실렸는지' 를 알 수 있어야 선별 기준을 고칠 수 있다.
    """
    out: List[str] = []
    r = cluster.reasons or {}

    outlets = cluster.outlets
    if len(outlets) > 1:
        out.append("%d개 매체 보도 (%s)" % (len(outlets), " · ".join(outlets[:3])))

    roles = {a.raw.get("role") for a in cluster.articles}
    if "must" in roles:
        names = {a.source_name for a in cluster.articles if a.raw.get("role") == "must"}
        out.append("필수 수집원 (%s)" % " · ".join(sorted(names)[:2]))

    if r.get("priority", 0) >= 4:
        out.append("%s 관련 — 범정부 AI 정책 최상위 방향" % (getattr(cluster, "priority_topic", "") or "최우선 주제"))
    elif r.get("priority", 0):
        out.append("%s 언급" % (getattr(cluster, "priority_topic", "") or "최우선 주제"))

    info = getattr(cluster, "hot_entity", None)
    if info and len(info.get("platforms", [])) >= 2:
        out.append(
            "%s %d곳에서 ‘%s’ 동시 확인"
            % (
                " · ".join(info["platforms"][:3]),
                len(info["platforms"]),
                display_name(info["name"]),
            )
        )

    groups = getattr(cluster, "work_groups", []) or []
    label = {
        "직접": "우리청 직접 관련",
        "유사기관": "유사기관 사례 (경찰·소방 등 현장 집행기관)",
        "인접기관": "업무 인접기관 사례 (해수부·국방·관세)",
        "타기관사례": "타 기관 AI 도입 사례 (참고)",
        "공공전환": "공공부문 AI 전환",
        "인재교육": "직원 교육·경진대회",
        "현장임무": "현장 임무 적용 가능",
        "현장기술": "현장 적용 가능 기술",
        "인프라": "인프라·예산 근거",
    }
    for g in groups[:2]:
        out.append(label.get(g, g))
    work = (cluster.onto or {}).get(
        (load().ontology.get("meta") or {}).get("work_axis", "업무 분야")) or []
    if work and not groups:
        out.append("업무 연관 (%s)" % " · ".join(work[:2]))

    if r.get("ai_focus", 0) >= 5:
        out.append("AI가 기사의 중심 주제")

    dev = r.get("dev_signal", 0)
    if dev >= 2:
        peak = max(
            [a.raw.get("points", 0) for a in cluster.articles]
            + [a.raw.get("stars", 0) for a in cluster.articles]
        )
        if peak:
            out.append("개발자 반응 지표 상위 (%s)" % format(peak, ","))

    if r.get("vendor", 0):
        out.append("업체 수주 소식 (감점)")
    if r.get("overseas", 0):
        out.append("해외 기관 소식 (감점)")
    if r.get("local", 0):
        out.append("지방 조직 소식 (감점)")
    if r.get("promo", 0):
        out.append("행사 안내 성격 (참고용)")

    if rank_ and total:
        label = {t["key"]: t.get("label", t["key"]) for t in load().tracks()}.get(
            cluster.lead.track, "전체"
        )
        out.append("%s 트랙 후보 중 중요도 %d위 (%d개 이슈 중)" % (label, rank_, total))

    if not out:
        out.append("트랙 구성상 보완 선정")
    return out[:4]


# ---------------------------------------------------------------- 뜨는 키워드
#
# 제목에서 낱말을 뽑아 이번 주에 자주 나온 말을 고른다.
# 영어 기능어(and·for·to…)가 상위를 덮지 않도록 불용어를 넉넉히 둔다.

_HANGUL_W = re.compile(r"[가-힣]")

_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{2,}")

# 개발자 트랙은 영어 제목이라 일반 낱말(model·code·free…)이 상위를 덮는다.
# 영어는 '모델·도구 이름' 이거나 아래 목록에 있을 때만 키워드로 인정한다.
def _norm_word(w: str, alias: Dict[str, str] = None) -> str:
    w = w.strip(".,-·")
    return (alias or {}).get(w.lower(), w.lower())


def keywords(clusters: List[Cluster], picked: List[Cluster], limit: int = 24) -> List[Dict]:
    """이번 주 뜨는 키워드.

    - 여러 이슈에 걸쳐 나온 낱말일수록 위로 올린다
    - 실제로 게재한 항목에 나온 낱말은 가중치를 준다
    - 모델·도구 이름(entities)은 표기를 살려 따로 표시한다
    """
    from collections import Counter

    cfg = load()
    stop_ko = {str(x) for x in (cfg.lex("stop_ko") or [])}
    stop_en = {str(x) for x in (cfg.lex("stop_en") or [])}
    allow_en = {str(x).lower() for x in (cfg.lex("allow_en") or [])}
    alias = {str(k).lower(): str(v) for k, v in (cfg.lex("alias") or {}).items()}

    picked_ids = {id(c) for c in picked}
    count: Counter = Counter()
    track_of: Dict[str, str] = {}
    in_picked = set()

    for c in clusters:
        title = c.lead.title
        title_entities = {e.lower() for e in entities(title)}
        seen = set()
        for raw_w in _WORD.findall(title):
            key = _norm_word(raw_w, alias)
            if not key or len(key) < 2:
                continue
            if key in stop_en or key in stop_ko or raw_w in stop_ko:
                continue
            is_ko = bool(_HANGUL_W.search(key))
            if not is_ko and key not in allow_en and key not in title_entities:
                continue
            if key in seen:
                continue
            seen.add(key)
            count[key] += 1
            track_of.setdefault(key, c.lead.track)
            if id(c) in picked_ids:
                in_picked.add(key)

    hot_entities = {e for c in clusters for e in entities(c.lead.title)}

    out = []
    for key, n in count.most_common(limit * 3):
        if n < 2 and key not in in_picked:
            continue
        out.append(
            {
                "text": display_name(key) if key in hot_entities else key,
                "count": n,
                "track": track_of.get(key, (load().track_keys() or ["policy"])[0]),
                "picked": key in in_picked,
                "entity": key in hot_entities,
            }
        )
        if len(out) >= limit:
            break
    # 게재 항목에 나온 낱말과 모델 이름을 앞으로
    out.sort(key=lambda k: (-(k["picked"] * 2 + k["entity"]), -k["count"]))
    return out


def _is_latin(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9.\-]+", s or ""))


# 같은 개념의 한/영 표기를 하나로 모은다
def tech_keywords(clusters: List[Cluster], cfg: Config, limit: int = 5) -> List[Dict]:
    """해설할 값어치가 있는 기술·도구 낱말만 고른다.

    화면에 뿌리는 키워드(keywords)는 기관명·일반어까지 포함하지만,
    "이 말이 뭔데?" 에 답할 대상은 기술 용어와 제품 이름이어야 한다.
    과기부·행안부·민간·역량 같은 말은 해설할 것이 없다.
    """
    cfg = cfg or load()
    term_alias = {str(k).lower(): str(v) for k, v in (cfg.lex("term_alias") or {}).items()}
    term_display = {str(k): str(v) for k, v in (cfg.lex("term_display") or {}).items()}
    too_broad = {str(x).lower() for x in (cfg.lex("too_broad") or [])}
    from collections import Counter

    # 온톨로지 기술·도구 축의 낱말을 해설 대상으로 인정한다
    tech_terms = set()
    for group in ((cfg.ontology.get("axes") or {}).get(
            (cfg.ontology.get("meta") or {}).get("tech_axis", "기술·도구")) or {}).values():
        for kw in group:
            tech_terms.add(str(kw).lower())

    count: Counter = Counter()
    display: Dict[str, str] = {}
    for c in clusters:
        title = c.lead.title
        # 제목만 세면 실제로 화제인 것을 놓친다. 실측: GLM 은 영문 제목 한 건에만
        # 있고 국문 기사는 「Ox 알파…지푸의 신형 AI」처럼 본문에서만 다뤄
        # 1회로 집계돼 핫이슈 후보에도 못 들었다. 본문 앞머리까지 함께 본다.
        body = " ".join((a.summary or "")[:400] for a in c.articles[:3])
        low = (title + " " + body).lower()
        seen = set()
        # 제품·모델 이름
        for name in entities(title + " " + body):
            if not is_product(name) or name in seen:
                continue
            seen.add(name)
            count[name] += 1
            display[name] = display_name(name)
        # 기술 용어. 영문 약어는 낱말 경계를 지켜야 한다.
        # ("ide" 가 video·guide 안에서 잡히던 문제)
        for term in tech_terms:
            if term in seen:
                continue
            if _is_latin(term):
                if not re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(term), low):
                    continue
            elif term not in low:
                continue
            canon = term_alias.get(term, term)
            if canon in seen:
                continue
            seen.add(term)
            seen.add(canon)
            count[canon] += 1
            display.setdefault(canon, term_display.get(canon, canon))

    out = []
    for key, n in count.most_common(limit * 6):
        if n < 2 or key.lower() in too_broad:
            continue
        # 핫이슈 자리는 '이게 뭔지 조사해 설명할' 대상이다. 제품·모델 이름이라야
        # 설명할 것이 있다. 순한글 일반명사(유출·비전·환각)를 낱말 목록으로
        # 하나씩 막는 것은 끝이 없어, 이름처럼 생겼는지로 가른다.
        if not _looks_like_product(key):
            continue
        out.append({"text": display.get(key, key), "count": n, "key": key.lower()})
        if len(out) >= limit:
            break
    return out


def _looks_like_product(term: str) -> bool:
    """제품·모델 이름처럼 생겼나.

    GLM·Kimi·GPT-5·Qwen3 처럼 로마자나 숫자를 품는다. 우리말 이름은
    '지푸AI'·'하이퍼클로바' 처럼 로마자가 섞이거나 네 글자를 넘는 고유명사다.
    '유출'·'비전'·'환각' 같은 두세 글자 일반명사는 걸러진다.
    """
    if re.search(r"[A-Za-z0-9]", term):
        return True
    return len(term) >= 5


# 같은 사건을 여러 매체가 제각각 제목으로 쓰면 제목 유사도로는 안 묶인다.
# 실측: 「경찰청 수사자료 분석 솔루션」 건이 8개 클러스터로 쪼개져 상위를
# 도배했다. 제목쌍 유사도는 0.21~0.28 이라 임계값을 낮추면(0.35) 다른 것까지
# 뭉개진다(클러스터 167→95). 그래서 묶는 대신 고를 때 걸러낸다.
    stop = {str(x).lower() for x in (load().lex('stop_diversify') or [])}
    return {w for w in words if w not in stop and len(w) >= 2}


def diversify(clusters: List[Cluster], take: int, overlap: int = 2) -> List[Cluster]:
    """점수 순으로 훑되, 이미 고른 것과 같은 사건으로 보이면 건너뛴다.

    뜻을 지닌 낱말이 overlap 개 이상 겹치면 같은 사건으로 본다.
    한 사건을 여러 매체가 다뤘다는 사실은 이미 매체 수로 점수에 반영돼 있으므로,
    목록에까지 여러 번 실을 이유가 없다.
    """
    picked: List[Cluster] = []
    marks: List[set] = []
    for c in clusters:
        toks = _tokens(c.lead.title)
        if any(_shared(toks, m) >= overlap for m in marks):
            continue
        picked.append(c)
        marks.append(toks)
        if len(picked) >= take:
            break
    return picked


def _tokens(text: str) -> set:
    """제목에서 뜻을 지닌 낱말만 남긴다."""
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", (text or "").lower())
    stop = {str(x).lower() for x in (load().lex('stop_diversify') or [])}
    return {w for w in words if w not in stop and len(w) >= 2}


def diversify(clusters: List[Cluster], take: int, overlap: int = 2) -> List[Cluster]:
    """점수 순으로 훑되, 이미 고른 것과 같은 사건으로 보이면 건너뛴다.

    뜻을 지닌 낱말이 overlap 개 이상 겹치면 같은 사건으로 본다.
    한 사건을 여러 매체가 다뤘다는 사실은 이미 매체 수로 점수에 반영돼 있으므로,
    목록에까지 여러 번 실을 이유가 없다.
    """
    picked: List[Cluster] = []
    marks: List[set] = []
    for c in clusters:
        toks = _tokens(c.lead.title)
        if any(_shared(toks, m) >= overlap for m in marks):
            continue
        picked.append(c)
        marks.append(toks)
        if len(picked) >= take:
            break
    return picked


def _shared(left: set, right: set) -> int:
    """겹치는 낱말 수. 한국어는 낱말이 붙어 늘어나므로 포함 관계도 센다.

    '경찰' 과 '경찰청', '수사' 와 '수사자료' 는 같은 것을 가리킨다.
    정확히 같은 낱말만 세면 같은 사건이 서로 다른 것으로 보인다.
    """
    n = 0
    for a in left:
        for b in right:
            if a == b or (len(a) >= 2 and len(b) >= 2 and (a in b or b in a)):
                n += 1
                break
    return n
