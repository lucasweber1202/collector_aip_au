"""Official AIP terminal gate and calendar-year retail fuel prices."""
from __future__ import annotations

import hashlib
import html
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
import openpyxl

from scripts.config import REQUEST_TIMEOUT, USER_AGENT
from scripts.time_series import Observation

ROOT = "https://aip.com.au"
TGP_PAGE = ROOT + "/resources/historical-ulp-and-diesel-tgp-data/"
RETAIL_PAGE = ROOT + "/resources/aip-annual-retail-price-data/"
CITIES = ("Sydney", "Melbourne", "Brisbane", "Adelaide", "Perth", "Darwin", "Hobart", "National")
RETAIL_REGIONS = ("NSW", "VIC", "QLD", "SA", "WA", "NT", "TAS", "National")
SHEETS = {"Petrol TGP": ("TGP", "PETROL", "daily"), "Diesel TGP": ("TGP", "DIESEL", "daily"),
          "Average Petrol Retail": ("RETAIL", "PETROL", "annual"),
          "Average Diesel Retail": ("RETAIL", "DIESEL", "annual")}


@dataclass(frozen=True)
class SourceData:
    observations: list[Observation]
    catalog: dict[str, dict[str, Any]]


def build_series_id(kind: str, fuel: str, city: str) -> str:
    if kind not in {"TGP", "RETAIL"} or fuel not in {"PETROL", "DIESEL"} or city not in (CITIES if kind == "TGP" else RETAIL_REGIONS):
        raise ValueError(f"Invalid AIP identity: {kind}, {fuel}, {city}")
    return f"AIP_{kind}_{fuel}_{city.upper()}"


def parse_series_id(series_id: str) -> tuple[str, str, str]:
    parts = series_id.split("_")
    if len(parts) != 4 or parts[0] != "AIP":
        raise ValueError(f"Invalid AIP series ID: {series_id}")
    kind, fuel, city = parts[1:]
    region = city.title() if kind == "TGP" or city == "NATIONAL" else city
    if build_series_id(kind, fuel, region) != series_id:
        raise ValueError(f"Invalid AIP series ID: {series_id}")
    return kind, fuel, region


def discover_sources(client: httpx.Client) -> tuple[str, str, date]:
    tgp = client.get(TGP_PAGE)
    retail = client.get(RETAIL_PAGE)
    tgp.raise_for_status()
    retail.raise_for_status()
    tgp_match = re.search(r'href="([^"]+/AIP_TGP_Data_(\d{2}-[A-Za-z]{3}-\d{4})\.xlsx)"', html.unescape(tgp.text))
    retail_match = re.search(r'href="([^"]+/AIP_Annual_Retail_Price_Data\.xlsx)"', html.unescape(retail.text))
    if not tgp_match or not retail_match:
        raise ValueError("AIP official workbook links missing or blocked by source protection")
    published = datetime.strptime(tgp_match.group(2), "%d-%b-%Y").replace(tzinfo=UTC).date()
    return urljoin(ROOT, tgp_match.group(1)), urljoin(ROOT, retail_match.group(1)), published


def parse_workbook(blob: bytes, url: str, kind: str, published: date) -> SourceData:
    workbook = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    names = [name for name, spec in SHEETS.items() if spec[0] == kind]
    if any(name not in workbook for name in names):
        raise ValueError(f"AIP {kind} sheet missing")
    observations: list[Observation] = []
    catalog: dict[str, dict[str, Any]] = {}
    snapshot = hashlib.sha256(blob).hexdigest()
    seen: set[tuple[str, date]] = set()
    for name in names:
        _, fuel, frequency = SHEETS[name]
        rows = iter(workbook[name].values)
        header = next(rows)
        regions = CITIES if kind == "TGP" else RETAIL_REGIONS
        if tuple(str(v).replace("\nAverage", "").strip() for v in header[1:9]) != regions:
            raise ValueError(f"AIP {name} geography header changed")
        for row in rows:
            if kind == "TGP":
                if not isinstance(row[0], datetime):
                    continue
                ref = row[0].date()
            else:
                # Calendar-year observations only; financial years are a different measure.
                if not isinstance(row[0], int) or row[0] < 1900:
                    continue
                ref = date(row[0], 12, 31)
            for i, city in enumerate(regions, start=1):
                raw = row[i]
                if raw is None:
                    continue
                if not isinstance(raw, int | float) or not math.isfinite(raw) or raw <= 0:
                    raise ValueError(f"Invalid AIP value in {name} at {ref}")
                sid = build_series_id(kind, fuel, city)
                key = sid, ref
                if key in seen:
                    raise ValueError(f"Duplicate AIP economic key: {key}")
                seen.add(key)
                observations.append(Observation(sid, ref, float(raw), snapshot))
                catalog[sid] = {"name": f"{fuel.title()} {kind} {city}", "description": f"AIP {name}; {city}; cents per litre including GST",
                                "country": "AUD", "frequency": frequency, "unit": "other", "eco_group": "consumer_prices" if kind == "RETAIL" else "producer_prices",
                                "source_url": url, "last_publish_date": published}
    if not observations:
        raise ValueError("No AIP fuel price data")
    return SourceData(observations, catalog)


def collect() -> SourceData:
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        tgp_url, retail_url, published = discover_sources(client)
        tgp = client.get(tgp_url)
        retail = client.get(retail_url)
        tgp.raise_for_status()
        retail.raise_for_status()
    first = parse_workbook(tgp.content, tgp_url, "TGP", published)
    second = parse_workbook(retail.content, retail_url, "RETAIL", published)
    return SourceData(first.observations + second.observations, first.catalog | second.catalog)
