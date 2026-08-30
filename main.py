"""Telegram entry point for the car-rental management bot."""

from __future__ import annotations

import asyncio
import calendar
import datetime
import logging
import math
import os
from contextlib import suppress
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from sheets_client import (
    add_new_booking,
    cancel_booking,
    get_active_rentals,
    get_active_bookings_for_car,
    get_active_cars,
    get_all_cars,
    get_available_cars,
    get_car_by_id,
    get_car_by_plate,
    get_cancellable_bookings_for_car,
    get_pending_notification_manager_ids,
    get_returnable_rentals,
    get_tomorrow_bookings,
    is_manager_authorized,
    mark_notification_sent,
    save_actual_return,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("car-rental-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class AvailabilityForm(StatesGroup):
    start_date = State()
    end_date = State()


class BookingForm(StatesGroup):
    start_date = State()
    end_date = State()
    car = State()
    client_name = State()
    phone = State()
    price = State()
    prepayment = State()
    start_mileage = State()
    deposit = State()
    pay_type = State()
    confirmation = State()


class OccupancyForm(StatesGroup):
    car = State()
    calendar = State()


class CancellationForm(StatesGroup):
    car = State()
    booking = State()
    reason = State()
    deposit_status = State()


class ReturnForm(StatesGroup):
    booking = State()
    end_mileage = State()
    cleanliness = State()
    wash_fee = State()
    extra_fee = State()
    total_paid = State()
    pay_type = State()
    notes = State()
    confirmation = State()


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = year * 12 + month - 1 + delta
    return month_index // 12, month_index % 12 + 1


def calendar_keyboard(
    flow: str,
    stage: str,
    minimum_date: datetime.date | None = None,
    year: int | None = None,
    month: int | None = None,
    occupied_dates: dict[str, list[str]] | None = None,
    allow_past: bool = False,
) -> InlineKeyboardMarkup:
    if not allow_past:
        minimum_date = minimum_date or datetime.date.today()
    year = year or (minimum_date or datetime.date.today()).year
    month = month or (minimum_date or datetime.date.today()).month
    minimum_month = (
        (minimum_date.year, minimum_date.month) if minimum_date else None
    )
    previous_year, previous_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"{MONTH_NAMES[month - 1]} {year}",
                callback_data="calendar:noop",
            )
        ],
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"calendar:{flow}:{stage}:{previous_year}:{previous_month}"
                    if minimum_month is None
                    or (previous_year, previous_month) > minimum_month
                    else "calendar:noop"
                ),
            ),
            InlineKeyboardButton(
                text="Сегодня",
                callback_data=(
                    f"calendar:{flow}:{stage}:{datetime.date.today().year}:"
                    f"{datetime.date.today().month}"
                ),
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=f"calendar:{flow}:{stage}:{next_year}:{next_month}",
            ),
        ],
        [
            InlineKeyboardButton(text=weekday, callback_data="calendar:noop")
            for weekday in WEEKDAY_NAMES
        ],
    ]

    for week in calendar.monthcalendar(year, month):
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="calendar:noop"))
                continue
            current_date = datetime.date(year, month, day)
            if occupied_dates and current_date.isoformat() in occupied_dates:
                row.append(
                    InlineKeyboardButton(
                        text=f"🔴 {day}",
                        callback_data=(
                            f"calendar:{flow}:{stage}:busy:{current_date.isoformat()}"
                        ),
                    )
                )
            elif minimum_date and current_date < minimum_date:
                row.append(InlineKeyboardButton(text="·", callback_data="calendar:noop"))
            else:
                row.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=(
                            f"calendar:{flow}:{stage}:day:{current_date.isoformat()}"
                        ),
                    )
                )
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _calendar_prompt(flow: str, stage: str, minimum_date: datetime.date | None = None) -> str:
    minimum_text = ""
    if minimum_date:
        minimum_text = f"\nМинимальная дата: {minimum_date.strftime('%d.%m.%Y')}."
    if stage == "start":
        return (
            "Выберите дату начала аренды в календаре."
            "\nМожно также ввести дату текстом в формате ГГГГ-ММ-ДД."
            + minimum_text
        )
    return (
        "Выберите дату окончания аренды в календаре."
        "\nМожно также ввести дату текстом в формате ГГГГ-ММ-ДД."
        + minimum_text
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Доступные авто"),
                KeyboardButton(text="Новая бронь"),
            ],
            [
                KeyboardButton(text="🚗 Выдача авто"),
                KeyboardButton(text="Активные аренды"),
            ],
            [
                KeyboardButton(text="🏁 Приём авто"),
                KeyboardButton(text="🔍 Проверка броней"),
            ],
            [KeyboardButton(text="🚫 Отмена брони")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Подтвердить"),
                KeyboardButton(text="Отмена"),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


PAYMENT_METHODS = {
    "cash": "Наличные",
    "card": "Банковская карта",
    "sbp": "Перевод (СБП)",
    "legal": "Безнал (Юр. лицо)",
}


DEFAULT_WASH_FEE = 1000
MINSK_TZ = ZoneInfo("Europe/Minsk")
NOTIFICATION_TIMEZONE_NAME = os.getenv("RENTAL_TIMEZONE", "Europe/Moscow")
try:
    NOTIFICATION_TZ = ZoneInfo(NOTIFICATION_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(
        f"Unknown RENTAL_TIMEZONE: {NOTIFICATION_TIMEZONE_NAME}"
    ) from exc
NOTIFICATION_SEND_LOCK = asyncio.Lock()


def payment_methods_keyboard(flow: str = "return") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💵 Наличные",
                    callback_data=f"payment:{flow}:cash",
                ),
                InlineKeyboardButton(
                    text="💳 Банковская карта",
                    callback_data=f"payment:{flow}:card",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📱 Перевод (СБП)",
                    callback_data=f"payment:{flow}:sbp",
                ),
                InlineKeyboardButton(
                    text=(
                        "🏦 Безнал"
                        if flow == "reservation"
                        else "🏦 Безнал (Юр. лицо)"
                    ),
                    callback_data=f"payment:{flow}:legal",
                ),
            ],
        ]
    )


def cleanliness_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Чистая",
                    callback_data="cleanliness:clean",
                ),
                InlineKeyboardButton(
                    text="🧼 Грязная",
                    callback_data="cleanliness:dirty",
                ),
            ]
        ]
    )


def rental_choice_keyboard(items: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, item in enumerate(items, start=1):
        builder.add(KeyboardButton(text=f"{index}. {_booking_label(item)}"))
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def cancellation_booking_keyboard(bookings: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, booking in enumerate(bookings):
        text = (
            f"Бронь #{_booking_id(booking)} · "
            f"{_value(booking, 'Дата начала (ГГГГ-ММ-ДД)', 'Дата начала')} — "
            f"{_value(booking, 'Дата окончания (ГГГГ-ММ-ДД)', 'Дата окончания')} · "
            f"{_value(booking, 'ФИО Клиента', 'Клиент', 'Клиент ФИО')}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=text[:64],
                    callback_data=f"cancel_booking:{index}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancellation_deposit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Залог возвращен клиенту",
                    callback_data="cancel_deposit:returned",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Залог удержан автопрокатом",
                    callback_data="cancel_deposit:retained",
                )
            ],
        ]
    )


