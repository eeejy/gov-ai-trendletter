"""정책 트랙 수집기: 대한민국 정책브리핑 · 표준 정부게시판(해양경찰청 등)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Article
from .base import Collector, clean, parse_date, within

KOREA_KR = "https://www.korea.kr"


def _press_items(html: str):
    """정책브리핑 목록 공통 마크업: div.list_type li a > span.text 안에 제목/요약/출처."""
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select('div.list_type a[href*="View.do"]')
    for a in rows:
        strong = a.find("strong")
        if not strong:
            continue
        lead = a.select_one("span.lead")
        source = a.select_one("span.source")
        spans = [clean(s.get_text()) for s in source.find_all("span")] if source else []
        date_txt = next((s for s in spans if re.search(r"\d{4}", s)), "")
        dept = next((s for s in spans if s and not re.search(r"\d{4}", s)), "")
        yield {
            "title": clean(strong.get_text()),
            "url": urljoin(KOREA_KR, a["href"].replace("&amp;", "&")),
            "lead": clean(lead.get_text()) if lead else "",
            "date": date_txt,
            "dept": dept,
        }


class KoreaKrListCollector(Collector):
    """정책브리핑 정책뉴스 목록을 그대로 훑는다."""

    name = "koreakr_list"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        base = self.params.get("url", KOREA_KR + "/news/policyNewsList.do")
        out: List[Article] = []
        for page in range(1, int(self.params.get("pages", 1)) + 1):
            url = "%s?pageIndex=%d" % (base, page)
            try:
                html = self.fetcher.get(url)
            except RuntimeError:
                break
            found = 0
            for row in _press_items(html):
                found += 1
                published = parse_date(row["date"])
                if not within(published, since):
                    continue
                out.append(
                    self.make(
                        row["title"],
                        row["url"],
                        published=published,
                        summary=row["lead"][:600],
                        raw={"dept": row["dept"]},
                    )
                )
                if len(out) >= limit:
                    return out
            if not found:
                break
        return out


class KoreaKrSearchCollector(Collector):
    """정책브리핑 보도자료를 부처 + 키워드 + 기간으로 검색한다.

    **검색어만 쓰면 본문 전문검색이라 엉뚱한 부처 자료가 섞인다.**
    2026-08-30 확인: "경찰청 인공지능" 으로 검색했더니 통일부·외교부·질병관리청
    자료가 12건 중 대부분이었고 정작 경찰청 자료는 0건이었다.
    부처를 지정할 때는 반드시 `depts` (repCode) 를 함께 보낸다.

    - depts   : config/korea_kr_depts.yaml 의 부처명 목록
    - queries : 검색어 목록 (비우면 해당 부처의 모든 보도자료)
    """

    name = "koreakr_search"
    ENDPOINT = KOREA_KR + "/briefing/pressReleaseList.do"

    def _repcodes(self) -> str:
        names = self.params.get("depts") or []
        if not names:
            return ""
        try:
            import yaml

            from ..config import CONFIG_DIR

            table = (
                yaml.safe_load((CONFIG_DIR / "korea_kr_depts.yaml").read_text(encoding="utf-8"))
                or {}
            ).get("departments", {})
        except Exception:  # noqa: BLE001
            return ""
        return ",".join(table[n] for n in names if n in table)

    def collect(self, since: datetime, limit: int) -> List[Article]:
        out: List[Article] = []
        seen = set()
        today = datetime.now().date()
        repcode = self._repcodes()
        queries = self.params.get("queries") or [""]
        for query in queries:
            qs = urlencode(
                {
                    "srchWord": query,
                    "startDate": since.date().isoformat(),
                    "endDate": today.isoformat(),
                    "pageIndex": 1,
                    "repCodeType": "",
                    "repCode": repcode,
                }
            )
            try:
                html = self.fetcher.get(self.ENDPOINT + "?" + qs)
            except RuntimeError:
                continue
            for row in _press_items(html):
                if row["url"] in seen:
                    continue
                published = parse_date(row["date"])
                if not within(published, since):
                    continue
                seen.add(row["url"])
                art = self.make(
                    row["title"],
                    row["url"],
                    published=published,
                    summary=row["lead"][:600],
                    raw={"dept": row["dept"], "query": query},
                )
                if row["dept"]:
                    art.source_name = row["dept"]
                out.append(art)
                if len(out) >= limit:
                    return out
        return out


class GovBoardCollector(Collector):
    """행정표준 게시판(nttSn 방식). 해양경찰청 보도자료가 이 형식이다.

    목록이 <table> 이고 제목 셀에 selectNttInfo.do?nttSn=... 링크, 등록일 열이 따로 있다.
    """

    name = "govboard"

    def collect(self, since: datetime, limit: int) -> List[Article]:
        base = self.params["url"]
        origin = "{u.scheme}://{u.netloc}".format(u=urlparse(base))
        out: List[Article] = []
        for page in range(1, int(self.params.get("pages", 1)) + 1):
            # 이 게시판은 pageIndex 가 아니라 currPage 를 쓴다.
            # pageIndex 로 넘기면 1페이지가 그대로 다시 와서 중복만 쌓인다(2026-08-30 확인).
            url = base if page == 1 else "%s&currPage=%d" % (base, page)
            try:
                html = self.fetcher.get(url)
            except RuntimeError:
                break
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                break
            for tr in rows:
                a = tr.find("a", href=re.compile(r"selectNttInfo\.do"))
                if not a:
                    continue
                cells = [clean(td.get_text()) for td in tr.find_all("td")]
                date_txt = next(
                    (c for c in cells if re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$", c)), ""
                )
                published = parse_date(date_txt)
                if not within(published, since):
                    continue
                out.append(
                    self.make(
                        a.get_text(),
                        urljoin(origin, a["href"].replace("&amp;", "&")),
                        published=published,
                    )
                )
                if len(out) >= limit:
                    return out
        return out


class SeoulAiCollector(Collector):
    """서울 AI 플랫폼 「AI 정책」.

    2026-08-29 확인: 로그인·세션·토큰이 전혀 필요 없다.
    화면은 비어 보이지만 목록은 POST /hmpg/bpst/bpstListPgng.do 가
    서버렌더 HTML 조각으로 돌려준다(누적 24,345건).

    이 플랫폼이 이미 17개 광역지자체 · 정부부처 · 연구기관(STEPI·SPRI·IITP·
    KISTEP·NIA) · 해외(OECD·미국·일본·싱가포르·중국 등)의 AI 관련 보도자료를
    모아 두었고, 항목마다 원문 URL을 그대로 제공한다.
    특히 지자체와 해외 정책은 다른 수집원이 전혀 다루지 않는 영역이다.
    """

    name = "seoul_ai"
    ENDPOINT = "https://seoulai.saif.or.kr/hmpg/bpst/bpstListPgng.do"
    SUMMARY = "https://seoulai.saif.or.kr/hmpg/bpst/bpstPostSummary.do"

    # 필터를 하나도 보내지 않으면 전체가 조회된다. 좁히고 싶을 때만 지정한다.
    GROUP_FIELDS = {
        "local": "hmpg_nm_sa",      # 광역지자체
        "central": "hmpg_nm_mi",    # 정부부처·연구기관
        "overseas": "hmpg_nm_ov",   # 해외
    }

    def collect(self, since: datetime, limit: int) -> List[Article]:
        page_size = min(int(self.params.get("page_size", 100)), 300)
        data = [
            ("miv_pageNo", "1"),
            ("miv_pageSize", str(page_size)),
            ("sidx", "FRST_REG_DT"),
            ("sord", "DESC"),
            ("wrt_bgng_ymd", since.date().isoformat()),
            ("wrt_end_ymd", datetime.now().date().isoformat()),
            ("searchtxt", self.params.get("searchtxt", "")),
        ]
        for group, values in (self.params.get("groups") or {}).items():
            field = self.GROUP_FIELDS.get(group)
            if field:
                for v in values:
                    data.append((field, v))

        try:
            html = self.fetcher.post(self.ENDPOINT, data)
        except RuntimeError:
            return []

        soup = BeautifulSoup(html, "lxml")
        out: List[Article] = []
        for li in soup.select("li.list"):
            subject = li.select_one("p.subject")
            if not subject:
                continue
            # '원문 보기' 버튼에 원 기관의 실제 주소가 들어 있다. 이것을 링크로 쓴다.
            origin = li.select_one("a.btn-white[href^=http]")
            if not origin:
                continue

            org = published = ""
            for cell in li.select(".left-bottom li"):
                label = cell.find("b")
                value = cell.find("p")
                if not label or not value:
                    continue
                key = clean(label.get_text())
                if key == "기관":
                    org = clean(value.get_text())
                elif key == "등록일":
                    published = clean(value.get_text())

            dt = parse_date(published)
            if not within(dt, since):
                continue

            # 플랫폼이 항목마다 만들어 둔 요약을 함께 가져온다.
            # 목록에는 제목만 있어 AI 관문이 제목만 보고 판정하던 문제를 없앤다.
            btn = li.select_one("a.btn-add-summary")
            keys = (
                (btn.get("data-hmpg-mng-no"), btn.get("data-pst-no")) if btn else (None, None)
            )

            art = self.make(
                subject.get_text(),
                origin["href"],
                published=dt,
                raw={"dept": org, "via": "서울AI플랫폼", "keys": keys},
            )
            if org:
                # 원 기관명을 남겨야 정책브리핑 수집분과 매체 수가 중복 계산되지 않는다.
                art.source_name = org
            out.append(art)
            if len(out) >= limit:
                break

        if self.params.get("fetch_summary", True):
            self._fill_summaries(out, int(self.params.get("summary_limit", 80)))
        return out

    def _fill_summaries(self, articles: List[Article], cap: int) -> None:
        """플랫폼의 AI 요약을 채운다. 한 건이 실패해도 나머지는 그대로 둔다."""
        import json

        for art in articles[:cap]:
            mng, pst = art.raw.get("keys") or (None, None)
            if not mng or not pst:
                continue
            try:
                body = self.fetcher.post(self.SUMMARY, [("hmpg_mng_no", mng), ("pst_no", pst)])
                data = json.loads(body)
            except Exception:  # noqa: BLE001 - 요약은 있으면 좋은 것이다
                continue
            text = clean((data.get("summary") or "").replace("\n", " "))
            if text:
                art.summary = text[:700]
