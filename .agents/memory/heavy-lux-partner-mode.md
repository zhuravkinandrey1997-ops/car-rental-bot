---
name: Heavy Lux partner boundary
description: Durable access and privacy rule for the restricted Heavy Lux Telegram role.
---

`partner_heavy_lux` is an operational role with one isolated financial summary. Operational views remain limited to `heavy_lux_cr` vehicle, client, dates, mileage, fuel, condition, and status data; the financial menu may expose only monthly Heavy Lux gross collections, booking counts, and the partner's 30% commission. Rent, deposits, penalties, deductions, direct expenses, company profit, and other sources remain internal manager data.

**Why:** The partner needs monthly visibility into its own commission while still being unable to inspect Leon Rental expenses, profit, payment details, or unrelated deals; the old Dodge partner flow must remain compatible.

**How to apply:** Keep the role on a separate menu/FSM branch, route its report directly to the `heavy_lux_cr` source filter after month selection, format only partner-safe fields, filter operational records by the explicit source when present (with the legacy vehicle-keyword fallback), and automatically use the vehicle tariff for new bookings whenever available.