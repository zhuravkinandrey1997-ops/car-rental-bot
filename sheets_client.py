import os
import datetime
import json
import logging
import socket
import threading
import time
import traceback
import uuid
import gspread
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("car-rental-sheets")

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "leon_rental_fleet_rub_v4")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_TITLE_MAP = {
    "Cars": "Автомобили",
    "Bookings": "Бронирования",
    "Returns_Cash": "Возвраты_Касса",
    "Expenses": "Затраты",
    "Monthly_Summary": "Сводная_по_месяцам",
    "Car_Profit_Summary": "Прибыль_по_авто",
    "Managers": "Менеджеры",
}
BOOKING_HEADERS = [
    "ID Брони",
    "ID Авто",
    "Автомобиль",
    "Дата начала",
    "Дата окончания",
    "ФИО Клиента",
    "Телефон",
    "Сумма брони (RUB)",
    "Задаток (RUB)",
    "Способ оплаты задатка",
    "ID Менеджера",
    "Статус",
    "Адрес доставки",
    "Стоимость доставки (RUB)",
]
ACCEPTANCE_ACT_HEADERS = [
    "ID Акта",
    "ID Брони",
    "Дата и время создания акта",
    "Менеджер",
    "Автомобиль (Марка, Модель, Госномер)",
    "Клиент (Имя, Телефон)",
    "Дата и время выдачи",
    "Дата и время приёма (факт)",
    "Начальный пробег",
    "Конечный пробег",
    "Пройденный пробег (км)",
    "Лимит пробега (км)",
    "Перепробег (км)",
    "Штраф за перепробег (RUB)",
    "Просрочка по времени (часов)",
    "Штраф за просрочку (RUB)",
    "Состояние чистоты (Чистая/Грязная)",
    "Стоимость мойки (RUB)",
    "Дополнительные штрафы (RUB)",
    "Полная стоимость аренды (RUB)",
    "Задаток / Предоплата (RUB)",
    "Внесённый страховой залог (RUB)",
    "Сумма удержаний из залога (RUB)",
    "Итоговый статус залога (Сумма возврата или Сумма доплаты)",
    "Фактически принятая доплата (RUB)",
    "Способ доплаты",
    "Примечание",
]
EXPENSE_HEADERS = [
    "ID Записи",
    "Дата",
    "Сумма (RUB)",
    "Категория / На что",
    "Автомобиль (Госномер / Общие)",
    "Telegram ID",
    "Имя",
    "Примечание",
]
MONTHLY_SUMMARY_HEADERS = [
    "Месяц (ГГГГ-ММ)",
    "Общий приход (RUB)",
    "Прямые расходы (RUB)",
    "Общие расходы компании (RUB)",
    "ЗП Ксении (10%) (RUB)",
    "Итого расходы (RUB)",
    "Чистая прибыль компании (RUB)",
]
CAR_PROFIT_SUMMARY_HEADERS = [
    "Месяц (ГГГГ-ММ)",
    "ID Авто",
    "Марка и Модель",
    "Госномер",
    "Приход / Выручка (RUB)",
    "Затраты на авто (RUB)",
    "Чистая прибыль авто (RUB)",
]
EXPENSES_LOCK = threading.Lock()
BOOKINGS_LOCK = threading.Lock()
MIN_OPERATIONAL_COLUMNS = 35
TARGET_OPERATIONAL_COLUMNS = 35


def _is_transient_spreadsheet_error(error: Exception) -> bool:
    """Return whether a Google Sheets open failure is safe to retry."""
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(response, dict):
        status_code = response.get("status_code", response.get("status"))
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        status_code = None
    if status_code in {500, 502, 503}:
        return True
    if isinstance(error, (TimeoutError, ConnectionError, socket.timeout)):
        return True

    error_name = type(error).__name__.casefold()
    error_text = str(error).casefold()
    return (
        "timeout" in error_name
        or "connection" in error_name
        or "timed out" in error_text
        or "temporarily unavailable" in error_text
        or "service unavailable" in error_text
        or any(code in error_text for code in ("500", "502", "503"))
    )


