---
name: Manager-approved rental price
description: Return-flow rule for confirming tariff price, applying discounts, and accounting for issuance payments.
---

At vehicle return, calculate the tariff total first, then require the manager to keep it or enter the agreed discounted total. Calculate the remaining rent from the approved total minus the booking prepayment and any amount paid at issuance.

**Why:** The customer may receive a negotiated discount or pay the balance at handover; using only the tariff total creates a false outstanding balance and overwrites the agreed booking amount.

**How to apply:** Keep the calculated tariff total available for comparison, persist the approved total as the booking's final rental amount, and display whether the act uses the discounted total. Keep issuance payment separate from return-time payment.