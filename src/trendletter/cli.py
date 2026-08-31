"""명령줄 진입점.

  collect   수집만 하고 data/raw 에 저장
  draft     수집 결과로 초안(JSON) 생성
  render    초안을 HTML 로 미리보기
  publish   초안을 확정본 HTML 로 발행
  editor    로컬 웹 편집기 실행
  sources   수집원 상태 점검
  doctor    설치·설정이 갖춰졌는지 한 번에 점검
  daily     일간 브리핑 (Claude 없이 수집·점수만)
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
    # 일부만 골라 돌린 결과는 '마지막 수집본' 으로 잡히면 안 된다
    path = store.save_raw(articles, partial=bool(args.source))
    _say("총 %d건 → %s" % (len(articles), path))
    if args.source:
        _say("일부 수집원만 돌렸으므로 partial- 로 저장했습니다."
             " draft --reuse 는 이 파일을 쓰지 않습니다.")
    return 0


def cmd_draft(args) -> int:
    cfg = load()
    if args.reuse:
        raw = store.latest_raw()
        if not raw:
            _say("재사용할 수집 결과가 없습니다. 먼저 collect 를 실행하세요.")
            return 1
        articles = store.load_raw(raw)
        # --source 로 일부만 수집한 결과가 '마지막 수집본' 으로 남아 있을 수 있다.
        # 그걸 모르고 재사용하면 특정 기관 소식이 통째로 빠진 채 초안이 나온다.
        used = {a.source_id for a in articles}
        enabled = {s["id"] for s in cfg.enabled_sources()}
        _say("수집 결과 재사용: %s (%d건 · 수집원 %d/%d곳)"
             % (raw.name, len(articles), len(used), len(enabled)))
        missing = enabled - used
        if missing:
            names = {s["id"]: s["name"] for s in cfg.enabled_sources()}
            _say("  ! 이 수집본에는 아래 수집원이 빠져 있습니다. 해당 기관 소식은"
                 " 후보에 오르지 않습니다:")
            _say("    %s" % ", ".join(sorted(names[m] for m in missing)))
            _say("  ! 전체를 보려면 --reuse 없이 실행하세요.")
    else:
        _say("수집 시작 (최근 %d일)" % (args.days or cfg.get("collect.days", 7)))
        articles = pipeline.collect(
            cfg, days=args.days, use_cache=not args.no_cache, progress=_say
        )
        store.save_raw(articles)

    issue = pipeline.make_draft(articles, cfg, days=args.days,
                                theme=args.theme or "", progress=_say)
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


def cmd_daily(args) -> int:
    """일간 브리핑. Claude 를 쓰지 않고 수집·점수만으로 상위 몇 건을 보낸다.

    주간 동향지와는 다른 물건이다. 시사점을 쓰지 않고 기사 본문 앞머리와
    원문 링크만 보낸다. 판단은 사람이 원문을 보고 한다.
    사람 검토 없이 나가므로, 요약을 지어내지 않는 것이 안전 설계다.
    """
    from trendletter import telegram as tg
    from trendletter.scoring import diversify

    cfg = load()
    days = args.days or 1
    top = args.top or int(cfg.get("daily.top", 6))
    floor = float(args.min_score if args.min_score is not None
                  else cfg.get("daily.min_score", 8.0))

    started = datetime.now()
    _say("[%s] 일간 브리핑 (최근 %d일)" % (started.strftime("%m-%d %H:%M"), days))
    articles = pipeline.collect(cfg, days=days, progress=_say)
    if not articles:
        _say("수집된 자료가 없습니다.")
    clusters = pipeline.build_clusters(articles, cfg)
    pipeline.enrich_bodies(clusters, progress=_say)
    clusters = pipeline.build_clusters(articles, cfg)

    ranked = [c for c in clusters if c.score >= floor]

    # 어제 이미 보낸 것은 빼고 본다. 같은 소식이 이틀 연속 오면 신뢰를 잃는다.
    seen = {} if args.ignore_seen else store.load_seen()
    fresh = [c for c in ranked if not store.already_sent(seen, c.lead)]
    dropped = len(ranked) - len(fresh)

    # 한 사건을 여러 매체가 제각각 제목으로 쓰면 제목 유사도로 안 묶인다.
    # 고를 때 걸러 같은 사건이 목록을 도배하지 않게 한다.
    picked = diversify(fresh, top)

    _say("클러스터 %d개 · %.1f점 이상 %d건 · 이미 보낸 것 %d건 제외 → %d건"
         % (len(clusters), floor, len(ranked), dropped, len(picked)))

    text = tg.daily_text(picked, len(articles), started.date())
    _say("\n" + text + "\n")
    _say("(%d자)" % len(text))

    if not args.send:
        _say("실제로 보내려면 --send 를 붙이세요.")
        return 0
    try:
        tg.notify_admin(text, cfg) if args.to_admin else tg.send_text(text, cfg)
    except Exception as exc:  # noqa: BLE001
        _say("발송 실패: %s" % exc)
        return 1
    _say("발송 완료")

    # 보낸 것만 기록한다. 미발송으로 본 것은 남기지 않는다.
    today = datetime.now().strftime("%Y-%m-%d")
    for c in picked:
        store.remember(seen, c.lead, today)
    path = store.save_seen(seen)
    _say("발송 기록 %d건 → %s" % (len(seen), path))
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
        issue = pipeline.make_draft(articles, cfg, days=args.days, progress=_say)
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
        warn = pipeline.health_warnings(pipeline.LAST_HEALTH)
        if warn:
            lines += ["", "⚠️ 수집 상태 확인이 필요합니다"] + ["· " + w for w in warn]
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


def _chk(ok: bool, label: str, detail: str = "", fix: str = "") -> bool:
    mark = "OK  " if ok else "X   "
    _say("  %s%-26s %s" % (mark, label, detail))
    if not ok and fix:
        _say("      → %s" % fix)
    return ok


def cmd_doctor(args) -> int:
    """새 PC에서 바로 쓸 수 있는 상태인지 한 번에 점검한다."""
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[2]
    bad = 0

    _say("\n[1] 실행 환경")
    v = sys.version_info
    bad += not _chk(v >= (3, 9), "파이썬 %d.%d" % (v.major, v.minor),
                    "3.9 이상 필요", "python3 -m venv .venv 로 다시 만드세요")
    for mod, why in (("flask", "편집기"), ("yaml", "설정 읽기"),
                     ("requests", "수집"), ("bs4", "HTML 파싱"),
                     ("jinja2", "동향지 렌더"), ("lxml", "HTML 파서")):
        try:
            __import__(mod)
            _chk(True, mod, why)
        except ImportError:
            bad += 1
            _chk(False, mod, "없음 (%s)" % why, "./.venv/bin/pip install -r requirements.txt")
    try:
        import fontTools  # noqa: F401
        import brotli  # noqa: F401
        _chk(True, "fonttools+brotli", "표지 글꼴 내장")
    except ImportError:
        _chk(False, "fonttools+brotli", "없음 — 기본 글꼴로 렌더됩니다", "선택 사항입니다")

    _say("\n[2] 설정 파일")
    for rel, need in (("config/settings.yaml", True), ("config/sources.yaml", True),
                      ("config/ontology.yaml", True), ("config/korea_kr_depts.yaml", True),
                      ("config/secrets.yaml", False)):
        f = root / rel
        if f.exists():
            try:
                import yaml
                yaml.safe_load(f.read_text(encoding="utf-8"))
                _chk(True, rel, "%.1fKB" % (f.stat().st_size / 1024))
            except Exception as exc:  # noqa: BLE001
                bad += 1
                _chk(False, rel, "형식 오류: %s" % exc)
        elif need:
            bad += 1
            _chk(False, rel, "없음")
        else:
            _chk(False, rel, "없음 — 텔레그램 안 씀",
                 "config/secrets.example.yaml 를 복사해서 채우세요")

    _say("\n[3] 쓰기 권한")
    for rel in ("data/raw", "data/drafts", "data/issues", "data/cache"):
        d = root / rel
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".probe"
            probe.write_text("x"); probe.unlink()
            _chk(True, rel, "쓰기 가능")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            _chk(False, rel, str(exc))

    _say("\n[4] Claude CLI (초안 작성)")
    exe = shutil.which("claude")
    if exe:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True,
                                 text=True, timeout=20)
            _chk(True, "claude", out.stdout.strip() or exe)
        except Exception as exc:  # noqa: BLE001
            _chk(False, "claude", "응답 없음: %s" % exc)
    else:
        _chk(False, "claude", "없음 — 초안이 규칙 기반으로만 작성됩니다",
             "npm i -g @anthropic-ai/claude-code")

    _say("\n[5] 텔레그램")
    cfg = load()
    if not cfg.secret("telegram.token"):
        _chk(False, "봇 토큰", "설정 안 됨", "config/secrets.yaml 에 token 을 넣으세요")
    else:
        from trendletter import telegram as tgmod
        try:
            res = tgmod.check(cfg)
            _chk(bool(res.get("bot")), "봇", res.get("bot") or res.get("error", ""))
            _chk(bool(res.get("chat")), "대상 방", res.get("chat") or "chat_id 확인 필요",
                 "python run.py telegram --find")
        except Exception as exc:  # noqa: BLE001
            _chk(False, "연결", str(exc))

    _say("\n[6] 수집원 연결 (대표 3곳)")
    import urllib.request
    for name, url in (("해양경찰청", "https://www.kcg.go.kr"),
                      ("korea.kr", "https://www.korea.kr"),
                      ("서울 AI 플랫폼", "https://seoulai.saif.or.kr")):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                _chk(r.status < 400, name, "HTTP %d" % r.status)
        except Exception as exc:  # noqa: BLE001
            _chk(False, name, "%s" % type(exc).__name__)
            _say("      → 방화벽·프록시 환경이면 정상일 수 있습니다")

    _say("")
    if bad:
        _say("필수 항목 %d개가 준비되지 않았습니다. 위의 → 안내를 따라 주세요.\n" % bad)
        return 1
    _say("필수 항목은 모두 준비됐습니다.  python run.py editor 로 시작하세요.\n")
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

    dl = sub.add_parser("daily", help="일간 브리핑 (Claude 없이 수집·점수만)")
    dl.add_argument("--days", type=int, help="며칠치를 볼지 (기본 1)")
    dl.add_argument("--top", type=int, help="최대 몇 건 (기본 6)")
    dl.add_argument("--min-score", type=float, help="이 점수 미만은 싣지 않음 (기본 8.0)")
    dl.add_argument("--send", action="store_true", help="실제로 발송 (없으면 미리보기)")
    dl.add_argument("--to-admin", action="store_true", help="채널 대신 담당자에게만")
    dl.add_argument("--ignore-seen", action="store_true",
                    help="이미 보낸 것도 다시 넣기 (시험용)")
    dl.set_defaults(func=cmd_daily)

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

    dr = sub.add_parser("doctor", help="설치·설정 상태를 한 번에 점검")
    dr.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sources", help="수집원 상태 점검")
    s.add_argument("--days", type=int)
    s.set_defaults(func=cmd_sources)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
