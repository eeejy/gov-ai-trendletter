# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 설정. 맥과 윈도우가 같은 파일을 쓴다.

프로그램에 딸려 가는 것: 서식·프롬프트·글꼴·처음 쓸 분야와 설정.
사용자가 고치는 것(설정·산출물)은 앱 안이 아니라 사용자 폴더로 간다 —
config.py 의 ensure_data_root() 가 첫 실행 때 복사한다.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

# 비밀 키와 남의 기관 설정은 절대 앱에 넣지 않는다.
# config/ 를 통째로 넣으면 secrets.yaml 이 따라간다 — 텔레그램 토큰과
# API 키가 배포물에 실린다. 그래서 넣을 파일을 하나씩 적는다.
SKIP = {"secrets.yaml", ".DS_Store"}
SKIP_DIR = {".backup", "__pycache__", ".git"}


def tree(rel, dest=None):
    """폴더 하나를 통째로. 비밀 파일과 사본 폴더는 뺀다."""
    src = ROOT / rel
    out = []
    if not src.exists():
        return out
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        if item.name in SKIP or item.suffix in (".pyc",):
            continue
        if any(part in SKIP_DIR for part in item.relative_to(src).parts):
            continue
        out.append((str(item), str(Path(dest or rel) / item.relative_to(src).parent)))
    return out


datas = (tree("src") + tree("templates") + tree("prompts")
         + tree("profiles") + tree("config") + tree("assets")
         + tree("src/trendletter/editor/templates", "trendletter/editor/templates"))

_leaked = [s for s, _ in datas if Path(s).name in SKIP]
assert not _leaked, "비밀 파일이 앱에 들어가려 합니다: %s" % _leaked

hidden = [
    "trendletter.cli", "trendletter.editor.app", "trendletter.editor.settings",
    "trendletter.providers.claude_cli", "trendletter.providers.anthropic_api",
    "trendletter.providers.openai_api", "trendletter.providers.gemini_api",
    "trendletter.collectors.gov", "trendletter.collectors.news",
    "trendletter.collectors.dev",
    "ruamel.yaml", "lxml._elementpath", "bs4", "jinja2", "flask",
    "fontTools.subset", "brotli",
]

a = Analysis(
    [str(ROOT / "installer" / "app_main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# 실행 파일 이름은 영문으로 둔다. 한글로 하면 zip 안에서 이름이 깨진다 —
# Compress-Archive 가 UTF-8 표시를 달지 않아, 푸는 도구에 따라 아예 못 연다.
# 사람이 보는 이름(창 제목·맥 앱 이름)은 한글 그대로다.
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="trendletter",
    debug=False,
    strip=False,
    upx=False,
    console=(sys.platform != "darwin"),   # 맥은 창 없이, 윈도우는 진행 상황이 보이게
    icon=str(ROOT / "installer" / "icon.icns") if (ROOT / "installer" / "icon.icns").exists() else None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="trendletter")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="동향지.app",
        icon=str(ROOT / "installer" / "icon.icns") if (ROOT / "installer" / "icon.icns").exists() else None,
        bundle_identifier="kr.go.trendletter.app",
        info_plist={
            "CFBundleName": "동향지",
            "CFBundleDisplayName": "정보동향지",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            # 인터넷에서 수집하므로 평문 접속을 막지 않는다 (기관 내부망 대비)
            "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
        },
    )
