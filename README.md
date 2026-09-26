# AIP fuel price predictors (draft)

Standalone Python 3.11 collector of Australian Institute of Petroleum terminal gate prices (daily, seven capitals plus national average) and retail prices (calendar-year averages, seven states/territories plus national). Source-native workbook geography and frequency are preserved in stable IDs; canonical metadata, time_series and logs use vintages for changes.

Set `COLLECTOR_DB_URL=postgresql+psycopg2://...` for local runs or `PROD=true` for Databricks. Source pages and files currently present anti-bot HTML to terminal HTTP clients in this environment. The two official XLSX files were retrieved through the browser, parsed and spot-checked; unattended `collect()` must be validated from the deployment machine. See METHODOLOGY.md.
