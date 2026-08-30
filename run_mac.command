#!/bin/bash
# Mac 개발용 실행 파일. Finder 에서 더블클릭해도 동작한다.
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "가상환경을 만듭니다..."
  python3 -m venv .venv || exit 1
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt || exit 1
fi

if [ $# -eq 0 ]; then
  echo "==========================================="
  echo "  AI 정보동향지 반자동화"
  echo "==========================================="
  echo "  1) 수집원 점검"
  echo "  2) 이번 주 초안 만들기"
  echo "  3) 편집기 열기"
  echo "  4) 발행"
  echo "==========================================="
  read -r -p "번호 선택: " n
  case "$n" in
    1) set -- sources ;;
    2) set -- draft ;;
    3) set -- editor ;;
    4) set -- publish ;;
    *) echo "취소"; exit 0 ;;
  esac
fi

./.venv/bin/python run.py "$@"
status=$?
echo
read -r -p "엔터를 누르면 창을 닫습니다." _
exit $status
