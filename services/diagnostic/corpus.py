"""Argus runbook corpus.

Parses the incident runbooks in runbooks/, lints them, and splits each into
one chunk per section. The chunks are what gets embedded and indexed; their
ids are stable across runs because the diagnostic layer cites them.

Run directly to lint the corpus in this repo:

    python services/diagnostic/corpus.py
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_DIR = ROOT / "runbooks"
RULES_DIR = ROOT / "observability" / "rules"
CORRELATOR = ROOT / "services" / "alert-correlator" / "main.py"

# Every runbook has exactly these sections, in this order.
SECTIONS = ("Symptoms", "Likely causes", "Diagnosis", "Remediation", "Escalation")
FIELDS = ("id", "title", "alerts", "services", "severity", "owner", "last_reviewed")
SEVERITIES = ("info", "warning", "critical")
ANY_SERVICE = "*"

# The embedding model truncates at 512 tokens; ~260 words leaves room for the
# title header and for kubectl commands, which tokenize badly.
MAX_CHUNK_WORDS = 260

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_HEADING = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Runbook:
    id: str
    title: str
    alerts: tuple[str, ...]
    services: tuple[str, ...]
    severity: str
    owner: str
    last_reviewed: date
    source_path: str
    sections: tuple[tuple[str, str], ...]  # (heading, body), in file order


@dataclass(frozen=True)
class Chunk:
    id: str  # <runbook_id>#<section-slug>[-n]
    runbook_id: str
    section: str
    ordinal: int
    content: str  # the section body, as written
    text: str  # what is embedded: title and section heading, then the body
    content_hash: str


def known_alerts(rules_dir: Path = RULES_DIR) -> set[str]:
    """Alert names defined in the Prometheus rule files."""
    names: set[str] = set()
    for path in rules_dir.glob("*.yaml"):
        names.update(re.findall(r"^\s*- alert:\s*(\S+)", path.read_text(encoding="utf-8"), re.MULTILINE))
    return names


def known_services(correlator: Path = CORRELATOR) -> set[str]:
    """Services in the correlator's topology, read without importing it."""
    for node in ast.parse(correlator.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TOPOLOGY" for t in node.targets):
            topology = ast.literal_eval(node.value)
            return set(topology) | {dep for deps in topology.values() for dep in deps}
    raise ValueError(f"TOPOLOGY not found in {correlator}")


def _str_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v for v in value)


