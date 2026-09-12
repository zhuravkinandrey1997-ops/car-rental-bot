---
name: Booking status transitions
description: Domain rule separating reservation cancellation from issued-vehicle returns.
---

Only a booking with status `Подтверждена` may be cancelled. Cancellation
selectors must be built from confirmed booking rows, not from the current
status of the related car. A booking with status `В аренде` must remain in the
vehicle-return workflow and cannot be changed directly to `Отменена`.
An automobile remains occupied while any booking for it is `В аренде`,
regardless of the planned return date; only a completed return act releases it.

**Why:** Cancelling an issued rental would release occupied dates and remove the
vehicle from the return list while it is still physically with the client.
Planned dates are not proof that the client returned the vehicle, so relying on
them can incorrectly expose an occupied car as available.

**How to apply:** Build the cancellation car list from unique car identifiers
in confirmed booking rows, then load that car's confirmed bookings. Keep the
final persisted-status check limited to confirmed reservations. Keep the
return selector limited to issued rentals. For availability and issuance,
check open booking status first; after issuance set the car status to `В
аренде`, and after return set it to `Свободен`.