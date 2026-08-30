"""개발자 트랙 수집기: Hacker News · Reddit · GitHub.

단순 언급량이 아니라 '플랫폼 수 × 반응 크기 × 실제 활동'을 신호로 쓰기 위해
각 항목에 원시 지표(points, comments, stars)를 raw 에 담아 둔다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List
from urllib.parse import urlencode

from ..models import Article
from .base import Collector, clean, within

AI_PAT = re.compile(
    r"\b(ai|llm|gpt|claude|gemini|llama|agent|rag|transformer|inference|"
    r"fine.?tun|open.?source model|anthropic|openai|mistral|qwen|deepseek)\b",
    re.I,
)


class HackerNewsCollector(Collector):
    """Algolia HN API. 인증이 필요 없고 points/comments 를 함께 준다."""

    name = "hackernews"
    API = "https://hn.algolia.com/api/v1/search_by_date"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        # Algolia 는 OR/괄호를 검색 문법이 아니라 문자열로 취급한다.
        # 따라서 짧은 질의를 여러 번 던지고 결과를 합친다.
        hits = []
        seen_ids = set()
        for query in self.params.get("queries", ["AI", "LLM", "agent", "open source model"]):
            params = {
                "query": query,
                "tags": "story",
                "numericFilters": "created_at_i>%d,points>%d"
                % (int(since.timestamp()), int(self.params.get("min_points", 100))),
                "hitsPerPage": 30,
            }
            try:
                data = self.fetcher.get_json(self.API + "?" + urlencode(params))
            except Exception:  # noqa: BLE001
                continue
            for hit in data.get("hits", []):
                if hit.get("objectID") not in seen_ids:
                    seen_ids.add(hit.get("objectID"))
                    hits.append(hit)

        out: List[Article] = []
        for hit in hits:
            title = clean(hit.get("title") or "")
            if not title:
                continue
            url = hit.get("url") or "https://news.ycombinator.com/item?id=%s" % hit.get("objectID")
            published = None
            if hit.get("created_at"):
                published = datetime.strptime(hit["created_at"][:19], "%Y-%m-%dT%H:%M:%S")
            if not within(published, since):
                continue
            if not AI_PAT.search(title):
                continue
            out.append(
                self.make(
                    title,
                    url,
                    published=published,
                    summary=clean(hit.get("story_text") or "")[:400],
                    raw={
                        "points": hit.get("points", 0),
                        "comments": hit.get("num_comments", 0),
                        "discussion": "https://news.ycombinator.com/item?id=%s" % hit.get("objectID"),
                    },
                )
            )
        out.sort(key=lambda a: a.raw.get("points", 0), reverse=True)
        return out[:limit]


class RedditCollector(Collector):
    """서브레딧 주간 상위 글.

    2026-08-29 확인: .json / api.reddit.com 은 403 으로 차단되고 Atom 피드만 열린다.
    Atom 에는 추천수·댓글수가 없으므로 '주간 top 진입' 자체를 반응 신호로 쓰고,
    반응 크기 비교는 Hacker News 지표와 플랫폼 수로 대신한다.
    """

    name = "reddit"
    FEED = "https://www.reddit.com/r/%s/top/.rss?t=week"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        from bs4 import BeautifulSoup

        out: List[Article] = []
        for sub in self.params.get("subreddits", []):
            try:
                xml = self.fetcher.get(self.FEED % sub)
            except Exception:  # noqa: BLE001
                continue
            soup = BeautifulSoup(xml, "xml")
            for rank_, entry in enumerate(soup.find_all("entry"), 1):
                title = clean(entry.title.get_text() if entry.title else "")
                link_el = entry.find("link")
                url = link_el.get("href") if link_el else ""
                if not title or not url:
                    continue
                published = None
                if entry.updated:
                    try:
                        published = datetime.strptime(
                            entry.updated.get_text()[:19], "%Y-%m-%dT%H:%M:%S"
                        )
                    except ValueError:
                        published = None
                if not within(published, since, slack_days=2):
                    continue
                out.append(
                    self.make(
                        title,
                        url,
                        published=published,
                        tags=["r/" + sub],
                        raw={"subreddit": sub, "weekly_rank": rank_},
                    )
                )
        # 주간 순위가 높을수록 앞에 둔다.
        out.sort(key=lambda a: a.raw.get("weekly_rank", 99))
        return out[:limit]


class GithubCollector(Collector):
    """최근 생성된 AI 저장소 중 별이 많이 붙은 것. 개발자 신호의 실사용 검증용."""

    name = "github"
    API = "https://api.github.com/search/repositories"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        # 최근 7일 생성 + topic:ai 는 사실상 결과가 없다(2026-08-29 확인).
        # '최근 N일 안에 만들어져 이미 별이 많이 붙은 저장소'를 급부상 신호로 본다.
        from datetime import timedelta

        window = int(self.params.get("created_days", 30))
        born = (datetime.now() - timedelta(days=window)).date().isoformat()
        q = self.params.get("query") or "created:>%s stars:>%d AI in:name,description" % (
            born,
            int(self.params.get("min_stars_gained", 200)),
        )
        url = self.API + "?" + urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": 20})
        try:
            data = self.fetcher.get_json(url)
        except Exception:  # noqa: BLE001
            return []
        out: List[Article] = []
        for repo in data.get("items", []):
            published = None
            if repo.get("created_at"):
                published = datetime.strptime(repo["created_at"][:19], "%Y-%m-%dT%H:%M:%S")
            out.append(
                self.make(
                    "%s — %s" % (repo.get("full_name", ""), repo.get("description") or ""),
                    repo.get("html_url", ""),
                    published=published,
                    summary=clean(repo.get("description") or ""),
                    tags=repo.get("topics", []),
                    raw={"stars": repo.get("stargazers_count", 0)},
                )
            )
        return out[:limit]


class OpenRouterCollector(Collector):
    """OpenRouter 공개 랭킹. 엔드포인트 확인 후 활성화한다(docs/sources.md)."""

    name = "openrouter"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        return []
