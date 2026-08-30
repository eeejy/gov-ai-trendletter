"""명령줄 진입점.

  collect   수집만 하고 data/raw 에 저장
  draft     수집 결과로 초안(JSON) 생성
  render    초안을 HTML 로 미리보기
  publish   초안을 확정본 HTML 로 발행
  editor    로컬 웹 편집기 실행
  sources   수집원 상태 점검
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 소스 레이아웃을 설치 없이 쓰기 위한 경로 보정
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trendletter import pipeline, store            # noqa: E402
from trendletter.config import load                # noqa: E402
from trendletter.render import write               # noqa: E402


def _say(msg: str) -> None:
    print(msg, flush=True)


def cmd_collect(args) -> int:
    cfg = load()
    _say("수집 시작 (최근 %d일)" % (args.days or cfg.get("collect.days", 7)))
    articles = pipeline.collect(
        cfg, days=args.days, only=args.source, use_cache=not args.no_cache, progress=_say
    )
    path = store.save_raw(articles)
    _say("총 %d건 → %s" % (len(articles), path))
    return 0


def cmd_draft(args) -> int:
    cfg = load()
    if args.reuse:
        raw = store.latest_raw()
        if not raw:
            _say("재사용할 수집 결과가 없습니다. 먼저 collect 를 실행하세요.")
            return 1
        _say("수집 결과 재사용: %s" % raw.name)
        articles = store.load_raw(raw)
    else:
        _say("수집 시작 (최근 %d일)" % (args.days or cfg.get("collect.days", 7)))
        articles = pipeline.collect(
            cfg, days=args.days, use_cache=not args.no_cache, progress=_say
        )
        store.save_raw(articles)

    issue = pipeline.make_draft(articles, cfg, days=args.days, theme=args.theme or "")
    issue.meta["sources_used"] = len({a.source_id for a in articles})
    clusters = issue.meta.pop("_clusters", {})
    issue.meta.pop("_all_clusters", None)
    topics = issue.meta.pop("_topics", [])
    if not args.no_llm:
        pipeline.add_trend_items(issue, topics, cfg, progress=_say)
        pipeline.polish(issue, clusters, cfg, progress=_say)
    if not args.no_video:
        _say("  · 관련 영상 찾는 중…")
        pipeline.attach_videos(issue, progress=_say)
    path = store.save_draft(issue)
    _say("초안 %s · %d건 → %s" % (issue.label, len(issue.items), path))
    for it in issue.items:
        _say("  %02d [%s/%s] %s" % (it.no, it.field_label, it.impact, it.title[:52]))
    _say("\n다음: python run.py editor  (편집 후 publish)")
    return 0


def _resolve_draft(arg):
    if arg:
        return Path(arg)
    p = store.latest_draft()
    if not p:
        _say("초안이 없습니다. 먼저 draft 를 실행하세요.")
        raise SystemExit(1)
    return p


def cmd_render(args) -> int:
    issue = store.load_draft(_resolve_draft(args.draft))
    path = write(issue, preview=True)
    _say("미리보기 → %s" % path)
    return 0


def cmd_publish(args) -> int:
    path = _resolve_draft(args.draft)
    issue = store.load_draft(path)
    empty = [
        it.no
        for it in issue.items
        if not it.note.strip() or "작성하세요" in " ".join(it.body)
    ]
    if empty and not args.force:
        _say("본문 또는 시사점이 비어 있는 항목: %s" % empty)
        _say("편집기에서 채우거나 --force 로 강행하세요.")
        return 1
    issue.status = "published"
    store.save_draft(issue)
    out = write(issue)
    _say("발행 → %s" % out)

    cfg = load()
    if cfg.get("telegram.enabled") and not args.no_telegram:
        from trendletter import telegram as tg

        base = (cfg.get("telegram.html_base") or "").strip()
        url = (base.rstrip("/") + "/" + out.name) if base else ""
        try:
            r = tg.send(issue, url, cfg, dry_run=args.dry_run)
            if r.get("dry_run"):
                _say("\n[보내지 않음 — 미리보기 %d자]\n%s" % (r["length"], r["text"]))
            else:
                _say("텔레그램 발송 완료 (message_id %s)" % r.get("message_id"))
        except Exception as exc:  # noqa: BLE001 - 발행 자체는 이미 끝났다
            _say("텔레그램 발송 실패: %s" % str(exc)[:200])
    return 0


def cmd_telegram(args) -> int:
    """텔레그램 연결 확인 / 미리보기 / 발송."""
    from trendletter import telegram as tg

    cfg = load()
    if args.check:
        try:
            info = tg.check(cfg)
            _say("봇 @%s → %s (%s)" % (info["bot"], info["chat"], info["chat_type"]))
            return 0
        except Exception as exc:  # noqa: BLE001
            _say(str(exc))
            return 1

    issue = store.load_draft(_resolve_draft(args.draft))
    base = (args.url or cfg.get("telegram.html_base") or "").strip()
    url = (base.rstrip("/") + "/" + issue.slug + ".html") if base else ""
    r = tg.send(issue, url, cfg, dry_run=not args.send)
    if r.get("dry_run"):
        _say("[보내지 않음 — %d자]\n" % r["length"])
        _say(r["text"])
        _say("\n실제로 보내려면 --send 를 붙이세요.")
    else:
        _say("발송 완료 (message_id %s)" % r.get("message_id"))
    return 0


def cmd_editor(args) -> int:
    from trendletter.editor.app import run

    cfg = load()
    run(cfg.get("editor.host", "127.0.0.1"), int(cfg.get("editor.port", 8765)))
    return 0


def cmd_sources(args) -> int:
    """각 수집원에 실제로 접속해 응답 여부와 건수를 확인한다."""
    from datetime import datetime, timedelta

    from trendletter.collectors import build
    from trendletter.http import Fetcher

    cfg = load()
    since = datetime.now() - timedelta(days=args.days or cfg.get("collect.days", 7))
    fetcher = Fetcher(use_cache=False)
    ok = fail = off = 0
    for s in cfg.sources:
        if not s.get("enabled"):
            _say("  -  %-24s 비활성" % s["name"])
            off += 1
            continue
        try:
            n = len(build(s, fetcher).collect(since, 20))
            _say("  %s %-24s %3d건  (%s/%s)" % ("OK" if n else "??", s["name"], n, s["track"], s["role"]))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            _say("  X  %-24s %s" % (s["name"], exc))
            fail += 1
    _say("\n정상 %d · 실패 %d · 비활성 %d" % (ok, fail, off))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="trendletter", description="AI 정보동향지 반자동화")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="수집만 실행")
    c.add_argument("--days", type=int)
    c.add_argument("--source", action="append", help="특정 수집원만 (여러 번 지정 가능)")
    c.add_argument("--no-cache", action="store_true")
    c.set_defaults(func=cmd_collect)

    d = sub.add_parser("draft", help="초안 생성")
    d.add_argument("--days", type=int)
    d.add_argument("--theme", help="이번 호 주제")
    d.add_argument("--reuse", action="store_true", help="마지막 수집 결과 재사용")
    d.add_argument("--no-cache", action="store_true")
    d.add_argument("--no-llm", action="store_true", help="Claude 초안 작성을 건너뜀")
    d.add_argument("--no-video", action="store_true", help="관련 영상 수집을 건너뜀")
    d.set_defaults(func=cmd_draft)

    r = sub.add_parser("render", help="초안 HTML 미리보기")
    r.add_argument("--draft")
    r.set_defaults(func=cmd_render)

    b = sub.add_parser("publish", help="확정본 발행")
    b.add_argument("--draft")
    b.add_argument("--force", action="store_true")
    b.add_argument("--no-telegram", action="store_true", help="발행 후 텔레그램 발송 안 함")
    b.add_argument("--dry-run", action="store_true", help="텔레그램 내용만 미리 보기")
    b.set_defaults(func=cmd_publish)

    g = sub.add_parser("telegram", help="텔레그램 확인·미리보기·발송")
    g.add_argument("--check", action="store_true", help="봇·대상 방 연결 확인")
    g.add_argument("--send", action="store_true", help="실제로 보냄 (없으면 미리보기)")
    g.add_argument("--draft")
    g.add_argument("--url", help="HTML 전문 주소 (없으면 설정값 사용)")
    g.set_defaults(func=cmd_telegram)

    e = sub.add_parser("editor", help="로컬 웹 편집기")
    e.set_defaults(func=cmd_editor)

    s = sub.add_parser("sources", help="수집원 상태 점검")
    s.add_argument("--days", type=int)
    s.set_defaults(func=cmd_sources)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
