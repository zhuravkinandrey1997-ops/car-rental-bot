---
name: Rental financial separation
description: Business rule separating booking prepayment, rental settlement, and the insurance deposit.
---

Record booking prepayment and its method separately from the insurance deposit collected at issuance. At return, calculate rent as daily rate times rental days, reduce rent by the prepayment, and settle that balance independently. Offset only mileage, lateness, wash, and additional penalties against the insurance deposit.

**Why:** Booking prepayment is part of rental revenue, while the insurance deposit is held security money. Mixing them distorts both the client balance and cash reporting.

**How to apply:** Keep separate amounts and payment methods for prepayment, rental settlement, insurance deposit, and penalty top-ups. Show distinct rental and insurance blocks in the return act and retain legacy fields only as read/write compatibility aliases.