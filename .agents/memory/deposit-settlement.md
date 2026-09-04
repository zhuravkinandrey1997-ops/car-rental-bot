---
name: Rental financial separation
description: Business rule separating booking prepayment, rental settlement, and the insurance deposit.
---

Record booking prepayment and its method separately from the insurance deposit collected at issuance. At return, calculate rent as daily rate times rental days, reduce rent by the prepayment, and settle that balance independently. Offset wash and additional penalties against the insurance deposit; treat lateness and over-mileage as observations until a manager explicitly chooses no penalty or enters a manual amount.

**Why:** Booking prepayment is part of rental revenue, while the insurance deposit is held security money. Automatic time/mileage charges can create unauthorized deductions when a rental extension was agreed verbally.

**How to apply:** Keep separate amounts and payment methods for prepayment, rental settlement, insurance deposit, and penalty top-ups. Show the detected time/mileage variance separately from the manager-approved manual penalty, and retain legacy fields only as read/write compatibility aliases.