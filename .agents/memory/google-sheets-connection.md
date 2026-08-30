---
name: Google Sheets identifier
description: The bot supports both a spreadsheet title and a Google Sheets file ID.
---

When `SPREADSHEET_NAME` contains the long Google Sheets file identifier, open the spreadsheet by key; `gspread.open()` only searches titles.

**Why:** A valid service-account credential and a valid file ID still produce `SpreadsheetNotFound` when a file ID is passed to the title-based method.

**How to apply:** Preserve title-based opening for the default title, but detect explicit IDs and use `open_by_key()`.