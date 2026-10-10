"""Postgres + pgvector store for the runbook index.

Two users: the ingest job writes through apply(); retrieval reads through
PgIndex, which supplies the three signals retrieve.py combines.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

# The default is the throwaway local database from `make rag-db`.
DSN = os.environ.get("RAG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/argus_rag")
SCHEMA = Path(__file__).with_name("schema.sql")
ANY_SERVICE = "*"


def connect(init: bool = False) -> psycopg.Connection:
    """Open a connection. init=True applies schema.sql first (ingest only:
    the service's role cannot create tables, and should not need to)."""
    # autocommit: reads need no transaction, and it makes the transaction()
    # block in apply() a real BEGIN/COMMIT, not a savepoint inside an implicit
    # transaction that nothing ever commits.
    conn = psycopg.connect(DSN, autocommit=True)
    if init:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
    register_vector(conn)
    return conn


def read_meta(conn) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM index_meta").fetchall())


def read_hashes(conn) -> dict[str, str]:
    return dict(conn.execute("SELECT id, content_hash FROM runbook_chunks").fetchall())


def apply(conn, runbooks, chunks, embeddings: dict, meta: dict[str, str]) -> int:
    """Make the index match the corpus, in one transaction.

    `chunks` is every chunk in the corpus; `embeddings` holds vectors only for
    the ones to write (new or changed). Anything indexed but no longer in the
    corpus is deleted. Returns the number of chunks deleted.
    """
    with conn.transaction():
        for rb in runbooks:
            conn.execute(
                """
                INSERT INTO runbooks (id, title, alerts, services, severity, owner, last_reviewed, source_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                  title = EXCLUDED.title, alerts = EXCLUDED.alerts, services = EXCLUDED.services,
                  severity = EXCLUDED.severity, owner = EXCLUDED.owner,
                  last_reviewed = EXCLUDED.last_reviewed, source_path = EXCLUDED.source_path
                """,
                (rb.id, rb.title, list(rb.alerts), list(rb.services), rb.severity, rb.owner,
                 rb.last_reviewed, rb.source_path),
            )
        for c in chunks:
            if c.id not in embeddings:
                continue
            conn.execute(
                """
                INSERT INTO runbook_chunks (id, runbook_id, section, ordinal, content, text, content_hash, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                  runbook_id = EXCLUDED.runbook_id, section = EXCLUDED.section, ordinal = EXCLUDED.ordinal,
                  content = EXCLUDED.content, text = EXCLUDED.text,
                  content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding
                """,
                (c.id, c.runbook_id, c.section, c.ordinal, c.content, c.text, c.content_hash, embeddings[c.id]),
            )
        deleted = conn.execute(
            "DELETE FROM runbook_chunks WHERE NOT (id = ANY(%s))", ([c.id for c in chunks],)
        ).rowcount
        conn.execute("DELETE FROM runbooks WHERE NOT (id = ANY(%s))", ([rb.id for rb in runbooks],))
        for key, value in meta.items():
            conn.execute(
                "INSERT INTO index_meta (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
    return deleted


class PgIndex:
    """The signals retrieve.py asks for. Every one is restricted to runbooks
    that apply to the incident's root service (or to any service)."""

    def __init__(self, conn, embed_query):
        self.conn = conn
        self.embed_query = embed_query

    def alert_agreement(self, alert_names, root: str) -> dict[str, float]:
        """Chunk id -> how well its runbook's alerts agree with the incident's.

        Weighted Jaccard: each alert counts by how few runbooks list it, so an
        alert that accompanies almost every incident (ServiceAnomalyDetected)
        says less than one that names a single failure mode. Every chunk of a
        runbook shares its score; runbooks sharing no alert are absent.
        """
        listed = [alerts for (alerts,) in self.conn.execute("SELECT alerts FROM runbooks").fetchall()]

        def weight(names) -> float:
            # Unlisted alerts weigh as much as the rarest listed one.
            return sum(math.log(1 + len(listed) / max(1, sum(n in a for a in listed))) for n in names)

        rows = self.conn.execute(
            """
            SELECT c.id, r.alerts FROM runbook_chunks c JOIN runbooks r ON r.id = c.runbook_id
            WHERE r.alerts && %s AND r.services && %s
            """,
            (list(alert_names), [root, ANY_SERVICE]),
        ).fetchall()
        wanted = set(alert_names)
        return {cid: weight(wanted & set(alerts)) / weight(wanted | set(alerts)) for cid, alerts in rows}

    def vector_ranks(self, text: str, root: str, n: int) -> tuple[dict[str, int], float | None]:
        embedding = self.embed_query(text)
        rows = self.conn.execute(
            """
            SELECT c.id, 1 - (c.embedding <=> %s) FROM runbook_chunks c JOIN runbooks r ON r.id = c.runbook_id
            WHERE r.services && %s
            ORDER BY c.embedding <=> %s, c.id LIMIT %s
            """,
            (embedding, [root, ANY_SERVICE], embedding, n),
        ).fetchall()
        ranks = {chunk_id: rank for rank, (chunk_id, _) in enumerate(rows, start=1)}
        return ranks, (float(rows[0][1]) if rows else None)

    def keyword_ranks(self, text: str, root: str, n: int) -> dict[str, int]:
        # Any-term match: the query is a list of names, not a phrase, and
        # requiring every term would match nothing.
        terms = list(dict.fromkeys(re.findall(r"[A-Za-z0-9]+", text)))
        if not terms:
            return {}
        rows = self.conn.execute(
            """
            SELECT c.id FROM runbook_chunks c JOIN runbooks r ON r.id = c.runbook_id,
                 to_tsquery('english', %s) q
            WHERE c.tsv @@ q AND r.services && %s
            ORDER BY ts_rank(c.tsv, q) DESC, c.id LIMIT %s
            """,
            (" | ".join(terms), [root, ANY_SERVICE], n),
        ).fetchall()
        return {chunk_id: rank for rank, (chunk_id,) in enumerate(rows, start=1)}

    def chunks(self, ids: list[str]) -> dict[str, dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, runbook_id, section, content FROM runbook_chunks WHERE id = ANY(%s)", (ids,)
            )
            return {row["id"]: row for row in cur.fetchall()}
