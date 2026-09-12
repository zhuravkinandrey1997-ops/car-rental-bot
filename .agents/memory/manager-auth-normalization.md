---
name: Manager authorization normalization
description: Durable rules for reading manager access and routing roles.
---

Normalize every manager row before authorization: trim the Telegram ID, trim and lowercase the role, and trim/lowercase the active flag before comparing it with да, yes, or true. The destructive cancellation flows are exclusive to the normalized `администратор плюс` role.

**Why:** Google Sheets values may contain inconsistent casing and surrounding spaces, and destructive booking/expense cancellation must not be reachable through stale buttons or forged callbacks by ordinary managers.

**How to apply:** Require a matching active row for access, route administrator, Admin Plus, and partner roles explicitly, and route any other active role to the standard manager menu. Gate both the menu buttons and every message/callback step of cancellation flows with the normalized Admin Plus role. A missing row must receive a clear ID-specific denial.