def get_spreadsheet_with_retry(
    gc_client,
    spreadsheet_key: str,
    max_retries: int = 5,
):
    """Open a spreadsheet while tolerating short Google API outages."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    for attempt in range(max_retries):
        try:
            return gc_client.open_by_key(spreadsheet_key)
        except Exception as error:
            is_last_attempt = attempt == max_retries - 1
            if is_last_attempt or not _is_transient_spreadsheet_error(error):
                raise
            retry_number = attempt + 1
            print(
                "Временный сбой Google API "
                f"({error}), повторная попытка "
                f"{retry_number}/{max_retries} через 3 сек..."
            )
            time.sleep(3)


if SERVICE_ACCOUNT_JSON:
    gc = gspread.service_account_from_dict(json.loads(SERVICE_ACCOUNT_JSON))
else:
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)

# Google Sheets IDs are not spreadsheet titles. Keep support for both so the
# default name and an explicitly supplied spreadsheet ID work as expected.
if len(SPREADSHEET_NAME) > 30 and " " not in SPREADSHEET_NAME:
    sh = get_spreadsheet_with_retry(gc, SPREADSHEET_NAME)
else:
    sh = gc.open(SPREADSHEET_NAME)

def migrate_sheet_titles(spreadsheet) -> list[tuple[str, str]]:
    """Rename legacy worksheet titles to their Russian equivalents."""
    worksheets = {
        worksheet.title: worksheet for worksheet in spreadsheet.worksheets()
    }
    renamed: list[tuple[str, str]] = []
    for old_title, new_title in SHEET_TITLE_MAP.items():
        source = worksheets.get(old_title)
        target = worksheets.get(new_title)
        if source is not None and target is not None:
            raise RuntimeError(
                f"Worksheet title conflict: both {old_title!r} and "
                f"{new_title!r} exist."
            )
        if source is None:
            continue
        source.update_title(new_title)
        worksheets.pop(old_title)
        worksheets[new_title] = source
        renamed.append((old_title, new_title))
    if renamed:
        logger.info(
            "Renamed worksheets: %s",
            ", ".join(f"{old} -> {new}" for old, new in renamed),
        )
    else:
        logger.info("Worksheet title migration is already complete")
    return renamed


migrate_sheet_titles(sh)


def _ensure_sheet_width(ws) -> None:
    """Keep operational worksheets wide enough for optional legacy fields."""
    current_columns = int(getattr(ws, "col_count", 0) or 0)
    if current_columns < MIN_OPERATIONAL_COLUMNS:
        ws.resize(
            rows=ws.row_count,
            cols=TARGET_OPERATIONAL_COLUMNS,
        )


def _ensure_all_sheet_widths(spreadsheet) -> None:
    """Expand every worksheet before any header or data update is attempted."""
    for worksheet in spreadsheet.worksheets():
        if worksheet.col_count < MIN_OPERATIONAL_COLUMNS:
            worksheet.resize(
                rows=worksheet.row_count,
                cols=MIN_OPERATIONAL_COLUMNS,
            )


_ensure_all_sheet_widths(sh)
ws_cars = sh.worksheet("Автомобили")
_ensure_sheet_width(ws_cars)


def _get_bookings_sheet():
    """Find the bookings worksheet under either supported title."""
    try:
        worksheet = sh.worksheet("Бронирования")
    except gspread.exceptions.WorksheetNotFound:
        try:
            worksheet = sh.worksheet("Bookings")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(
                title="Бронирования",
                rows=1000,
                cols=TARGET_OPERATIONAL_COLUMNS,
            )
            worksheet.append_row(
                BOOKING_HEADERS,
                value_input_option="USER_ENTERED",
            )
    _ensure_sheet_width(worksheet)
    return worksheet


ws_bookings = _get_bookings_sheet()
ws_returns = sh.worksheet("Возвраты_Касса")
ws_managers = sh.worksheet("Менеджеры")
try:
    ws_acceptance_acts = sh.worksheet("Acceptance_Acts")
except gspread.exceptions.WorksheetNotFound:
    ws_acceptance_acts = sh.add_worksheet(
        title="Acceptance_Acts",
        rows=1000,
        cols=30,
    )
acceptance_sheet_headers = ws_acceptance_acts.row_values(1)
for acceptance_header in ACCEPTANCE_ACT_HEADERS:
    if acceptance_header not in acceptance_sheet_headers:
        acceptance_sheet_headers.append(acceptance_header)
        ws_acceptance_acts.update_cell(
            1,
            len(acceptance_sheet_headers),
            acceptance_header,
        )


def is_manager_authorized(tg_id: int) -> bool:
    managers = ws_managers.get_all_records()
    for m in managers:
        if str(m.get("Telegram ID")) == str(tg_id) and str(m.get("Доступ активен")).lower() in ["да", "yes", "true"]:
            return True
    return False


def get_active_manager_ids() -> list[int]:
    manager_ids = []
    seen_ids = set()
    for manager in ws_managers.get_all_records():
        access = str(manager.get("Доступ активен", "")).strip().lower()
        if access not in {"да", "yes", "true"}:
            continue
        raw_id = str(manager.get("Telegram ID", "")).strip()
        try:
            manager_id = int(raw_id)
        except ValueError:
            continue
        if manager_id not in seen_ids:
            seen_ids.add(manager_id)
            manager_ids.append(manager_id)
    return manager_ids


def get_pending_notification_manager_ids(
    target_date: datetime.date,
) -> list[int]:
    sent_date = target_date.isoformat()
    recipients: dict[int, bool] = {}
    for manager in ws_managers.get_all_records():
        access = str(manager.get("Доступ активен", "")).strip().lower()
        if access not in {"да", "yes", "true"}:
            continue
        try:
            manager_id = int(str(manager.get("Telegram ID", "")).strip())
        except ValueError:
            continue
        already_sent = (
            str(manager.get("Последний план на дату", "")).strip() == sent_date
        )
        recipients[manager_id] = recipients.get(manager_id, False) or already_sent
    return [
        manager_id
        for manager_id, already_sent in recipients.items()
        if not already_sent
    ]


def mark_notification_sent(
    manager_id: int,
    target_date: datetime.date,
) -> None:
    records = ws_managers.get_all_records()
    headers = _ensure_headers(ws_managers, ["Последний план на дату"])
    column = headers.index("Последний план на дату") + 1
    for row_number, manager in enumerate(records, start=2):
        if str(manager.get("Telegram ID", "")).strip() == str(manager_id):
            ws_managers.update_cell(
                row_number,
                column,
                target_date.isoformat(),
            )


def _booking_date(value: object) -> datetime.date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for date_format in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%d.%m.%Y",
        "%d.%m.%Y %H:%M",
    ):
        try:
            return datetime.datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


def _first_booking_date(booking: dict, *keys: str) -> datetime.date | None:
    for key in keys:
        parsed = _booking_date(booking.get(key))
        if parsed is not None:
            return parsed
    return None


def get_tomorrow_bookings(target_date: datetime.date) -> dict[str, list[dict]]:
    events = {"issuances": [], "returns": []}
    for booking in ws_bookings.get_all_records():
        status = str(booking.get("Статус", "")).strip()
        if status == "Подтверждена" and _first_booking_date(
            booking,
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
        ) == target_date:
            events["issuances"].append(booking)
        elif status == "В аренде" and _first_booking_date(
            booking,
            "Дата окончания (ГГГГ-ММ-ДД)",
            "Дата окончания",
        ) == target_date:
            events["returns"].append(booking)
    return events


def get_available_cars(start_date_str: str, end_date_str: str):
    req_start = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    req_end = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    all_cars = ws_cars.get_all_records()
    all_bookings = ws_bookings.get_all_records()
    busy_car_ids = set()
    for b in all_bookings:
        if b.get("Статус") in ["Подтверждена", "В аренде"]:
            try:
                b_start = datetime.datetime.strptime(str(b.get("Дата начала (ГГГГ-ММ-ДД)")), "%Y-%m-%d").date()
                b_end = datetime.datetime.strptime(str(b.get("Дата окончания (ГГГГ-ММ-ДД)")), "%Y-%m-%d").date()
                if req_start <= b_end and req_end >= b_start:
                    busy_car_ids.add(b.get("ID Авто"))
            except ValueError:
                continue
    return [c for c in all_cars if c.get("ID") not in busy_car_ids and c.get("Статус") == "Активен"]


def _ensure_headers(ws, required_headers: list[str]) -> list[str]:
    _ensure_sheet_width(ws)
    headers = ws.row_values(1)
    required_columns = max(
        TARGET_OPERATIONAL_COLUMNS,
        len(headers),
        len(required_headers),
    )
    if int(getattr(ws, "col_count", 0) or 0) < required_columns:
        ws.resize(rows=ws.row_count, cols=required_columns)
    for header in required_headers:
        if header not in headers:
            headers.append(header)
            ws.update_cell(1, len(headers), header)
    return headers


def _ensure_header_aliases(
    ws,
    required_aliases: list[tuple[str, ...]],
) -> list[str]:
    """Ensure one header from each alias group exists without duplicating it."""
    _ensure_sheet_width(ws)
    headers = ws.row_values(1)
    for aliases in required_aliases:
        if any(alias in headers for alias in aliases):
            continue
        header = aliases[0]
        headers.append(header)
        ws.update_cell(1, len(headers), header)
    return headers


def _update_existing_headers(
    ws,
    row_number: int,
    fields: list[tuple[tuple[str, ...], object, bool]],
) -> None:
    """Update only columns that are already present in the worksheet."""
    headers = ws.row_values(1)
    resolved_updates: list[tuple[int, object]] = []
    missing_required: list[str] = []
    for aliases, value, required in fields:
        matching_columns = [
            column_number
            for column_number, header in enumerate(headers, start=1)
            if header in aliases
        ]
        if not matching_columns:
            if required:
                missing_required.append(aliases[0])
            continue
        resolved_updates.extend(
            (column_number, value) for column_number in matching_columns
        )
    if missing_required:
        raise ValueError(
            "Missing required booking headers: "
            + ", ".join(missing_required)
        )
    for column_number, value in resolved_updates:
        ws.update_cell(row_number, column_number, value)


def _append_record(ws, values: dict[str, object], required_headers: list[str]) -> None:
    headers = _ensure_headers(ws, required_headers)
    ws.append_row([values.get(header, "") for header in headers])


def _get_or_create_sheet(title: str, headers: list[str]):
    try:
        worksheet = sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(
            title=title,
            rows=1000,
            cols=max(30, len(headers)),
        )
    _ensure_headers(worksheet, headers)
    return worksheet


ws_expenses = _get_or_create_sheet("Затраты", EXPENSE_HEADERS)
ws_monthly_summary = _get_or_create_sheet(
    "Сводная_по_месяцам",
    MONTHLY_SUMMARY_HEADERS,
)
ws_car_profit_summary = _get_or_create_sheet(
    "Прибыль_по_авто",
    CAR_PROFIT_SUMMARY_HEADERS,
)


def safe_int(value: object) -> int:
    """Convert a Sheets number/currency value to an integer safely."""
    if not value:
        return 0
    try:
        clean_value = (
            str(value)
            .replace(" ", "")
            .replace("\xa0", "")
            .replace("₽", "")
            .replace(",", "")
            .strip()
        )
        return int(float(clean_value)) if clean_value else 0
    except (ValueError, TypeError):
        return 0


def _number(value: object) -> int:
    """Backward-compatible alias for numeric report values."""
    return safe_int(value)


def _month_from_record(record: dict, *keys: str) -> str:
    for key in keys:
        raw = str(record.get(key, "") or "").strip()
        if len(raw) >= 7 and raw[4] == "-" and raw[:7].replace("-", "").isdigit():
            return raw[:7]
        for date_format in ("%d.%m.%Y", "%d.%m.%Y %H:%M"):
            try:
                return datetime.datetime.strptime(raw, date_format).strftime("%Y-%m")
            except ValueError:
                continue
    return ""


def get_available_expense_months(
    reference_date: datetime.date | None = None,
) -> list[str]:
    reference_date = reference_date or datetime.date.today()
    current_month = reference_date.strftime("%Y-%m")
    previous_month_date = reference_date.replace(day=1) - datetime.timedelta(days=1)
    months = {current_month, previous_month_date.strftime("%Y-%m")}
    for record in ws_expenses.get_all_records():
        month = _month_from_record(record, "Дата")
        if month:
            months.add(month)
    return sorted(months, reverse=True)


def get_expenses_for_month(
    month_key: str,
    vehicle_filter: str | None = None,
) -> list[dict]:
    records = ws_expenses.get_all_records()
    normalized_filter = str(vehicle_filter or "").strip().casefold()
    general_values = {
        "",
        "общие",
        "общие расходы компании",
        "общие расходы (весь автопарк)",
        "🏢 общие расходы (весь автопарк)",
    }
    result: list[dict] = []
    for record in records:
        if _month_from_record(record, "Дата") != month_key:
            continue
        if normalized_filter:
            vehicle = str(
                record.get("Автомобиль (Госномер / Общие)")
                or record.get("Автомобиль")
                or ""
            ).strip().casefold()
            if normalized_filter == "general":
                if vehicle not in general_values:
                    continue
            elif vehicle != normalized_filter:
                continue
        result.append(dict(record))
    return result


def _upsert_record(
    ws,
    key_header: str,
    key_value: object,
    values: dict[str, object],
    required_headers: list[str],
) -> None:
    _upsert_record_by_keys(
        ws,
        (key_header,),
        (key_value,),
        values,
        required_headers,
    )


def _upsert_record_by_keys(
    ws,
    key_headers: tuple[str, ...],
    key_values: tuple[object, ...],
    values: dict[str, object],
    required_headers: list[str],
) -> None:
    if len(key_headers) != len(key_values):
        raise ValueError("Key headers and values must have the same length")
    records = ws.get_all_records()
    headers = _ensure_headers(ws, required_headers)
    target_row = None
    for row_number, record in enumerate(records, start=2):
        if all(
            str(record.get(header, "")).strip() == str(value).strip()
            for header, value in zip(key_headers, key_values)
        ):
            target_row = row_number
            break
    if target_row is None:
        ws.append_row([values.get(header, "") for header in headers])
        return
    for header, value in values.items():
        if header not in headers:
            continue
        ws.update_cell(target_row, headers.index(header) + 1, value)


def add_expense(
    amount: int,
    category: str,
    vehicle: str,
    telegram_id: int,
    name: str,
    note: str,
    expense_date: str,
) -> str:
    expense_id = f"EXP-{uuid.uuid4().hex[:16].upper()}"
    with EXPENSES_LOCK:
        _append_record(
            ws_expenses,
            {
                "ID Записи": expense_id,
                "Дата": expense_date,
                "Сумма (RUB)": amount,
                "Категория / На что": category,
                "Автомобиль (Госномер / Общие)": vehicle,
                "Telegram ID": telegram_id,
                "Имя": name,
                "Примечание": note,
            },
            EXPENSE_HEADERS,
        )
    return expense_id


def get_recent_expenses(limit: int = 10) -> list[dict]:
    with EXPENSES_LOCK:
        records = ws_expenses.get_all_records()
    recent: list[dict] = []
    start_row = max(2, len(records) + 2 - max(limit, 0))
    for row_number, record in enumerate(records[start_row - 2 :], start=start_row):
        item = dict(record)
        item["_row_number"] = row_number
        recent.append(item)
    recent.reverse()
    return recent


def _expense_fingerprint(record: dict) -> tuple[str, ...]:
    return tuple(
        str(record.get(header, "") or "").strip()
        for header in EXPENSE_HEADERS
    )


def delete_expense(
    expense_id: object,
    expected_record: dict | None = None,
) -> bool:
    expected_fingerprint = (
        _expense_fingerprint(expected_record)
        if expected_record is not None
        else None
    )
    with EXPENSES_LOCK:
        records = ws_expenses.get_all_records()
        headers = _ensure_headers(ws_expenses, EXPENSE_HEADERS)
        for row_number, record in enumerate(records, start=2):
            if str(record.get("ID Записи", "")).strip() != str(expense_id).strip():
                continue
            if (
                expected_fingerprint is not None
                and _expense_fingerprint(record) != expected_fingerprint
            ):
                return False
            current_values = ws_expenses.row_values(row_number)
            current_record = dict(zip(headers, current_values))
            if str(current_record.get("ID Записи", "")).strip() != str(
                expense_id
            ).strip():
                return False
            if (
                expected_fingerprint is not None
                and _expense_fingerprint(current_record) != expected_fingerprint
            ):
                return False
            ws_expenses.delete_rows(row_number)
            return True
    return False


def get_available_financial_months(
    reference_date: datetime.date | None = None,
) -> list[str]:
    reference_date = reference_date or datetime.date.today()
    current_month = reference_date.strftime("%Y-%m")
    previous_month_date = reference_date.replace(day=1) - datetime.timedelta(days=1)
    months = {current_month, previous_month_date.strftime("%Y-%m")}
    for record in ws_returns.get_all_records():
        month = _month_from_record(
            record,
            "Дата возврата",
            "Дата и время приёма",
        )
        if month:
            months.add(month)
    for record in ws_expenses.get_all_records():
        month = _month_from_record(record, "Дата")
        if month:
            months.add(month)
    return sorted(months, reverse=True)


def get_financial_report(month_key: str) -> dict:
    returns = ws_returns.get_all_records()
    expenses = ws_expenses.get_all_records()
    cars = ws_cars.get_all_records()

    def in_month(record: dict, *keys: str) -> bool:
        return _month_from_record(record, *keys) == month_key

    def return_revenue(record: dict) -> int:
        canonical_income = record.get("Итого приход (RUB)")
        if str(canonical_income if canonical_income is not None else "").strip():
            return _number(canonical_income)
        rental_keys = (
            "Фактически оплачено за аренду (RUB)",
            "Сумма аренды получена (RUB)",
        )
        top_up_keys = (
            "Полученная доплата (RUB)",
            "Полученная доплата",
        )
        rental_paid = next(
            (
                record.get(key)
                for key in rental_keys
                if str(
                    record.get(key)
                    if record.get(key) is not None
                    else ""
                ).strip()
            ),
            0,
        )
        top_up = next(
            (
                record.get(key)
                for key in top_up_keys
                if str(
                    record.get(key)
                    if record.get(key) is not None
                    else ""
                ).strip()
            ),
            0,
        )
        has_actual_payment_fields = any(
            str(
                record.get(key)
                if record.get(key) is not None
                else ""
            ).strip()
            for key in (*rental_keys, *top_up_keys)
        )
        if has_actual_payment_fields:
            return _number(rental_paid) + _number(top_up)
        return _number(
            record.get("Стоимость аренды (RUB)")
            or record.get("Сумма аренды (RUB)")
        )

    def car_id(car: dict) -> str:
        return str(
            car.get("ID Авто")
            or car.get("ID")
            or car.get("Car ID")
            or ""
        ).strip()

    def car_plate(car: dict) -> str:
        return str(
            car.get("Госномер")
            or car.get("Номер авто")
            or car.get("Номер")
            or car.get("Plate")
            or ""
        ).strip()

    def car_name(car: dict) -> str:
        return str(
            car.get("Марка и Модель")
            or car.get("Марка и модель")
            or car.get("Марка Модель")
            or car.get("Название")
            or car.get("Авто")
            or car.get("Модель")
            or ""
        ).strip()

    def return_plate(record: dict) -> str:
        return str(
            record.get("Госномер")
            or record.get("Номер авто")
            or record.get("Номер")
            or record.get("Plate")
            or ""
        ).strip().casefold()

    def expense_vehicle(record: dict) -> str:
        return str(
            record.get("Автомобиль (Госномер / Общие)")
            or record.get("Автомобиль")
            or ""
        ).strip().casefold()

    def is_general_expense(record: dict) -> bool:
        vehicle = expense_vehicle(record)
        return not vehicle or vehicle in {
            "общие",
            "общие расходы компании",
            "общие расходы (весь автопарк)",
            "🏢 общие расходы (весь автопарк)",
        }

    def expense_category(record: dict) -> str:
        return str(
            record.get("Категория / На что")
            or record.get("Категория")
            or "Прочее"
        ).strip() or "Прочее"

    month_returns = [
        record
        for record in returns
        if in_month(record, "Дата возврата", "Дата и время приёма")
    ]
    month_expenses = [
        record
        for record in expenses
        if in_month(record, "Дата")
    ]
    gross_income = sum(return_revenue(record) for record in month_returns)
    direct_expenses = sum(
        _number(record.get("Сумма (RUB)"))
        for record in month_expenses
        if not is_general_expense(record)
    )
    general_expenses = sum(
        _number(record.get("Сумма (RUB)"))
        for record in month_expenses
        if is_general_expense(record)
    )
    ksenia_salary = round(gross_income * 0.10)
    total_expenses = direct_expenses + general_expenses + ksenia_salary
    company_profit = gross_income - total_expenses

    car_income: dict[str, int] = {}
    car_mileage: dict[str, int] = {}
    for record in month_returns:
        plate = return_plate(record)
        if plate:
            car_income[plate] = car_income.get(plate, 0) + return_revenue(record)
            car_mileage[plate] = car_mileage.get(plate, 0) + _number(
                record.get("Пробег за аренду (км)")
                or record.get("Пройденный пробег (км)")
                or record.get("Пробег за период (км)")
            )

    car_expenses: dict[str, int] = {}
    car_expenses_by_category: dict[str, dict[str, int]] = {}
    for record in month_expenses:
        if is_general_expense(record):
            continue
        vehicle = expense_vehicle(record)
        amount = _number(record.get("Сумма (RUB)"))
        category = expense_category(record)
        car_expenses[vehicle] = car_expenses.get(vehicle, 0) + amount
        categories = car_expenses_by_category.setdefault(vehicle, {})
        categories[category] = categories.get(category, 0) + amount

    car_rows = []
    for car in cars:
        plate_display = car_plate(car)
        plate_key = plate_display.casefold()
        stable_car_id = car_id(car) or plate_display
        display_name = car_name(car)
        income = car_income.get(plate_key, 0)
        costs = car_expenses.get(plate_key, 0)
        car_rows.append(
            {
                "Месяц (ГГГГ-ММ)": month_key,
                "ID Авто": stable_car_id,
                "Марка и Модель": display_name,
                "Госномер": plate_display,
                "Приход / Выручка (RUB)": income,
                "Затраты на авто (RUB)": costs,
                "Расходы по статьям": dict(
                    sorted(car_expenses_by_category.get(plate_key, {}).items())
                ),
                "Пробег за месяц (км)": car_mileage.get(plate_key, 0),
                "Чистая прибыль авто (RUB)": income - costs,
            }
        )

    _upsert_record(
        ws_monthly_summary,
        "Месяц (ГГГГ-ММ)",
        month_key,
        {
            "Месяц (ГГГГ-ММ)": month_key,
            "Общий приход (RUB)": gross_income,
            "Прямые расходы (RUB)": direct_expenses,
            "Общие расходы компании (RUB)": general_expenses,
            "ЗП Ксении (10%) (RUB)": ksenia_salary,
            "Итого расходы (RUB)": total_expenses,
            "Чистая прибыль компании (RUB)": company_profit,
        },
        MONTHLY_SUMMARY_HEADERS,
    )
    for row in car_rows:
        _upsert_record_by_keys(
            ws_car_profit_summary,
            ("Месяц (ГГГГ-ММ)", "ID Авто"),
            (month_key, row["ID Авто"]),
            row,
            CAR_PROFIT_SUMMARY_HEADERS,
        )
    return {
        "month": month_key,
        "gross_income": gross_income,
        "direct_expenses": direct_expenses,
        "general_expenses": general_expenses,
        "ksenia_salary": ksenia_salary,
        "total_expenses": total_expenses,
        "company_profit": company_profit,
        "cars": car_rows,
    }


def add_new_booking(
    car_id: int,
    car_name: str,
    start_date: str,
    end_date: str,
    client_name: str,
    phone: str,
    price: int,
    manager_id: int,
    issued_at: str = "",
    start_mileage: int = 0,
    deposit: int = 0,
    pay_type: str = "Без оплаты",
    deposit_pay_type: str = "Без оплаты",
    status: str = "Подтверждена",
    prepayment: int = 0,
    prepayment_pay_type: str = "Без оплаты",
    manager_name: str = "",
    delivery_address: str = "Самовывоз",
    delivery_price: int = 0,
):
    try:
        bookings_sheet = _get_bookings_sheet()
        records = bookings_sheet.get_all_records()
        booking_ids = []
        for record in records:
            raw_booking_id = record.get("ID Брони", record.get("ID", 0))
            try:
                booking_ids.append(
                    int(str(raw_booking_id).replace(" ", ""))
                )
            except (TypeError, ValueError):
                continue
        new_booking_id = max(booking_ids, default=100) + 1

        booking_deposit = prepayment if prepayment else deposit
        booking_deposit_pay_type = (
            prepayment_pay_type
            if prepayment
            else deposit_pay_type
        )
        car_id_value = int(str(car_id or 0).replace(" ", ""))
        rent_price_value = int(str(price or 0).replace(" ", ""))
        delivery_price_value = int(
            str(delivery_price or 0).replace(" ", "")
        )
        total_price_value = rent_price_value + delivery_price_value
        delivery_address_value = str(delivery_address or "Самовывоз").strip()
        if not delivery_address_value:
            delivery_address_value = "Самовывоз"
        booking_deposit_value = int(
            str(booking_deposit or 0).replace(" ", "")
        )
        manager_id_value = int(str(manager_id or 0).replace(" ", ""))
        insurance_deposit_value = int(
            str(deposit or 0).replace(" ", "")
        )
        start_mileage_value = int(
            str(start_mileage or 0).replace(" ", "")
        )
        row = [
            new_booking_id,
            car_id_value,
            str(car_name or ""),
            str(start_date or ""),
            str(end_date or ""),
            str(client_name or ""),
            str(phone or ""),
            total_price_value,
            booking_deposit_value,
            str(booking_deposit_pay_type or "Без оплаты"),
            manager_id_value,
            str(status or "Подтверждена"),
            delivery_address_value,
            delivery_price_value,
        ]
        headers = _ensure_headers(bookings_sheet, BOOKING_HEADERS)

        row_to_append = row
        if headers[: len(BOOKING_HEADERS)] != BOOKING_HEADERS:
            legacy_values = {
                "ID Брони": new_booking_id,
                "ID": new_booking_id,
                "ID Авто": car_id_value,
                "Автомобиль": str(car_name or ""),
                "Авто": str(car_name or ""),
                "Дата начала": str(start_date or ""),
                "Дата начала (ГГГГ-ММ-ДД)": str(start_date or ""),
                "Дата окончания": str(end_date or ""),
                "Дата окончания (ГГГГ-ММ-ДД)": str(end_date or ""),
                "ФИО Клиента": str(client_name or ""),
                "Клиент": str(client_name or ""),
                "Телефон": str(phone or ""),
                "Сумма брони (RUB)": total_price_value,
                "Цена": total_price_value,
                "Адрес доставки": delivery_address_value,
                "Стоимость доставки (RUB)": delivery_price_value,
                "ID Менеджера": manager_id_value,
                "Telegram ID Менеджера": manager_id_value,
                "Имя менеджера": str(manager_name or manager_id),
                "Менеджер": str(manager_name or manager_id),
                "Статус": str(status or "Подтверждена"),
                "Задаток (RUB)": booking_deposit_value,
                "Залог/Предоплата": booking_deposit_value,
                "Задаток/Предоплата": booking_deposit_value,
                "Способ оплаты задатка": str(
                    booking_deposit_pay_type or "Без оплаты"
                ),
                "Тип оплаты залога": str(
                    booking_deposit_pay_type or "Без оплаты"
                ),
                "Дата и время выдачи": str(issued_at or ""),
                "Пробег при выдаче (км)": start_mileage_value,
                "Принятая сумма аренды (RUB)": total_price_value,
                "Залог (RUB)": insurance_deposit_value,
                "Внесенный залог": insurance_deposit_value,
                "Страховой залог": insurance_deposit_value,
                "Способ оплаты": str(pay_type or "Без оплаты"),
                "Способ оплаты залога": str(
                    deposit_pay_type or "Без оплаты"
                ),
                "Способ оплаты страхового залога": str(
                    deposit_pay_type or "Без оплаты"
                ),
                "Плановое окончание": f"{str(end_date or '')} 23:59",
            }
            row_to_append = [
                legacy_values.get(header, "")
                for header in headers
            ]
        bookings_sheet.append_row(
            row_to_append,
            value_input_option="USER_ENTERED",
        )
        return new_booking_id
    except Exception:
        traceback.print_exc()
        raise


def get_available_cars_for_direct_issuance() -> list[dict]:
    """Return every car so the manager can explicitly override its status."""
    return ws_cars.get_all_records()


def update_car_status(
    car_id: str = "",
    plate: str = "",
    status: str = "Свободен",
    mileage: int | None = None,
) -> None:
    """Update a car's status and, when provided, its current mileage."""
    values = ws_cars.get_all_values()
    if not values:
        raise ValueError("Автомобили sheet is empty")
    headers = values[0]
    id_columns = [
        index
        for index, header in enumerate(headers)
        if header in {"ID", "ID Авто", "Car ID"}
    ]
    plate_columns = [
        index
        for index, header in enumerate(headers)
        if header in {"Госномер", "Номер авто", "Номер", "Plate"}
    ]
    target_row = None
    for row_number, row in enumerate(values[1:], start=2):
        row_id = next(
            (str(row[index]).strip() for index in id_columns if index < len(row)),
            "",
        )
        row_plate = next(
            (
                str(row[index]).strip()
                for index in plate_columns
                if index < len(row)
            ),
            "",
        )
        if (car_id and row_id == str(car_id).strip()) or (
            plate and row_plate == str(plate).strip()
        ):
            target_row = row_number
            break
    if target_row is None:
        raise ValueError(
            f"Car was not found (ID={car_id!r}, plate={plate!r})"
        )

    status_column = next(
        (
            index + 1
            for index, header in enumerate(headers)
            if header == "Статус"
        ),
        None,
    )
    if status_column is None:
        raise ValueError("Автомобили sheet has no Статус header")
    ws_cars.update_cell(target_row, status_column, status)

    if mileage is not None:
        mileage_header = next(
            (
                header
                for header in (
                    "Текущий пробег (км)",
                    "Пробег",
                    "Текущий пробег",
                    "Пробег (км)",
                )
                if header in headers
            ),
            None,
        )
        if mileage_header:
            ws_cars.update_cell(
                target_row,
                headers.index(mileage_header) + 1,
                mileage,
            )


