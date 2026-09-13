---
name: Hidden worksheet visibility
description: The Sheets bootstrap and read-only diagnosis must treat hidden worksheets as present.
---

Use gspread's default `Spreadsheet.worksheets()` behavior when checking required or optional sheet titles; do not pass `exclude_hidden=True`.

**Why:** gspread 6.2.1 includes hidden worksheets by default, while excluding them would make a hidden required sheet appear missing and could recreate hidden optional sheets.

**How to apply:** Keep bootstrap and diagnostics on the same default metadata path, and use metadata fixtures with `hidden: true` when testing visibility-sensitive behavior.