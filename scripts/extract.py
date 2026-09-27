"""Official AIP terminal gate and calendar-year retail fuel prices."""
from __future__ import annotations

import hashlib
import html
import io
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
import openpyxl

from scripts.config import BACKOFF_FACTOR, DOWNLOAD_DELAY, MAX_RETRIES, REQUEST_TIMEOUT, USER_AGENT
from scripts.releases import ReleaseEvidence
from scripts.time_series import Observation

logger = logging.getLogger(__name__)

ROOT = "https://aip.com.au"
TGP_PAGE = ROOT + "/resources/historical-ulp-and-diesel-tgp-data/"
RETAIL_PAGE = ROOT + "/resources/aip-annual-retail-price-data/"
CITIES = ("Sydney", "Melbourne", "Brisbane", "Adelaide", "Perth", "Darwin", "Hobart", "National")
RETAIL_REGIONS = ("NSW", "VIC", "QLD", "SA", "WA", "NT", "TAS", "National")
SHEETS = {"Petrol TGP": ("TGP", "PETROL", "daily"), "Diesel TGP": ("TGP", "DIESEL", "daily"),
          "Average Petrol Retail": ("RETAIL", "PETROL", "annual"),
          "Average Diesel Retail": ("RETAIL", "DIESEL", "annual")}


MIN_PAYLOAD_BYTES = {"TGP": 200_000, "RETAIL": 20_000}
MIN_OBSERVATIONS = {"TGP": 50_000, "RETAIL": 200}
# Markers of bot-protection interstitials seen on Australian and NZ sources.
CHALLENGE_MARKERS = (b"captcha", b"incapsula", b"_incapsula_resource", b"cf-mitigated", b"just a moment",
                     b"pardon our interruption", b"attention required", b"access denied")


class SourceLayoutError(ValueError):
    """An AIP page or workbook no longer has the audited layout."""


class SourceAccessError(RuntimeError):
    """AIP answered with a challenge, an error page or a non-workbook body."""


@dataclass(frozen=True)
class SourceData:
    observations: list[Observation]
    catalog: dict[str, dict[str, Any]]
    releases: tuple[ReleaseEvidence, ...] = ()


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


def _is_challenge(body: bytes) -> bool:
    lowered = body[:20_000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def fetch(client: httpx.Client, url: str) -> httpx.Response:
    """GET with bounded retries for transport errors and 429/5xx only.

    A challenge page or any other 4xx is returned (or raised) at once: retrying
    a bot check is not a legitimate way to get past it.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            if attempt == MAX_RETRIES:
                raise SourceAccessError(f"AIP unreachable after {attempt + 1} attempts: {url}") from exc
            logger.warning("AIP transport error on %s (%s); retrying", url, exc)
        else:
            if response.status_code != 429 and response.status_code < 500:
                if response.status_code >= 400:
                    kind = "challenge" if _is_challenge(response.content) else "error"
                    raise SourceAccessError(f"AIP returned HTTP {response.status_code} ({kind}) for {url}")
                return response
            if attempt == MAX_RETRIES:
                raise SourceAccessError(f"AIP returned HTTP {response.status_code} for {url}")
        time.sleep(DOWNLOAD_DELAY * BACKOFF_FACTOR**attempt)
    raise AssertionError("unreachable")


def check_page(response: httpx.Response) -> str:
    """A source page must be real HTML from AIP, not an interstitial."""
    if _is_challenge(response.content):
        raise SourceAccessError(f"AIP served a bot-protection page for {response.url}")
    return response.text


def check_payload(response: httpx.Response, kind: str) -> bytes:
    """Refuse HTML, challenges and non-XLSX bodies before parsing."""
    blob = response.content
    content_type = response.headers.get("content-type", "").lower()
    head = blob[:512].lstrip().lower()
    if "text/html" in content_type or head.startswith((b"<!doctype", b"<html")) or _is_challenge(blob[:2048]):
        raise SourceAccessError(f"AIP returned HTML instead of the {kind} workbook: {response.url}")
    if not blob.startswith(b"PK\x03\x04"):
        raise SourceAccessError(f"AIP {kind} file is not an XLSX workbook: {response.url}")
    if len(blob) < MIN_PAYLOAD_BYTES[kind]:
        raise SourceAccessError(f"AIP {kind} workbook is implausibly small ({len(blob)} bytes)")
    return blob


def discover_sources(client: httpx.Client) -> tuple[str, str, date, date]:
    tgp_text = check_page(fetch(client, TGP_PAGE))
    time.sleep(DOWNLOAD_DELAY)
    retail_text = check_page(fetch(client, RETAIL_PAGE))
    tgp_match = re.search(r'href="([^"]+/AIP_TGP_Data_(\d{2}-[A-Za-z]{3}-\d{4})\.xlsx)"', html.unescape(tgp_text))
    retail_match = re.search(r'href="([^"]+/AIP_Annual_Retail_Price_Data\.xlsx)"', html.unescape(retail_text))
    if not tgp_match or not retail_match:
        raise SourceLayoutError("AIP official workbook links missing from the source pages")
    published = datetime.strptime(tgp_match.group(2), "%d-%b-%Y").replace(tzinfo=UTC).date()
    retail_plain = html.unescape(re.sub(r"<[^>]+>", " ", retail_text))
    retail_date = re.search(r"PUBLICATION DATE\s+(\d{1,2} [A-Za-z]+ \d{4})", retail_plain, re.IGNORECASE)
    if retail_date is None:
        raise SourceLayoutError("AIP retail publication date missing")
    retail_published = datetime.strptime(retail_date.group(1), "%d %B %Y").replace(tzinfo=UTC).date()
    return urljoin(ROOT, tgp_match.group(1)), urljoin(ROOT, retail_match.group(1)), published, retail_published


def parse_workbook(blob: bytes, url: str, kind: str, published: date, min_observations: int | None = None) -> SourceData:
    workbook = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    names = [name for name, spec in SHEETS.items() if spec[0] == kind]
    if any(name not in workbook for name in names):
        raise SourceLayoutError(f"AIP {kind} sheet missing")
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
            raise SourceLayoutError(f"AIP {name} geography header changed")
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
    minimum = MIN_OBSERVATIONS[kind] if min_observations is None else min_observations
    if len(observations) < max(minimum, 1):
        raise SourceLayoutError(f"AIP {kind} yielded {len(observations)} observations; expected at least {minimum}")
    return SourceData(observations, catalog)


def collect() -> SourceData:
    """Discover both workbooks on the official pages and parse them."""
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        tgp_url, retail_url, published, retail_published = discover_sources(client)
        time.sleep(DOWNLOAD_DELAY)
        tgp = check_payload(fetch(client, tgp_url), "TGP")
        time.sleep(DOWNLOAD_DELAY)
        retail = check_payload(fetch(client, retail_url), "RETAIL")
    first = parse_workbook(tgp, tgp_url, "TGP", published)
    second = parse_workbook(retail, retail_url, "RETAIL", retail_published)
    evidence = tuple(
        ReleaseEvidence(kind, url, when, max(o.reference_date for o in part.observations), frozenset(part.catalog))
        for kind, url, when, part in (("TGP", tgp_url, published, first), ("RETAIL", retail_url, retail_published, second))
    )
    logger.info("AIP TGP %s (%d obs), retail %s (%d obs)", published, len(first.observations), retail_published, len(second.observations))
    return SourceData(first.observations + second.observations, first.catalog | second.catalog, evidence)
