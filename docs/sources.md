# 수집원 확인 결과

> 이 문서의 수치와 예시는 **해양 안전·치안 분야 기관**에 적용해 실제로 돌린 결과다.
> 다른 기관에 쓸 때는 `config/` 의 수집원·업무 관련도를 그 기관에 맞게 바꾼다.

2026-08-29 실제 접속으로 확인한 내용이다. 사이트 개편 시 이 문서를 먼저 갱신한다.

## 정책 트랙

| 수집원 | 경로 | 방식 | 상태 |
| --- | --- | --- | --- |
| 국가AI전략위원회 | 정책브리핑 보도자료 검색 (`국가인공지능전략위원회`, `국가AI전략위원회`) | HTML | 정상 (13건/7일) |
| 대한민국 정책브리핑 | `korea.kr/news/policyNewsList.do` | HTML | 정상 |
| 해양경찰청 | `kcg.go.kr/kcg/na/ntt/selectNttList.do?mi=2799&bbsId=313` | HTML | 정상 |
| 해양수산부 | 정책브리핑 검색 | HTML | 정상 |
| 행안부·과기정통부 | 정책브리핑 검색 | HTML | 정상 |
| 경찰청·소방청 | 정책브리핑 검색 | HTML | 정상 |
| 서울 AI 플랫폼 (AI 정책) | `POST /hmpg/bpst/bpstListPgng.do` | HTML 조각 | 정상 (60건/7일, 누적 24,345건) |

### 정책브리핑 보도자료 검색

`https://www.korea.kr/briefing/pressReleaseList.do` 에 아래 쿼리를 붙이면
키워드 + 기간 검색이 그대로 동작한다. 별도 API 키가 필요 없다.

```
?srchWord=인공지능&startDate=2026-08-15&endDate=2026-08-29&pageIndex=1
```

목록 마크업은 `div.list_type ul li a` 안에 제목(`strong`), 요약(`span.lead`),
출처(`span.source > span` 2개: 날짜, 부처명)가 들어 있다.
부처명을 그대로 `source_name` 으로 쓰기 때문에 부처별 소스를 따로 만들 필요가 없다.

**국가AI전략위원회 원문 사이트는 아직 확인하지 못했다.** 위원회 자료도 정책브리핑에
올라오므로 당장은 검색 경로로 확보하고, 원문 사이트 목록 구조는 별도로 확인한다.

### 서울 AI 플랫폼 「AI 정책」

**로그인·세션·CSRF 토큰이 전혀 필요 없다.** 화면이 비어 보이는 이유는 로그인 때문이
아니라, 목록을 AJAX 로 따로 불러오기 때문이다(그 요청이 504 로 실패하면 빈 화면이 된다).

메뉴 링크는 `javascript:;` 지만 `onclick="goSubMenu('/hmpg/bpst/bpstListPage.do', ...)"`
에 실제 경로가 들어 있다. 목록 자체는 아래 POST 가 **서버렌더 HTML 조각**으로 돌려준다.

```
POST https://seoulai.saif.or.kr/hmpg/bpst/bpstListPgng.do
  miv_pageNo=1&miv_pageSize=150&sidx=FRST_REG_DT&sord=DESC
  &wrt_bgng_ymd=2026-08-22&wrt_end_ymd=2026-08-29&searchtxt=
  &hmpg_nm_sa=충청남도 ...   (광역지자체 17개)
  &hmpg_nm_mi=정부부처·청·위원회 ...  (중앙부처·연구기관)
  &hmpg_nm_ov=OECD ...      (해외)
```

필터를 하나도 보내지 않으면 전체가 조회된다. 응답은 `li.list` 반복이고,
`p.subject`(제목) · `.left-bottom li`(기관/등록일/조회수) ·
**`a.btn-white`(원 기관의 실제 원문 URL)** 를 담고 있다.

이 플랫폼은 이미 아래를 모아 두었다. 다른 수집원이 전혀 다루지 않는 영역이다.

