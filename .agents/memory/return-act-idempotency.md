---
name: Return act idempotency
description: Reliability rule for saving one completed return across multiple Google Sheets.
---

Use the booking ID as the idempotency key for each return ledger. On retry, append only missing ledger rows and change the booking to completed only after every required ledger row exists.

**Why:** Google Sheets writes are not transactional. A temporary failure between writes can otherwise leave a completed booking without an acceptance act or create duplicate financial rows on retry.

**How to apply:** Keep return persistence recoverable while the booking is still in rental status, allow repair of a completed partial record, reject only when all required rows already exist, and write the completed status last.