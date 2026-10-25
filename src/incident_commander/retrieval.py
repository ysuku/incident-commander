"""Runbook retrieval for Incident Commander (Checkpoint 3.1).

What this is: the semantic long-term-memory layer behind the `runbook_lookup`
tool. It indexes public Prometheus/Kubernetes runbooks (the kube-prometheus
runbook set) in Chroma and returns a few cited chunks for a symptom query.

Design choices (see DESIGN_LOG.md, Module 3):
- Chunking: one runbook = up to two chunks, split on the runbook's own headings.
    "symptom" chunk = title + Meaning + Impact   (what the alert looks like)
    "action"  chunk = title + Diagnosis + Mitigation (what to check / do)
  Every chunk repeats the alert title so it is never an orphan fragment.
  Sections longer than MAX_CHUNK_CHARS are split on paragraph boundaries.
- Scope: only the `kubernetes` and `node` runbook families are indexed by
  default. RCAEval faults (cpu, mem, disk, delay, loss, socket) live in the
  workload/node layer; the monitoring-stack runbooks (alertmanager, etcd,
  prometheus...) are distractors for this agent. `components=` can widen it.
- Ranking: fetch FETCH_K by cosine similarity, drop anything beyond
  MAX_DISTANCE (abstain if nothing is left), then MMR-rerank to K results so
  the agent does not get four near-duplicate chunks.
- Embeddings are computed here and handed to Chroma, so tests can inject a
  deterministic embedder and production uses Chroma's bundled MiniLM (ONNX).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from .config import DATA_DIR

RUNBOOKS_CONTENT = DATA_DIR / "runbooks" / "repo" / "content" / "runbooks"
INDEX_DIR = DATA_DIR / "index"

DEFAULT_COMPONENTS = ("kubernetes", "node")
MAX_CHUNK_CHARS = 1200
K = 4                 # chunks returned to the agent
FETCH_K = 12          # candidates pulled before thresholding + MMR
MMR_LAMBDA = 0.6      # 1.0 = pure relevance, 0.0 = pure diversity
MAX_DISTANCE = 0.65   # cosine distance cut-off; STARTING value, calibrate on dev cases

Embedder = Callable[[Sequence[str]], np.ndarray]


# ----------------------------------------------------------------- chunking
@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    alert: str
    component: str
    kind: str          # "symptom" | "action"
    source_ref: str    # e.g. runbooks/kubernetes/CPUThrottlingHigh.md#symptom


_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_DETAILS_TAGS = re.compile(r"</?details>|</?summary>", re.I)
_LINK_DEFS = re.compile(r"(?m)^\[\d+\]:\s*\S+.*$")


def _clean(text: str) -> str:
    text = _FRONT_MATTER.sub("", text, count=1)
    text = _DETAILS_TAGS.sub("", text)
    text = _LINK_DEFS.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _sections(text: str) -> dict[str, str]:
    """Split on '## ' headings -> {heading_lower: body}."""
    out: dict[str, str] = {}
    for part in re.split(r"(?m)^## ", text)[1:]:
        head, _, body = part.partition("\n")
        out[head.strip().lower()] = body.strip()
    return out


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) + 2 > limit:
            pieces.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def chunk_runbook(path: Path, component: str) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    title = path.stem
    secs = _sections(_clean(raw))
    groups = {
        "symptom": [secs.get("meaning", ""), secs.get("impact", "")],
        "action": [secs.get("diagnosis", ""), secs.get("mitigation", "")],
    }
    chunks: list[Chunk] = []
    for kind, bodies in groups.items():
        body = "\n\n".join(b for b in bodies if b)
        if not body:
            continue
        for i, piece in enumerate(_split_long(body, MAX_CHUNK_CHARS)):
            suffix = f"-{i}" if i else ""
            chunks.append(Chunk(
                chunk_id=f"{component}/{title}#{kind}{suffix}",
                text=f"{title} ({kind}): {piece}",
                alert=title,
                component=component,
                kind=kind,
                source_ref=f"runbooks/{component}/{title}.md#{kind}",
            ))
    return chunks


def load_chunks(root: Path = RUNBOOKS_CONTENT, components: Sequence[str] = DEFAULT_COMPONENTS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for comp in components:
        for f in sorted((root / comp).glob("*.md")):
            if f.name.startswith("_"):
                continue
            chunks.extend(chunk_runbook(f, comp))
    return chunks


# --------------------------------------------------------------- embeddings
def _normalize(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return a / np.clip(n, 1e-12, None)


def minilm_embedder() -> Embedder:
    """Chroma's bundled all-MiniLM-L6-v2 (ONNX). Downloads the model on first use."""
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    ef = ONNXMiniLM_L6_V2()
    return lambda texts: _normalize(np.array(ef(list(texts))))


