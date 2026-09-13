---
name: Google Sheets startup
description: Bot startup should share one worksheet metadata snapshot and recover transient quota failures in-process.
---

Resolve startup worksheets from one `Spreadsheet.worksheets()` snapshot and keep the whole bootstrap behind one bounded transient-error retry. Log readiness only after that bootstrap succeeds.

**Why:** gspread's per-title `worksheet()` lookup fetches spreadsheet metadata again; repeated startup lookups can exhaust the read quota and restart both bots before polling begins.

**How to apply:** Keep worksheet references cached for initialization, retry 429/temporary Google API failures inside the process, and emit the ready message from the configured application logger after Sheets initialization has completed.

Startup failures must be represented by a safe diagnostic status with a
reason category and attempt count; the bot must abort before creating
Telegram polling tasks.

**Why:** Raw Google API exceptions can expose implementation details or
credentials, while polling against an unready spreadsheet creates misleading
runtime failures.

**How to apply:** Classify quota, network, worksheet-structure, and
configuration failures without logging the original exception text; expose a
safe hint and keep the failure observable from the workflow logs.