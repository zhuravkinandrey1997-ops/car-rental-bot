# Car Rental Telegram Bot

A manager-only Telegram bot for car rental bookings, active rentals, and vehicle returns stored in Google Sheets.

## Run & Operate

- `uv run python main.py` — run the Telegram bot
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required secret: `TELEGRAM_BOT_TOKEN`
- Required local credential file: `service_account.json`
- Optional env: `SPREADSHEET_NAME`, `SERVICE_ACCOUNT_FILE`, `LOG_LEVEL`

## Stack

- Python 3.11
- Telegram: aiogram 3
- Google Sheets: gspread
- Configuration: python-dotenv

## Where things live

- `main.py` — bot handlers, menus, and aiogram FSM flows
- `sheets_client.py` — Google Sheets client and the supplied fleet operations
- `.env.example` — non-secret configuration template
- `README.md` — setup and worksheet expectations

## Architecture decisions

- Manager access is checked before every menu operation using the `Managers` worksheet.
- Booking and return forms are conversational FSM flows so managers can complete them from Telegram.
- Telegram credentials stay in Replit Secrets; Google credentials stay in the ignored service-account JSON file.

## Product

- Managers can check availability, create bookings, review active rentals, and register returns with mileage and payment details.

## User preferences

- Use aiogram 3, gspread, and python-dotenv as requested.

## Gotchas

- The bot cannot start Google Sheets operations until `service_account.json` is uploaded and shared access is granted to the spreadsheet.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
