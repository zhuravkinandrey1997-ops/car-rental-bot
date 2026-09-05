---
name: Heavy Lux partner boundary
description: Durable access and privacy rule for the restricted Heavy Lux Telegram role.
---

`partner_heavy_lux` is an operational-only role. Its visible source is `heavy_lux_cr`; booking, active-rental, issuance, and return views must contain only vehicle, client, dates, mileage, fuel, condition, and status data. Rent, deposits, penalties, deductions, and payment methods remain internal manager data.

**Why:** The partner needs to coordinate vehicle handoffs without access to the rental company's financial terms, and the old Dodge partner flow must remain compatible.

**How to apply:** Keep the role on a separate menu/FSM branch, filter partner records by the explicit source when present (with the legacy vehicle-keyword fallback), and automatically use the vehicle tariff for new bookings whenever available.