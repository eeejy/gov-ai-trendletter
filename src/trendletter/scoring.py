"""온톨로지 태깅과 중요도 점수.

점수는 '자동 선별'이 아니라 '사람이 볼 순서'를 정하기 위한 것이다.
role=must 소스는 점수와 무관하게 후보에 남는다.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List

from .config import Config
from .models import Cluster

BOOST_WEIGHT = {"강함": 3.0, "보통": 1.5, "약함": 0.5}

# 영어권 개발자 소스는 한글 키워드에 걸리지 않으므로 별도로 본다.
_EN_AI = re.compile(
    r"\b(ai|llm|gpt|claude|gemini|llama|agent|rag|model|inference|"
    r"transformer|openai|anthropic|deepseek|qwen|mistral|gpu|cuda)\b",
    re.I,
)


def _count(keywords, text_low) -> int:
    return sum(1 for kw in keywords if str(kw).lower() in text_low)


def ai_focus(cluster: Cluster, cfg: Config) -> float:
    """AI가 기사의 주제인지, 스쳐 지나가는 언급인지를 구분한다.

    '해양안전 콘텐츠 공모전' 본문에 '인공지능콘텐츠' 가 한 번 나온다고 해서
    AI 동향지 항목이 되지는 않는다. 제목 적중을 크게 본다.
    """
    core = cfg.get("filter.ai_core", []) or []
    adjacent = cfg.get("filter.ai_adjacent", []) or []

    titles = " ".join(a.title for a in cluster.articles)
    bodies = " ".join(
        a.summary + " " + " ".join(str(t) for t in a.tags) for a in cluster.articles
    )
    tl, bl = titles.lower(), bodies.lower()

    score_ = 0.0
    if _count(core, tl) or _EN_AI.search(titles):
        score_ += 4.0
    if _count(adjacent, tl):
        score_ += 1.5
    score_ += min(_count(core, bl), 3) * 1.0
    score_ += min(_count(adjacent, bl), 2) * 0.3
    return score_


# 주간 검색량 집계, 시황 요약, 주가 기사는 '동향'이 아니라 목록이다.
_NOT_ISSUE = re.compile(
    r"이슈\s*트렌드|주간\s*(이슈|정리|브리핑)|한\s*주\s*(정리|요약)|"
    r"주가|증시|코스피|코스닥|시황|급등|급락|오늘의|이번\s*주\s*인기"
)


# 시청 위주 행사 안내 / 직원이 참여할 수 있는 행사
_EVENT_PROMO = re.compile(r"특강|세미나|웨비나|강연|설명회|간담회|즐겨요|보러오세요|시청")
_JOINABLE = re.compile(r"경진대회|공모전|해커톤|공모|모집|접수|참가|아이디어")


def is_ai_related(cluster: Cluster, cfg: Config) -> bool:
    """AI 정보동향지이므로 AI가 주제가 아닌 자료는 후보에서 뺀다.

    해양경찰청 보도자료처럼 게시판 전체를 가져오는 소스가 있어 반드시 필요하다.
    빠뜨린 항목은 편집기의 후보 목록에서 사람이 직접 추가할 수 있다.
    """
    if _NOT_ISSUE.search(cluster.lead.title or ""):
        return False
    if not cfg.get("filter.require_ai", True):
        return True
    return ai_focus(cluster, cfg) >= 3.5


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
        in_title = any(str(k).lower() in tl for k in group.get("keywords", []))
        in_body = any(str(k).lower() in bl for k in group.get("keywords", []))
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
    if _EVENT_PROMO.search(title_) and not _JOINABLE.search(title_):
        reasons["promo"] = -2.5

    # 7) 우리 기관 관련 수집원(해양경찰청·해양수산부)은 소스 기준으로 가산한다
    roles = {a.raw.get("role") for a in cluster.articles}
    reasons["must"] = 4.0 if "must" in roles else 0.0

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
    return cluster.lead.track == "dev" and bool(_ANNOUNCE.search(title))


def select(clusters: List[Cluster], cfg: Config) -> List[Cluster]:
    """트랙별 최소·최대 건수를 지키면서 전체 상한까지 고른다."""
    quota = cfg.get("compose.quota", {}) or {}
    total_max = int(cfg.get("compose.total_max", 6))

    chosen: List[Cluster] = []
    used = set()

    def ok(c: Cluster) -> bool:
        return c.lead.track != "dev" or dev_eligible(c, cfg)

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

    chosen.sort(key=lambda c: (["policy", "industry", "dev"].index(c.lead.track), -c.score))
    return chosen[:total_max]


# ---------------------------------------------------------------- 개발자 트랙
#
# 새로 만들어진 저장소를 별 수로 줄세우면 'AI 워터마크 제거기' 같은 단발성
# 도구가 올라온다. 실제로 뜨는 기술은 여러 플랫폼에서 같은 이름이 동시에
# 오르내린다(2026-08-29: Ox Alpha·GLM 이 HN·Reddit·ZDNet 에 함께 등장).
# 그래서 '이름이 몇 개 플랫폼에 걸쳐 나오는가' 를 개발자 트랙의 신호로 쓴다.

_VENDOR = (
    r"GPT|Claude|Gemini|Llama|Qwen|DeepSeek|GLM|Kimi|Mistral|MiniMax|Grok|"
    r"Phi|Nova|Titan|Command R|Ox Alpha|Sora|Midjourney|Stable Diffusion|"
    r"Whisper|Copilot|Cursor|Codex|Devin|opencode|Ollama|vLLM|LangChain|"
    r"제미나이|클로드|딥시크|라마|큐원|지푸|오픈AI|앤트로픽|엔비디아"
)
_ENTITY_PATTERNS = [
    # 벤더·모델 이름 + 뒤따르는 버전 (GLM-5.3, Gemini 3.5, Kimi K3)
    re.compile(r"\b(%s)\b[-\s]?([A-Za-z]?\d[\d.]*)?" % _VENDOR, re.I),
    # 대문자로 시작하고 버전이 붙은 제품명 (Ox Alpha, OpenBot 2)
    re.compile(r"\b([A-Z][A-Za-z]{2,})[-\s](\d[\d.]*)\b"),
]

# 플랫폼 묶음. 국내 매체가 함께 다루면 그것도 별개 신호로 센다.
_PLATFORM = {
    "hackernews": "HN",
    "reddit": "Reddit",
    "github_trending": "GitHub",
    "aitimes": "국내매체",
    "zdnet": "국내매체",
    "itnewsmoa": "국내매체",
}


# 소문자 기본형 → 실제 표기. 'ox alpha' 대신 'Ox Alpha' 로 보여주기 위한 것.
_DISPLAY: Dict[str, str] = {}


def entities(text: str) -> set:
    """제목에서 모델·도구 이름을 뽑아 소문자 기본형으로 돌려준다."""
    found = set()
    for pat in _ENTITY_PATTERNS:
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
_VENDOR_ONLY = {
    "앤트로픽", "엔비디아", "오픈ai", "구글", "메타", "마이크로소프트",
    "네이버", "카카오", "삼성", "지푸",
    "anthropic", "openai", "google", "nvidia", "meta", "microsoft",
}


def is_product(name: str) -> bool:
    return name.lower() not in _VENDOR_ONLY


def entity_platforms(articles) -> Dict[str, set]:
    """이름별로 어떤 플랫폼에서 언급됐는지 모은다."""
    table: Dict[str, set] = {}
    for a in articles:
        platform = _PLATFORM.get(a.source_id)
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
    work = (cluster.onto or {}).get("업무 분야") or []
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

    if r.get("promo", 0):
        out.append("행사 안내 성격 (참고용)")

    if rank_ and total:
        label = {"policy": "정책", "industry": "산업", "dev": "개발자"}.get(
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

_STOP_KO = set(
    "인공지능 지능 위해 통해 대한 있는 없는 이번 관련 지원 추진 개발 기술 서비스 사업 "
    "모델 데이터 국내 정부 발표 확대 강화 방안 계획 도입 활용 구축 운영 제공 시행 "
    "예정 방침 밝혀 위한 대해 따른 통한 최초 처음 오는 지난 올해 내년 우리 국민 "
    "기업 산업 시장 분야 중심 기반 신규 주요 전체 대상 결과 참여 진행 협력 체결 "
    "공개 출시 선정 개최 실시 마련 검토 논의 확인 시작 완료 성공 최대 최고 급증 "
    "보도자료 참고자료 브리핑 정례 회의 간담회 등 시대 현장 방식 경우 이후 이상 "
    "가능 필요 예상 전망 지난해 올해도 관계자 이날 대비 수준 규모 위원회 장관 "
    "차관 청장 국장 실장 과장 팀장 본격 본격화 글로벌 세계 상보 종합 속보 단독 "
    "전문가 관계 국가 한국 중국 미국 일본 대신 통제 고속도로 공모 이슈 트렌드 "
    "지역 지방 서울 부산 인천 경기 이용 사용 제작 공유 확산 연구 조사 평가".split()
)
_STOP_EN = set(
    "and for to is with in on the of that a an it its by from as at or be are was "
    "this these those you your we our they their he she his her not but if then "
    "how why what when where who which can will just new now more most all any "
    "using use used make made get got has have had do does did about into over "
    "own let via vs no yes out up down off than very much some other".split()
)
_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{2,}")

# 개발자 트랙은 영어 제목이라 일반 낱말(model·code·free…)이 상위를 덮는다.
# 영어는 '모델·도구 이름' 이거나 아래 목록에 있을 때만 키워드로 인정한다.
_EN_ALLOW = {
    "agent", "agents", "opensource", "benchmark", "inference", "rag",
    "multimodal", "reasoning", "finetuning", "quantization", "context",
    "coding", "robotics", "vision", "voice", "safety", "alignment",
}
# 같은 뜻의 표기를 하나로 모은다
_ALIAS = {
    "agents": "agent", "models": "model", "tools": "tool",
    "에이전트": "agent", "오픈소스": "opensource",
}


def _norm_word(w: str) -> str:
    w = w.strip(".,-·")
    return _ALIAS.get(w.lower(), w.lower())


def keywords(clusters: List[Cluster], picked: List[Cluster], limit: int = 24) -> List[Dict]:
    """이번 주 뜨는 키워드.

    - 여러 이슈에 걸쳐 나온 낱말일수록 위로 올린다
    - 실제로 게재한 항목에 나온 낱말은 가중치를 준다
    - 모델·도구 이름(entities)은 표기를 살려 따로 표시한다
    """
    from collections import Counter

    picked_ids = {id(c) for c in picked}
    count: Counter = Counter()
    track_of: Dict[str, str] = {}
    in_picked = set()

    for c in clusters:
        title = c.lead.title
        title_entities = {e.lower() for e in entities(title)}
        seen = set()
        for raw_w in _WORD.findall(title):
            key = _norm_word(raw_w)
            if not key or len(key) < 2:
                continue
            if key in _STOP_EN or key in _STOP_KO or raw_w in _STOP_KO:
                continue
            is_ko = bool(_HANGUL_W.search(key))
            if not is_ko and key not in _EN_ALLOW and key not in title_entities:
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
                "track": track_of.get(key, "industry"),
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
_TERM_ALIAS = {
    "agent": "에이전트", "에이전트": "에이전트",
    "llm": "LLM", "대규모언어모델": "LLM",
    "rag": "RAG",
    "mcp": "MCP",
    "소버린": "소버린 AI",
    "파운데이션모델": "파운데이션 모델",
    "생성형": "생성형 AI",
    "멀티모달": "멀티모달",
    "오픈소스": "오픈소스",
    "파인튜닝": "파인튜닝",
}
_TERM_DISPLAY = {"에이전트": "AI 에이전트"}

# 해설할 것이 마땅치 않은 너무 일반적인 낱말
# 종합 정리 대상으로는 너무 일반적인 말.
# '로봇' 은 여러 매체에 흩어져 나오지만 하나의 사건이 아니다(2026-08-30).
_TOO_BROAD = {
    "보안", "검증", "api", "ide", "자동화", "센서", "개인정보", "윤리",
    "로봇", "드론", "클라우드", "데이터센터", "반도체", "gpu", "빅데이터",
}


def tech_keywords(clusters: List[Cluster], cfg: Config, limit: int = 5) -> List[Dict]:
    """해설할 값어치가 있는 기술·도구 낱말만 고른다.

    화면에 뿌리는 키워드(keywords)는 기관명·일반어까지 포함하지만,
    "이 말이 뭔데?" 에 답할 대상은 기술 용어와 제품 이름이어야 한다.
    과기부·행안부·민간·역량 같은 말은 해설할 것이 없다.
    """
    from collections import Counter

    # 온톨로지 기술·도구 축의 낱말을 해설 대상으로 인정한다
    tech_terms = set()
    for group in ((cfg.ontology.get("axes") or {}).get("기술·도구") or {}).values():
        for kw in group:
            tech_terms.add(str(kw).lower())

    count: Counter = Counter()
    display: Dict[str, str] = {}
    for c in clusters:
        title = c.lead.title
        low = title.lower()
        seen = set()
        # 제품·모델 이름
        for name in entities(title):
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
            canon = _TERM_ALIAS.get(term, term)
            if canon in seen:
                continue
            seen.add(term)
            seen.add(canon)
            count[canon] += 1
            display.setdefault(canon, _TERM_DISPLAY.get(canon, canon))

    out = []
    for key, n in count.most_common(limit * 4):
        if n < 2 or key.lower() in _TOO_BROAD:
            continue
        out.append({"text": display.get(key, key), "count": n, "key": key.lower()})
        if len(out) >= limit:
            break
    return out
