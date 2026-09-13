---
name: Worksheet conflict recovery
description: Safe operator recovery when a legacy and canonical Google Sheets title coexist.
---

When a legacy/current worksheet pair conflicts, the operator must choose which
worksheet's data is authoritative and explicitly confirm the operation. Preserve
both worksheets by renaming the non-authoritative one to a unique archive title;
never delete, merge, or overwrite worksheet data automatically.

**Why:** The bootstrap cannot infer which duplicate contains the correct rental
history, and choosing silently could cause irreversible data loss.

**How to apply:** Re-read the current worksheet metadata immediately before
renaming, validate that the selected pair still exists, then leave exactly one
canonical title so the next bootstrap can validate the structure.