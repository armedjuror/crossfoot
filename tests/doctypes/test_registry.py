from crossfoot.doctypes.bank_statement.registry import classify, register, _REGISTRY
from crossfoot.types import ExtractedDoc


class _FakeLayout:
    def __init__(self, slug, score):
        self.slug = slug
        self.document_type = "bank_statement"
        self.country = "IN"
        self._score = score

    def matches(self, doc):
        return self._score

    def parse(self, doc):
        raise NotImplementedError


def _empty_doc():
    return ExtractedDoc(pages=[[]], page_count=1, is_scanned=False, first_page_text="")


def test_classify_picks_highest_scoring_layout(monkeypatch):
    monkeypatch.setattr("crossfoot.doctypes.bank_statement.registry._REGISTRY",
                         [_FakeLayout("a", 0.6), _FakeLayout("b", 0.9)])
    best, runner_up = classify(_empty_doc())
    assert best.slug == "b"
    assert best.score == 0.9
    assert runner_up.slug == "a"


def test_classify_below_threshold_is_unsupported(monkeypatch):
    monkeypatch.setattr("crossfoot.doctypes.bank_statement.registry._REGISTRY",
                         [_FakeLayout("a", 0.2)])
    best, runner_up = classify(_empty_doc())
    assert best.score <= 0.5
    assert runner_up is None


def test_register_appends_to_registry():
    before = len(_REGISTRY)
    register(_FakeLayout("z", 0.1))
    assert len(_REGISTRY) == before + 1
    _REGISTRY.pop()   # keep the module-level registry clean for other tests