def cancellation_car_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        builder.add(
            KeyboardButton(
                text=f"{index}. {_car_name(car)} ({_car_plate(car)})"
            )
        )
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def car_choice_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        builder.add(KeyboardButton(text=f"{index}. {_car_label(car)}"))
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def _value(record: dict, *keys: str, default: str = "—") -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _booking_id(record: dict) -> str:
    return _value(record, "ID Брони", "ID", "Booking ID")


def _car_id(record: dict) -> str:
    return _value(record, "ID Авто", "ID", "Car ID")


def _car_name(car: dict) -> str:
    return _value(
        car,
        "Марка и Модель",
        "Марка и модель",
        "Марка Модель",
        "Название",
        "Авто",
        "Модель",
    )


def _car_plate(car: dict) -> str:
    return _value(car, "Госномер", "Номер", "Номер авто", "Plate")


def _car_daily_rate(car: dict) -> str:
    return _value(
        car,
        "Цена / сутки (RUB)",
        "Суточный тариф",
        "Цена за сутки",
        "Цена",
        "Стоимость",
    )


def _car_label(car: dict) -> str:
    return f"{_car_name(car)} ({_car_plate(car)}) — {_car_daily_rate(car)} RUB/сутки"


def _booking_car(booking: dict) -> dict:
    car_id = _value(booking, "ID Авто", "Car ID", default="")
    if car_id:
        car = get_car_by_id(car_id)
        if car:
            return car

    plate = _value(booking, "Госномер", "Номер авто", "Номер", "Plate", default="")
    if plate:
        car = get_car_by_plate(plate)
        if car:
            return car
    return {}


def _booking_label(booking: dict) -> str:
    car_record = _booking_car(booking)
    if car_record:
        car = f"{_car_name(car_record)} ({_car_plate(car_record)})"
    else:
        saved_name = _value(
            booking,
            "Автомобиль",
            "Авто",
            "Название авто",
            "Марка и Модель",
            "Модель",
        )
        saved_plate = _value(booking, "Госномер", "Номер авто", "Номер", "Plate")
        car = f"{saved_name} ({saved_plate})"
    dates = (
        f"{_value(booking, 'Дата начала (ГГГГ-ММ-ДД)', 'Дата начала')} — "
        f"{_value(booking, 'Дата окончания (ГГГГ-ММ-ДД)', 'Дата окончания')}"
    )
    return f"{car} · {dates}"


def _occupied_dates(bookings: list[dict]) -> dict[str, list[str]]:
    occupied: dict[str, list[str]] = {}
    for booking in bookings:
        try:
            start_date = datetime.date.fromisoformat(
                _value(
                    booking,
                    "Дата начала (ГГГГ-ММ-ДД)",
                    "Дата начала",
                    default="",
                )
            )
            end_date = datetime.date.fromisoformat(
                _value(
                    booking,
                    "Дата окончания (ГГГГ-ММ-ДД)",
                    "Дата окончания",
                    default="",
                )
            )
        except ValueError:
            logger.warning("Skipping booking with invalid dates: %s", _booking_id(booking))
            continue

        if end_date < start_date:
            continue
        booking_id = _booking_id(booking)
        current_date = start_date
        while current_date <= end_date:
            occupied.setdefault(current_date.isoformat(), []).append(booking_id)
            current_date += datetime.timedelta(days=1)
    return occupied


def _parse_date(value: str) -> bool:
    import datetime

    try:
        datetime.datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_int(value: str, field_name: str, minimum: int = 0) -> tuple[int | None, str | None]:
    try:
        parsed = int(value.strip().replace(" ", ""))
    except ValueError:
        return None, f"Введите целое число для поля «{field_name}»."
    if parsed < minimum:
        return None, f"Значение «{field_name}» не может быть меньше {minimum}."
    return parsed, None


def _now_minsk() -> datetime.datetime:
    return datetime.datetime.now(MINSK_TZ).replace(second=0, microsecond=0)


def _timestamp(value: datetime.datetime | None = None) -> str:
    return (value or _now_minsk()).strftime("%Y-%m-%d %H:%M")


def _record_date(record: dict, *keys: str) -> datetime.date | None:
    raw = _value(record, *keys, default="")
    for date_format in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


def _record_datetime(
    record: dict,
    *keys: str,
    default: datetime.datetime | None = None,
) -> datetime.datetime:
    raw = _value(record, *keys, default="")
    for date_format in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.datetime.strptime(raw, date_format)
            return parsed.replace(tzinfo=MINSK_TZ)
        except ValueError:
            continue
    return default or _now_minsk()


def _notification_time(
    booking: dict,
    *keys: str,
    default: str = "весь день",
) -> str:
    raw = _value(booking, *keys, default="")
    if not raw:
        return default
    for date_format in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y",
    ):
        try:
            parsed = datetime.datetime.strptime(raw, date_format)
        except ValueError:
            continue
        if "H" in date_format:
            return parsed.strftime("%H:%M")
        return default
    return raw


def _notification_car_lookup(cars: list[dict]) -> dict[str, dict]:
    lookup = {}
    for car in cars:
        car_id = _value(car, "ID", "ID Авто", "Car ID", default="")
        plate = _value(car, "Госномер", "Номер авто", "Номер", default="")
        if car_id:
            lookup[f"id:{car_id}"] = car
        if plate:
            lookup[f"plate:{plate}"] = car
    return lookup


def _notification_car_label(
    booking: dict,
    car_lookup: dict[str, dict],
) -> str:
    car_id = _value(booking, "ID Авто", "Car ID", default="")
    plate = _value(
        booking,
        "Госномер",
        "Номер авто",
        "Номер",
        "Plate",
        default="",
    )
    car_record = car_lookup.get(f"id:{car_id}") or car_lookup.get(
        f"plate:{plate}"
    )
    if car_record:
        return f"{_car_name(car_record)} ({_car_plate(car_record)})"
    return (
        f"{_value(booking, 'Автомобиль', 'Авто', 'Название авто', default='Авто')} "
        f"({_value(booking, 'Госномер', 'Номер авто', 'Номер', default='—')})"
    )


def _notification_booking_line(
    booking: dict,
    car_lookup: dict[str, dict],
    *time_keys: str,
) -> str:
    client = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="не указан",
    )
    phone = _value(booking, "Телефон", "Телефон клиента", default="не указан")
    scheduled_time = _notification_time(booking, *time_keys)
    return (
        f"• {_notification_car_label(booking, car_lookup)} — "
        f"Клиент: {client}/{phone} — Время: {scheduled_time}"
    )


def build_tomorrow_summary(
    target_date: datetime.date,
    events: dict[str, list[dict]],
    cars: list[dict] | None = None,
) -> str:
    date_label = target_date.strftime("%d.%m.%Y")
    issuances = events.get("issuances", [])
    returns = events.get("returns", [])
    if not issuances and not returns:
        return f"📅 На завтра ({date_label}) запланированных выдач и возвратов нет."

    car_lookup = _notification_car_lookup(cars or [])
    sections = [f"📅 План на завтра ({date_label}):"]
    if issuances:
        sections.append(
            "🚗 ВЫДАЧИ АВТО:\n"
            + "\n".join(
                _notification_booking_line(
                    booking,
                    car_lookup,
                    "Дата и время выдачи",
                    "Время выдачи",
                    "Дата начала (ГГГГ-ММ-ДД)",
                    "Дата начала",
                )
                for booking in issuances
            )
        )
    if returns:
        sections.append(
            "🏁 ПРИЁМЫ АВТО:\n"
            + "\n".join(
                _notification_booking_line(
                    booking,
                    car_lookup,
                    "Плановое окончание",
                    "Плановая дата и время окончания",
                    "Дата окончания (ГГГГ-ММ-ДД)",
                    "Дата окончания",
                )
                for booking in returns
            )
        )
    return "\n\n".join(sections)


