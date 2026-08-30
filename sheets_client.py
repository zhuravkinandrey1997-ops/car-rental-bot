import os
import datetime
import json
import gspread
from dotenv import load_dotenv

load_dotenv()

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "leon_rental_fleet_rub_v4")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

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
        "Способ оплаты": pay_type,
        "Залог/Предоплата": prepayment,
        "Тип оплаты залога": prepayment_pay_type,
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
            "Способ оплаты",
            "Залог/Предоплата",
            "Тип оплаты залога",
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
):
    returned_at = returned_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_penalties = over_mileage_fee + late_fee + wash_fee + extra_fee
    returns_records = ws_returns.get_all_records()
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
        "Перепробег / Доплаты (RUB)": total_penalties,
        "Итого приход (RUB)": total_paid,
        "Способ оплаты": pay_type,
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
        "Залог к возврату (RUB)": max(deposit_balance, 0),
        "Доплата по залогу (RUB)": max(-deposit_balance, 0),
        "Итоговый расчёт (RUB)": deposit_balance,
    }
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
            "Перепробег / Доплаты (RUB)",
            "Итого приход (RUB)",
            "Способ оплаты",
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
            "Залог к возврату (RUB)",
            "Доплата по залогу (RUB)",
            "Итоговый расчёт (RUB)",
        ],
    )
    cell = ws_bookings.find(str(booking_id))
    if cell:
        booking_updates = {
            "Статус": "Завершена",
            "Дата и время приёма": returned_at,
            "Пробег при возврате (км)": end_mileage,
            "Пробег за аренду (км)": delta_mileage,
            "Лимит пробега (км)": mileage_limit,
            "Перепробег (км)": over_mileage,
            "Штраф за перепробег (RUB)": over_mileage_fee,
            "Просрочка (ч)": late_hours,
            "Штраф за просрочку (RUB)": late_fee,
            "Состояние чистоты": cleanliness,
            "Мойка (RUB)": wash_fee,
            "Доп. штрафы/удержания (RUB)": extra_fee,
            "Залог к возврату (RUB)": max(deposit_balance, 0),
            "Доплата по залогу (RUB)": max(-deposit_balance, 0),
            "Итоговый расчёт (RUB)": deposit_balance,
            "Примечания / Состояние": notes,
        }
        booking_headers = _ensure_headers(ws_bookings, list(booking_updates))
        for header, value in booking_updates.items():
            ws_bookings.update_cell(cell.row, booking_headers.index(header) + 1, value)
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