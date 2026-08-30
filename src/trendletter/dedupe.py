"""같은 사건을 다룬 기사를 하나의 이슈로 통합한다.

기사 수가 아니라 '보도한 매체 수'를 중요도 신호로 남기는 것이 목적이다.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import Article, Cluster

# 정책브리핑은 검색어·기간이 URL 에 남아 같은 기사가 여러 주소로 잡힌다.
# 기사를 식별하는 파라미터만 남긴다.
_ID_PARAMS = ("newsId", "nttSn", "idxno", "no", "id", "articleId")


def normalize_url(url: str) -> str:
    try:
        u = urlparse(url)
    except ValueError:
        return url
    keep = [(k, v) for k, v in parse_qsl(u.query) if k in _ID_PARAMS]
    return urlunparse((u.scheme, u.netloc, u.path, "", urlencode(keep), ""))


_STOP = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|[\"'“”‘’·…,\.\-–—:;!?\|]|"
    r"(단독|속보|종합|영상|사진|인터뷰|기고|칼럼|오늘의|주간)"
)
_NUM = re.compile(r"\d+")


def normalize(title: str) -> str:
    t = _STOP.sub(" ", title or "")
    t = _NUM.sub("#", t)
    return " ".join(t.split()).lower()


def _tokens(title: str):
    return {w for w in normalize(title).split() if len(w) > 1}


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    # 한국어 기사 제목은 어순 변화가 커서 토큰 겹침을 더 신뢰한다.
    return max(ratio, 0.35 * ratio + 0.65 * jaccard)


_HANGUL = re.compile(r"[가-힣]")

# 제목만으로는 '같은 사람이 주재한 다른 회의' 같은 짝을 구분할 수 없다.
# 실제로 희소 고유명사 규칙을 시험했더니 독파모·모두의 AI 는 올바르게 묶였지만
# 국무총리 주재 회의 3건과 구글 제미나이 별건 기사가 함께 묶였다(2026-08-29).
#
# 병합을 놓치면 초안에 두 번 보이므로 담당자가 지우면 되지만,
# 잘못 병합하면 기사 하나가 조용히 사라져 알아채기 어렵다.
# 따라서 병합은 보수적으로 하고, 애매한 짝은 경고로만 알린다.
NEAR_LOW = 0.35


def near_duplicates(clusters, threshold: float = 0.72):
    """서로 같은 사건일 수 있는 묶음 쌍을 (i, j, 점수) 로 돌려준다.

    병합하지 않고 편집기에서 '유사 항목' 으로 표시하는 데 쓴다.
    """
    pairs = []
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            a, b = clusters[i].lead.title, clusters[j].lead.title
            if not (_HANGUL.search(a) and _HANGUL.search(b)):
                continue
            s = similarity(a, b)
            if NEAR_LOW <= s < threshold:
                pairs.append((i, j, round(s, 3)))
    return pairs


def merge_by_entity(clusters: List[Cluster], entities_of, newsiness,
                    min_sim: float = 0.30, is_product=None):
    """같은 모델·도구 이름을 다루는 이슈를 한 건으로 합친다.

    제목 유사도만으로는 영어 기사가 묶이지 않는다. 실제로 GLM 관련 소식이
    7개 이슈로 흩어졌고(2026-08-30), 그 결과 개인 경험담이 대표로 올라갔다.
    이름이 같고 제목이 최소한이라도 겹치면 한 사건으로 본다.

    대표 기사는 '소식다운' 제목을 먼저 고른다(newsiness).
    """
    is_product = is_product or (lambda _n: True)
    # 이름 하나에 여러 이슈가 달릴 수 있다. 첫 이슈와만 견주면
    # 표현이 다른 세 번째·네 번째 이슈가 영영 묶이지 않는다.
    buckets: Dict[str, List[Cluster]] = {}
    order: List[Cluster] = []

    for c in clusters:
        names = entities_of(c.lead.title)
        target = None
        for name in sorted(names):
            # 회사 이름만 같은 경우는 서로 다른 소식일 때가 많아 기준을 높인다
            need = min_sim if is_product(name) else max(min_sim, 0.55)
            best, best_sim = None, 0.0
            for holder in buckets.get(name, ()):
                s = max(similarity(c.lead.title, a.title) for a in holder.articles)
                if s >= need and s > best_sim:
                    best, best_sim = holder, s
            if best is not None:
                target = best
                break
        if target is not None:
            target.articles.extend(c.articles)
            # 합쳐진 이슈의 이름도 같은 통에 달아 둔다
            for name in entities_of(c.lead.title):
                lst = buckets.setdefault(name, [])
                if target not in lst:
                    lst.append(target)
        else:
            order.append(c)
            for name in names:
                buckets.setdefault(name, []).append(c)

    for c in order:
        # 소식다운 제목 → 최신 → 주력 매체 순으로 대표를 고른다
        role_rank = {"must": 0, "primary": 1, "verify": 2, "discover": 3}
        c.articles.sort(
            key=lambda a: (
                -newsiness(a.title),
                role_rank.get(a.raw.get("role", "primary"), 1),
                -(a.published.timestamp() if a.published else 0),
            )
        )
    return order


def cluster(articles: List[Article], threshold: float = 0.72) -> List[Cluster]:
    """URL 완전중복 제거 후 제목 유사도로 묶는다. 대표 기사는 주력 매체·최신 순."""
    by_url = {}
    for a in articles:
        by_url.setdefault(normalize_url(a.url), a)
    # 같은 매체가 같은 제목을 여러 경로로 내보내는 경우도 한 건으로 본다.
    by_title = {}
    for a in by_url.values():
        by_title.setdefault((a.source_name, normalize(a.title)), a)
    items = list(by_title.values())

    clusters: List[Cluster] = []
    for art in items:
        placed = False
        for cl in clusters:
            if any(similarity(art.title, x.title) >= threshold for x in cl.articles):
                cl.articles.append(art)
                placed = True
                break
        if not placed:
            clusters.append(Cluster(articles=[art]))

    role_rank = {"must": 0, "primary": 1, "verify": 2, "discover": 3}
    for cl in clusters:
        cl.articles.sort(
            key=lambda a: (
                role_rank.get(a.raw.get("role", "primary"), 1),
                -(a.published.timestamp() if a.published else 0),
            )
        )
    return clusters
