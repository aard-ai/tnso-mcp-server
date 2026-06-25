from tnso_mcp_server.api.models import DataflowInfo
from tnso_mcp_server.utils.blacklist import DataflowBlacklist


def test_blacklist_filters_matching_ids():
    bl = DataflowBlacklist(["X"])
    dataflows = [DataflowInfo(id="X"), DataflowInfo(id="Y")]
    assert [d.id for d in bl.filter_dataflows(dataflows)] == ["Y"]


def test_empty_blacklist_is_noop():
    bl = DataflowBlacklist([])
    dataflows = [DataflowInfo(id="X")]
    assert bl.filter_dataflows(dataflows) == dataflows


def test_env_blacklist(monkeypatch):
    monkeypatch.setenv("DATAFLOW_BLACKLIST", "A, B")
    bl = DataflowBlacklist()
    assert bl.is_blacklisted("A")
    assert bl.is_blacklisted("B")
    assert not bl.is_blacklisted("C")
