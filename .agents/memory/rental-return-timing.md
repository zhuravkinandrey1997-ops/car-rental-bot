---
name: Rental return timing
description: Default planned return time used when a booking has an end date but no explicit end time.
---

Issuances store an exact planned return date/time derived from the actual issue time;
date-only legacy bookings treat 23:59 on the booking end date as the fallback.

**Why:** Late-fee calculations need an exact comparison point, while older rows do
not contain an issue time and must remain safe to process.

**How to apply:** Prefer explicit planned return values and the actual issue
date/time when calculating periods; retain 23:59 only for legacy date-only rows.