@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM 운영용 실행 파일. 바탕화면 바로가기로 만들어 쓴다.
REM 괄호 블록 안에서는 %n% 가 실행 전에 펼쳐진다. 그래서 메뉴를 블록 밖으로
REM 빼고 !n! 를 쓴다. 예전 판은 무엇을 골라도 인자 없이 실행됐다.
cd /d "%~dp0"

if not exist .venv (
  echo 가상환경을 만듭니다...
  python -m venv .venv || goto :err
  .venv\Scripts\python.exe -m pip install --upgrade pip >nul
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
)

if not "%~1"=="" (
  set "ARGS=%*"
  goto :run
)

echo ===========================================
echo   정보동향지 반자동화
echo ===========================================
echo   1^) 설정 열기        분야·수집원·키워드
echo   2^) 준비 상태 점검
echo   3^) 수집원 점검
echo   4^) 이번 호 초안 만들기
echo   5^) 편집기 열기      (기본)
echo   6^) 발행
echo ===========================================
set "n="
set /p n="번호 선택 [5]: "
if "!n!"=="" set "n=5"

if "!n!"=="1" set "ARGS=editor --setup"
if "!n!"=="2" set "ARGS=doctor"
if "!n!"=="3" set "ARGS=sources"
if "!n!"=="4" set "ARGS=draft"
if "!n!"=="5" set "ARGS=editor"
if "!n!"=="6" set "ARGS=publish"

if not defined ARGS (
  echo 1~6 중에서 고르세요.
  pause
  goto :eof
)

:run
.venv\Scripts\python.exe run.py !ARGS!
if errorlevel 1 pause
goto :eof

:err
echo 설치에 실패했습니다. Python 3.9 이상이 설치되어 있는지 확인하세요.
pause