def save_direct_issuance(
    car_id: int,
    car_name: str,
    plate: str,
    client_name: str,
    phone: str,
    end_date: str,
    price: int,
    pay_type: str,
    start_mileage: int,
    deposit: int,
    deposit_pay_type: str,
    manager_id: int,
    manager_name: str,
    issued_at: str,
) -> int:
    """Create an immediately active booking and mark its car as rented."""
    with BOOKINGS_LOCK:
        bookings_sheet = _get_bookings_sheet()
        records = bookings_sheet.get_all_records()
        booking_ids = []
        for record in records:
            try:
                booking_ids.append(
                    int(
                        str(
                            record.get("ID Брони")
                            or record.get("ID")
                            or 0
                        ).replace(" ", "")
                    )
                )
            except (TypeError, ValueError):
                continue
        booking_id = max(booking_ids, default=100) + 1
        today = datetime.date.today().isoformat()
        required_headers = [
            ("ID Брони", "ID", "Booking ID"),
            ("ID Авто", "Car ID"),
            ("Автомобиль", "Авто", "Название авто"),
            ("Дата начала (ГГГГ-ММ-ДД)", "Дата начала"),
            ("Дата окончания (ГГГГ-ММ-ДД)", "Дата окончания"),
            ("ФИО Клиента", "Клиент", "Клиент ФИО"),
            ("Телефон", "Телефон клиента"),
            ("Сумма брони (RUB)", "Цена", "Стоимость"),
            ("Telegram ID Менеджера", "ID Менеджера"),
            ("Статус",),
            ("Дата и время выдачи", "Дата выдачи"),
            ("Пробег при выдаче (км)", "Начальный пробег"),
            ("Принятая сумма аренды (RUB)", "Фактически оплачено за аренду (RUB)"),
            ("Залог (RUB)", "Внесенный залог", "Страховой залог"),
            ("Способ оплаты", "Способ оплаты аренды"),
            ("Способ оплаты залога", "Способ оплаты страхового залога"),
            ("Плановое окончание", "Плановая дата и время окончания"),
        ]
        headers = _ensure_header_aliases(bookings_sheet, required_headers)
        values = {
            "ID Брони": booking_id,
            "ID": booking_id,
            "Booking ID": booking_id,
            "ID Авто": car_id,
            "Car ID": car_id,
            "Автомобиль": car_name,
            "Авто": car_name,
            "Название авто": car_name,
            "Дата начала (ГГГГ-ММ-ДД)": today,
            "Дата начала": today,
            "Дата окончания (ГГГГ-ММ-ДД)": end_date,
            "Дата окончания": end_date,
            "ФИО Клиента": client_name,
            "Клиент": client_name,
            "Клиент ФИО": client_name,
            "Телефон": phone,
            "Телефон клиента": phone,
            "Сумма брони (RUB)": price,
            "Цена": price,
            "Стоимость": price,
            "Telegram ID Менеджера": manager_id,
            "ID Менеджера": manager_id,
            "Telegram ID Менеджера выдачи": manager_id,
            "Имя менеджера": manager_name,
            "Менеджер": manager_name,
            "Статус": "В аренде",
            "Дата и время выдачи": issued_at,
            "Дата выдачи": issued_at,
            "Пробег при выдаче (км)": start_mileage,
            "Начальный пробег": start_mileage,
            "Принятая сумма аренды (RUB)": price,
            "Фактически оплачено за аренду (RUB)": price,
            "Способ оплаты": pay_type,
            "Способ оплаты аренды": pay_type,
            "Залог (RUB)": deposit,
            "Внесенный залог": deposit,
            "Страховой залог": deposit,
            "Способ оплаты залога": deposit_pay_type,
            "Способ оплаты страхового залога": deposit_pay_type,
            "Задаток/Предоплата": 0,
            "Задаток (RUB)": 0,
            "Способ оплаты задатка": "Без оплаты",
            "Плановое окончание": f"{end_date} 23:59",
            "Плановая дата и время окончания": f"{end_date} 23:59",
        }
        bookings_sheet.append_row(
            [values.get(header, "") for header in headers],
            value_input_option="USER_ENTERED",
        )
        try:
            update_car_status(
                car_id=str(car_id),
                plate=plate,
                status="В аренде",
            )
        except Exception:
            logger.exception(
                "Direct issuance created booking %s but car status update failed",
                booking_id,
            )
            raise
        return booking_id


