import os
import datetime
import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass
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
HEAVY_LUX_SOURCE = "heavy_lux_cr"
HEAVY_LUX_COMMISSION_RATE = 0.30
FINANCIAL_SOURCE_ALL = "all"
FINANCIAL_SOURCE_LEON = "leon"
FINANCIAL_SOURCE_HEAVY_LUX = HEAVY_LUX_SOURCE
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
    "Дата выдачи",
    "Время выдачи",
    "Время возврата",
    "Плановое окончание",
    "Источник",
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
    "Штраф за опоздание / перепробег (RUB)",
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
NOTIFICATION_STATE_HEADERS = [
    "Тип уведомления",
    "ID Брони",
    "Отправлено в UTC",
]
EXPIRING_RENTAL_ALERT_TYPE = "expiring_rental"
EXPIRING_RENTAL_ALERT_RETENTION_DAYS = 7
MONTHLY_SUMMARY_HEADERS = [
    "Месяц (ГГГГ-ММ)",
    "Общий приход (RUB)",
    "Партнёрская комиссия heavy_lux_cr (RUB)",
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
COLUMN_EXPANSION_STEP = 5
RUNTIME_OPERATION_MAX_RETRIES = 3
RUNTIME_RETRY_DELAYS = (1, 3)


@dataclass(frozen=True)
class SheetsStartupStatus:
    """Safe diagnostic state for the Google Sheets bootstrap."""

    ready: bool
    reason: str
    attempts: int
    hint: str


class SheetsStartupError(RuntimeError):
    """Safe, user-facing error raised when Sheets is not ready."""

    def __init__(self, status: SheetsStartupStatus):
        self.status = status
        super().__init__(
            "Google Sheets is not ready: "
            f"{status.reason}; attempts={status.attempts}. {status.hint}"
        )


class SheetsStructureError(RuntimeError):
    """Internal marker for an invalid worksheet structure during startup."""

    def __init__(
        self,
        conflicts: tuple[tuple[str, str], ...] = (),
    ):
        self.conflicts = tuple(conflicts)
        super().__init__("Google Sheets worksheet structure is invalid")


SHEETS_STARTUP_HINTS = {
    "ready": "Доступ к Google Sheets и обязательным листам подтверждён.",
    "quota": (
        "Проверьте квоту Google Sheets и не перезапускайте workflow "
        "до восстановления лимита."
    ),
    "network": (
        "Проверьте сетевой доступ к Google Sheets и повторите запуск "
        "после восстановления соединения."
    ),
    "structure": (
        "Проверьте обязательные листы таблицы: Автомобили, "
        "Возвраты_Касса и Менеджеры."
    ),
    "configuration": (
        "Проверьте имя таблицы, доступ сервисного аккаунта и настройки "
        "подключения Google Sheets."
    ),
}

REQUIRED_WORKSHEET_TITLES = (
    "Автомобили",
    "Бронирования",
    "Возвраты_Касса",
    "Менеджеры",
)


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
    if status_code in {408, 429, 500, 502, 503, 504}:
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
        or "connection reset" in error_text
        or "connection aborted" in error_text
        or any(
            marker in error_text
            for marker in (" 408", " 429", " 500", " 502", " 503", " 504")
        )
    )

def _retry_transient_operation(
    operation,
    operation_name: str,
    max_retries: int = 5,
    attempt_counter: list[int] | None = None,
):
    """Run one Sheets operation with bounded in-process retries."""
    global SHEETS_STARTUP_ATTEMPTS

    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    for attempt in range(max_retries):
        if attempt_counter is not None:
            attempt_counter[0] = attempt + 1
            SHEETS_STARTUP_ATTEMPTS = attempt + 1
        try:
            return operation()
        except Exception as error:
            is_last_attempt = attempt == max_retries - 1
            if is_last_attempt:
                logger.error(
                    "Google Sheets operation failed after %s attempts: "
                    "operation=%s; reason=%s",
                    max_retries,
                    operation_name,
                    _startup_failure_reason(error),
                )
                raise
            if not _is_transient_spreadsheet_error(error):
                raise
            logger.warning(
                "Temporary Google Sheets failure during %s "
                "(reason=%s; attempt %s/%s); retrying in 3 seconds",
                operation_name,
                _startup_failure_reason(error),
                attempt + 1,
                max_retries,
            )
            time.sleep(3)