def parse_runbook(
    path: Path, alerts: set[str] | None = None, services: set[str] | None = None
) -> tuple[Runbook | None, list[str]]:
    """Parse one runbook. Returns it with no errors, or None with the errors.

    `alerts` and `services` are the names that exist in this repo; pass None to
    skip those two checks (the ingest job runs where the rule files are absent).
    """
    where = f"{path.parent.name}/{path.name}"
    # Normalised so a chunk hashes the same on Windows and in CI.
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FRONT_MATTER.match(text)
    if not match:
        return None, [f"{where}: missing '---' front matter block"]
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return None, [f"{where}: front matter is not valid YAML ({exc})"]
    if not isinstance(meta, dict):
        return None, [f"{where}: front matter must be a mapping"]

    errors: list[str] = []

    def err(message: str) -> None:
        errors.append(f"{where}: {message}")

    for missing in [f for f in FIELDS if f not in meta]:
        err(f"missing field '{missing}'")
    for unknown in [f for f in meta if f not in FIELDS]:
        err(f"unknown field '{unknown}'")
    if errors:
        return None, errors

    if meta["id"] != path.stem or not _ID.match(str(meta["id"])):
        err(f"id '{meta['id']}' must be lower-case-dashed and match the file name")
    for field in ("title", "owner"):
        if not isinstance(meta[field], str) or not meta[field].strip():
            err(f"'{field}' must be a non-empty string")
    if meta["severity"] not in SEVERITIES:
        err(f"severity '{meta['severity']}' is not one of {', '.join(SEVERITIES)}")
    if not isinstance(meta["last_reviewed"], date):
        err("last_reviewed must be a date (YYYY-MM-DD)")

    if not _str_list(meta["alerts"]):
        err("alerts must be a non-empty list of alert names")
    elif alerts is not None:
        for name in sorted(set(meta["alerts"]) - alerts):
            err(f"alert '{name}' is not defined in observability/rules/")

    if not _str_list(meta["services"]):
        err("services must be a non-empty list")
    elif ANY_SERVICE in meta["services"]:
        if len(meta["services"]) > 1:
            err(f"services: '{ANY_SERVICE}' cannot be combined with named services")
    elif services is not None:
        for name in sorted(set(meta["services"]) - services):
            err(f"service '{name}' is not in the correlator topology")

    parts = _HEADING.split(match.group(2))
    if parts[0].strip():
        err("text before the first '## ' section is not indexed; move it into a section")
    headings = [h.strip() for h in parts[1::2]]
    bodies = [b.strip() for b in parts[2::2]]
    if tuple(headings) != SECTIONS:
        err(f"sections must be exactly: {', '.join(SECTIONS)} (found: {', '.join(headings) or 'none'})")
    for heading, body in zip(headings, bodies):
        if not body:
            err(f"section '{heading}' is empty")

    if errors:
        return None, errors
    return (
        Runbook(
            id=meta["id"],
            title=meta["title"].strip(),
            alerts=tuple(meta["alerts"]),
            services=tuple(meta["services"]),
            severity=meta["severity"],
            owner=meta["owner"].strip(),
            last_reviewed=meta["last_reviewed"],
            source_path=where,
            sections=tuple(zip(headings, bodies)),
        ),
        [],
    )


def load_corpus(
    runbook_dir: Path = RUNBOOK_DIR, alerts: set[str] | None = None, services: set[str] | None = None
) -> tuple[list[Runbook], list[str]]:
    """Every runbook in the directory, plus every lint error found."""
    runbooks: list[Runbook] = []
    errors: list[str] = []
    paths = sorted(runbook_dir.glob("*.md"))
    if not paths:
        errors.append(f"{runbook_dir}: no runbooks found")
    for path in paths:
        runbook, errs = parse_runbook(path, alerts, services)
        errors.extend(errs)
        if runbook:
            runbooks.append(runbook)
    return runbooks, errors


def _split(body: str) -> list[str]:
    """One piece if the section fits the embedding window, else whole
    paragraphs packed up to the limit. A paragraph is never cut."""
    if len(body.split()) <= MAX_CHUNK_WORDS:
        return [body]
    pieces: list[str] = []
    current: list[str] = []
    words = 0
    for paragraph in re.split(r"\n\s*\n", body):
        n = len(paragraph.split())
        if current and words + n > MAX_CHUNK_WORDS:
            pieces.append("\n\n".join(current))
            current, words = [], 0
        current.append(paragraph)
        words += n
    pieces.append("\n\n".join(current))
    return pieces


def chunk(runbook: Runbook) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, body in runbook.sections:
        slug = heading.lower().replace(" ", "-")
        pieces = _split(body)
        for n, piece in enumerate(pieces, start=1):
            suffix = f"-{n}" if len(pieces) > 1 else ""
            # The header is embedded with the body: on its own, a "Remediation"
            # section does not say what it remediates.
            text = f"{runbook.title} — {heading}\n\n{piece}"
            chunks.append(
                Chunk(
                    id=f"{runbook.id}#{slug}{suffix}",
                    runbook_id=runbook.id,
                    section=heading,
                    ordinal=len(chunks),
                    content=piece,
                    text=text,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    return chunks


def main() -> int:
    runbooks, errors = load_corpus(alerts=known_alerts(), services=known_services())
    for error in errors:
        print(error)
    if errors:
        print(f"\n{len(errors)} problem(s) in the runbook corpus")
        return 1
    print(f"{len(runbooks)} runbooks, {sum(len(chunk(rb)) for rb in runbooks)} chunks: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
