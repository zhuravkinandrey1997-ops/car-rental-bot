---
name: Telegram ID formatting
description: Compatibility rule for Telegram IDs read from Google Sheets.
---

Treat a Telegram ID from Sheets as a normalized string: trim it, remove spaces, and remove a trailing Sheets-style `.0` representation before strict comparison with the incoming Telegram ID.

**Why:** Google Sheets can expose numeric IDs with formatting artifacts such as embedded spaces or a decimal suffix, which otherwise causes valid users to be silently rejected.

**How to apply:** Use the normalized value for authentication comparisons and any manager-recipient ID conversion.