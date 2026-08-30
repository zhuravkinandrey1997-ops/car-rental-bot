# Telegram bot for car rental management

This project contains a manager-only Telegram bot built with aiogram 3. It reads and writes the `Cars`, `Bookings`, `Returns_Cash`, and `Managers` worksheets through `gspread`.

## Included workflows

- Check available cars for a date range
- Create a confirmed booking
- View active rentals
- Register an actual vehicle return, cash payment, extra fees, and notes
- Mark the booking as `Завершена` and update the car mileage after a return
- Send every active manager a daily 20:00 summary of tomorrow's vehicle
  issuances and returns

## Setup

1. Create a Google service account and either:
   - download its JSON key into the project as `service_account.json`, or
   - save the complete JSON as the protected `GOOGLE_SERVICE_ACCOUNT_JSON` secret.
2. Share the Google spreadsheet with the service account email.
3. Set the spreadsheet name in `SPREADSHEET_NAME` if it differs from the default. Copy `.env.example` to `.env` for local settings.
4. Add each manager to the `Managers` worksheet with a `Telegram ID` column and set `Доступ активен` to `да`, `yes`, or `true`.
5. Keep `TELEGRAM_BOT_TOKEN` in Replit Secrets. It is intentionally not placed in `.env`.
6. Set `RENTAL_TIMEZONE` to the rental company's IANA timezone if it differs
   from the default `Europe/Moscow`.
7. Start the bot with:

   ```bash
   uv run python main.py
   ```

## Expected sheet structure

The bot expects the headers used by the supplied Google Sheets client. When a new
field is first saved, the bot adds any missing headers to the relevant sheet.
Booking rows include the following data:

`ID Брони`, `ID Авто`, `Автомобиль`, `Дата начала (ГГГГ-ММ-ДД)`,
`Дата окончания (ГГГГ-ММ-ДД)`, `ФИО Клиента`, `Телефон`, `Сумма брони (RUB)`,
`Telegram ID Менеджера`, `Статус`, `Дата и время выдачи`,
`Пробег при выдаче (км)`, `Принятая сумма аренды (RUB)`, `Залог (RUB)`,
`Способ оплаты`, `Залог/Предоплата`, `Тип оплаты залога`,
`Плановое окончание`, `Причина отмены`, `Статус залога при отмене`.

The car sheet should include `ID`, `Статус`, a vehicle name, a plate column such as
`Госномер`, and a mileage column such as `Пробег`. `Returns_Cash` receives the
return timestamp, mileage limit and overage, late fees, cleanliness, wash fee,
additional deductions, deposit settlement, payment method, and notes. The bot
updates the booking status to `Завершена` and the car mileage after confirmation.
Cancelling an active booking changes its status to `Отменена`, records the
cancellation reason and deposit outcome, and releases its dates for availability
checks.

## Daily notifications

The bot runs an APScheduler cron job every day at `20:00` in
`RENTAL_TIMEZONE` (default: `Europe/Moscow`). It scans `Bookings` for tomorrow's
confirmed issuances and in-progress rental returns, formats one summary, and
sends it to every `Managers` row where `Доступ активен` is `да`, `yes`, or
`true`. A no-events message is sent when tomorrow has no issuances or returns.
After a successful send, the bot stores the target date in the automatically
created `Последний план на дату` column. This prevents normal restarts from
duplicating the same evening summary and lets a bot restarted after 20:00 send
the missed plan. Failed recipients are retried during the evening, and summaries
larger than Telegram's single-message limit are split into safe chunks.