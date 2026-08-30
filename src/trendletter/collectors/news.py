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