def hashing_embedder(dim: int = 512) -> Embedder:
    """Deterministic bag-of-words stand-in. TESTS ONLY: it matches shared words,
    not meaning, so it must never be used to report retrieval quality."""
    def embed(texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for r, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[r, int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        return _normalize(out)
    return embed


# ---------------------------------------------------------------- the index
class RunbookIndex:
    def __init__(self, embedder: Embedder | None = None, persist_dir: Path | None = INDEX_DIR,
                 collection: str = "runbooks"):
        import chromadb
        self.embed = embedder or minilm_embedder()
        self.client = chromadb.PersistentClient(str(persist_dir)) if persist_dir else chromadb.EphemeralClient()
        self.col = self.client.get_or_create_collection(collection)

    def build(self, chunks: list[Chunk]) -> int:
        """(Re)index chunks. Upsert by chunk_id, so rebuilding is idempotent."""
        embs = self.embed([c.text for c in chunks])
        self.col.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=embs.tolist(),
            metadatas=[{"alert": c.alert, "component": c.component, "kind": c.kind,
                        "source_ref": c.source_ref} for c in chunks],
        )
        return len(chunks)

    def search(self, query: str, k: int = K, fetch_k: int = FETCH_K, mmr_lambda: float = MMR_LAMBDA,
               max_distance: float | None = MAX_DISTANCE, components: Sequence[str] | None = None,
               use_mmr: bool = True) -> list[dict]:
        """Return up to k cited chunks, or [] (abstain) if nothing is close enough.

        Each hit: {source_ref, alert, component, kind, distance, text}.
        """
        q = self.embed([query])[0]
        where = {"component": {"$in": list(components)}} if components else None
        res = self.col.query(query_embeddings=[q.tolist()], n_results=fetch_k, where=where,
                             include=["documents", "metadatas", "embeddings"])
        if not res["ids"][0]:
            return []
        embs = _normalize(np.array(res["embeddings"][0]))
        sims = embs @ q
        cand = [i for i in range(len(sims)) if max_distance is None or (1.0 - sims[i]) <= max_distance]
        if not cand:
            return []
        cand.sort(key=lambda i: -sims[i])
        picked = cand[:k] if not use_mmr else _mmr(cand, sims, embs, k, mmr_lambda)
        return [{
            "source_ref": res["metadatas"][0][i]["source_ref"],
            "alert": res["metadatas"][0][i]["alert"],
            "component": res["metadatas"][0][i]["component"],
            "kind": res["metadatas"][0][i]["kind"],
            "distance": round(float(1.0 - sims[i]), 3),
            "text": res["documents"][0][i],
        } for i in picked]


def _mmr(cand: list[int], sims: np.ndarray, embs: np.ndarray, k: int, lam: float) -> list[int]:
    """Maximal Marginal Relevance over candidate indices (cosine on unit vectors)."""
    k = min(k, len(cand))                       # clamp: can't pick more than exist
    selected: list[int] = []
    remaining = list(cand)
    while remaining and len(selected) < k:
        best, best_score = None, -np.inf        # scores can go negative after pick 1
        for i in remaining:
            redundancy = max((float(embs[i] @ embs[j]) for j in selected), default=0.0)
            score = lam * float(sims[i]) - (1.0 - lam) * redundancy
            if score > best_score:
                best, best_score = i, score
        selected.append(best)
        remaining.remove(best)                  # remove each pick from the pool
    return selected
