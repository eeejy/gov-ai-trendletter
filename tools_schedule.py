#!/usr/bin/env python3
"""정해진 요일·시각에 초안이 자동으로 만들어지도록 예약한다.

  python tools_schedule.py            등록 파일을 만들고 설치 방법을 안내
  python tools_schedule.py --day 5 --time 08:00
  python tools_schedule.py --remove   예약 해제 방법 안내

**초안까지만 자동이다.** 발행과 직원 발송은 담당자가 편집기에서 확인한 뒤 한다.
"""
import argparse
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL = "kr.trendletter.weekly"
DAYS = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 0: "일"}


def python_bin() -> Path:
    venv = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    return venv if venv.exists() else Path(sys.executable)


def mac(day: int, hour: int, minute: int, days_back: int) -> None:
    plist = ROOT / ("%s.plist" % LABEL)
    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>WorkingDirectory</key><string>%s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>%s</string>
    <string>weekly</string>
    <string>--days</string><string>%d</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>%d</integer>
    <key>Hour</key><integer>%d</integer>
    <key>Minute</key><integer>%d</integer>
  </dict>
  <key>StandardOutPath</key><string>%s/weekly.log</string>
  <key>StandardErrorPath</key><string>%s/weekly.err</string>
</dict>
</plist>
"""
        % (LABEL, ROOT, python_bin(), ROOT / "run.py", days_back,
           day, hour, minute, logs, logs),
        encoding="utf-8",
    )
    print("등록 파일을 만들었습니다: %s\n" % plist.name)
    print("아래 한 줄을 터미널에 붙여 넣으면 예약됩니다.\n")
    print("  cp '%s' ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/%s.plist\n"
          % (plist, LABEL))
    print("확인 : launchctl list | grep %s" % LABEL)
    print("해제 : launchctl unload ~/Library/LaunchAgents/%s.plist" % LABEL)
    print("기록 : %s/weekly.log" % logs)


def windows(day: int, hour: int, minute: int, days_back: int) -> None:
    wd = {1: "MON", 2: "TUE", 3: "WED", 4: "THU", 5: "FRI", 6: "SAT", 0: "SUN"}[day]
    bat = ROOT / "weekly.bat"
    bat.write_text(
        "@echo off\r\nchcp 65001 >nul\r\ncd /d \"%%~dp0\"\r\n"
        "\"%s\" run.py weekly --days %d >> data\\logs\\weekly.log 2>&1\r\n"
        % (python_bin(), days_back),
        encoding="utf-8",
    )
    (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    print("실행 파일을 만들었습니다: weekly.bat\n")
    print("아래 한 줄을 명령 프롬프트에 붙여 넣으면 예약됩니다.\n")
    print('  schtasks /create /tn "AI동향지 주간초안" /tr "\\"%s\\"" /sc weekly /d %s /st %02d:%02d /f\n'
          % (bat, wd, hour, minute))
    print('확인 : schtasks /query /tn "AI동향지 주간초안"')
    print('해제 : schtasks /delete /tn "AI동향지 주간초안" /f')
    print("기록 : data\\logs\\weekly.log")


def main() -> int:
    ap = argparse.ArgumentParser(description="주간 초안 자동 생성 예약")
    ap.add_argument("--day", type=int, default=5,
                    help="요일 (1=월 … 5=금, 0=일). 기본 5(금)")
    ap.add_argument("--time", default="08:00", help="시각 HH:MM. 기본 08:00")
    ap.add_argument("--days-back", type=int, default=7, help="수집 기간(일)")
    ap.add_argument("--remove", action="store_true", help="해제 방법만 안내")
    a = ap.parse_args()

    hour, minute = (int(x) for x in a.time.split(":"))
    print("=" * 66)
    print(" 주간 초안 자동 생성 — 매주 %s요일 %02d:%02d, 최근 %d일" %
          (DAYS.get(a.day, "?"), hour, minute, a.days_back))
    print("=" * 66)
    print()
    print(" 초안까지만 자동으로 만들고 담당자에게 텔레그램으로 알립니다.")
    print(" **발행과 직원 발송은 담당자가 편집기에서 확인한 뒤 합니다.**")
    print()

    if a.remove:
        if platform.system() == "Darwin":
            print("  launchctl unload ~/Library/LaunchAgents/%s.plist" % LABEL)
        else:
            print('  schtasks /delete /tn "AI동향지 주간초안" /f')
        return 0

    if platform.system() == "Darwin":
        mac(a.day, hour, minute, a.days_back)
    else:
        windows(a.day, hour, minute, a.days_back)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