def get_active_rentals():
    all_bookings = ws_bookings.get_all_records()
    return [b for b in all_bookings if b.get("Статус") in ["В аренде", "Подтверждена"]]


def get_returnable_rentals():
    all_bookings = ws_bookings.get_all_records()
    return [b for b in all_bookings if b.get("Статус") == "В аренде"]


def get_confirmed_bookings():
    all_bookings = ws_bookings.get_all_records()
    return [b for b in all_bookings if b.get("Статус") == "Подтверждена"]


def issue_booking(
    booking_id: int,
    start_mileage: int,
    insurance_deposit: int,
    insurance_deposit_pay_type: str,
    manager_id: int,
    issued_at: str,
) -> None:
    records = ws_bookings.get_all_records()
    booking_row = None
    current_status = ""
    for row_number, booking in enumerate(records, start=2):
        current_id = (
            booking.get("ID Брони")
            or booking.get("ID")
            or booking.get("Booking ID")
            or ""
        )
        if str(current_id).strip() == str(booking_id).strip():
            booking_row = row_number
            current_status = str(booking.get("Статус", "")).strip()
            break
    if booking_row is None:
        raise ValueError(f"Booking {booking_id} was not found")
    if current_status != "Подтверждена":
        raise ValueError(
            f"Booking {booking_id} cannot be issued from status {current_status}"
        )

    _update_existing_headers(
        ws_bookings,
        booking_row,
        [
            (("Статус",), "В аренде", True),
            (("Дата и время выдачи",), issued_at, True),
            (("Пробег при выдаче (км)",), start_mileage, True),
            (
                (
                    "Залог (RUB)",
                    "Страховой залог (RUB)",
                    "Страховой залог",
                    "Внесенный залог",
                    "Внесенный страховой залог (RUB)",
                ),
                insurance_deposit,
                True,
            ),
            (
                (
                    "Способ оплаты страхового залога",
                    "Способ оплаты залога",
                    "Способ оплаты",
                ),
                insurance_deposit_pay_type,
                False,
            ),
            (("Telegram ID Менеджера выдачи",), manager_id, False),
        ],
    )


