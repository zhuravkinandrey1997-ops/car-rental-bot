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