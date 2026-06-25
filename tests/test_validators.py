from tnso_mcp_server.utils.validators import validate_dataflow_id, validate_keywords


def test_validate_keywords_splits_and_lowercases():
    assert validate_keywords("Pop, Aging ,  ") == ["pop", "aging"]


def test_validate_keywords_empty():
    assert validate_keywords("") == []
    assert validate_keywords(None) == []


def test_validate_dataflow_id_accepts_tnso_ids():
    assert validate_dataflow_id("DF_01DI_IND_AGING")


def test_validate_dataflow_id_rejects_bad():
    assert not validate_dataflow_id("")
    assert not validate_dataflow_id("bad id!")
    assert not validate_dataflow_id("TNSO,DF,1.0")  # commas/dots are not allowed in the id alone
