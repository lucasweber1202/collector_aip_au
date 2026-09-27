"""Check AIP native workbook structure and economic identities."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts import metadata
from scripts.extract import (
    SourceAccessError,
    SourceLayoutError,
    build_series_id,
    check_page,
    check_payload,
    collect,
    fetch,
    parse_series_id,
    parse_workbook,
)
from tests.aip_workbook import retail, tgp


def test_ids() -> None:
    for kind, fuel, region in (("TGP", "PETROL", "Sydney"), ("RETAIL", "DIESEL", "NSW")):
        assert parse_series_id(build_series_id(kind, fuel, region)) == (kind, fuel, region)
    with pytest.raises(ValueError):
        build_series_id("TGP", "PETROL", "NSW")


@pytest.mark.skipif(not os.getenv("AIP_OFFICIAL_XLSX"), reason="opt-in official workbook")
def test_official_workbooks() -> None:
    root = Path(os.environ["AIP_OFFICIAL_XLSX"])
    tgp = parse_workbook((root / "AIP_TGP_Data_25-Sep-2026.xlsx").read_bytes(), "https://aip.com.au/tgp", "TGP", date(2026, 9, 25))
    retail = parse_workbook((root / "AIP_Annual_Retail_Price_Data.xlsx").read_bytes(), "https://aip.com.au/retail", "RETAIL", date(2026, 7, 8))
    assert len(tgp.catalog) == 16 and len(tgp.observations) == 94912
    assert len(retail.catalog) == 16 and len(retail.observations) == 344
    assert next(o.value for o in tgp.observations if o.series_id == "AIP_TGP_DIESEL_NATIONAL" and o.reference_date == date(2026, 9, 25)) == 269


def test_tgp_and_retail_layouts() -> None:
    days = [date(2026, 9, 24), date(2026, 9, 25)]
    data = parse_workbook(tgp(days), "https://aip.com.au/tgp.xlsx", "TGP", date(2026, 9, 25), min_observations=1)
    assert len(data.catalog) == 16 and len(data.observations) == 32
    petrol = data.catalog["AIP_TGP_PETROL_NATIONAL"]
    assert (petrol["frequency"], petrol["unit"], petrol["eco_group"], petrol["country"]) == ("daily", "other", "producer_prices", "AUD")
    annual = parse_workbook(retail([2024, 2025]), "https://aip.com.au/retail.xlsx", "RETAIL", date(2026, 7, 8), min_observations=1)
    assert {o.reference_date for o in annual.observations} == {date(2024, 12, 31), date(2025, 12, 31)}
    assert annual.catalog["AIP_RETAIL_DIESEL_NSW"]["frequency"] == "annual"
    metadata.validate_catalog(data.catalog | annual.catalog)


def test_short_or_reshaped_workbooks_fail() -> None:
    with pytest.raises(SourceLayoutError, match="observations"):
        parse_workbook(tgp([date(2026, 9, 25)]), "u", "TGP", date(2026, 9, 25))
    with pytest.raises(SourceLayoutError, match="sheet missing"):
        parse_workbook(retail([2025]), "u", "TGP", date(2026, 9, 25), min_observations=1)


def _response(body: bytes, content_type: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": content_type}, request=httpx.Request("GET", "https://aip.com.au/f"))


def test_challenges_and_non_workbooks_are_refused() -> None:
    with pytest.raises(SourceAccessError, match="HTML"):
        check_payload(_response(b"<html>Please complete the captcha</html>" * 10000, "text/html"), "TGP")
    with pytest.raises(SourceAccessError, match="not an XLSX"):
        check_payload(_response(b"x" * 300000, "application/octet-stream"), "TGP")
    with pytest.raises(SourceAccessError, match="small"):
        check_payload(_response(b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), "RETAIL")
    with pytest.raises(SourceAccessError, match="bot-protection"):
        check_page(_response(b"<html><script src='/_Incapsula_Resource?x'></script></html>", "text/html"))


def test_fetch_does_not_retry_a_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(403, content=b"<html>Just a moment...</html>")

    monkeypatch.setattr("scripts.extract.DOWNLOAD_DELAY", 0.0)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(SourceAccessError, match="challenge"):
        fetch(client, "https://aip.com.au/x")
    assert len(calls) == 1


def test_fetch_retries_transient_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    monkeypatch.setattr("scripts.extract.DOWNLOAD_DELAY", 0.0)
    with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
        assert fetch(client, "https://aip.com.au/x").content == b"ok"


@pytest.mark.skipif(os.getenv("AIP_LIVE_SMOKE") != "1", reason="opt-in unattended official fetch")
def test_live_unattended_fetch() -> None:
    result = collect()
    assert {e.name for e in result.releases} == {"TGP", "RETAIL"}
    assert len(result.catalog) == 32 and len(result.observations) > 90_000
    first = min(o.reference_date for o in result.observations if o.series_id == "AIP_TGP_PETROL_NATIONAL")
    assert first == date(2004, 1, 1)