def get_active_cars():
    all_cars = ws_cars.get_all_records()
    return [car for car in all_cars if car.get("Статус") == "Активен"]


def get_all_cars():
    return ws_cars.get_all_records()


def get_active_bookings_for_car(car_id: str, plate: str = ""):
    active_statuses = ["В аренде", "Подтверждена"]
    bookings = [
        booking
        for booking in ws_bookings.get_all_records()
        if booking.get("Статус") in active_statuses
    ]
    matching_bookings = []
    for booking in bookings:
        if str(booking.get("ID Авто", "")).strip() == str(car_id).strip():
            matching_bookings.append(booking)
            continue
        booking_plate = str(
            booking.get("Госномер")
            or booking.get("Номер авто")
            or booking.get("Номер")
            or ""
        ).strip()
        if plate and booking_plate == str(plate).strip():
            matching_bookings.append(booking)
    return matching_bookings


def get_cancellable_bookings_for_car(car_id: str, plate: str = ""):
    return [
        booking
        for booking in get_active_bookings_for_car(car_id, plate)
        if booking.get("Статус") == "Подтверждена"
    ]


def cancel_booking(
    booking_id: int,
    reason: str,
    deposit_status: str,
) -> None:
    try:
        all_values = ws_bookings.get_all_values()
        if not all_values:
            raise ValueError("Бронирования sheet is empty")

        headers = all_values[0]
        booking_row = None
        current_status = ""
        status_column = (
            headers.index("Статус") if "Статус" in headers else None
        )
        for row_number, row in enumerate(all_values[1:], start=2):
            if row and str(row[0]).strip() == str(booking_id).strip():
                booking_row = row_number
                if status_column is not None and status_column < len(row):
                    current_status = str(row[status_column]).strip()
                break
        if booking_row is None:
            raise ValueError(f"Booking {booking_id} was not found")
        if current_status != "Подтверждена":
            raise ValueError(
                f"Booking {booking_id} cannot be cancelled from status "
                f"{current_status}"
            )

        updates = {
            "Статус": "Отменена",
            "Причина отмены": reason,
            "Статус залога при отмене": deposit_status,
            "Статус задатка при отмене": deposit_status,
        }
        headers = _ensure_headers(ws_bookings, list(updates))
        for header, value in updates.items():
            ws_bookings.update_cell(
                booking_row,
                headers.index(header) + 1,
                value,
            )
    except Exception:
        logger.exception(
            "Failed to cancel booking %s in Google Sheets",
            booking_id,
        )
        raise


