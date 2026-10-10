import pytest


class FakeIndex:
    """Canned signals; chunk ids are '<runbook>#<n>'."""

    def __init__(self, agreement=None, vector=(), keyword=(), best=0.9):
        self.agreement = agreement or {}
        self.vector = {cid: rank for rank, cid in enumerate(vector, start=1)}
        self.keyword = {cid: rank for rank, cid in enumerate(keyword, start=1)}
        self.best = best

    def alert_agreement(self, alert_names, root):
        return self.agreement

    def vector_ranks(self, text, root, n):
        return self.vector, (self.best if self.vector else None)

    def keyword_ranks(self, text, root, n):
        return self.keyword

    def chunks(self, ids):
        return {
            cid: {"id": cid, "runbook_id": cid.split("#")[0], "section": "Symptoms", "content": cid} for cid in ids
        }


INCIDENT = {
    "probable_root_service": "paymentservice",
    "alerts": [
        {"alertname": "ServiceAnomalyDetected", "service": "checkoutservice"},
        {"alertname": "BoutiquePodRestarting", "service": "paymentservice"},
        {"alertname": "ServiceAnomalyDetected", "service": "paymentservice"},
    ],
}


@pytest.fixture(scope="module")
def r(retrieve_module):
    return retrieve_module


def test_humanize_spells_out_an_alert_name(r):
    assert r.humanize("BoutiquePodsNotReady") == "boutique pods not ready"


def test_query_puts_root_service_alerts_first_and_dedupes_names(r):
    query = r.build_query(INCIDENT)
    assert query.root == "paymentservice"
    assert query.alert_names == ("BoutiquePodRestarting", "ServiceAnomalyDetected")
    assert query.text.startswith("BoutiquePodRestarting (boutique pod restarting) on paymentservice; ")
    assert query.text.endswith("on checkoutservice")


def test_query_omits_the_pseudo_service_of_an_unlabelled_alert(r):
    incident = {
        "probable_root_service": "ForecasterNotRunning",
        "alerts": [{"alertname": "ForecasterNotRunning", "service": "ForecasterNotRunning"}],
    }
    assert r.build_query(incident).text == "ForecasterNotRunning (forecaster not running)"


def test_fuse_sums_reciprocal_ranks(r):
    fused = dict(r.fuse({"vector": {"a#0": 1, "b#0": 2}, "keyword": {"b#0": 1}}))
    assert fused["a#0"] == pytest.approx(1 / 61)
    assert fused["b#0"] == pytest.approx(1 / 62 + 1 / 61)
    assert next(iter(fused)) == "b#0"


def test_alert_agreement_outranks_text_signals(r):
    index = FakeIndex(agreement={"listed#0": 0.4}, vector=["other#0", "listed#0"], keyword=["other#0"])
    result = r.retrieve(INCIDENT, index)
    assert result.runbooks == ["listed", "other"]
    assert result.hits[0].alert_agreement == 0.4
    assert result.hits[0].ranks == {"vector": 2}


def test_text_signals_order_chunks_with_equal_agreement(r):
    index = FakeIndex(agreement={"a#0": 1.0, "b#0": 1.0}, vector=["b#0", "a#0"], keyword=["b#0", "a#0"])
    assert r.retrieve(INCIDENT, index).runbooks == ["b", "a"]


def test_runbooks_are_ranked_once_and_hits_capped_at_k(r):
    index = FakeIndex(vector=["a#0", "a#1", "b#0", "a#2", "c#0"])
    result = r.retrieve(INCIDENT, index, k=2, min_similarity=0.5)
    assert result.runbooks == ["a", "b", "c"]
    assert [h.chunk_id for h in result.hits] == ["a#0", "a#1"]


def test_no_match_when_no_alert_agrees_and_similarity_is_low(r):
    index = FakeIndex(vector=["a#0"], keyword=["a#0"], best=0.60)
    result = r.retrieve(INCIDENT, index, min_similarity=0.73)
    assert result.matched is False
    assert result.hits == [] and result.runbooks == []
    assert result.best_similarity == 0.60


def test_unlisted_alert_matches_on_similarity_alone(r):
    index = FakeIndex(vector=["a#0"], best=0.80)
    assert r.retrieve(INCIDENT, index, min_similarity=0.73).runbooks == ["a"]


def test_alert_agreement_matches_whatever_the_similarity(r):
    index = FakeIndex(agreement={"a#0": 0.2}, vector=["a#0"], best=0.10)
    assert r.retrieve(INCIDENT, index, min_similarity=0.73).matched is True
