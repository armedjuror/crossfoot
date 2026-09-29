from typing import Protocol

from crossfoot.types import ExtractedDoc, LayoutMatch, ParsedStatement

THRESHOLD = 0.5

_REGISTRY: list["Layout"] = []


class Layout(Protocol):
    slug: str
    document_type: str
    country: str

    def matches(self, doc: ExtractedDoc) -> float: ...
    def parse(self, doc: ExtractedDoc) -> ParsedStatement: ...


def register(layout: "Layout") -> None:
    _REGISTRY.append(layout)


def classify(doc: ExtractedDoc) -> tuple[LayoutMatch, LayoutMatch | None]:
    scored = sorted(
        ((layout, layout.matches(doc)) for layout in _REGISTRY),
        key=lambda pair: pair[1], reverse=True,
    )
    if not scored:
        return LayoutMatch(slug="none", score=0.0, trusted=False), None

    best_layout, best_score = scored[0]
    best = LayoutMatch(slug=best_layout.slug, score=best_score, trusted=False)
    if len(scored) < 2:
        return best, None
    runner_layout, runner_score = scored[1]
    runner_up = LayoutMatch(slug=runner_layout.slug, score=runner_score, trusted=False)
    return best, runner_up
