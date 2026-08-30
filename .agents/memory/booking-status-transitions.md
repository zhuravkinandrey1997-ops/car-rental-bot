---
name: Booking status transitions
description: Domain rule separating reservation cancellation from issued-vehicle returns.
---

Only a booking with status `Подтверждена` may be cancelled. A booking with
status `В аренде` must remain in the vehicle-return workflow and cannot be
changed directly to `Отменена`.

**Why:** Cancelling an issued rental would release occupied dates and remove the
vehicle from the return list while it is still physically with the client.

**How to apply:** Keep cancellation selectors and the final persisted-status
check limited to confirmed reservations. Keep the return selector limited to
issued rentals.