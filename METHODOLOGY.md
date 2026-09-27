# AIP fuel prices — methodology

Authority: `guimasuko/collector_template` main `723f8633bbd367ad9cca0a199e84b10fd355da36`;
`AUD` is in its `metadata.country` vocabulary and is what this collector emits.

## Source

Official Australian Institute of Petroleum pages:
[historical terminal gate prices](https://aip.com.au/resources/historical-ulp-and-diesel-tgp-data/)
and [annual retail price data](https://www.aip.com.au/resources/aip-annual-retail-price-data/).
The collector reads both pages, takes the current workbook links from them
(`AIP_TGP_Data_<dd-Mon-yyyy>.xlsx`, `AIP_Annual_Retail_Price_Data.xlsx`) and
the publication dates (the TGP file-name date; the retail page's
`PUBLICATION DATE`). No URL or date is hardcoded.

## Unattended acquisition (resolved)

The earlier draft recorded that "Python HTTP received source anti-bot HTML".
On 2026-09-27 the pages and both workbooks were fetched unattended over plain
HTTPS from this session with the collector's own `httpx` client: pages 200
`text/html` (LiteSpeed), workbooks 200
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` with the
XLSX magic, six consecutive TGP downloads all valid, and the page also served
with the default `python-httpx` User-Agent. No headers beyond a User-Agent,
no cookies and no browser are involved. The one failure observed was a
connection reset inside this session's egress proxy, not an AIP response.

Hardening so a challenge can never be parsed as data:

- `fetch` retries only transport errors, HTTP 429 and 5xx (bounded by
  `COLLECTOR_MAX_RETRIES`, exponential back-off from
  `COLLECTOR_DOWNLOAD_DELAY`); any other 4xx fails at once and says whether the
  body is a bot challenge. A challenge is never retried.
- `check_page` refuses a page carrying bot-protection markers (captcha,
  Incapsula, Cloudflare, "Just a moment", "Pardon Our Interruption").
- `check_payload` refuses HTML (by `Content-Type` or body), a body without the
  XLSX `PK\x03\x04` magic, and an implausibly small file.
- Missing sheets, changed geography headers or fewer observations than a full
  file holds (TGP 50,000; retail 200) raise `SourceLayoutError`.

## Series

Daily terminal gate prices for 8 cities (incl. national) and annual
calendar-year retail averages for 8 states/territories (incl. national), petrol
and diesel: 32 series. Cents/litre including GST; canonical `unit = other`
with the exact unit in the description. TGP `eco_group = producer_prices`,
retail `consumer_prices`. Financial-year rows are a different measure and are
skipped. The workbooks publish no native series codes, so IDs encode
file/fuel/geography (`AIP_TGP_PETROL_NATIONAL`, `AIP_RETAIL_DIESEL_NSW`).

26 September 2026 workbook: TGP 16 series × 5,932 business days = 94,912
observations (1 Jan 2004 – 25 Sep 2026); retail 16 series, 344 observations
(2002–2025). National TGP on 25 Sep 2026: petrol 231.2, diesel 269.0.

## Release monitoring

Each workbook is classified on every run from its publication date and latest
covered date against `metadata` before the run, plus rows changed:
`first_release`, `same_release`, `new_release`, `revised_source` or
`layout_changed`. An unchanged rerun on a later day is `same_release`; a date
that goes backwards fails the run.

## Point in time

Both workbooks are current revised archives, not historical publications.
`vintage_date` is the UTC collection date; the first backfill is dated the day
it was collected. Later changes become new vintages; a same-day change
overwrites that day's vintage (template rule). Daily TGP can lead pump prices;
annual retail is context only for a monthly nowcast.

## Verification (2026-09-27)

- PostgreSQL 16.13, live unattended `main.py`: run 1 wrote 95,256
  observations and 32 metadata rows (`first_release`); run 2 wrote nothing
  (`same_release`).
- `tests/test_postgres_integration.py`: canonical tables, idempotent rerun,
  later-day vintage, same-day overwrite, metadata MERGE with NULL in every
  nullable column, time-series MERGE, run log, release classification.
- All emitted SQL parses with the Spark SQL grammar (pyspark 4.1.1).
  **Databricks corporate runtime: not verified.**
