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


def add_new_booking(car_id: int, car_name: str, start_date: str, end_date: str, client_name: str, phone: str, price: int, manager_id: int):
    records = ws_bookings.get_all_records()
    new_id = len(records) + 101
    row = [new_id, car_id, car_name, start_date, end_date, client_name, phone, price, manager_id, "Подтверждена"]
    ws_bookings.append_row(row)
    return new_id


def get_active_rentals():
    all_bookings = ws_bookings.get_all_records()
    return [b for b in all_bookings if b.get("Статус") in ["В аренде", "Подтверждена"]]


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


def save_actual_return(booking_id: int, car_name: str, plate: str, client: str, start_mileage: int, end_mileage: int, delta_mileage: int, base_price: int, extra_fee: int, total_paid: int, pay_type: str, manager_id: int, notes: str):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    returns_records = ws_returns.get_all_records()
    new_id = len(returns_records) + 1
    row = [new_id, booking_id, car_name, plate, client, today_str, start_mileage, end_mileage, delta_mileage, base_price, extra_fee, total_paid, pay_type, manager_id, notes]
    ws_returns.append_row(row)
    cell = ws_bookings.find(str(booking_id))
    if cell:
        ws_bookings.update_cell(cell.row, 10, "Завершена")
    car_cell = ws_cars.find(plate)
    if car_cell:
        ws_cars.update_cell(car_cell.row, 11, end_mileage)