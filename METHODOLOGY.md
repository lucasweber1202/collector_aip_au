# AIP fuel prices

Official [historical terminal gate data](https://aip.com.au/resources/historical-ulp-and-diesel-tgp-data/) and [annual retail data](https://www.aip.com.au/resources/aip-annual-retail-price-data/). The TGP XLSX dated 25 September 2026 produced 16 series × 5,932 business days = 94,912 observations (2004-01-01–2026-09-25). Petrol national = 231.2 and diesel national = 269.0 cents/litre on 25 September. The annual retail XLSX produced 16 series and 344 observations, 2002–2025, with no financial-year duplicates. Retail geographies are states/territories; TGP geographies are cities, so their IDs are deliberately distinct.

Daily terminal gate prices can lead pump prices; annual retail averages are context only, too slow for a monthly nowcast. Both are cents/litre inclusive of GST, mapped to canonical `unit=other` with exact unit in descriptions. Source archives contain current revised history, not historical publication vintages. No individual source-native series IDs are published in these workbooks; IDs encode file/fuel/geography explicitly.

**Draft gate:** Python HTTP received source anti-bot HTML; XLSX validation succeeded via browser download. Automated fetch, PostgreSQL and same-day/idempotency gates, full clean-room, and Databricks runtime remain. AUD vocabulary pending template PR #1.
