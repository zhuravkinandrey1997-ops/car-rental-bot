---
name: Google Sheets startup throttling
description: Startup behavior when repeated workflow launches exhaust the Sheets API read quota.
---

Repeated workflow restarts can exhaust the Google Sheets per-minute read quota before either bot reaches its normal ready log. The failure is an external API throttle, not evidence of a Python import or callback error.

**Why:** gspread metadata and worksheet reads are performed during startup, so rapid restarts can consume the same read budget even when the code is unchanged.

**How to apply:** After a quota 429, let the workflow remain running or wait for the quota window to recover before retrying. Prefer reducing startup reads over repeated restart attempts.