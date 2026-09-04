---
name: Google Sheets grid capacity
description: Dynamic header writes must expand the worksheet grid before using computed column indexes.
---

Dynamic or legacy-compatible headers can exceed the current Google Sheets grid
even when the sheet already has the minimum operational width.

**Why:** gspread raises a 400 grid-limit error before the header or data write
when a computed column such as AJ is outside the worksheet's current columns.

**How to apply:** Before appending headers or updating cells by header index,
compare the required column count with `col_count` and expand with `add_cols`
in a small fixed step or by the exact deficit. Treat alternate header names as
aliases so existing sheets do not accumulate duplicate semantic columns.