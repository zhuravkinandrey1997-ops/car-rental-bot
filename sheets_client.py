import os
import datetime
import json
import gspread
from dotenv import load_dotenv

load_dotenv()

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "leon_rental_fleet_rub_v4")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
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

if SERVICE_ACCOUNT_JSON:
    gc = gspread.service_account_from_dict(json.loads(SERVICE_ACCOUNT_JSON))
else:
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)

# Google Sheets IDs are not spreadsheet titles. Keep support for both so the
# default name and an explicitly supplied spreadsheet ID work as expected.
if len(SPREADSHEET_NAME) > 30 and " " not in SPREADSHEET_NAME:
    sh = gc.open_by_key(SPREADSHEET_NAME)
else:
    sh = gc.open(SPREADSHEET_NAME)

ws_cars = sh.worksheet("Cars")
ws_bookings = sh.worksheet("Bookings")
ws_returns = sh.worksheet("Returns_Cash")
ws_managers = sh.worksheet("Managers")
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
    headers = ws.row_values(1)
    for header in required_headers:
        if header not in headers:
            headers.append(header)
            ws.update_cell(1, len(headers), header)
    return headers


def _append_record(ws, values: dict[str, object], required_headers: list[str]) -> None:
    headers = _ensure_headers(ws, required_headers)
    ws.append_row([values.get(header, "") for header in headers])


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
):
    records = ws_bookings.get_all_records()
    new_id = len(records) + 101
    values = {
        "ID Брони": new_id,
        "ID": new_id,
        "ID Авто": car_id,
        "Автомобиль": car_name,
        "Авто": car_name,
        "Дата начала (ГГГГ-ММ-ДД)": start_date,
        "Дата окончания (ГГГГ-ММ-ДД)": end_date,
        "ФИО Клиента": client_name,
        "Клиент": client_name,
        "Телефон": phone,
        "Сумма брони (RUB)": price,
        "Цена": price,
        "Telegram ID Менеджера": manager_id,
        "Статус": status,
        "Дата и время выдачи": issued_at,
        "Пробег при выдаче (км)": start_mileage,
        "Принятая сумма аренды (RUB)": price,
        "Сумма аренды получена (RUB)": price,
        "Залог (RUB)": deposit,
        "Внесенный залог": deposit,
        "Страховой залог": deposit,
        "Способ оплаты": pay_type,
        "Способ оплаты залога": deposit_pay_type,
        "Способ оплаты страхового залога": deposit_pay_type,
        "Залог/Предоплата": prepayment,
        "Задаток/Предоплата": prepayment,
        "Тип оплаты залога": prepayment_pay_type,
        "Способ оплаты задатка": prepayment_pay_type,
        "Плановое окончание": f"{end_date} 23:59",
    }
    _append_record(
        ws_bookings,
        values,
        [
            "ID Брони",
            "ID Авто",
            "Автомобиль",
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата окончания (ГГГГ-ММ-ДД)",
            "ФИО Клиента",
            "Телефон",
            "Сумма брони (RUB)",
            "Telegram ID Менеджера",
            "Статус",
            "Дата и время выдачи",
            "Пробег при выдаче (км)",
            "Принятая сумма аренды (RUB)",
            "Залог (RUB)",
            "Внесенный залог",
            "Страховой залог",
            "Способ оплаты",
            "Способ оплаты залога",
            "Способ оплаты страхового залога",
            "Залог/Предоплата",
            "Задаток/Предоплата",
            "Тип оплаты залога",
            "Способ оплаты задатка",
            "Плановое окончание",
        ],
    )
    return new_id


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

    updates = {
        "Статус": "В аренде",
        "Дата и время выдачи": issued_at,
        "Пробег при выдаче (км)": start_mileage,
        "Telegram ID Менеджера выдачи": manager_id,
        "Страховой залог": insurance_deposit,
        "Страховой залог (RUB)": insurance_deposit,
        "Внесенный страховой залог (RUB)": insurance_deposit,
        "Способ оплаты страхового залога": insurance_deposit_pay_type,
        "Залог (RUB)": insurance_deposit,
        "Внесенный залог": insurance_deposit,
        "Способ оплаты залога": insurance_deposit_pay_type,
    }
    headers = _ensure_headers(ws_bookings, list(updates))
    for header, value in updates.items():
        ws_bookings.update_cell(
            booking_row,
            headers.index(header) + 1,
            value,
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
    records = ws_bookings.get_all_records()
    booking_row = None
    current_status = ""
    for row_number, booking in enumerate(records, start=2):
        if str(
            booking.get("ID Брони")
            or booking.get("ID")
            or booking.get("Booking ID")
            or ""
        ).strip() == str(booking_id).strip():
            booking_row = row_number
            current_status = str(booking.get("Статус", "")).strip()
            break
    if booking_row is None:
        raise ValueError(f"Booking {booking_id} was not found")
    if current_status != "Подтверждена":
        raise ValueError(
            f"Booking {booking_id} cannot be cancelled from status {current_status}"
        )

    updates = {
        "Статус": "Отменена",
        "Причина отмены": reason,
        "Статус залога при отмене": deposit_status,
        "Статус задатка при отмене": deposit_status,
    }
    headers = _ensure_headers(ws_bookings, list(updates))
    for header, value in updates.items():
        ws_bookings.update_cell(booking_row, headers.index(header) + 1, value)


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
    booking_id: int,
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
):
    returned_at = returned_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_penalties = over_mileage_fee + late_fee + wash_fee + extra_fee
    booking_row = None
    current_status = ""
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
    return_already_saved = any(
        str(record.get("ID Брони", "")).strip()
        == str(booking_id).strip()
        for record in returns_records
    )
    acceptance_records = ws_acceptance_acts.get_all_records()
    acceptance_already_saved = any(
        str(record.get("ID Брони", "")).strip()
        == str(booking_id).strip()
        for record in acceptance_records
    )
    if (
        current_status == "Завершена"
        and return_already_saved
        and acceptance_already_saved
    ):
        raise ValueError(f"Booking {booking_id} was already returned")
    if current_status not in {"В аренде", "Завершена"}:
        raise ValueError(
            f"Booking {booking_id} cannot be returned from status {current_status}"
        )

    new_id = len(returns_records) + 1
    values = {
        "ID Записи": new_id,
        "ID": new_id,
        "ID Брони": booking_id,
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

    ws_bookings.update_cell(
        booking_row,
        booking_headers.index("Статус") + 1,
        "Завершена",
    )

    car_cell = ws_cars.find(plate)
    if car_cell:
        car_headers = ws_cars.row_values(1)
        mileage_headers = (
            "Текущий пробег (км)",
            "Пробег",
            "Текущий пробег",
            "Пробег (км)",
        )
        mileage_header = next((header for header in mileage_headers if header in car_headers), None)
        if mileage_header:
            ws_cars.update_cell(car_cell.row, car_headers.index(mileage_header) + 1, end_mileage)