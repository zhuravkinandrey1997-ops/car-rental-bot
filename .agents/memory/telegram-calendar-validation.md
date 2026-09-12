---
name: Telegram calendar validation
description: Server-side validation rules for inline date calendars used by rental FSM flows.
---

Inline calendar buttons are only a user-interface constraint; callback handlers must validate the selected date against the same minimum and maximum bounds before changing FSM state or saving rental dates.

**Why:** Telegram callbacks can be stale or manually crafted, and a disabled-looking calendar cell is not a security or data-integrity boundary.

**How to apply:** Recompute the date bounds from current FSM data in the callback, reject invalid dates, and keep the existing text handler from bypassing the calendar flow.