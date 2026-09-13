---
name: Tiered rental pricing
description: The client booking flow's rental-day convention and tariff-column selection.
---

Client rental days are the difference between return and start dates, with a minimum of one day; the end date is not added as an extra day. The selected daily rate comes from the duration tier in the `Автомобили` sheet, and savings compare that total with the one-day rate for the same duration.

**Why:** The business tariff grid defines prices by rental duration, and same-day bookings must still charge one day without turning a two-night interval into three billable days.

**How to apply:** Preserve this convention in client booking calculations, customer summaries, and any future price previews; keep the five tariff columns immediately after the base daily-price column.