"""Embedding model for the runbook index.

One small model, run in-process on CPU, so ingestion, CI and the cluster all
embed identically. Changing MODEL invalidates the index: the ingest job sees
the mismatch in index_meta and re-embeds every chunk.
"""

from __future__ import annotations

import os

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

_model = None


def _get():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        # RAG_MODEL_CACHE: where the downloaded model lives (CI caches it; the
        # image bakes it in). Unset = fastembed's default temp-dir cache.
        _model = TextEmbedding(MODEL, cache_dir=os.environ.get("RAG_MODEL_CACHE"))
    return _model


def embed_passages(texts: list[str]):
    return list(_get().passage_embed(texts))


def embed_query(text: str):
    # Separate call on purpose: this model family prefixes queries, not passages.
    return next(iter(_get().query_embed(text)))
