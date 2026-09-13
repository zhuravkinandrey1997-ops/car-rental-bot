---
name: Runtime Sheets recovery
description: Reliability rules for recovering Google Sheets operations after transient runtime failures.
---

Runtime Google Sheets retries must be bounded and classify failures without exposing raw provider errors. Reads and absolute cell/range updates are safe to repeat; appends and deletes require an idempotency check after an ambiguous response before another attempt.

**Why:** A successful Sheets mutation can lose its response during a network or quota incident. Blindly repeating an append creates duplicate bookings, expenses, or return records; blindly repeating a row deletion can remove the next record after row shifting.

**How to apply:** Preserve a stable key for every user-facing append, re-read after a transient write failure, and keep the current user flow/state available when the bounded retry budget is exhausted so the action can be retried without rebuilding it.