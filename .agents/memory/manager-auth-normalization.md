---
name: Manager authorization normalization
description: Durable rules for reading manager access and routing roles.
---

Normalize every manager row before authorization: trim the Telegram ID, trim and lowercase the role, and trim/lowercase the active flag before comparing it with да, yes, or true.

**Why:** Google Sheets values may contain inconsistent casing and surrounding spaces, and rejecting an active user with an unrecognized role is worse than safely giving the standard manager menu.

**How to apply:** Require a matching active row for access, route administrator and partner roles explicitly, and route any other active role to the standard manager menu. A missing row must receive a clear ID-specific denial.