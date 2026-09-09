---
name: Evening notification delivery
description: Reliability policy for scheduled Telegram summaries backed by Google Sheets.
---

Run the evening scheduler in the single bot workflow. Delivery is at-least-once:
persist the target date per recipient only after every Telegram message chunk is
accepted, and retry still-pending recipients during the same evening.

**Why:** Telegram delivery and a Google Sheets marker cannot be committed
atomically. Marking before send risks a missed message; marking after send
prioritizes delivery but can very rarely duplicate after a crash.

**How to apply:** Keep one scheduler leader, deduplicate manager IDs, perform
blocking Sheets calls off the asyncio event loop, split long Telegram messages,
and retain restart catch-up plus evening retries.

The same scheduler lock and at-least-once principle apply to one-hour booking
start reminders: use an in-memory booking-ID set, mark it only after the
manager/partner message with its action button is accepted, and require an
explicit start time rather than guessing from a date-only booking.

**Why:** A date-only booking has no reliable one-hour trigger, while sending
before recording successful delivery can lose the operational reminder.

**How to apply:** Read confirmed bookings through the existing scheduler,
resolve the creator's role before routing Heavy Lux reminders to the partner,
and leave failed deliveries eligible for a retry while the 50–70 minute window
is open.

Completed one-hour expiry alerts use a dedicated persistent state with a bounded
retention period, and the state is written only after every recipient accepts the
message. If the state cannot be read, do not send until a later scheduler run.

**Why:** Process memory disappears on restart, while sending without a readable
marker can duplicate an alert. Writing after delivery preserves retries for
partial failures; the retention window prevents the state sheet from growing
forever.

**How to apply:** Keep the persisted alert key stable per booking, clean expired
rows during state access, and perform the blocking storage operations off the
asyncio event loop.