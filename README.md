# Telegram bot for car rental management

This project contains a manager-only Telegram bot built with aiogram 3. It reads and writes the `Cars`, `Bookings`, `Returns_Cash`, and `Managers` worksheets through `gspread`.

## Included workflows

- Check available cars for a date range
- Create a confirmed booking
- View active rentals
- Register an actual vehicle return, cash payment, extra fees, and notes
- Mark the booking as `Завершена` and update the car mileage after a return

## Setup

1. Create a Google service account and download its JSON key into the project as `service_account.json`.
2. Share the Google spreadsheet with the service account email.
3. Set the spreadsheet name in `SPREADSHEET_NAME` if it differs from the default. Copy `.env.example` to `.env` for local settings.
4. Add each manager to the `Managers` worksheet with a `Telegram ID` column and set `Доступ активен` to `да`, `yes`, or `true`.
5. Keep `TELEGRAM_BOT_TOKEN` in Replit Secrets. It is intentionally not placed in `.env`.
6. Start the bot with:

   ```bash
   uv run python main.py
   ```

## Expected sheet structure

The bot expects the headers used by the supplied Google Sheets client. The booking rows are written in this order:

`ID`, `ID Авто`, `Авто`, `Дата начала (ГГГГ-ММ-ДД)`, `Дата окончания (ГГГГ-ММ-ДД)`, `Клиент`, `Телефон`, `Цена`, `Telegram ID менеджера`, `Статус`

The car sheet should include `ID`, `Статус`, a vehicle name, a plate column such as `Госномер`, and a mileage column such as `Пробег`. The returns sheet receives the 15 values written by `save_actual_return`.