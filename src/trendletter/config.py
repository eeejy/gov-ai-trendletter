"""설정 로딩.

분야 하나가 `profiles/<id>/` 폴더 하나다. 코드는 분야를 모른다 —
무엇을 모으고 무엇을 중요하게 볼지는 전부 그 폴더가 정한다.

  config/settings.yaml        앱 전역 (출력 경로·편집기·모델·현재 분야)
  config/lexicon.common.yaml  분야 무관 낱말 사전
  profiles/<id>/profile.yaml  분야 정의 (트랙·관문·감점)
  profiles/<id>/sources.yaml  수집원
  profiles/<id>/ontology.yaml 관련도
  profiles/<id>/lexicon.yaml  분야 낱말 사전

`ROOT` 는 환경변수로 바꿀 수 있다. 설치형에서 프로그램과 데이터를 가르는 데 쓴다.
  TRENDLETTER_ROOT=/path/to/data
분야는 환경변수로도 고를 수 있다.
  TRENDLETTER_PROFILE=intl-coop
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 프로그램이 놓인 곳 (프로파일·프롬프트·서식이 여기 있다)
APP_ROOT = Path(__file__).resolve().parents[2]
# 설정과 산출물이 놓인 곳. 설치형에서는 사용자 폴더를 가리킨다.
ROOT = Path(os.environ.get("TRENDLETTER_ROOT") or APP_ROOT).resolve()

CONFIG_DIR = ROOT / "config"
PROFILES_DIR = ROOT / "profiles"
# 설치형에서 프로파일은 프로그램 쪽에 있고 데이터만 사용자 쪽에 있을 수 있다.
if not PROFILES_DIR.exists():
    PROFILES_DIR = APP_ROOT / "profiles"


def _load(path: Path, required: bool = True) -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError("설정 파일이 없습니다: %s" % path)
        return {}
    with path.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


def _rx(pattern: str) -> Optional[re.Pattern]:
    """YAML 여러 줄로 적은 정규식을 컴파일한다. 줄바꿈과 들여쓰기는 지운다."""
    if not pattern:
        return None
    flat = re.sub(r"\s*\n\s*", "", str(pattern)).strip()
    if not flat:
        return None
    try:
        return re.compile(flat, re.I)
    except re.error:
        return None


def profiles() -> List[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir()
                  if p.is_dir() and (p / "profile.yaml").exists())


class Config:
    def __init__(self, profile_id: Optional[str] = None) -> None:
        self.settings: Dict[str, Any] = _load(CONFIG_DIR / "settings.yaml")

        self.profile_id = (profile_id
                           or os.environ.get("TRENDLETTER_PROFILE")
                           or self.settings.get("profile")
                           or "ai")
        self.profile_dir = PROFILES_DIR / self.profile_id
        if not self.profile_dir.exists():
            have = ", ".join(profiles()) or "(없음)"
            raise FileNotFoundError(
                "분야를 찾을 수 없습니다: %s\n  있는 분야: %s" % (self.profile_id, have))

        self.profile: Dict[str, Any] = _load(self.profile_dir / "profile.yaml")
        self.sources: List[Dict[str, Any]] = (
            _load(self.profile_dir / "sources.yaml").get("sources") or [])
        self.ontology: Dict[str, Any] = _load(self.profile_dir / "ontology.yaml")

        # 낱말 사전은 공통 위에 분야를 덧씌운다.
        common = _load(CONFIG_DIR / "lexicon.common.yaml", required=False)
        mine = _load(self.profile_dir / "lexicon.yaml", required=False)
        self.lexicon: Dict[str, Any] = {}
        for key in set(common) | set(mine):
            a, b = common.get(key), mine.get(key)
            if isinstance(a, dict) or isinstance(b, dict):
                self.lexicon[key] = {**(a or {}), **(b or {})}
            else:
                self.lexicon[key] = list(a or []) + list(b or [])

        self.secrets: Dict[str, Any] = _load(CONFIG_DIR / "secrets.yaml",
                                             required=False)
        self._rx_cache: Dict[str, Any] = {}

    # --- 경로 -----------------------------------------------------------
    def path(self, key: str) -> Path:
        rel = self.settings.get("output", {}).get(key)
        if not rel:
            raise KeyError("output.%s 설정이 없습니다" % key)
        p = ROOT / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        p = ROOT / "data" / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # --- 조회 -----------------------------------------------------------
    @staticmethod
    def _dig(root: Any, dotted: str, default: Any) -> Any:
        node = root
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    _MISSING = object()

    def get(self, dotted: str, default: Any = None) -> Any:
        """settings 를 먼저 보고, 없으면 분야 프로파일을 본다.

        분야가 정하는 값(트랙·관문·감점)은 프로파일에 있고 나머지는 settings 에
        있다. 부르는 쪽이 어디에 있는지 신경 쓰지 않아도 되게 한다.
        """
        got = self._dig(self.settings, dotted, self._MISSING)
        if got is not self._MISSING:
            return got
        got = self._dig(self.profile, dotted, self._MISSING)
        return default if got is self._MISSING else got

    def prof(self, dotted: str, default: Any = None) -> Any:
        return self._dig(self.profile, dotted, default)

    def lex(self, key: str, default: Any = None) -> Any:
        return self.lexicon.get(key, default if default is not None else [])

    def rx(self, dotted: str) -> Optional[re.Pattern]:
        """프로파일에 적힌 정규식을 컴파일해 캐시한다."""
        if dotted not in self._rx_cache:
            self._rx_cache[dotted] = _rx(self.get(dotted, ""))
        return self._rx_cache[dotted]

    # --- 트랙 -----------------------------------------------------------
    def tracks(self) -> List[Dict[str, Any]]:
        return list(self.profile.get("tracks") or [])

    def track_keys(self) -> List[str]:
        return [str(t["key"]) for t in self.tracks()]

    def track(self, key: str) -> Dict[str, Any]:
        for t in self.tracks():
            if str(t.get("key")) == str(key):
                return t
        return {}

    def quota(self, key: str) -> List[int]:
        q = self.track(key).get("quota") or [0, 99]
        return [int(q[0]), int(q[1])]

    # --- 수집원 ---------------------------------------------------------
    def source(self, source_id: str) -> Dict[str, Any]:
        for s in self.sources:
            if s["id"] == source_id:
                return s
        raise KeyError("알 수 없는 수집원: %s" % source_id)

    def enabled_sources(self, only: List[str] = None) -> List[Dict[str, Any]]:
        if only:
            return [self.source(sid) for sid in only]
        return [s for s in self.sources if s.get("enabled")]

    def platforms(self) -> Dict[str, str]:
        """수집원 id → 플랫폼 이름. 예전엔 scoring.py 에 박혀 있었다."""
        return {s["id"]: s["platform"] for s in self.sources if s.get("platform")}

    def secret(self, dotted: str, default: Any = None) -> Any:
        env = "TRENDLETTER_" + dotted.replace(".", "_").upper()
        if os.environ.get(env):
            return os.environ[env]
        return self._dig(self.secrets, dotted, default)


_cfg: Optional[Config] = None


def load(profile_id: Optional[str] = None) -> Config:
    global _cfg
    if _cfg is None or (profile_id and profile_id != _cfg.profile_id):
        _cfg = Config(profile_id)
    return _cfg


def reload() -> Config:
    """설정 파일을 고친 뒤 다시 읽는다 (편집기의 설정 화면에서 쓴다)."""
    global _cfg
    _cfg = None
    return load()
