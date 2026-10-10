"""Build the runbook index from runbooks/.

    python services/diagnostic/ingest.py

Safe to re-run: only new or changed chunks are embedded, chunks removed from
the corpus are removed from the index, and the whole update is one
transaction, so a reader never sees a half-built index.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime

import corpus
import embed
import store


def _git_sha() -> str:
    # GIT_SHA is set in the image, where there is no checkout to ask.
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=corpus.ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    # Lint before touching the database. The alert and service checks need
    # files that only exist in a checkout, so they are skipped elsewhere.
    in_repo = corpus.RULES_DIR.is_dir() and corpus.CORRELATOR.is_file()
    runbooks, errors = corpus.load_corpus(
        alerts=corpus.known_alerts() if in_repo else None,
        services=corpus.known_services() if in_repo else None,
    )
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} problem(s) in the runbook corpus; index not changed")
        return 1
    chunks = [c for rb in runbooks for c in corpus.chunk(rb)]

    conn = store.connect(init=True)
    meta = store.read_meta(conn)
    indexed = store.read_hashes(conn)
    model_changed = bool(indexed) and (
        meta.get("embedding_model") != embed.MODEL or meta.get("embedding_dim") != str(embed.DIM)
    )
    if model_changed:
        print(f"embedding model changed ({meta.get('embedding_model')} -> {embed.MODEL}): re-embedding everything")
        indexed = {}

    stale = [c for c in chunks if indexed.get(c.id) != c.content_hash]
    vectors = embed.embed_passages([c.text for c in stale]) if stale else []
    deleted = store.apply(
        conn,
        runbooks,
        chunks,
        {c.id: v for c, v in zip(stale, vectors)},
        {
            "embedding_model": embed.MODEL,
            "embedding_dim": str(embed.DIM),
            "corpus_git_sha": _git_sha(),
            "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    conn.close()

    added = sum(1 for c in stale if c.id not in indexed)
    print(
        f"{len(runbooks)} runbooks, {len(chunks)} chunks: "
        f"{added} added, {len(stale) - added} updated, {len(chunks) - len(stale)} unchanged, {deleted} deleted"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
