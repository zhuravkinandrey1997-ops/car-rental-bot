---
name: Rental return timing
description: Default planned return time used when a booking has an end date but no explicit end time.
---

Date-only rental bookings treat 23:59 on the booking end date as the planned return time.

**Why:** The existing booking flow collects dates rather than planned clock times, while late fees still need a deterministic comparison point.

**How to apply:** If a future change adds explicit planned return times, prefer those values and retain 23:59 only as the fallback for legacy date-only bookings.