---
name: Rental return timing
description: Default planned return time used when a booking has an end date but no explicit end time.
---

Issuances store an exact planned return date/time derived from the actual issue time;
date-only legacy bookings treat 23:59 on the booking end date as the fallback.
Paid rental days use the booked period with a 120-minute grace period; actual
late return time stays separate from the rental-day count.

**Why:** Late-fee calculations need an exact comparison point, while older rows do
not contain an issue time and must remain safe to process. A late return should
not silently convert a booked day into another full rental day.

**How to apply:** Prefer explicit planned return values and the actual issue
date/time when calculating periods; retain 23:59 only for legacy date-only rows.
Apply the grace period to booked-day calculation and require a separate manager
decision for any late-return удержание.