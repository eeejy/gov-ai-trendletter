"""수집기 레지스트리. sources.yaml 의 collector 값으로 찾는다."""

from __future__ import annotations

from typing import Any, Dict, List, Type

from .base import Collector
from .dev import GithubCollector, HackerNewsCollector, RedditCollector
from .gov import (
    GovBoardCollector,
    KoreaKrListCollector,
    KoreaKrSearchCollector,
    SeoulAiCollector,
)
from .news import (
    GoogleNewsCollector,
    ItNewsMoaCollector,
    RssCollector,
    ZdnetCollector,
)

REGISTRY: Dict[str, Type[Collector]] = {
    c.name: c
    for c in (
        RssCollector,
        ZdnetCollector,
        ItNewsMoaCollector,
        GoogleNewsCollector,
        KoreaKrListCollector,
        KoreaKrSearchCollector,
        GovBoardCollector,
        SeoulAiCollector,
        HackerNewsCollector,
        RedditCollector,
        GithubCollector,
    )
}


def build(source: Dict[str, Any], fetcher) -> Collector:
    name = source.get("collector")
    if name not in REGISTRY:
        raise KeyError("등록되지 않은 수집기: %s (source=%s)" % (name, source.get("id")))
    return REGISTRY[name](source, fetcher)


__all__ = ["Collector", "REGISTRY", "build"]