- **광역지자체 17곳** — 충남·경북·경남·인천·제주·세종·대구 등
- **중앙부처·연구기관** — 정부부처·청·위원회, NIA, KISTEP, SPRI, IITP, STEPI
- **해외** — OECD, 미국(캘리포니아·뉴욕), 일본(도쿄), 독일(베를린), 싱가포르,
  중국, 인도, 호주, 캐나다

실제로 이 소스를 켜자 다른 수집원에 없던 기관 21곳(OECD·BERLIN·SINGAPORE·
광역지자체 등)이 새로 들어왔다.

### 자료 품질 (2026-08-30 실측, 60건 기준)

| 구분 | 건수 | 비고 |
| --- | ---: | --- |
| 중앙부처 | 30 | 정책브리핑과 내용이 겹칠 수 있으나 URL은 원 기관 주소 |
| 지자체 | 22 | 제주 8 · 충남 5 · 경북 3 등. **다른 수집원이 전혀 다루지 않음** |
| 해외 | 7 | OECD 4 · 베를린 등. **다른 수집원이 전혀 다루지 않음** |
| 연구기관 | 1 | STEPI 등 |

- **URL 중복 0건, 제목 중복 9건** — 60건 중 51건이 이 소스에만 있는 자료다.
  이 소스를 끄면 지자체·해외 정책을 통째로 놓친다
- **제목에 AI 관련어가 있는 것은 23건(38%)** — 플랫폼이 원 사이트를 '인공지능'
  키워드로 넓게 긁어오기 때문에 가축방역·재개발·사망사고까지 섞인다.
  AI 중심성 관문이 걸러낸다

건질 만한 것 예시 — 우리청 AX 가이드라인 수립에 직접 참고할 수 있는 자료다.

```
OECD   Guidance on Government use of Public Generative AI Tools
OECD   Government AI Offices
OECD   Generative AI for Engineering Design
```

### 검색어로 좁히지 않는 이유 (2026-08-30 시험)

`searchtxt` 로 주제를 좁히면 오히려 나빠진다.

| 검색어 | 결과 |
| --- | --- |
| `공공기관 AI 도입` | **0건** |
| `AI 가이드라인` | **0건** |
| `해양` | 63건이지만 상위가 제주 공직자 회의·충남 유럽 수출 등 무관 |
| `소방` | 42건이지만 상위가 울산 공업축제·추경 편성 등 무관 |

플랫폼이 원 사이트를 `인공지능` 키워드로 이미 훑어 오는데, 그 위에 우리 검색어를
겹치면 본문에 그 낱말이 있기만 해도 걸린다. **필터를 더하지 않고 최신순으로 받는다.**

다만 플랫폼이 물려받는 한계가 있다. 정부부처 자료는 korea.kr 을 `인공지능` 으로
검색해 가져오는데, korea.kr 이 본문 전문검색이라 관계부처 합동 보도자료가 대량으로
딸려 온다(의왕역 사망사고·재개발 현장점검 등). AI 중심성 관문이 걸러낸다.

### 항목별 AI 요약 (bpstPostSummary)

플랫폼은 항목마다 자체 AI 요약을 만들어 둔다. 목록에는 없고 별도 호출로 받는다.

```
POST https://seoulai.saif.or.kr/hmpg/bpst/bpstPostSummary.do
  hmpg_mng_no={목록의 data-hmpg-mng-no}&pst_no={data-pst-no}
→ {"summary":"3줄 요약...","success":"true"}
```

**수집 단계에서는 받지 않는다.** 60건 전부 받으면 90초가 걸리는데, AI 중심성 관문의
판정은 **한 건도 바뀌지 않았다**(제목 적중 4.0점이 기준 3.5점을 넘는 유일한 경로이고,
본문은 최대 3.0점이라 구조적으로 통과가 불가능하다).

