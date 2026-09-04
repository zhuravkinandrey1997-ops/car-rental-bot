---
name: Google Sheets write quota
description: Return persistence must minimize Google Sheets write requests to avoid per-minute 429 quota errors.
---

Batch header and row updates into one Sheets request instead of issuing many `update_cell` calls during one return operation.

**Why:** Google Sheets can return HTTP 429 when a single return writes each missing header or field separately; this previously made a valid acceptance fail at the final save step.

**How to apply:** Ensure worksheet capacity first, create missing headers in one header-row update, update each affected row in one range update, and keep return persistence retryable when a transient write fails.