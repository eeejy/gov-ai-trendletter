@echo off
chcp 65001 >nul
REM Windows 운영용 실행 파일. 바탕화면 바로가기로 만들어 사용한다.
cd /d "%~dp0"

if not exist .venv (
  echo 가상환경을 만듭니다...
  python -m venv .venv || goto :err
  .venv\Scripts\python.exe -m pip install --upgrade pip >nul
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
)

if "%~1"=="" (
  echo ===========================================
  echo   AI 정보동향지 반자동화
  echo ===========================================
  echo   1^) 수집원 점검
  echo   2^) 이번 주 초안 만들기
  echo   3^) 편집기 열기
  echo   4^) 발행
  echo ===========================================
  set /p n="번호 선택: "
  if "!n!"=="" set n=3
  if "%n%"=="1" set ARGS=sources
  if "%n%"=="2" set ARGS=draft
  if "%n%"=="3" set ARGS=editor
  if "%n%"=="4" set ARGS=publish
) else (
  set ARGS=%*
)

.venv\Scripts\python.exe run.py %ARGS%
goto :eof

:err
echo 설치에 실패했습니다. Python 3.9 이상이 설치되어 있는지 확인하세요.
pause
