#!/bin/bash
# Mac 실행 파일. Finder 에서 더블클릭해도 동작한다.
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "가상환경을 만듭니다..."
  python3 -m venv .venv || exit 1
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt || exit 1
fi

if [ $# -eq 0 ]; then
  echo "==========================================="
  echo "  정보동향지 반자동화"
  echo "==========================================="
  echo "  1) 설정 열기        분야·수집원·키워드"
  echo "  2) 준비 상태 점검"
  echo "  3) 수집원 점검"
  echo "  4) 이번 호 초안 만들기"
  echo "  5) 편집기 열기      (기본)"
  echo "  6) 발행"
  echo "==========================================="
  read -r -p "번호 선택 [5]: " n
  case "${n:-5}" in
    1) set -- editor --setup ;;
    2) set -- doctor ;;
    3) set -- sources ;;
    4) set -- draft ;;
    5) set -- editor ;;
    6) set -- publish ;;
    *) echo "1~6 중에서 고르세요."; exit 1 ;;
  esac
fi

./.venv/bin/python run.py "$@"
status=$?
echo
read -r -p "엔터를 누르면 창을 닫습니다." _
exit $status
