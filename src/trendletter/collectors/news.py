"""산업 트랙 수집기: AI타임스(RSS) · ZDNet Korea · IT뉴스모아."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Article
from .base import Collector, clean, parse_date, within

_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return clean(_TAG.sub(" ", text or ""))


class RssCollector(Collector):
    """표준 RSS 2.0. AI타임스가 title/link/description/author/pubDate 를 모두 제공한다."""

    name = "rss"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        out: List[Article] = []
        seen = set()
        for feed in self.params.get("feeds", []):
            try:
                xml = self.fetcher.get(feed)
            except RuntimeError:
                continue
            soup = BeautifulSoup(xml, "xml")
            for node in soup.find_all("item"):
                link = clean(node.link.get_text() if node.link else "")
                if not link or link in seen:
                    continue
                published = parse_date(node.pubDate.get_text() if node.pubDate else "")
                if not within(published, since):
                    continue
                seen.add(link)
                out.append(
                    self.make(
                        node.title.get_text() if node.title else "",
                        link,
                        published=published,
                        summary=_strip_html(node.description.get_text() if node.description else "")[:600],
                        author=clean(node.author.get_text() if node.author else ""),
                        tags=[clean(c.get_text()) for c in node.find_all("category")],
                    )
                )
                if len(out) >= limit:
                    return out
        return out


class ZdnetCollector(Collector):
    """ZDNet Korea 키워드 목록(div.newsPost). 실패 시 feedburner 전체 피드로 대체한다."""

    name = "zdnet"
    BASE = "https://zdnet.co.kr"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        out: List[Article] = []
        url = self.params.get("url")
        pages = int(self.params.get("pages", 1))
        for page in range(1, pages + 1):
            page_url = url if page == 1 else "%s&page=%d" % (url, page)
            try:
                html = self.fetcher.get(page_url)
            except RuntimeError:
                break
            soup = BeautifulSoup(html, "lxml")
            posts = soup.select("div.newsPost")
            if not posts:
                break
            for post in posts:
                text_box = post.select_one("div.assetText")
                if not text_box:
                    continue
                a = text_box.find("a", href=True)
                h3 = text_box.find("h3")
                if not a or not h3:
                    continue
                byline = text_box.select_one("p.byline")
                date_txt = ""
                reporter = ""
                if byline:
                    span = byline.find("span")
                    date_txt = clean(span.get_text()) if span else ""
                    ra = byline.find("a")
                    reporter = clean(ra.get_text()) if ra else ""
                published = parse_date(date_txt)
                if not within(published, since):
                    continue
                lead = text_box.find("p")
                out.append(
                    self.make(
                        h3.get_text(),
                        urljoin(self.BASE, a["href"]),
                        published=published,
                        summary=clean(lead.get_text()) if lead else "",
                        author=reporter,
                    )
                )
                if len(out) >= limit:
                    return out
        if not out and self.params.get("fallback_feed"):
            out = self._from_feed(since, limit)
        return out

    def _from_feed(self, since: datetime, limit: int) -> List[Article]:
        try:
            xml = self.fetcher.get(self.params["fallback_feed"])
        except RuntimeError:
            return []
        soup = BeautifulSoup(xml, "xml")
        out: List[Article] = []
        for node in soup.find_all("item"):
            published = parse_date(node.pubDate.get_text() if node.pubDate else "")
            if not within(published, since):
                continue
            title = clean(node.title.get_text() if node.title else "")
            desc = _strip_html(node.description.get_text() if node.description else "")
            # 전체 피드이므로 AI 관련만 남긴다.
            if not re.search(r"AI|인공지능|LLM|생성형|에이전트|반도체|데이터", title + desc):
                continue
            out.append(
                self.make(
                    title,
                    clean(node.link.get_text() if node.link else ""),
                    published=published,
                    summary=desc[:600],
                )
            )
            if len(out) >= limit:
                break
        return out


class ItNewsMoaCollector(Collector):
    """IT뉴스모아. Nuxt 서버렌더 결과의 a.article-card 를 읽는다(원출처와 키워드 배지 포함)."""

    name = "itnewsmoa"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        url = self.params.get("url", "https://news.dlwlrmaon.com/")
        try:
            html = self.fetcher.get(url)
        except RuntimeError:
            return []
        soup = BeautifulSoup(html, "lxml")
        out: List[Article] = []
        for card in soup.select("a.article-card"):
            title_el = card.select_one(".article-card__title")
            if not title_el:
                continue
            meta = card.select_one(".article-card__meta")
            published = parse_date(clean(meta.get_text()) if meta else "")
            if not within(published, since):
                continue
            outlet = card.select_one(".article-card__source")
            tags = [clean(t.get_text()) for t in card.select(".utility-pill--keyword")]
            art = self.make(
                title_el.get_text(),
                urljoin(url, card.get("href", "")),
                published=published,
                tags=tags,
                raw={"outlet": clean(outlet.get_text()) if outlet else ""},
            )
            # 원 매체명을 유지해야 '복수 매체 보도' 신호를 셀 수 있다.
            if art.raw.get("outlet"):
                art.source_name = "IT뉴스모아/%s" % art.raw["outlet"]
            out.append(art)
            if len(out) >= limit:
                break
        return out


class GoogleNewsCollector(Collector):
    """구글 뉴스 검색 RSS.

    기관 게시판·전문지만 보면 우리청 관련 언론 보도를 통째로 놓친다.
    실제로 「해양경찰청, AI·친환경·K-조선·방산 기술로 차세대 함정 청사진」과
    「스텔라비전, 해경 항공 AI '딥 블루 아이' 개발 참여」는 기존 14개 수집원
    어디에도 올라오지 않았다. 지난 11개 호에서 내부 소식이 19%였는데
    그 공백을 이 수집기가 메운다.

    제목이 '기사 제목 - 매체명' 꼴이라 매체명을 떼어 출처로 쓴다.
    같은 사건을 여러 매체가 쓰므로 중복이 많지만, 그건 뒤의 엔티티 병합이
    처리한다. 오히려 매체 수가 중요도 신호가 된다.
    """

    name = "google_news"
    BASE = "https://news.google.com/rss/search"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        out: List[Article] = []
        seen = set()
        for q in self.params.get("queries", []):
            url = q["url"] if isinstance(q, dict) and "url" in q else self._build(q)
            try:
                xml = self.fetcher.get(url)
            except RuntimeError:
                continue
            soup = BeautifulSoup(xml, "xml")
            for node in soup.find_all("item"):
                link = clean(node.link.get_text() if node.link else "")
                raw_title = node.title.get_text() if node.title else ""
                title, outlet = self._split(raw_title)
                key = title.lower()
                if not link or not title or key in seen:
                    continue
                published = parse_date(node.pubDate.get_text() if node.pubDate else "")
                if not within(published, since):
                    continue
                seen.add(key)
                out.append(
                    self.make(
                        title, link,
                        published=published,
                        # 구글 뉴스 description 은 링크 뭉치뿐이라 본문으로 쓰지 않는다.
                        # 필요하면 enrich_bodies 가 원문에서 받아 온다.
                        summary="",
                        raw={"dept": outlet} if outlet else {},
                    )
                )
                if len(out) >= limit:
                    return out
        return out

    def _build(self, q) -> str:
        from urllib.parse import quote
        term = q if isinstance(q, str) else q.get("q", "")
        return "%s?q=%s&hl=ko&gl=KR&ceid=KR:ko" % (self.BASE, quote(term))

    @staticmethod
    def _split(raw: str) -> tuple:
        """'제목 - 매체명' 을 나눈다. 제목 안에 하이픈이 있어도 마지막 것만 본다."""
        text = clean(raw)
        if " - " not in text:
            return text, ""
        head, _, tail = text.rpartition(" - ")
        # 매체명은 짧다. 길면 제목의 일부로 본다.
        if head and len(tail) <= 20:
            return head.strip(), tail.strip()
        return text, ""
