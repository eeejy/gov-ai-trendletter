"""config/*.yaml 로딩. Mac·Windows 모두 프로젝트 루트를 기준으로 경로를 잡는다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError("설정 파일이 없습니다: %s" % path)
    with path.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


class Config:
    def __init__(self) -> None:
        self.settings: Dict[str, Any] = _load("settings.yaml")
        self.sources: List[Dict[str, Any]] = _load("sources.yaml").get("sources", [])
        self.ontology: Dict[str, Any] = _load("ontology.yaml")
        self.secrets: Dict[str, Any] = {}
        if (CONFIG_DIR / "secrets.yaml").exists():
            self.secrets = _load("secrets.yaml")

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
    def source(self, source_id: str) -> Dict[str, Any]:
        for s in self.sources:
            if s["id"] == source_id:
                return s
        raise KeyError("알 수 없는 수집원: %s" % source_id)

    def enabled_sources(self, only: List[str] = None) -> List[Dict[str, Any]]:
        if only:
            return [self.source(sid) for sid in only]
        return [s for s in self.sources if s.get("enabled")]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.settings
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, dotted: str, default: Any = None) -> Any:
        env = "TRENDLETTER_" + dotted.replace(".", "_").upper()
        if os.environ.get(env):
            return os.environ[env]
        node: Any = self.secrets
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


_cfg: Config = None


def load() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg
