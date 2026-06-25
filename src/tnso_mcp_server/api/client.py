"""Async SDMX REST client for the TNSO (Thailand NSO) endpoint.

Mapped from ISTAT:
  * base URL  ``esploradati.istat.it/SDMXWS/rest`` -> ``ns1-stathub.nso.go.th/rest``
  * agency    ``IT1`` -> ``TNSO``
  * data path bare ``{df}/.../ALL/`` -> comma flowRef ``TNSO,{df},{ver}/{key}``
  * data is fetched as native **SDMX-CSV** (TNSO supports it directly), while
    structure/codelist/conceptscheme/constraint metadata stays SDMX-ML (parsed with lxml).
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import time
from collections import deque
from urllib.parse import urlencode

import httpx
from lxml import etree
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .models import (
    ApiError,
    CodelistInfo,
    CodeValue,
    ConceptInfo,
    ConceptSchemeInfo,
    DataflowInfo,
    DatastructureInfo,
    DimensionInfo,
    NoRecordsError,
)

logger = logging.getLogger(__name__)

# SDMX 2.1 namespaces used by the TNSO NSI web service.
NS = {
    "message": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "structure": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "common": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

CSV_ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
XML_ACCEPT = "application/vnd.sdmx.structure+xml;version=2.1"

DEFAULT_BASE_URL = "https://ns1-stathub.nso.go.th/rest"
DEFAULT_AGENCY = "TNSO"


def _localized(elem, tag: str) -> dict[str, str]:
    """Collect ``<common:{tag} xml:lang=...>`` children into a {lang: text} dict."""
    out: dict[str, str] = {}
    for node in elem.findall(f"common:{tag}", NS):
        lang = node.get(_XML_LANG, "")
        out[lang] = (node.text or "").strip()
    return out


def _is_retryable_error(exc: BaseException) -> bool:
    """Decide whether a request failure is worth retrying.

    Only transient failures are retried: network/transport errors, timeouts, and
    server-side responses (HTTP 5xx) or rate limiting (429). Other 4xx responses
    are deterministic — replaying a bad dataflow id, filter, or period would just
    burn the rate-limit budget — so they are surfaced immediately.
    """
    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


class RateLimiter:
    """Simple sliding-window async rate limiter (default 4 calls / 60s)."""

    def __init__(self, max_calls: int = 4, time_window: float = 60.0) -> None:
        """Allow at most ``max_calls`` within any rolling ``time_window`` seconds."""
        self.max_calls = max_calls
        self.time_window = time_window
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a call slot is free, then record this call."""
        async with self._lock:
            now = time.monotonic()
            self._evict(now)
            if len(self._calls) >= self.max_calls:
                wait = self.time_window - (now - self._calls[0])
                if wait > 0:
                    logger.debug("Rate limit reached; sleeping %.1fs", wait)
                    await asyncio.sleep(wait)
                self._evict(time.monotonic())
            self._calls.append(time.monotonic())

    def _evict(self, now: float) -> None:
        """Drop call timestamps that have aged out of the current window."""
        while self._calls and now - self._calls[0] >= self.time_window:
            self._calls.popleft()


class ApiClient:
    """Async SDMX REST client for the TNSO endpoint (rate-limited, retrying)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        agency: str = DEFAULT_AGENCY,
        timeout: float = 30.0,
        availableconstraint_timeout: float = 180.0,
        max_retries: int = 3,
    ) -> None:
        """Configure the HTTP client, rate limiter, and retry/timeout budgets."""
        self.base_url = base_url.rstrip("/")
        self.agency = agency
        self.timeout = timeout
        self.availableconstraint_timeout = availableconstraint_timeout
        self.max_retries = max_retries
        self._rate_limiter = RateLimiter()
        # Honour a CA bundle if the network intercepts TLS; else system default.
        ca_bundle = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
        verify: object = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=True,  # respect HTTP(S)_PROXY
            verify=verify,
            headers={"User-Agent": "tnso-mcp-server/0.1 (+https://github.com/ondata/istat_mcp_server)"},
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Low-level GET with retry + rate limiting
    # ------------------------------------------------------------------ #
    async def _get(
        self,
        path: str,
        *,
        params: dict | None = None,
        accept: str = XML_ACCEPT,
        timeout: float | None = None,
    ) -> str:
        """GET ``path`` with rate limiting + transient-failure retries; return the body text.

        Raises :class:`ApiError` for HTTP errors, timeouts, and transport failures.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"

        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_is_retryable_error),
            reraise=True,
        )
        async def _do() -> str:
            """Run one rate-limited GET attempt; retried by the decorator on transient errors."""
            await self._rate_limiter.acquire()
            start = time.monotonic()
            logger.info("-> GET %s", url)
            resp = await self._client.get(
                url,
                params=params,
                headers={"Accept": accept},
                timeout=timeout or self.timeout,
            )
            elapsed = time.monotonic() - start
            logger.info("<- %s %s (%d bytes, %.2fs)", resp.status_code, url, len(resp.content), elapsed)
            if resp.status_code == 404:
                snippet = resp.text[:300]
                if "NoRecordsFound" in snippet or "No Results Found" in snippet:
                    raise NoRecordsError(
                        "No records found. Check the dataflow id, dimension filters, "
                        "and time period (TNSO years are Buddhist Era, e.g. 2567 = 2024).",
                        status_code=404,
                    )
            resp.raise_for_status()
            return resp.text

        try:
            return await _do()
        except ApiError:
            raise
        except httpx.TimeoutException as exc:
            raise ApiError(
                f"Request to {url} timed out. Try narrowing the query (filters / period)."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"HTTP {exc.response.status_code} from {url}", status_code=exc.response.status_code
            ) from exc
        except httpx.RequestError as exc:
            # NetworkError and other transport-level failures: normalize so callers
            # only ever have to handle ApiError, never a raw httpx exception.
            raise ApiError(f"Network error contacting {url}: {type(exc).__name__}") from exc

    # ------------------------------------------------------------------ #
    # flowRef / URL helpers
    # ------------------------------------------------------------------ #
    def flow_ref(self, dataflow_id: str, version: str | None = None) -> str:
        """Build an SDMX comma flowRef, e.g. ``TNSO,DF_X,1.0`` (version defaults to 'latest')."""
        return f"{self.agency},{dataflow_id},{version or 'latest'}"

    def _data_query(
        self,
        start_period: str | None = None,
        end_period: str | None = None,
        detail: str = "full",
        dimension_at_observation: str | None = None,
        first_n_observations: int | None = None,
    ) -> dict[str, str]:
        """Build the SDMX data query params shared by the real fetch and the footer URLs."""
        query: dict[str, str] = {}
        if start_period:
            query["startPeriod"] = start_period
        if end_period:
            query["endPeriod"] = end_period
        if detail and detail != "full":
            query["detail"] = detail
        if dimension_at_observation:
            query["dimensionAtObservation"] = dimension_at_observation
        if first_n_observations and first_n_observations > 0:
            query["firstNObservations"] = str(first_n_observations)
        return query

    def data_csv_url(
        self,
        dataflow_id: str,
        version: str | None = None,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        detail: str = "full",
        dimension_at_observation: str | None = None,
    ) -> str:
        """A browser-openable CSV URL (uses ``?format=csv``, verified on the NSI service).

        Carries every request-shaping param so the link reproduces the same dataset
        as the returned table.
        """
        query = {
            "format": "csv",
            **self._data_query(start_period, end_period, detail, dimension_at_observation),
        }
        return f"{self.base_url}/data/{self.flow_ref(dataflow_id, version)}/{key or 'all'}?{urlencode(query)}"

    def data_curl_url(
        self,
        dataflow_id: str,
        version: str | None = None,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        detail: str = "full",
        dimension_at_observation: str | None = None,
    ) -> str:
        """Same dataset as ``data_csv_url`` but relying on the Accept header (no ``?format``).

        Used for the curl example so it carries identical period/detail/layout params.
        """
        query = self._data_query(start_period, end_period, detail, dimension_at_observation)
        qs = f"?{urlencode(query)}" if query else ""
        return f"{self.base_url}/data/{self.flow_ref(dataflow_id, version)}/{key or 'all'}{qs}"

    # ------------------------------------------------------------------ #
    # Metadata fetchers (SDMX-ML)
    # ------------------------------------------------------------------ #
    async def get_dataflows(self) -> list[DataflowInfo]:
        """Fetch and parse the full list of TNSO dataflows."""
        xml = await self._get(f"dataflow/{self.agency}")
        return self._parse_dataflows(xml)

    @staticmethod
    def _parse_dataflows(xml: str) -> list[DataflowInfo]:
        """Parse a dataflow list SDMX-ML document into ``DataflowInfo`` objects."""
        root = etree.fromstring(xml.encode("utf-8"))
        out: list[DataflowInfo] = []
        for df in root.iterfind(".//structure:Dataflow", NS):
            names = _localized(df, "Name")
            descs = _localized(df, "Description")
            ref = df.find("./structure:Structure/Ref", NS)
            out.append(
                DataflowInfo(
                    id=df.get("id", ""),
                    version=df.get("version", ""),
                    agency=df.get("agencyID", ""),
                    name_en=names.get("en", ""),
                    name_th=names.get("th", ""),
                    description_en=descs.get("en", ""),
                    description_th=descs.get("th", ""),
                    id_datastructure=ref.get("id", "") if ref is not None else "",
                )
            )
        return out

    async def get_datastructure(self, id_datastructure: str) -> DatastructureInfo:
        """Fetch and parse a data structure definition (DSD) by id."""
        xml = await self._get(f"datastructure/{self.agency}/{id_datastructure}")
        return self._parse_datastructure(id_datastructure, xml)

    @staticmethod
    def _parse_datastructure(id_datastructure: str, xml: str) -> DatastructureInfo:
        """Parse a DSD document into ordered dimensions (time dimension included)."""
        root = etree.fromstring(xml.encode("utf-8"))
        items: list[tuple[int, DimensionInfo]] = []
        dim_list = root.find(".//structure:DataStructureComponents/structure:DimensionList", NS)
        if dim_list is not None:
            for tag in ("Dimension", "TimeDimension"):
                for dim in dim_list.findall(f"structure:{tag}", NS):
                    enum_ref = dim.find(".//structure:Enumeration/Ref", NS)
                    pos = dim.get("position")
                    items.append(
                        (
                            int(pos) if pos and pos.isdigit() else 9999,
                            DimensionInfo(
                                dimension=dim.get("id", ""),
                                codelist=enum_ref.get("id", "") if enum_ref is not None else "",
                            ),
                        )
                    )
        items.sort(key=lambda t: t[0])
        return DatastructureInfo(id_datastructure=id_datastructure, dimensions=[d for _, d in items])

    async def get_codelist(self, codelist_id: str) -> CodelistInfo:
        """Fetch and parse a codelist (code -> Thai/English labels) by id."""
        xml = await self._get(f"codelist/{self.agency}/{codelist_id}")
        return self._parse_codelist(codelist_id, xml)

    @staticmethod
    def _parse_codelist(codelist_id: str, xml: str) -> CodelistInfo:
        """Parse a codelist document into ``CodeValue`` entries."""
        root = etree.fromstring(xml.encode("utf-8"))
        values: list[CodeValue] = []
        for code in root.iterfind(".//structure:Codelist/structure:Code", NS):
            names = _localized(code, "Name")
            values.append(
                CodeValue(code=code.get("id", ""), name_en=names.get("en", ""), name_th=names.get("th", ""))
            )
        return CodelistInfo(id_codelist=codelist_id, values=values)

    async def get_availableconstraint(
        self, dataflow_id: str, version: str | None = None
    ) -> tuple[dict[str, list[str]], tuple[str, str] | None]:
        """Return ({dimension: [codes]}, (start_period, end_period)|None).

        Uses ``?mode=available`` so the server returns only codes actually present
        in the data. Structure: ContentConstraint > CubeRegion > common:KeyValue[@id]
        > common:Value, plus a common:TimeRange for the time dimension.
        """
        flow = self.flow_ref(dataflow_id, version)
        xml = await self._get(
            f"availableconstraint/{flow}/all/all",
            params={"mode": "available"},
            timeout=self.availableconstraint_timeout,
        )
        return self._parse_availableconstraint(xml)

    @staticmethod
    def _parse_availableconstraint(
        xml: str,
    ) -> tuple[dict[str, list[str]], tuple[str, str] | None]:
        """Parse an availableconstraint document into (dim -> codes, time range)."""
        root = etree.fromstring(xml.encode("utf-8"))
        dim_values: dict[str, list[str]] = {}
        for kv in root.iterfind(".//structure:CubeRegion/common:KeyValue", NS):
            dim = kv.get("id", "")
            values = [(v.text or "").strip() for v in kv.findall("common:Value", NS) if v.text]
            if values:
                dim_values[dim] = values
        time_range: tuple[str, str] | None = None
        tr = root.find(".//common:TimeRange", NS)
        if tr is not None:
            start = (tr.findtext("common:StartPeriod", default="", namespaces=NS) or "").strip()
            end = (tr.findtext("common:EndPeriod", default="", namespaces=NS) or "").strip()
            time_range = (start, end)
        return dim_values, time_range

    async def get_conceptschemes(self) -> list[ConceptSchemeInfo]:
        """Fetch and parse every TNSO concept scheme (used by the concept lookup)."""
        xml = await self._get(f"conceptscheme/{self.agency}")
        return self._parse_conceptschemes(xml)

    @staticmethod
    def _parse_conceptschemes(xml: str) -> list[ConceptSchemeInfo]:
        """Parse concept-scheme documents into ``ConceptSchemeInfo`` objects."""
        root = etree.fromstring(xml.encode("utf-8"))
        schemes: list[ConceptSchemeInfo] = []
        for cs in root.iterfind(".//structure:ConceptScheme", NS):
            cs_names = _localized(cs, "Name")
            concepts: list[ConceptInfo] = []
            for concept in cs.iterfind("structure:Concept", NS):
                cnames = _localized(concept, "Name")
                concepts.append(
                    ConceptInfo(
                        id=concept.get("id", ""),
                        name_en=cnames.get("en", ""),
                        name_th=cnames.get("th", ""),
                    )
                )
            schemes.append(
                ConceptSchemeInfo(
                    id=cs.get("id", ""),
                    agency=cs.get("agencyID", ""),
                    version=cs.get("version", ""),
                    name_en=cs_names.get("en", ""),
                    concepts=concepts,
                )
            )
        return schemes

    # ------------------------------------------------------------------ #
    # Data fetch (SDMX-CSV)
    # ------------------------------------------------------------------ #
    async def get_data_csv(
        self,
        dataflow_id: str,
        version: str | None = None,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        detail: str = "full",
        dimension_at_observation: str | None = None,
        first_n_observations: int | None = None,
    ) -> str:
        """Fetch observations as native SDMX-CSV text for the given flowRef + key.

        ``first_n_observations`` (when set) bounds the payload to the first N
        observations per series — used by cheap non-empty probes, not full fetches.
        """
        params = self._data_query(
            start_period, end_period, detail, dimension_at_observation, first_n_observations
        )
        path = f"data/{self.flow_ref(dataflow_id, version)}/{key or 'all'}"
        return await self._get(path, params=params, accept=CSV_ACCEPT)
