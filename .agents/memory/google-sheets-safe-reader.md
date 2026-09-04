---
name: Google Sheets safe reader
description: Historical worksheets may contain blank or duplicate headers and must be read without get_all_records validation.
---

Read malformed historical sheets with get_all_values, normalize blank headers to stable local placeholders, suffix duplicates, and preserve the source row number in each record.

**Why:** Legacy booking sheets can have empty or duplicate columns beyond the main table, causing gspread's strict get_all_records to abort unrelated operations such as financial reports.

**How to apply:** Use the safe reader for bookings and report inputs; resolve money and payment fields through explicit aliases and safe_float instead of assuming a perfect header row.