"""
rag/store.py
────────────
Builds, persists, and queries the COBOL knowledge vector store.

Architecture
────────────
  • Backend  : ChromaDB (local persistent store, no server needed)
  • Embeddings: OpenAI text-embedding-3-small  (1536-dim, cheap, accurate)
  • Chunking  : pre-authored chunks in cobol_docs.py (each chunk = one COBOL concept)
  • Retrieval : top-k cosine similarity search, returns raw text chunks
  • Rebuild   : automatic if the DB is empty or the chunk count has changed

Usage
─────
    from rag.store import CobolKnowledgeStore

    store = CobolKnowledgeStore(api_key="sk-...")
    store.build()                        # idempotent — skips if already built

    chunks = store.retrieve(
        cobol_source="COMPUTE RESULT = P * (1 + R/100) ** N",
        top_k=4,
    )
    # chunks is a list[str] of documentation texts ready to inject into the prompt
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings
from openai import OpenAI

from rag.cobol_docs import CHUNKS

# ── Config ────────────────────────────────────────────────────────────────
_DB_DIR      = Path(__file__).parent / "_chroma_db"
_COLLECTION  = "cobol_syntax"
_EMBED_MODEL = "text-embedding-3-small"


class CobolKnowledgeStore:
    """
    Thin wrapper around a ChromaDB collection that stores COBOL documentation
    chunks as embedding vectors.

    Parameters
    ----------
    api_key : str | None
        OpenAI API key. Falls back to $OPENAI_API_KEY.
    db_path : Path | None
        Where to persist the ChromaDB files. Defaults to rag/_chroma_db/.
    """

    def __init__(
        self,
        api_key: str | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._client_oai = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        db_dir = db_path or _DB_DIR
        db_dir.mkdir(parents=True, exist_ok=True)

        self._chroma = chromadb.PersistentClient(
            path=str(db_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._col = self._chroma.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Build ──────────────────────────────────────────────────────────────

    def build(self, force: bool = False) -> None:
        """
        Embed all CHUNKS and upsert into ChromaDB.
        Idempotent: skips if the collection already has the right number of docs
        and the content hash hasn't changed, unless force=True.
        """
        content_hash = _chunks_hash()
        stored_hash  = self._stored_hash()

        if not force and stored_hash == content_hash:
            return  # already up-to-date

        print(f"[RAG] Building COBOL knowledge store ({len(CHUNKS)} chunks)…")

        ids       = [c["id"]   for c in CHUNKS]
        texts     = [c["text"] for c in CHUNKS]
        metadatas = [{"topic": c["topic"], "tags": ",".join(c["tags"])} for c in CHUNKS]

        # Embed in one batched API call (≤2048 items per call)
        embeddings = self._embed(texts)

        # Upsert (add or replace)
        self._col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        # Persist the content hash so we can skip next time
        _hash_file().write_text(content_hash, encoding="utf-8")
        print(f"[RAG] Store ready. {self._col.count()} vectors indexed.")

    # ── Retrieve ───────────────────────────────────────────────────────────

    def retrieve(
        self,
        cobol_source: str,
        top_k: int = 5,
    ) -> list[str]:
        """
        Given a COBOL source snippet, return the top_k most relevant
        documentation chunks as plain text strings.

        Strategy
        --------
        1. Keyword scan: extract COBOL keywords from source and boost any
           chunks whose tags contain those keywords (pre-filter step).
        2. Semantic search: embed the source and find nearest neighbours.
        3. Merge: union of keyword-matched ids + semantic results, ranked
           by semantic score, deduplicated, top_k returned.
        """
        if self._col.count() == 0:
            self.build()

        # 1. Keyword boost — find chunk ids whose tags match keywords in source
        keyword_ids = _keyword_match(cobol_source)

        # 2. Semantic search — embed source, query ChromaDB
        query_vec = self._embed([cobol_source])[0]
        sem_results = self._col.query(
            query_embeddings=[query_vec],
            n_results=min(top_k + len(keyword_ids), self._col.count()),
            include=["documents", "distances", "metadatas"],
        )

        sem_docs = sem_results["documents"][0]
        sem_ids  = sem_results["ids"][0]

        # 3. Promote keyword matches to the front, then fill with semantic results
        seen: set[str] = set()
        ordered_docs: list[str] = []

        # First: keyword-matched chunks (in keyword_ids order)
        for kid in keyword_ids:
            if kid in sem_ids and kid not in seen:
                idx = sem_ids.index(kid)
                ordered_docs.append(sem_docs[idx])
                seen.add(kid)

        # Then: remaining semantic results
        for sid, sdoc in zip(sem_ids, sem_docs):
            if sid not in seen:
                ordered_docs.append(sdoc)
                seen.add(sid)

        return ordered_docs[:top_k]

    # ── Internal ───────────────────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI embeddings API and return a list of float vectors."""
        response = self._client_oai.embeddings.create(
            model=_EMBED_MODEL,
            input=texts,
        )
        # Response items are in the same order as input
        return [item.embedding for item in response.data]

    def _stored_hash(self) -> str:
        f = _hash_file()
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""


