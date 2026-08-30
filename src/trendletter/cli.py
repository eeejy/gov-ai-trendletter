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
from datetime import datetime
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
            r = tg.send(issue, url, cfg, dry_run=args.dry_run, html_file=out)
            if r.get("dry_run"):
                _say("\n[보내지 않음 — 미리보기 %d자]\n%s" % (r["length"], r["text"]))
            else:
                _say("텔레그램 발송 완료 (message_id %s)" % r.get("message_id"))
        except Exception as exc:  # noqa: BLE001 - 발행 자체는 이미 끝났다
            _say("텔레그램 발송 실패: %s" % str(exc)[:200])
    return 0


def cmd_weekly(args) -> int:
    """정해진 시간에 자동으로 도는 작업.

    **초안까지만 만들고 멈춘다.** 발행과 직원 발송은 담당자가 편집기에서 확인한 뒤 한다.
    사람 검토 없이 나가면 잘못된 내용이 전 직원에게 전달될 수 있다.
    """
    cfg = load()
    started = datetime.now()
    _say("[%s] 주간 초안 생성 시작" % started.strftime("%Y-%m-%d %H:%M"))

    try:
        articles = pipeline.collect(cfg, days=args.days, progress=_say)
        store.save_raw(articles)
        issue = pipeline.make_draft(articles, cfg, days=args.days)
        issue.meta["sources_used"] = len({a.source_id for a in articles})
        clusters = issue.meta.pop("_clusters", {})
        issue.meta.pop("_all_clusters", None)
        topics = issue.meta.pop("_topics", [])
        if not args.no_llm:
            pipeline.add_trend_items(issue, topics, cfg, progress=_say)
            pipeline.polish(issue, clusters, cfg, progress=_say)
        path = store.save_draft(issue)
    except Exception as exc:  # noqa: BLE001
        _say("실패: %s" % exc)
        if cfg.get("telegram.enabled") and not args.no_notify:
            try:
                from trendletter import telegram as tg

                tg.notify_admin("⚠️ 동향지 초안 생성 실패\n%s" % str(exc)[:300], cfg)
            except Exception:  # noqa: BLE001
                pass
        return 1

    took = int((datetime.now() - started).total_seconds())
    _say("초안 %s · %d건 · %d초 → %s" % (issue.label, len(issue.items), took, path))

    if cfg.get("telegram.enabled") and not args.no_notify:
        lines = [
            "📝 %s 초안이 준비됐습니다 (%d분 %d초)" % (issue.label, took // 60, took % 60),
            "",
            "수집 %d건 → 이슈 %d개 → 게재 후보 %d건"
            % (
                issue.meta.get("collected", 0),
                issue.meta.get("ai_passed", 0),
                len(issue.items),
            ),
            "",
        ]
        lines += ["%d. %s" % (it.no, it.title) for it in issue.items]
        lines += ["", "편집기에서 확인 후 발행하세요.", "http://127.0.0.1:8765"]
        try:
            from trendletter import telegram as tg

            tg.notify_admin("\n".join(lines), cfg)
            _say("담당자 알림 발송 완료")
        except Exception as exc:  # noqa: BLE001
            _say("담당자 알림 실패: %s" % str(exc)[:150])
    return 0


def cmd_telegram(args) -> int:
    """텔레그램 방 찾기 / 연결 확인 / 미리보기 / 발송."""
    from trendletter import telegram as tg

    cfg = load()

    if args.find:
        try:
            info = tg.discover(args.token, cfg)
        except Exception as exc:  # noqa: BLE001
            _say(str(exc))
            return 1

        _say("봇 @%s\n" % info["bot"])
        if info.get("webhook"):
            _say("이 봇에는 웹훅이 걸려 있어 이 방법으로는 방을 찾을 수 없습니다.")
            _say("  현재 웹훅: %s" % info.get("webhook_url", "")[:70])
            _say("  (기존 시스템이 쓰고 있을 수 있으니 웹훅을 끄지 마세요)\n")
            _say("대신 이렇게 하세요.")
            _say("  · 공개 채널이면 chat_id 에 @채널사용자명 을 그대로 적으면 됩니다")
            _say("  · 비공개면 채널 메시지를 @userinfobot 에게 전달하면 알려 줍니다")
            return 0

        if not info["chats"]:
            _say("찾은 방이 없습니다.")
            _say("  채널·그룹에 아무 메시지나 하나 올린 뒤 다시 실행하세요.")
            _say("  (봇은 자기가 들어온 뒤의 메시지만 볼 수 있습니다)")
            return 1

        _say("찾은 방 %d개 — 아래 값을 config/secrets.yaml 에 넣으세요.\n" % len(info["chats"]))
        for c in info["chats"]:
            kind = {"private": "1:1 대화", "group": "그룹",
                    "supergroup": "그룹", "channel": "채널"}.get(c["type"], c["type"])
            name = c["title"] or "(이름 없음)"
            _say("  %-10s %-24s chat_id: %s" % (kind, name[:24], c["id"]))
            if c.get("username"):
                _say("  %-10s %-24s 또는     : @%s" % ("", "", c["username"]))
        _say("\n  직원 배포용 → chat_id,  본인 1:1 대화 → admin_chat_id")
        return 0

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
    from trendletter.config import ROOT

    html_path = load().path("html_dir") / (issue.slug + ".html")
    r = tg.send(
        issue, url, cfg, dry_run=not args.send,
        html_file=html_path if html_path.exists() else None,
    )
    if r.get("dry_run"):
        _say("[보내지 않음 — %d자]\n" % r["length"])
        _say(r["text"])
        if r.get("attach"):
            _say("\n첨부: %s" % r["attach"])
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

    w = sub.add_parser("weekly", help="예약 실행용 — 초안까지만 만들고 담당자에게 알림")
    w.add_argument("--days", type=int)
    w.add_argument("--no-llm", action="store_true")
    w.add_argument("--no-notify", action="store_true", help="담당자 알림 없이")
    w.set_defaults(func=cmd_weekly)

    g = sub.add_parser("telegram", help="텔레그램 확인·미리보기·발송")
    g.add_argument("--check", action="store_true", help="봇·대상 방 연결 확인")
    g.add_argument("--find", action="store_true", help="봇이 볼 수 있는 방과 chat_id 찾기")
    g.add_argument("--token", help="설정에 없을 때 토큰을 직접 주기")
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