대신 **초안에 뽑힌 항목만** `pipeline.fill_summaries()` 가 뒤늦게 채운다.
이 소스는 목록에 제목만 있어, 요약이 없으면 Claude 가 제목만 보고 본문을 써야 한다.
보통 2~5건이라 몇 초면 끝난다.

**주의 — 영어 자료가 업무 관련도 0점을 받던 문제(해결됨).**
업무 관련도 키워드가 한글뿐이라 OECD 「공공기관 생성형 AI 사용 지침」이
업무 관련도 0점, 총점 4.0점에 머물렀다. `ontology.yaml` 의 `공공전환` 그룹에
`government use`·`public sector`·`guidance on` 등 영어 표현을 추가해
7.4점으로 정상화했다(2026-08-30).

### 해양경찰청 게시판 ID

`selectNttList.do?mi={메뉴}&bbsId={게시판}` 형식이다.

| 게시판 | mi | bbsId |
| --- | --- | --- |
| 공지사항 | 2796 | 310 |
| 고시공고 | 2798 | 312 |
| **보도자료** | **2799** | **313** |
| 설명자료 | 5237 | 1103 |
| 정책자료(일반) | 2815 | 317 |

이 게시판은 AI 여부와 무관하게 전체 보도자료를 반환한다.
따라서 `filter.require_ai` 관문이 반드시 켜져 있어야 한다.

## 산업 트랙

| 수집원 | 경로 | 방식 | 비고 |
| --- | --- | --- | --- |
| AI타임스 | `aitimes.com/rss/allArticle.xml` (전체), `rss/S1N1.xml` (정책) | RSS | 50건 제공. title/link/description/author/pubDate 완비 |
| ZDNet Korea | `zdnet.co.kr/newskey/?lstcode=인공지능` | HTML (`div.newsPost`) | `zdnet.co.kr/news/news_xml.asp` 는 404. 전체 피드는 `feeds.feedburner.com/zdkorea` (30건, 전 분야) |
| IT뉴스모아 | `news.dlwlrmaon.com` | HTML (`a.article-card`) | Nuxt 서버렌더. 원매체명(`.article-card__source`)과 키워드 배지 제공 |

IT뉴스모아는 원매체명을 `IT뉴스모아/{매체}` 로 남긴다. 이렇게 해야
'복수 매체 보도' 신호를 셀 때 IT뉴스모아 한 곳으로 뭉개지지 않는다.

## 개발자 트랙

| 수집원 | 경로 | 방식 | 비고 |
| --- | --- | --- | --- |
| Hacker News | `hn.algolia.com/api/v1/search_by_date` | JSON | **Algolia 는 `OR`·괄호를 검색 문법이 아니라 문자열로 취급한다.** 짧은 질의를 여러 번 던져 합친다 |
| Reddit | `reddit.com/r/{sub}/top/.rss?t=week` | Atom | **`.json` / `api.reddit.com` 은 403 차단.** 연속 요청 시 429 → 호스트당 3초 간격 필요 |
| GitHub | `api.github.com/search/repositories` | JSON | `created:>7일전 topic:ai` 는 결과 0건. 생성 기간을 30일로 넓혀 사용 |
| OpenRouter | — | — | **미구현.** 공개 랭킹 엔드포인트 확인 필요 |
| Threads | — | — | 수동 보충 전용 |

Reddit Atom 피드에는 **추천수·댓글수가 없다.** 따라서 '반응 크기'는
주간 top 진입 순위와 Hacker News 지표로만 판단한다. 원래 설계했던
"플랫폼 내 상대적 반응"의 정량 비교는 Reddit 쪽에서는 불가능하다.

## 알려진 제약

1. **OpenRouter 미구현** — `enabled: false` 로 두었다.
2. **Reddit 지표 부재** — 위 참고.
3. **국가AI전략위원회 원문 미확인** — 정책브리핑 검색으로 대체 중.
4. **호출 간격** — Reddit 3초, GitHub 1.5초, 그 외 0.4초로 제한한다.
   전체 수집은 약 1~2분 걸린다.
