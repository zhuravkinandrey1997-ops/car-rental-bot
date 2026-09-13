# Telegram bot for car rental management

This project contains a manager-only Telegram bot built with aiogram 3. It reads
and writes the rental, return, expense, and financial-summary worksheets through
`gspread`.

## Included workflows

- Check available cars for a date range
- Create a confirmed booking
- View active rentals
- Register an actual vehicle return, cash payment, extra fees, and notes
- Mark the booking as `Завершена` and update the car mileage after a return
- Send every active manager a daily 20:00 summary of tomorrow's vehicle
  issuances and returns
- Record categorized expenses linked to a specific vehicle or to the whole fleet
- Cancel recent expenses after an explicit confirmation; the matching row is
  deleted from `Затраты`
- Build company-wide or selected-vehicle monthly financial reports

## Setup

1. Create a Google service account and either:
   - download its JSON key into the project as `service_account.json`, or
   - save the complete JSON as the protected `GOOGLE_SERVICE_ACCOUNT_JSON` secret.
2. Share the Google spreadsheet with the service account email.
3. Set the spreadsheet name in `SPREADSHEET_NAME` if it differs from the default. Copy `.env.example` to `.env` for local settings.
4. Add each manager to the `Менеджеры` worksheet with a `Telegram ID` column and set `Доступ активен` to `да`, `yes`, or `true`.
5. Keep `TELEGRAM_BOT_TOKEN` in Replit Secrets. It is intentionally not placed in `.env`.
6. Set `RENTAL_TIMEZONE` to the rental company's IANA timezone if it differs
   from the default `Europe/Moscow`.
7. Start the bot with:

   ```bash
   uv run python main.py
   ```

If startup stops because both a legacy and a current worksheet title exist,
inspect the pairs without changing the table:

```bash
uv run python main.py --sheets-conflicts
```

To recover interactively, choose which worksheet's data to preserve and confirm
the displayed plan explicitly:

```bash
uv run python main.py --recover-sheets-conflicts
```

The other worksheet is renamed to a unique archive title. No worksheet is
deleted, merged, or overwritten. Run the normal bot command again after the
recovery.

## Expected sheet structure

The bot expects the headers used by the supplied Google Sheets client. When a new
field is first saved, the bot adds any missing headers to the relevant sheet.
Booking rows include the following data:

`ID Брони`, `ID Авто`, `Автомобиль`, `Дата начала (ГГГГ-ММ-ДД)`,
`Дата окончания (ГГГГ-ММ-ДД)`, `ФИО Клиента`, `Телефон`, `Сумма брони (RUB)`,
`Telegram ID Менеджера`, `Статус`, `Дата и время выдачи`,
`Дата выдачи`, `Время выдачи`, `Время возврата`,
`Пробег при выдаче (км)`, `Задаток/Предоплата`, `Способ оплаты задатка`,
`Страховой залог`, `Способ оплаты страхового залога`,
`Плановое окончание`, `Причина отмены`, `Статус задатка при отмене`.
При возврате менеджер подтверждает расчётную стоимость аренды или вводит
согласованную стоимость со скидкой; итоговая сумма сохраняется в
`Сумма брони (RUB)`. Если в брони указано `Оплачено при выдаче (RUB)`, эта
сумма также вычитается вместе с предоплатой при расчёте остатка.

The `Автомобили` sheet should include `ID`, `Статус`, a vehicle name, a plate column such as
`Госномер`, and a mileage column such as `Пробег`. `Возвраты_Касса` receives the
return timestamp, mileage limit and overage, late fees, cleanliness, wash fee,
additional deductions, deposit settlement, payment method, and notes. The bot
updates the booking status to `Завершена` and the car mileage after confirmation.
At booking, the bot records the reservation prepayment separately as
`Задаток/Предоплата`. Issuance selects that confirmed booking, changes its status
to `В аренде`, uses the base insurance deposit from `Автомобили` as a hint, and stores
the actual amount in `Страховой залог`. At return, rental cost is calculated as
the daily rate multiplied by rental days and can be replaced by a manager-approved
discounted total. The rental balance is reduced by the prepayment and any amount
paid at issuance. Mileage,
lateness, washing, and additional penalties are offset only against the insurance
deposit; an extra damage-payment step is shown only when those penalties exceed
the deposit. The return records the actual rental payment and method separately
from any additional payment for damage or penalties, and rejects a repeated
return if the booking is no longer in `В аренде`.
Each completed return also creates one row in `Acceptance_Acts`. If the sheet
does not exist, the bot creates it automatically with the act, booking, vehicle,
client, mileage, penalty, deposit, payment, and note columns.
The bot also creates `Затраты`, `Сводная_по_месяцам`, and `Прибыль_по_авто`
with their required headers when they are missing. Expense entry, financial
reports, and expense history are visible only to the three configured
finance-manager Telegram IDs.
After the expense amount and category are selected, the manager must assign the
expense either to one car from `Автомобили` or to `🏢 Общие расходы (весь автопарк)`.
The stored value is the vehicle plate or `Общие`.

Before building a report, the bot offers the current month, previous month, and
all additional months found in `Возвраты_Касса` or `Затраты`. It then offers a
company-wide report or an inline multi-select list of vehicles. The company
report separates vehicle expenses from general company expenses, adds Ksenia's
salary at 10% of actual received income, and shows organization profit plus
profit for every car. The selected-vehicle report shows revenue, categorized
vehicle expenses, monthly mileage and profit for each checked car, followed by
group totals. General expenses and Ksenia's salary are company-level values and
are not allocated to a selected vehicle group.

Report revenue uses actual received income from `Итого приход (RUB)` in
`Возвраты_Касса`, falling back to the actual rental payment plus received penalty
top-up for legacy rows. It does not replace a recorded zero payment with the
accrued rental price. Each calculated report updates the summary worksheets.
The `📑 Просмотр расходов` menu opens a month-based history of `Затраты`.
Authorized managers can view all records or filter them by a vehicle or
`Общие расходы`; each result includes the record details and the period total.
When the selection is not empty, the bot also offers a UTF-8 CSV download for
accounting with the period, selected filter, record ID, date, amount, category,
vehicle/general binding, author, note, and an `ИТОГО` row. Stale month
selections and empty filtered results are reported without creating a file.
Cancelling an active booking changes its status to `Отменена`, records the
cancellation reason and deposit outcome, and releases its dates for availability
checks.

## Daily notifications

The bot runs an APScheduler cron job every day at `20:00` in
`RENTAL_TIMEZONE` (default: `Europe/Moscow`). It scans `Бронирования` for tomorrow's
confirmed issuances and in-progress rental returns, formats one summary, and
sends it to every `Менеджеры` row where `Доступ активен` is `да`, `yes`, or
`true`. A no-events message is sent when tomorrow has no issuances or returns.
After a successful send, the bot stores the target date in the automatically
created `Последний план на дату` column. This prevents normal restarts from
duplicating the same evening summary and lets a bot restarted after 20:00 send
the missed plan. Failed recipients are retried during the evening, and summaries
larger than Telegram's single-message limit are split into safe chunks.