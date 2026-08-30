"""공통 HTTP 계층. 캐시·재시도·인코딩 보정을 한 곳에서 처리한다."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from .config import load

# 호스트별 최소 요청 간격(초). Reddit 은 연속 요청에 429 를 준다(2026-08-29 확인).
HOST_INTERVAL = {
    "www.reddit.com": 3.0,
    "old.reddit.com": 3.0,
    "api.github.com": 1.5,
}
DEFAULT_INTERVAL = 0.4

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class Fetcher:
    def __init__(self, use_cache: bool = True) -> None:
        cfg = load()
        self.cfg = cfg
        self.timeout = cfg.get("collect.timeout", 25)
        self.cache_hours = cfg.get("collect.cache_hours", 6)
        self.use_cache = use_cache
        self.session = requests.Session()
        # 일부 정부 사이트는 사용자 지정 UA를 차단하므로 일반 브라우저 UA를 쓴다.
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_UA,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            }
        )
        self._last_hit: Dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        gap = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        last = self._last_hit.get(host)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _cache_path(self, url: str):
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".bin"
        return self.cfg.cache_dir / name

    def get(self, url: str, retries: int = 2, encoding: Optional[str] = None) -> str:
        path = self._cache_path(url)
        if self.use_cache and path.exists():
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if age < timedelta(hours=self.cache_hours):
                return path.read_bytes().decode(encoding or "utf-8", "replace")

        last = None
        for attempt in range(retries + 1):
            try:
                self._throttle(url)
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 429:
                    # 속도 제한은 더 길게 기다린 뒤 다시 시도한다.
                    time.sleep(5.0 * (attempt + 1))
                    r.raise_for_status()
                r.raise_for_status()
                body = r.content
                path.write_bytes(body)
                if encoding:
                    return body.decode(encoding, "replace")
                # requests 의 추정 인코딩이 ISO-8859-1 로 잘못 잡히는 경우가 잦다.
                if r.encoding and r.encoding.lower() not in ("iso-8859-1",):
                    return body.decode(r.encoding, "replace")
                return body.decode(r.apparent_encoding or "utf-8", "replace")
            except Exception as exc:      # noqa: BLE001 - 수집은 한 소스 실패로 멈추지 않는다
                last = exc
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError("요청 실패: %s (%s)" % (url, last))

    def get_json(self, url: str, retries: int = 2):
        import json

        return json.loads(self.get(url, retries=retries))

    def post(self, url: str, data, retries: int = 2) -> str:
        """POST 로만 목록을 주는 사이트용. 결과는 캐시하지 않는다.

        data 는 (key, value) 튜플의 리스트를 받는다.
        같은 이름의 필드를 여러 번 보내야 하는 곳이 있기 때문이다.
        """
        last = None
        for attempt in range(retries + 1):
            try:
                self._throttle(url)
                r = self.session.post(
                    url,
                    data=data,
                    timeout=self.timeout,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                if r.status_code == 429:
                    time.sleep(5.0 * (attempt + 1))
                r.raise_for_status()
                return r.content.decode(r.encoding or "utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError("요청 실패(POST): %s (%s)" % (url, last))
