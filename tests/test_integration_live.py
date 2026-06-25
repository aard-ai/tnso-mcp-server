"""Live integration tests against the real TNSO SDMX API.

Opt-in only: run with  `pytest -m integration`  (skipped by default elsewhere via
`-m 'not integration'`). Requires outbound network access to
https://ns1-stathub.nso.go.th. If your network intercepts TLS, set SSL_CERT_FILE.
"""

from __future__ import annotations

import pytest

from tnso_mcp_server.api.client import ApiClient

pytestmark = pytest.mark.integration

AGING = "DF_01DI_IND_AGING"
AGING_VERSION = "1.0"


async def test_live_dataflows_list():
    api = ApiClient()
    try:
        dataflows = await api.get_dataflows()
        # Don't tie the smoke test to a moving catalog size; just require a non-empty
        # list plus the known AGING dataflow.
        assert dataflows
        assert any(d.id == AGING for d in dataflows)
    finally:
        await api.aclose()


async def test_live_constraints_and_data():
    api = ApiClient()
    try:
        dim_values, time_range = await api.get_availableconstraint(AGING, AGING_VERSION)
        assert "CWT" in dim_values
        assert time_range is not None and time_range[1]

        csv_text = await api.get_data_csv(
            AGING, AGING_VERSION, key="all", start_period="2567", end_period="2567"
        )
        assert "OBS_VALUE" in csv_text
        assert "TNSO:DF_01DI_IND_AGING" in csv_text
    finally:
        await api.aclose()


async def test_live_province_codelist():
    api = ApiClient()
    try:
        codelist = await api.get_codelist("CL_CWT")
        assert len(codelist.values) >= 70
        bangkok = next((c for c in codelist.values if c.code == "10"), None)
        assert bangkok is not None
        assert "Bangkok" in bangkok.name_en
    finally:
        await api.aclose()
