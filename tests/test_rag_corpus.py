import pytest

RUNBOOK = """---
id: {id}
title: Test runbook
alerts: [{alert}]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms
{symptoms}
## Likely causes
causes
## Diagnosis
diagnosis
## Remediation
remediation
## Escalation
escalation
"""


def write(tmp_path, name="test-runbook", **overrides):
    fields = {"id": name, "alert": "KnownAlert", "symptoms": "symptoms"} | overrides
    path = tmp_path / f"{name}.md"
    path.write_text(RUNBOOK.format(**fields), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def repo_corpus(corpus_module):
    m = corpus_module
    return m.load_corpus(alerts=m.known_alerts(), services=m.known_services())


def test_repo_corpus_is_lint_clean(repo_corpus):
    runbooks, errors = repo_corpus
    assert errors == []
    assert len(runbooks) >= 10


def test_every_alert_rule_has_a_runbook(corpus_module, repo_corpus):
    runbooks, _ = repo_corpus
    covered = {alert for rb in runbooks for alert in rb.alerts}
    assert corpus_module.known_alerts() - covered == set()


def test_chunk_ids_are_unique_and_one_per_section(corpus_module, repo_corpus):
    runbooks, _ = repo_corpus
    chunks = [c for rb in runbooks for c in corpus_module.chunk(rb)]
    assert len({c.id for c in chunks}) == len(chunks)
    for rb in runbooks:
        ids = [c.id for c in corpus_module.chunk(rb)]
        assert ids == [f"{rb.id}#{s.lower().replace(' ', '-')}" for s in corpus_module.SECTIONS]


def test_embedded_text_carries_title_and_section(corpus_module, tmp_path):
    runbook, errors = corpus_module.parse_runbook(write(tmp_path))
    assert errors == []
    remediation = corpus_module.chunk(runbook)[3]
    assert remediation.text.startswith("Test runbook — Remediation\n\n")
    assert remediation.content == "remediation"


def test_hash_is_stable_and_follows_content(corpus_module, tmp_path):
    m = corpus_module
    before = m.chunk(m.parse_runbook(write(tmp_path))[0])
    again = m.chunk(m.parse_runbook(write(tmp_path))[0])
    edited = m.chunk(m.parse_runbook(write(tmp_path, symptoms="different symptoms"))[0])
    assert [c.content_hash for c in before] == [c.content_hash for c in again]
    assert before[0].content_hash != edited[0].content_hash
    assert [c.content_hash for c in before[1:]] == [c.content_hash for c in edited[1:]]


def test_hash_ignores_windows_line_endings(corpus_module, tmp_path):
    m = corpus_module
    path = write(tmp_path)
    unix = m.chunk(m.parse_runbook(path)[0])
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    windows = m.chunk(m.parse_runbook(path)[0])
    assert [c.content_hash for c in unix] == [c.content_hash for c in windows]


def test_long_section_splits_on_paragraphs_with_numbered_ids(corpus_module, tmp_path):
    m = corpus_module
    paragraph = " ".join(["word"] * (m.MAX_CHUNK_WORDS - 10))
    runbook, errors = m.parse_runbook(write(tmp_path, symptoms=f"{paragraph}\n\n{paragraph}"))
    assert errors == []
    ids = [c.id for c in m.chunk(runbook)]
    assert ids[:3] == ["test-runbook#symptoms-1", "test-runbook#symptoms-2", "test-runbook#likely-causes"]


def test_lint_rejects_unknown_alert(corpus_module, tmp_path):
    runbook, errors = corpus_module.parse_runbook(write(tmp_path, alert="NoSuchAlert"), alerts={"KnownAlert"})
    assert runbook is None
    assert "alert 'NoSuchAlert' is not defined" in errors[0]


def test_lint_rejects_id_that_does_not_match_file_name(corpus_module, tmp_path):
    runbook, errors = corpus_module.parse_runbook(write(tmp_path, id="something-else"))
    assert runbook is None
    assert "must be lower-case-dashed and match the file name" in errors[0]


def test_lint_rejects_missing_section(corpus_module, tmp_path):
    path = write(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("## Escalation\nescalation\n", ""), encoding="utf-8")
    runbook, errors = corpus_module.parse_runbook(path)
    assert runbook is None
    assert "sections must be exactly" in errors[0]


def test_lint_rejects_unknown_service(corpus_module, tmp_path):
    path = write(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace('services: ["*"]', "services: [paymentservcie]"), encoding="utf-8"
    )
    runbook, errors = corpus_module.parse_runbook(path, services={"paymentservice"})
    assert runbook is None
    assert "service 'paymentservcie' is not in the correlator topology" in errors[0]
