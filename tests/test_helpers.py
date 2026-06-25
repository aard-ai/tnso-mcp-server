from datetime import date

from tnso_mcp_server.tools.helpers import build_key, csv_to_tsv, current_buddhist_year


def test_csv_to_tsv():
    assert csv_to_tsv("A,B\n1,2\n") == "A\tB\n1\t2"


def test_build_key_all_when_no_filters():
    assert build_key(["POP_IND", "SEX", "AREA", "CWT"], None) == "all"


def test_build_key_positions_values_in_dimension_order():
    # order: POP_IND(empty) . SEX(_T) . AREA(empty) . CWT(10)
    key = build_key(["POP_IND", "SEX", "AREA", "CWT"], {"CWT": ["10"], "SEX": ["_T"]})
    assert key == "._T..10"


def test_build_key_joins_multiple_codes_with_plus():
    key = build_key(["CWT"], {"CWT": ["10", "58"]})
    assert key == "10+58"


def test_current_buddhist_year():
    assert current_buddhist_year() == date.today().year + 543