def _retry_runtime_operation(
    operation,
    operation_name: str,
    max_retries: int = RUNTIME_OPERATION_MAX_RETRIES,
):
    """Run a runtime Sheets operation with a short, bounded retry budget."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as error:
            is_last_attempt = attempt == max_retries - 1
            if is_last_attempt or not _is_transient_spreadsheet_error(error):
                raise
            logger.warning(
                "Temporary Google Sheets failure during %s "
                "(reason=%s; attempt %s/%s); retrying",
                operation_name,
                _startup_failure_reason(error),
                attempt + 1,
                max_retries,
            )
            time.sleep(
                RUNTIME_RETRY_DELAYS[
                    min(attempt, len(RUNTIME_RETRY_DELAYS) - 1)
                ]
            )


def _sheets_read(operation, operation_name: str):
    """Read from Sheets while recovering short-lived API failures."""
    return _retry_runtime_operation(operation, operation_name)


def _sheets_write(operation, operation_name: str):
    """Repeat an idempotent absolute Sheets update on transient failures."""
    return _retry_runtime_operation(operation, operation_name)


def _row_matches_key(
    headers: list[object],
    row: list[object],
    key_fields: tuple[tuple[str, ...], ...],
    key_values: tuple[object, ...],
) -> bool:
    if len(key_fields) != len(key_values):
        raise ValueError("Key fields and values must have the same length")
    for aliases, expected in zip(key_fields, key_values):
        column = next(
            (
                index
                for index, header in enumerate(headers)
                if str(header or "").strip() in aliases
            ),
            None,
        )
        if column is None or column >= len(row):
            return False
        if str(row[column] or "").strip() != str(expected).strip():
            return False
    return True


def _row_with_key_exists(
    ws,
    key_fields: tuple[tuple[str, ...], ...],
    key_values: tuple[object, ...],
) -> bool:
    """Confirm an append reached Sheets after an ambiguous write response."""
    if not key_fields:
        return False
    all_values = _sheets_read(
        ws.get_all_values,
        f"checking persisted row in {getattr(ws, 'title', 'worksheet')}",
    )
    if not all_values:
        return False
    headers = list(all_values[0])
    return any(
        _row_matches_key(headers, row, key_fields, key_values)
        for row in all_values[1:]
    )


def _append_row_with_recovery(
    ws,
    row_values: list[object],
    *,
    key_fields: tuple[tuple[str, ...], ...],
    key_values: tuple[object, ...],
    operation_name: str,
    value_input_option: str | None = None,
) -> None:
    """Append once per attempt, checking the idempotency key after failures."""
    for attempt in range(RUNTIME_OPERATION_MAX_RETRIES):
        try:
            if value_input_option is None:
                ws.append_row(row_values)
            else:
                ws.append_row(
                    row_values,
                    value_input_option=value_input_option,
                )
            return
        except Exception as error:
            if not _is_transient_spreadsheet_error(error):
                raise
            if _row_with_key_exists(ws, key_fields, key_values):
                logger.info(
                    "Google Sheets append during %s completed before "
                    "the response was received",
                    operation_name,
                )
                return
            if attempt == RUNTIME_OPERATION_MAX_RETRIES - 1:
                raise
            logger.warning(
                "Temporary Google Sheets failure during %s "
                "(reason=%s; attempt %s/%s); retrying",
                operation_name,
                _startup_failure_reason(error),
                attempt + 1,
                RUNTIME_OPERATION_MAX_RETRIES,
            )
            time.sleep(
                RUNTIME_RETRY_DELAYS[
                    min(attempt, len(RUNTIME_RETRY_DELAYS) - 1)
                ]
            )
def get_spreadsheet_with_retry(
    gc_client,
    spreadsheet_key: str,
    max_retries: int = 5,
    attempt_counter: list[int] | None = None,
):
    """Open a spreadsheet while tolerating short Google API outages."""
    return _retry_transient_operation(
        lambda: gc_client.open_by_key(spreadsheet_key),
        "opening spreadsheet",
        max_retries,
        attempt_counter,
    )


gc = None
sh = None
_worksheet_cache: dict[str, object] = {}
SHEETS_READY = False
SHEETS_STARTUP_ATTEMPTS = 0
SHEETS_STARTUP_STATUS = SheetsStartupStatus(
    ready=False,
    reason="not_started",
    attempts=0,
    hint="Инициализация Google Sheets ещё не завершена.",
)


def migrate_sheet_titles(spreadsheet) -> list[tuple[str, str]]:
    """Rename legacy worksheet titles to their Russian equivalents."""
    worksheets = {
        worksheet.title: worksheet for worksheet in spreadsheet.worksheets()
    }
    return _migrate_sheet_titles_from_cache(worksheets)

def _migrate_sheet_titles_from_cache(
    worksheets: dict[str, object],
) -> list[tuple[str, str]]:
    """Rename legacy titles using one already-loaded metadata response."""
    conflicts = _worksheet_title_conflicts(worksheets)
    if conflicts:
        raise SheetsStructureError(conflicts)

    renamed: list[tuple[str, str]] = []
    for old_title, new_title in SHEET_TITLE_MAP.items():
        source = worksheets.get(old_title)
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


def _worksheet_title_conflicts(
    worksheets: dict[str, object] | set[str] | tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """Return every legacy/current title pair present in one metadata snapshot."""
    titles = set(worksheets)
    return tuple(
        (old_title, new_title)
        for old_title, new_title in SHEET_TITLE_MAP.items()
        if old_title in titles and new_title in titles
    )


def _column_letter(column_number: int) -> str:
    """Convert a one-based column number to an A1 column label."""
    result = ""
    number = int(column_number)
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _ensure_column_capacity(ws, required_columns: int) -> None:
    """Expand a worksheet before writing a cell outside its current grid."""
    required_columns = max(int(required_columns), 1)
    current_columns = int(getattr(ws, "col_count", 0) or 0)
    if current_columns >= required_columns:
        return
    columns_to_add = max(
        COLUMN_EXPANSION_STEP,
        required_columns - current_columns,
    )
    operation_name = f"expanding {getattr(ws, 'title', 'worksheet')}"
    for attempt in range(RUNTIME_OPERATION_MAX_RETRIES):
        try:
            ws.add_cols(columns_to_add)
            return
        except Exception as error:
            if not _is_transient_spreadsheet_error(error):
                raise
            if int(getattr(ws, "col_count", 0) or 0) >= required_columns:
                return
            if attempt == RUNTIME_OPERATION_MAX_RETRIES - 1:
                raise
            logger.warning(
                "Temporary Google Sheets failure during %s "
                "(reason=%s; attempt %s/%s); retrying",
                operation_name,
                _startup_failure_reason(error),
                attempt + 1,
                RUNTIME_OPERATION_MAX_RETRIES,
            )
            time.sleep(
                RUNTIME_RETRY_DELAYS[
                    min(attempt, len(RUNTIME_RETRY_DELAYS) - 1)
                ]
            )


def _ensure_sheet_width(ws) -> None:
    """Keep operational worksheets wide enough for optional legacy fields."""
    _ensure_column_capacity(ws, TARGET_OPERATIONAL_COLUMNS)


def _ensure_all_sheet_widths(spreadsheet) -> None:
    """Expand every worksheet before any header or data update is attempted."""
    for worksheet in spreadsheet.worksheets():
        _ensure_column_capacity(worksheet, MIN_OPERATIONAL_COLUMNS)


def _get_bookings_sheet():
    """Find the bookings worksheet under either supported title."""
    try:
        worksheet = _cached_worksheet("Бронирования")
    except gspread.exceptions.WorksheetNotFound:
        try:
            worksheet = _cached_worksheet("Bookings")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(
                title="Бронирования",
                rows=1000,
                cols=TARGET_OPERATIONAL_COLUMNS,
            )
            _append_row_with_recovery(
                worksheet,
                BOOKING_HEADERS,
                key_fields=(),
                key_values=(),
                operation_name="creating bookings headers",
                value_input_option="USER_ENTERED",
            )
            _worksheet_cache["Бронирования"] = worksheet
    _ensure_sheet_width(worksheet)
    return worksheet


def get_manager_access(tg_id: int) -> dict | None:
    """Return normalized access data for a Telegram ID from Менеджеры."""
    clean_user_id = str(tg_id).strip()
    role = ""
    for manager in _sheets_read(
        ws_managers.get_all_records,
        "reading manager access",
    ):
        sheet_id = (
            str(manager.get("Telegram ID", ""))
            .strip()
            .replace(" ", "")
            .replace(".0", "")
        )
        if clean_user_id != sheet_id:
            continue
        cell_role = (
            manager.get("Роль")
            or manager.get("Role")
            or manager.get("Тип доступа")
            or ""
        )
        role = str(cell_role).strip().lower()
        active = str(manager.get("Доступ активен", "")).strip().lower() in [
            "да",
            "yes",
            "true",
        ]
        print(f"[AUTH] Запрос от ID: {clean_user_id}, найденная роль: {role}")
        return {
            "user_id": clean_user_id,
            "role": role,
            "active": active,
        }
    print(f"[AUTH] Запрос от ID: {clean_user_id}, найденная роль: {role}")
    return None


def is_manager_authorized(tg_id: int) -> bool:
    access = get_manager_access(tg_id)
    return bool(access and access["active"])


def is_partner_authorized(tg_id: int) -> bool:
    """Return whether an active manager row has the partner role."""
    access = get_manager_access(tg_id)
    return bool(
        access
        and access["active"]
        and access["role"] in {"партнёр", "партнер", "partner"}
    )


def get_active_manager_ids() -> list[int]:
    manager_ids = []
    seen_ids = set()
    for manager in _sheets_read(
        ws_managers.get_all_records,
        "reading active managers",
    ):
        access = str(manager.get("Доступ активен", "")).strip().lower()
        if access not in {"да", "yes", "true"}:
            continue
        raw_id = (
            str(manager.get("Telegram ID", ""))
            .strip()
            .replace(" ", "")
            .replace(".0", "")
        )
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
    for manager in _sheets_read(
        ws_managers.get_all_records,
        "reading pending notification recipients",
    ):
        access = str(manager.get("Доступ активен", "")).strip().lower()
        if access not in {"да", "yes", "true"}:
            continue
        try:
            manager_id = int(
                str(manager.get("Telegram ID", ""))
                .strip()
                .replace(" ", "")
                .replace(".0", "")
            )
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
    records = _sheets_read(
        ws_managers.get_all_records,
        "reading manager notification state",
    )
    headers = _ensure_headers(ws_managers, ["Последний план на дату"])
    column = headers.index("Последний план на дату") + 1
    for row_number, manager in enumerate(records, start=2):
        sheet_id = (
            str(manager.get("Telegram ID", ""))
            .strip()
            .replace(" ", "")
            .replace(".0", "")
        )
        if sheet_id == str(manager_id).strip():
            _sheets_write(
                lambda: ws_managers.update_cell(
                    row_number,
                    column,
                    target_date.isoformat(),
                ),
                "saving manager notification state",
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
    for booking in _get_sheet_records_safely(ws_bookings, "Бронирования"):
        status = str(booking.get("Статус", "")).strip()
        if status == "Подтверждена" and _first_booking_date(
            booking,
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
        ) == target_date:
            events["issuances"].append(booking)
        elif status == "В аренде" and _first_booking_date(
            booking,
            "Плановое окончание",
            "Плановая дата и время окончания",
            "Дата окончания (ГГГГ-ММ-ДД)",
            "Дата окончания",
        ) == target_date:
            events["returns"].append(booking)
    return events


def _normalized_car_identifier(value: object) -> str:
    return str(value or "").strip().replace(" ", "").removesuffix(".0")


def _open_rental_car_identifiers(
    bookings: list[dict],
) -> tuple[set[str], set[str]]:
    rented_ids: set[str] = set()
    rented_plates: set[str] = set()
    for booking in bookings:
        if str(booking.get("Статус", "")).strip() != "В аренде":
            continue
        car_id = _normalized_car_identifier(
            booking.get("ID Авто") or booking.get("Car ID")
        )
        plate = str(
            booking.get("Госномер")
            or booking.get("Номер авто")
            or booking.get("Номер")
            or ""
        ).strip().casefold()
        if car_id:
            rented_ids.add(car_id)
        if plate:
            rented_plates.add(plate)
    return rented_ids, rented_plates


def has_open_rental_for_car(car_id: str = "", plate: str = "") -> bool:
    bookings = _get_sheet_records_safely(ws_bookings, "Бронирования")
    rented_ids, rented_plates = _open_rental_car_identifiers(bookings)
    normalized_id = _normalized_car_identifier(car_id)
    normalized_plate = str(plate or "").strip().casefold()
    return (
        bool(normalized_id and normalized_id in rented_ids)
        or bool(normalized_plate and normalized_plate in rented_plates)
    )


def get_available_cars(start_date_str: str, end_date_str: str):
    req_start = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    req_end = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    all_cars = _sheets_read(
        ws_cars.get_all_records,
        "reading cars",
    )
    all_bookings = _get_sheet_records_safely(
        ws_bookings,
        "Бронирования",
    )
    busy_car_ids: set[str] = set()
    for b in all_bookings:
        status = str(b.get("Статус", "")).strip().casefold()
        if status not in {
            "подтверждена",
            "в аренде",
            "ожидает подтверждения",
        }:
            continue
        b_start = _first_booking_date(
            b,
            "Дата выдачи",
            "Дата выдачи (ГГГГ-ММ-ДД)",
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
        )
        b_end = _first_booking_date(
            b,
            "Плановое окончание",
            "Плановая дата и время окончания",
            "Дата окончания (ГГГГ-ММ-ДД)",
            "Дата окончания",
        )
        if b_start is None or b_end is None:
            # A live rental without a known end remains conservatively busy.
            if status == "в аренде":
                busy_car_ids.add(
                    _normalized_car_identifier(
                        b.get("ID Авто") or b.get("Car ID")
                    )
                )
            continue
        if req_start <= b_end and req_end >= b_start:
            busy_car_ids.add(
                _normalized_car_identifier(
                    b.get("ID Авто") or b.get("Car ID")
                )
            )
    available_cars = []
    for car in all_cars:
        car_id = _normalized_car_identifier(car.get("ID"))
        status = str(car.get("Статус", "") or "").strip().casefold()
        if car_id in busy_car_ids or status in {"в ремонте", "списан"}:
            continue
        available_cars.append(car)
    return available_cars


def _ensure_headers(ws, required_headers: list[str]) -> list[str]:
    _ensure_sheet_width(ws)
    headers = _sheets_read(
        lambda: ws.row_values(1),
        f"reading headers from {getattr(ws, 'title', 'worksheet')}",
    )
    missing_headers = sum(
        header not in headers
        for header in required_headers
    )
    _ensure_column_capacity(
        ws,
        max(TARGET_OPERATIONAL_COLUMNS, len(headers) + missing_headers),
    )
    missing_headers = [
        header for header in required_headers if header not in headers
    ]
    if missing_headers:
        headers.extend(missing_headers)
        _ensure_column_capacity(ws, len(headers))
        _sheets_write(
            lambda: ws.update(
                [headers],
                range_name=f"A1:{_column_letter(len(headers))}1",
                value_input_option="USER_ENTERED",
            ),
            f"updating headers in {getattr(ws, 'title', 'worksheet')}",
        )
    return headers


def _ensure_header_aliases(
    ws,
    required_aliases: list[tuple[str, ...]],
) -> list[str]:
    """Ensure one header from each alias group exists without duplicating it."""
    _ensure_sheet_width(ws)
    headers = _sheets_read(
        lambda: ws.row_values(1),
        f"reading aliases from {getattr(ws, 'title', 'worksheet')}",
    )
    missing_aliases = sum(
        not any(alias in headers for alias in aliases)
        for aliases in required_aliases
    )
    _ensure_column_capacity(ws, len(headers) + missing_aliases)
    missing_headers: list[str] = []
    for aliases in required_aliases:
        if any(alias in headers for alias in aliases):
            continue
        header = aliases[0]
        headers.append(header)
        missing_headers.append(header)
    if missing_headers:
        _ensure_column_capacity(ws, len(headers))
        _sheets_write(
            lambda: ws.update(
                [headers],
                range_name=f"A1:{_column_letter(len(headers))}1",
                value_input_option="USER_ENTERED",
            ),
            f"updating aliases in {getattr(ws, 'title', 'worksheet')}",
        )
    return headers


def _update_existing_headers(
    ws,
    row_number: int,
    fields: list[tuple[tuple[str, ...], object, bool]],
) -> None:
    """Update only columns that are already present in the worksheet."""
    headers = _sheets_read(
        lambda: ws.row_values(1),
        f"reading row headers from {getattr(ws, 'title', 'worksheet')}",
    )
    _ensure_column_capacity(ws, len(headers))
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
        _sheets_write(
            lambda: ws.update_cell(row_number, column_number, value),
            f"updating row {row_number} in {getattr(ws, 'title', 'worksheet')}",
        )


def _records_from_values(
    all_values: list[list[object]],
    sheet_name: str,
) -> list[dict]:
    """Convert raw worksheet rows without gspread header validation."""
    if not all_values:
        return []

    raw_headers = all_values[0]
    headers: list[str] = []
    header_bases: list[str] = []
    occurrences: dict[str, int] = {}
    duplicate_headers: list[str] = []
    blank_headers = 0
    for column_index, raw_header in enumerate(raw_headers, start=1):
        base_header = str(raw_header or "").strip()
        if not base_header:
            blank_headers += 1
            base_header = f"__column_{column_index}"
        occurrences[base_header] = occurrences.get(base_header, 0) + 1
        occurrence = occurrences[base_header]
        if occurrence > 1:
            duplicate_headers.append(base_header)
            header = f"{base_header}__{occurrence}"
        else:
            header = base_header
        headers.append(header)
        header_bases.append(base_header)

    if blank_headers or duplicate_headers:
        logger.warning(
            "Normalized malformed headers in %s: blank=%s duplicates=%s",
            sheet_name,
            blank_headers,
            sorted(set(duplicate_headers)),
        )

    records: list[dict] = []
    for row_number, row in enumerate(all_values[1:], start=2):
        record: dict[str, object] = {
            "__sheet_name__": sheet_name,
            "__row_number__": row_number,
        }
        for column_index, header in enumerate(headers):
            value = row[column_index] if column_index < len(row) else ""
            record[header] = value
            base_header = header_bases[column_index]
            if (
                base_header not in record
                or not str(record[base_header] or "").strip()
            ) and str(value or "").strip():
                record[base_header] = value
            elif base_header not in record:
                record[base_header] = value
        records.append(record)
    return records


def _get_sheet_records_safely(ws, sheet_name: str) -> list[dict]:
    """Read a worksheet without gspread's duplicate/blank-header validation."""
    return _records_from_values(
        _sheets_read(
            ws.get_all_values,
            f"reading {sheet_name}",
        ),
        sheet_name,
    )


def _is_heavy_lux_booking(source: object = "", client_name: object = "") -> bool:
    """Return whether a booking belongs to Heavy Lux for row highlighting."""
    normalized_source = str(source or "").strip().casefold()
    normalized_client = str(client_name or "").strip().casefold()
    return (
        normalized_source == HEAVY_LUX_SOURCE.casefold()
        or "хэви" in normalized_client
    )


def _booking_color_source(
    source: object = "",
    client_name: object = "",
) -> str:
    """Normalize a booking source to one of the two row-color categories."""
    if _is_heavy_lux_booking(source, client_name):
        return HEAVY_LUX_SOURCE
    return "Леон Рентал"


