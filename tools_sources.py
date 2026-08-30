#!/usr/bin/env python3
"""수집원 전체 현황 — 분류·방식·가중치·라이브 접속 결과·실제 수집 목록.

  python tools_sources.py           라이브 점검 + 최신 수집분
  python tools_sources.py --offline 라이브 점검 없이 최신 수집분만
"""
import sys, collections
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from trendletter import store
from trendletter.config import load
from trendletter.collectors import build
from trendletter.http import Fetcher

LIVE = "--offline" not in sys.argv
cfg = load()
TRACK = {"policy": "정책·공공", "industry": "산업·도구", "dev": "개발자·기술"}
ROLE = {"must": "필수", "primary": "주력", "verify": "보완·검증", "discover": "추가발견"}

# 수집기별 방식 설명
HOW = {
    "koreakr_search": "정책브리핑 보도자료 · 부처코드+검색어",
    "koreakr_list": "정책브리핑 정책뉴스 목록",
    "govboard": "기관 게시판 직접 (nttSn)",
    "seoul_ai": "서울AI플랫폼 POST (bpstListPgng)",
    "rss": "RSS 피드",
    "zdnet": "키워드 목록 페이지 파싱",
    "itnewsmoa": "SSR 카드 파싱 (원매체명 유지)",
    "hackernews": "Algolia API",
    "reddit": "Atom 피드 (JSON 차단)",
    "github": "GitHub Search API",
    "openrouter": "미구현",
    "manual": "수동 입력 전용",
}

raw_path = sorted(Path("data/raw").glob("collect-*.json"))
arts = store.load_raw(raw_path[-1]) if raw_path else []
by_src = collections.defaultdict(list)
for a in arts:
    by_src[a.source_id].append(a)

print("=" * 96)
print(" 수집원 현황  |  기준 수집분: %s (%d건)" % (raw_path[-1].name if raw_path else "없음", len(arts)))
print("=" * 96)

live = {}
if LIVE:
    print("\n라이브 접속 점검 중...", flush=True)
    since = datetime.now() - timedelta(days=7)
    f = Fetcher(use_cache=False)
    for s in cfg.sources:
        if not s.get("enabled"):
            live[s["id"]] = ("비활성", 0)
            continue
        try:
            n = len(build(s, f).collect(since, 20))
            live[s["id"]] = ("정상" if n else "응답0건", n)
        except Exception as exc:  # noqa: BLE001
            live[s["id"]] = ("실패: %s" % str(exc)[:34], 0)

for track in ("policy", "industry", "dev"):
    rows = [s for s in cfg.sources if s.get("track") == track]
    print("\n" + "─" * 96)
    print("■ %s  (%d개 수집원)" % (TRACK[track], len(rows)))
    print("─" * 96)
    print("%-26s %-7s %5s %-32s %-10s %5s" %
          ("수집원", "역할", "가중치", "수집 방식", "접속", "수집"))
    for s in rows:
        st, ln = live.get(s["id"], ("-", 0))
        got = len(by_src.get(s["id"], []))
        print("%-26s %-7s %5.1f %-32s %-10s %5s" % (
            s["name"][:26], ROLE.get(s.get("role"), s.get("role", "")),
            s.get("weight", 1.0), HOW.get(s.get("collector"), s.get("collector", ""))[:32],
            st[:10], got if got else ("-" if not s.get("enabled") else 0)))

print("\n" + "=" * 96)
print(" 가중치가 점수에 반영되는 방식")
print("=" * 96)
print("""
  source        = 수집원 가중치 × 2.0        (최대 3.2)
  must          = 필수 수집원이면 +4.0        (해양경찰청·해양수산부 — 소스 기준)
  priority      = 최우선 주제 제목 언급 +5.0  (국가AI전략위 등 — 내용 기준)
  work          = 업무 관련도 최대 12.0       (직접6·유사기관4·현장임무4·공공전환3
                                              ·현장기술3·인재교육2.5·인프라2)
  outlets       = (매체 수-1) × 1.5, 최대 4.5
  dev_signal    = HN점수·GitHub별(로그)·Reddit주간순위, 최대 4.0
  cross_platform= (플랫폼 수-1) × 1.6, 최대 3.5
  ai_focus      = AI 중심성 × 0.5            (관문 역할이라 절반만)
  ontology      = 적중 축 수 × 0.8
  promo         = 시청용 특강·세미나 -2.5
""")

print("=" * 96)
print(" 수집원별 실제 수집 목록")
print("=" * 96)
for track in ("policy", "industry", "dev"):
    for s in [x for x in cfg.sources if x.get("track") == track]:
        items = by_src.get(s["id"], [])
        if not items:
            continue
        print("\n▸ %s  (%s · 가중치 %.1f · %d건)" %
              (s["name"], ROLE.get(s.get("role"), ""), s.get("weight", 1.0), len(items)))
        for a in items[:8]:
            d = a.published.strftime("%m-%d") if a.published else "  -  "
            org = (a.raw.get("dept") or a.source_name)[:16]
            print("    %s  %-16s %s" % (d, org, a.title[:58]))
        if len(items) > 8:
            print("    … 외 %d건" % (len(items) - 8))
