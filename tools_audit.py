#!/usr/bin/env python3
"""수집원별로 무엇을 가져와 무엇이 실렸는지 추적한다.

  python tools_audit.py            최신 수집분 기준
"""
import sys, json, glob, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from trendletter import store, pipeline
from trendletter.config import load
from trendletter.scoring import select, is_ai_related, ai_focus

cfg = load()
raw_path = sorted(Path("data/raw").glob("collect-*.json"))[-1]
arts = store.load_raw(raw_path)
draft = store.load_draft(store.latest_draft())

print("=" * 78)
print("수집 파일: %s" % raw_path.name)
print("초안     : %s (%d건 게재)" % (draft.slug, len(draft.items)))
print("=" * 78)

# ── 1. 수집원별 ──────────────────────────────────────────────
from trendletter.dedupe import cluster, merge_by_entity
from trendletter.scoring import entities, newsiness, is_product
th = float(cfg.get("dedupe.title_similarity", 0.72))
raw_cl = merge_by_entity(cluster(arts, th), entities, newsiness,
                         float(cfg.get("dedupe.entity_min_similarity", 0.30)), is_product)
passed = pipeline.build_clusters(arts, cfg)
chosen = select(passed, cfg)

src_meta = {s["id"]: s for s in cfg.sources}
by_src = collections.Counter(a.source_id for a in arts)
# 관문을 통과한 이슈에 '기사로서' 남은 것을 센다.
# 대표(lead) 기준으로 세면 다른 이슈에 병합된 기사가 탈락으로 보인다.
passed_keys = {a.key for c in passed for a in c.articles}
lead_src = collections.Counter(a.source_id for a in arts if a.key in passed_keys)
used_keys = {k for it in draft.items for k in it.origin_keys}
contrib = collections.Counter(a.source_id for a in arts if a.key in used_keys)

print("\n[1] 수집원별 기여")
print("%-22s %-9s %-7s %5s %6s %6s %6s" % ("수집원", "트랙", "역할", "수집", "관문통과", "게재기여", "통과율"))
print("-" * 78)
for sid, n in by_src.most_common():
    m = src_meta.get(sid, {})
    p = lead_src.get(sid, 0)
    print("%-22s %-9s %-7s %5d %6d %6d %5.0f%%" % (
        m.get("name", sid)[:22], m.get("track", "?"), m.get("role", "?"),
        n, p, contrib.get(sid, 0), p / n * 100 if n else 0))
print("-" * 78)
print("%-22s %-9s %-7s %5d %6d %6d" % ("합계", "", "", len(arts), len(passed), sum(contrib.values())))

# ── 2. 단계별 ────────────────────────────────────────────────
print("\n[2] 단계별 감소")
print("  수집        %4d건" % len(arts))
print("  중복 통합   %4d이슈  (%d건 흡수)" % (len(raw_cl), len(arts) - len(raw_cl)))
print("  AI 관문     %4d이슈  (%d개 탈락)" % (len(passed), len(raw_cl) - len(passed)))
print("  게재        %4d건" % len(draft.items))

# ── 3. 게재 항목 추적 ────────────────────────────────────────
print("\n[3] 게재 항목이 어디서 왔나")
key2art = {a.key: a for a in arts}
for it in draft.items:
    tag = " [종합]" if it.synthesis else ""
    print("\n  %02d%s %s" % (it.no, tag, it.title[:56]))
    srcs = collections.Counter()
    for k in it.origin_keys:
        a = key2art.get(k)
        if a:
            srcs[src_meta.get(a.source_id, {}).get("name", a.source_id)] += 1
    for s, n in srcs.most_common():
        print("       ← %-24s %d건" % (s[:24], n))
    for w in it.why:
        print("       · %s" % w)

# ── 4. 상위인데 탈락한 것 ────────────────────────────────────
print("\n[4] 점수 상위인데 실리지 않은 것")
picked = {c.lead.url for c in chosen}
n = 0
for c in passed[:16]:
    if c.lead.url in picked:
        continue
    print("  %5.1f [%-8s] %s" % (c.score, c.lead.track, c.lead.title[:56]))
    n += 1
    if n >= 6:
        break

# ── 5. AI 관문 탈락 표본 ─────────────────────────────────────
print("\n[5] AI 관문 탈락 표본 (오탈락 점검용)")
dropped = [c for c in raw_cl if not is_ai_related(c, cfg)]
dropped.sort(key=lambda c: -ai_focus(c, cfg))
for c in dropped[:8]:
    print("  %.1f점  %s" % (ai_focus(c, cfg), c.lead.title[:62]))
print("\n  (총 %d개 탈락)" % len(dropped))
