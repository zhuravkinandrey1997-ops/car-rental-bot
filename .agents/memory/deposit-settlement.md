---
name: Deposit settlement
description: Business rule for recording an issuance deposit and settling return deductions against it.
---

Record the actual deposited amount and its payment method at vehicle issuance. At return, subtract mileage, lateness, wash, and additional penalties from that recorded amount; request and record an additional payment only when the result is negative.

**Why:** The deposit is held money, not a new return-time payment. Deductions must be offset against it before the manager asks the client for more money or returns the remainder.

**How to apply:** Preserve the original deposit audit fields, show the full-refund, partial-refund, fully-retained, or additional-payment result in the return act, and keep legacy deposit fields as read fallbacks.