def _booking_source_from_row(
    row: list[object],
    headers: list[object],
) -> str:
    """Determine a booking's color source, preferring the fixed BR column."""
    source = (
        row[BOOKING_SOURCE_COLUMN - 1]
        if len(row) >= BOOKING_SOURCE_COLUMN
        else ""
    )
    if not str(source or "").strip():
        for index, header in enumerate(headers):
            if str(header or "").strip() == "Источник" and index < len(row):
                source = row[index]
    client_name = ""
    client_headers = {
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
    }
    for index, header in enumerate(headers):
        if str(header or "").strip() in client_headers and index < len(row):
            client_name = row[index]
            if str(client_name or "").strip():
                break
    return _booking_color_source(source, client_name)


def _booking_color_end_column(ws, row: list[object] | None = None) -> int:
    """Return the exclusive zero-based end column for a row color range."""
    current_grid_width = int(getattr(ws, "col_count", 0) or 0)
    row_width = len(row or [])
    return max(
        BOOKING_COLOR_MIN_END_COLUMN,
        current_grid_width,
        row_width,
    )


def _booking_color_request(
    sheet_id: int,
    row_index: int,
    source: object,
    *,
    end_column_index: int,
) -> dict:
    """Build one Sheets API repeatCell request for a booking row."""
    if int(row_index) < 2:
        raise ValueError("Booking row index must point below the header row")
    color = (
        HEAVY_LUX_BOOKING_COLOR
        if _booking_color_source(source) == HEAVY_LUX_SOURCE
        else LEON_RENTAL_BOOKING_COLOR
    )
    return {
        "repeatCell": {
            "range": {
                "sheetId": int(sheet_id),
                "startRowIndex": int(row_index) - 1,
                "endRowIndex": int(row_index),
                "startColumnIndex": BOOKING_COLOR_START_COLUMN,
                "endColumnIndex": int(end_column_index),
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": color,
                },
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    }


def colorize_booking_row(
    spreadsheet_id,
    sheet_id,
    row_index,
    source,
) -> None:
    """Color one booking row through Google Sheets spreadsheets.batchUpdate."""
    del spreadsheet_id  # The bound gspread Spreadsheet already targets this file.
    request = _booking_color_request(
        sheet_id,
        row_index,
        source,
        end_column_index=_booking_color_end_column(ws_bookings),
    )
    _sheets_write(
        lambda: sh.batch_update({"requests": [request]}),
        "coloring booking row",
    )


def colorize_existing_booking_rows() -> int:
    """Color every existing booking row in one Google Sheets batchUpdate."""
    all_values = _sheets_read(
        ws_bookings.get_all_values,
        "reading booking rows for coloring",
    )
    if len(all_values) <= 1:
        return 0

    headers = all_values[0]
    end_column_index = max(
        BOOKING_COLOR_MIN_END_COLUMN,
        len(headers),
        *(len(row) for row in all_values[1:]),
    )
    requests = []
    for row_index, row in enumerate(all_values[1:], start=2):
        if not any(str(value or "").strip() for value in row):
            continue
        requests.append(
            _booking_color_request(
                getattr(ws_bookings, "id", 0),
                row_index,
                _booking_source_from_row(row, headers),
                end_column_index=end_column_index,
            )
        )
    if requests:
        _sheets_write(
            lambda: sh.batch_update({"requests": requests}),
            "coloring existing booking rows",
        )
    return len(requests)


BOOKINGS_FINANCIAL_RANGE = "A1:BT1000"
BOOKING_ACCEPTED_RENTAL_COLUMN = 13  # M: Принятая сумма аренды
BOOKING_ACCEPTED_TOTAL_COLUMN = 46  # AT: Фактически принято
BOOKING_SOURCE_COLUMN = 70  # BR: Источник
BOOKING_COLOR_START_COLUMN = 0  # A, zero-based Sheets API index
BOOKING_COLOR_MIN_END_COLUMN = 13  # At least A:M
HEAVY_LUX_BOOKING_COLOR = {
    "red": 0.85,
    "green": 0.95,
    "blue": 0.85,
}
LEON_RENTAL_BOOKING_COLOR = {
    "red": 0.98,
    "green": 0.86,
    "blue": 0.86,
}


def _get_financial_booking_records() -> list[dict]:
    """Read financial booking fields from their fixed legacy columns.

    The bookings sheet has accumulated optional fields over time, so header
    lookup is not reliable for the financial source and accepted amounts.
    Keep the full A:BT read wide enough to preserve those fixed positions.
    """
    all_values = _sheets_read(
        lambda: ws_bookings.get(BOOKINGS_FINANCIAL_RANGE),
        "reading booking financial columns",
    )
    records = _records_from_values(all_values, "Бронирования")
    for row_offset, record in enumerate(records, start=1):
        row = all_values[row_offset] if row_offset < len(all_values) else []
        record["Источник"] = (
            str(
                row[BOOKING_SOURCE_COLUMN - 1]
                if len(row) >= BOOKING_SOURCE_COLUMN
                else ""
            )
            .strip()
        )
        record["__accepted_rental_m"] = (
            row[BOOKING_ACCEPTED_RENTAL_COLUMN - 1]
            if len(row) >= BOOKING_ACCEPTED_RENTAL_COLUMN
            else ""
        )
        record["__accepted_total_at"] = (
            row[BOOKING_ACCEPTED_TOTAL_COLUMN - 1]
            if len(row) >= BOOKING_ACCEPTED_TOTAL_COLUMN
            else ""
        )
        record["__payment_method_o"] = (
            row[15 - 1]
            if len(row) >= 15
            else ""
        )
    return records


def _append_record(ws, values: dict[str, object], required_headers: list[str]) -> None:
    headers = _ensure_headers(ws, required_headers)
    row_values = [values.get(header, "") for header in headers]
    _ensure_column_capacity(ws, len(row_values))
    key_fields: tuple[tuple[str, ...], ...] = ()
    key_values: tuple[object, ...] = ()
    if values.get("Тип уведомления") and values.get("ID Брони"):
        key_fields = (("Тип уведомления",), ("ID Брони",))
        key_values = (
            values["Тип уведомления"],
            values["ID Брони"],
        )
    elif values.get("ID внепланового возврата"):
        key_fields = (("ID внепланового возврата",),)
        key_values = (values["ID внепланового возврата"],)
    elif values.get("ID Брони"):
        key_fields = (("ID Брони",),)
        key_values = (values["ID Брони"],)
    elif values.get("ID Записи"):
        key_fields = (("ID Записи",),)
        key_values = (values["ID Записи"],)
    _append_row_with_recovery(
        ws,
        row_values,
        key_fields=key_fields,
        key_values=key_values,
        operation_name=f"appending to {getattr(ws, 'title', 'worksheet')}",
        value_input_option="USER_ENTERED",
    )


def _update_row_values(
    ws,
    row_number: int,
    headers: list[str],
    updates: dict[str, object],
) -> None:
    """Update several fields in one Sheets request without losing other cells."""
    row_values = _sheets_read(
        lambda: ws.row_values(row_number),
        f"reading row {row_number} from {getattr(ws, 'title', 'worksheet')}",
    )
    if len(row_values) < len(headers):
        row_values.extend([""] * (len(headers) - len(row_values)))
    for header, value in updates.items():
        if header not in headers:
            raise KeyError(f"Column {header!r} was not found")
        row_values[headers.index(header)] = value
    _ensure_column_capacity(ws, len(headers))
    _sheets_write(
        lambda: ws.update(
            [row_values[: len(headers)]],
            range_name=(
                f"A{row_number}:{_column_letter(len(headers))}{row_number}"
            ),
            value_input_option="USER_ENTERED",
        ),
        f"updating row {row_number} in {getattr(ws, 'title', 'worksheet')}",
    )


def _get_or_create_sheet(title: str, headers: list[str]):
    try:
        worksheet = _cached_worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(
            title=title,
            rows=1000,
            cols=max(30, len(headers)),
        )
        _worksheet_cache[title] = worksheet
    _ensure_headers(worksheet, headers)
    return worksheet

def _initialize_worksheets() -> None:
    """Resolve all startup worksheets from one metadata snapshot."""
    global _worksheet_cache, ws_cars, ws_bookings, ws_returns, ws_managers
    global ws_acceptance_acts, ws_expenses, ws_monthly_summary
    global ws_car_profit_summary, ws_notification_state

    _worksheet_cache = {
        worksheet.title: worksheet for worksheet in sh.worksheets()
    }
    _migrate_sheet_titles_from_cache(_worksheet_cache)

    ws_cars = _cached_worksheet("Автомобили")
    _ensure_sheet_width(ws_cars)
    ws_bookings = _get_bookings_sheet()
    ws_returns = _cached_worksheet("Возвраты_Касса")
    ws_managers = _cached_worksheet("Менеджеры")
    try:
        ws_acceptance_acts = _cached_worksheet("Acceptance_Acts")
    except gspread.exceptions.WorksheetNotFound:
        ws_acceptance_acts = sh.add_worksheet(
            title="Acceptance_Acts",
            rows=1000,
            cols=30,
        )
        _worksheet_cache["Acceptance_Acts"] = ws_acceptance_acts
    _ensure_sheet_width(ws_acceptance_acts)
    acceptance_sheet_headers = _sheets_read(
        lambda: ws_acceptance_acts.row_values(1),
        "reading acceptance-act headers",
    )
    missing_acceptance_headers = [
        header
        for header in ACCEPTANCE_ACT_HEADERS
        if header not in acceptance_sheet_headers
    ]
    if missing_acceptance_headers:
        acceptance_sheet_headers.extend(missing_acceptance_headers)
        _ensure_column_capacity(
            ws_acceptance_acts,
            len(acceptance_sheet_headers),
        )
        _sheets_write(
            lambda: ws_acceptance_acts.update(
                [acceptance_sheet_headers],
                range_name=(
                    f"A1:{_column_letter(len(acceptance_sheet_headers))}1"
                ),
                value_input_option="USER_ENTERED",
            ),
            "updating acceptance-act headers",
        )

    ws_expenses = _get_or_create_sheet("Затраты", EXPENSE_HEADERS)
    ws_monthly_summary = _get_or_create_sheet(
        "Сводная_по_месяцам",
        MONTHLY_SUMMARY_HEADERS,
    )
    ws_car_profit_summary = _get_or_create_sheet(
        "Прибыль_по_авто",
        CAR_PROFIT_SUMMARY_HEADERS,
    )
    ws_notification_state = _get_or_create_sheet(
        "Состояние_уведомлений",
        NOTIFICATION_STATE_HEADERS,
    )
def _notification_state_datetime(
    value: object,
    *,
    default: datetime.datetime | None = None,
) -> datetime.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _notification_state_now(
    value: datetime.datetime | None = None,
) -> datetime.datetime:
    current = value or datetime.datetime.now(datetime.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.timezone.utc)
    return current.astimezone(datetime.timezone.utc)


def _cleanup_expired_rental_alert_rows(
    records: list[dict],
    *,
    now: datetime.datetime,
) -> None:
    cutoff = now - datetime.timedelta(
        days=EXPIRING_RENTAL_ALERT_RETENTION_DAYS
    )
    expired_rows = []
    for record in records:
        if (
            str(record.get("Тип уведомления", "")).strip()
            != EXPIRING_RENTAL_ALERT_TYPE
        ):
            continue
        sent_at = _notification_state_datetime(
            record.get("Отправлено в UTC"),
        )
        if sent_at is None or sent_at < cutoff:
            row_number = record.get("__row_number__")
            if row_number:
                expired_rows.append(
                    (
                        int(row_number),
                        str(record.get("Тип уведомления", "")).strip(),
                        str(record.get("ID Брони", "")).strip(),
                        str(record.get("Отправлено в UTC", "")).strip(),
                    )
                )

    # Delete from the bottom up so earlier row numbers remain valid.
    for row_number, alert_type, booking_id, sent_at in sorted(
        expired_rows,
        reverse=True,
    ):
        _delete_notification_state_row_with_recovery(
            row_number,
            alert_type,
            booking_id,
            sent_at,
        )


def _delete_notification_state_row_with_recovery(
    row_number: int,
    alert_type: str,
    booking_id: str,
    sent_at: str,
) -> None:
    """Delete one notification row without repeating a successful delete."""
    for attempt in range(RUNTIME_OPERATION_MAX_RETRIES):
        try:
            ws_notification_state.delete_rows(row_number)
            return
        except Exception as error:
            if not _is_transient_spreadsheet_error(error):
                raise
            remaining = _get_sheet_records_safely(
                ws_notification_state,
                "Состояние_уведомлений",
            )
            still_present = any(
                str(record.get("Тип уведомления", "")).strip() == alert_type
                and str(record.get("ID Брони", "")).strip() == booking_id
                and str(record.get("Отправлено в UTC", "")).strip() == sent_at
                for record in remaining
            )
            if not still_present:
                return
            if attempt == RUNTIME_OPERATION_MAX_RETRIES - 1:
                raise
            logger.warning(
                "Temporary Google Sheets failure during deleting "
                "notification state (reason=%s; attempt %s/%s); retrying",
                _startup_failure_reason(error),
                attempt + 1,
                RUNTIME_OPERATION_MAX_RETRIES,
            )
            time.sleep(
                RUNTIME_RETRY_DELAYS[
                    min(attempt, len(RUNTIME_RETRY_DELAYS) - 1)
                ]
            )


def get_expiring_rental_alerted_booking_ids(
    now: datetime.datetime | None = None,
) -> set[str]:
    """Return persisted one-hour alert keys and remove expired rows."""
    current = _notification_state_now(now)
    records = _get_sheet_records_safely(
        ws_notification_state,
        "Состояние_уведомлений",
    )
    _cleanup_expired_rental_alert_rows(records, now=current)

    cutoff = current - datetime.timedelta(
        days=EXPIRING_RENTAL_ALERT_RETENTION_DAYS
    )
    alerted_booking_ids: set[str] = set()
    for record in records:
        if (
            str(record.get("Тип уведомления", "")).strip()
            != EXPIRING_RENTAL_ALERT_TYPE
        ):
            continue
        sent_at = _notification_state_datetime(
            record.get("Отправлено в UTC"),
        )
        booking_id = str(record.get("ID Брони", "")).strip()
        if sent_at and sent_at >= cutoff and booking_id:
            alerted_booking_ids.add(booking_id)
    return alerted_booking_ids


def mark_expiring_rental_alert_sent(
    booking_id: object,
    sent_at: datetime.datetime | None = None,
) -> None:
    """Persist a completed one-hour alert delivery."""
    normalized_booking_id = str(booking_id or "").strip()
    if not normalized_booking_id:
        return

    current = _notification_state_now(sent_at)
    # Cleanup also runs on the write path, keeping the state bounded when a
    # scheduler check happens to persist a new alert after a long pause.
    get_expiring_rental_alerted_booking_ids(current)
    records = _get_sheet_records_safely(
        ws_notification_state,
        "Состояние_уведомлений",
    )
    headers = _ensure_headers(
        ws_notification_state,
        NOTIFICATION_STATE_HEADERS,
    )
    timestamp = current.isoformat(timespec="seconds")
    for record in records:
        if (
            str(record.get("Тип уведомления", "")).strip()
            == EXPIRING_RENTAL_ALERT_TYPE
            and str(record.get("ID Брони", "")).strip()
            == normalized_booking_id
        ):
            _update_row_values(
                ws_notification_state,
                int(record["__row_number__"]),
                headers,
                {"Отправлено в UTC": timestamp},
            )
            return

    _append_record(
        ws_notification_state,
        {
            "Тип уведомления": EXPIRING_RENTAL_ALERT_TYPE,
            "ID Брони": normalized_booking_id,
            "Отправлено в UTC": timestamp,
        },
        NOTIFICATION_STATE_HEADERS,
    )


def safe_int(value: object) -> int:
    """Convert a Sheets number/currency value to an integer safely."""
    return int(safe_float(value))


def safe_float(value: object) -> float:
    """Convert a Sheets number/currency value to a float safely."""
    if value is None:
        return 0.0
    try:
        clean_value = (
            str(value)
            .replace(" ", "")
            .replace("\xa0", "")
            .replace("₽", "")
            .replace("RUB", "")
            .strip()
        )
        if "," in clean_value and "." in clean_value:
            if clean_value.rfind(",") > clean_value.rfind("."):
                clean_value = clean_value.replace(".", "").replace(",", ".")
            else:
                clean_value = clean_value.replace(",", "")
        elif "," in clean_value:
            comma_parts = clean_value.split(",")
            if len(comma_parts) == 2 and len(comma_parts[1]) <= 2:
                clean_value = ".".join(comma_parts)
            else:
                clean_value = clean_value.replace(",", "")
        return float(clean_value) if clean_value else 0.0
    except (ValueError, TypeError):
        return 0.0


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
    for record in _sheets_read(
        ws_expenses.get_all_records,
        "reading expense months",
    ):
        month = _month_from_record(record, "Дата")
        if month:
            months.add(month)
    return sorted(months, reverse=True)


def get_expenses_for_month(
    month_key: str,
    vehicle_filter: str | None = None,
) -> list[dict]:
    records = _sheets_read(
        ws_expenses.get_all_records,
        "reading expenses",
    )
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
    records = _sheets_read(
        ws.get_all_records,
        f"reading records from {getattr(ws, 'title', 'worksheet')}",
    )
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
        _append_row_with_recovery(
            ws,
            [values.get(header, "") for header in headers],
            key_fields=tuple((header,) for header in key_headers),
            key_values=key_values,
            operation_name=f"appending to {getattr(ws, 'title', 'worksheet')}",
        )
        return
    updates = {
        header: value
        for header, value in values.items()
        if header in headers
    }
    if updates:
        _update_row_values(ws, target_row, headers, updates)


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
        records = _sheets_read(
            ws_expenses.get_all_records,
            "reading recent expenses",
        )
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


def _delete_expense_row_with_recovery(
    row_number: int,
    expense_id: object,
    expected_fingerprint: tuple[str, ...] | None,
    current_record: dict,
) -> None:
    """Delete one expense without repeating a successful row deletion."""
    for attempt in range(RUNTIME_OPERATION_MAX_RETRIES):
        try:
            ws_expenses.delete_rows(row_number)
            return
        except Exception as error:
            if not _is_transient_spreadsheet_error(error):
                raise
            remaining = _sheets_read(
                ws_expenses.get_all_records,
                "checking expense deletion",
            )
            still_present = any(
                str(record.get("ID Записи", "")).strip()
                == str(expense_id).strip()
                and (
                    expected_fingerprint is None
                    or _expense_fingerprint(record) == expected_fingerprint
                )
                for record in remaining
            )
            if not still_present:
                return
            if attempt == RUNTIME_OPERATION_MAX_RETRIES - 1:
                raise
            logger.warning(
                "Temporary Google Sheets failure during deleting expense "
                "(reason=%s; attempt %s/%s); retrying",
                _startup_failure_reason(error),
                attempt + 1,
                RUNTIME_OPERATION_MAX_RETRIES,
            )
            time.sleep(
                RUNTIME_RETRY_DELAYS[
                    min(attempt, len(RUNTIME_RETRY_DELAYS) - 1)
                ]
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
        records = _sheets_read(
            ws_expenses.get_all_records,
            "reading expenses for deletion",
        )
        headers = _ensure_headers(ws_expenses, EXPENSE_HEADERS)
        for row_number, record in enumerate(records, start=2):
            if str(record.get("ID Записи", "")).strip() != str(expense_id).strip():
                continue
            if (
                expected_fingerprint is not None
                and _expense_fingerprint(record) != expected_fingerprint
            ):
                return False
            current_values = _sheets_read(
                lambda: ws_expenses.row_values(row_number),
                f"reading expense row {row_number}",
            )
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
            _delete_expense_row_with_recovery(
                row_number,
                expense_id,
                expected_fingerprint,
                current_record,
            )
            return True
    return False


def get_available_financial_months(
    reference_date: datetime.date | None = None,
) -> list[str]:
    reference_date = reference_date or datetime.date.today()
    current_month = reference_date.strftime("%Y-%m")
    previous_month_date = reference_date.replace(day=1) - datetime.timedelta(days=1)
    months = {current_month, previous_month_date.strftime("%Y-%m")}
    for record in _get_sheet_records_safely(ws_returns, "Возвраты_Касса"):
        month = _month_from_record(
            record,
            "Дата возврата",
            "Дата и время приёма",
        )
        if month:
            months.add(month)
    for record in _sheets_read(
        ws_expenses.get_all_records,
        "reading financial expense months",
    ):
        month = _month_from_record(record, "Дата")
        if month:
            months.add(month)
    return sorted(months, reverse=True)


def normalize_financial_source_filter(source_filter: object = FINANCIAL_SOURCE_ALL) -> str:
    """Normalize the financial report source selector."""
    normalized = str(source_filter or FINANCIAL_SOURCE_ALL).strip().casefold()
    aliases = {
        "all": FINANCIAL_SOURCE_ALL,
        "общий": FINANCIAL_SOURCE_ALL,
        "сводный": FINANCIAL_SOURCE_ALL,
        "leon": FINANCIAL_SOURCE_LEON,
        "леон": FINANCIAL_SOURCE_LEON,
        "леон rental": FINANCIAL_SOURCE_LEON,
        "леон рентал": FINANCIAL_SOURCE_LEON,
        HEAVY_LUX_SOURCE.casefold(): FINANCIAL_SOURCE_HEAVY_LUX,
    }
    try:
        return aliases[normalized]
    except KeyError as error:
        raise ValueError(
            f"Unknown financial source filter: {source_filter!r}"
        ) from error


def get_financial_report(
    month_key: str,
    source_filter: object = FINANCIAL_SOURCE_ALL,
) -> dict:
    source_filter = normalize_financial_source_filter(source_filter)
    context = {
        "sheet": "—",
        "row": "—",
        "column": "—",
        "value": "—",
    }
    try:
        return _build_financial_report(
            month_key,
            context,
            source_filter=source_filter,
        )
    except Exception as error:
        logger.exception(
            "Financial report failed for %s at sheet=%s row=%s "
            "column=%s value=%r: %s",
            month_key,
            context["sheet"],
            context["row"],
            context["column"],
            context["value"],
            error,
        )
        raise


def _build_financial_report(
    month_key: str,
    context: dict[str, object],
    source_filter: str = FINANCIAL_SOURCE_ALL,
) -> dict:
    bookings = _get_financial_booking_records()
    returns = _get_sheet_records_safely(ws_returns, "Возвраты_Касса")
    expenses = _get_sheet_records_safely(ws_expenses, "Затраты")
    cars = _get_sheet_records_safely(ws_cars, "Автомобили")

    def in_month(record: dict, *keys: str) -> bool:
        return _month_from_record(record, *keys) == month_key

    def contains_month(record: dict, *keys: str) -> bool:
        return any(
            month_key in str(record.get(key) or "")
            for key in keys
        )

    def cell(record: dict, *keys: str, default: object = "") -> object:
        for key in keys:
            if key not in record:
                continue
            value = record.get(key)
            context.update(
                sheet=record.get("__sheet_name__", "—"),
                row=record.get("__row_number__", "—"),
                column=key,
                value=value,
            )
            if str(value if value is not None else "").strip():
                return value
        if keys:
            context.update(
                sheet=record.get("__sheet_name__", "—"),
                row=record.get("__row_number__", "—"),
                column=keys[0],
                value=default,
            )
        return default

    def normalize_payment_method(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if (
            not normalized
            or "без оплат" in normalized
            or "не оплач" in normalized
        ):
            return "Без оплаты"
        if "сбп" in normalized:
            return "СБП"
        if (
            "безнал" in normalized
            or "карта" in normalized
            or "перевод" in normalized
        ):
            return "Безнал"
        if "нал" in normalized or "cash" in normalized:
            return "Наличные"
        return "Безнал"

    def first_matching_value(record: dict, fragment: str) -> object:
        fragment = fragment.casefold()
        for key, value in record.items():
            if key.startswith("__"):
                continue
            if fragment in str(key).casefold():
                context.update(
                    sheet=record.get("__sheet_name__", "—"),
                    row=record.get("__row_number__", "—"),
                    column=key,
                    value=value,
                )
                if str(value if value is not None else "").strip():
                    return value
        return ""

    def payment_parts(record: dict) -> list[tuple[float, str]]:
        issuance_amount = safe_float(
            cell(
                record,
                "Оплачено при выдаче (RUB)",
                "Фактически оплачено при выдаче (RUB)",
                default="",
            )
        )
        issuance_method = normalize_payment_method(
            cell(
                record,
                "Способ оплаты при выдаче",
                "Способ оплаты аренды",
                "Способ оплаты",
                default="Без оплаты",
            )
        )
        canonical_income = cell(
            record,
            "Итого приход (RUB)",
            "Итого приход",
            default="",
        )
        if str(canonical_income if canonical_income is not None else "").strip():
            amount = safe_float(canonical_income)
            method = normalize_payment_method(
                cell(
                    record,
                    "Способ оплаты",
                    "Способ оплаты доплаты",
                    "Способ оплаты аренды",
                    default="Без оплаты",
                )
            )
            parts = [(amount, method)] if amount else []
            if issuance_amount:
                parts.append((issuance_amount, issuance_method))
            return parts

        rental_keys = (
            "Фактически оплачено за аренду (RUB)",
            "Сумма аренды получена (RUB)",
            "Принятая сумма аренды (RUB)",
        )
        top_up_keys = (
            "Полученная доплата (RUB)",
            "Полученная доплата",
        )
        rental_paid = cell(record, *rental_keys, default="")
        top_up = cell(record, *top_up_keys, default="")
        has_actual_payment_fields = any(
            str(
                cell(record, key, default="")
            ).strip()
            for key in (*rental_keys, *top_up_keys)
        )
        if has_actual_payment_fields:
            parts: list[tuple[float, str]] = []
            if issuance_amount:
                parts.append((issuance_amount, issuance_method))
            rental_amount = safe_float(rental_paid)
            top_up_amount = safe_float(top_up)
            if rental_amount:
                parts.append(
                    (
                        rental_amount,
                        normalize_payment_method(
                            cell(
                                record,
                                "Способ оплаты аренды",
                                "Способ оплаты",
                                default="Без оплаты",
                            )
                        ),
                    )
                )
            if top_up_amount:
                parts.append(
                    (
                        top_up_amount,
                        normalize_payment_method(
                            cell(
                                record,
                                "Способ оплаты доплаты",
                                "Способ оплаты",
                                default="Без оплаты",
                            )
                        ),
                    )
                )
            return parts

        accepted_amount = safe_float(
            cell(
                record,
                "Принятая сумма аренды (RUB)",
                "Сумма брони (RUB)",
                "Стоимость аренды (RUB)",
                "Сумма аренды (RUB)",
                default="",
            )
        )
        if not accepted_amount:
            accepted_amount = safe_float(
                cell(
                    record,
                    "Задаток/Предоплата",
                    "Задаток (RUB)",
                    "Предоплата",
                    default="",
                )
            )
        return [
            (
                accepted_amount,
                normalize_payment_method(
                    cell(
                        record,
                        "Способ оплаты",
                        "Способ оплаты аренды",
                        "Способ оплаты задатка",
                        default="Без оплаты",
                    )
                ),
            )
        ] if accepted_amount else []

    def return_revenue(record: dict) -> float:
        return sum(amount for amount, _ in payment_parts(record))

    def booking_source(record: dict) -> str:
        source = str(record.get("Источник", "") or "").strip().casefold()
        client = str(
            record.get("ФИО Клиента")
            or record.get("Клиент")
            or record.get("Клиент ФИО")
            or ""
        ).strip().casefold()
        if (
            source == HEAVY_LUX_SOURCE
            or "heavy" in client
            or "хэви" in client
            or "хеви" in client
        ):
            return FINANCIAL_SOURCE_HEAVY_LUX
        return FINANCIAL_SOURCE_LEON

    def source_commission_rate(record: dict) -> float:
        return (
            HEAVY_LUX_COMMISSION_RATE
            if booking_source(record) == FINANCIAL_SOURCE_HEAVY_LUX
            else 0.0
        )

    def net_income_for_payment(record: dict, amount: float) -> float:
        return amount * (1 - source_commission_rate(record))

    def booking_payment_parts(record: dict) -> list[tuple[float, str]]:
        status = str(record.get("Статус", "") or "").strip().casefold()
        if status not in {"завершена", "в аренде"}:
            return []
        accepted_amount = safe_float(record.get("__accepted_rental_m"))
        if accepted_amount <= 0:
            accepted_amount = safe_float(record.get("__accepted_total_at"))
        if not accepted_amount:
            return []
        method = normalize_payment_method(record.get("__payment_method_o"))
        if method == "Без оплаты":
            # A positive accepted amount in M/AT is an actual received
            # payment even when the legacy payment-method cell was not
            # updated. Keep it in revenue and classify it as cash.
            method = "Наличные"
        return [
            (
                accepted_amount,
                method,
            )
        ]

    def car_id(car: dict) -> str:
        return str(
            car.get("ID Авто")
            or car.get("ID")
            or car.get("Car ID")
            or ""
        ).strip()

    def normalized_identifier(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized.endswith(".0"):
            normalized = normalized[:-2]
        return normalized

    def booking_car_id(record: dict) -> str:
        return normalized_identifier(
            record.get("ID Авто")
            or record.get("ID")
            or record.get("Car ID")
            or ""
        )

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
            or record.get("Госномер")
            or record.get("Номер авто")
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

    def financial_source_matches(record: dict) -> bool:
        return (
            source_filter == FINANCIAL_SOURCE_ALL
            or booking_source(record) == source_filter
        )

    month_returns = [
        record
        for record in returns
        if in_month(record, "Дата возврата", "Дата и время приёма")
    ]
    month_expenses = [
        record
        for record in expenses
        if contains_month(record, "Дата")
    ]
    month_bookings = [
        record
        for record in bookings
        if str(cell(record, "Статус", default="")).strip().casefold()
        in {"в аренде", "завершена"}
        and contains_month(
            record,
            "Дата выдачи",
            "Дата выдачи (ГГГГ-ММ-ДД)",
            "Дата начала",
            "Дата начала (ГГГГ-ММ-ДД)",
        )
        and financial_source_matches(record)
    ]

    source_car_identifiers: set[str] = set()
    if source_filter != FINANCIAL_SOURCE_ALL:
        for record in month_bookings:
            for value in (
                booking_car_id(record),
                record.get("Автомобиль"),
                record.get("Авто"),
                record.get("Название авто"),
                record.get("Марка и Модель"),
                record.get("Госномер"),
                record.get("Номер авто"),
                record.get("Номер"),
            ):
                normalized = str(value or "").strip().casefold()
                if normalized:
                    source_car_identifiers.add(normalized)
        for car in cars:
            normalized_identifiers = {
                str(value or "").strip().casefold()
                for value in (car_id(car), car_plate(car), car_name(car))
                if str(value or "").strip()
            }
            if normalized_identifiers & source_car_identifiers:
                source_car_identifiers.update(normalized_identifiers)

    def expense_matches_source(record: dict) -> bool:
        if source_filter == FINANCIAL_SOURCE_ALL:
            return True
        if is_general_expense(record):
            return False
        vehicle = expense_vehicle(record)
        if not vehicle or not source_car_identifiers:
            return False
        return any(
            (
                identifier == vehicle
                if identifier.isdigit()
                else (
                    identifier == vehicle
                    or identifier in vehicle
                    or vehicle in identifier
                )
            )
            for identifier in source_car_identifiers
        )

    payment_breakdown = {
        "Наличные": 0.0,
        "Безнал": 0.0,
        "СБП": 0.0,
    }
    gross_income = 0.0
    partner_fee = 0.0
    for record in month_bookings:
        for amount, method in booking_payment_parts(record):
            partner_fee += amount * source_commission_rate(record)
            gross_income += amount
            payment_breakdown[method] = (
                payment_breakdown.get(method, 0.0) + amount
            )
    direct_expenses = int(round(sum(
        safe_float(cell(record, "Сумма (RUB)", default=""))
        for record in month_expenses
        if (
            not is_general_expense(record)
            and expense_matches_source(record)
        )
    )))
    general_expenses = int(round(sum(
        safe_float(cell(record, "Сумма (RUB)", default=""))
        for record in month_expenses
        if (
            source_filter == FINANCIAL_SOURCE_ALL
            and is_general_expense(record)
        )
    )))
    ksenia_salary = 0
    total_expenses = direct_expenses + general_expenses
    company_revenue = gross_income - partner_fee
    company_profit = company_revenue - total_expenses

    car_gross_income: dict[str, float] = {}
    car_partner_fee: dict[str, float] = {}
    car_income: dict[str, float] = {}
    car_booking_count: dict[str, int] = {}
    car_mileage: dict[str, int] = {}
    for record in month_bookings:
        car_key = booking_car_id(record)
        if car_key:
            car_booking_count[car_key] = car_booking_count.get(car_key, 0) + 1
            for amount, _ in booking_payment_parts(record):
                fee = amount * source_commission_rate(record)
                car_gross_income[car_key] = (
                    car_gross_income.get(car_key, 0) + amount
                )
                car_partner_fee[car_key] = (
                    car_partner_fee.get(car_key, 0) + fee
                )
                car_income[car_key] = car_income.get(car_key, 0) + (
                    amount - fee
                )

    car_expenses: dict[str, int] = {}
    car_expenses_by_category: dict[str, dict[str, int]] = {}
    for record in month_expenses:
        if not expense_matches_source(record):
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
        car_key = normalized_identifier(stable_car_id)
        display_name = car_name(car)
        if (
            source_filter != FINANCIAL_SOURCE_ALL
            and car_key not in source_car_identifiers
            and plate_key not in source_car_identifiers
        ):
            continue
        car_gross = car_gross_income.get(car_key, 0)
        car_fee = car_partner_fee.get(car_key, 0)
        income = car_income.get(car_key, 0)
        costs = car_expenses.get(plate_key, 0)
        car_row = {
            "Месяц (ГГГГ-ММ)": month_key,
            "ID Авто": stable_car_id,
            "Марка и Модель": display_name,
            "Госномер": plate_display,
            "Количество сдач": car_booking_count.get(car_key, 0),
            "Приход / Выручка (RUB)": income,
            "Затраты на авто (RUB)": costs,
            "Расходы по статьям": dict(
                sorted(car_expenses_by_category.get(plate_key, {}).items())
            ),
            "Пробег за месяц (км)": car_mileage.get(plate_key, 0),
            "Чистая прибыль авто (RUB)": income - costs,
        }
        if source_filter == FINANCIAL_SOURCE_HEAVY_LUX:
            car_row.update(
                {
                    "Валовый сбор (RUB)": car_gross,
                    "Комиссия heavy_lux_cr (RUB)": car_fee,
                    "Доля Leon Rental (70%) (RUB)": income,
                }
            )
        car_rows.append(car_row)

    if source_filter == FINANCIAL_SOURCE_ALL:
        try:
            _upsert_record(
                ws_monthly_summary,
                "Месяц (ГГГГ-ММ)",
                month_key,
                {
                    "Месяц (ГГГГ-ММ)": month_key,
                    "Общий приход (RUB)": gross_income,
                    "Партнёрская комиссия heavy_lux_cr (RUB)": partner_fee,
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
        except gspread.exceptions.APIError as error:
            if "429" not in str(error):
                raise
            logger.warning(
                "Financial report %s calculated successfully, but summary "
                "persistence was skipped because Google Sheets write quota "
                "was exceeded: %s",
                month_key,
                error,
            )
    return {
        "month": month_key,
        "source_filter": source_filter,
        "booking_count": len(month_bookings),
        "gross_income": int(round(gross_income)),
        "company_revenue": int(round(company_revenue)),
        "client_payments": int(round(gross_income)),
        "partner_fee": int(round(partner_fee)),
        "direct_expenses": direct_expenses,
        "general_expenses": general_expenses,
        "ksenia_salary": ksenia_salary,
        "total_expenses": total_expenses,
        "company_profit": company_profit,
        "payment_breakdown": {
            method: int(round(amount))
            for method, amount in payment_breakdown.items()
        },
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
    source: str = "Леон Рентал",
):
    try:
        bookings_sheet = _get_bookings_sheet()
        records = _get_sheet_records_safely(
            bookings_sheet,
            "Бронирования",
        )
        rented_ids, rented_plates = _open_rental_car_identifiers(records)
        normalized_car_id = _normalized_car_identifier(car_id)
        normalized_plate = ""
        if (
            normalized_car_id in rented_ids
            or (normalized_plate and normalized_plate in rented_plates)
        ):
            raise ValueError(
                f"Car {car_id} is already rented and has no completed return act"
            )
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
            "",
            "",
            "",
            f"{str(end_date or '')} 23:59",
            str(source or ""),
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
                "Дата выдачи": "",
                "Время выдачи": "",
                "Время возврата": "",
                "Источник": str(source or ""),
            }
            row_to_append = [
                legacy_values.get(header, "")
                for header in headers
            ]
        _append_row_with_recovery(
            bookings_sheet,
            row_to_append,
            key_fields=(("ID Брони", "ID", "Booking ID"),),
            key_values=(new_booking_id,),
            operation_name=f"creating booking {new_booking_id}",
            value_input_option="USER_ENTERED",
        )
        booking_row = (
            int(records[-1]["__row_number__"]) + 1
            if records and records[-1].get("__row_number__")
            else len(records) + 2
        )
        try:
            colorize_booking_row(
                getattr(sh, "id", SPREADSHEET_NAME),
                getattr(bookings_sheet, "id", 0),
                booking_row,
                _booking_color_source(source, client_name),
            )
        except Exception:
            logger.exception(
                "Booking %s was saved but row coloring failed",
                new_booking_id,
            )
        return new_booking_id
    except Exception as error:
        logger.error(
            "Booking creation failed: reason=%s",
            _startup_failure_reason(error),
        )
        raise


def get_booking_by_id(booking_id: int | str) -> dict | None:
    """Return one booking by ID using the safe legacy-sheet reader."""
    normalized_id = _normalized_car_identifier(booking_id)
    for booking in _get_sheet_records_safely(ws_bookings, "Бронирования"):
        candidate = booking.get("ID Брони") or booking.get("ID") or ""
        if _normalized_car_identifier(candidate) == normalized_id:
            return dict(booking)
    return None


def add_public_booking(
    *,
    car_id: int | str,
    car_name: str,
    start_date: str,
    end_date: str,
    client_name: str,
    phone: str,
    client_telegram_id: int,
    price: int,
    prepayment: int,
    delivery_address: str,
    delivery_price: int,
    receipt_payment_method: str = "Перевод (СБП)",
    status: str = "Ожидает подтверждения",
    source: str = "Сайт",
) -> int:
    """Create a website booking request after rechecking date availability."""
    available_cars = get_available_cars(start_date, end_date)
    normalized_car_id = _normalized_car_identifier(car_id)
    if not any(
        _normalized_car_identifier(car.get("ID")) == normalized_car_id
        for car in available_cars
    ):
        raise ValueError(
            "The selected car is no longer available for the requested dates"
        )
    return add_new_booking(
        car_id=int(str(car_id).replace(" ", "").removesuffix(".0")),
        car_name=car_name,
        start_date=start_date,
        end_date=end_date,
        client_name=client_name,
        phone=phone,
        price=price,
        manager_id=client_telegram_id,
        manager_name="Клиент с сайта",
        status=status,
        prepayment=prepayment,
        prepayment_pay_type=receipt_payment_method,
        pay_type=receipt_payment_method,
        delivery_address=delivery_address,
        delivery_price=delivery_price,
        source=source,
    )


def update_public_booking_status(
    booking_id: int | str,
    status: str,
) -> dict:
    """Atomically update a pending public booking and return its row data."""
    try:
        all_values = _sheets_read(
            ws_bookings.get_all_values,
            f"reading booking {booking_id} for status update",
        )
        if not all_values:
            raise ValueError("Бронирования sheet is empty")
        headers = all_values[0]
        status_column = headers.index("Статус") if "Статус" in headers else None
        if status_column is None:
            raise ValueError("Бронирования sheet has no Статус header")
        normalized_id = _normalized_car_identifier(booking_id)
        target_row = None
        current_status = ""
        target_values: list[str] = []
        for row_number, row in enumerate(all_values[1:], start=2):
            candidate = row[0] if row else ""
            if _normalized_car_identifier(candidate) == normalized_id:
                target_row = row_number
                target_values = list(row)
                current_status = (
                    str(row[status_column]).strip()
                    if status_column < len(row)
                    else ""
                )
                break
        if target_row is None:
            raise ValueError(f"Booking {booking_id} was not found")
        if current_status not in {"Ожидает подтверждения", "Заявка с сайта/бота"}:
            raise ValueError(
                f"Booking {booking_id} is no longer pending "
                f"(status={current_status})"
            )
        _sheets_write(
            lambda: ws_bookings.update_cell(
                target_row,
                status_column + 1,
                status,
            ),
            f"updating booking {booking_id} status",
        )
        target_values.extend([""] * max(0, len(headers) - len(target_values)))
        target_values[status_column] = status
        return dict(zip(headers, target_values))
    except Exception:
        logger.exception(
            "Failed to update public booking %s to %s",
            booking_id,
            status,
        )
        raise


def get_available_cars_for_direct_issuance() -> list[dict]:
    """Return every car with open-booking status taking precedence."""
    cars = _sheets_read(
        ws_cars.get_all_records,
        "reading cars for direct issuance",
    )
    bookings = _get_sheet_records_safely(ws_bookings, "Бронирования")
    rented_ids, rented_plates = _open_rental_car_identifiers(bookings)
    result = []
    for car in cars:
        enriched = dict(car)
        car_id = _normalized_car_identifier(car.get("ID"))
        car_plate = str(
            car.get("Госномер")
            or car.get("Номер авто")
            or car.get("Номер")
            or ""
        ).strip().casefold()
        is_rented = car_id in rented_ids or car_plate in rented_plates
        if is_rented:
            enriched["Статус"] = "В аренде"
            enriched["__open_rental__"] = True
        else:
            enriched["__open_rental__"] = False
        result.append(enriched)
    return result


def update_car_status(
    car_id: str = "",
    plate: str = "",
    status: str = "Свободен",
    mileage: int | None = None,
) -> None:
    """Update a car's status and, when provided, its current mileage."""
    _ensure_headers(ws_cars, ["Статус"])
    values = _sheets_read(
        ws_cars.get_all_values,
        "reading cars for status update",
    )
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
        if (
            car_id
            and _normalized_car_identifier(row_id)
            == _normalized_car_identifier(car_id)
        ) or (
            plate
            and row_plate.casefold() == str(plate).strip().casefold()
        ):
            target_row = row_number
            break
    if target_row is None:
        raise ValueError(
            f"Car was not found (ID={car_id!r}, plate={plate!r})"
        )

    if "Статус" not in headers:
        raise ValueError("Автомобили sheet has no Статус header after repair")
    car_updates = {"Статус": status}
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
            car_updates[mileage_header] = mileage
    _update_row_values(ws_cars, target_row, headers, car_updates)


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
    planned_end_at: str = "",
    source: str = "Леон Рентал",
) -> int:
    """Create an immediately active booking and mark its car as rented."""
    with BOOKINGS_LOCK:
        bookings_sheet = _get_bookings_sheet()
        records = _get_sheet_records_safely(
            bookings_sheet,
            "Бронирования",
        )
        existing_booking = next(
            (
                record
                for record in records
                if str(record.get("Статус", "")).strip() == "В аренде"
                and _normalized_car_identifier(
                    record.get("ID Авто")
                    or record.get("Car ID")
                    or record.get("ID")
                )
                == _normalized_car_identifier(car_id)
                and str(
                    record.get("ФИО Клиента")
                    or record.get("Клиент")
                    or record.get("Клиент ФИО")
                    or ""
                ).strip()
                == str(client_name or "").strip()
                and str(
                    record.get("Дата окончания")
                    or record.get("Дата окончания (ГГГГ-ММ-ДД)")
                    or ""
                ).strip()
                == str(end_date or "").strip()
                and safe_int(
                    record.get("Сумма брони (RUB)")
                    or record.get("Цена")
                    or record.get("Стоимость")
                    or 0
                )
                == int(price)
            ),
            None,
        )
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
        existing_booking_id = (
            existing_booking.get("ID Брони")
            or existing_booking.get("ID")
            or existing_booking.get("Booking ID")
            if existing_booking
            else None
        )
        booking_id = (
            int(str(existing_booking_id).replace(" ", ""))
            if existing_booking_id is not None
            else max(booking_ids, default=100) + 1
        )
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
            ("Дата и время выдачи",),
            ("Фактическая дата выдачи", "Дата выдачи"),
            ("Пробег при выдаче (км)", "Начальный пробег"),
            ("Принятая сумма аренды (RUB)", "Фактически оплачено за аренду (RUB)"),
            ("Залог (RUB)", "Внесенный залог", "Страховой залог"),
            ("Способ оплаты", "Способ оплаты аренды"),
            ("Способ оплаты залога", "Способ оплаты страхового залога"),
            ("Плановое окончание", "Плановая дата и время окончания"),
            ("Время выдачи",),
            ("Время возврата",),
            ("Источник",),
        ]
        headers = _ensure_header_aliases(bookings_sheet, required_headers)
        issued_parts = str(issued_at or "").replace("T", " ").split()
        issue_date = issued_parts[0] if issued_parts else today
        issue_time = issued_parts[1][:5] if len(issued_parts) > 1 else "23:59"
        planned_end_at = planned_end_at or f"{end_date} 23:59"
        planned_parts = str(planned_end_at).replace("T", " ").split()
        return_time = planned_parts[1][:5] if len(planned_parts) > 1 else "23:59"
        values = {
            "ID Брони": booking_id,
            "ID": booking_id,
            "Booking ID": booking_id,
            "ID Авто": car_id,
            "Car ID": car_id,
            "Автомобиль": car_name,
            "Авто": car_name,
            "Название авто": car_name,
            "Дата начала (ГГГГ-ММ-ДД)": issue_date,
            "Дата начала": issue_date,
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
            "Фактическая дата выдачи": issue_date,
            "Дата выдачи": issue_date,
            "Время выдачи": issue_time,
            "Время возврата": return_time,
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
            "Плановое окончание": planned_end_at,
            "Плановая дата и время окончания": planned_end_at,
            "Источник": source,
        }
        if existing_booking is None:
            _append_row_with_recovery(
                bookings_sheet,
                [values.get(header, "") for header in headers],
                key_fields=(("ID Брони", "ID", "Booking ID"),),
                key_values=(booking_id,),
                operation_name=f"creating direct issuance {booking_id}",
                value_input_option="USER_ENTERED",
            )
            booking_row = len(records) + 2
        else:
            booking_row = int(existing_booking["__row_number__"])
        try:
            colorize_booking_row(
                getattr(sh, "id", SPREADSHEET_NAME),
                getattr(bookings_sheet, "id", 0),
                booking_row,
                _booking_color_source(source, client_name),
            )
        except Exception:
            logger.exception(
                "Direct issuance booking %s was saved but row coloring failed",
                booking_id,
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


def _attach_car_references(bookings: list[dict]) -> list[dict]:
    """Attach справочник values once for a list of booking records."""
    cars = _sheets_read(
        ws_cars.get_all_records,
        "reading cars for cancellable bookings",
    )
    enriched_bookings = []
    for booking in bookings:
        enriched = dict(booking)
        fallback_name = str(booking.get("Автомобиль") or "—").strip() or "—"
        enriched["__car_name__"] = fallback_name
        enriched["__car_plate__"] = ""
        for car in cars:
            if str(booking.get("ID Авто")).strip() == str(car.get("ID")).strip():
                enriched["__car_name__"] = (
                    car.get("Марка и Модель") or fallback_name
                )
                enriched["__car_plate__"] = (
                    str(car.get("Госномер", "Без номера") or "").strip()
                )
                break
        enriched_bookings.append(enriched)
    return enriched_bookings


def get_active_rentals():
    all_bookings = _get_sheet_records_safely(
        ws_bookings,
        "Бронирования",
    )
    active_bookings = [
        booking
        for booking in all_bookings
        if booking.get("Статус") in ["В аренде", "Подтверждена"]
    ]
    return _attach_car_references(active_bookings)


def get_partner_active_rentals(keyword: str) -> list[dict]:
    """Return only active rentals belonging to the requested partner source."""
    return [
        booking
        for booking in get_partner_bookings(keyword)
        if booking.get("Статус") in {"В аренде", "Подтверждена"}
    ]


def get_returnable_rentals():
    all_bookings = _get_sheet_records_safely(
        ws_bookings,
        "Бронирования",
    )
    return [b for b in all_bookings if b.get("Статус") == "В аренде"]


def update_booking_planned_end(
    booking_id: int | str,
    planned_end_at: str,
) -> None:
    """Update the planned return time for an active booking."""
    with BOOKINGS_LOCK:
        records = _get_sheet_records_safely(ws_bookings, "Бронирования")
        booking_row = None
        current_status = ""
        for row_number, booking in enumerate(records, start=2):
            current_id = (
                booking.get("ID Брони")
                or booking.get("ID")
                or booking.get("Booking ID")
                or ""
            )
            normalized_id = str(current_id).strip().removesuffix(".0")
            if normalized_id == str(booking_id).strip().removesuffix(".0"):
                booking_row = row_number
                current_status = str(booking.get("Статус", "")).strip()
                break
        if booking_row is None:
            raise ValueError(f"Booking {booking_id} was not found")
        if current_status != "В аренде":
            raise ValueError(
                f"Booking {booking_id} cannot be extended from status "
                f"{current_status}"
            )

        headers = _ensure_headers(
            ws_bookings,
            ["Плановое окончание"],
        )
        updates = {"Плановое окончание": planned_end_at}
        if "Плановая дата и время окончания" in headers:
            updates["Плановая дата и время окончания"] = planned_end_at
        planned_parts = str(planned_end_at).split()
        if "Время возврата" in headers and len(planned_parts) > 1:
            updates["Время возврата"] = planned_parts[1][:5]
        _update_row_values(ws_bookings, booking_row, headers, updates)


def get_confirmed_bookings():
    all_bookings = _get_sheet_records_safely(
        ws_bookings,
        "Бронирования",
    )
    return [b for b in all_bookings if b.get("Статус") == "Подтверждена"]


def issue_booking(
    booking_id: int,
    start_mileage: int,
    insurance_deposit: int,
    insurance_deposit_pay_type: str,
    manager_id: int,
    issued_at: str,
    planned_end_at: str = "",
) -> None:
    records = _get_sheet_records_safely(ws_bookings, "Бронирования")
    booking_row = None
    current_status = ""
    selected_booking: dict = {}
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
            selected_booking = booking
            break
    if booking_row is None:
        raise ValueError(f"Booking {booking_id} was not found")
    if current_status != "Подтверждена":
        raise ValueError(
            f"Booking {booking_id} cannot be issued from status {current_status}"
        )
    rented_ids, rented_plates = _open_rental_car_identifiers(records)
    selected_car_id = _normalized_car_identifier(
        selected_booking.get("ID Авто") or selected_booking.get("Car ID")
    )
    selected_plate = str(
        selected_booking.get("Госномер")
        or selected_booking.get("Номер авто")
        or selected_booking.get("Номер")
        or ""
    ).strip().casefold()
    if selected_car_id in rented_ids or selected_plate in rented_plates:
        raise ValueError(
            f"Car for booking {booking_id} is already rented and has no "
            "completed return act"
        )

    issued_parts = str(issued_at or "").replace("T", " ").split()
    issue_date = issued_parts[0] if issued_parts else ""
    issue_time = issued_parts[1][:5] if len(issued_parts) > 1 else ""
    planned_end_at = planned_end_at or ""
    planned_parts = str(planned_end_at).replace("T", " ").split()
    return_time = planned_parts[1][:5] if len(planned_parts) > 1 else ""
    _ensure_header_aliases(
        ws_bookings,
        [
            ("Дата и время выдачи",),
            ("Фактическая дата выдачи", "Дата выдачи"),
            ("Время выдачи",),
            ("Время возврата",),
            ("Плановое окончание", "Плановая дата и время окончания"),
        ],
    )
    _update_existing_headers(
        ws_bookings,
        booking_row,
        [
            (("Статус",), "В аренде", True),
            (
                ("Дата и время выдачи", "Фактическая дата выдачи", "Дата выдачи"),
                issued_at,
                True,
            ),
            (
                ("Фактическая дата выдачи", "Дата выдачи"),
                issue_date,
                True,
            ),
            (("Время выдачи",), issue_time, True),
            (("Время возврата",), return_time, True),
            (
                ("Плановое окончание", "Плановая дата и время окончания"),
                planned_end_at,
                True,
            ),
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
    update_car_status(
        car_id=selected_car_id,
        plate=selected_plate,
        status="В аренде",
    )


def get_active_cars():
    all_cars = _sheets_read(ws_cars.get_all_records, "reading active cars")
    return [car for car in all_cars if car.get("Статус") == "Активен"]


def get_cancellable_cars() -> list[dict]:
    """Return unique cars that have at least one confirmed booking.

    The bookings sheet is authoritative here. A car's current operational
    status must not hide future confirmed reservations from cancellation.
    """
    bookings = [
        booking
        for booking in _get_sheet_records_safely(
            ws_bookings,
            "Бронирования",
        )
        if str(booking.get("Статус", "")).strip() == "Подтверждена"
    ]
    if not bookings:
        return []

    cars = _sheets_read(
        ws_cars.get_all_records,
        "reading cars for cancellable bookings",
    )
    cars_by_id = {
        _normalized_car_identifier(
            car.get("ID") or car.get("ID Авто") or car.get("Car ID")
        ): dict(car)
        for car in cars
        if _normalized_car_identifier(
            car.get("ID") or car.get("ID Авто") or car.get("Car ID")
        )
    }
    unique_cars: dict[str, dict] = {}
    for booking in bookings:
        raw_car_id = (
            booking.get("ID Авто")
            or booking.get("ID")
            or booking.get("Car ID")
            or ""
        )
        car_id = _normalized_car_identifier(raw_car_id)
        car = dict(cars_by_id.get(car_id, {}))
        booking_name = str(
            booking.get("Автомобиль")
            or booking.get("Авто")
            or booking.get("Название авто")
            or booking.get("Марка и Модель")
            or ""
        ).strip()
        booking_plate = str(
            booking.get("Госномер")
            or booking.get("Номер авто")
            or booking.get("Номер")
            or ""
        ).strip()
        if not car:
            car = {
                "ID": raw_car_id,
                "Марка и Модель": booking_name or "Автомобиль",
                "Госномер": booking_plate,
            }
        elif booking_name and not any(
            str(car.get(field) or "").strip()
            for field in (
                "Марка и Модель",
                "Марка и модель",
                "Марка Модель",
                "Название",
                "Авто",
                "Модель",
            )
        ):
            car["Марка и Модель"] = booking_name
        if booking_plate and not any(
            str(car.get(field) or "").strip()
            for field in ("Госномер", "Номер авто", "Номер", "Plate")
        ):
            car["Госномер"] = booking_plate

        unique_key = car_id or (
            f"{str(car.get('Марка и Модель') or '').strip().casefold()}|"
            f"{str(car.get('Госномер') or '').strip().casefold()}"
        )
        unique_cars.setdefault(unique_key, car)
    return list(unique_cars.values())


def get_all_cars():
    return _sheets_read(ws_cars.get_all_records, "reading all cars")


def _contains_car_keyword(
    record: dict,
    keyword: str,
    fields: tuple[str, ...],
) -> bool:
    normalized_keyword = str(keyword or "").strip().casefold()
    if not normalized_keyword:
        return False
    return any(
        normalized_keyword in str(record.get(field, "") or "").casefold()
        for field in fields
    )


def get_partner_cars(keyword: str) -> list[dict]:
    """Return cars matching a partner make/model keyword or source."""
    return [
        dict(car)
        for car in _sheets_read(
            ws_cars.get_all_records,
            "reading partner cars",
        )
        if _contains_car_keyword(
            car,
            keyword,
            (
                "Марка и Модель",
                "Марка и модель",
                "Марка Модель",
                "Название",
                "Авто",
                "Модель",
                "Источник",
                "Партнёр",
                "Партнер",
                "Канал",
            ),
        )
    ]


def get_partner_bookings(keyword: str) -> list[dict]:
    """Return bookings whose car name or source contains the partner keyword."""
    bookings = [
        dict(booking)
        for booking in _get_sheet_records_safely(
            ws_bookings,
            "Бронирования",
        )
        if _contains_car_keyword(
            booking,
            keyword,
            (
                "Автомобиль",
                "Авто",
                "Название авто",
                "Марка и Модель",
                "Модель",
                "Источник",
                "Партнёр",
                "Партнер",
                "Канал",
            ),
        )
    ]
    return _attach_car_references(bookings)


def get_partner_expenses(keyword: str) -> list[dict]:
    """Return expenses tied to cars matching the partner keyword."""
    partner_cars = get_partner_cars(keyword)
    normalized_identifiers = {str(keyword or "").strip().casefold()}
    car_fields = (
        "Марка и Модель",
        "Марка и модель",
        "Марка Модель",
        "Название",
        "Авто",
        "Модель",
        "Госномер",
        "Номер авто",
        "Номер",
        "Plate",
    )
    for car in partner_cars:
        normalized_identifiers.update(
            str(car.get(field, "") or "").strip().casefold()
            for field in car_fields
            if str(car.get(field, "") or "").strip()
        )

    expenses = []
    for expense in _sheets_read(
        ws_expenses.get_all_records,
        "reading partner expenses",
    ):
        vehicle = str(
            expense.get("Автомобиль (Госномер / Общие)")
            or expense.get("Автомобиль")
            or ""
        ).strip().casefold()
        if vehicle and any(
            identifier in vehicle
            for identifier in normalized_identifiers
            if identifier
        ):
            expenses.append(dict(expense))
    return expenses


def get_active_bookings_for_car(car_id: str, plate: str = ""):
    active_statuses = ["В аренде", "Подтверждена"]
    bookings = [
        booking
        for booking in _get_sheet_records_safely(
            ws_bookings,
            "Бронирования",
        )
        if str(booking.get("Статус", "")).strip() in active_statuses
    ]
    matching_bookings = []
    for booking in bookings:
        if _normalized_car_identifier(booking.get("ID Авто")) == (
            _normalized_car_identifier(car_id)
        ):
            matching_bookings.append(booking)
            continue
        booking_plate = str(
            booking.get("Госномер")
            or booking.get("Номер авто")
            or booking.get("Номер")
            or ""
        ).strip()
        if plate and booking_plate.casefold() == str(plate).strip().casefold():
            matching_bookings.append(booking)
    return matching_bookings


def get_cancellable_bookings_for_car(car_id: str, plate: str = ""):
    return [
        booking
        for booking in get_active_bookings_for_car(car_id, plate)
        if str(booking.get("Статус", "")).strip() == "Подтверждена"
    ]


def cancel_booking(
    booking_id: int,
    reason: str,
    deposit_status: str,
) -> None:
    try:
        all_values = _sheets_read(
            ws_bookings.get_all_values,
            f"reading booking {booking_id} for cancellation",
        )
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
            _sheets_write(
                lambda: ws_bookings.update_cell(
                    booking_row,
                    headers.index(header) + 1,
                    value,
                ),
                f"updating cancelled booking {booking_id}",
            )
    except Exception:
        logger.exception(
            "Failed to cancel booking %s in Google Sheets",
            booking_id,
        )
        raise


def get_car_by_id(car_id: str):
    all_cars = _sheets_read(
        ws_cars.get_all_records,
        "reading car by ID",
    )
    for car in all_cars:
        if str(car.get("ID")) == str(car_id):
            return car
    return {}


def get_car_by_plate(plate: str):
    all_cars = _sheets_read(
        ws_cars.get_all_records,
        "reading car by plate",
    )
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
    time_mileage_penalty: int = 0,
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
    rental_paid_at_issuance: int = 0,
    manager_name: str = "",
    phone: str = "",
    issued_at: str = "",
    act_created_at: str = "",
    allow_unlinked: bool = False,
    car_id: str = "",
    idempotency_key: str = "",
    source: str = "Леон Рентал",
):
    returned_at = returned_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_penalties = (
        time_mileage_penalty
        + wash_fee
        + extra_fee
    )
    booking_row = None
    current_status = ""
    booking_source = ""
    booking_client = ""
    if not allow_unlinked:
        for row_number, booking in enumerate(
            _get_sheet_records_safely(ws_bookings, "Бронирования"),
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
                booking_source = booking.get("Источник", "")
                booking_client = (
                    booking.get("ФИО Клиента")
                    or booking.get("Клиент")
                    or booking.get("Клиент ФИО")
                    or ""
                )
                break
        if booking_row is None:
            raise ValueError(f"Booking {booking_id} was not found")
    returns_records = _get_sheet_records_safely(ws_returns, "Возвраты_Касса")
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
    acceptance_records = _sheets_read(
        ws_acceptance_acts.get_all_records,
        "reading acceptance acts",
    )
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
        "Оплачено при выдаче (RUB)": rental_paid_at_issuance,
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
        "Штраф за опоздание / перепробег (RUB)": time_mileage_penalty,
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
            "Оплачено при выдаче (RUB)",
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
            "Штраф за опоздание / перепробег (RUB)",
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
        "Источник": source,
        "Пробег при возврате (км)": end_mileage,
        "Пробег за аренду (км)": delta_mileage,
        "Сумма брони (RUB)": rental_total,
        "Итоговая стоимость аренды (RUB)": rental_total,
        "Принятая сумма аренды (RUB)": rental_total,
        "Стоимость аренды (RUB)": rental_total,
        "Задаток/Предоплата": prepayment,
        "Способ оплаты задатка": prepayment_pay_type,
        "Остаток к оплате аренды (RUB)": rental_balance,
        "Сумма аренды к доплате (RUB)": rental_balance,
        "Оплачено при выдаче (RUB)": rental_paid_at_issuance,
        "Фактически оплачено за аренду (RUB)": (
            rental_paid_at_issuance + rental_paid
        ),
        "Способ оплаты аренды": rental_pay_type,
        "Осталось оплатить за аренду (RUB)": rental_outstanding,
        "Переплата задатка к возврату (RUB)": prepayment_overpayment,
        "Лимит пробега (км)": mileage_limit,
        "Перепробег (км)": over_mileage,
        "Штраф за перепробег (RUB)": over_mileage_fee,
        "Просрочка (ч)": late_hours,
        "Штраф за просрочку (RUB)": late_fee,
        "Штраф за опоздание / перепробег (RUB)": time_mileage_penalty,
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
        _update_row_values(
            ws_bookings,
            booking_row,
            booking_headers,
            booking_updates,
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
        "Штраф за опоздание / перепробег (RUB)": time_mileage_penalty,
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
        _sheets_write(
            lambda: ws_bookings.update_cell(
                booking_row,
                booking_headers.index("Статус") + 1,
                "Завершена",
            ),
            f"completing booking {booking_id}",
        )
        try:
            colorize_booking_row(
                getattr(sh, "id", SPREADSHEET_NAME),
                getattr(ws_bookings, "id", 0),
                booking_row,
                _booking_color_source(
                    booking_source or source,
                    booking_client or client,
                ),
            )
        except Exception:
            logger.exception(
                "Booking %s was completed but row coloring failed",
                booking_id,
            )

def _initialize_worksheets_with_retry(
    max_retries: int = 5,
    attempt_counter: list[int] | None = None,
) -> None:
    """Keep a temporary startup quota response inside this process."""
    _retry_transient_operation(
        _initialize_worksheets,
        "startup",
        max_retries,
        attempt_counter,
    )


def _startup_failure_reason(error: Exception) -> str:
    """Classify a startup failure without exposing its raw error text."""
    if isinstance(error, SheetsStructureError):
        return "structure"

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(response, dict):
        status_code = response.get("status_code", response.get("status"))
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        status_code = None

    error_text = str(error).casefold()
    if status_code == 429 or any(
        marker in error_text
        for marker in ("quota", "rate limit", "too many requests")
    ):
        return "quota"
    if _is_transient_spreadsheet_error(error):
        return "network"
    if isinstance(error, gspread.exceptions.WorksheetNotFound):
        return "structure"
    return "configuration"


def _startup_failure_hint(error: Exception, reason: str) -> str:
    """Build a safe startup hint, including only known worksheet titles."""
    if reason == "structure" and isinstance(error, SheetsStructureError):
        if error.conflicts:
            pairs = ", ".join(
                f"{old_title} / {new_title}"
                for old_title, new_title in error.conflicts
            )
            return (
                "Обнаружены конфликтующие названия листов: "
                f"{pairs}. Оставьте только один вариант названия "
                "для каждой пары и повторите запуск."
            )
    return SHEETS_STARTUP_HINTS[reason]


def sheets_operation_failure_hint(error: Exception) -> str:
    """Return a safe operator hint for an exhausted runtime operation."""
    reason = _startup_failure_reason(error)
    return {
        "quota": (
            "Google Sheets временно ограничил частоту запросов. "
            "Повторите действие через минуту."
        ),
        "network": (
            "Google Sheets временно недоступен по сети. "
            "Повторите действие через несколько секунд."
        ),
        "structure": (
            "Структура Google Sheets не соответствует ожидаемой. "
            "Проверьте обязательные листы и заголовки."
        ),
        "configuration": (
            "Не удалось выполнить операцию Google Sheets. "
            "Проверьте доступ сервисного аккаунта и настройки таблицы."
        ),
    }[reason]


def _startup_failure_status(
    error: Exception,
    attempts: int,
) -> SheetsStartupStatus:
    """Build a safe startup status suitable for logs and Telegram-free errors."""
    reason = _startup_failure_reason(error)
    return SheetsStartupStatus(
        ready=False,
        reason=reason,
        attempts=max(1, attempts),
        hint=_startup_failure_hint(error, reason),
    )


def _create_sheets_client():
    """Create a Google Sheets client without opening or changing a sheet."""
    if SERVICE_ACCOUNT_JSON:
        return gspread.service_account_from_dict(json.loads(SERVICE_ACCOUNT_JSON))
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)


def _diagnose_sheets_access_once() -> None:
    """Check spreadsheet access and required worksheet titles without writes."""
    gc_client = _create_sheets_client()
    if len(SPREADSHEET_NAME) > 30 and " " not in SPREADSHEET_NAME:
        spreadsheet = gc_client.open_by_key(SPREADSHEET_NAME)
    else:
        spreadsheet = gc_client.open(SPREADSHEET_NAME)

    worksheet_titles = {worksheet.title for worksheet in spreadsheet.worksheets()}
    conflicts = _worksheet_title_conflicts(worksheet_titles)
    if conflicts:
        raise SheetsStructureError(conflicts)
    if any(title not in worksheet_titles for title in REQUIRED_WORKSHEET_TITLES):
        raise SheetsStructureError()


def diagnose_sheets_access(max_retries: int = 5) -> SheetsStartupStatus:
    """Return a safe, read-only diagnosis for an operator."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    attempts = 0
    for attempt in range(max_retries):
        attempts = attempt + 1
        try:
            _diagnose_sheets_access_once()
        except Exception as error:
            reason = _startup_failure_reason(error)
            is_last_attempt = attempt == max_retries - 1
            if is_last_attempt or not _is_transient_spreadsheet_error(error):
                return SheetsStartupStatus(
                    ready=False,
                    reason=reason,
                    attempts=attempts,
                    hint=_startup_failure_hint(error, reason),
                )
            time.sleep(3)
        else:
            return SheetsStartupStatus(
                ready=True,
                reason="ready",
                attempts=attempts,
                hint=SHEETS_STARTUP_HINTS["ready"],
            )

    # The loop always returns, but keep a safe result if its bounds change.
    return SheetsStartupStatus(
        ready=False,
        reason="configuration",
        attempts=attempts,
        hint=SHEETS_STARTUP_HINTS["configuration"],
    )


def _bootstrap_sheets() -> None:
    """Create the Sheets client and initialize all required worksheets."""
    global gc, sh, SHEETS_STARTUP_ATTEMPTS

    attempt_counter = [0]
    attempt_counter[0] = 1
    SHEETS_STARTUP_ATTEMPTS = 1
    if SERVICE_ACCOUNT_JSON:
        gc = gspread.service_account_from_dict(json.loads(SERVICE_ACCOUNT_JSON))
    else:
        gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)

    # Google Sheets IDs are not spreadsheet titles. Keep support for both so
    # the default name and an explicitly supplied spreadsheet ID work as
    # expected.
    if len(SPREADSHEET_NAME) > 30 and " " not in SPREADSHEET_NAME:
        sh = get_spreadsheet_with_retry(
            gc,
            SPREADSHEET_NAME,
            attempt_counter=attempt_counter,
        )
        SHEETS_STARTUP_ATTEMPTS = attempt_counter[0]
    else:
        # Keep title-based projects on the same in-process retry path as
        # ID-based projects. A transient quota response must not make the
        # workflow restart before the bots have even been created.
        sh = _retry_transient_operation(
            lambda: gc.open(SPREADSHEET_NAME),
            "opening spreadsheet",
            attempt_counter=attempt_counter,
        )
        SHEETS_STARTUP_ATTEMPTS = attempt_counter[0]
    _initialize_worksheets_with_retry(attempt_counter=attempt_counter)
    SHEETS_STARTUP_ATTEMPTS = attempt_counter[0]


def ensure_sheets_ready() -> None:
    """Log readiness after startup recovery before polling begins."""
    if not SHEETS_READY:
        raise SheetsStartupError(SHEETS_STARTUP_STATUS)
    logger.info("Google Sheets ready; bot startup may continue")


def _run_sheets_startup() -> bool:
    """Run the Sheets bootstrap and publish its safe readiness status."""
    global SHEETS_READY, SHEETS_STARTUP_STATUS

    try:
        _bootstrap_sheets()
    except Exception as error:
        SHEETS_READY = False
        SHEETS_STARTUP_STATUS = _startup_failure_status(
            error,
            SHEETS_STARTUP_ATTEMPTS,
        )
        logger.error(
            "Google Sheets startup failed: reason=%s; attempts=%s; hint=%s",
            SHEETS_STARTUP_STATUS.reason,
            SHEETS_STARTUP_STATUS.attempts,
            SHEETS_STARTUP_STATUS.hint,
        )
    else:
        SHEETS_READY = True
        SHEETS_STARTUP_STATUS = SheetsStartupStatus(
            ready=True,
            reason="ready",
            attempts=SHEETS_STARTUP_ATTEMPTS,
            hint="Google Sheets готова к работе.",
        )
    return SHEETS_READY


def _cached_worksheet(*titles: str):
    """Resolve a worksheet without issuing another metadata request."""
    for title in titles:
        worksheet = _worksheet_cache.get(title)
        if worksheet is not None:
            return worksheet
    raise gspread.exceptions.WorksheetNotFound(titles[0])


_run_sheets_startup()
