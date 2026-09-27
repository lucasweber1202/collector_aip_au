# AIP fuel price predictors

Standalone Python 3.11 collector of Australian Institute of Petroleum terminal
gate prices (daily, seven capitals plus national average) and retail prices
(calendar-year averages, seven states/territories plus national). Workbook
links and publication dates are discovered on the official pages and fetched
unattended over HTTPS; challenge pages and non-workbook bodies are refused.
Canonical `metadata`, `time_series` and `logs` with vintages.

Set `COLLECTOR_DB_URL=postgresql+psycopg2://...` for local runs or `PROD=true`
for Databricks. Tests: `pytest` (Spark grammar needs `java`);
`COLLECTOR_TEST_PG_URL=<disposable PostgreSQL>` for the PostgreSQL write paths,
`AIP_LIVE_SMOKE=1` for the live unattended fetch. See METHODOLOGY.md.
