---
name: Partner car filtering
description: Safe identity matching for partner-scoped vehicle bookings and expenses.
---

Filter partner expenses by the human-facing make/model and registration plate. Do not use numeric car IDs as substring identifiers inside expense vehicle text.

**Why:** A numeric ID can appear as an unrelated digit sequence in another vehicle's registration plate, exposing records from the wrong car.

**How to apply:** Prefer exact or clearly scoped plate/name matches, while retaining the partner make/model keyword as the fallback for legacy rows that store the vehicle name directly.