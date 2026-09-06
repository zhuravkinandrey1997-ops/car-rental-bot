---
name: Financial report revenue
description: Revenue basis for monthly company and per-car profitability reports.
---

Use the actual received income recorded for each return as report revenue. Do not treat the full accrued rental cost as received revenue when the client paid only part of the balance.

**Why:** Partial and zero rental payments are valid return outcomes. Using the billed amount would overstate company profit and Ksenia's 10% salary.

**How to apply:** Prefer the canonical return-income total, then fall back to actual rental payment plus received penalty top-up for legacy rows. Use full rental cost only when no actual-payment fields exist.

For bookings whose source is exactly `heavy_lux_cr`, keep the client payment
at 100% in payment columns but count only 70% as company revenue; track the
remaining 30% as partner commission. `Леон Рентал` has no commission.

**Why:** Heavy Lux payments are collected by the operation but only the
company's 70% share belongs in net income and per-car profitability.

**How to apply:** Apply the source rate after extracting actual received
payment amounts, before aggregating payment methods, monthly profit, or
`Прибыль_по_авто`; never rewrite the client's payment fields.