def get_car_by_id(car_id: str):
    all_cars = ws_cars.get_all_records()
    for car in all_cars:
        if str(car.get("ID")) == str(car_id):
            return car
    return {}


def get_car_by_plate(plate: str):
    all_cars = ws_cars.get_all_records()
    for car in all_cars:
        if str(car.get("Госномер", "")).strip() == str(plate).strip():
            return car
    return {}


def save_actual_return(
    booking_id: int | str | None,
    car_name: str,
    plate: str,
    client: str,
    start_mileage: int,
    end_mileage: int,
    delta_mileage: int,
    base_price: int,
    extra_fee: int,
    total_paid: int,
    pay_type: str,
    manager_id: int,
    notes: str,
    returned_at: str = "",
    daily_rate: int = 0,
    rental_days: int = 0,
    mileage_limit: int = 0,
    over_mileage: int = 0,
    over_mileage_fee: int = 0,
    late_hours: int = 0,
    late_fee: int = 0,
    cleanliness: str = "",
    wash_fee: int = 0,
    deposit: int = 0,
    deposit_balance: int = 0,
    rental_total: int = 0,
    prepayment: int = 0,
    rental_balance: int = 0,
    prepayment_overpayment: int = 0,
    deposit_pay_type: str = "Без оплаты",
    prepayment_pay_type: str = "Без оплаты",
    rental_paid: int = 0,
    rental_pay_type: str = "Без оплаты",
    rental_outstanding: int = 0,
    manager_name: str = "",
    phone: str = "",
    issued_at: str = "",
    act_created_at: str = "",
    allow_unlinked: bool = False,
    car_id: str = "",
    idempotency_key: str = "",
):
    returned_at = returned_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_penalties = over_mileage_fee + late_fee + wash_fee + extra_fee
    booking_row = None
    current_status = ""
    if not allow_unlinked:
        for row_number, booking in enumerate(
            ws_bookings.get_all_records(),
            start=2,
        ):
            current_id = (
                booking.get("ID Брони")
                or booking.get("ID")
                or booking.get("Booking ID")
                or ""
            )
            if str(current_id).strip() == str(booking_id).strip():
                booking_row = row_number
                current_status = str(booking.get("Статус", "")).strip()
                break
        if booking_row is None:
            raise ValueError(f"Booking {booking_id} was not found")
    returns_records = ws_returns.get_all_records()
    return_key = str(idempotency_key or booking_id or "").strip()
    return_already_saved = any(
        str(
            record.get("ID внепланового возврата")
            or record.get("ID Брони")
            or ""
        ).strip()
        == return_key
        for record in returns_records
    ) if return_key else False
    acceptance_records = ws_acceptance_acts.get_all_records()
    acceptance_already_saved = any(
        str(record.get("ID Брони", "")).strip()
        == str(booking_id).strip()
        for record in acceptance_records
    )
    if (
        not allow_unlinked
        and
        current_status == "Завершена"
        and return_already_saved
        and acceptance_already_saved
    ):
        raise ValueError(f"Booking {booking_id} was already returned")
    if not allow_unlinked and current_status not in {"В аренде", "Завершена"}:
        raise ValueError(
            f"Booking {booking_id} cannot be returned from status {current_status}"
        )

    new_id = len(returns_records) + 1
    values = {
        "ID Записи": new_id,
        "ID": new_id,
        "ID Брони": "" if allow_unlinked else booking_id,
        "ID внепланового возврата": return_key if allow_unlinked else "",
        "Автомобиль": car_name,
        "Госномер": plate,
        "Клиент": client,
        "Дата возврата": returned_at,
        "Дата и время приёма": returned_at,
        "Пробег до (км)": start_mileage,
        "Пробег после (км)": end_mileage,
        "Пробег за аренду (км)": delta_mileage,
        "Сумма аренды (RUB)": base_price,
        "Стоимость аренды (RUB)": rental_total,
        "Задаток/Предоплата": prepayment,
        "Способ оплаты задатка": prepayment_pay_type,
        "Остаток к оплате аренды (RUB)": rental_balance,
        "Сумма аренды к доплате (RUB)": rental_balance,
        "Фактически оплачено за аренду (RUB)": rental_paid,
        "Способ оплаты аренды": rental_pay_type,
        "Осталось оплатить за аренду (RUB)": rental_outstanding,
        "Переплата задатка к возврату (RUB)": prepayment_overpayment,
        "Перепробег / Доплаты (RUB)": total_penalties,
        "Итого удержаний (RUB)": total_penalties,
        "Итого штрафов (RUB)": total_penalties,
        "Итого приход (RUB)": rental_paid + total_paid,
        "Полученная доплата (RUB)": total_paid,
        "Способ оплаты": pay_type,
        "Способ оплаты доплаты": pay_type,
        "Менеджер (TG ID)": manager_id,
        "Примечания / Состояние": notes,
        "Суточный тариф (RUB)": daily_rate,
        "Количество суток": rental_days,
        "Лимит пробега (км)": mileage_limit,
        "Перепробег (км)": over_mileage,
        "Штраф за перепробег (RUB)": over_mileage_fee,
        "Просрочка (ч)": late_hours,
        "Штраф за просрочку (RUB)": late_fee,
        "Состояние чистоты": cleanliness,
        "Мойка (RUB)": wash_fee,
        "Доп. штрафы/удержания (RUB)": extra_fee,
        "Залог (RUB)": deposit,
        "Внесенный залог": deposit,
        "Страховой залог (RUB)": deposit,
        "Страховой залог": deposit,
        "Внесенный страховой залог (RUB)": deposit,
        "Способ оплаты страхового залога": deposit_pay_type,
        "Залог к возврату (RUB)": max(deposit_balance, 0),
        "Страховой залог к возврату (RUB)": max(deposit_balance, 0),
        "Доплата по залогу (RUB)": max(-deposit_balance, 0),
        "Доплата за штрафы (RUB)": max(-deposit_balance, 0),
        "Итоговый расчёт (RUB)": deposit_balance,
    }
    if not return_already_saved:
        _append_record(
            ws_returns,
            values,
            [
            "ID Записи",
            "ID Брони",
            "ID внепланового возврата",
            "Автомобиль",
            "Госномер",
            "Клиент",
            "Дата возврата",
            "Пробег до (км)",
            "Пробег после (км)",
            "Пробег за аренду (км)",
            "Сумма аренды (RUB)",
            "Стоимость аренды (RUB)",
            "Задаток/Предоплата",
            "Способ оплаты задатка",
            "Остаток к оплате аренды (RUB)",
            "Сумма аренды к доплате (RUB)",
            "Фактически оплачено за аренду (RUB)",
            "Способ оплаты аренды",
            "Осталось оплатить за аренду (RUB)",
            "Переплата задатка к возврату (RUB)",
            "Перепробег / Доплаты (RUB)",
            "Итого удержаний (RUB)",
            "Итого штрафов (RUB)",
            "Итого приход (RUB)",
            "Полученная доплата (RUB)",
            "Способ оплаты",
            "Способ оплаты доплаты",
            "Менеджер (TG ID)",
            "Примечания / Состояние",
            "Дата и время приёма",
            "Суточный тариф (RUB)",
            "Количество суток",
            "Лимит пробега (км)",
            "Перепробег (км)",
            "Штраф за перепробег (RUB)",
            "Просрочка (ч)",
            "Штраф за просрочку (RUB)",
            "Состояние чистоты",
            "Мойка (RUB)",
            "Доп. штрафы/удержания (RUB)",
            "Залог (RUB)",
            "Внесенный залог",
            "Страховой залог (RUB)",
            "Страховой залог",
            "Внесенный страховой залог (RUB)",
            "Способ оплаты страхового залога",
            "Залог к возврату (RUB)",
            "Страховой залог к возврату (RUB)",
            "Доплата по залогу (RUB)",
            "Доплата за штрафы (RUB)",
            "Итоговый расчёт (RUB)",
            ],
        )
    booking_updates = {
        "Дата и время приёма": returned_at,
        "Пробег при возврате (км)": end_mileage,
        "Пробег за аренду (км)": delta_mileage,
        "Стоимость аренды (RUB)": rental_total,
        "Задаток/Предоплата": prepayment,
        "Способ оплаты задатка": prepayment_pay_type,
        "Остаток к оплате аренды (RUB)": rental_balance,
        "Сумма аренды к доплате (RUB)": rental_balance,
        "Фактически оплачено за аренду (RUB)": rental_paid,
        "Способ оплаты аренды": rental_pay_type,
        "Осталось оплатить за аренду (RUB)": rental_outstanding,
        "Переплата задатка к возврату (RUB)": prepayment_overpayment,
        "Лимит пробега (км)": mileage_limit,
        "Перепробег (км)": over_mileage,
        "Штраф за перепробег (RUB)": over_mileage_fee,
        "Просрочка (ч)": late_hours,
        "Штраф за просрочку (RUB)": late_fee,
        "Состояние чистоты": cleanliness,
        "Мойка (RUB)": wash_fee,
        "Доп. штрафы/удержания (RUB)": extra_fee,
        "Итого удержаний (RUB)": total_penalties,
        "Итого штрафов (RUB)": total_penalties,
        "Внесенный залог": deposit,
        "Страховой залог": deposit,
        "Внесенный страховой залог (RUB)": deposit,
        "Способ оплаты страхового залога": deposit_pay_type,
        "Залог к возврату (RUB)": max(deposit_balance, 0),
        "Страховой залог к возврату (RUB)": max(deposit_balance, 0),
        "Доплата по залогу (RUB)": max(-deposit_balance, 0),
        "Доплата за штрафы (RUB)": max(-deposit_balance, 0),
        "Полученная доплата (RUB)": total_paid,
        "Способ оплаты доплаты": pay_type,
        "Итоговый расчёт (RUB)": deposit_balance,
        "Примечания / Состояние": notes,
    }
    booking_headers = []
    if booking_row is not None:
        booking_headers = _ensure_headers(
            ws_bookings,
            [*booking_updates, "Статус"],
        )
        for header, value in booking_updates.items():
            ws_bookings.update_cell(
                booking_row,
                booking_headers.index(header) + 1,
                value,
            )

    acceptance_id = len(acceptance_records) + 1
    acceptance_values = {
        "ID Акта": acceptance_id,
        "ID Брони": booking_id,
        "Дата и время создания акта": act_created_at or returned_at,
        "Менеджер": manager_name or str(manager_id),
        "Автомобиль (Марка, Модель, Госномер)": (
            f"{car_name} ({plate})"
        ),
        "Клиент (Имя, Телефон)": f"{client} / {phone}",
        "Дата и время выдачи": issued_at,
        "Дата и время приёма (факт)": returned_at,
        "Начальный пробег": start_mileage,
        "Конечный пробег": end_mileage,
        "Пройденный пробег (км)": delta_mileage,
        "Лимит пробега (км)": mileage_limit,
        "Перепробег (км)": over_mileage,
        "Штраф за перепробег (RUB)": over_mileage_fee,
        "Просрочка по времени (часов)": late_hours,
        "Штраф за просрочку (RUB)": late_fee,
        "Состояние чистоты (Чистая/Грязная)": cleanliness,
        "Стоимость мойки (RUB)": wash_fee,
        "Дополнительные штрафы (RUB)": extra_fee,
        "Полная стоимость аренды (RUB)": rental_total,
        "Задаток / Предоплата (RUB)": prepayment,
        "Внесённый страховой залог (RUB)": deposit,
        "Сумма удержаний из залога (RUB)": total_penalties,
        "Итоговый статус залога (Сумма возврата или Сумма доплаты)": (
            f"Сумма возврата: {max(deposit_balance, 0)} RUB"
            if deposit_balance >= 0
            else f"Сумма доплаты: {abs(deposit_balance)} RUB"
        ),
        "Фактически принятая доплата (RUB)": total_paid,
        "Способ доплаты": pay_type,
        "Примечание": notes,
    }
    if not acceptance_already_saved:
        _append_record(
            ws_acceptance_acts,
            acceptance_values,
            ACCEPTANCE_ACT_HEADERS,
        )

    update_car_status(
        car_id=car_id,
        plate=plate,
        status="Свободен",
        mileage=end_mileage,
    )

    if booking_row is not None:
        ws_bookings.update_cell(
            booking_row,
            booking_headers.index("Статус") + 1,
            "Завершена",
        )