"""Check AIP native workbook structure and economic identities."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from scripts.extract import build_series_id, parse_series_id, parse_workbook


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