def split_telegram_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


async def _send_notification_chunks(
    bot: Bot,
    manager_id: int,
    chunks: list[str],
) -> None:
    for chunk in chunks:
        last_error = None
        for attempt in range(3):
            try:
                await bot.send_message(chat_id=manager_id, text=chunk)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(5 * (attempt + 1))
        if last_error is not None:
            raise last_error


async def send_tomorrow_notifications(
    bot: Bot,
    now: datetime.datetime | None = None,
) -> None:
    current = now or datetime.datetime.now(NOTIFICATION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NOTIFICATION_TZ)
    else:
        current = current.astimezone(NOTIFICATION_TZ)
    target_date = current.date() + datetime.timedelta(days=1)
    async with NOTIFICATION_SEND_LOCK:
        try:
            events, manager_ids, cars = await asyncio.to_thread(
                lambda: (
                    get_tomorrow_bookings(target_date),
                    get_pending_notification_manager_ids(target_date),
                    get_all_cars(),
                )
            )
        except Exception:
            logger.exception("Could not collect tomorrow's rental plan")
            return

        summary = build_tomorrow_summary(target_date, events, cars)
        chunks = split_telegram_message(summary)
        if not manager_ids:
            logger.info(
                "Tomorrow's rental plan for %s has no pending recipients",
                target_date,
            )
            return
        for manager_id in manager_ids:
            try:
                await _send_notification_chunks(bot, manager_id, chunks)
                await asyncio.to_thread(
                    mark_notification_sent,
                    manager_id,
                    target_date,
                )
            except TelegramAPIError:
                logger.exception(
                    "Could not send tomorrow's rental plan to manager %s",
                    manager_id,
                )
            except Exception:
                logger.exception(
                    "Unexpected error sending tomorrow's rental plan to manager %s",
                    manager_id,
                )


async def send_missed_tomorrow_notifications(bot: Bot) -> None:
    now = datetime.datetime.now(NOTIFICATION_TZ)
    if now.time() >= datetime.time(20, 0):
        await send_tomorrow_notifications(bot, now)


