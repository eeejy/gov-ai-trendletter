"""수집 원본(Article)부터 발행본(Issue)까지의 데이터 모델."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

TRACKS = ("policy", "industry", "dev")

# PDF 서식의 머리표 값과 1:1로 대응한다.
FIELD_LABELS = {
    "policy": "기관 동향",
    "industry": "산업 동향",
    "dev": "기술 동향",
    "internal": "직접 개발형",
}
AUDIENCES = ("전 직원", "사업기획", "정책기획", "현장부서")
IMPACTS = ("높음", "중간", "낮음")
NOTE_KINDS = ("시사점", "향후계획")


def _iso(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


@dataclass
class Article:
    """수집원에서 가져온 원본 1건. 가공하지 않는다."""

    source_id: str
    source_name: str
    track: str
    title: str
    url: str
    published: Optional[datetime] = None
    summary: str = ""
    author: str = ""
    tags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["published"] = _iso(self.published)
        d["key"] = self.key
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Article":
        d = dict(d)
        d.pop("key", None)
        p = d.get("published")
        if isinstance(p, str) and p:
            try:
                d["published"] = datetime.fromisoformat(p)
            except ValueError:
                d["published"] = None
        return cls(**d)


@dataclass
class Cluster:
    """같은 사건을 다룬 기사 묶음. 기사 수가 아니라 매체 수를 중요도 신호로 쓴다."""

    articles: List[Article]
    score: float = 0.0
    reasons: Dict[str, float] = field(default_factory=dict)
    onto: Dict[str, List[str]] = field(default_factory=dict)
    hot_entity: Dict[str, Any] = field(default_factory=dict)  # 여러 플랫폼에 걸친 이름
    work_groups: List[str] = field(default_factory=list)      # 걸린 업무 관련도 그룹
    priority_topic: str = ""                                  # 최우선 주제(전략위 등)

    @property
    def lead(self) -> Article:
        return self.articles[0]

    @property
    def outlets(self) -> List[str]:
        seen: List[str] = []
        for a in self.articles:
            if a.source_name not in seen:
                seen.append(a.source_name)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "articles": [a.to_dict() for a in self.articles],
            "score": round(self.score, 3),
            "reasons": {k: round(v, 3) for k, v in self.reasons.items()},
            "onto": self.onto,
            "outlets": self.outlets,
            "hot_entity": self.hot_entity,
            "work_groups": self.work_groups,
        }


@dataclass
class Item:
    """동향지에 실리는 항목 1건. PDF 서식의 한 블록과 같다."""

    no: int = 0
    field_label: str = "기관 동향"      # 분야
    audience: str = "전 직원"           # 참고 대상
    impact: str = "중간"                # 영향
    title: str = ""
    source_label: str = ""              # <출처 · 26.8.24.>
    body: List[str] = field(default_factory=list)   # 'ㅇ ' / ' - ' / '  * ' 접두 유지
    note_kind: str = "시사점"           # 지금 선택된 맺음말 종류
    # 시사점과 향후계획을 둘 다 보관한다. 종류를 바꿔도 쓰던 내용이 사라지지 않고,
    # 어느 쪽이 더 나은지 담당자가 오가며 비교할 수 있다.
    notes: Dict[str, str] = field(default_factory=lambda: {"시사점": "", "향후계획": ""})
    links: List[Dict[str, str]] = field(default_factory=list)
    videos: List[Dict[str, str]] = field(default_factory=list)   # 관련 유튜브 영상
    onto: Dict[str, List[str]] = field(default_factory=dict)
    locked: bool = False                # 사람이 수정한 항목 잠금
    origin_keys: List[str] = field(default_factory=list)
    similar_to: List[str] = field(default_factory=list)  # 같은 사건일 수 있는 다른 항목 제목
    why: List[str] = field(default_factory=list)         # 이 항목을 고른 이유
    track: str = "policy"                                # policy | industry | dev
    synthesis: bool = False                              # 여러 소식을 모아 정리한 항목

    @property
    def note(self) -> str:
        """지금 선택된 종류의 맺음말."""
        return self.notes.get(self.note_kind, "")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["note"] = self.note      # 템플릿·미리보기에서 바로 쓰도록 함께 담는다
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Item":
        d = dict(d)
        allowed = {f for f in cls.__dataclass_fields__}
        notes = d.get("notes")
        if not isinstance(notes, dict):
            notes = {k: "" for k in NOTE_KINDS}
        else:
            notes = {k: str(notes.get(k, "") or "") for k in NOTE_KINDS}
        # 예전 초안은 note 하나만 갖고 있다. 선택된 종류 쪽으로 옮겨 준다.
        legacy = d.pop("note", "")
        kind = d.get("note_kind", "시사점")
        if legacy and not notes.get(kind):
            notes[kind] = legacy
        d["notes"] = notes
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Issue:
    """한 회차 전체."""

    year: int
    number: int
    published_on: date
    period_from: date
    period_to: date
    theme: str = ""
    items: List[Item] = field(default_factory=list)
    status: str = "draft"               # draft | published
    keyword_briefs: List[Dict[str, str]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return "제%d-%d호" % (self.year, self.number)

    @property
    def slug(self) -> str:
        return "AI정보동향지(제%d-%d호)" % (self.year, self.number)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "year": self.year,
            "number": self.number,
            "published_on": _iso(self.published_on),
            "period_from": _iso(self.period_from),
            "period_to": _iso(self.period_to),
            "theme": self.theme,
            "items": [i.to_dict() for i in self.items],
            "keyword_briefs": self.keyword_briefs,
            "status": self.status,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Issue":
        return cls(
            year=int(d["year"]),
            number=int(d["number"]),
            published_on=date.fromisoformat(d["published_on"]),
            period_from=date.fromisoformat(d["period_from"]),
            period_to=date.fromisoformat(d["period_to"]),
            theme=d.get("theme", ""),
            items=[Item.from_dict(x) for x in d.get("items", [])],
            keyword_briefs=d.get("keyword_briefs", []),
            status=d.get("status", "draft"),
            meta=d.get("meta", {}),
        )