# ── Helpers ────────────────────────────────────────────────────────────────

def _hash_file() -> Path:
    return _DB_DIR / "chunks.hash"


def _chunks_hash() -> str:
    """Stable hash of all chunk texts — used to detect when docs changed."""
    combined = "".join(c["id"] + c["text"] for c in CHUNKS)
    return hashlib.sha256(combined.encode()).hexdigest()


# COBOL keyword patterns for the fast keyword-match pre-filter
_KEYWORD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bPIC\b|\bPICTURE\b",          re.I), "pic_clause"),
    (re.compile(r"\bCOMP\b|\bCOMP-3\b|\bBINARY\b|\bPACKED",re.I), "pic_comp"),
    (re.compile(r"\bCOMPUTE\b",                   re.I), "compute"),
    (re.compile(r"\bADD\b|\bSUBTRACT\b|\bMULTIPLY\b|\bDIVIDE\b", re.I), "add_subtract_multiply_divide"),
    (re.compile(r"\bPERFORM\b",                   re.I), "perform"),
    (re.compile(r"\bIF\b|\bELSE\b|\bEVALUATE\b", re.I), "if_evaluate"),
    (re.compile(r"\bMOVE\b",                      re.I), "move"),
    (re.compile(r"\bOCCURS\b",                    re.I), "occurs"),
    (re.compile(r"\bREDEFINES\b",                 re.I), "redefines"),
    (re.compile(r"\bCOPY\b",                      re.I), "copy_books"),
    (re.compile(r"\bREAD\b|\bWRITE\b|\bOPEN\b|\bCLOSE\b|\bFD\b|\bSELECT\b", re.I), "read_write"),
    (re.compile(r"\bCALL\b|\bLINKAGE\b",          re.I), "call_linkage"),
    (re.compile(r"\bDISPLAY\b|\bACCEPT\b",        re.I), "display_accept"),
    (re.compile(r"\bSTRING\b|\bUNSTRING\b",       re.I), "string_unstring"),
    (re.compile(r"\bINSPECT\b",                   re.I), "inspect"),
    (re.compile(r"\bFUNCTION\b",                  re.I), "intrinsic_functions"),
    (re.compile(r"\bWORKING-STORAGE\b",            re.I), "working_storage"),
    (re.compile(r"\bPROCEDURE DIVISION\b",         re.I), "procedure_div"),
    (re.compile(r"\bSTOP RUN\b|\bGOBACK\b",        re.I), "stop_run_goback"),
    (re.compile(r"\bDATE\b|\bTIME\b",              re.I), "date_handling"),
    (re.compile(r"\bROUNDED\b|\bON SIZE ERROR\b",  re.I), "decimal_precision"),
    (re.compile(r"\bSIGN\b|\bLEADING\b|\bTRAILING\b", re.I), "sign_handling"),
    (re.compile(r"\b(interest|loan|amort|annuity)\b", re.I), "interest_calculation"),
    (re.compile(r"\b(payroll|gross.pay|net.pay|tax.rate)\b", re.I), "payroll_patterns"),
]


def _keyword_match(cobol_source: str) -> list[str]:
    """Return a deduplicated list of chunk ids whose keywords appear in the source."""
    seen: set[str] = set()
    result: list[str] = []
    # Always include decimal_precision for any COBOL — it's universally relevant
    result.append("decimal_precision")
    seen.add("decimal_precision")

    for pattern, chunk_id in _KEYWORD_PATTERNS:
        if chunk_id not in seen and pattern.search(cobol_source):
            result.append(chunk_id)
            seen.add(chunk_id)
    return result
