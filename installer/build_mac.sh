#!/bin/bash
# 맥용 .app 만들기.  ./installer/build_mac.sh
#
# 결과: dist/동향지.app  — Finder 에서 더블클릭하면 브라우저가 열린다.
# 받는 사람 컴퓨터에 파이썬이 없어도 된다.
set -e
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "가상환경이 없습니다. python3 -m venv .venv 부터 하세요."; exit 1
fi

echo "[1/3] 빌드 도구 준비"
./.venv/bin/python -m pip install --quiet --upgrade pyinstaller

echo "[2/3] 이전 결과 지우기"
rm -rf build dist

echo "[3/3] 앱 만들기 (2~4분)"
./.venv/bin/python -m PyInstaller --noconfirm --clean installer/trendletter.spec

if [ ! -d "dist/동향지.app" ]; then
  echo "실패: dist/동향지.app 이 만들어지지 않았습니다."; exit 1
fi

# 서명이 없으면 Gatekeeper 가 막는다. 자체 서명이라도 해 두면
# '확인되지 않은 개발자' 한 번만 허용하면 계속 열린다.
codesign --force --deep --sign - "dist/동향지.app" 2>/dev/null \
  && echo "  자체 서명 완료" || echo "  서명 건너뜀 (없어도 열립니다)"

SIZE=$(du -sh "dist/동향지.app" | cut -f1)
echo
echo "완료: dist/동향지.app  ($SIZE)"
echo
echo "처음 열 때 '확인되지 않은 개발자' 라고 나오면"
echo "  앱을 오른쪽 클릭 → 열기 → 열기"
echo "설정과 산출물은 여기에 쌓입니다:"
echo "  ~/Library/Application Support/동향지"
