---
name: Keep-alive port ownership
description: Port ownership constraint for the Telegram bot health-check server.
---

The Telegram bot keep-alive server must be the sole process listening on port 8080 in this workspace.

**Why:** The separate API artifact workflow also binds port 8080. Running both causes the bot health server to fail during startup or makes health requests reach the wrong service.

**How to apply:** Before restarting the Telegram workflow, keep the separate API workflow stopped. Verify `GET /` returns the Leon Rental health text after the bot starts.