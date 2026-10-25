"""Tests for the runbook retrieval layer. Logic only: they use a deterministic
hashing embedder, so they say nothing about semantic quality (that is the
demo script's job, with the real MiniLM embedder)."""
import pytest

from incident_commander import retrieval as R

pytestmark = pytest.mark.skipif(
    not (R.RUNBOOKS_CONTENT / "kubernetes").exists(), reason="runbooks not downloaded (scripts/download_data.py)"
)


@pytest.fixture(scope="module")
def index():
    idx = R.RunbookIndex(embedder=R.hashing_embedder(), persist_dir=None, collection="t_runbooks")
    idx.build(R.load_chunks(components=("kubernetes", "node", "prometheus", "etcd")))
    return idx


def test_chunks_are_titled_cited_and_bounded():
    chunks = R.load_chunks()
    assert len(chunks) > 60
    for c in chunks:
        assert c.text.startswith(c.alert)               # never an orphan fragment
        assert c.source_ref.startswith("runbooks/") and "#" in c.source_ref
        assert len(c.text) <= R.MAX_CHUNK_CHARS + len(c.alert) + 20
        assert "<details>" not in c.text


def test_default_scope_excludes_monitoring_stack():
    comps = {c.component for c in R.load_chunks()}
    assert comps == {"kubernetes", "node"}


def test_component_filter_applies(index):
    hits = index.search("etcd leader election failing", components=["node"], max_distance=None)
    assert hits and all(h["component"] == "node" for h in hits)


def test_threshold_abstains(index):
    assert index.search("zzzz qqqq unrelated gibberish", max_distance=0.2) == []


def test_mmr_returns_k_and_does_not_duplicate(index):
    hits = index.search("cpu throttling container limits", k=4, max_distance=None, use_mmr=True)
    assert len(hits) == 4
    assert len({h["source_ref"] for h in hits}) == 4


def test_mmr_clamps_k(index):
    hits = index.search("cpu", k=50, fetch_k=5, max_distance=None)
    assert len(hits) == 5