def create_notification_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=NOTIFICATION_TZ)
    scheduler.add_job(
        send_tomorrow_notifications,
        CronTrigger(
            hour="20-23",
            minute="0,15,30,45",
            timezone=NOTIFICATION_TZ,
        ),
        args=[bot],
        id="daily_tomorrow_rental_plan",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler


def _return_calculation(data: dict) -> dict[str, int | str]:
    booking = data["booking"]
    car = data["car"]
    start_date = _record_date(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
    ) or _now_minsk().date()
    end_date = _record_date(
        booking,
        "Дата окончания (ГГГГ-ММ-ДД)",
        "Дата окончания",
    ) or start_date
    planned_end = _record_datetime(
        booking,
        "Плановое окончание",
        "Плановая дата и время окончания",
        default=datetime.datetime.combine(end_date, datetime.time(23, 59), MINSK_TZ),
    )
    returned_at = _record_datetime(
        {"Дата и время приёма": data.get("returned_at", "")},
        "Дата и время приёма",
        default=_now_minsk(),
    )
    late_seconds = max(0, int((returned_at - planned_end).total_seconds()))
    late_hours = math.ceil(late_seconds / 3600) if late_seconds else 0

    daily_rate, _ = _parse_int(
        _car_daily_rate(car),
        "суточный тариф",
        minimum=0,
    )
    daily_rate = daily_rate or 0
    late_fee = round(daily_rate * 0.10 * late_hours)

    rental_days = max(1, (end_date - start_date).days + 1)
    mileage_limit = rental_days * 250
    delta_mileage = int(data["end_mileage"]) - int(data["start_mileage"])
    over_mileage = max(0, delta_mileage - mileage_limit)
    over_mileage_fee = over_mileage * 50
    wash_fee = int(data.get("wash_fee", 0))
    extra_fee = int(data.get("extra_fee", 0))
    deposit = int(data.get("deposit", 0))
    deposit_balance = deposit - late_fee - over_mileage_fee - wash_fee - extra_fee

    return {
        "daily_rate": daily_rate,
        "rental_days": rental_days,
        "mileage_limit": mileage_limit,
        "delta_mileage": delta_mileage,
        "over_mileage": over_mileage,
        "over_mileage_fee": over_mileage_fee,
        "late_hours": late_hours,
        "late_fee": late_fee,
        "wash_fee": wash_fee,
        "extra_fee": extra_fee,
        "deposit": deposit,
        "deposit_balance": deposit_balance,
        "cleanliness": data.get("cleanliness", "—"),
    }


async def ensure_manager(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return False
    try:
        authorized = is_manager_authorized(user.id)
    except Exception:
        logger.exception("Manager authorization check failed")
        await message.answer("Не удалось проверить доступ. Попробуйте ещё раз позже.")
        return False
    if not authorized:
        await message.answer(
            "У вас нет активного доступа менеджера. Обратитесь к администратору."
        )
        return False
    return True


async def cancel_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Операция отменена.", reply_markup=main_keyboard())


async def show_menu(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    await message.answer(
        "Панель управления автопрокатом готова. Выберите операцию:",
        reply_markup=main_keyboard(),
    )


async def start_handler(message: Message, state: FSMContext) -> None:
    await show_menu(message, state)


async def start_availability(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    await state.set_state(AvailabilityForm.start_date)
    calendar_message = await message.answer(
        _calendar_prompt("availability", "start"),
        reply_markup=calendar_keyboard("availability", "start"),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def availability_start_date(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not _parse_date(value):
        await message.answer("Неверный формат. Пример: 2026-09-15.")
        return
    selected_date = datetime.date.fromisoformat(value)
    if selected_date < datetime.date.today():
        await message.answer("Дата начала не может быть в прошлом.")
        return
    await state.update_data(start_date=value)
    await state.set_state(AvailabilityForm.end_date)
    calendar_message = await message.answer(
        _calendar_prompt("availability", "end", selected_date),
        reply_markup=calendar_keyboard(
            "availability",
            "end",
            minimum_date=selected_date,
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def availability_end_date(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    data = await state.get_data()
    if not _parse_date(value):
        await message.answer("Неверный формат. Пример: 2026-09-20.")
        return
    if value < data["start_date"]:
        await message.answer("Дата окончания не может быть раньше даты начала.")
        return
    await _finish_availability(message, state, data["start_date"], value)


async def _finish_availability(
    message: Message,
    state: FSMContext,
    start_date: str,
    end_date: str,
) -> None:
    try:
        cars = get_available_cars(start_date, end_date)
    except Exception:
        logger.exception("Could not load available cars")
        await state.clear()
        await message.answer("Не удалось получить список авто из Google Sheets.", reply_markup=main_keyboard())
        return
    await state.clear()
    if not cars:
        await message.answer("На выбранный период свободных авто нет.", reply_markup=main_keyboard())
        return
    lines = [
        f"{index}. {_car_label(car)}"
        for index, car in enumerate(cars, start=1)
    ]
    await message.answer(
        "Свободные автомобили:\n\n" + "\n".join(lines),
        reply_markup=main_keyboard(),
    )


async def _start_booking_flow(
    message: Message,
    state: FSMContext,
    mode: str,
    title: str,
) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    await state.update_data(booking_mode=mode)
    await state.set_state(BookingForm.start_date)
    calendar_message = await message.answer(
        f"{title}.\n\n" + _calendar_prompt("booking", "start"),
        reply_markup=calendar_keyboard("booking", "start"),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def start_booking(message: Message, state: FSMContext) -> None:
    await _start_booking_flow(message, state, "reservation", "Новая бронь")


async def start_issuance(message: Message, state: FSMContext) -> None:
    await _start_booking_flow(message, state, "issuance", "Выдача авто")


async def booking_start_date(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not _parse_date(value):
        await message.answer("Неверный формат. Пример: 2026-09-15.")
        return
    selected_date = datetime.date.fromisoformat(value)
    if selected_date < datetime.date.today():
        await message.answer("Дата начала не может быть в прошлом.")
        return
    await state.update_data(start_date=value)
    await state.set_state(BookingForm.end_date)
    calendar_message = await message.answer(
        _calendar_prompt("booking", "end", selected_date),
        reply_markup=calendar_keyboard(
            "booking",
            "end",
            minimum_date=selected_date,
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def booking_end_date(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    data = await state.get_data()
    if not _parse_date(value):
        await message.answer("Неверный формат. Пример: 2026-09-20.")
        return
    if value < data["start_date"]:
        await message.answer("Дата окончания не может быть раньше даты начала.")
        return
    await _finish_booking_dates(message, state, data["start_date"], value)


async def _finish_booking_dates(
    message: Message,
    state: FSMContext,
    start_date: str,
    end_date: str,
) -> None:
    try:
        cars = get_available_cars(start_date, end_date)
    except Exception:
        logger.exception("Could not load cars for booking")
        await state.clear()
        await message.answer("Не удалось получить список авто из Google Sheets.", reply_markup=main_keyboard())
        return
    if not cars:
        await state.clear()
        await message.answer("На выбранный период свободных авто нет.", reply_markup=main_keyboard())
        return
    await state.update_data(end_date=end_date, cars=cars)
    await state.set_state(BookingForm.car)
    await message.answer("Выберите автомобиль:", reply_markup=car_choice_keyboard(cars))


async def start_occupancy(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    try:
        cars = get_active_cars()
    except Exception:
        logger.exception("Could not load cars for occupancy calendar")
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=main_keyboard(),
        )
        return
    if not cars:
        await message.answer(
            "В листе Cars нет активных автомобилей.",
            reply_markup=main_keyboard(),
        )
        return
    await state.update_data(cars=cars)
    await state.set_state(OccupancyForm.car)
    await message.answer(
        "Выберите автомобиль для проверки занятости:",
        reply_markup=car_choice_keyboard(cars),
    )


async def occupancy_car(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cars = data.get("cars", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        car = cars[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите автомобиль кнопкой из списка.")
        return

    car_id = _car_id(car)
    plate = _car_plate(car)
    try:
        bookings = get_active_bookings_for_car(car_id, plate)
    except Exception:
        logger.exception("Could not load bookings for occupancy calendar")
        await state.clear()
        await message.answer(
            "Не удалось получить брони из Google Sheets.",
            reply_markup=main_keyboard(),
        )
        return

    await state.update_data(
        selected_car=car,
        occupancy_bookings=bookings,
    )
    await state.set_state(OccupancyForm.calendar)
    calendar_message = await message.answer(
        "📅 Занятость автомобиля\n"
        f"{_car_name(car)} ({plate})\n\n"
        "🔴 — дата занята. Нажмите на неё, чтобы увидеть номер брони.\n"
        "Обычная дата — свободна.",
        reply_markup=calendar_keyboard(
            "occupancy",
            "calendar",
            allow_past=True,
            occupied_dates=_occupied_dates(bookings),
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def start_cancellation(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    try:
        cars = get_active_cars()
    except Exception:
        logger.exception("Could not load cars for cancellation")
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=main_keyboard(),
        )
        return
    if not cars:
        await message.answer(
            "В листе Cars нет активных автомобилей.",
            reply_markup=main_keyboard(),
        )
        return
    await state.update_data(cars=cars)
    await state.set_state(CancellationForm.car)
    await message.answer(
        "🚫 Отмена брони.\n\nВыберите автомобиль:",
        reply_markup=cancellation_car_keyboard(cars),
    )


async def cancellation_car(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cars = data.get("cars", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        car = cars[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите автомобиль кнопкой из списка.")
        return

    try:
        bookings = get_cancellable_bookings_for_car(_car_id(car), _car_plate(car))
    except Exception:
        logger.exception("Could not load bookings for cancellation")
        await state.clear()
        await message.answer(
            "Не удалось получить список активных броней.",
            reply_markup=main_keyboard(),
        )
        return
    if not bookings:
        await state.clear()
        await message.answer(
            f"Для автомобиля {_car_name(car)} ({_car_plate(car)}) активных броней нет.",
            reply_markup=main_keyboard(),
        )
        return
    await state.update_data(selected_car=car, cancel_bookings=bookings)
    await state.set_state(CancellationForm.booking)
    selection_message = await message.answer(
        "Выберите бронь для отмены:",
        reply_markup=cancellation_booking_keyboard(bookings),
    )
    await state.update_data(
        cancellation_booking_message_id=selection_message.message_id
    )


async def cancellation_booking_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != CancellationForm.booking.state:
        await callback.answer(
            "Этот список устарел. Начните отмену заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "cancellation_booking_message_id"
    ):
        await callback.answer(
            "Этот список устарел. Используйте последний список броней.",
            show_alert=True,
        )
        return
    try:
        booking_index = int(callback.data.split(":", 1)[1])
        booking = data["cancel_bookings"][booking_index]
    except (ValueError, KeyError, IndexError, TypeError):
        await callback.answer("Список броней устарел.", show_alert=True)
        return
    await state.update_data(selected_booking=booking)
    await state.set_state(CancellationForm.reason)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Введите причину отмены брони №{_booking_id(booking)}:"
    )


async def cancellation_reason(message: Message, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    if len(reason) < 2:
        await message.answer("Укажите причину отмены текстом.")
        return
    await state.update_data(cancellation_reason=reason)
    await state.set_state(CancellationForm.deposit_status)
    deposit_message = await message.answer(
        "Что произошло с залогом/предоплатой?",
        reply_markup=cancellation_deposit_keyboard(),
    )
    await state.update_data(
        cancellation_deposit_message_id=deposit_message.message_id
    )


async def cancellation_deposit_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != CancellationForm.deposit_status.state:
        await callback.answer(
            "Этот выбор устарел. Начните отмену заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "cancellation_deposit_message_id"
    ):
        await callback.answer(
            "Этот выбор устарел. Используйте последние кнопки.",
            show_alert=True,
        )
        return
    deposit_status = {
        "returned": "Возвращен",
        "retained": "Удержан",
    }.get(callback.data.removeprefix("cancel_deposit:"))
    if not deposit_status:
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    booking = data.get("selected_booking")
    car = data.get("selected_car")
    if not booking or not car:
        await callback.answer(
            "Операция устарела. Начните отмену заново.",
            show_alert=True,
        )
        return
    try:
        cancel_booking(
            booking_id=int(_booking_id(booking)),
            reason=data.get("cancellation_reason", ""),
            deposit_status=deposit_status,
        )
    except Exception:
        logger.exception("Could not cancel booking")
        await state.clear()
        await callback.answer("Не удалось отменить бронь.", show_alert=True)
        await callback.message.answer(
            "Не удалось обновить бронь в Google Sheets.",
            reply_markup=main_keyboard(),
        )
        return
    await state.clear()
    await callback.answer("Бронь отменена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Бронь №{_booking_id(booking)} отменена.\n"
        f"Автомобиль: {_car_name(car)} ({_car_plate(car)})\n"
        f"Статус залога: {deposit_status}.\n"
        "Даты автомобиля освобождены.",
        reply_markup=main_keyboard(),
    )


def _calendar_minimum_date(
    flow: str,
    stage: str,
    data: dict,
) -> datetime.date | None:
    if flow == "occupancy":
        return None
    if stage == "end" and data.get("start_date"):
        return datetime.date.fromisoformat(data["start_date"])
    return datetime.date.today()


async def calendar_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if parts[1] == "noop":
        await callback.answer()
        return

    if len(parts) < 5:
        await callback.answer("Календарь устарел. Откройте операцию заново.", show_alert=True)
        return

    flow = parts[1]
    stage = parts[2]
    data = await state.get_data()
    expected_states = {
        ("availability", "start"): AvailabilityForm.start_date.state,
        ("availability", "end"): AvailabilityForm.end_date.state,
        ("booking", "start"): BookingForm.start_date.state,
        ("booking", "end"): BookingForm.end_date.state,
        ("occupancy", "calendar"): OccupancyForm.calendar.state,
    }
    expected_state = expected_states.get((flow, stage))
    current_state = await state.get_state()
    if expected_state is None or current_state != expected_state:
        await callback.answer(
            "Этот календарь устарел. Откройте нужную операцию заново.",
            show_alert=True,
        )
        return
    if flow == "booking" and data.get("booking_mode") not in (
        "reservation",
        "issuance",
    ):
        await callback.answer(
            "Этот календарь устарел. Откройте бронь заново.",
            show_alert=True,
        )
        return
    if getattr(callback.message, "message_id", None) != data.get(
        "calendar_message_id"
    ):
        await callback.answer(
            "Этот календарь устарел. Используйте последний календарь.",
            show_alert=True,
        )
        return
    minimum_date = _calendar_minimum_date(flow, stage, data)
    message = callback.message

    if len(parts) == 5 and parts[3] not in ("day", "busy"):
        try:
            year = int(parts[3])
            month = int(parts[4])
        except ValueError:
            await callback.answer("Не удалось открыть этот месяц.", show_alert=True)
            return
        await callback.answer()
        await message.edit_reply_markup(
            reply_markup=calendar_keyboard(
                flow,
                stage,
                minimum_date=minimum_date,
                year=year,
                month=month,
                allow_past=flow == "occupancy",
                occupied_dates=(
                    _occupied_dates(data.get("occupancy_bookings", []))
                    if flow == "occupancy"
                    else None
                ),
            )
        )
        return

    if len(parts) == 5 and parts[3] == "busy":
        try:
            selected_date = datetime.date.fromisoformat(parts[4])
        except ValueError:
            await callback.answer("Некорректная дата.", show_alert=True)
            return
        occupied = _occupied_dates(data.get("occupancy_bookings", []))
        booking_ids = occupied.get(selected_date.isoformat(), [])
        if booking_ids:
            await callback.answer(
                "Дата занята: " + ", ".join(f"Бронь #{booking_id}" for booking_id in booking_ids),
                show_alert=True,
            )
        else:
            await callback.answer("Эта дата больше не занята.")
        return

    if len(parts) != 5 or parts[3] != "day":
        await callback.answer("Календарь устарел. Откройте операцию заново.", show_alert=True)
        return

    try:
        selected_date = datetime.date.fromisoformat(parts[4])
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return
    if minimum_date and selected_date < minimum_date:
        await callback.answer("Эта дата недоступна для выбора.", show_alert=True)
        return

    selected_value = selected_date.isoformat()
    if flow == "occupancy" and stage == "calendar":
        await callback.answer(
            f"Свободная дата: {selected_date.strftime('%d.%m.%Y')}"
        )
        return
    await callback.answer()

    if flow == "availability" and stage == "start":
        await state.update_data(start_date=selected_value)
        await state.set_state(AvailabilityForm.end_date)
        await message.edit_text(
            _calendar_prompt("availability", "end", selected_date),
            reply_markup=calendar_keyboard(
                "availability",
                "end",
                minimum_date=selected_date,
            ),
        )
        return

    if flow == "availability" and stage == "end":
        await message.edit_text(
            f"Период выбран: {data['start_date']} — {selected_value}"
        )
        await _finish_availability(message, state, data["start_date"], selected_value)
        return

    if flow == "booking" and stage == "start":
        await state.update_data(start_date=selected_value)
        await state.set_state(BookingForm.end_date)
        booking_title = (
            "Новая бронь"
            if data.get("booking_mode") == "reservation"
            else "Выдача авто"
        )
        await message.edit_text(
            f"{booking_title}.\n\n" + _calendar_prompt("booking", "end", selected_date),
            reply_markup=calendar_keyboard(
                "booking",
                "end",
                minimum_date=selected_date,
            ),
        )
        return

    if flow == "booking" and stage == "end":
        await message.edit_text(
            f"Период брони выбран: {data['start_date']} — {selected_value}"
        )
        await _finish_booking_dates(message, state, data["start_date"], selected_value)
        return

    await callback.answer("Календарь устарел. Откройте операцию заново.", show_alert=True)


async def booking_car(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cars = data.get("cars", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        car = cars[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите автомобиль кнопкой из списка.")
        return
    await state.update_data(
        car_id=_car_id(car),
        car_name=_car_name(car),
    )
    await state.set_state(BookingForm.client_name)
    await message.answer("Введите ФИО клиента:")


async def booking_client_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Введите имя клиента полностью.")
        return
    await state.update_data(client_name=value)
    await state.set_state(BookingForm.phone)
    await message.answer("Введите номер телефона клиента:")


async def booking_phone(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 5:
        await message.answer("Проверьте номер телефона и попробуйте снова.")
        return
    await state.update_data(phone=value)
    await state.set_state(BookingForm.price)
    data = await state.get_data()
    if data.get("booking_mode") == "reservation":
        await message.answer("Введите итоговую стоимость аренды в RUB:")
    else:
        await message.answer("Введите принятую сумму аренды в RUB:")


async def booking_price(message: Message, state: FSMContext) -> None:
    price, error = _parse_int(
        message.text or "",
        "принятая сумма аренды",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(price=price)
    data = await state.get_data()
    if data.get("booking_mode") == "reservation":
        await state.set_state(BookingForm.prepayment)
        await message.answer(
            "Введите сумму предоплаты/залога бронирования в RUB "
            "(0, если без предоплаты):"
        )
        return
    await state.set_state(BookingForm.start_mileage)
    await message.answer("Введите начальный пробег автомобиля в км:")


async def booking_prepayment(message: Message, state: FSMContext) -> None:
    prepayment, error = _parse_int(
        message.text or "",
        "предоплата/залог бронирования",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(prepayment=prepayment)
    if prepayment == 0:
        await state.update_data(prepayment_pay_type="Без оплаты")
        await _send_reservation_confirmation(message, state)
        return
    await state.set_state(BookingForm.pay_type)
    payment_message = await message.answer(
        "Выберите способ оплаты предоплаты/залога:",
        reply_markup=payment_methods_keyboard("reservation"),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def booking_start_mileage(message: Message, state: FSMContext) -> None:
    start_mileage, error = _parse_int(
        message.text or "",
        "начальный пробег",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(start_mileage=start_mileage)
    await state.set_state(BookingForm.deposit)
    await message.answer("Введите сумму залога в RUB (0, если нет):")


async def booking_deposit(message: Message, state: FSMContext) -> None:
    deposit, error = _parse_int(
        message.text or "",
        "залог",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(deposit=deposit)
    await state.set_state(BookingForm.pay_type)
    payment_message = await message.answer(
        "Выберите форму оплаты принятой суммы аренды:",
        reply_markup=payment_methods_keyboard("issuance"),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def _send_booking_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    issued_at = data.get("issued_at") or _timestamp()
    await state.set_state(BookingForm.confirmation)
    await message.answer(
        "Проверьте акт выдачи авто:\n\n"
        f"Авто: {data['car_name']}\n"
        f"Период: {data['start_date']} — {data['end_date']}\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Принятая сумма аренды: {int(data['price']):,} RUB\n"
        f"Начальный пробег: {int(data['start_mileage']):,} км\n"
        f"Залог: {int(data['deposit']):,} RUB\n"
        f"Форма оплаты: {data['pay_type']}\n"
        f"Дата и время выдачи: {issued_at}".replace(",", " ")
        + "\n\nСохранить выдачу авто?",
        reply_markup=confirmation_keyboard(),
    )


async def _send_reservation_confirmation(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    await state.set_state(BookingForm.confirmation)
    await message.answer(
        "Проверьте новую бронь:\n\n"
        f"Авто: {data['car_name']}\n"
        f"Период: {data['start_date']} — {data['end_date']}\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Стоимость аренды: {int(data['price']):,} RUB\n"
        f"Предоплата/залог: {int(data['prepayment']):,} RUB\n"
        f"Тип оплаты залога: {data['prepayment_pay_type']}\n\n"
        "Сохранить бронь?".replace(",", " "),
        reply_markup=confirmation_keyboard(),
    )


async def booking_confirmation(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    manager_id = message.from_user.id if message.from_user else 0
    is_reservation = data.get("booking_mode") == "reservation"
    try:
        booking_id = add_new_booking(
            car_id=int(data["car_id"]),
            car_name=data["car_name"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            client_name=data["client_name"],
            phone=data["phone"],
            price=int(data["price"]),
            manager_id=manager_id,
            issued_at="" if is_reservation else _timestamp(),
            start_mileage=0 if is_reservation else int(data["start_mileage"]),
            deposit=0 if is_reservation else int(data["deposit"]),
            pay_type="Без оплаты" if is_reservation else data["pay_type"],
            status="Подтверждена" if is_reservation else "В аренде",
            prepayment=int(data.get("prepayment", 0)),
            prepayment_pay_type=data.get("prepayment_pay_type", "Без оплаты"),
        )
    except Exception:
        logger.exception("Could not save booking")
        await state.clear()
        await message.answer("Не удалось сохранить бронь в Google Sheets.", reply_markup=main_keyboard())
        return
    await state.clear()
    await message.answer(
        (
            f"Бронь №{booking_id} сохранена."
            if is_reservation
            else f"Акт выдачи авто по брони №{booking_id} сохранён. "
            "Автомобиль отмечен как выданный."
        ),
        reply_markup=main_keyboard(),
    )


async def list_active_rentals(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    try:
        rentals = get_active_rentals()
    except Exception:
        logger.exception("Could not load active rentals")
        await message.answer("Не удалось получить активные аренды.", reply_markup=main_keyboard())
        return
    if not rentals:
        await message.answer("Активных аренд нет.", reply_markup=main_keyboard())
        return
    await message.answer(
        "Активные аренды:\n\n"
        + "\n\n".join(
            f"Бронь №{_booking_id(item)}\n{_booking_label(item)}\n"
            f"Телефон: {_value(item, 'Телефон', 'Телефон клиента')}\n"
            f"Статус: {_value(item, 'Статус')}"
            for item in rentals
        ),
        reply_markup=main_keyboard(),
    )


async def start_return(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    try:
        rentals = get_returnable_rentals()
    except Exception:
        logger.exception("Could not load rentals for return")
        await message.answer("Не удалось получить список аренд.", reply_markup=main_keyboard())
        return
    if not rentals:
        await message.answer("Нет аренд, которые можно принять обратно.", reply_markup=main_keyboard())
        return
    await state.update_data(rentals=rentals)
    await state.set_state(ReturnForm.booking)
    await message.answer(
        "🏁 Приём авто.\n\nВыберите аренду для оформления:",
        reply_markup=rental_choice_keyboard(rentals),
    )


async def return_booking(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rentals = data.get("rentals", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        booking = rentals[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите аренду кнопкой из списка.")
        return

    try:
        car = get_car_by_id(_car_id(booking))
    except Exception:
        logger.exception("Could not load car for return")
        await state.clear()
        await message.answer("Не удалось получить данные автомобиля.", reply_markup=main_keyboard())
        return

    start_mileage_text = _value(
        car,
        "Текущий пробег (км)",
        "Пробег",
        "Текущий пробег",
        "Пробег (км)",
        "Пробег начальный",
        default="0",
    )
    start_mileage, _ = _parse_int(start_mileage_text, "начальный пробег", minimum=0)
    if start_mileage is None:
        start_mileage = 0
    base_price, _ = _parse_int(
        _value(
            booking,
            "Сумма брони (RUB)",
            "Принятая сумма аренды (RUB)",
            "Цена",
            "Стоимость",
            "Итоговая стоимость",
            default="0",
        ),
        "базовая стоимость",
        minimum=0,
    )
    if base_price is None:
        base_price = 0
    deposit, _ = _parse_int(
        _value(booking, "Залог (RUB)", "Залог", default="0"),
        "залог",
        minimum=0,
    )

    await state.update_data(
        booking=booking,
        car=car,
        start_mileage=start_mileage,
        base_price=base_price,
        deposit=deposit or 0,
        returned_at=_timestamp(),
        wash_fee=0,
    )
    await state.set_state(ReturnForm.end_mileage)
    await message.answer(
        f"Возврат брони №{_booking_id(booking)}.\n"
        f"Начальный пробег: {start_mileage} км.\n"
        "Введите конечный пробег в км:",
        reply_markup=ReplyKeyboardRemove(),
    )


async def return_end_mileage(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    end_mileage, error = _parse_int(message.text or "", "конечный пробег", minimum=0)
    if error:
        await message.answer(error)
        return
    if end_mileage < int(data["start_mileage"]):
        await message.answer("Конечный пробег не может быть меньше начального.")
        return
    await state.update_data(end_mileage=end_mileage)
    await state.set_state(ReturnForm.cleanliness)
    cleanliness_message = await message.answer(
        "Укажите состояние чистоты автомобиля:",
        reply_markup=cleanliness_keyboard(),
    )
    await state.update_data(
        cleanliness_message_id=cleanliness_message.message_id
    )


async def cleanliness_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != ReturnForm.cleanliness.state:
        await callback.answer(
            "Этот выбор устарел. Продолжите текущую операцию.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "cleanliness_message_id"
    ):
        await callback.answer(
            "Этот выбор устарел. Используйте последние кнопки.",
            show_alert=True,
        )
        return
    cleanliness = {
        "clean": "Чистая",
        "dirty": "Грязная",
    }.get(callback.data.removeprefix("cleanliness:"))
    if not cleanliness:
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    await state.update_data(cleanliness=cleanliness)
    await callback.answer(f"Выбрано: {cleanliness}")
    await callback.message.edit_reply_markup(reply_markup=None)
    if cleanliness == "Чистая":
        await state.update_data(wash_fee=0)
        await state.set_state(ReturnForm.extra_fee)
        await callback.message.answer(
            "Введите сумму дополнительных штрафов/удержаний в RUB (0, если нет):"
        )
        return
    await state.set_state(ReturnForm.wash_fee)
    await callback.message.answer(
        f"Введите стоимость мойки в RUB "
        f"(0 — применить стандартную сумму {DEFAULT_WASH_FEE:,} RUB):".replace(",", " ")
    )


async def return_wash_fee(message: Message, state: FSMContext) -> None:
    wash_fee, error = _parse_int(
        message.text or "",
        "стоимость мойки",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    if wash_fee == 0:
        wash_fee = DEFAULT_WASH_FEE
    await state.update_data(wash_fee=wash_fee)
    await state.set_state(ReturnForm.extra_fee)
    await message.answer(
        "Введите сумму дополнительных штрафов/удержаний в RUB (0, если нет):"
    )


async def return_extra_fee(message: Message, state: FSMContext) -> None:
    extra_fee, error = _parse_int(message.text or "", "дополнительные начисления", minimum=0)
    if error:
        await message.answer(error)
        return
    await state.update_data(extra_fee=extra_fee)
    await state.set_state(ReturnForm.total_paid)
    await message.answer("Введите фактически полученную сумму в RUB:")


async def return_total_paid(message: Message, state: FSMContext) -> None:
    total_paid, error = _parse_int(message.text or "", "фактически полученная сумма", minimum=0)
    if error:
        await message.answer(error)
        return
    await state.update_data(total_paid=total_paid)
    if total_paid == 0:
        await state.update_data(pay_type="Без оплаты")
        await state.set_state(ReturnForm.notes)
        await message.answer(
            "Фактически получено: 0 RUB.\n"
            "Способ оплаты автоматически установлен: Без оплаты.\n\n"
            "Введите примечание или отправьте «нет»:"
        )
        return
    await state.set_state(ReturnForm.pay_type)
    payment_message = await message.answer(
        "Выберите способ оплаты:",
        reply_markup=payment_methods_keyboard(),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def return_pay_type(message: Message, state: FSMContext) -> None:
    payment_message = await message.answer(
        "Выберите способ оплаты кнопкой ниже:",
        reply_markup=payment_methods_keyboard(),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def payment_method_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    payment_parts = callback.data.split(":")
    if len(payment_parts) == 2:
        flow = "return"
        payment_key = payment_parts[1]
    elif len(payment_parts) == 3:
        flow = payment_parts[1]
        payment_key = payment_parts[2]
    else:
        await callback.answer("Некорректный вариант оплаты.", show_alert=True)
        return
    pay_type = PAYMENT_METHODS.get(payment_key)
    if flow == "reservation" and payment_key == "legal":
        pay_type = "Безнал"
    if not pay_type:
        await callback.answer("Этот вариант оплаты недоступен.", show_alert=True)
        return
    current_state = await state.get_state()
    data = await state.get_data()
    valid_flow = (
        flow == "reservation"
        and current_state == BookingForm.pay_type.state
        and data.get("booking_mode") == "reservation"
    ) or (
        flow in ("issuance", "booking")
        and current_state == BookingForm.pay_type.state
        and data.get("booking_mode") == "issuance"
    ) or (
        flow == "return"
        and current_state == ReturnForm.pay_type.state
    )
    if not valid_flow:
        await callback.answer(
            "Этот выбор оплаты устарел. Продолжите текущую операцию.",
            show_alert=True,
        )
        return
    if getattr(callback.message, "message_id", None) != data.get(
        "payment_message_id"
    ):
        await callback.answer(
            "Этот выбор оплаты устарел. Используйте последние кнопки.",
            show_alert=True,
        )
        return
    await callback.answer(f"Выбрано: {pay_type}")
    await callback.message.edit_reply_markup(reply_markup=None)
    if flow == "reservation":
        await state.update_data(prepayment_pay_type=pay_type)
        await _send_reservation_confirmation(callback.message, state)
        return
    if flow in ("issuance", "booking"):
        await state.update_data(pay_type=pay_type)
        await _send_booking_confirmation(callback.message, state)
        return
    if flow == "return":
        await state.update_data(pay_type=pay_type)
        await state.set_state(ReturnForm.notes)
        await callback.message.answer("Введите примечание или отправьте «нет»:")
        return
    await callback.answer("Этот вариант оплаты устарел.", show_alert=True)


async def return_notes(message: Message, state: FSMContext) -> None:
    notes = (message.text or "").strip()
    if notes.lower() == "нет":
        notes = ""
    await state.update_data(notes=notes)
    data = await state.get_data()
    await state.set_state(ReturnForm.confirmation)
    delta = int(data["end_mileage"]) - int(data["start_mileage"])
    calculation = _return_calculation(data)
    total = (
        int(data["base_price"])
        + int(calculation["late_fee"])
        + int(calculation["over_mileage_fee"])
        + int(calculation["wash_fee"])
        + int(data["extra_fee"])
    )
    deposit_balance = int(calculation["deposit_balance"])
    deposit_result = (
        f"к возврату: {deposit_balance:,} RUB"
        if deposit_balance >= 0
        else f"доплата: {abs(deposit_balance):,} RUB"
    )
    await message.answer(
        "🏁 Акт приёма автомобиля\n\n"
        f"• Автомобиль: {_car_name(data['car'])} ({_car_plate(data['car'])})\n"
        f"• Период: {_value(data['booking'], 'Дата и время выдачи', 'Дата начала', 'Дата начала (ГГГГ-ММ-ДД)')} — "
        f"{data.get('returned_at', _timestamp())}\n"
        f"• Суммарный пробег: {delta:,} км "
        f"(Лимит: {int(calculation['mileage_limit']):,} км, "
        f"перепробег: {int(calculation['over_mileage']):,} км → "
        f"штраф: {int(calculation['over_mileage_fee']):,} RUB)\n"
        f"• Просрочка по времени: {int(calculation['late_hours'])} ч. → "
        f"штраф: {int(calculation['late_fee']):,} RUB\n"
        f"• Состояние: {calculation['cleanliness']}\n"
        f"• Сумма аренды: {int(data['base_price']):,} RUB\n"
        f"• Мойка: {int(calculation['wash_fee']):,} RUB\n"
        f"• Доп. штрафы/удержания: {int(data['extra_fee']):,} RUB\n"
        f"• Залог: {int(data['deposit']):,} RUB, {deposit_result}\n"
        f"• Итого к оплате: {total:,} RUB\n"
        f"• Получено дополнительно: {int(data['total_paid']):,} RUB\n"
        f"• Оплата: {data['pay_type']}\n"
        f"• Примечание: {notes or '—'}\n\n"
        "Сохранить приём автомобиля?".replace(",", " "),
        reply_markup=confirmation_keyboard(),
    )


async def return_confirmation(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    booking = data["booking"]
    car = data["car"]
    manager_id = message.from_user.id if message.from_user else 0
    calculation = _return_calculation(data)
    try:
        save_actual_return(
            booking_id=int(_booking_id(booking)),
            car_name=_value(booking, "Авто", "Название авто", "Модель"),
            plate=_car_plate(car),
            client=_value(booking, "Клиент", "Клиент ФИО", "Имя клиента"),
            start_mileage=int(data["start_mileage"]),
            end_mileage=int(data["end_mileage"]),
            delta_mileage=int(data["end_mileage"]) - int(data["start_mileage"]),
            base_price=int(data["base_price"]),
            extra_fee=int(data["extra_fee"]),
            total_paid=int(data["total_paid"]),
            pay_type=data["pay_type"],
            manager_id=manager_id,
            notes=data.get("notes", ""),
            returned_at=data.get("returned_at", _timestamp()),
            daily_rate=int(calculation["daily_rate"]),
            rental_days=int(calculation["rental_days"]),
            mileage_limit=int(calculation["mileage_limit"]),
            over_mileage=int(calculation["over_mileage"]),
            over_mileage_fee=int(calculation["over_mileage_fee"]),
            late_hours=int(calculation["late_hours"]),
            late_fee=int(calculation["late_fee"]),
            cleanliness=str(calculation["cleanliness"]),
            wash_fee=int(calculation["wash_fee"]),
            deposit=int(calculation["deposit"]),
            deposit_balance=int(calculation["deposit_balance"]),
        )
    except Exception:
        logger.exception("Could not save return")
        await state.clear()
        await message.answer("Не удалось сохранить возврат в Google Sheets.", reply_markup=main_keyboard())
        return
    await state.clear()
    await message.answer(
        f"Возврат брони №{_booking_id(booking)} оформлен. "
        "Бронь завершена, пробег автомобиля обновлён.",
        reply_markup=main_keyboard(),
    )


async def unknown_message(message: Message) -> None:
    if await ensure_manager(message):
        await message.answer("Выберите действие кнопками меню.", reply_markup=main_keyboard())


async def error_handler(event: object) -> None:
    logger.exception("Unhandled bot event: %s", event)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.callback_query.register(calendar_callback, F.data.startswith("calendar:"))
    dispatcher.callback_query.register(payment_method_callback, F.data.startswith("payment:"))
    dispatcher.callback_query.register(cleanliness_callback, F.data.startswith("cleanliness:"))
    dispatcher.callback_query.register(
        cancellation_booking_callback,
        F.data.startswith("cancel_booking:"),
    )
    dispatcher.callback_query.register(
        cancellation_deposit_callback,
        F.data.startswith("cancel_deposit:"),
    )
    dispatcher.message.register(start_handler, CommandStart())
    dispatcher.message.register(show_menu, Command("menu"))
    dispatcher.message.register(cancel_flow, F.text.casefold() == "отмена")

    dispatcher.message.register(start_availability, F.text == "Доступные авто")
    dispatcher.message.register(start_booking, F.text == "Новая бронь")
    dispatcher.message.register(start_issuance, F.text == "🚗 Выдача авто")
    dispatcher.message.register(list_active_rentals, F.text == "Активные аренды")
    dispatcher.message.register(start_return, F.text == "🏁 Приём авто")
    dispatcher.message.register(start_return, F.text == "Оформить возврат")
    dispatcher.message.register(start_occupancy, F.text == "🔍 Проверка броней")
    dispatcher.message.register(start_cancellation, F.text == "🚫 Отмена брони")

    dispatcher.message.register(availability_start_date, AvailabilityForm.start_date)
    dispatcher.message.register(availability_end_date, AvailabilityForm.end_date)

    dispatcher.message.register(booking_start_date, BookingForm.start_date)
    dispatcher.message.register(booking_end_date, BookingForm.end_date)
    dispatcher.message.register(booking_car, BookingForm.car)
    dispatcher.message.register(booking_client_name, BookingForm.client_name)
    dispatcher.message.register(booking_phone, BookingForm.phone)
    dispatcher.message.register(booking_price, BookingForm.price)
    dispatcher.message.register(booking_prepayment, BookingForm.prepayment)
    dispatcher.message.register(booking_start_mileage, BookingForm.start_mileage)
    dispatcher.message.register(booking_deposit, BookingForm.deposit)
    dispatcher.message.register(booking_confirmation, BookingForm.confirmation)

    dispatcher.message.register(occupancy_car, OccupancyForm.car)

    dispatcher.message.register(cancellation_car, CancellationForm.car)
    dispatcher.message.register(cancellation_reason, CancellationForm.reason)

    dispatcher.message.register(return_booking, ReturnForm.booking)
    dispatcher.message.register(return_end_mileage, ReturnForm.end_mileage)
    dispatcher.message.register(return_wash_fee, ReturnForm.wash_fee)
    dispatcher.message.register(return_extra_fee, ReturnForm.extra_fee)
    dispatcher.message.register(return_total_paid, ReturnForm.total_paid)
    dispatcher.message.register(return_pay_type, ReturnForm.pay_type)
    dispatcher.message.register(return_notes, ReturnForm.notes)
    dispatcher.message.register(return_confirmation, ReturnForm.confirmation)

    dispatcher.message.register(unknown_message)
    dispatcher.errors.register(error_handler)
    return dispatcher


async def run_bot() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    bot = Bot(token=TOKEN)
    dispatcher = create_dispatcher()
    scheduler = create_notification_scheduler(bot)
    catchup_task: asyncio.Task | None = None
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        scheduler.start()
        catchup_task = asyncio.create_task(
            send_missed_tomorrow_notifications(bot)
        )
        logger.info(
            "Daily tomorrow plan scheduled for 20:00 (%s)",
            NOTIFICATION_TIMEZONE_NAME,
        )
        logger.info("Car rental bot started")
        await dispatcher.start_polling(bot)
    except TelegramAPIError:
        logger.exception("Telegram API error")
        raise
    finally:
        if scheduler.running:
            with suppress(Exception):
                scheduler.shutdown(wait=False)
        if catchup_task and not catchup_task.done():
            catchup_task.cancel()
            with suppress(asyncio.CancelledError):
                await catchup_task
        with suppress(Exception):
            await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()