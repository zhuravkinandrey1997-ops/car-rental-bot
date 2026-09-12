---
name: Financial report source filters
description: Source-scoped financial reports and their relationship to the canonical monthly summaries.
---

Source-scoped financial reports must filter bookings by their normalized source, include only direct expenses attached to cars in those source bookings, and exclude general company expenses. Only the combined report may persist the monthly and per-car summary rows.

**Why:** A filtered view is an analysis of the canonical report, not a replacement for it; persisting a Leon or Heavy Lux view would overwrite the all-company financial history.

**How to apply:** Keep the combined report as the default persisted calculation. Treat numeric car IDs as exact identifiers when matching expense text; use plates and make/model text for human-facing matches.