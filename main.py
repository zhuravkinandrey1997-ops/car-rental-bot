"""Telegram entry point for the car-rental management bot."""

from __future__ import annotations

import asyncio
import calendar
import csv
import datetime
import html
import io
import logging
import math
import os
import re
from contextlib import suppress
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from aiogram_calendar.schemas import SimpleCalAct
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from sheets_client import (
    add_expense,
    add_new_booking,
    delete_expense,
    cancel_booking,
    colorize_existing_booking_rows,
    get_active_rentals,
    get_partner_active_rentals,
    get_active_bookings_for_car,
    get_all_cars,
    get_available_cars,
    get_available_cars_for_direct_issuance,
    get_booking_by_id,
    get_car_by_id,
    get_car_by_plate,
    get_cancellable_cars,
    get_cancellable_bookings_for_car,
    get_confirmed_bookings,
    get_available_financial_months,
    get_available_expense_months,
    get_expenses_for_month,
    get_financial_report,
    get_expiring_rental_alerted_booking_ids,
    get_recent_expenses,
    get_pending_notification_manager_ids,
    get_returnable_rentals,
    get_tomorrow_bookings,
    get_partner_bookings,
    get_partner_cars,
    get_partner_expenses,
    get_manager_access,
    ensure_sheets_ready,
    SheetsStartupError,
    is_manager_authorized,
    is_partner_authorized,
    issue_booking,
    mark_notification_sent,
    mark_expiring_rental_alert_sent,
    save_actual_return,
    save_direct_issuance,
    safe_int,
    add_public_booking,
    update_public_booking_status,
    update_booking_planned_end,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("car-rental-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLIENT_BOT_TOKEN = os.getenv("CLIENT_BOT_TOKEN")
NOTIFY_MANAGERS = [661842368, 952125424, 1574781782]
PARTNER_ID = 226694751
PARTNER_CAR_KEYWORD = "Dodge"
HEAVY_LUX_PARTNER_ROLE = "partner_heavy_lux"
ADMIN_PLUS_ROLE = "администратор плюс"
HEAVY_LUX_PARTNER_SOURCE = "heavy_lux_cr"
LEON_RENTAL_SOURCE = "Леон Рентал"
CLIENT_BOOKING_SOURCE = "Сайт"
CLIENT_DELIVERY_FEE = 3000
CLIENT_PREPAYMENT_RATE = 0.30
CLIENT_PAYMENT_BANK = "Сбербанк"
CLIENT_PAYMENT_PHONE = "+79110326262"
CLIENT_PAYMENT_RECIPIENT = "Лунева К.Д."
SOURCE_SELECTION_PROMPT = "От чьего имени оформляется операция?"
CLIENT_FAQ_RULES = (
    (
        ("залог", "депозит", "возврат"),
        "Страховой депозит обязателен для каждого автомобиля, зависит от выбранной модели и возвращается в полном объеме после проверки штрафов.",
    ),
    (
        ("возраст", "стаж", "требования", "документы", "условия"),
        "Требования к арендатору: возраст от 21 года, водительский стаж от 2 лет. Для оформления требуются паспорт РФ и водительское удостоверение.",
    ),
    (
        ("пулково", "аэропорт", "доставка", "подача", "адрес"),
        "Доставка автомобилей по Санкт-Петербургу и встреча в аэропорту Пулково осуществляются круглосуточно (24/7). Стоимость и время подачи согласовываются при оформлении.",
    ),
    (
        ("авто", "машины", "наличие", "свободные", "прайс", "цены"),
        "Актуальный автопарк и занятость синхронизируются в реальном времени. Нажмите кнопку ниже, чтобы открыть каталог и выбрать свободный автомобиль на ваши даты.",
    ),
    (
        ("оплата", "реквизиты", "перевод", "сбер"),
        "Официальные реквизиты для бронирования и депозита: Сбербанк +79110326262.",
    ),
)
CLIENT_FAQ_FALLBACK = (
    "С удовольствием проконсультируем вас индивидуально! Напишите нашему менеджеру "
    "@leonRentalSPB или позвоните: +7 (911) 215-56-62."
)

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
    source = State()
    issuance_mode = State()
    issuance_booking = State()
    direct_car_confirmation = State()
    issue_date = State()
    issue_time = State()
    start_date = State()
    end_date = State()
    direct_end_date = State()
    car = State()
    client_name = State()
    phone = State()
    delivery = State()
    delivery_address = State()
    delivery_price = State()
    price = State()
    prepayment = State()
    start_mileage = State()
    deposit = State()
    deposit_pay_type = State()
    pay_type = State()
    confirmation = State()


class ClientBookingState(StatesGroup):
    car = State()
    start_date = State()
    end_date = State()
    delivery = State()
    delivery_address = State()
    client_name = State()
    phone = State()
    receipt = State()
    confirmation = State()


class OccupancyForm(StatesGroup):
    car = State()
    calendar = State()


class CancellationForm(StatesGroup):
    car = State()
    booking = State()
    confirmation = State()
    reason = State()
    deposit_status = State()


class ReturnForm(StatesGroup):
    source = State()
    booking = State()
    end_mileage = State()
    direct_deposit = State()
    cleanliness = State()
    fuel_level = State()
    wash_fee = State()
    extra_fee = State()
    penalty_decision = State()
    manual_penalty = State()
    rental_total_decision = State()
    rental_total_override = State()
    rental_paid = State()
    rental_pay_type = State()
    total_paid = State()
    pay_type = State()
    notes = State()
    confirmation = State()


class OverdueExtensionForm(StatesGroup):
    end_at = State()


class ExpenseForm(StatesGroup):
    amount = State()
    category = State()
    vehicle = State()
    note = State()
    confirmation = State()


class ExpenseCancellationForm(StatesGroup):
    selection = State()
    confirmation = State()


class FinancialReportForm(StatesGroup):
    month = State()
    report_type = State()
    cars = State()


class ExpenseHistoryForm(StatesGroup):
    month = State()
    filter = State()
    car = State()


ADMIN_IDS = {
    661842368,
    946721277,
    1574781782,
    952125424,
}
FINANCE_MANAGER_IDS = frozenset(ADMIN_IDS)
EXPENSE_CATEGORIES = (
    "Мойка / Детейлинг",
    "ТО / Ремонт",
    "Топливо",
    "Страховка / Штрафы",
    "Офис / Сервисы / Реклама",
    "Прочее",
)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = year * 12 + month - 1 + delta
    return month_index // 12, month_index % 12 + 1


def calendar_keyboard(
    flow: str,
    stage: str,
    minimum_date: datetime.date | None = None,
    maximum_date: datetime.date | None = None,
    year: int | None = None,
    month: int | None = None,
    occupied_dates: dict[str, list[str]] | None = None,
    allow_past: bool = False,
    selected_start_date: datetime.date | None = None,
    selected_end_date: datetime.date | None = None,
    locked: bool = False,
) -> InlineKeyboardMarkup:
    if not allow_past:
        minimum_date = minimum_date or datetime.date.today()
    year = year or (minimum_date or datetime.date.today()).year
    month = month or (minimum_date or datetime.date.today()).month
    minimum_month = (
        (minimum_date.year, minimum_date.month) if minimum_date else None
    )
    maximum_month = (
        (maximum_date.year, maximum_date.month) if maximum_date else None
    )
    previous_year, previous_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)

    def day_callback(current_date: datetime.date, action: str = "day") -> str:
        if locked:
            return "calendar:noop"
        return (
            f"calendar:{flow}:{stage}:{action}:{current_date.isoformat()}"
        )

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
                    "calendar:noop"
                    if locked
                    else f"calendar:{flow}:{stage}:{previous_year}:{previous_month}"
                    if (
                        minimum_month is None
                        or (previous_year, previous_month) > minimum_month
                    )
                    else "calendar:noop"
                ),
            ),
            InlineKeyboardButton(
                text="Сегодня",
                callback_data=(
                    "calendar:noop"
                    if locked
                    else f"calendar:{flow}:{stage}:{datetime.date.today().year}:"
                    f"{datetime.date.today().month}"
                ),
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=(
                    "calendar:noop"
                    if locked
                    or (
                        maximum_month is not None
                        and (next_year, next_month) > maximum_month
                    )
                    else f"calendar:{flow}:{stage}:{next_year}:{next_month}"
                ),
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
            if (
                selected_start_date
                and selected_end_date
                and current_date == selected_start_date == selected_end_date
            ):
                row.append(
                    InlineKeyboardButton(
                        text=f"🟢🔴 {day}",
                        callback_data=day_callback(current_date),
                    )
                )
            elif selected_end_date and current_date == selected_end_date:
                row.append(
                    InlineKeyboardButton(
                        text=f"🔴 {day}",
                        callback_data=day_callback(current_date),
                    )
                )
            elif selected_start_date and current_date == selected_start_date:
                row.append(
                    InlineKeyboardButton(
                        text=f"🟢 {day}",
                        callback_data=day_callback(current_date),
                    )
                )
            elif (
                selected_start_date
                and selected_end_date
                and selected_start_date < current_date < selected_end_date
            ):
                row.append(
                    InlineKeyboardButton(
                        text=f"▫️ {day}",
                        callback_data=day_callback(current_date),
                    )
                )
            elif minimum_date and current_date < minimum_date:
                row.append(InlineKeyboardButton(text="·", callback_data="calendar:noop"))
            elif maximum_date and current_date > maximum_date:
                row.append(InlineKeyboardButton(text="·", callback_data="calendar:noop"))
            elif occupied_dates and current_date.isoformat() in occupied_dates:
                row.append(
                    InlineKeyboardButton(
                        text=f"🔴 {day}",
                        callback_data=day_callback(current_date, "busy"),
                    )
                )
            else:
                row.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=day_callback(current_date),
                    )
                )
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _calendar_prompt(flow: str, stage: str, minimum_date: datetime.date | None = None) -> str:
    if flow == "issuance" and stage == "issue":
        return (
            "📅 Выберите дату фактической выдачи в календаре.\n"
            "Прошедшие даты доступны для оформления задним числом. "
            "Будущие даты недоступны."
        )
    if flow == "issuance" and stage == "end":
        return (
            f"🟢 Дата выдачи: {format_date_ru(minimum_date.isoformat())}. "
            "Теперь выберите дату окончания аренды:"
        )
    minimum_text = ""
    if minimum_date:
        minimum_text = (
            f"\nМинимальная дата: "
            f"{format_date_ru(minimum_date.isoformat())}."
        )
    if stage == "start":
        return (
            "Выберите дату начала аренды в календаре."
            "\nМожно также ввести дату текстом в формате ГГГГ-ММ-ДД."
            + minimum_text
        )
    if minimum_date:
        return (
            f"🟢 Дата начала: {format_date_ru(minimum_date.isoformat())}. "
            "Теперь выберите 🔴 Дату окончания (по):"
            "\nМожно также ввести дату текстом в формате ГГГГ-ММ-ДД."
        )
    return (
        "Выберите дату окончания аренды в календаре."
        "\nМожно также ввести дату текстом в формате ГГГГ-ММ-ДД."
        + minimum_text
    )


def _period_selection_text(start_date: str, end_date: str) -> str:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    total_days = (end - start).days + 1
    return (
        f"📅 Выбран период: 🟢 {format_date_ru(start_date)} ➔ "
        f"🔴 {format_date_ru(end_date)} "
        f"(всего {total_days} дней)"
    )


def partner_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Брони Dodge")],
            [KeyboardButton(text="🚗 Статус Dodge")],
            [KeyboardButton(text="📑 Расходы Dodge")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_main_keyboard(
    user_id: int,
    role: str | None = None,
) -> ReplyKeyboardMarkup:
    uid = int(user_id)
    if role is None and uid != PARTNER_ID:
        try:
            access = get_manager_access(uid)
            if access and access["active"]:
                role = access["role"]
        except Exception:
            logger.exception("Could not resolve role for menu")
    normalized_role = str(role or "").strip().lower()
    if normalized_role == HEAVY_LUX_PARTNER_ROLE:
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
                    KeyboardButton(text="🔎 Проверка броней"),
                ],
                [KeyboardButton(text="📈 Фин. отчет")],
                [KeyboardButton(text="Отмена")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )
    if uid == PARTNER_ID or normalized_role in {
        "партнёр",
        "партнер",
        "partner",
        HEAVY_LUX_PARTNER_ROLE,
    }:
        return partner_menu_keyboard()
    buttons = [
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
            KeyboardButton(text="🔎 Проверка броней"),
        ],
    ]
    if normalized_role in {"администратор", "admin", ADMIN_PLUS_ROLE}:
        buttons.append(
            [
                KeyboardButton(text="💸 Внести расход"),
                KeyboardButton(text="📈 Фин. отчет"),
            ]
        )
        buttons.append([KeyboardButton(text="📑 Просмотр расходов")])
    if normalized_role == ADMIN_PLUS_ROLE:
        buttons.append([KeyboardButton(text="🚫 Отмена брони")])
        buttons.append([KeyboardButton(text="❌ Отменить расход")])
    buttons.append([KeyboardButton(text="Отмена")])
    return ReplyKeyboardMarkup(
        keyboard=buttons,
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


def return_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить сохранение",
                    callback_data="confirm_return_save",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_return",
                ),
            ],
        ]
    )


def issuance_mode_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 По существующей брони")],
            [KeyboardButton(text="⚡ Срочная выдача (без брони)")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def source_selection_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=f"🏢 {LEON_RENTAL_SOURCE}"),
                KeyboardButton(text=f"🤝 {HEAVY_LUX_PARTNER_SOURCE}"),
            ],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _source_from_choice(value: str) -> str | None:
    normalized = (value or "").strip()
    choices = {
        f"🏢 {LEON_RENTAL_SOURCE}": LEON_RENTAL_SOURCE,
        f"🤝 {HEAVY_LUX_PARTNER_SOURCE}": HEAVY_LUX_PARTNER_SOURCE,
        LEON_RENTAL_SOURCE: LEON_RENTAL_SOURCE,
        HEAVY_LUX_PARTNER_SOURCE: HEAVY_LUX_PARTNER_SOURCE,
    }
    return choices.get(normalized)


def _operation_source(data: dict) -> str:
    if data.get("partner_mode"):
        return HEAVY_LUX_PARTNER_SOURCE
    return data.get("source") or LEON_RENTAL_SOURCE


def direct_deposit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Вернуть залог",
                    callback_data="direct_deposit:return",
                ),
                InlineKeyboardButton(
                    text="⚠️ Удержать залог",
                    callback_data="direct_deposit:retain",
                ),
            ]
        ]
    )


def delivery_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Нужна доставка",
                    callback_data="delivery_yes",
                ),
                InlineKeyboardButton(
                    text="🏢 Самовывоз",
                    callback_data="delivery_no",
                ),
            ]
        ]
    )


def issuance_date_keyboard() -> InlineKeyboardMarkup:
    current_time = _now_moscow().strftime("%H:%M")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⏱ Сейчас ({current_time})",
                    callback_data="issue_date_now",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Другая дата",
                    callback_data="issue_date_calendar",
                )
            ],
        ]
    )


PAYMENT_METHODS = {
    "cash": "Наличные",
    "card": "Банковская карта",
    "sbp": "Перевод (СБП)",
    "legal": "Безнал (Юр. лицо)",
}


DEFAULT_WASH_FEE = 1000
RENTAL_GRACE_PERIOD_MINUTES = 120
MINSK_TZ = ZoneInfo("Europe/Minsk")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
NOTIFICATION_TIMEZONE_NAME = os.getenv("RENTAL_TIMEZONE", "Europe/Moscow")
try:
    NOTIFICATION_TZ = ZoneInfo(NOTIFICATION_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(
        f"Unknown RENTAL_TIMEZONE: {NOTIFICATION_TIMEZONE_NAME}"
    ) from exc
NOTIFICATION_SEND_LOCK = asyncio.Lock()
EXPIRING_RENTAL_ALERTED_BOOKING_IDS: set[str] = set()
OVERDUE_1H_ALERTED_BOOKING_IDS: set[str] = set()
UPCOMING_BOOKING_NOTIFIED_IDS: set[str] = set()


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


def time_mileage_penalty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Нет, без штрафа",
                    callback_data="return_penalty:none",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Ввести сумму штрафа вручную",
                    callback_data="return_penalty:manual",
                ),
            ],
        ]
    )


def rental_total_keyboard(total_price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Оставить {_format_number(total_price)} RUB",
                    callback_data="return_rental_total:keep",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Изменить общую стоимость (скидка)",
                    callback_data="return_rental_total:discount",
                ),
            ],
        ]
    )


def overdue_rental_keyboard(booking_id: object) -> InlineKeyboardMarkup:
    booking_key = str(booking_id).strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏁 Закрыть (Приём авто)",
                    callback_data=f"return_car:{booking_key}",
                ),
                InlineKeyboardButton(
                    text="⏳ Продлить аренду",
                    callback_data=f"extend_rent:{booking_key}",
                ),
            ]
        ]
    )


def upcoming_handover_keyboard(booking_id: object) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Перейти к выдаче",
                    callback_data=f"handover_car:{str(booking_id).strip()}",
                )
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
            f"#{_booking_id(booking)}: "
            f"{format_date_ru(_value(booking, 'Дата начала (ГГГГ-ММ-ДД)', 'Дата начала'))} — "
            f"{format_date_ru(_value(booking, 'Дата окончания (ГГГГ-ММ-ДД)', 'Дата окончания'))} | "
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


def cancellation_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить",
                    callback_data="cancel_booking_confirm",
                ),
                InlineKeyboardButton(
                    text="↩️ Назад",
                    callback_data="cancel_booking_back",
                ),
            ]
        ]
    )


def cancellation_deposit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Задаток возвращен клиенту",
                    callback_data="cancel_deposit:returned",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Задаток удержан автопрокатом",
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


def direct_issuance_car_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        status = _value(car, "Статус", default="Не указан")
        normalized_status = status.casefold()
        if normalized_status in {"свободен", "активен", "free", "active"}:
            icon = "🟢"
        elif normalized_status in {"в аренде", "rented"}:
            icon = "🔴"
        else:
            icon = "🟡"
        builder.add(
            KeyboardButton(
                text=f"{index}. {icon} {_car_name(car)} ({status})"
            )
        )
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def direct_issuance_warning_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data="direct_issuance_car:yes",
                ),
                InlineKeyboardButton(
                    text="Назад",
                    callback_data="direct_issuance_car:back",
                ),
            ]
        ]
    )


def car_choice_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        builder.add(KeyboardButton(text=f"{index}. {_car_label(car)}"))
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def client_booking_car_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        builder.add(
            KeyboardButton(
                text=f"{index}. {_car_name(car)} ({_car_plate(car) or 'Без номера'})"
            )
        )
    builder.adjust(1)
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def client_welcome_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🏎 Автопарк и цены"),
                KeyboardButton(text="📅 Забронировать онлайн"),
            ],
            [
                KeyboardButton(text="✈️ Подача в Пулково"),
                KeyboardButton(text="📋 Условия и залог"),
            ],
            [KeyboardButton(text="👤 Связь с менеджером")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def client_ai_booking_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏎 Забронировать автомобиль",
                    callback_data="client_ai:book",
                )
            ]
        ]
    )


def client_booking_entry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Забронировать онлайн",
                    callback_data="client_booking:start",
                )
            ]
        ]
    )


def client_booking_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить / В меню",
                    callback_data="client_booking:cancel",
                )
            ]
        ]
    )


def client_car_booking_keyboard(car: dict) -> InlineKeyboardMarkup:
    car_id = _car_id(car)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📅 Забронировать {_car_name(car)}"[:64],
                    callback_data=f"client_booking:car:{car_id}",
                )
            ]
        ]
    )


def client_fleet_selection_keyboard(cars: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for car in cars:
        car_id = _car_id(car)
        if not car_id:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📅 Забронировать {_car_name(car)}"[:64],
                    callback_data=f"client_booking:car:{car_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отменить / В меню",
                callback_data="client_booking:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✈️ Подача в Пулково (+ 3 000 RUB)",
                    callback_data="client_delivery:airport",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📍 Доставка по адресу (+ 3 000 RUB)",
                    callback_data="client_delivery:address",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏢 Забрать в офисе (Бесплатно)",
                    callback_data="client_delivery:office",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить / В меню",
                    callback_data="client_booking:cancel",
                )
            ],
        ]
    )


def client_delivery_address_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Назад к способам получения",
                    callback_data="client_delivery:back",
                )
            ]
        ]
    )


def client_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
            [KeyboardButton(text="Ввести Telegram username")],
            [KeyboardButton(text="❌ Отменить / В меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def client_cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить / В меню")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def client_pulkovo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Заказать подачу в аэропорт",
                    callback_data="client_booking:pulkovo",
                )
            ]
        ]
    )


def client_manager_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать в Telegram: @leonRentalSPB",
                    url="https://t.me/leonRentalSPB",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📞 Позвонить: +7 (911) 215-56-62",
                    url="tel:+79112155662",
                )
            ],
        ]
    )


def client_admin_booking_keyboard(booking_id: object) -> InlineKeyboardMarkup:
    booking_key = str(booking_id).strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить бронь",
                    callback_data=f"client_booking:confirm:{booking_key}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"client_booking:reject:{booking_key}",
                ),
            ]
        ]
    )


def expense_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=category,
                    callback_data=f"expense_category:{index}",
                )
            ]
            for index, category in enumerate(EXPENSE_CATEGORIES)
        ]
    )


def expense_vehicle_keyboard(cars: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, car in enumerate(cars, start=1):
        plate = _value(
            car,
            "Госномер",
            "Номер авто",
            "Номер",
            "Plate",
            default="",
        )
        builder.add(
            KeyboardButton(
                text=f"{index}. {_car_name(car)} ({plate or 'без госномера'})"
            )
        )
    builder.adjust(1)
    builder.row(KeyboardButton(text="🏢 Общие расходы (весь автопарк)"))
    builder.row(KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def expense_cancellation_keyboard(
    expenses: list[dict],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, expense in enumerate(expenses):
        raw_amount = str(expense.get("Сумма (RUB)") or "0")
        amount = _format_number(raw_amount.replace(" ", "").replace(",", ""))
        date = format_date_ru(_value(expense, "Дата", default="—"))
        category = _value(expense, "Категория / На что", default="—")
        vehicle = _value(
            expense,
            "Автомобиль (Госномер / Общие)",
            default="—",
        )
        callback_data = f"expense_cancel_select:{index}"
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=(
                            f"❌ ID: {expense.get('ID Записи', '—')} · "
                            f"{date} · {amount} RUB"
                        )[:64],
                        callback_data=callback_data,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"Статья: {category}"[:64],
                        callback_data=callback_data,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"Авто: {vehicle}"[:64],
                        callback_data=callback_data,
                    )
                ],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def financial_month_keyboard(months: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_month = _now_minsk().strftime("%Y-%m")
    previous_month = (
        _now_minsk().date().replace(day=1) - datetime.timedelta(days=1)
    ).strftime("%Y-%m")
    for month in months:
        label = ""
        if month == current_month:
            label = "Текущий месяц"
        elif month == previous_month:
            label = "Предыдущий месяц"
        text = f"{label} ({month})" if label else month
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"financial_month:{month}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def expense_history_month_keyboard(months: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_month = _now_minsk().strftime("%Y-%m")
    previous_month = (
        _now_minsk().date().replace(day=1) - datetime.timedelta(days=1)
    ).strftime("%Y-%m")
    for month in months:
        label = ""
        if month == current_month:
            label = "Текущий"
        elif month == previous_month:
            label = "Прошлый"
        text = f"{label} ({month})" if label else month
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"expense_history_month:{month}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def expense_history_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏢 Все расходы за месяц",
                    callback_data="expense_history_filter:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚗 Фильтр по автомобилю",
                    callback_data="expense_history_filter:cars",
                )
            ],
        ]
    )


def expense_history_car_keyboard(cars: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, car in enumerate(cars):
        label = f"{_car_name(car)} ({_car_plate(car) or 'без госномера'})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"expense_history_car:{index}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🏢 Общие расходы",
                callback_data="expense_history_car:general",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def expense_history_export_keyboard(
    month_key: str,
    vehicle_filter: str | None = None,
) -> InlineKeyboardMarkup:
    filter_code = vehicle_filter or "all"
    callback_data = (
        f"expense_export:{month_key}:{quote(filter_code, safe='')}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬇️ Скачать CSV для бухгалтерии",
                    callback_data=callback_data,
                )
            ]
        ]
    )


def financial_report_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🏢 {LEON_RENTAL_SOURCE}",
                    callback_data="financial_type:leon",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🤝 {HEAVY_LUX_PARTNER_SOURCE}",
                    callback_data="financial_type:heavy_lux_cr",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Сводный (Общий)",
                    callback_data="financial_type:all",
                )
            ],
        ]
    )


def financial_car_selection_keyboard(
    cars: list[dict],
    selected_indexes: list[int] | set[int],
) -> InlineKeyboardMarkup:
    selected = set(selected_indexes)
    rows: list[list[InlineKeyboardButton]] = []
    for index, car in enumerate(cars):
        marker = "✅" if index in selected else "⬜"
        label = f"{_car_name(car)} ({_car_plate(car) or 'без госномера'})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {label}"[:64],
                    callback_data=f"financial_car_toggle:{index}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Рассчитать по выбранным",
                callback_data="financial_car_calculate",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _value(record: dict, *keys: str, default: str = "—") -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def format_date_ru(date_str: str) -> str:
    """Преобразует дату из YYYY-MM-DD или ISO в DD.MM.YYYY."""
    if not date_str:
        return ""
    date_text = str(date_str)
    try:
        if "." in date_text and len(date_text.split(".")[0]) == 2:
            return date_text
        dt = datetime.datetime.strptime(date_text.split()[0], "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return date_text


def _format_datetime_ru(date_time_str: str) -> str:
    """Format an ISO date-time for Telegram while retaining its time."""
    if not date_time_str:
        return ""
    date_time_text = str(date_time_str).strip()
    parts = date_time_text.replace("T", " ").split(maxsplit=1)
    formatted_date = format_date_ru(parts[0])
    return (
        f"{formatted_date} {parts[1]}"
        if len(parts) > 1
        else formatted_date
    )


def _parse_datetime_text(raw: object) -> datetime.datetime | None:
    text = str(raw or "").strip().replace("T", " ")
    if not text:
        return None
    for date_format in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y",
    ):
        try:
            return datetime.datetime.strptime(text, date_format).replace(
                tzinfo=MINSK_TZ
            )
        except ValueError:
            continue
    return None


def _booking_issued_datetime(
    booking: dict,
    default: datetime.datetime | None = None,
) -> datetime.datetime | None:
    full_value = _value(
        booking,
        "Дата и время выдачи",
        default="",
    )
    if full_value:
        parsed = _parse_datetime_text(full_value)
        if parsed:
            return parsed
    issue_date = _value(
        booking,
        "Фактическая дата выдачи",
        "Дата выдачи",
        default="",
    )
    issue_time = _value(booking, "Время выдачи", default="")
    if issue_date and issue_time:
        parsed = _parse_datetime_text(f"{issue_date} {issue_time}")
        if parsed:
            return parsed
    if issue_date:
        return _parse_datetime_text(issue_date) or default
    return default


def _booking_planned_end_datetime(
    booking: dict,
    default: datetime.datetime | None = None,
) -> datetime.datetime | None:
    raw = _value(
        booking,
        "Плановое окончание",
        "Плановая дата и время окончания",
        default="",
    )
    return _parse_datetime_text(raw) or default


def _booking_planned_start_datetime(
    booking: dict,
    default: datetime.datetime | None = None,
) -> datetime.datetime | None:
    full_value = _value(
        booking,
        "Плановое начало",
        "Плановая дата и время начала",
        "Дата и время подачи",
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
        "Время подачи",
        default="",
    )
    if full_value:
        parsed = _parse_datetime_text(full_value)
        if parsed and not (
            parsed.hour == 0
            and parsed.minute == 0
            and len(str(full_value).strip().split()) == 1
        ):
            return parsed

    start_date = _value(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
        default="",
    )
    start_time = _value(
        booking,
        "Плановое время подачи",
        "Время подачи",
        "Время начала",
        "Время выдачи",
        default="",
    )
    if start_date and start_time:
        parsed = _parse_datetime_text(f"{start_date} {start_time}")
        if parsed:
            return parsed
    return default


def _booked_rental_days(
    start_datetime: datetime.datetime | None,
    planned_end: datetime.datetime,
    start_date: datetime.date,
) -> int:
    """Calculate paid days from the booked period, not the actual late return."""
    start_at = start_datetime or datetime.datetime.combine(
        start_date,
        datetime.time.min,
        MINSK_TZ,
    )
    booked_seconds = max(0, int((planned_end - start_at).total_seconds()))
    billable_seconds = max(
        0,
        booked_seconds - RENTAL_GRACE_PERIOD_MINUTES * 60,
    )
    return max(1, math.ceil(billable_seconds / (24 * 60 * 60)))


def _booking_period_text(booking: dict) -> str:
    start = _booking_issued_datetime(booking)
    end = _booking_planned_end_datetime(booking)
    if start and end:
        return f"{_format_datetime_ru(_timestamp(start))} — {_format_datetime_ru(_timestamp(end))}"
    start_date = _value(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
        default="",
    )
    end_date = _value(
        booking,
        "Дата окончания (ГГГГ-ММ-ДД)",
        "Дата окончания",
        default="",
    )
    return f"{format_date_ru(start_date)} — {format_date_ru(end_date)}"


def _issue_datetime_text(issued_at: str, planned_end_at: str = "") -> str:
    period = _format_datetime_ru(issued_at)
    if planned_end_at:
        period += f" — {_format_datetime_ru(planned_end_at)}"
    return period


def _html_value(value: object, default: str = "—") -> str:
    text = str(value).strip() if value is not None else ""
    return html.escape(text or default)


def _booking_id(record: dict) -> str:
    return _value(record, "ID Брони", "ID", "Booking ID")


def _booking_status_text(booking: dict) -> str:
    status = _value(booking, "Статус", default="—").strip()
    normalized = status.casefold()
    if normalized in {"подтверждена", "подтверждено"}:
        return "🔘 Статус: Подтверждена"
    if normalized in {"запланирована", "запланировано", "забронирована"}:
        return "⚪️ Бронь"
    if normalized == "в аренде":
        return "🟢 Статус: В аренде"
    if normalized == "завершена":
        return "🏁 Статус: Завершена"
    return f"Статус: {status or '—'}"


def _manager_display_name(user) -> str:
    return (
        getattr(user, "full_name", "")
        or getattr(user, "username", "")
        or str(getattr(user, "id", "—"))
    )


def _booking_manager_name(booking: dict) -> str:
    manager_name = _value(
        booking,
        "Имя менеджера",
        "Менеджер",
        "ФИО менеджера",
        "Менеджер, создавший бронь",
        default="",
    )
    if manager_name:
        return manager_name
    manager_id = _value(
        booking,
        "Telegram ID Менеджера",
        "Telegram ID менеджера",
        default="",
    )
    return f"Telegram ID {manager_id}" if manager_id else "—"


def _booking_prepayment_text(booking: dict) -> str:
    raw_amount = _value(
        booking,
        "Задаток/Предоплата",
        "Залог/Предоплата",
        "Предоплата",
        default="0",
    )
    amount = safe_int(raw_amount)
    if amount <= 0:
        return "Без задатка"
    payment_type = _value(
        booking,
        "Способ оплаты задатка",
        "Тип оплаты залога",
        default="",
    )
    payment_suffix = (
        f" ({payment_type})"
        if payment_type and payment_type != "Без оплаты"
        else ""
    )
    return f"{_format_number(amount)} RUB{payment_suffix}"


def _booking_card_text(booking: dict) -> str:
    client_name = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="—",
    )
    phone = _value(
        booking,
        "Телефон",
        "Телефон клиента",
        default="—",
    )
    start_date = _value(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
        default="—",
    )
    end_date = _value(
        booking,
        "Дата окончания (ГГГГ-ММ-ДД)",
        "Дата окончания",
        default="—",
    )
    return (
        f"Номер: Бронь #{_booking_id(booking)}\n"
        f"Клиент: {client_name} ({phone})\n"
        f"Менеджер: {_booking_manager_name(booking)}\n"
        f"Задаток: {_booking_prepayment_text(booking)}\n"
        f"Период: {format_date_ru(start_date)} — "
        f"{format_date_ru(end_date)}\n"
        + _booking_status_text(booking)
    )


def _partner_booking_card_text(
    booking: dict,
    car: dict | None = None,
    *,
    source: str = HEAVY_LUX_PARTNER_SOURCE,
) -> str:
    """Render only operational booking data for a restricted partner."""
    car = car or {}
    car_name = (
        str(booking.get("__car_name__") or "").strip()
        or _car_name(car)
        or _value(
            booking,
            "Автомобиль",
            "Авто",
            "Название авто",
            "Марка и Модель",
            "Модель",
            default="—",
        )
    )
    plate = (
        str(booking.get("__car_plate__") or "").strip()
        or _car_plate(car)
        or _value(
            booking,
            "Госномер",
            "Номер авто",
            "Номер",
            "Plate",
            default="",
        )
    )
    client_name = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="—",
    )
    phone = _value(booking, "Телефон", "Телефон клиента", default="—")
    start_date = _value(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
        default="—",
    )
    end_date = _value(
        booking,
        "Дата окончания (ГГГГ-ММ-ДД)",
        "Дата окончания",
        default="—",
    )
    start_mileage = _value(
        booking,
        "Пробег при выдаче (км)",
        "Начальный пробег",
        default="",
    )
    end_mileage = _value(
        booking,
        "Пробег при возврате (км)",
        "Конечный пробег",
        default="",
    )
    fuel = _value(
        booking,
        "Уровень топлива",
        "Топливо",
        "Топливо при выдаче",
        "Топливо при возврате",
        default="",
    )
    mileage_parts = []
    if start_mileage:
        mileage_parts.append(f"выдача: {start_mileage} км")
    if end_mileage:
        mileage_parts.append(f"возврат: {end_mileage} км")
    technical_lines = []
    if mileage_parts:
        technical_lines.append("Пробег: " + " / ".join(mileage_parts))
    if fuel:
        technical_lines.append(f"Топливо: {fuel}")
    vehicle_line = (
        f"Автомобиль: {car_name} ({plate})"
        if plate and plate not in {"—", "Без номера"}
        else f"Автомобиль: {car_name}"
    )
    return "\n".join(
        [
            f"Источник: {source}",
            f"Бронь №{_booking_id(booking)}",
            vehicle_line,
            f"Клиент: {client_name} ({phone})",
            f"Период: {format_date_ru(start_date)} — "
            f"{format_date_ru(end_date)}",
            *technical_lines,
            _booking_status_text(booking),
        ]
    )


def _is_heavy_lux_car(car: dict) -> bool:
    source = _value(
        car,
        "Источник",
        "Партнёр",
        "Партнер",
        "Канал",
        default="",
    ).casefold()
    if source:
        return HEAVY_LUX_PARTNER_SOURCE.casefold() in source
    return PARTNER_CAR_KEYWORD.casefold() in _car_name(car).casefold()


def _is_heavy_lux_booking(booking: dict) -> bool:
    source = _value(
        booking,
        "Источник",
        "Партнёр",
        "Партнер",
        "Канал",
        default="",
    ).casefold()
    if source:
        return HEAVY_LUX_PARTNER_SOURCE.casefold() in source
    car_name = _value(
        booking,
        "Автомобиль",
        "Авто",
        "Название авто",
        "Марка и Модель",
        "Модель",
        default="",
    )
    return PARTNER_CAR_KEYWORD.casefold() in car_name.casefold()


def _partner_issuance_card_text(data: dict) -> str:
    car = data.get("car", {})
    booking = data.get("booking", {})
    booking_id = _booking_id(booking) if booking else "без брони"
    fuel = _value(
        car,
        "Уровень топлива",
        "Топливо",
        "Уровень топлива (%)",
        "Топливо (%)",
        "Топливо при выдаче",
        default="—",
    )
    return (
        "Проверьте акт выдачи авто:\n\n"
        f"Источник: {HEAVY_LUX_PARTNER_SOURCE}\n"
        f"Бронь №{booking_id}\n"
        f"Автомобиль: {_car_name(car) or data.get('car_name', '—')} "
        f"({_car_plate(car) or '—'})\n"
        f"Период: {_issue_datetime_text(data.get('issued_at', ''), data.get('planned_end_at', ''))}\n"
        f"Клиент: {data.get('client_name', '—')}\n"
        f"Телефон: {data.get('phone', '—')}\n"
        f"Пробег при выдаче: {data.get('start_mileage', '—')} км\n"
        f"Топливо: {fuel}\n\n"
        "Сохранить выдачу авто?"
    )


def _partner_return_card_text(data: dict) -> str:
    booking = data.get("booking", {})
    car = data.get("car", {})
    phone = _value(booking, "Телефон", "Телефон клиента", default="—")
    issued_datetime = _booking_issued_datetime(booking)
    issued_at = (
        _timestamp(issued_datetime)
        if issued_datetime
        else _value(
            booking,
            "Дата и время выдачи",
            "Фактическая дата выдачи",
            "Дата выдачи",
            "Дата начала",
            default="—",
        )
    )
    return (
        "🏁 АКТ ПРИЁМА АВТОМОБИЛЯ\n"
        f"Источник: {HEAVY_LUX_PARTNER_SOURCE}\n"
        f"Бронь №{_booking_id(booking)}\n"
        f"Автомобиль: {_car_name(car)} ({_car_plate(car)})\n"
        f"Клиент: {_value(booking, 'ФИО Клиента', 'Клиент', default='—')} / {phone}\n"
        f"Период: {_format_datetime_ru(issued_at)} — "
        f"{_format_datetime_ru(data.get('returned_at', _timestamp()))}\n"
        f"Пробег: выдача {data.get('start_mileage', '—')} км / "
        f"возврат {data.get('end_mileage', '—')} км\n"
        f"Топливо: {data.get('fuel_level', '—')}\n"
        f"Состояние: {data.get('cleanliness', '—')}\n"
        f"Статус: готов к сохранению\n"
        f"Примечание: {data.get('notes') or '—'}\n\n"
        "Сохранить приём автомобиля?"
    )


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


def _car_base_deposit(car: dict) -> int | None:
    raw = _value(
        car,
        "Базовый залог (RUB)",
        "Базовый залог",
        "Залог (RUB)",
        "Залог",
        "Депозит (RUB)",
        "Депозит",
        default="",
    )
    if not raw:
        return None
    parsed, _ = _parse_int(raw, "базовый залог", minimum=0)
    return parsed


def _car_class(car: dict) -> str:
    return _value(
        car,
        "Класс",
        "Класс авто",
        "Класс автомобиля",
        "Категория",
        "Тип кузова",
        default="Не указан",
    )


def _car_mileage_limit(car: dict) -> str:
    return _value(
        car,
        "Включенный суточный пробег (км)",
        "Включённый суточный пробег (км)",
        "Лимит пробега (км/сутки)",
        "Лимит пробега",
        "Суточный пробег",
        "Пробег/сутки",
        default="Уточняется",
    )


def _car_photo_url(car: dict) -> str:
    return _value(
        car,
        "Фото",
        "Фото URL",
        "Ссылка на фото",
        "URL фото",
        "Изображение",
        "Image",
        "Photo",
        default="",
    ).strip()


def _car_label(car: dict) -> str:
    return f"{_car_name(car)} ({_car_plate(car)}) — {_car_daily_rate(car)} RUB/сутки"


def _partner_auto_booking_price(data: dict) -> int | None:
    """Calculate a partner booking amount from the car's daily tariff."""
    try:
        daily_rate = safe_int(_car_daily_rate(data.get("car", {})))
        start_date = datetime.date.fromisoformat(str(data["start_date"]))
        end_date = datetime.date.fromisoformat(str(data["end_date"]))
    except (KeyError, TypeError, ValueError):
        return None
    days = (end_date - start_date).days + 1
    if daily_rate <= 0 or days <= 0:
        return None
    return daily_rate * days


async def _stop_partner_flow_without_tariff(
    message: Message,
    state: FSMContext,
) -> None:
    """Stop safely instead of asking a partner to enter a financial amount."""
    await state.clear()
    await message.answer(
        "Не удалось определить тариф автомобиля. "
        "Обратитесь к менеджеру для настройки тарифа.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


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
    dates = _booking_period_text(booking)
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


def _format_number(value: object) -> str:
    return f"{safe_int(value):,}".replace(",", " ")


def build_issuance_notification(
    data: dict,
    booking_id: object,
    car: dict,
    manager_name: str,
    *,
    is_direct_issuance: bool = False,
) -> str:
    """Build the manager notification for a completed vehicle issuance."""
    fuel_level = _value(
        car,
        "Уровень топлива",
        "Топливо",
        "Уровень топлива (%)",
        "Топливо (%)",
        "Топливо при выдаче",
        default="—",
    )
    issuance_backdated_text = (
        "⚠️ <b>Оформлено задним числом</b>\n"
        if data.get("backdated")
        else ""
    )
    rent_amount = data.get("price", 0)
    payment_method = data.get("pay_type", "Без оплаты")
    deposit_amount = data.get("deposit", 0)
    deposit_payment_method = data.get("deposit_pay_type", "Без оплаты")
    return (
        "🚗 <b>Автомобиль выдан клиенту!</b>\n"
        f"🧾 Основание: "
        f"<b>{'срочная выдача без брони' if is_direct_issuance else f'бронь №{_html_value(booking_id)}'}</b>\n"
        f"🚘 Авто: <b>{_html_value(_car_name(car))} "
        f"({_html_value(_car_plate(car))})</b>\n"
        f"👤 Клиент: <b>{_html_value(data.get('client_name'))}</b>\n"
        f"💰 Аренда: <b>{_format_number(rent_amount)} ₽</b> "
        f"({_html_value(payment_method, 'Без оплаты')})\n"
        f"🛡 Залог: <b>{_format_number(deposit_amount)} ₽</b> "
        f"({_html_value(deposit_payment_method, 'Без оплаты')})\n"
        f"📍 Пробег на выезде: <b>"
        f"{_html_value(data.get('start_mileage'))} км</b>\n"
        f"⛽ Уровень топлива: <b>{_html_value(fuel_level)}</b>\n"
        f"⏱ Период: <b>{_html_value(_issue_datetime_text(data.get('issued_at', ''), data.get('planned_end_at', '')))}</b>\n"
        f"{issuance_backdated_text}"
        f"👨‍💼 Выдал менеджер: <b>{_html_value(manager_name)}</b>"
    )


def _now_minsk() -> datetime.datetime:
    return datetime.datetime.now(MINSK_TZ).replace(second=0, microsecond=0)


def _now_moscow() -> datetime.datetime:
    return datetime.datetime.now(MOSCOW_TZ).replace(second=0, microsecond=0)


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
    scheduled_time = _booking_period_text(booking)
    if scheduled_time == " — ":
        scheduled_time = _notification_time(booking, *time_keys)
    return (
        f"• {_notification_car_label(booking, car_lookup)} — "
        f"Клиент: {client}/{phone} — Период: {scheduled_time}"
    )


def build_tomorrow_summary(
    target_date: datetime.date,
    events: dict[str, list[dict]],
    cars: list[dict] | None = None,
) -> str:
    date_label = format_date_ru(target_date.isoformat())
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


async def send_broadcast_notification(bot: Bot, text: str):
    for manager_id in NOTIFY_MANAGERS:
        try:
            await bot.send_message(
                chat_id=manager_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            print(
                f"Не удалось отправить уведомление менеджеру "
                f"{manager_id}: {e}"
            )


async def send_operation_notification(
    bot: Bot,
    text: str,
    car_name: str = "",
) -> None:
    """Notify managers and, for Dodge operations, the partner separately."""
    await send_broadcast_notification(bot, text)
    if PARTNER_CAR_KEYWORD.casefold() not in str(car_name or "").casefold():
        return
    try:
        await bot.send_message(
            chat_id=PARTNER_ID,
            text=text,
            parse_mode="HTML",
        )
    except Exception as error:
        logger.exception(
            "Could not send partner notification to %s: %s",
            PARTNER_ID,
            error,
        )


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


def build_expiring_rental_notification(booking: dict) -> str:
    """Build a non-financial one-hour rental expiry alert."""
    booking_id = _booking_id(booking)
    car_name = _value(
        booking,
        "__car_name__",
        "Автомобиль",
        "Авто",
        "Название авто",
        default="Авто",
    )
    car_plate = _value(
        booking,
        "__car_plate__",
        "Госномер",
        "Номер авто",
        "Номер",
        default="—",
    )
    client_name = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="не указан",
    )
    client_phone = _value(
        booking,
        "Телефон",
        "Телефон клиента",
        default="не указан",
    )
    planned_end_raw = _value(
        booking,
        "Плановое окончание",
        "Плановая дата и время окончания",
        default="не указано",
    )
    planned_end = _parse_datetime_text(planned_end_raw)
    end_time = (
        _format_datetime_ru(_timestamp(planned_end))
        if planned_end
        else planned_end_raw
    )
    return (
        "⚠️ Внимание! До конца аренды остался 1 час!\n"
        f"📋 Бронь: №{booking_id}\n"
        f"🚘 Авто: {car_name} ({car_plate})\n"
        f"👤 Клиент: {client_name} ({client_phone})\n"
        f"⏰ Окончание: {end_time}\n"
        "📍 Подготовьтесь к приёму автомобиля."
    )


def _expiring_rental_recipient_ids(booking: dict) -> list[int]:
    recipients = list(dict.fromkeys(NOTIFY_MANAGERS))
    source = _value(booking, "Источник", default="").casefold()
    if source == HEAVY_LUX_PARTNER_SOURCE.casefold():
        if PARTNER_ID not in recipients:
            recipients.append(PARTNER_ID)
    return recipients


async def check_expiring_rentals(
    bot: Bot,
    now: datetime.datetime | None = None,
) -> None:
    """Notify internal recipients about rentals ending in roughly one hour."""
    current = now or datetime.datetime.now(NOTIFICATION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NOTIFICATION_TZ)
    else:
        current = current.astimezone(NOTIFICATION_TZ)

    async with NOTIFICATION_SEND_LOCK:
        try:
            rentals = await asyncio.to_thread(get_active_rentals)
        except Exception:
            logger.exception("Could not collect rentals expiring in one hour")
            return

        active_booking_ids = {
            _booking_id(booking)
            for booking in rentals
            if _booking_id(booking) not in {"", "—"}
        }
        EXPIRING_RENTAL_ALERTED_BOOKING_IDS.intersection_update(
            active_booking_ids
        )
        try:
            persisted_alerted_booking_ids = await asyncio.to_thread(
                get_expiring_rental_alerted_booking_ids,
                current,
            )
        except Exception:
            logger.exception(
                "Could not load persisted one-hour expiry alert state"
            )
            return
        EXPIRING_RENTAL_ALERTED_BOOKING_IDS.update(
            persisted_alerted_booking_ids.intersection(active_booking_ids)
        )

        for booking in rentals:
            if str(booking.get("Статус", "")).strip() != "В аренде":
                continue
            booking_id = _booking_id(booking)
            if (
                booking_id in {"", "—"}
                or booking_id in EXPIRING_RENTAL_ALERTED_BOOKING_IDS
            ):
                continue
            planned_end = _booking_planned_end_datetime(booking)
            if planned_end is None:
                logger.warning(
                    "Skipping expiring-rental alert for booking %s: "
                    "planned end is missing or invalid",
                    booking_id,
                )
                continue
            minutes_left = (
                planned_end.astimezone(NOTIFICATION_TZ) - current
            ).total_seconds() / 60
            if not 50 <= minutes_left <= 70:
                continue

            notification = build_expiring_rental_notification(booking)
            delivery_succeeded = True
            for recipient_id in _expiring_rental_recipient_ids(booking):
                try:
                    await _send_notification_chunks(
                        bot,
                        recipient_id,
                        [notification],
                    )
                except Exception:
                    delivery_succeeded = False
                    logger.exception(
                        "Could not send one-hour expiry alert for booking %s "
                        "to chat %s",
                        booking_id,
                        recipient_id,
                    )
            if delivery_succeeded:
                EXPIRING_RENTAL_ALERTED_BOOKING_IDS.add(booking_id)
                try:
                    await asyncio.to_thread(
                        mark_expiring_rental_alert_sent,
                        booking_id,
                        current,
                    )
                except Exception:
                    logger.exception(
                        "Could not persist one-hour expiry alert for booking %s",
                        booking_id,
                    )
                logger.info(
                    "Sent one-hour expiry alert for booking %s",
                    booking_id,
                )


def build_upcoming_booking_notification(booking: dict) -> str:
    booking_id = _booking_id(booking)
    car_name = _value(
        booking,
        "__car_name__",
        "Автомобиль",
        "Авто",
        "Название авто",
        default="Авто",
    )
    car_plate = _value(
        booking,
        "__car_plate__",
        "Госномер",
        "Номер авто",
        "Номер",
        default="—",
    )
    client_name = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="не указан",
    )
    client_phone = _value(
        booking,
        "Телефон",
        "Телефон клиента",
        default="не указан",
    )
    start_at = _booking_planned_start_datetime(booking)
    start_text = (
        _format_datetime_ru(_timestamp(start_at))
        if start_at
        else _value(
            booking,
            "Плановое начало",
            "Дата и время подачи",
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
            default="не указано",
        )
    )
    return (
        "⏳ Напоминание! До начала брони остался 1 час!\n"
        f"📋 Бронь: №{booking_id}\n"
        f"🚘 Авто: {car_name} ({car_plate})\n"
        f"👤 Клиент: {client_name} ({client_phone})\n"
        f"🕒 Время подачи / начала: {start_text}\n\n"
        "Подготовьте автомобиль к выдаче (мойка, заправка, документы)."
    )


async def _upcoming_booking_recipient_ids(booking: dict) -> list[int]:
    manager_id = _booking_manager_id(booking)
    source = _value(booking, "Источник", default="").casefold()
    if manager_id is None and source == HEAVY_LUX_PARTNER_SOURCE.casefold():
        return [PARTNER_ID]
    if manager_id is None:
        return []

    try:
        access = await asyncio.to_thread(
            _manager_access_for_user,
            manager_id,
        )
    except Exception:
        logger.exception(
            "Could not resolve booking creator %s for upcoming booking %s",
            manager_id,
            _booking_id(booking),
        )
        access = None
    role = _normalized_role(access)
    if (
        source == HEAVY_LUX_PARTNER_SOURCE.casefold()
        or role == HEAVY_LUX_PARTNER_ROLE
    ):
        return [PARTNER_ID]
    return [manager_id]


async def _send_upcoming_booking_alert(
    bot: Bot,
    recipient_id: int,
    booking_id: str,
    text: str,
) -> None:
    keyboard = upcoming_handover_keyboard(booking_id)
    last_error = None
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=recipient_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
    if last_error is not None:
        raise last_error


async def check_upcoming_bookings(
    bot: Bot,
    now: datetime.datetime | None = None,
) -> None:
    """Notify once about confirmed bookings starting in roughly one hour."""
    current = now or datetime.datetime.now(NOTIFICATION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NOTIFICATION_TZ)
    else:
        current = current.astimezone(NOTIFICATION_TZ)

    async with NOTIFICATION_SEND_LOCK:
        try:
            bookings = await asyncio.to_thread(get_active_rentals)
        except Exception:
            logger.exception("Could not collect upcoming bookings")
            return

        confirmed_ids = {
            _booking_id(booking)
            for booking in bookings
            if str(booking.get("Статус", "")).strip() == "Подтверждена"
            and _booking_id(booking) not in {"", "—"}
        }
        UPCOMING_BOOKING_NOTIFIED_IDS.intersection_update(confirmed_ids)

        for booking in bookings:
            if str(booking.get("Статус", "")).strip() != "Подтверждена":
                continue
            booking_id = _booking_id(booking)
            if (
                booking_id in {"", "—"}
                or booking_id in UPCOMING_BOOKING_NOTIFIED_IDS
            ):
                continue
            start_at = _booking_planned_start_datetime(booking)
            if start_at is None:
                logger.warning(
                    "Skipping upcoming-booking alert for booking %s: "
                    "start date/time is missing or invalid",
                    booking_id,
                )
                continue
            minutes_left = (
                start_at.astimezone(NOTIFICATION_TZ) - current
            ).total_seconds() / 60
            if not 50 <= minutes_left <= 70:
                continue

            recipient_ids = await _upcoming_booking_recipient_ids(booking)
            if not recipient_ids:
                logger.warning(
                    "Skipping upcoming-booking alert for booking %s: "
                    "manager Telegram ID is missing",
                    booking_id,
                )
                continue
            notification = build_upcoming_booking_notification(booking)
            delivery_succeeded = True
            for recipient_id in recipient_ids:
                try:
                    await _send_upcoming_booking_alert(
                        bot,
                        recipient_id,
                        booking_id,
                        notification,
                    )
                except Exception:
                    delivery_succeeded = False
                    logger.exception(
                        "Could not send upcoming-booking alert for booking %s "
                        "to chat %s",
                        booking_id,
                        recipient_id,
                    )
            if delivery_succeeded:
                UPCOMING_BOOKING_NOTIFIED_IDS.add(booking_id)
                logger.info(
                    "Sent upcoming-booking alert for booking %s",
                    booking_id,
                )


def _booking_manager_id(booking: dict) -> int | None:
    raw_id = _value(
        booking,
        "Telegram ID Менеджера",
        "ID Менеджера",
        "Telegram ID Менеджера выдачи",
        default="",
    )
    normalized_id = str(raw_id).strip().replace(" ", "").removesuffix(".0")
    try:
        manager_id = int(normalized_id)
    except (TypeError, ValueError):
        return None
    return manager_id if manager_id > 0 else None


def _admin_notification_ids() -> list[int]:
    return list(dict.fromkeys([*NOTIFY_MANAGERS, *ADMIN_IDS]))


def build_overdue_rental_notification(booking: dict) -> str:
    """Build the overdue alert without any financial information."""
    booking_id = _booking_id(booking)
    car_name = _value(
        booking,
        "__car_name__",
        "Автомобиль",
        "Авто",
        "Название авто",
        default="Авто",
    )
    car_plate = _value(
        booking,
        "__car_plate__",
        "Госномер",
        "Номер авто",
        "Номер",
        default="—",
    )
    client_name = _value(
        booking,
        "ФИО Клиента",
        "Клиент",
        "Клиент ФИО",
        "Имя клиента",
        default="не указан",
    )
    client_phone = _value(
        booking,
        "Телефон",
        "Телефон клиента",
        default="не указан",
    )
    planned_end_raw = _value(
        booking,
        "Плановое окончание",
        "Плановая дата и время окончания",
        default="не указано",
    )
    planned_end = _parse_datetime_text(planned_end_raw)
    planned_end_text = (
        _format_datetime_ru(_timestamp(planned_end))
        if planned_end
        else planned_end_raw
    )
    return (
        "⚠️ Внимание! Аренда не закрыта вовремя!\n"
        f"📋 Бронь: №{booking_id}\n"
        f"🚘 Авто: {car_name} ({car_plate})\n"
        f"👤 Клиент: {client_name} ({client_phone})\n"
        f"⏰ Плановое окончание: {planned_end_text} "
        "(задержка более 1 часа)\n\n"
        "Машина возвращена или аренда продолжается?"
    )


async def _send_overdue_alert(
    bot: Bot,
    chat_id: int,
    booking_id: str,
    text: str,
) -> None:
    keyboard = overdue_rental_keyboard(booking_id)
    last_error = None
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
    if last_error is not None:
        raise last_error


async def _send_overdue_recipients(
    bot: Bot,
    booking: dict,
    text: str,
) -> bool:
    booking_id = _booking_id(booking)
    manager_id = _booking_manager_id(booking)
    manager_access = None
    manager_is_partner = False
    if manager_id is not None:
        try:
            manager_access = await asyncio.to_thread(
                _manager_access_for_user,
                manager_id,
            )
        except Exception:
            logger.exception(
                "Could not resolve manager %s for overdue booking %s",
                manager_id,
                booking_id,
            )
        manager_is_partner = (
            _normalized_role(manager_access)
            in {
                "партнёр",
                "партнер",
                "partner",
                HEAVY_LUX_PARTNER_ROLE,
            }
        )

    manager_available = bool(manager_access and manager_access.get("active"))
    manager_delivered = False
    if manager_available and manager_id is not None:
        try:
            await _send_overdue_alert(bot, manager_id, booking_id, text)
            manager_delivered = True
        except Exception:
            logger.exception(
                "Could not send overdue alert for booking %s to manager %s",
                booking_id,
                manager_id,
            )

    admins_required = (
        not manager_available
        or not manager_delivered
        or manager_is_partner
    )
    admin_delivered = False
    if admins_required:
        for admin_id in _admin_notification_ids():
            if admin_id == manager_id and manager_delivered:
                continue
            try:
                await _send_overdue_alert(bot, admin_id, booking_id, text)
                admin_delivered = True
            except Exception:
                logger.exception(
                    "Could not send overdue alert for booking %s to admin %s",
                    booking_id,
                    admin_id,
                )

    return (
        (manager_delivered and (not admins_required or admin_delivered))
        if manager_available
        else admin_delivered
    )


async def check_overdue_rentals(
    bot: Bot,
    now: datetime.datetime | None = None,
) -> None:
    """Notify once when an active rental passes one hour overdue."""
    current = now or datetime.datetime.now(NOTIFICATION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NOTIFICATION_TZ)
    else:
        current = current.astimezone(NOTIFICATION_TZ)

    async with NOTIFICATION_SEND_LOCK:
        try:
            rentals = await asyncio.to_thread(get_active_rentals)
        except Exception:
            logger.exception("Could not collect overdue rentals")
            return

        active_booking_ids = {
            _booking_id(booking)
            for booking in rentals
            if _booking_id(booking) not in {"", "—"}
        }
        OVERDUE_1H_ALERTED_BOOKING_IDS.intersection_update(active_booking_ids)

        for booking in rentals:
            if str(booking.get("Статус", "")).strip() != "В аренде":
                continue
            booking_id = _booking_id(booking)
            if (
                booking_id in {"", "—"}
                or booking_id in OVERDUE_1H_ALERTED_BOOKING_IDS
            ):
                continue
            planned_end = _booking_planned_end_datetime(booking)
            if planned_end is None:
                logger.warning(
                    "Skipping overdue alert for booking %s: planned end "
                    "is missing or invalid",
                    booking_id,
                )
                continue
            overdue_minutes = (
                current - planned_end.astimezone(NOTIFICATION_TZ)
            ).total_seconds() / 60
            if not 60 <= overdue_minutes < 75:
                continue

            notification = build_overdue_rental_notification(booking)
            if await _send_overdue_recipients(bot, booking, notification):
                OVERDUE_1H_ALERTED_BOOKING_IDS.add(booking_id)
                logger.info(
                    "Sent one-hour overdue alert for booking %s",
                    booking_id,
                )


def _find_returnable_booking(bookings: list[dict], booking_id: str) -> dict | None:
    normalized_id = str(booking_id).strip().removesuffix(".0")
    for booking in bookings:
        current_id = _booking_id(booking).strip().removesuffix(".0")
        if current_id == normalized_id:
            return booking
    return None


async def _callback_operational_access(
    callback: CallbackQuery,
) -> bool:
    user = callback.from_user
    if user is None:
        return False
    if is_heavy_lux_partner_user(user.id):
        return True
    access = _manager_access_for_user(user.id)
    return bool(
        access
        and access.get("active")
        and _normalized_role(access)
        not in {
            "партнёр",
            "партнер",
            "partner",
            HEAVY_LUX_PARTNER_ROLE,
        }
    )


async def overdue_return_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _callback_operational_access(callback):
        await callback.answer(
            "Недостаточно прав для оформления возврата.",
            show_alert=True,
        )
        return
    booking_id = callback.data.removeprefix("return_car:")
    try:
        booking = await asyncio.to_thread(
            lambda: _find_returnable_booking(
                get_returnable_rentals(),
                booking_id,
            )
        )
        if not booking:
            raise ValueError("Активная бронь не найдена.")
        car = await asyncio.to_thread(get_car_by_id, _car_id(booking))
        if not car:
            car = await asyncio.to_thread(
                get_car_by_plate,
                _value(booking, "Госномер", "Номер авто", "Номер", default=""),
            )
        if not car:
            raise ValueError("Автомобиль из брони не найден.")
    except Exception as error:
        logger.exception("Could not open overdue return for booking %s", booking_id)
        await callback.answer(str(error), show_alert=True)
        return

    await callback.answer("Открываем сценарий приёма авто.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await state.update_data(
        return_cars=[car],
        partner_mode=is_heavy_lux_partner_user(callback.from_user.id),
    )
    await _prepare_return(callback.message, state, car, booking)


async def overdue_extend_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _callback_operational_access(callback):
        await callback.answer(
            "Недостаточно прав для продления аренды.",
            show_alert=True,
        )
        return
    booking_id = callback.data.removeprefix("extend_rent:")
    try:
        booking = await asyncio.to_thread(
            lambda: _find_returnable_booking(
                get_returnable_rentals(),
                booking_id,
            )
        )
    except Exception:
        logger.exception("Could not load booking %s for extension", booking_id)
        booking = None
    if not booking:
        await callback.answer("Активная бронь не найдена.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await state.update_data(overdue_booking_id=_booking_id(booking))
    await state.set_state(OverdueExtensionForm.end_at)
    await callback.message.answer(
        "Введите новое плановое окончание аренды в формате "
        "ГГГГ-ММ-ДД ЧЧ:ММ:"
    )


async def overdue_extension_input(
    message: Message,
    state: FSMContext,
) -> None:
    parsed = _parse_datetime_text(message.text or "")
    if parsed is None:
        await message.answer(
            "Неверный формат. Пример: 2026-09-06 18:30."
        )
        return
    now = datetime.datetime.now(MINSK_TZ)
    if parsed <= now:
        await message.answer("Новое окончание должно быть в будущем.")
        return
    data = await state.get_data()
    booking_id = data.get("overdue_booking_id")
    if not booking_id:
        await state.clear()
        await message.answer(
            "Сценарий продления устарел. Откройте уведомление заново.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    planned_end_at = _timestamp(parsed)
    try:
        await asyncio.to_thread(
            update_booking_planned_end,
            booking_id,
            planned_end_at,
        )
    except Exception:
        logger.exception(
            "Could not extend rental for booking %s",
            booking_id,
        )
        await state.clear()
        await message.answer(
            "Не удалось обновить плановое окончание в Google Sheets.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return

    OVERDUE_1H_ALERTED_BOOKING_IDS.discard(str(booking_id))
    await state.clear()
    await message.answer(
        f"✅ Аренда по брони №{booking_id} продлена.\n"
        f"Новое плановое окончание: {_format_datetime_ru(planned_end_at)}",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


async def handover_car_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _callback_operational_access(callback):
        await callback.answer(
            "Недостаточно прав для оформления выдачи.",
            show_alert=True,
        )
        return
    booking_id = callback.data.removeprefix("handover_car:")
    try:
        bookings = await asyncio.to_thread(get_confirmed_bookings)
        booking = _find_returnable_booking(bookings, booking_id)
    except Exception:
        logger.exception(
            "Could not load confirmed booking %s for handover",
            booking_id,
        )
        booking = None
    if not booking:
        await callback.answer(
            "Подтверждённая бронь не найдена. Возможно, выдача уже оформлена.",
            show_alert=True,
        )
        return

    await callback.answer("Открываем сценарий выдачи.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await state.update_data(
        booking_mode="issuance",
        issuance_bookings=[booking],
        partner_mode=is_heavy_lux_partner_user(callback.from_user.id),
    )
    await _prepare_issuance_booking(callback.message, state, booking)


def create_notification_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=NOTIFICATION_TZ)
    scheduler.add_job(
        check_expiring_rentals,
        IntervalTrigger(minutes=5, timezone=NOTIFICATION_TZ),
        args=[bot],
        id="expiring_rental_alerts",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        check_overdue_rentals,
        IntervalTrigger(minutes=5, timezone=NOTIFICATION_TZ),
        args=[bot],
        id="overdue_rental_alerts",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        check_upcoming_bookings,
        IntervalTrigger(minutes=5, timezone=NOTIFICATION_TZ),
        args=[bot],
        id="upcoming_booking_alerts",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
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
    legacy_start_date = _record_date(
        booking,
        "Дата начала (ГГГГ-ММ-ДД)",
        "Дата начала",
    ) or _now_minsk().date()
    start_datetime = _booking_issued_datetime(booking)
    start_date = start_datetime.date() if start_datetime else legacy_start_date
    end_date = _record_date(
        booking,
        "Дата окончания (ГГГГ-ММ-ДД)",
        "Дата окончания",
    ) or start_date
    planned_end_raw = _value(
        booking,
        "Плановое окончание",
        "Плановая дата и время окончания",
        default="",
    )
    planned_end = _parse_datetime_text(planned_end_raw) or datetime.datetime.combine(
        end_date,
        datetime.time(23, 59),
        MINSK_TZ,
    )
    if planned_end_raw and _parse_datetime_text(planned_end_raw):
        end_date = planned_end.date()
    returned_at = _record_datetime(
        {"Дата и время приёма": data.get("returned_at", "")},
        "Дата и время приёма",
        default=_now_minsk(),
    )
    late_seconds = max(0, int((returned_at - planned_end).total_seconds()))
    late_hours = math.ceil(late_seconds / 3600) if late_seconds else 0
    chargeable_late_seconds = max(
        0,
        late_seconds - RENTAL_GRACE_PERIOD_MINUTES * 60,
    )
    chargeable_late_hours = (
        math.ceil(chargeable_late_seconds / 3600)
        if chargeable_late_seconds
        else 0
    )

    daily_rate, _ = _parse_int(
        _car_daily_rate(car),
        "суточный тариф",
        minimum=0,
    )
    daily_rate = daily_rate or 0
    rental_days = _booked_rental_days(
        start_datetime,
        planned_end,
        start_date,
    )
    calculated_rental_total = (
        0 if data.get("direct_return") else daily_rate * rental_days
    )
    rental_total_override = data.get("rental_total_override")
    if rental_total_override is not None and not data.get("direct_return"):
        rental_total = max(0, int(rental_total_override))
    else:
        rental_total = calculated_rental_total
    prepayment, _ = _parse_int(
        _value(
            booking,
            "Задаток/Предоплата",
            "Залог/Предоплата",
            "Предоплата",
            default="0",
        ),
        "задаток",
        minimum=0,
    )
    prepayment = prepayment or 0
    rental_paid_at_issuance = max(
        0,
        int(data.get("rental_paid_at_issuance", 0)),
    )
    rental_balance = rental_total - prepayment - rental_paid_at_issuance
    rental_due = max(rental_balance, 0)
    prepayment_overpayment = max(-rental_balance, 0)
    mileage_limit = rental_days * 250
    delta_mileage = int(data["end_mileage"]) - int(data["start_mileage"])
    over_mileage = max(0, delta_mileage - mileage_limit)
    time_mileage_penalty = max(
        0,
        int(data.get("time_mileage_penalty", 0)),
    )
    late_fee = 0
    over_mileage_fee = 0
    wash_fee = int(data.get("wash_fee", 0))
    extra_fee = int(data.get("extra_fee", 0))
    deposit = int(data.get("deposit", 0))
    total_penalties = (
        time_mileage_penalty
        + wash_fee
        + extra_fee
    )
    deposit_balance = deposit - total_penalties
    if data.get("direct_return"):
        direct_deposit_status = data.get("direct_deposit_status")
        total_penalties = deposit if direct_deposit_status == "retained" else 0
        deposit_balance = 0 if direct_deposit_status == "retained" else deposit
        time_mileage_penalty = 0
        late_fee = 0
        over_mileage_fee = 0
        late_hours = 0
        chargeable_late_hours = 0
        chargeable_late_seconds = 0
        over_mileage = 0

    return {
        "daily_rate": daily_rate,
        "rental_days": rental_days,
        "rental_total": rental_total,
        "calculated_rental_total": calculated_rental_total,
        "rental_total_override": rental_total_override,
        "prepayment": prepayment,
        "rental_paid_at_issuance": rental_paid_at_issuance,
        "rental_balance": rental_balance,
        "rental_due": rental_due,
        "prepayment_overpayment": prepayment_overpayment,
        "mileage_limit": mileage_limit,
        "delta_mileage": delta_mileage,
        "over_mileage": over_mileage,
        "over_mileage_fee": over_mileage_fee,
        "late_hours": late_hours,
        "chargeable_late_hours": chargeable_late_hours,
        "chargeable_late_seconds": chargeable_late_seconds,
        "late_seconds": late_seconds,
        "late_fee": late_fee,
        "time_mileage_penalty": time_mileage_penalty,
        "wash_fee": wash_fee,
        "extra_fee": extra_fee,
        "total_penalties": total_penalties,
        "deposit": deposit,
        "deposit_balance": deposit_balance,
        "cleanliness": data.get("cleanliness", "—"),
    }


def _deposit_settlement_text(calculation: dict[str, int | str]) -> str:
    deposit = int(calculation["deposit"])
    total_penalties = int(calculation["total_penalties"])
    difference = int(calculation["deposit_balance"])
    if total_penalties == 0:
        return (
            "✅ Страховой залог возвращается клиенту: "
            f"{deposit:,} RUB"
        ).replace(",", " ")
    if difference > 0:
        return (
            f"⚠️ Удержано из страхового залога: {total_penalties:,} RUB. "
            f"Остаток залога к возврату: {difference:,} RUB"
        ).replace(",", " ")
    if difference == 0:
        return "⚠️ Страховой залог полностью удержан в счет штрафов (0 RUB к возврату)."
    return (
        "🚨 Залог удержан полностью (0 RUB к возврату). "
        "Доплата за повреждения/штрафы: "
        f"{abs(difference):,} RUB"
    ).replace(",", " ")


def _deposit_outcome_text(calculation: dict[str, int | str]) -> str:
    difference = int(calculation["deposit_balance"])
    if difference >= 0:
        return f"✅ Залог к возврату клиенту: {difference:,} RUB".replace(
            ",",
            " ",
        )
    return f"🚨 Требуется доплата с клиента: {abs(difference):,} RUB".replace(
        ",",
        " ",
    )


def _manager_access_for_user(user_id: int) -> dict | None:
    if str(user_id).strip() == str(PARTNER_ID):
        return {
            "user_id": str(PARTNER_ID),
            "role": "партнёр",
            "active": True,
        }
    return get_manager_access(user_id)


def _normalized_role(access: dict | None) -> str:
    return str(access.get("role", "") if access else "").strip().lower()


def get_user_role(user_id: int) -> str:
    """Return the normalized role from the Менеджеры access record."""
    return _normalized_role(_manager_access_for_user(user_id))


def _is_admin_plus_user(user_id: int) -> bool:
    try:
        return get_user_role(user_id) == ADMIN_PLUS_ROLE
    except Exception:
        logger.exception("Admin Plus authorization check failed")
        return False


async def ensure_admin_plus(message: Message) -> bool:
    user = message.from_user
    if user is None or not _is_admin_plus_user(user.id):
        await message.answer(
            "🚫 Ошибка доступа: отменять брони и расходы может только "
            "Администратор плюс."
        )
        return False
    return True


def _callback_has_admin_plus_access(callback: CallbackQuery) -> bool:
    return bool(
        callback.from_user
        and _is_admin_plus_user(callback.from_user.id)
    )


def is_partner_user(user_id: int) -> bool:
    try:
        access = _manager_access_for_user(user_id)
        return bool(
            access
            and access["active"]
            and (
                str(user_id).strip() == str(PARTNER_ID)
                or _normalized_role(access)
                in {
                    "партнёр",
                    "партнер",
                    "partner",
                    HEAVY_LUX_PARTNER_ROLE,
                }
            )
        )
    except Exception:
        logger.exception("Partner authorization check failed")
        return False


def is_heavy_lux_partner_user(user_id: int) -> bool:
    try:
        access = _manager_access_for_user(user_id)
        return bool(
            access
            and access["active"]
            and _normalized_role(access) == HEAVY_LUX_PARTNER_ROLE
        )
    except Exception:
        logger.exception("Heavy Lux partner authorization check failed")
        return False


async def ensure_operational_user(message: Message) -> bool:
    """Allow managers and the restricted Heavy Lux partner to run operations."""
    user = message.from_user
    if user is not None and is_heavy_lux_partner_user(user.id):
        return True
    return await ensure_manager(message)


def _partner_car_status(car: dict) -> str:
    status = _value(car, "Статус", default="—")
    if status.casefold() in {"активен", "свободен", "available"}:
        return "Свободен"
    if status.casefold() in {"в аренде", "rented"}:
        return "В аренде"
    return status


async def ensure_partner(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return False
    try:
        access = _manager_access_for_user(user.id)
    except Exception:
        logger.exception("Partner authorization check failed")
        await message.answer("Не удалось проверить доступ. Попробуйте ещё раз позже.")
        return False
    if access is None:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {str(user.id).strip()}. "
            "Сообщите его администратору."
        )
        return False
    if not access["active"]:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {str(user.id).strip()}. "
            "Сообщите его администратору."
        )
        return False
    if is_partner_user(user.id):
        return True
        await message.answer(
            "Эта функция доступна только партнёру по автомобилю.",
            reply_markup=get_main_keyboard(user.id, _normalized_role(access)),
        )
    return False


async def ensure_manager(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return False
    try:
        access = _manager_access_for_user(user.id)
    except Exception:
        logger.exception("Manager authorization check failed")
        await message.answer("Не удалось проверить доступ. Попробуйте ещё раз позже.")
        return False
    user_id = str(user.id).strip()
    if access is None:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {user_id}. "
            "Сообщите его администратору."
        )
        return False
    if not access["active"]:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {user_id}. "
            "Сообщите его администратору."
        )
        return False
    if is_partner_user(user.id):
        await message.answer(
            "Административные функции недоступны в партнёрском режиме.",
            reply_markup=get_main_keyboard(user.id, _normalized_role(access)),
        )
        return False
    return True


async def cancel_flow(message: Message, state: FSMContext) -> None:
    # Always discard any partially completed flow before returning to the menu.
    await state.clear()
    reply_markup = get_main_keyboard(message.from_user.id)
    await message.answer(
        "Операция отменена.",
        reply_markup=reply_markup,
    )


async def show_menu(message: Message, state: FSMContext) -> None:
    # Clear stale FSM data before any authorization or menu processing.
    await state.clear()
    user = message.from_user
    if user and is_partner_user(user.id):
        await message.answer(
            (
                "Панель управления автопрокатом готова. Выберите операцию:"
                if is_heavy_lux_partner_user(user.id)
                else "Партнёрский кабинет Dodge. Выберите раздел:"
            ),
            reply_markup=get_main_keyboard(user.id),
        )
        return
    if not await ensure_manager(message):
        return
    access = _manager_access_for_user(message.from_user.id)
    if (
        access
        and access["active"]
        and _normalized_role(access) in {"менеджер", "manager"}
        and str(message.from_user.id).strip() == "946721277"
    ):
        await message.answer(
            "Добро пожаловать, Влад! Выберите действие:",
            reply_markup=get_main_keyboard(
                message.from_user.id,
                _normalized_role(access),
            ),
        )
        return
    await message.answer(
        "Панель управления автопрокатом готова. Выберите операцию:",
        reply_markup=get_main_keyboard(
            message.from_user.id,
            _normalized_role(access),
        ),
    )


def _client_booking_days(start_date: str, end_date: str) -> int:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    return (end - start).days + 1


def _client_period_has_occupied_dates(
    start_date: str,
    end_date: str,
    occupied_dates: dict[str, list[str]] | set[str] | list[str],
) -> bool:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        return False
    occupied = set(occupied_dates)
    return any(
        (start + datetime.timedelta(days=offset)).isoformat() in occupied
        for offset in range((end - start).days + 1)
    )


def _client_prepayment(total_price: int) -> int:
    return max(1, math.ceil(total_price * CLIENT_PREPAYMENT_RATE))


def _client_payment_instruction(prepayment: int) -> str:
    return (
        "💳 РЕКВИЗИТЫ ДЛЯ ВНЕСЕНИЯ ПРЕДОПЛАТЫ:\n"
        f"Банк: {CLIENT_PAYMENT_BANK}\n"
        f"Номер телефона: {CLIENT_PAYMENT_PHONE}\n"
        f"Получатель: {CLIENT_PAYMENT_RECIPIENT}\n"
        f"Сумма к оплате: {_format_number(prepayment)} RUB\n\n"
        "⚠️ Важно:\n"
        "После перевода обязательно отправьте в этот чат скриншот или PDF "
        "чека об оплате.\n"
        "Предоставление чека фиксирует бронь за вами!"
    )


def _client_calendar(stage: str, start_date: datetime.date | None = None) -> SimpleCalendar:
    calendar = SimpleCalendar(
        cancel_btn="❌ Отменить / В меню",
        today_btn="Сегодня",
        show_alerts=True,
    )
    # The package uses the process locale for labels only when explicitly
    # requested. Keep the client calendar in Russian independently of the host.
    calendar._labels.days_of_week = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    calendar._labels.months = [
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
    ]
    minimum = start_date or _now_moscow().date()
    calendar.set_dates_range(
        datetime.datetime.combine(minimum, datetime.time.min),
        datetime.datetime.combine(datetime.date(2099, 12, 31), datetime.time.max),
    )
    return calendar


async def _send_client_calendar(
    message: Message,
    state: FSMContext,
    stage: str,
    start_date: datetime.date | None = None,
) -> None:
    data = await state.get_data()
    minimum_date = start_date or _now_moscow().date()
    calendar_date = start_date or _now_moscow().date()
    occupied_dates = data.get("client_occupied_dates", {})
    calendar_message = await message.answer(
        (
            "📅 Выберите дату начала аренды:"
            if stage == "start"
            else "📅 Выберите дату возврата автомобиля:"
        ),
        reply_markup=calendar_keyboard(
            "client_booking",
            stage,
            minimum_date=minimum_date,
            year=calendar_date.year,
            month=calendar_date.month,
            occupied_dates=occupied_dates,
            selected_start_date=start_date if stage == "end" else None,
        ),
    )
    await state.update_data(
        client_calendar_stage=stage,
        client_calendar_message_id=calendar_message.message_id,
        calendar_message_id=calendar_message.message_id,
    )


def _client_fleet_card_text(car: dict) -> str:
    daily_rate = _car_daily_rate(car)
    deposit = _car_base_deposit(car)
    deposit_text = (
        f"{_format_number(deposit)} RUB" if deposit is not None else "уточняется"
    )
    return (
        f"🏎 {_car_name(car)}\n\n"
        f"Класс: {_car_class(car)}\n"
        f"Цена: {daily_rate} RUB/сутки\n"
        f"Залог: {deposit_text}\n"
        f"Суточный пробег: {_car_mileage_limit(car)} км"
    )


async def _load_client_active_cars() -> list[dict]:
    """Load the public fleet without considering current rental occupancy.

    The client catalog and the first booking step are a showroom: a car that
    is currently rented must still be visible. Date availability is checked
    later in ``_finish_client_booking_dates`` after the client chooses dates.
    """
    cars = await asyncio.to_thread(get_all_cars)
    excluded_statuses = {"списан", "в ремонте"}
    return [
        dict(car)
        for car in cars
        if str(
            car.get("Статус")
            or car.get("Состояние")
            or car.get("Status")
            or ""
        ).strip().casefold()
        not in excluded_statuses
    ]


async def client_fleet_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        cars = await _load_client_active_cars()
    except Exception:
        logger.exception("Could not load fleet for client")
        await message.answer(
            "Не удалось загрузить автопарк. Попробуйте ещё раз позже.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    if not cars:
        await message.answer(
            "Сейчас нет автомобилей, доступных для онлайн-бронирования.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    for index, car in enumerate(cars):
        text = _client_fleet_card_text(car)
        markup = client_car_booking_keyboard(car)
        if index == 0:
            markup = markup
        photo_url = _car_photo_url(car)
        if photo_url:
            try:
                await message.answer_photo(
                    photo=photo_url,
                    caption=text,
                    reply_markup=markup,
                )
                continue
            except Exception:
                logger.warning("Could not send car photo for %s", _car_name(car))
        await message.answer(text, reply_markup=markup)
    await message.answer(
        "Выберите автомобиль для бронирования кнопкой под его карточкой.",
        reply_markup=client_welcome_keyboard(),
    )


async def start_client_booking(
    message: Message,
    state: FSMContext,
    preferred_delivery: str | None = None,
) -> None:
    await state.clear()
    try:
        cars = await _load_client_active_cars()
    except Exception:
        logger.exception("Could not load cars for client booking")
        await message.answer(
            "Не удалось загрузить автомобили. Попробуйте ещё раз позже.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    if not cars:
        await message.answer(
            "Сейчас нет автомобилей, доступных для онлайн-бронирования.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    await state.update_data(cars=cars, preferred_delivery=preferred_delivery)
    await state.set_state(ClientBookingState.car)
    await message.answer(
        "📅 Онлайн-бронирование\n\nШаг 1 из 5. Выберите автомобиль:",
        reply_markup=client_fleet_selection_keyboard(cars),
    )


def _client_car_from_list(cars: list[dict], car_id: str) -> dict | None:
    normalized = str(car_id).strip()
    return next(
        (
            car
            for car in cars
            if _car_id(car).strip() == normalized
        ),
        None,
    )


async def client_booking_car_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    car_id = callback.data.removeprefix("client_booking:car:")
    data = await state.get_data()
    cars = data.get("cars") or []
    if not cars:
        try:
            cars = await _load_client_active_cars()
        except Exception:
            logger.exception("Could not load cars after card callback")
            await callback.answer("Не удалось загрузить автомобиль.", show_alert=True)
            return
    car = _client_car_from_list(cars, car_id)
    if car is None:
        await callback.answer(
            "Автомобиль уже недоступен. Откройте автопарк заново.",
            show_alert=True,
        )
        return
    daily_rate = safe_int(_car_daily_rate(car))
    if daily_rate <= 0:
        await callback.answer("Для автомобиля не указан тариф.", show_alert=True)
        return
    try:
        active_bookings = await asyncio.to_thread(
            get_active_bookings_for_car,
            _car_id(car),
            _car_plate(car),
        )
    except Exception:
        logger.exception("Could not load client car bookings for calendar")
        await callback.answer(
            "Не удалось загрузить занятые даты автомобиля.",
            show_alert=True,
        )
        return
    occupied_dates = _occupied_dates(active_bookings)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(
        car=car,
        car_id=_car_id(car),
        car_name=_car_name(car),
        daily_rate=daily_rate,
        client_occupied_dates=occupied_dates,
    )
    await state.set_state(ClientBookingState.start_date)
    await callback.message.answer(
        f"Вы выбрали: {_car_name(car)}.\n"
        "Шаг 2 из 5. Выберите даты аренды кнопками календаря."
    )
    await _send_client_calendar(callback.message, state, "start")


async def client_calendar_callback(
    callback: CallbackQuery,
    callback_data: SimpleCalendarCallback,
    state: FSMContext,
) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    stage = data.get("client_calendar_stage")
    expected_state = (
        ClientBookingState.start_date.state
        if stage == "start"
        else ClientBookingState.end_date.state
    )
    if current_state != expected_state or not callback.message:
        await callback.answer(
            "Этот календарь устарел. Начните бронирование заново.",
            show_alert=True,
        )
        return
    calendar = _client_calendar(
        stage,
        datetime.date.fromisoformat(data["start_date"])
        if stage == "end" and data.get("start_date")
        else None,
    )
    if callback_data.act == SimpleCalAct.cancel:
        await callback.answer("Бронирование отменено")
        await state.clear()
        with suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Бронирование отменено.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    selected, selected_date = await calendar.process_selection(
        callback,
        callback_data,
    )
    if not selected or selected_date is None:
        return
    selected_iso = selected_date.date().isoformat()
    if stage == "start":
        await state.update_data(start_date=selected_iso)
        await state.set_state(ClientBookingState.end_date)
        await callback.message.answer(
            f"Дата начала: {format_date_ru(selected_iso)}.\n"
            "Теперь выберите дату возврата:",
        )
        await _send_client_calendar(
            callback.message,
            state,
            "end",
            selected_date.date(),
        )
        return
    start_date = data.get("start_date")
    if not start_date:
        await callback.answer("Дата начала потеряна. Начните заново.", show_alert=True)
        await state.clear()
        return
    await state.update_data(end_date=selected_iso)
    await _finish_client_booking_dates(
        callback.message,
        state,
        start_date,
        selected_iso,
    )


async def client_booking_date_text(
    message: Message,
    state: FSMContext,
) -> None:
    await message.answer(
        "Даты выбираются только кнопками календаря. "
        "Откройте последний календарь или отмените бронирование.",
        reply_markup=client_cancel_reply_keyboard(),
    )


async def _finish_client_booking_dates(
    message: Message,
    state: FSMContext,
    start_date: str,
    end_date: str,
) -> None:
    data = await state.get_data()
    car = data.get("car")
    if not car:
        await state.clear()
        await message.answer(
            "Автомобиль не выбран. Начните бронирование заново.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    if _client_period_has_occupied_dates(
        start_date,
        end_date,
        data.get("client_occupied_dates", {}),
    ):
        await message.answer(
            "В выбранный период попадают уже занятые даты. "
            "Выберите другой период.",
            reply_markup=client_cancel_reply_keyboard(),
        )
        return
    try:
        available_cars = await asyncio.to_thread(
            get_available_cars,
            start_date,
            end_date,
        )
    except Exception:
        logger.exception("Could not check client car availability")
        await state.clear()
        await message.answer(
            "Не удалось проверить доступность автомобиля. Попробуйте позже.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    selected_id = _car_id(car)
    if not any(_car_id(item).strip() == selected_id.strip() for item in available_cars):
        await state.clear()
        await message.answer(
            "К сожалению, этот автомобиль уже занят на выбранные даты. "
            "Выберите другой автомобиль.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    days = _client_booking_days(start_date, end_date)
    rent_price = int(data["daily_rate"]) * days
    await state.update_data(
        days=days,
        rent_price=rent_price,
    )
    if data.get("preferred_delivery") == "airport":
        await state.update_data(
            delivery_method="Подача в Пулково",
            delivery_address="Пулково",
            delivery_price=CLIENT_DELIVERY_FEE,
        )
        await state.set_state(ClientBookingState.client_name)
        await message.answer(
            f"Подача в Пулково выбрана: {_format_number(CLIENT_DELIVERY_FEE)} RUB.\n"
            "Шаг 4 из 5. Введите имя и фамилию арендатора:",
            reply_markup=client_cancel_reply_keyboard(),
        )
        return
    await state.set_state(ClientBookingState.delivery)
    await message.answer(
        f"Период: {format_date_ru(start_date)} — {format_date_ru(end_date)}\n"
        f"Автомобиль: {_car_name(car)}\n"
        f"Стоимость аренды: {_format_number(rent_price)} RUB\n\n"
        "Шаг 3 из 5. Выберите способ получения:",
        reply_markup=client_delivery_keyboard(),
    )


async def client_delivery_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    current_state = await state.get_state()
    choice = callback.data.removeprefix("client_delivery:")
    if choice == "back":
        if current_state != ClientBookingState.delivery_address.state:
            await callback.answer(
                "Этот выбор устарел. Начните бронирование заново.",
                show_alert=True,
            )
            return
        await callback.answer()
        await state.set_state(ClientBookingState.delivery)
        with suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Шаг 3 из 5. Выберите способ получения:",
            reply_markup=client_delivery_keyboard(),
        )
        return
    if current_state != ClientBookingState.delivery.state:
        await callback.answer(
            "Этот выбор устарел. Начните бронирование заново.",
            show_alert=True,
        )
        return
    if choice not in {"office", "airport", "address"}:
        await callback.answer("Недоступный вариант.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if choice == "address":
        await state.update_data(
            delivery_method="Доставка по адресу",
            delivery_address="",
            delivery_price=CLIENT_DELIVERY_FEE,
        )
        await state.set_state(ClientBookingState.delivery_address)
        await callback.message.answer(
            "📍 Укажите точный адрес доставки автомобиля в Санкт-Петербурге "
            "(улица, номер дома, отель/ЖК) или отправьте геопозицию:",
            reply_markup=client_delivery_address_keyboard(),
        )
        return
    delivery_method = "Подача в Пулково" if choice == "airport" else "Забрать в офисе"
    delivery_address = "Пулково" if choice == "airport" else "Офис Leon Rental"
    delivery_price = CLIENT_DELIVERY_FEE if choice == "airport" else 0
    await state.update_data(
        delivery_method=delivery_method,
        delivery_address=delivery_address,
        delivery_price=delivery_price,
    )
    await state.set_state(ClientBookingState.client_name)
    await callback.message.answer(
        "Шаг 4 из 5. Введите имя и фамилию арендатора:",
        reply_markup=client_cancel_reply_keyboard(),
    )


async def client_delivery_address(
    message: Message,
    state: FSMContext,
) -> None:
    if await state.get_state() != ClientBookingState.delivery_address.state:
        return
    if message.location:
        latitude = message.location.latitude
        longitude = message.location.longitude
        address = (
            f"Геопозиция: {latitude:.6f}, {longitude:.6f}\n"
            f"https://maps.google.com/?q={latitude},{longitude}"
        )
    else:
        address = (message.text or "").strip()
    if len(address) < 3:
        await message.answer(
            "Укажите полный адрес или отправьте геопозицию.",
            reply_markup=client_delivery_address_keyboard(),
        )
        return
    await state.update_data(
        delivery_method="Доставка по адресу",
        delivery_address=address,
        delivery_price=CLIENT_DELIVERY_FEE,
    )
    await state.set_state(ClientBookingState.client_name)
    await message.answer(
        "Адрес доставки сохранён.\n"
        "Шаг 4 из 5. Введите имя и фамилию арендатора:",
        reply_markup=client_cancel_reply_keyboard(),
    )


async def client_booking_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer(
            "Введите имя и фамилию полностью.",
            reply_markup=client_cancel_reply_keyboard(),
        )
        return
    await state.update_data(client_name=name)
    await state.set_state(ClientBookingState.phone)
    await message.answer(
        "Шаг 5 из 5. Отправьте Telegram-контакт кнопкой "
        "или введите свой username, начиная с @:",
        reply_markup=client_contact_keyboard(),
    )


async def client_booking_phone(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in {"Ввести Telegram username", "Ввести номер текстом"}:
        await message.answer(
            "Введите Telegram username, начиная с @:",
            reply_markup=client_contact_keyboard(),
        )
        return
    contact = (
        message.contact.phone_number
        if message.contact is not None
        else text
    )
    if not contact or len(contact.strip()) < 5:
        await message.answer(
            "Отправьте контакт кнопкой или укажите username вида @username.",
            reply_markup=client_contact_keyboard(),
        )
        return
    if message.contact is None and not contact.startswith("@"):
        normalized_phone = re.sub(r"[\s().-]", "", contact)
        if not re.fullmatch(r"\+?\d{7,15}", normalized_phone):
            await message.answer(
                "Укажите username вида @username или корректный номер телефона.",
                reply_markup=client_contact_keyboard(),
            )
            return
    await state.update_data(phone=contact.strip())
    data = await state.get_data()
    rent_price = int(data["rent_price"])
    delivery_price = int(data.get("delivery_price", 0))
    total_price = rent_price + delivery_price
    await state.update_data(total_price=total_price)
    await state.set_state(ClientBookingState.confirmation)
    await message.answer(
        _client_booking_summary_text({**data, "phone": contact.strip(), "total_price": total_price}),
        reply_markup=client_booking_confirmation_keyboard(),
    )


def client_booking_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить заявку",
                    callback_data="client_booking:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить / В меню",
                    callback_data="client_booking:cancel",
                )
            ],
        ]
    )


def _client_booking_summary_text(data: dict) -> str:
    delivery_method = data.get("delivery_method") or data.get(
        "delivery_address",
        "—",
    )
    address_lines = ""
    if delivery_method == "Доставка по адресу":
        address_lines = f"Адрес доставки: {data.get('delivery_address', '—')}\n"
    return (
        "📋 Проверьте заявку:\n\n"
        f"Автомобиль: {data.get('car_name', '—')}\n"
        f"Период: {format_date_ru(data.get('start_date', ''))} — "
        f"{format_date_ru(data.get('end_date', ''))}\n"
        f"Способ получения: {delivery_method}\n"
        f"{address_lines}"
        f"Арендатор: {data.get('client_name', '—')}\n"
        f"Контакт: {data.get('phone', '—')}\n\n"
        f"Аренда: {_format_number(data.get('rent_price', 0))} RUB\n"
        f"Подача: {_format_number(data.get('delivery_price', 0))} RUB\n"
        f"Итого: {_format_number(data.get('total_price', 0))} RUB\n\n"
        "Нажмите «Подтвердить заявку», чтобы отправить запрос менеджеру."
    )


async def client_booking_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer("Бронирование отменено")
    await state.clear()
    if callback.message:
        with suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Бронирование отменено.",
            reply_markup=client_welcome_keyboard(),
        )


def _client_booking_admin_text(
    booking_id: object,
    data: dict,
) -> str:
    delivery_method = data.get("delivery_method") or data.get(
        "delivery_address",
        "—",
    )
    address_lines = ""
    if delivery_method == "Доставка по адресу":
        address_lines = f"Адрес доставки: {data.get('delivery_address', '—')}\n"
    return (
        "🔔 Новая заявка с сайта/бота!\n\n"
        f"Номер заявки: #{booking_id}\n"
        f"Авто: {data.get('car_name', '—')}\n"
        f"Клиент: {data.get('client_name', '—')} "
        f"({data.get('phone', '—')})\n"
        f"Даты: {format_date_ru(data.get('start_date', ''))} — "
        f"{format_date_ru(data.get('end_date', ''))}\n"
        f"Способ получения: {delivery_method}\n"
        f"{address_lines}"
        f"Итого: {_format_number(data.get('total_price', 0))} RUB\n"
        "Статус: Заявка с сайта/бота"
    )


async def client_booking_confirm_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    admin_bot: Bot | None = None,
) -> None:
    if not callback.message or await state.get_state() != ClientBookingState.confirmation.state:
        await callback.answer(
            "Заявка уже обработана или устарела.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if not callback.from_user:
        await callback.answer("Не удалось определить клиента.", show_alert=True)
        return
    try:
        booking_id = await asyncio.to_thread(
            add_public_booking,
            car_id=data["car_id"],
            car_name=data["car_name"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            client_name=data["client_name"],
            phone=data["phone"],
            client_telegram_id=callback.from_user.id,
            price=int(data["rent_price"]),
            prepayment=0,
            delivery_address=data.get("delivery_address", "Офис Leon Rental"),
            delivery_price=int(data.get("delivery_price", 0)),
            status="Заявка с сайта/бота",
            source="Сайт",
        )
    except ValueError:
        await state.clear()
        await callback.answer("Автомобиль уже занят на эти даты.", show_alert=True)
        await callback.message.answer(
            "Автомобиль уже заняли на выбранные даты. "
            "Откройте автопарк и выберите другой вариант.",
            reply_markup=client_welcome_keyboard(),
        )
        return
    except Exception:
        logger.exception("Could not save confirmed client booking")
        await callback.answer("Не удалось сохранить заявку.", show_alert=True)
        return

    notification_bot = admin_bot or bot
    admin_text = _client_booking_admin_text(booking_id, data)
    delivered_to_admin = False
    for admin_id in _admin_notification_ids():
        try:
            await notification_bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=client_admin_booking_keyboard(booking_id),
            )
            delivered_to_admin = True
        except Exception:
            logger.exception(
                "Could not notify manager %s about client booking %s",
                admin_id,
                booking_id,
            )
    await state.clear()
    await callback.answer("Заявка отправлена менеджеру")
    with suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Заявка #{booking_id} отправлена менеджеру.\n"
        "Мы свяжемся с вами для подтверждения деталей.",
        reply_markup=client_welcome_keyboard(),
    )
    if not delivered_to_admin:
        logger.warning("Client booking %s was saved without manager delivery", booking_id)


async def client_ai_booking_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    await callback.answer()
    await start_client_booking(callback.message, state)


async def client_pulkovo_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "✈️ Подача автомобиля в аэропорт Пулково\n\n"
        f"Стоимость подачи: {_format_number(CLIENT_DELIVERY_FEE)} RUB.\n"
        "Менеджер согласует точное время и место встречи после заявки.",
        reply_markup=client_pulkovo_keyboard(),
    )
    await message.answer(
        "Выберите раздел в нижнем меню, чтобы продолжить.",
        reply_markup=client_welcome_keyboard(),
    )


async def client_pulkovo_booking_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message:
        await callback.answer()
        await start_client_booking(
            callback.message,
            state,
            preferred_delivery="airport",
        )
    else:
        await callback.answer()


async def client_rules_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "📋 Условия и залог\n\n"
        "• Аренда доступна клиентам от 21 года; для отдельных автомобилей "
        "может потребоваться возраст от 25 лет.\n"
        "• Для оформления нужны паспорт и водительское удостоверение.\n"
        "• Страховой залог вносится до выдачи автомобиля и возвращается после "
        "приёма машины, если нет штрафов, повреждений и задолженностей.\n"
        "• В стоимость входит суточный лимит пробега, указанный в карточке "
        "автомобиля. Превышение лимита рассчитывается по тарифу.\n"
        "• Точный размер залога и дополнительные условия зависят от модели "
        "и подтверждаются менеджером.",
        reply_markup=client_welcome_keyboard(),
    )


async def client_manager_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Наш менеджер поможет подобрать автомобиль и оформить аренду.\n"
        "Напишите @leonRentalSPB или позвоните: +7 (911) 215-56-62.",
        reply_markup=client_welcome_keyboard(),
    )


def _client_local_faq_response(prompt: str) -> str:
    normalized_prompt = prompt.casefold()
    for keywords, response in CLIENT_FAQ_RULES:
        if any(keyword in normalized_prompt for keyword in keywords):
            return response
    return CLIENT_FAQ_FALLBACK


async def client_ai_message(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        return
    prompt = (message.text or "").strip()
    if not prompt:
        return
    answer = _client_local_faq_response(prompt)
    await message.answer(
        answer,
        reply_markup=client_ai_booking_keyboard(),
    )


async def client_booking_receipt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    admin_bot: Bot | None = None,
) -> None:
    if message.document is None and not message.photo:
        await message.answer(
            "Отправьте фото или PDF-файл чека об оплате предоплаты."
        )
        return
    data = await state.get_data()
    if not message.from_user:
        await message.answer("Не удалось определить Telegram-пользователя.")
        return
    try:
        booking_id = await asyncio.to_thread(
            add_public_booking,
            car_id=data["car_id"],
            car_name=data["car_name"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            client_name=data["client_name"],
            phone=data["phone"],
            client_telegram_id=message.from_user.id,
            price=int(data["rent_price"]),
            prepayment=int(data["prepayment"]),
            delivery_address=data.get("delivery_address", "Самовывоз"),
            delivery_price=int(data.get("delivery_price", 0)),
        )
    except ValueError:
        logger.info("Client booking became unavailable before receipt save")
        await state.clear()
        await message.answer(
            "К сожалению, выбранный автомобиль уже заняли на эти даты. "
            "Начните бронирование заново, чтобы выбрать другой вариант."
        )
        return
    except Exception:
        logger.exception("Could not save public client booking")
        await state.clear()
        await message.answer(
            "Не удалось сохранить заявку. Попробуйте отправить её позже."
        )
        return

    admin_text = _client_booking_admin_text(booking_id, data)
    delivered_to_admin = False
    notification_bot = admin_bot or bot
    for admin_id in _admin_notification_ids():
        try:
            await bot.forward_message(
                chat_id=admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            await notification_bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=client_admin_booking_keyboard(booking_id),
            )
            delivered_to_admin = True
        except Exception:
            logger.exception(
                "Could not forward client receipt for booking %s to %s",
                booking_id,
                admin_id,
            )
    await state.clear()
    await message.answer(
        "Спасибо! Чек получен. Наш менеджер проверит оплату и подтвердит "
        "бронь в течение 10 минут."
    )


async def client_booking_admin_callback(
    callback: CallbackQuery,
    bot: Bot,
    client_bot: Bot | None = None,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "Недостаточно прав для обработки заявок.",
            show_alert=True,
        )
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {"confirm", "reject"}:
        await callback.answer("Некорректная заявка.", show_alert=True)
        return
    booking_id = parts[2]
    try:
        booking = await asyncio.to_thread(get_booking_by_id, booking_id)
        if not booking:
            raise ValueError("booking not found")
        status = "Подтверждена" if parts[1] == "confirm" else "Отклонена"
        updated_booking = await asyncio.to_thread(
            update_public_booking_status,
            booking_id,
            status,
        )
    except ValueError:
        await callback.answer(
            "Заявка уже обработана или не найдена.",
            show_alert=True,
        )
        return
    except Exception:
        logger.exception("Could not process public booking %s", booking_id)
        await callback.answer("Не удалось обновить заявку.", show_alert=True)
        return

    client_id = _booking_manager_id(updated_booking or booking)
    if client_id:
        notification_bot = client_bot or bot
        client_message = (
            "🎉 Ваша бронь успешно подтверждена! Ждём вас."
            if status == "Подтверждена"
            else "К сожалению, заявка на бронирование отклонена. "
            "Свяжитесь с менеджером для уточнения деталей."
        )
        try:
            await notification_bot.send_message(
                chat_id=client_id,
                text=client_message,
            )
        except Exception:
            logger.exception(
                "Could not notify client %s about booking %s",
                client_id,
                booking_id,
            )
    await callback.answer(
        "Бронь подтверждена" if status == "Подтверждена" else "Заявка отклонена"
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Заявка #{booking_id}: статус «{status}»."
    )


async def start_handler(message: Message, state: FSMContext) -> None:
    # /start must interrupt every active FSM flow, including stale states.
    await state.clear()
    try:
        access = _manager_access_for_user(message.from_user.id)
    except Exception:
        logger.exception("Could not resolve /start access")
        await message.answer("Не удалось проверить доступ. Попробуйте позже.")
        return
    if access is not None:
        await show_menu(message, state)
        return
    await message.answer(
        "Этот бот предназначен для команды Leon Rental. "
        "Для онлайн-бронирования используйте @LeonRentalSpb_bot."
    )


async def client_start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Добро пожаловать в Leon Rental! 🚗\n"
        "Здесь вы можете выбрать премиальный автомобиль, рассчитать стоимость "
        "и забронировать поездку онлайн. Используйте постоянное меню ниже.",
        reply_markup=client_welcome_keyboard(),
    )


async def client_manager_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Наш менеджер поможет подобрать автомобиль и оформить аренду.\n"
        "Напишите @leonRentalSPB или позвоните: +7 (911) 215-56-62.",
        reply_markup=client_welcome_keyboard(),
    )


async def client_cancel_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Бронирование отменено. Выберите действие в нижнем меню.",
        reply_markup=client_welcome_keyboard(),
    )


async def partner_bookings(message: Message) -> None:
    if not await ensure_partner(message):
        return
    partner_keyword = (
        HEAVY_LUX_PARTNER_SOURCE
        if is_heavy_lux_partner_user(message.from_user.id)
        else PARTNER_CAR_KEYWORD
    )
    try:
        bookings = await asyncio.to_thread(
            get_partner_bookings,
            partner_keyword,
        )
    except Exception:
        logger.exception("Could not load partner bookings")
        await message.answer("Не удалось загрузить партнёрские брони.")
        return
    if not bookings:
        await message.answer(
            "Партнёрские брони не найдены.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    lines = [
        f"🔎 БРОНИ {HEAVY_LUX_PARTNER_SOURCE if is_heavy_lux_partner_user(message.from_user.id) else 'DODGE'}",
        "",
    ]
    partner_source = (
        HEAVY_LUX_PARTNER_SOURCE
        if is_heavy_lux_partner_user(message.from_user.id)
        else "Dodge"
    )
    for booking in bookings:
        lines.extend(
            [_partner_booking_card_text(booking, source=partner_source), ""]
        )
    chunks = split_telegram_message("\n".join(lines).rstrip())
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=(
                get_main_keyboard(message.from_user.id)
                if index == len(chunks) - 1
                else None
            ),
        )


async def partner_status(message: Message) -> None:
    if not await ensure_partner(message):
        return
    partner_keyword = (
        HEAVY_LUX_PARTNER_SOURCE
        if is_heavy_lux_partner_user(message.from_user.id)
        else PARTNER_CAR_KEYWORD
    )
    try:
        cars = await asyncio.to_thread(
            get_partner_cars,
            partner_keyword,
        )
    except Exception:
        logger.exception("Could not load partner car status")
        await message.answer("Не удалось загрузить статус партнёрских авто.")
        return
    if not cars:
        await message.answer(
            "Партнёрские автомобили не найдены.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    lines = [
        f"🚗 СТАТУС {HEAVY_LUX_PARTNER_SOURCE if is_heavy_lux_partner_user(message.from_user.id) else 'DODGE'}",
        "",
    ]
    for car in cars:
        lines.extend(
            [
                f"Автомобиль: {_car_name(car)}",
                f"Статус: {_partner_car_status(car)}",
                f"Текущий пробег: {_value(car, 'Текущий пробег (км)', 'Пробег', 'Текущий пробег', default='—')} км",
                f"Госномер: {_value(car, 'Госномер', 'Номер авто', 'Номер', 'Plate', default='—')}",
                "",
            ]
        )
    await message.answer(
        "\n".join(lines).rstrip(),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


async def partner_expenses(message: Message) -> None:
    if not await ensure_partner(message):
        return
    if is_heavy_lux_partner_user(message.from_user.id):
        await message.answer(
            "Раздел расходов недоступен в партнёрском режиме.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    try:
        expenses = await asyncio.to_thread(
            get_partner_expenses,
            PARTNER_CAR_KEYWORD,
        )
    except Exception:
        logger.exception("Could not load partner expenses")
        await message.answer("Не удалось загрузить расходы Dodge.")
        return
    if not expenses:
        await message.answer(
            "Расходы Dodge не найдены.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    lines = ["📑 РАСХОДЫ DODGE", ""]
    for expense in expenses:
        lines.extend(
            [
                f"Дата: {format_date_ru(_value(expense, 'Дата', default='—'))}",
                f"Сумма: {_format_number(expense.get('Сумма (RUB)', 0))} RUB",
                f"Категория: {_value(expense, 'Категория / На что', 'Категория', default='—')}",
                f"Описание: {_value(expense, 'Примечание', 'Комментарий', 'Описание', default='—')}",
                "",
            ]
        )
    chunks = split_telegram_message("\n".join(lines).rstrip())
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=(
                get_main_keyboard(message.from_user.id)
                if index == len(chunks) - 1
                else None
            ),
        )


async def ensure_finance_access(message: Message) -> bool:
    user = message.from_user
    if user is None:
        await message.answer("Не удалось определить пользователя.")
        return False
    try:
        access = _manager_access_for_user(user.id)
    except Exception:
        logger.exception("Finance authorization check failed")
        await message.answer("Не удалось проверить доступ. Попробуйте ещё раз позже.")
        return False
    if access is None:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {str(user.id).strip()}. "
            "Сообщите его администратору."
        )
        return False
    if not access["active"]:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {str(user.id).strip()}. "
            "Сообщите его администратору."
        )
        return False
    if is_partner_user(user.id):
        await message.answer(
            "Финансовые операции недоступны в партнёрском режиме.",
            reply_markup=get_main_keyboard(user.id, _normalized_role(access)),
        )
        return False
    if _normalized_role(access) not in {
        "администратор",
        "admin",
        ADMIN_PLUS_ROLE,
    }:
        await message.answer(
            "Финансовые операции доступны только уполномоченным менеджерам.",
            reply_markup=get_main_keyboard(user.id, _normalized_role(access)),
        )
        return False
    return True


def _callback_has_finance_access(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        return False
    try:
        access = _manager_access_for_user(callback.from_user.id)
        return bool(
            access
            and access["active"]
            and _normalized_role(access)
            in {"администратор", "admin", ADMIN_PLUS_ROLE}
        )
    except Exception:
        logger.exception("Finance callback authorization check failed")
        return False


async def ensure_financial_report_access(message: Message) -> bool:
    """Allow administrators and the isolated Heavy Lux report role."""
    user = message.from_user
    if user is None:
        await message.answer("Не удалось определить пользователя.")
        return False
    try:
        access = _manager_access_for_user(user.id)
    except Exception:
        logger.exception("Financial report authorization check failed")
        await message.answer("Не удалось проверить доступ. Попробуйте ещё раз позже.")
        return False
    if not access or not access["active"]:
        await message.answer(
            "Доступ ограничен. "
            f"Ваш Telegram ID: {str(user.id).strip()}. "
            "Сообщите его администратору."
        )
        return False
    if _normalized_role(access) == HEAVY_LUX_PARTNER_ROLE:
        return True
    if _normalized_role(access) not in {
        "администратор",
        "admin",
        ADMIN_PLUS_ROLE,
    }:
        await message.answer(
            "Финансовый отчёт доступен только уполномоченным пользователям.",
            reply_markup=get_main_keyboard(user.id, _normalized_role(access)),
        )
        return False
    return True


def _callback_has_financial_report_access(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        return False
    try:
        access = _manager_access_for_user(callback.from_user.id)
        return bool(
            access
            and access["active"]
            and _normalized_role(access)
            in {
                HEAVY_LUX_PARTNER_ROLE,
                "администратор",
                "admin",
                ADMIN_PLUS_ROLE,
            }
        )
    except Exception:
        logger.exception("Financial report callback authorization check failed")
        return False


def _format_financial_report(report: dict) -> str:
    payment_breakdown = report.get("payment_breakdown", {})
    source_filter = report.get("source_filter", "all")
    if source_filter == "leon":
        title = f"🏢 ФИНАНСОВЫЙ ОТЧЁТ LEON RENTAL ЗА {report['month']}"
        summary_lines = [
            f"🟢 Валовый приход: "
            f"{_format_number(report['gross_income'])} RUB",
            "🤝 Комиссия партнёров: 0 RUB",
            f"🔴 Прямые расходы за месяц: "
            f"{_format_number(report['total_expenses'])} RUB",
            f"💎 Чистая прибыль: "
            f"{_format_number(report['company_profit'])} RUB",
        ]
    elif source_filter == "heavy_lux_cr":
        title = f"🤝 ФИНАНСОВЫЙ ОТЧЁТ HEAVY_LUX_CR ЗА {report['month']}"
        summary_lines = [
            f"🟢 Общий валовый сбор: "
            f"{_format_number(report['gross_income'])} RUB",
            f"🤝 Доход партнёра heavy_lux_cr (30%): "
            f"{_format_number(report.get('partner_fee', 0))} RUB",
            f"🏢 Доля Leon Rental (70%): "
            f"{_format_number(report.get('company_revenue', 0))} RUB",
            f"🔴 Прямые расходы за месяц: "
            f"{_format_number(report['total_expenses'])} RUB",
            f"💎 Чистый остаток Leon Rental: "
            f"{_format_number(report['company_profit'])} RUB",
        ]
    else:
        title = f"📊 СВОДНЫЙ ФИНАНСОВЫЙ ОТЧЁТ ЗА {report['month']}"
        summary_lines = [
            f"🟢 Общий приход: "
            f"{_format_number(report['gross_income'])} RUB",
            f"🤝 Комиссия heavy_lux_cr (30%): "
            f"{_format_number(report.get('partner_fee', 0))} RUB",
            f"🔴 Расходы за месяц: "
            f"{_format_number(report['total_expenses'])} RUB",
            f"💎 Итоговая чистая прибыль Leon Rental: "
            f"{_format_number(report['company_profit'])} RUB",
        ]
    lines = [
        title,
        "",
        *summary_lines,
        "",
        "💳 ВЫРУЧКА ПО СПОСОБАМ ОПЛАТЫ:",
        f"  Наличные: {_format_number(payment_breakdown.get('Наличные', 0))} RUB",
        f"  Безнал: {_format_number(payment_breakdown.get('Безнал', 0))} RUB",
        f"  СБП: {_format_number(payment_breakdown.get('СБП', 0))} RUB",
        "",
        "🚗 ЧИСТАЯ ПРИБЫЛЬ ПО АВТО:",
    ]
    if not report["cars"]:
        lines.append("Нет автомобилей для детализации.")
    else:
        for car in report["cars"]:
            label = (
                f"{car['Марка и Модель']} ({car['Госномер']})"
                if car["Марка и Модель"]
                else f"Авто {car['ID Авто']} ({car['Госномер']})"
            )
            if source_filter == "heavy_lux_cr":
                lines.extend(
                    [
                        "",
                        label,
                        f"  Валовый сбор: "
                        f"{_format_number(car.get('Валовый сбор (RUB)', 0))} RUB",
                        f"  Комиссия 30%: "
                        f"{_format_number(car.get('Комиссия heavy_lux_cr (RUB)', 0))} RUB",
                        f"  Доля Leon Rental 70%: "
                        f"{_format_number(car.get('Доля Leon Rental (70%) (RUB)', 0))} RUB",
                        f"  Прямые расходы: "
                        f"{_format_number(car['Затраты на авто (RUB)'])} RUB",
                        f"  Чистый остаток: "
                        f"{_format_number(car['Чистая прибыль авто (RUB)'])} RUB",
                    ]
                )
            else:
                lines.extend(
                    [
                        "",
                        label,
                        f"  Приход: "
                        f"{_format_number(car['Приход / Выручка (RUB)'])} RUB",
                        f"  Прямые расходы: "
                        f"{_format_number(car['Затраты на авто (RUB)'])} RUB",
                        f"  Чистая прибыль: "
                        f"{_format_number(car['Чистая прибыль авто (RUB)'])} RUB",
                    ]
                )
    return "\n".join(lines)


def _format_heavy_lux_partner_report(report: dict) -> str:
    """Format only the financial facts that belong to the Heavy Lux partner."""
    lines = [
        f"📈 ФИНАНСОВЫЙ ОТЧЁТ HEAVY LUX ЗА {report['month']}",
        "",
        f"🤝 Всего сдач через партнёра: {report.get('booking_count', 0)}",
        f"💵 Валовый сбор с клиентов: "
        f"{_format_number(report.get('gross_income', 0))} RUB",
        f"💎 Ваша комиссия к выплате (30%): "
        f"{_format_number(report.get('partner_fee', 0))} RUB",
        "",
        "🚗 СДАЧИ ПО АВТОМОБИЛЯМ:",
    ]
    cars = report.get("cars", [])
    if not cars:
        lines.append("Нет сдач за выбранный месяц.")
        return "\n".join(lines)
    for car in cars:
        lines.extend(
            [
                "",
                f"• {_car_report_label(car)}",
                f"  - Поездок: {car.get('Количество сдач', 0)}",
                f"  - Оборот: "
                f"{_format_number(car.get('Валовый сбор (RUB)', 0))} RUB",
                f"  - Ваша комиссия (30%): "
                f"{_format_number(car.get('Комиссия heavy_lux_cr (RUB)', 0))} RUB",
            ]
        )
    return "\n".join(lines)


def _car_report_label(car: dict) -> str:
    name = car.get("Марка и Модель") or f"Авто {car.get('ID Авто', '—')}"
    plate = car.get("Госномер") or "без госномера"
    return f"{name} ({plate})"


def _financial_car_key(car: dict) -> str:
    car_id = _value(car, "ID Авто", "ID", "Car ID", default="")
    if car_id:
        return f"id:{car_id.casefold()}"
    plate = _value(
        car,
        "Госномер",
        "Номер авто",
        "Номер",
        "Plate",
        default="",
    )
    return f"plate:{plate.casefold()}"


def _format_selected_cars_report(
    report: dict,
    selected_car_keys: set[str],
) -> str:
    selected_cars = [
        car
        for car in report.get("cars", [])
        if _financial_car_key(car) in selected_car_keys
    ]
    lines = [
        f"🚗 ОТЧЁТ ПО ВЫБРАННЫМ АВТО ЗА {report['month']}",
        "",
    ]
    group_income = 0
    group_expenses = 0
    group_profit = 0
    for car in selected_cars:
        income = safe_int(car.get("Приход / Выручка (RUB)"))
        expenses = safe_int(car.get("Затраты на авто (RUB)"))
        profit = safe_int(car.get("Чистая прибыль авто (RUB)"))
        group_income += income
        group_expenses += expenses
        group_profit += profit
        lines.extend(
            [
                f"🚗 {_car_report_label(car)}",
                f"  Выручка: {_format_number(income)} RUB",
                "  Расходы на авто:",
            ]
        )
        by_category = car.get("Расходы по статьям", {})
        if by_category:
            for category, amount in by_category.items():
                lines.append(
                    f"    • {category}: {_format_number(safe_int(amount))} RUB"
                )
        else:
            lines.append("    • Нет расходов")
        lines.extend(
            [
                f"  Пробег за месяц: +"
                f"{_format_number(safe_int(car.get('Пробег за месяц (км)', 0)))} км",
                f"  Чистая прибыль машины: {_format_number(profit)} RUB",
                "",
            ]
        )
    lines.extend(
        [
            "━━━━━━━━━━━━",
            f"ИТОГ ПО ГРУППЕ:",
            f"🟢 Суммарная выручка: {_format_number(group_income)} RUB",
            f"🔴 Суммарные расходы группы: "
            f"{_format_number(group_expenses)} RUB",
            f"💎 Суммарная чистая прибыль группы: "
            f"{_format_number(group_profit)} RUB",
        ]
    )
    return "\n".join(lines)


async def _answer_financial_report(
    message: Message,
    text: str,
    user_id: int,
) -> None:
    chunks = split_telegram_message(text)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=(
                get_main_keyboard(user_id)
                if index == len(chunks) - 1
                else None
            ),
        )


GENERAL_EXPENSE_LABELS = frozenset(
    {
        "",
        "общие",
        "общие расходы компании",
        "общие расходы (весь автопарк)",
        "🏢 общие расходы (весь автопарк)",
    }
)


def _expense_history_vehicle(expense: dict) -> str:
    vehicle = _value(
        expense,
        "Автомобиль (Госномер / Общие)",
        "Автомобиль",
        default="",
    )
    return (
        "Общие"
        if vehicle.casefold() in GENERAL_EXPENSE_LABELS
        else vehicle
    )


def _expense_history_author(expense: dict) -> str:
    author = _value(
        expense,
        "Имя",
        "Кто внес",
        "Менеджер",
        default="",
    )
    if author:
        return author
    telegram_id = _value(
        expense,
        "Telegram ID",
        "Telegram ID менеджера",
        default="",
    )
    return f"Telegram ID {telegram_id}" if telegram_id else "—"


def _format_expense_history(
    expenses: list[dict],
    month_key: str,
    filter_label: str | None = None,
) -> str:
    if not expenses:
        filter_text = f"\n🔎 Фильтр: {filter_label}" if filter_label else ""
        return (
            f"📅 Месяц: {month_key}{filter_text}\n\n"
            "За выбранный период и фильтр расходов не найдено."
        )

    title = f"📑 ИСТОРИЯ РАСХОДОВ\n📅 Месяц: {month_key}"
    if filter_label:
        title += f"\n🔎 Фильтр: {filter_label}"
    lines = [title, "", "Список записей:"]
    total = 0
    for expense in expenses:
        amount = safe_int(expense.get("Сумма (RUB)"))
        total += amount
        lines.append(
            "• "
            f"№{_value(expense, 'ID Записи', 'ID', default='—')} | "
            f"{format_date_ru(_value(expense, 'Дата', default='—'))} | "
            f"{_format_number(amount)} ₽ | "
            f"{_value(expense, 'Категория / На что', 'Категория', default='—')} | "
            f"{_expense_history_vehicle(expense)} | "
            f"{_expense_history_author(expense)}"
        )
        lines.append(
            f"  💬 Примечание: "
            f"{_value(expense, 'Примечание', 'Комментарий', default='—')}"
        )
    lines.extend(
        [
            "",
            f"💰 Всего расходов за выбранный период: "
            f"{_format_number(total)} ₽",
        ]
    )
    return "\n".join(lines)


def _expense_export_csv(
    expenses: list[dict],
    month_key: str,
    filter_label: str | None = None,
) -> tuple[bytes, int]:
    """Build an Excel-compatible UTF-8 CSV and return it with its total."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["Период", month_key])
    if filter_label:
        writer.writerow(["Фильтр", filter_label])
    writer.writerow([])
    headers = [
        "ID",
        "Дата",
        "Сумма (RUB)",
        "Категория / На что",
        "Привязка",
        "Автор",
        "Примечание",
    ]
    writer.writerow(headers)
    total = 0
    for expense in expenses:
        amount = safe_int(expense.get("Сумма (RUB)"))
        total += amount
        writer.writerow(
            [
                _value(expense, "ID Записи", "ID", default=""),
                _value(expense, "Дата", default=""),
                amount,
                _value(
                    expense,
                    "Категория / На что",
                    "Категория",
                    default="",
                ),
                _expense_history_vehicle(expense),
                _expense_history_author(expense),
                _value(
                    expense,
                    "Примечание",
                    "Комментарий",
                    default="",
                ),
            ]
        )
    writer.writerow([])
    writer.writerow(["ИТОГО", "", total, "", "", "", ""])
    return output.getvalue().encode("utf-8-sig"), total


def _expense_export_filename(
    month_key: str,
    vehicle_filter: str | None = None,
) -> str:
    suffix = vehicle_filter or "all"
    safe_suffix = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "_", suffix).strip("_")
    return f"expenses_{month_key}_{safe_suffix or 'all'}.csv"


async def _send_expense_history(
    message: Message,
    user_id: int,
    month_key: str,
    vehicle_filter: str | None = None,
    filter_label: str | None = None,
) -> None:
    try:
        expenses = await asyncio.to_thread(
            get_expenses_for_month,
            month_key,
            vehicle_filter,
        )
    except Exception:
        logger.exception(
            "Could not load expense history for %s (filter=%s)",
            month_key,
            vehicle_filter,
        )
        await message.answer(
            "Не удалось получить историю расходов из Google Sheets.",
            reply_markup=get_main_keyboard(user_id),
        )
        return
    await _answer_financial_report(
        message,
        _format_expense_history(expenses, month_key, filter_label),
        user_id,
    )
    if expenses:
        await message.answer(
            "Файл с этой же выборкой можно скачать для бухгалтерии:",
            reply_markup=expense_history_export_keyboard(
                month_key,
                vehicle_filter,
            ),
        )


async def expense_history_export_callback(
    callback: CallbackQuery,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    try:
        _, month_key, encoded_filter = callback.data.split(":", 2)
        vehicle_filter = unquote(encoded_filter)
    except ValueError:
        await callback.answer("Некорректный запрос экспорта.", show_alert=True)
        return
    if not re.fullmatch(r"\d{4}-\d{2}", month_key):
        await callback.answer("Некорректный месяц.", show_alert=True)
        return
    if vehicle_filter == "all":
        vehicle_filter = None
        filter_label = None
    elif vehicle_filter == "general":
        filter_label = "Общие расходы"
    elif vehicle_filter:
        filter_label = f"Автомобиль: {vehicle_filter}"
    else:
        await callback.answer("Некорректный фильтр.", show_alert=True)
        return

    try:
        available_months = await asyncio.to_thread(
            get_available_expense_months,
            _now_minsk().date(),
        )
    except Exception:
        logger.exception("Could not validate expense export month")
        await callback.answer(
            "Не удалось проверить доступность месяца.",
            show_alert=True,
        )
        return
    if month_key not in available_months:
        await callback.answer(
            "Этот месяц больше недоступен. Откройте историю расходов заново.",
            show_alert=True,
        )
        return

    try:
        expenses = await asyncio.to_thread(
            get_expenses_for_month,
            month_key,
            vehicle_filter,
        )
    except Exception:
        logger.exception(
            "Could not export expense history for %s (filter=%s)",
            month_key,
            vehicle_filter,
        )
        await callback.answer(
            "Не удалось сформировать экспорт из Google Sheets.",
            show_alert=True,
        )
        return
    if not expenses:
        await callback.answer(
            "По выбранному месяцу и фильтру расходов нет — файл не сформирован.",
            show_alert=True,
        )
        return

    csv_bytes, total = _expense_export_csv(
        expenses,
        month_key,
        filter_label,
    )
    await callback.answer("CSV-файл готов")
    with suppress(TelegramAPIError):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer_document(
        document=BufferedInputFile(
            csv_bytes,
            filename=_expense_export_filename(month_key, vehicle_filter),
        ),
        caption=(
            f"Экспорт расходов за {month_key}"
            f"{f' ({filter_label})' if filter_label else ''}\n"
            f"Итого: {_format_number(total)} ₽"
        ),
    )


async def start_expense_history(message: Message, state: FSMContext) -> None:
    if not await ensure_finance_access(message):
        return
    await state.clear()
    try:
        months = await asyncio.to_thread(
            get_available_expense_months,
            _now_minsk().date(),
        )
    except Exception:
        logger.exception("Could not load expense history months")
        await message.answer(
            "Не удалось получить список месяцев расходов.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.set_state(ExpenseHistoryForm.month)
    selection_message = await message.answer(
        "Выберите месяц для просмотра расходов:",
        reply_markup=expense_history_month_keyboard(months),
    )
    await state.update_data(
        expense_history_months=months,
        expense_history_month_message_id=selection_message.message_id,
    )


async def expense_history_month_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != ExpenseHistoryForm.month.state:
        await callback.answer(
            "Этот выбор месяца устарел. Откройте просмотр расходов заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get(
        "expense_history_month_message_id"
    ):
        await callback.answer(
            "Используйте последний список месяцев.",
            show_alert=True,
        )
        return
    month_key = callback.data.split(":", 1)[1]
    if month_key not in data.get("expense_history_months", []):
        await callback.answer("Недоступный месяц.", show_alert=True)
        return
    await callback.answer(f"Выбран месяц: {month_key}")
    await state.update_data(expense_history_month=month_key)
    await state.set_state(ExpenseHistoryForm.filter)
    await callback.message.edit_text(
        f"Месяц расходов: {month_key}\n\nВыберите фильтр:",
        reply_markup=expense_history_filter_keyboard(),
    )
    await state.update_data(
        expense_history_filter_message_id=callback.message.message_id,
    )


async def expense_history_filter_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != ExpenseHistoryForm.filter.state:
        await callback.answer(
            "Этот выбор фильтра устарел. Откройте просмотр расходов заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get(
        "expense_history_filter_message_id"
    ):
        await callback.answer(
            "Используйте последний выбор фильтра.",
            show_alert=True,
        )
        return
    filter_type = callback.data.split(":", 1)[1]
    month_key = data.get("expense_history_month")
    if filter_type not in {"all", "cars"} or not month_key:
        await callback.answer("Некорректный фильтр.", show_alert=True)
        return
    if filter_type == "all":
        await callback.answer(f"Формирую историю за {month_key}")
        await callback.message.edit_reply_markup(reply_markup=None)
        await state.clear()
        await _send_expense_history(
            callback.message,
            callback.from_user.id,
            month_key,
        )
        return

    try:
        cars = list(await asyncio.to_thread(get_all_cars))
    except Exception:
        logger.exception("Could not load cars for expense history")
        await state.clear()
        await callback.answer(
            "Не удалось получить список автомобилей.",
            show_alert=True,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    await callback.answer("Выберите автомобиль")
    await state.update_data(
        expense_history_cars=cars,
        expense_history_car_message_id=callback.message.message_id,
    )
    await state.set_state(ExpenseHistoryForm.car)
    await callback.message.edit_text(
        f"Расходы за {month_key}.\nВыберите автомобиль или общие расходы:",
        reply_markup=expense_history_car_keyboard(cars),
    )


async def expense_history_car_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != ExpenseHistoryForm.car.state:
        await callback.answer(
            "Этот список автомобилей устарел. Откройте просмотр расходов заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get(
        "expense_history_car_message_id"
    ):
        await callback.answer(
            "Используйте последний список автомобилей.",
            show_alert=True,
        )
        return
    selection = callback.data.split(":", 1)[1]
    month_key = data.get("expense_history_month")
    if not month_key:
        await callback.answer("Месяц не выбран.", show_alert=True)
        return
    vehicle_filter: str | None
    filter_label: str | None
    if selection == "general":
        vehicle_filter = "general"
        filter_label = "Общие расходы"
    else:
        try:
            index = int(selection)
            car = data.get("expense_history_cars", [])[index]
        except (ValueError, IndexError):
            await callback.answer("Некорректный автомобиль.", show_alert=True)
            return
        vehicle_filter = _car_plate(car)
        if not vehicle_filter or vehicle_filter == "—":
            await callback.answer(
                "У выбранного автомобиля не указан госномер.",
                show_alert=True,
            )
            return
        filter_label = _car_report_label(car)
    await callback.answer(f"Формирую историю за {month_key}")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await _send_expense_history(
        callback.message,
        callback.from_user.id,
        month_key,
        vehicle_filter,
        filter_label,
    )


async def start_expense(message: Message, state: FSMContext) -> None:
    if not await ensure_finance_access(message):
        return
    await state.clear()
    await state.set_state(ExpenseForm.amount)
    await message.answer(
        "Введите сумму расхода в RUB:",
        reply_markup=ReplyKeyboardRemove(),
    )


async def expense_amount(message: Message, state: FSMContext) -> None:
    if not await ensure_finance_access(message):
        return
    amount, error = _parse_int(message.text or "", "сумма расхода", minimum=1)
    if error:
        await message.answer(error)
        return
    await state.update_data(expense_amount=amount)
    await state.set_state(ExpenseForm.category)
    category_message = await message.answer(
        "Выберите статью расхода:",
        reply_markup=expense_category_keyboard(),
    )
    await state.update_data(category_message_id=category_message.message_id)


async def expense_category_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != ExpenseForm.category.state:
        await callback.answer(
            "Этот выбор статьи устарел. Начните внесение расхода заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("category_message_id"):
        await callback.answer(
            "Используйте последние кнопки выбора статьи.",
            show_alert=True,
        )
        return
    try:
        category_index = int(callback.data.split(":", 1)[1])
        category = EXPENSE_CATEGORIES[category_index]
    except (ValueError, IndexError):
        await callback.answer("Некорректная статья расхода.", show_alert=True)
        return
    await callback.answer(f"Выбрано: {category}")
    await callback.message.edit_reply_markup(reply_markup=None)
    try:
        cars = list(get_all_cars())
    except Exception:
        logger.exception("Could not load cars for expense")
        await state.clear()
        await callback.message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    await state.update_data(expense_category=category, expense_cars=cars)
    await state.set_state(ExpenseForm.vehicle)
    await callback.message.answer(
        "Выберите автомобиль для расхода или общие расходы компании:",
        reply_markup=expense_vehicle_keyboard(cars),
    )


async def expense_vehicle(message: Message, state: FSMContext) -> None:
    if not await ensure_finance_access(message):
        return
    value = (message.text or "").strip()
    data = await state.get_data()
    if value in {
        "🏢 Общие расходы (весь автопарк)",
        "Общие расходы компании",
    }:
        vehicle = "Общие"
        car_name = ""
    else:
        try:
            index = int(value.split(".", 1)[0])
            car = data.get("expense_cars", [])[index - 1]
        except (ValueError, IndexError):
            await message.answer("Выберите автомобиль кнопкой из списка.")
            return
        vehicle = _value(
            car,
            "Госномер",
            "Номер авто",
            "Номер",
            "Plate",
            default="",
        )
        if not vehicle or vehicle == "—":
            await message.answer(
                "У выбранного автомобиля не указан госномер в листе Автомобили. "
                "Заполните госномер или выберите общие расходы."
            )
            return
        car_name = _car_name(car)
    await state.update_data(
        expense_vehicle=vehicle,
        expense_car_name=car_name,
    )
    await state.set_state(ExpenseForm.note)
    await message.answer(
        "Введите комментарий/примечание к расходу или отправьте «нет»:"
    )


async def expense_note(message: Message, state: FSMContext) -> None:
    if not await ensure_finance_access(message):
        return
    note = (message.text or "").strip()
    if note.lower() == "нет":
        note = ""
    await state.update_data(expense_note=note)
    data = await state.get_data()
    await state.set_state(ExpenseForm.confirmation)
    await message.answer(
        "Проверьте расход:\n\n"
        f"Сумма: {_format_number(data['expense_amount'])} RUB\n"
        f"Статья: {data['expense_category']}\n"
        f"Привязка: {data['expense_vehicle']}\n"
        f"Примечание: {note or '—'}\n\n"
        "Сохранить расход?",
        reply_markup=confirmation_keyboard(),
    )


async def expense_confirmation(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not await ensure_finance_access(message):
        return
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    user = message.from_user
    expense_date = _now_minsk().strftime("%Y-%m-%d")
    try:
        expense_id = await asyncio.to_thread(
            add_expense,
            int(data["expense_amount"]),
            data["expense_category"],
            data["expense_vehicle"],
            user.id if user else 0,
            _manager_display_name(user),
            data.get("expense_note", ""),
            expense_date,
        )
    except Exception:
        logger.exception("Could not save expense")
        await state.clear()
        await message.answer(
            "Не удалось сохранить расход в Google Sheets.",
            reply_markup=get_main_keyboard(user.id if user else 0),
        )
        return
    await state.clear()
    await message.answer(
        f"Расход №{expense_id} сохранён.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    expense_notification = (
        "💸 <b>Добавлен расход</b>\n"
        f"🧾 Номер: <b>{_html_value(expense_id)}</b>\n"
        f"🚗 Автомобиль: <b>{_html_value(data.get('expense_car_name'), '—')}</b>\n"
        f"📅 Дата: <b>{_html_value(format_date_ru(expense_date))}</b>\n"
        f"💰 Сумма: <b>{_format_number(data.get('expense_amount', 0))} ₽</b>\n"
        f"📂 Категория: <b>{_html_value(data.get('expense_category'))}</b>\n"
        f"📝 Описание: <i>{_html_value(data.get('expense_note'), '—')}</i>\n"
        f"👨‍💼 Внёс менеджер: <b>{_html_value(_manager_display_name(user))}</b>"
    )
    await send_operation_notification(
        bot,
        expense_notification,
        data.get("expense_car_name", ""),
    )


async def start_expense_cancellation(
    message: Message,
    state: FSMContext,
) -> None:
    if not await ensure_admin_plus(message):
        return
    await state.clear()
    try:
        expenses = await asyncio.to_thread(get_recent_expenses, 10)
    except Exception:
        logger.exception("Could not load recent expenses")
        await message.answer(
            "Не удалось получить список расходов.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not expenses:
        await message.answer(
            "В листе Затраты пока нет расходов для отмены.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.set_state(ExpenseCancellationForm.selection)
    selection_message = await message.answer(
        "Выберите расход, который нужно отменить:",
        reply_markup=expense_cancellation_keyboard(expenses),
    )
    await state.update_data(
        cancellation_expenses=expenses,
        cancellation_message_id=selection_message.message_id,
    )


async def expense_cancel_select_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_admin_plus_access(callback):
        await callback.answer(
            "🚫 Ошибка доступа: отменять брони и расходы может только "
            "Администратор плюс.",
            show_alert=True,
        )
        return
    if await state.get_state() != ExpenseCancellationForm.selection.state:
        await callback.answer(
            "Этот список расходов устарел. Откройте его заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("cancellation_message_id"):
        await callback.answer(
            "Используйте последний открытый список расходов.",
            show_alert=True,
        )
        return
    try:
        index = int(callback.data.split(":", 1)[1])
        expense = data.get("cancellation_expenses", [])[index]
        expense_id = str(expense.get("ID Записи", "")).strip()
        if not expense_id:
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer("Некорректный расход.", show_alert=True)
        return
    await callback.answer(f"Выбран расход #{expense_id}")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(selected_expense=expense)
    await state.set_state(ExpenseCancellationForm.confirmation)
    expense_amount = str(expense.get("Сумма (RUB)") or "0")
    await callback.message.answer(
        "Подтвердите отмену расхода:\n\n"
        f"ID: {expense_id}\n"
        f"Дата: {format_date_ru(_value(expense, 'Дата', default='—'))}\n"
        f"Сумма: {_format_number(expense_amount.replace(' ', '').replace(',', ''))} RUB\n"
        f"Статья: {_value(expense, 'Категория / На что', default='—')}\n"
        "Авто: "
        f"{_value(expense, 'Автомобиль (Госномер / Общие)', default='—')}\n\n"
        "Удалить эту строку из листа Затраты?",
        reply_markup=confirmation_keyboard(),
    )


async def expense_cancel_confirmation(
    message: Message,
    state: FSMContext,
) -> None:
    if not await ensure_admin_plus(message):
        return
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    expense = data.get("selected_expense", {})
    expense_id = str(expense.get("ID Записи", "")).strip()
    if not expense_id:
        await state.clear()
        await message.answer(
            "Не удалось определить ID расхода. Откройте список заново.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    try:
        deleted = await asyncio.to_thread(
            delete_expense,
            expense_id,
            expense,
        )
    except Exception:
        logger.exception("Could not delete expense %s", expense_id)
        await state.clear()
        await message.answer(
            "Не удалось удалить расход из Google Sheets.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.clear()
    if not deleted:
        await message.answer(
            f"Расход #{expense_id} уже удалён, изменён или не найден.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await message.answer(
        f"Расход #{expense_id} успешно отменён и удалён из Затраты.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


async def start_financial_report(message: Message, state: FSMContext) -> None:
    if not await ensure_financial_report_access(message):
        return
    await state.clear()
    try:
        months = await asyncio.to_thread(
            get_available_financial_months,
            _now_minsk().date(),
        )
    except Exception:
        logger.exception("Could not load financial report months")
        await message.answer(
            "Не удалось получить список доступных месяцев.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.set_state(FinancialReportForm.month)
    selection_message = await message.answer(
        "Выберите месяц для финансового отчёта:",
        reply_markup=financial_month_keyboard(months),
    )
    await state.update_data(
        financial_months=months,
        financial_month_message_id=selection_message.message_id,
        financial_partner_mode=is_heavy_lux_partner_user(message.from_user.id),
    )


async def financial_month_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_financial_report_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовому отчёту.",
            show_alert=True,
        )
        return
    if await state.get_state() != FinancialReportForm.month.state:
        await callback.answer(
            "Этот выбор месяца устарел. Откройте отчёт заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("financial_month_message_id"):
        await callback.answer(
            "Используйте последний список месяцев.",
            show_alert=True,
        )
        return
    month_key = callback.data.split(":", 1)[1]
    if month_key not in data.get("financial_months", []):
        await callback.answer("Недоступный месяц.", show_alert=True)
        return
    await callback.answer(
        (
            f"Формирую партнёрский отчёт за {month_key}"
            if is_heavy_lux_partner_user(callback.from_user.id)
            else f"Выбран месяц: {month_key}"
        )
    )
    await state.update_data(financial_month=month_key)
    if is_heavy_lux_partner_user(callback.from_user.id):
        await callback.message.edit_reply_markup(reply_markup=None)
        await state.clear()
        try:
            report = await asyncio.to_thread(
                get_financial_report,
                month_key,
                "heavy_lux_cr",
            )
        except Exception:
            logger.exception(
                "Could not build Heavy Lux partner report for %s",
                month_key,
            )
            await callback.message.answer(
                "Не удалось сформировать финансовый отчёт.",
                reply_markup=get_main_keyboard(callback.from_user.id),
            )
            return
        await _answer_financial_report(
            callback.message,
            _format_heavy_lux_partner_report(report),
            callback.from_user.id,
        )
        return
    await state.set_state(FinancialReportForm.report_type)
    await callback.message.edit_text(
        f"Месяц отчёта: {month_key}\n\nВыберите тип финансового отчёта:",
        reply_markup=financial_report_type_keyboard(),
    )
    await state.update_data(
        financial_type_message_id=callback.message.message_id,
    )


async def financial_report_type_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != FinancialReportForm.report_type.state:
        await callback.answer(
            "Этот выбор типа отчёта устарел. Откройте отчёт заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("financial_type_message_id"):
        await callback.answer(
            "Используйте последний выбор типа отчёта.",
            show_alert=True,
        )
        return
    report_type = callback.data.split(":", 1)[1]
    month_key = data.get("financial_month")
    if report_type not in {"leon", "heavy_lux_cr", "all"} or not month_key:
        await callback.answer("Некорректный тип отчёта.", show_alert=True)
        return

    if report_type in {"leon", "heavy_lux_cr", "all"}:
        await callback.answer(f"Формирую отчёт за {month_key}")
        await callback.message.edit_reply_markup(reply_markup=None)
        await state.clear()
        try:
            report = await asyncio.to_thread(
                get_financial_report,
                month_key,
                report_type,
            )
        except Exception:
            logger.exception("Could not build financial report for %s", month_key)
            await callback.message.answer(
                "Не удалось сформировать финансовый отчёт.",
                reply_markup=get_main_keyboard(callback.from_user.id),
            )
            return
        await _answer_financial_report(
            callback.message,
            _format_financial_report(report),
            callback.from_user.id,
        )
        return

    try:
        cars = await asyncio.to_thread(get_all_cars)
    except Exception:
        logger.exception("Could not load cars for financial report")
        await state.clear()
        await callback.answer(
            "Не удалось получить список автомобилей.",
            show_alert=True,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    if not cars:
        await state.clear()
        await callback.answer("В листе Автомобили нет автомобилей.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "В листе Автомобили нет автомобилей.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    await callback.answer("Отметьте нужные автомобили")
    await state.update_data(
        financial_cars=cars,
        selected_car_indexes=[],
        financial_car_message_id=callback.message.message_id,
    )
    await state.set_state(FinancialReportForm.cars)
    await callback.message.edit_text(
        f"Отчёт за {month_key}.\nОтметьте автомобили для расчёта:",
        reply_markup=financial_car_selection_keyboard(cars, []),
    )


async def financial_car_toggle_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != FinancialReportForm.cars.state:
        await callback.answer(
            "Этот список автомобилей устарел. Откройте отчёт заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("financial_car_message_id"):
        await callback.answer(
            "Используйте последний список автомобилей.",
            show_alert=True,
        )
        return
    try:
        index = int(callback.data.split(":", 1)[1])
        cars = data.get("financial_cars", [])
        if index < 0 or index >= len(cars):
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer("Некорректный автомобиль.", show_alert=True)
        return
    selected = set(data.get("selected_car_indexes", []))
    if index in selected:
        selected.remove(index)
        answer = "Автомобиль убран из отчёта"
    else:
        selected.add(index)
        answer = "Автомобиль добавлен в отчёт"
    selected_indexes = sorted(selected)
    await state.update_data(selected_car_indexes=selected_indexes)
    await callback.answer(answer)
    await callback.message.edit_reply_markup(
        reply_markup=financial_car_selection_keyboard(cars, selected_indexes),
    )


async def financial_car_calculate_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    if not _callback_has_finance_access(callback):
        await callback.answer(
            "У вас нет доступа к финансовым операциям.",
            show_alert=True,
        )
        return
    if await state.get_state() != FinancialReportForm.cars.state:
        await callback.answer(
            "Этот список автомобилей устарел. Откройте отчёт заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get("financial_car_message_id"):
        await callback.answer(
            "Используйте последний список автомобилей.",
            show_alert=True,
        )
        return
    selected_indexes = data.get("selected_car_indexes", [])
    month_key = data.get("financial_month")
    if not selected_indexes or not month_key:
        await callback.answer(
            "Выберите хотя бы один автомобиль.",
            show_alert=True,
        )
        return
    cars = data.get("financial_cars", [])
    selected_car_keys = {
        _financial_car_key(cars[index])
        for index in selected_indexes
        if 0 <= index < len(cars)
    }
    if not selected_car_keys:
        await callback.answer(
            "Выбранные автомобили больше недоступны.",
            show_alert=True,
        )
        return
    await callback.answer(f"Формирую отчёт за {month_key}")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    try:
        report = await asyncio.to_thread(get_financial_report, month_key)
    except Exception:
        logger.exception("Could not build car financial report for %s", month_key)
        await callback.message.answer(
            "Не удалось сформировать финансовый отчёт.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    await _answer_financial_report(
        callback.message,
        _format_selected_cars_report(report, selected_car_keys),
        callback.from_user.id,
    )


async def start_availability(message: Message, state: FSMContext) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    await state.update_data(
        partner_mode=(
            bool(message.from_user)
            and is_heavy_lux_partner_user(message.from_user.id)
        )
    )
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
        data = await state.get_data()
    except Exception:
        logger.exception("Could not load available cars")
        await state.clear()
        await message.answer(
            "Не удалось получить список авто из Google Sheets.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await state.clear()
        await message.answer(
            "На выбранный период свободных авто нет.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.clear()
    if data.get("partner_mode"):
        lines = [
            f"{index}. {_car_name(car)} ({_car_plate(car) or 'Без номера'})"
            for index, car in enumerate(cars, start=1)
        ]
    else:
        lines = [
            f"{index}. {_car_label(car)}"
            for index, car in enumerate(cars, start=1)
        ]
    await message.answer(
        "Свободные автомобили:\n\n" + "\n".join(lines),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


async def _start_booking_flow(
    message: Message,
    state: FSMContext,
    mode: str,
    title: str,
) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    await state.update_data(
        booking_mode=mode,
        partner_mode=(
            bool(message.from_user)
            and is_heavy_lux_partner_user(message.from_user.id)
        ),
    )
    if bool(message.from_user) and is_heavy_lux_partner_user(message.from_user.id):
        await state.update_data(source=HEAVY_LUX_PARTNER_SOURCE)
        await _show_booking_start_prompt(message, state, title)
        return
    await state.set_state(BookingForm.source)
    await message.answer(
        SOURCE_SELECTION_PROMPT,
        reply_markup=source_selection_keyboard(),
    )


async def _show_booking_start_prompt(
    message: Message,
    state: FSMContext,
    title: str,
) -> None:
    await state.set_state(BookingForm.start_date)
    calendar_message = await message.answer(
        f"{title}.\n\n" + _calendar_prompt("booking", "start"),
        reply_markup=calendar_keyboard("booking", "start"),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def booking_source_selection(
    message: Message,
    state: FSMContext,
) -> None:
    if (message.text or "").strip() == "Отмена":
        await cancel_flow(message, state)
        return
    source = _source_from_choice(message.text or "")
    if not source:
        await message.answer(
            SOURCE_SELECTION_PROMPT,
            reply_markup=source_selection_keyboard(),
        )
        return
    await state.update_data(source=source, partner_mode=False)
    data = await state.get_data()
    if data.get("source_flow") == "direct_issuance":
        await _start_direct_issuance(message, state)
        return
    title = (
        "Новая бронь"
        if data.get("booking_mode") == "reservation"
        else "Выдача авто"
    )
    await _show_booking_start_prompt(message, state, title)


async def start_booking(message: Message, state: FSMContext) -> None:
    await _start_booking_flow(message, state, "reservation", "Новая бронь")


async def start_issuance(message: Message, state: FSMContext) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    await state.update_data(
        partner_mode=(
            bool(message.from_user)
            and is_heavy_lux_partner_user(message.from_user.id)
        )
    )
    await state.set_state(BookingForm.issuance_mode)
    await message.answer(
        "🚗 Выдача авто.\n\nВыберите способ оформления:",
        reply_markup=issuance_mode_keyboard(),
    )


async def issuance_mode_selection(message: Message, state: FSMContext) -> None:
    choice = (message.text or "").strip()
    if choice == "⚡ Срочная выдача (без брони)":
        data = await state.get_data()
        if data.get("partner_mode"):
            await state.update_data(source=HEAVY_LUX_PARTNER_SOURCE)
            await _start_direct_issuance(message, state)
            return
        await state.set_state(BookingForm.source)
        await state.update_data(source_flow="direct_issuance")
        await message.answer(
            SOURCE_SELECTION_PROMPT,
            reply_markup=source_selection_keyboard(),
        )
        return
    if choice == "📋 По существующей брони":
        await _start_existing_issuance(message, state)
        return
    await message.answer("Выберите один из вариантов кнопкой ниже.")


async def _start_direct_issuance(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        cars = get_available_cars_for_direct_issuance()
    except Exception:
        logger.exception("Could not load cars for direct issuance")
        await state.clear()
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await state.clear()
        await message.answer(
            "В листе Автомобили нет автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.update_data(
        booking_mode="direct_issuance",
        cars=cars,
        direct_issuance_cars=cars,
    )
    await state.set_state(BookingForm.car)
    await message.answer(
        "⚡ Срочная выдача без брони.\n\n"
        "Выберите автомобиль:",
        reply_markup=direct_issuance_car_keyboard(cars),
    )


async def issuance_source_selection(
    message: Message,
    state: FSMContext,
) -> None:
    source = _source_from_choice(message.text or "")
    if not source:
        await message.answer(
            SOURCE_SELECTION_PROMPT,
            reply_markup=source_selection_keyboard(),
        )
        return
    await state.update_data(source=source, partner_mode=False)
    await _start_direct_issuance(message, state)


async def _start_existing_issuance(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        bookings = get_confirmed_bookings()
    except Exception:
        logger.exception("Could not load confirmed bookings for issuance")
        await message.answer(
            "Не удалось получить подтверждённые брони.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not bookings:
        await message.answer(
            "Нет подтверждённых броней для выдачи. Сначала создайте новую бронь.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.update_data(booking_mode="issuance", issuance_bookings=bookings)
    await state.set_state(BookingForm.issuance_booking)
    await message.answer(
        "🚗 Выдача авто.\n\nВыберите подтверждённую бронь:",
        reply_markup=rental_choice_keyboard(bookings),
    )


async def _prepare_issuance_booking(
    message: Message,
    state: FSMContext,
    booking: dict,
) -> bool:
    try:
        car = get_car_by_id(_car_id(booking))
    except Exception:
        logger.exception("Could not load car for issuance")
        await state.clear()
        await message.answer(
            "Не удалось получить данные автомобиля.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return False
    if not car:
        await state.clear()
        await message.answer(
            "Автомобиль из брони не найден в листе Автомобили.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return False
    prepayment, _ = _parse_int(
        _value(
            booking,
            "Задаток/Предоплата",
            "Залог/Предоплата",
            "Предоплата",
            default="0",
        ),
        "задаток",
        minimum=0,
    )
    await state.update_data(
        booking=booking,
        car=car,
        car_id=_car_id(car),
        car_name=_car_name(car),
        start_date=_value(
            booking,
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
        ),
        end_date=_value(
            booking,
            "Дата окончания (ГГГГ-ММ-ДД)",
            "Дата окончания",
        ),
        client_name=_value(
            booking,
            "ФИО Клиента",
            "Клиент",
        ),
        phone=_value(booking, "Телефон", "Телефон клиента"),
        price=_value(
            booking,
            "Принятая сумма аренды (RUB)",
            "Фактически оплачено за аренду (RUB)",
            "Сумма брони (RUB)",
            "Цена",
            default="0",
        ),
        pay_type=_value(
            booking,
            "Способ оплаты аренды",
            "Способ оплаты",
            default="Без оплаты",
        ),
        prepayment=prepayment or 0,
        prepayment_pay_type=_value(
            booking,
            "Способ оплаты задатка",
            "Тип оплаты залога",
            default="Без оплаты",
        ),
        original_start_date=_value(
            booking,
            "Дата начала (ГГГГ-ММ-ДД)",
            "Дата начала",
        ),
        original_end_date=_value(
            booking,
            "Дата окончания (ГГГГ-ММ-ДД)",
            "Дата окончания",
        ),
    )
    await state.set_state(BookingForm.issue_date)
    await message.answer(
        f"Бронь №{_booking_id(booking)} выбрана.\n"
        f"Автомобиль: {_car_name(car)} ({_car_plate(car)})\n"
        f"Задаток: {prepayment or 0:,} RUB.\n\n"
        "Укажите фактическую дату выдачи:",
        reply_markup=issuance_date_keyboard(),
    )
    return True


async def issuance_booking(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    bookings = data.get("issuance_bookings", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        booking = bookings[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите подтверждённую бронь кнопкой из списка.")
        return
    await _prepare_issuance_booking(message, state, booking)


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
            selected_start_date=selected_date,
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
    await message.answer(_period_selection_text(data["start_date"], value))
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
        await message.answer(
            "Не удалось получить список авто из Google Sheets.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await state.clear()
        await message.answer(
            "На выбранный период свободных авто нет.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.update_data(end_date=end_date, cars=cars)
    await state.set_state(BookingForm.car)
    await message.answer("Выберите автомобиль:", reply_markup=car_choice_keyboard(cars))


async def start_occupancy(message: Message, state: FSMContext) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    try:
        # Occupancy inspection must include every car, even currently rented
        # or reserved ones, so managers can inspect their occupied dates.
        cars = get_all_cars()
    except Exception:
        logger.exception("Could not load cars for occupancy calendar")
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await message.answer(
            "В листе Автомобили нет автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.update_data(
        cars=cars,
        partner_mode=(
            bool(message.from_user)
            and is_heavy_lux_partner_user(message.from_user.id)
        ),
    )
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
            reply_markup=get_main_keyboard(message.from_user.id),
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
        "🔴 — дата занята. Нажмите на неё, чтобы увидеть детали брони.\n"
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
    if not await ensure_admin_plus(message):
        return
    await state.clear()
    try:
        cars = get_cancellable_cars()
    except Exception:
        logger.exception("Could not load cars for cancellation")
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await message.answer(
            "В листе Бронирования нет подтверждённых броней для отмены.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.update_data(cars=cars)
    await state.set_state(CancellationForm.car)
    await message.answer(
        "🚫 Отмена брони.\n\nВыберите автомобиль:",
        reply_markup=cancellation_car_keyboard(cars),
    )


async def cancellation_car(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_plus(message):
        return
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
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not bookings:
        await state.clear()
        await message.answer(
            f"Для автомобиля {_car_name(car)} ({_car_plate(car)}) активных броней нет.",
            reply_markup=get_main_keyboard(message.from_user.id),
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
    if not _callback_has_admin_plus_access(callback):
        await callback.answer(
            "🚫 Ошибка доступа: отменять брони и расходы может только "
            "Администратор плюс.",
            show_alert=True,
        )
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
    await state.set_state(CancellationForm.confirmation)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    confirmation_message = await callback.message.answer(
        f"Вы действительно хотите отменить бронь #{_booking_id(booking)} "
        f"({_car_name(data.get('selected_car', {}))}, "
        f"{_value(booking, 'ФИО Клиента', 'Клиент', 'Клиент ФИО', default='—')})?",
        reply_markup=cancellation_confirmation_keyboard(),
    )
    await state.update_data(
        cancellation_confirmation_message_id=confirmation_message.message_id
    )


async def cancellation_confirmation_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if not _callback_has_admin_plus_access(callback):
        await callback.answer(
            "🚫 Ошибка доступа: отменять брони и расходы может только "
            "Администратор плюс.",
            show_alert=True,
        )
        return
    if await state.get_state() != CancellationForm.confirmation.state:
        await callback.answer(
            "Этот выбор устарел. Начните отмену заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if callback.message.message_id != data.get(
        "cancellation_confirmation_message_id"
    ):
        await callback.answer(
            "Используйте последние кнопки подтверждения.",
            show_alert=True,
        )
        return
    booking = data.get("selected_booking")
    if not booking:
        await callback.answer(
            "Операция устарела. Начните отмену заново.",
            show_alert=True,
        )
        return
    if callback.data == "cancel_booking_back":
        bookings = data.get("cancel_bookings", [])
        await state.set_state(CancellationForm.booking)
        await callback.answer()
        await callback.message.edit_text(
            "Выберите бронь для отмены:",
            reply_markup=cancellation_booking_keyboard(bookings),
        )
        await state.update_data(
            cancellation_booking_message_id=callback.message.message_id
        )
        return
    if callback.data != "cancel_booking_confirm":
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    await callback.answer("Бронь выбрана для отмены")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(CancellationForm.reason)
    await callback.message.answer(
        f"Введите причину отмены брони №{_booking_id(booking)}:"
    )


async def cancellation_reason(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_plus(message):
        return
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
    if not _callback_has_admin_plus_access(callback):
        await callback.answer(
            "🚫 Ошибка доступа: отменять брони и расходы может только "
            "Администратор плюс.",
            show_alert=True,
        )
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
            reply_markup=get_main_keyboard(callback.from_user.id),
        )
        return
    await state.clear()
    await callback.answer("Бронь отменена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Бронь №{_booking_id(booking)} отменена.\n"
        f"Автомобиль: {_car_name(car)} ({_car_plate(car)})\n"
        f"Статус задатка: {deposit_status}.\n"
        "Даты автомобиля освобождены.",
        reply_markup=get_main_keyboard(callback.from_user.id),
    )


def _calendar_minimum_date(
    flow: str,
    stage: str,
    data: dict,
) -> datetime.date | None:
    if flow == "occupancy":
        return None
    if flow == "issuance" and stage == "issue":
        return None
    if stage == "end" and data.get("start_date"):
        return datetime.date.fromisoformat(data["start_date"])
    return datetime.date.today()


def _calendar_maximum_date(
    flow: str,
    stage: str,
) -> datetime.date | None:
    if flow == "issuance" and stage == "issue":
        return _now_moscow().date()
    return None


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
        ("client_booking", "start"): ClientBookingState.start_date.state,
        ("client_booking", "end"): ClientBookingState.end_date.state,
        ("occupancy", "calendar"): OccupancyForm.calendar.state,
        ("issuance", "issue"): BookingForm.issue_date.state,
        ("issuance", "end"): BookingForm.direct_end_date.state,
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
    maximum_date = _calendar_maximum_date(flow, stage)
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
                maximum_date=maximum_date,
                year=year,
                month=month,
                allow_past=flow == "occupancy",
                occupied_dates=(
                    _occupied_dates(data.get("occupancy_bookings", []))
                    if flow == "occupancy"
                    else data.get("client_occupied_dates", {})
                    if flow == "client_booking"
                    else None
                ),
                selected_start_date=(
                    datetime.date.fromisoformat(data["start_date"])
                    if stage == "end" and data.get("start_date")
                    else None
                ),
            )
        )
        return

    if len(parts) == 5 and parts[3] == "busy":
        if flow == "client_booking":
            await callback.answer(
                "⚠️ На эту дату автомобиль уже забронирован. "
                "Пожалуйста, выберите другой день",
                show_alert=True,
            )
            return
        try:
            selected_date = datetime.date.fromisoformat(parts[4])
        except ValueError:
            await callback.answer("Некорректная дата.", show_alert=True)
            return
        occupied = _occupied_dates(data.get("occupancy_bookings", []))
        booking_ids = occupied.get(selected_date.isoformat(), [])
        if booking_ids:
            bookings_by_id = {
                _booking_id(booking): booking
                for booking in data.get("occupancy_bookings", [])
            }
            if data.get("partner_mode"):
                details = [
                    _partner_booking_card_text(
                        bookings_by_id[booking_id],
                        data.get("selected_car", {}),
                        source=HEAVY_LUX_PARTNER_SOURCE,
                    )
                    for booking_id in booking_ids
                    if booking_id in bookings_by_id
                ]
            else:
                details = [
                    _booking_card_text(bookings_by_id[booking_id])
                    for booking_id in booking_ids
                    if booking_id in bookings_by_id
                ]
            if details:
                await callback.answer("Детали брони отправлены ниже.")
                await message.answer(
                    "🔴 ЗАНЯТАЯ ДАТА\n\n" + "\n\n".join(details)
                )
            else:
                await callback.answer(
                    "Бронь для этой даты не найдена.",
                    show_alert=True,
                )
        else:
            await callback.answer(
                "Эта дата больше не занята.",
                show_alert=True,
            )
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
    if maximum_date and selected_date > maximum_date:
        await callback.answer("Будущая дата недоступна для выбора.", show_alert=True)
        return

    selected_value = selected_date.isoformat()
    if flow == "client_booking":
        occupied_dates = data.get("client_occupied_dates", {}) or {}
        if selected_value in occupied_dates:
            await callback.answer(
                "⚠️ На эту дату автомобиль уже забронирован. "
                "Пожалуйста, выберите другой день",
                show_alert=True,
            )
            return
        if stage == "end" and data.get("start_date"):
            if _client_period_has_occupied_dates(
                data["start_date"],
                selected_value,
                occupied_dates,
            ):
                await callback.answer(
                    "В выбранный период попадают уже занятые даты. "
                    "Выберите другой период",
                    show_alert=True,
                )
                return
    if flow == "occupancy" and stage == "calendar":
        await callback.answer(
            f"Свободная дата: {format_date_ru(selected_value)}"
        )
        return
    await callback.answer()

    if flow == "issuance" and stage == "issue":
        await state.update_data(issue_date=selected_value)
        await state.set_state(BookingForm.issue_time)
        await message.edit_text(
            f"Дата выдачи: {format_date_ru(selected_value)}.\n"
            "Введите время выдачи в формате ЧЧ:ММ:"
        )
        return

    if flow == "issuance" and stage == "end":
        await message.edit_text(
            f"Период выбран: {format_date_ru(data['start_date'])} — "
            f"{format_date_ru(selected_value)}"
        )
        await _complete_direct_end_date(message, state, selected_value)
        return

    if flow == "availability" and stage == "start":
        await state.update_data(start_date=selected_value)
        await state.set_state(AvailabilityForm.end_date)
        await message.edit_text(
            _calendar_prompt("availability", "end", selected_date),
            reply_markup=calendar_keyboard(
                "availability",
                "end",
                minimum_date=selected_date,
                selected_start_date=selected_date,
            ),
        )
        return

    if flow == "availability" and stage == "end":
        await message.edit_text(
            f"Период выбран: {format_date_ru(data['start_date'])} — "
            f"{format_date_ru(selected_value)}"
        )
        await _finish_availability(message, state, data["start_date"], selected_value)
        return

    if flow == "client_booking" and stage == "start":
        await state.update_data(start_date=selected_value)
        await state.set_state(ClientBookingState.end_date)
        await message.edit_text(
            _calendar_prompt("client_booking", "end", selected_date),
            reply_markup=calendar_keyboard(
                "client_booking",
                "end",
                minimum_date=selected_date,
                selected_start_date=selected_date,
                occupied_dates=data.get("client_occupied_dates", {}),
            ),
        )
        return

    if flow == "client_booking" and stage == "end":
        await message.edit_text(
            f"Период выбран: {format_date_ru(data['start_date'])} — "
            f"{format_date_ru(selected_value)}"
        )
        await _finish_client_booking_dates(
            message,
            state,
            data["start_date"],
            selected_value,
        )
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
                selected_start_date=selected_date,
            ),
        )
        return

    if flow == "booking" and stage == "end":
        start_date = datetime.date.fromisoformat(data["start_date"])
        await message.edit_text(
            _period_selection_text(data["start_date"], selected_value),
            reply_markup=calendar_keyboard(
                "booking",
                "end",
                minimum_date=start_date,
                year=selected_date.year,
                month=selected_date.month,
                selected_start_date=start_date,
                selected_end_date=selected_date,
                locked=True,
            ),
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
    if (
        data.get("booking_mode") == "direct_issuance"
        and _value(car, "Статус", default="").strip().casefold()
        in {"в аренде", "rented"}
    ):
        await message.answer(
            "⛔ Этот автомобиль уже находится в аренде. "
            "Сначала оформите приём и закройте текущую аренду.",
        )
        return
    await state.update_data(
        car_id=_car_id(car),
        car_name=_car_name(car),
        car=car,
    )
    await state.set_state(BookingForm.client_name)
    await message.answer("Введите ФИО клиента:")


def _parse_issue_time(value: str) -> datetime.time | None:
    try:
        return datetime.datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


def _issue_planned_end(
    issued_at: datetime.datetime,
    original_start_date: str,
    original_end_date: str,
) -> datetime.datetime:
    start_date = datetime.date.fromisoformat(original_start_date)
    end_date = datetime.date.fromisoformat(original_end_date)
    duration_days = max(0, (end_date - start_date).days)
    return issued_at + datetime.timedelta(days=duration_days)


async def _complete_issue_datetime(
    message: Message,
    state: FSMContext,
    issued_at: datetime.datetime,
) -> None:
    now = _now_moscow()
    if issued_at > now:
        await message.answer(
            "Дата и время выдачи не могут быть в будущем. "
            "Введите время ещё раз:"
        )
        return
    data = await state.get_data()
    issued_at = issued_at.replace(second=0, microsecond=0)
    issued_at_text = _timestamp(issued_at)
    if data.get("booking_mode") == "issuance":
        try:
            planned_end = _issue_planned_end(
                issued_at,
                data["original_start_date"],
                data["original_end_date"],
            )
        except (KeyError, ValueError):
            await state.clear()
            await message.answer(
                "Не удалось определить длительность брони. "
                "Начните выдачу заново.",
                reply_markup=get_main_keyboard(message.from_user.id),
            )
            return
        await state.update_data(
            issued_at=issued_at_text,
            issue_date=issued_at.date().isoformat(),
            issue_time=issued_at.strftime("%H:%M"),
            planned_end_at=_timestamp(planned_end),
            return_time=planned_end.strftime("%H:%M"),
            start_date=issued_at.date().isoformat(),
            end_date=planned_end.date().isoformat(),
            backdated=issued_at < now,
        )
        await state.set_state(BookingForm.start_mileage)
        backdated_text = (
            "\n⚠️ Оформлено задним числом."
            if issued_at < now
            else ""
        )
        await message.answer(
            "Фактическая выдача: "
            f"{_format_datetime_ru(issued_at_text)}{backdated_text}\n"
            f"Плановый возврат: {_format_datetime_ru(_timestamp(planned_end))}\n\n"
            "Введите начальный пробег автомобиля в км:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.update_data(
        issued_at=issued_at_text,
        issue_date=issued_at.date().isoformat(),
        issue_time=issued_at.strftime("%H:%M"),
        start_date=issued_at.date().isoformat(),
        backdated=issued_at < now,
    )
    await state.set_state(BookingForm.direct_end_date)
    calendar_message = await message.answer(
        "Фактическая выдача: "
        f"{_format_datetime_ru(issued_at_text)}"
        f"{' (Оформлено задним числом)' if issued_at < now else ''}\n\n"
        + _calendar_prompt(
            "issuance",
            "end",
            issued_at.date(),
        ),
        reply_markup=calendar_keyboard(
            "issuance",
            "end",
            minimum_date=issued_at.date(),
            selected_start_date=issued_at.date(),
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def issuance_date_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or callback.data not in {
        "issue_date_now",
        "issue_date_calendar",
    }:
        await callback.answer()
        return
    if await state.get_state() != BookingForm.issue_date.state:
        await callback.answer(
            "Этот выбор даты устарел. Начните выдачу заново.",
            show_alert=True,
        )
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if callback.data == "issue_date_now":
        await _complete_issue_datetime(callback.message, state, _now_moscow())
        return
    calendar_message = await callback.message.answer(
        _calendar_prompt("issuance", "issue"),
        reply_markup=calendar_keyboard(
            "issuance",
            "issue",
            maximum_date=_now_moscow().date(),
            allow_past=True,
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def issue_time(message: Message, state: FSMContext) -> None:
    selected_time = _parse_issue_time(message.text or "")
    if selected_time is None:
        await message.answer("Неверный формат. Пример: 14:30.")
        return
    data = await state.get_data()
    try:
        issue_date = datetime.date.fromisoformat(data["issue_date"])
    except (KeyError, ValueError):
        await message.answer("Дата выдачи потеряна. Начните выдачу заново.")
        await state.clear()
        return
    await _complete_issue_datetime(
        message,
        state,
        datetime.datetime.combine(issue_date, selected_time, MOSCOW_TZ),
    )


async def direct_issuance_car_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    await callback.answer(
        "Принудительная выдача автомобиля, который уже в аренде, запрещена.",
        show_alert=True,
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()


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
    data = await state.get_data()
    if data.get("booking_mode") == "direct_issuance":
        await state.set_state(BookingForm.issue_date)
        await message.answer(
            "Укажите фактическую дату выдачи:",
            reply_markup=issuance_date_keyboard(),
        )
        return
    if data.get("booking_mode") == "reservation":
        if data.get("partner_mode"):
            auto_price = _partner_auto_booking_price(data)
            await state.update_data(
                delivery_address="Самовывоз",
                delivery_price=0,
                prepayment=0,
                prepayment_pay_type="Без оплаты",
            )
            if auto_price is not None:
                await state.update_data(price=auto_price)
                await _send_reservation_confirmation(message, state)
                return
            await _stop_partner_flow_without_tariff(message, state)
            return
        await state.set_state(BookingForm.delivery)
        await message.answer(
            "Нужна ли доставка автомобиля?",
            reply_markup=delivery_choice_keyboard(),
        )
        return
    await state.set_state(BookingForm.price)
    await message.answer("Введите принятую сумму аренды в RUB:")


async def booking_delivery_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.message or callback.data not in {"delivery_yes", "delivery_no"}:
        await callback.answer()
        return
    if await state.get_state() != BookingForm.delivery.state:
        await callback.answer(
            "Этот выбор устарел. Продолжите оформление брони заново.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if data.get("booking_mode") != "reservation":
        await callback.answer(
            "Доставка доступна только при создании новой брони.",
            show_alert=True,
        )
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if callback.data == "delivery_yes":
        await state.set_state(BookingForm.delivery_address)
        await callback.message.answer("Введите адрес доставки автомобиля:")
        return
    await state.update_data(
        delivery_address="Самовывоз",
        delivery_price=0,
    )
    await state.set_state(BookingForm.price)
    await callback.message.answer("Введите итоговую стоимость аренды в RUB:")


async def booking_delivery_address(
    message: Message,
    state: FSMContext,
) -> None:
    address = (message.text or "").strip()
    if not address:
        await message.answer("Введите адрес доставки текстом.")
        return
    await state.update_data(delivery_address=address)
    await state.set_state(BookingForm.delivery_price)
    await message.answer(
        "Введите стоимость доставки в RUB (целое число, можно 0):"
    )


async def booking_delivery_price(
    message: Message,
    state: FSMContext,
) -> None:
    delivery_price, error = _parse_int(
        message.text or "",
        "стоимость доставки",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(delivery_price=delivery_price)
    await state.set_state(BookingForm.price)
    await message.answer("Введите итоговую стоимость аренды в RUB:")


async def _complete_direct_end_date(
    message: Message,
    state: FSMContext,
    value: str,
) -> None:
    data = await state.get_data()
    start_date = datetime.date.fromisoformat(data["start_date"])
    selected_date = datetime.date.fromisoformat(value)
    if selected_date < start_date:
        await message.answer("Дата окончания не может быть раньше даты выдачи.")
        return
    await state.update_data(
        end_date=value,
        planned_end_at=f"{value} {data.get('issue_time', '23:59')}",
        return_time=data.get("issue_time", "23:59"),
    )
    data = await state.get_data()
    if data.get("partner_mode"):
        auto_price = _partner_auto_booking_price(data)
        if auto_price is None:
            await _stop_partner_flow_without_tariff(message, state)
            return
        await state.update_data(
            price=auto_price,
            pay_type="Без оплаты",
            deposit=0,
            deposit_pay_type="Без оплаты",
        )
        await state.set_state(BookingForm.start_mileage)
        await message.answer("Введите пробег автомобиля на момент выдачи в км:")
        return
    await state.set_state(BookingForm.price)
    await message.answer("Введите фактическую сумму аренды в RUB:")


async def direct_end_date(message: Message, state: FSMContext) -> None:
    """Keep typed input from breaking the flow, but require calendar selection."""
    data = await state.get_data()
    try:
        start_date = datetime.date.fromisoformat(data["start_date"])
    except (KeyError, TypeError, ValueError):
        await message.answer("Дата выдачи потеряна. Начните выдачу заново.")
        await state.clear()
        return
    calendar_message = await message.answer(
        _calendar_prompt("issuance", "end", start_date),
        reply_markup=calendar_keyboard(
            "issuance",
            "end",
            minimum_date=start_date,
            selected_start_date=start_date,
        ),
    )
    await state.update_data(calendar_message_id=calendar_message.message_id)


async def booking_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("partner_mode"):
        auto_price = _partner_auto_booking_price(data)
        if auto_price is None:
            await _stop_partner_flow_without_tariff(message, state)
            return
        await state.update_data(
            price=auto_price,
            delivery_address="Самовывоз",
            delivery_price=0,
            prepayment=0,
            prepayment_pay_type="Без оплаты",
            pay_type="Без оплаты",
            deposit=0,
            deposit_pay_type="Без оплаты",
        )
        if data.get("booking_mode") == "reservation":
            await _send_reservation_confirmation(message, state)
        elif data.get("booking_mode") == "direct_issuance":
            await state.set_state(BookingForm.start_mileage)
            await message.answer("Введите пробег автомобиля на момент выдачи в км:")
        else:
            await _stop_partner_flow_without_tariff(message, state)
        return
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
            "Введите задаток (предоплату за бронь) в RUB "
            "(0, если без задатка):"
        )
        return
    if data.get("booking_mode") == "direct_issuance":
        await state.set_state(BookingForm.pay_type)
        payment_message = await message.answer(
            "Выберите способ оплаты аренды:",
            reply_markup=payment_methods_keyboard("direct_rental"),
        )
        await state.update_data(payment_message_id=payment_message.message_id)
        return
    await state.set_state(BookingForm.start_mileage)
    await message.answer("Введите начальный пробег автомобиля в км:")


async def booking_prepayment(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("partner_mode"):
        await state.update_data(
            prepayment=0,
            prepayment_pay_type="Без оплаты",
        )
        await _send_reservation_confirmation(message, state)
        return
    prepayment, error = _parse_int(
        message.text or "",
        "задаток/предоплата за бронь",
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
        "Выберите способ оплаты задатка (предоплаты за бронь):",
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
    data = await state.get_data()
    if data.get("partner_mode"):
        await state.update_data(
            deposit=0,
            deposit_pay_type="Без оплаты",
            prepayment=0,
            prepayment_pay_type="Без оплаты",
            pay_type="Без оплаты",
        )
        await _send_booking_confirmation(message, state)
        return
    await state.set_state(BookingForm.deposit)
    data = await state.get_data()
    base_deposit = _car_base_deposit(data.get("car", {}))
    hint = (
        f"Базовый залог из листа Автомобили: {base_deposit:,} RUB.\n".replace(",", " ")
        if base_deposit is not None
        else "Базовый залог в листе Автомобили не указан.\n"
    )
    await message.answer(
        hint
        + "Обязательно введите страховой залог (депозит) в RUB — "
        "гарантию за автомобиль (0, если депозит не внесён):"
    )


async def booking_deposit(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("partner_mode"):
        await _stop_partner_flow_without_tariff(message, state)
        return
    deposit, error = _parse_int(
        message.text or "",
        "залог",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(deposit=deposit)
    if deposit == 0:
        await state.update_data(deposit_pay_type="Без оплаты")
        await _send_booking_confirmation(message, state)
        return
    await state.set_state(BookingForm.deposit_pay_type)
    payment_message = await message.answer(
        "Выберите способ оплаты страхового залога:",
        reply_markup=payment_methods_keyboard("issuance_deposit"),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def _send_booking_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    issued_at = data.get("issued_at") or _timestamp()
    planned_end_at = data.get("planned_end_at", "")
    await state.set_state(BookingForm.confirmation)
    if data.get("partner_mode"):
        await message.answer(
            _partner_issuance_card_text(data),
            reply_markup=confirmation_keyboard(),
        )
        return
    backdated_text = (
        "Фактическая выдача: "
        f"{_format_datetime_ru(issued_at)} (Оформлено задним числом)\n"
        if data.get("backdated")
        else ""
    )
    direct_payment_text = ""
    if data.get("booking_mode") == "direct_issuance":
        direct_payment_text = (
            f"Сумма аренды: {_format_number(data.get('price', 0))} RUB\n"
            f"Способ оплаты аренды: {data.get('pay_type', 'Без оплаты')}\n"
        )
    await message.answer(
        "Проверьте акт выдачи авто:\n\n"
        f"Авто: {data['car_name']}\n"
        f"Период: {_issue_datetime_text(issued_at, planned_end_at)}\n"
        f"{backdated_text}"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"{direct_payment_text}"
        f"Задаток (предоплата): {int(data.get('prepayment', 0)):,} RUB\n"
        f"Начальный пробег: {int(data['start_mileage']):,} км\n"
        f"Страховой залог: {int(data['deposit']):,} RUB\n"
        f"Оплата страхового залога: {data['deposit_pay_type']}\n"
        f"Дата и время выдачи: {_format_datetime_ru(issued_at)}".replace(",", " ")
        + "\n\nСохранить выдачу авто?",
        reply_markup=confirmation_keyboard(),
    )


async def _send_reservation_confirmation(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    rent_price = int(data.get("price", 0))
    delivery_price = int(data.get("delivery_price", 0))
    total_price = rent_price + delivery_price
    await state.set_state(BookingForm.confirmation)
    if data.get("partner_mode"):
        await message.answer(
            "\n".join(
                [
                    "Проверьте новую бронь:",
                    "",
                    f"Источник: {HEAVY_LUX_PARTNER_SOURCE}",
                    "Автомобиль: "
                    f"{data.get('car_name', '—')} "
                    f"({_car_plate(data.get('car', {})) or '—'})",
                    f"Период: {format_date_ru(data['start_date'])} — "
                    f"{format_date_ru(data['end_date'])}",
                    f"Клиент: {data.get('client_name', '—')}",
                    f"Телефон: {data.get('phone', '—')}",
                    "",
                    "Сохранить бронь?",
                ]
            ),
            reply_markup=confirmation_keyboard(),
        )
        return
    await message.answer(
        "Проверьте новую бронь:\n\n"
        f"Авто: {data['car_name']}\n"
        f"Период: {format_date_ru(data['start_date'])} — "
        f"{format_date_ru(data['end_date'])}\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Стоимость аренды: {rent_price:,} RUB\n"
        f"📍 Доставка: {data.get('delivery_address', 'Самовывоз')}\n"
        f"🚚 Стоимость доставки: {delivery_price:,} RUB\n"
        f"Итого: {total_price:,} RUB\n"
        f"Задаток (предоплата за бронь): {int(data['prepayment']):,} RUB\n"
        f"Способ оплаты задатка: {data['prepayment_pay_type']}\n\n"
        "Сохранить бронь?".replace(",", " "),
        reply_markup=confirmation_keyboard(),
    )


async def booking_confirmation(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    manager_id = message.from_user.id if message.from_user else 0
    manager_name = _manager_display_name(message.from_user)
    is_reservation = data.get("booking_mode") == "reservation"
    is_direct_issuance = data.get("booking_mode") == "direct_issuance"
    try:
        if is_reservation:
            booking_id = add_new_booking(
                car_id=int(data["car_id"]),
                car_name=data["car_name"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                client_name=data["client_name"],
                phone=data["phone"],
                price=int(data["price"]),
                delivery_address=data.get("delivery_address", "Самовывоз"),
                delivery_price=int(data.get("delivery_price", 0)),
                manager_id=manager_id,
                status="Подтверждена",
                prepayment=int(data.get("prepayment", 0)),
                prepayment_pay_type=data.get(
                    "prepayment_pay_type",
                    "Без оплаты",
                ),
                manager_name=manager_name,
                source=_operation_source(data),
            )
        elif is_direct_issuance:
            booking_id = save_direct_issuance(
                car_id=int(data["car_id"]),
                car_name=data["car_name"],
                plate=_car_plate(data.get("car", {})),
                client_name=data["client_name"],
                phone=data["phone"],
                end_date=data["end_date"],
                price=int(data["price"]),
                pay_type=data.get("pay_type", "Без оплаты"),
                start_mileage=int(data["start_mileage"]),
                deposit=int(data["deposit"]),
                deposit_pay_type=data.get(
                    "deposit_pay_type",
                    "Без оплаты",
                ),
                manager_id=manager_id,
                manager_name=manager_name,
                issued_at=data.get("issued_at", _timestamp()),
                planned_end_at=data.get("planned_end_at", ""),
                source=_operation_source(data),
            )
        else:
            booking_id = int(_booking_id(data["booking"]))
            issue_booking(
                booking_id=booking_id,
                start_mileage=int(data["start_mileage"]),
                insurance_deposit=int(data["deposit"]),
                insurance_deposit_pay_type=data["deposit_pay_type"],
                manager_id=manager_id,
                issued_at=data.get("issued_at", _timestamp()),
                planned_end_at=data.get("planned_end_at", ""),
            )
    except Exception as e:
        import html
        import traceback

        err_text = traceback.format_exc()
        print("Ошибка сохранения брони:", err_text)
        await state.clear()
        await message.answer(
            "❌ Ошибка записи в таблицу:\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await state.clear()
    await message.answer(
        (
            f"Бронь №{booking_id} сохранена."
            if is_reservation
            else (
                f"Срочная выдача №{booking_id} сохранена. "
                "Автомобиль отмечен как выданный."
            )
            if is_direct_issuance
            else f"Акт выдачи авто по брони №{booking_id} сохранён. "
            "Автомобиль отмечен как выданный."
        ),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    if is_reservation:
        booking_deposit = int(data.get("prepayment", 0))
        rent_price = int(data.get("price", 0))
        delivery_price = int(data.get("delivery_price", 0))
        total_price = rent_price + delivery_price
        delivery_address = data.get("delivery_address", "Самовывоз")
        booking_notification = (
            f"🔔 <b>Новая бронь №{_html_value(booking_id)}</b>\n"
            f"🚗 Автомобиль: <b>{_html_value(data.get('car_name'))}</b>\n"
            f"📅 Период: <b>{_html_value(format_date_ru(data.get('start_date', '')))} — "
            f"{_html_value(format_date_ru(data.get('end_date', '')))}</b>\n"
            f"👤 Клиент: <b>{_html_value(data.get('client_name'))}</b> "
            f"(тел: {_html_value(data.get('phone'))})\n"
            f"💰 Сумма: <b>{_format_number(total_price)} ₽</b> "
            f"(аренда: <b>{_format_number(rent_price)} ₽</b>) | "
            f"Задаток: <b>{_format_number(booking_deposit)} ₽</b> "
            f"({_html_value(data.get('prepayment_pay_type'), 'Без оплаты')})\n"
            f"📍 Доставка: <b>{_html_value(delivery_address)}</b> "
            f"(Стоимость: <b>{_format_number(delivery_price)} ₽</b>)\n"
            f"👨‍💼 Оформил менеджер: <b>{_html_value(manager_name)}</b>"
        )
    else:
        booking = data.get("booking", {})
        car = data.get("car", {})
        booking_notification = build_issuance_notification(
            data,
            booking_id,
            car,
            manager_name,
            is_direct_issuance=is_direct_issuance,
        )
    notification_car_name = (
        data.get("car_name", "")
        if is_direct_issuance
        else _car_name(data.get("car", {}))
    )
    await send_operation_notification(
        bot,
        booking_notification,
        notification_car_name if not is_reservation else data.get("car_name", ""),
    )


async def list_active_rentals(message: Message, state: FSMContext) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    try:
        rentals = (
            get_partner_active_rentals(HEAVY_LUX_PARTNER_SOURCE)
            if is_heavy_lux_partner_user(message.from_user.id)
            else get_active_rentals()
        )
    except Exception:
        logger.exception("Could not load active rentals")
        await message.answer(
            "Не удалось получить активные аренды.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not rentals:
        await message.answer(
            "Активных аренд нет.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if is_heavy_lux_partner_user(message.from_user.id):
        text = "Активные аренды:\n\n" + "\n\n".join(
            _partner_booking_card_text(item) for item in rentals
        )
    else:
        text = "Активные аренды:\n\n" + "\n\n".join(
            f"Бронь №{_booking_id(item)}\n{_booking_label(item)}\n"
            f"Телефон: {_value(item, 'Телефон', 'Телефон клиента')}\n"
            f"{_booking_status_text(item)}"
            for item in rentals
        )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))


async def start_return(message: Message, state: FSMContext) -> None:
    if not await ensure_operational_user(message):
        return
    await state.clear()
    try:
        cars = get_all_cars()
    except Exception:
        logger.exception("Could not load cars for return")
        await message.answer(
            "Не удалось получить список автомобилей.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if not cars:
        await message.answer(
            "В листе Автомобили нет записей для приёма.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    partner_mode = (
        bool(message.from_user)
        and is_heavy_lux_partner_user(message.from_user.id)
    )
    await state.update_data(return_cars=cars, partner_mode=partner_mode)
    if partner_mode:
        await state.update_data(source=HEAVY_LUX_PARTNER_SOURCE)
        await _start_return_operation(message, state)
        return
    await state.set_state(ReturnForm.source)
    await message.answer(
        SOURCE_SELECTION_PROMPT,
        reply_markup=source_selection_keyboard(),
    )


async def _start_return_operation(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    cars = data.get("return_cars", [])
    await state.set_state(ReturnForm.booking)
    await message.answer(
        "🏁 Приём авто.\n\nВыберите автомобиль из автопарка:",
        reply_markup=car_choice_keyboard(cars),
    )


async def return_source_selection(
    message: Message,
    state: FSMContext,
) -> None:
    if (message.text or "").strip() == "Отмена":
        await cancel_flow(message, state)
        return
    source = _source_from_choice(message.text or "")
    if not source:
        await message.answer(
            SOURCE_SELECTION_PROMPT,
            reply_markup=source_selection_keyboard(),
        )
        return
    await state.update_data(source=source, partner_mode=False)
    await _start_return_operation(message, state)


async def _prepare_return(
    message: Message,
    state: FSMContext,
    car: dict,
    booking: dict | None,
) -> None:
    direct_return = booking is None
    current_state_data = await state.get_data()
    partner_mode = bool(current_state_data.get("partner_mode"))
    if direct_return:
        current_mileage = _value(
            car,
            "Текущий пробег (км)",
            "Пробег",
            "Текущий пробег",
            "Пробег (км)",
            default="0",
        )
        start_mileage = safe_int(current_mileage)
        deposit = _car_base_deposit(car) or 0
        returned_at = _timestamp()
        return_key = (
            "DIRECT-"
            + re.sub(
                r"[^A-Za-z0-9_-]",
                "-",
                _car_id(car) or _car_plate(car) or "CAR",
            )
            + "-"
            + re.sub(r"[^0-9]", "", returned_at)
        )
        booking = {
            "ID Брони": return_key,
            "ID Авто": _car_id(car),
            "Автомобиль": _car_name(car),
            "Госномер": _car_plate(car),
            "Дата начала": returned_at[:10],
            "Дата окончания": returned_at[:10],
            "ФИО Клиента": "Без брони",
            "Телефон": "—",
            "Статус": "В аренде",
            "Залог (RUB)": deposit,
            "Страховой залог": deposit,
            "Пробег при выдаче (км)": start_mileage,
        }
        base_price = 0
        prepayment = 0
        rental_paid_at_issuance = 0
        deposit_pay_type = "Без оплаты"
    else:
        try:
            current_mileage = _value(
                car,
                "Текущий пробег (км)",
                "Пробег",
                "Текущий пробег",
                "Пробег (км)",
                "Пробег начальный",
                default="0",
            )
            start_mileage_text = _value(
                booking,
                "Пробег при выдаче (км)",
                default=current_mileage,
            )
            start_mileage = safe_int(start_mileage_text)
            base_price = safe_int(
                _value(
                    booking,
                    "Сумма брони (RUB)",
                    "Принятая сумма аренды (RUB)",
                    "Цена",
                    "Стоимость",
                    "Итоговая стоимость",
                    default="0",
                )
            )
            deposit = safe_int(
                _value(
                    booking,
                    "Страховой залог",
                    "Страховой залог (RUB)",
                    "Внесенный страховой залог (RUB)",
                    "Внесенный залог",
                    "Залог (RUB)",
                    "Залог",
                    default="0",
                )
            )
            deposit_pay_type = _value(
                booking,
                "Способ оплаты страхового залога",
                "Способ оплаты залога",
                "Способ оплаты",
                default="Без оплаты",
            )
            prepayment = safe_int(
                _value(
                    booking,
                    "Задаток/Предоплата",
                    "Залог/Предоплата",
                    "Предоплата",
                    default="0",
                )
            )
            rental_paid_at_issuance = safe_int(
                _value(
                    booking,
                    "Оплачено при выдаче (RUB)",
                    "Фактически оплачено при выдаче (RUB)",
                    "Оплата при выдаче (RUB)",
                    "Фактически оплачено за аренду (RUB)",
                    default="0",
                )
            )
        except Exception:
            logger.exception("Could not prepare booking for return")
            await state.clear()
            await message.answer(
                "Не удалось получить данные аренды.",
                reply_markup=get_main_keyboard(message.from_user.id),
            )
            return
        returned_at = _timestamp()

    await state.update_data(
        booking=booking,
        car=car,
        start_mileage=start_mileage,
        base_price=base_price,
        deposit=deposit,
        deposit_pay_type=deposit_pay_type,
        prepayment=prepayment,
        rental_paid_at_issuance=rental_paid_at_issuance,
        prepayment_pay_type=_value(
            booking,
            "Способ оплаты задатка",
            "Тип оплаты залога",
            default="Без оплаты",
        ),
        returned_at=returned_at,
        wash_fee=0,
        extra_fee=0,
        direct_return=direct_return,
        direct_return_id=booking.get("ID Брони") if direct_return else None,
    )
    await state.set_state(ReturnForm.end_mileage)
    if direct_return and partner_mode:
        prompt = (
            f"Источник: {HEAVY_LUX_PARTNER_SOURCE}\n"
            f"Автомобиль: {_car_name(car)} ({_car_plate(car)}).\n"
            f"Начальный пробег: {start_mileage} км.\n\n"
            "Введите конечный пробег в км:"
        )
    elif direct_return:
        prompt = (
            f"Автомобиль: {_car_name(car)} ({_car_plate(car)}).\n"
            f"Начальный пробег: {start_mileage} км.\n"
            f"Базовый залог: {deposit} RUB.\n\n"
            "Введите конечный пробег в км:"
        )
    elif partner_mode:
        prompt = (
            f"Источник: {HEAVY_LUX_PARTNER_SOURCE}\n"
            f"Возврат брони №{_booking_id(booking)}.\n"
            f"Автомобиль: {_car_name(car)} ({_car_plate(car)}).\n"
            f"Начальный пробег: {start_mileage} км.\n\n"
            "Введите конечный пробег в км:"
        )
    else:
        prompt = (
            f"Возврат брони №{_booking_id(booking)}.\n"
            f"Начальный пробег: {start_mileage} км.\n"
            "Введите конечный пробег в км:"
        )
    await message.answer(prompt, reply_markup=ReplyKeyboardRemove())


async def return_booking(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cars = data.get("return_cars", [])
    try:
        choice = int((message.text or "").split(".", 1)[0])
        car = cars[choice - 1]
    except (ValueError, IndexError):
        await message.answer("Выберите автомобиль кнопкой из списка.")
        return

    try:
        active_bookings = [
            booking
            for booking in get_active_bookings_for_car(
                _car_id(car),
                _car_plate(car),
            )
            if str(booking.get("Статус", "")).strip() == "В аренде"
        ]
    except Exception:
        logger.exception("Could not load booking for selected return car")
        await state.clear()
        await message.answer(
            "Не удалось проверить активную аренду автомобиля.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    await _prepare_return(
        message,
        state,
        car,
        active_bookings[0] if active_bookings else None,
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
    data = await state.get_data()
    if data.get("partner_mode"):
        await state.set_state(ReturnForm.cleanliness)
        cleanliness_message = await message.answer(
            "Укажите состояние автомобиля при приёме:",
            reply_markup=cleanliness_keyboard(),
        )
        await state.update_data(
            cleanliness_message_id=cleanliness_message.message_id
        )
        return
    if data.get("direct_return"):
        await state.set_state(ReturnForm.rental_paid)
        await message.answer(
            "Введите фактически полученную оплату за аренду в RUB "
            "(0, если оплаты не было):"
        )
        return
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
    data = await state.get_data()
    if data.get("partner_mode"):
        await state.update_data(
            wash_fee=0,
            extra_fee=0,
            time_mileage_penalty=0,
            rental_paid=0,
            rental_pay_type="Без оплаты",
            total_paid=0,
            pay_type="Без оплаты",
            rental_total_confirmed=True,
            rental_total_override=None,
        )
        await state.set_state(ReturnForm.fuel_level)
        await callback.message.answer(
            "Укажите уровень топлива при приёме "
            "(например: 75% или полный бак):"
        )
        return
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


async def return_fuel_level(message: Message, state: FSMContext) -> None:
    fuel_level = (message.text or "").strip()
    if len(fuel_level) < 1:
        await message.answer("Укажите уровень топлива текстом.")
        return
    await state.update_data(fuel_level=fuel_level)
    await state.set_state(ReturnForm.notes)
    await message.answer(
        "Введите комментарий о состоянии автомобиля или отправьте «нет»:"
    )


async def return_extra_fee(message: Message, state: FSMContext) -> None:
    extra_fee, error = _parse_int(message.text or "", "дополнительные начисления", minimum=0)
    if error:
        await message.answer(error)
        return
    await state.update_data(extra_fee=extra_fee)
    data = await state.get_data()
    calculation = _return_calculation(data)
    if (
        not data.get("direct_return")
        and (
            int(calculation["chargeable_late_hours"]) > 0
            or int(calculation["over_mileage"]) > 0
        )
    ):
        await state.set_state(ReturnForm.penalty_decision)
        warning_message = await message.answer(
            "⚠️ Обнаружено опоздание на "
            f"{int(calculation['late_hours'])} ч. и перепробег "
            f"{int(calculation['over_mileage'])} км.\n\n"
            "Первые 2 часа опоздания не тарифицируются автоматически.\n"
            "Удерживать штраф за опоздание / перепробег?",
            reply_markup=time_mileage_penalty_keyboard(),
        )
        await state.update_data(
            penalty_decision_message_id=warning_message.message_id,
        )
        return
    await _continue_after_time_mileage_penalty(message, state)


async def _continue_after_time_mileage_penalty(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if (
        not data.get("direct_return")
        and not data.get("rental_total_confirmed")
    ):
        calculation = _return_calculation(data)
        await state.set_state(ReturnForm.rental_total_decision)
        total_message = await message.answer(
            f"Расчетная стоимость: "
            f"{_format_number(calculation['calculated_rental_total'])} RUB\n\n"
            "Итоговая стоимость аренды верна или была предоставлена скидка?",
            reply_markup=rental_total_keyboard(
                int(calculation["calculated_rental_total"])
            ),
        )
        await state.update_data(
            rental_total_decision_message_id=total_message.message_id,
        )
        return
    await _continue_after_rental_total(message, state)


async def _continue_after_rental_total(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    calculation = _return_calculation(data)
    rental_due = int(calculation["rental_due"])
    if rental_due > 0:
        await state.set_state(ReturnForm.rental_paid)
        await message.answer(
            "Введите фактически полученную оплату за аренду в RUB "
            f"(к оплате после учёта задатка: {rental_due:,} RUB):".replace(
                ",",
                " ",
            )
        )
        return
    await state.update_data(
        rental_paid=0,
        rental_pay_type="Без оплаты",
    )
    await _continue_return_after_rental_payment(message, state)


async def rental_total_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != ReturnForm.rental_total_decision.state:
        await callback.answer(
            "Этот выбор устарел. Продолжите текущую операцию.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "rental_total_decision_message_id"
    ):
        await callback.answer(
            "Это сообщение устарело. Используйте последнее предложение.",
            show_alert=True,
        )
        return
    decision = callback.data.removeprefix("return_rental_total:")
    if decision not in {"keep", "discount"}:
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if decision == "keep":
        await state.update_data(
            rental_total_confirmed=True,
            rental_total_override=None,
        )
        await callback.answer("Расчетную стоимость оставляем.")
        await _continue_after_rental_total(callback.message, state)
        return
    await callback.answer("Введите согласованную стоимость со скидкой.")
    await state.set_state(ReturnForm.rental_total_override)
    await callback.message.answer(
        "Введите согласованную общую стоимость аренды в RUB "
        "(например, 42000):"
    )


async def rental_total_override_input(
    message: Message,
    state: FSMContext,
) -> None:
    total_price, error = _parse_int(
        message.text or "",
        "итоговая стоимость аренды",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(
        rental_total_confirmed=True,
        rental_total_override=total_price,
    )
    await _continue_after_rental_total(message, state)


async def time_mileage_penalty_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != ReturnForm.penalty_decision.state:
        await callback.answer(
            "Этот выбор устарел. Продолжите текущую операцию.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "penalty_decision_message_id"
    ):
        await callback.answer(
            "Это предупреждение устарело. Используйте последнее сообщение.",
            show_alert=True,
        )
        return
    decision = callback.data.removeprefix("return_penalty:")
    if decision not in {"none", "manual"}:
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if decision == "none":
        await state.update_data(time_mileage_penalty=0)
        await callback.answer("Штраф за время и перепробег не удерживается.")
        await _continue_after_time_mileage_penalty(callback.message, state)
        return
    await callback.answer("Введите сумму штрафа вручную.")
    await state.set_state(ReturnForm.manual_penalty)
    await callback.message.answer(
        "Введите общую сумму штрафа за опоздание / перепробег в RUB "
        "(0, если удерживать штраф не нужно):"
    )


async def return_manual_penalty(
    message: Message,
    state: FSMContext,
) -> None:
    penalty, error = _parse_int(
        message.text or "",
        "штраф за опоздание / перепробег",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(time_mileage_penalty=penalty)
    await _continue_after_time_mileage_penalty(message, state)


async def return_rental_paid(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("partner_mode"):
        await state.update_data(
            rental_paid=0,
            rental_pay_type="Без оплаты",
        )
        await _continue_return_after_rental_payment(message, state)
        return
    rental_paid, error = _parse_int(
        message.text or "",
        "фактически полученная оплата за аренду",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(rental_paid=rental_paid)
    if rental_paid == 0:
        await state.update_data(rental_pay_type="Без оплаты")
        await _continue_return_after_rental_payment(message, state)
        return
    await state.set_state(ReturnForm.rental_pay_type)
    payment_message = await message.answer(
        "Выберите способ получения оплаты за аренду:",
        reply_markup=payment_methods_keyboard("return_rental"),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def return_rental_pay_type(
    message: Message,
    state: FSMContext,
) -> None:
    if (await state.get_data()).get("partner_mode"):
        await state.update_data(
            rental_paid=0,
            rental_pay_type="Без оплаты",
        )
        await _continue_return_after_rental_payment(message, state)
        return
    payment_message = await message.answer(
        "Выберите способ получения оплаты за аренду кнопкой ниже:",
        reply_markup=payment_methods_keyboard("return_rental"),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def _continue_return_after_rental_payment(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    calculation = _return_calculation(data)
    if data.get("direct_return"):
        await state.set_state(ReturnForm.direct_deposit)
        deposit_message = await message.answer(
            "Выберите, что сделать со страховым залогом:",
            reply_markup=direct_deposit_keyboard(),
        )
        await state.update_data(
            direct_deposit_message_id=deposit_message.message_id,
        )
        return
    difference = int(calculation["deposit_balance"])
    if difference < 0:
        required_payment = abs(difference)
        await state.update_data(required_additional_payment=required_payment)
        await state.set_state(ReturnForm.total_paid)
        await message.answer(
            _deposit_settlement_text(calculation)
            + "\n\nВведите фактически полученную доплату за повреждения/штрафы в RUB "
            f"(требуется {required_payment:,} RUB):".replace(",", " ")
        )
        return
    await state.update_data(total_paid=0, pay_type="Без оплаты")
    await state.set_state(ReturnForm.notes)
    await message.answer(
        _deposit_settlement_text(calculation)
        + "\n\nВведите примечание или отправьте «нет»:"
    )


async def direct_deposit_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    if await state.get_state() != ReturnForm.direct_deposit.state:
        await callback.answer(
            "Этот выбор устарел. Продолжите текущую операцию.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    if getattr(callback.message, "message_id", None) != data.get(
        "direct_deposit_message_id"
    ):
        await callback.answer(
            "Этот выбор оплаты устарел. Используйте последние кнопки.",
            show_alert=True,
        )
        return
    decision = callback.data.removeprefix("direct_deposit:")
    if decision not in {"return", "retain"}:
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    await state.update_data(
        direct_deposit_status=(
            "returned" if decision == "return" else "retained"
        ),
        total_paid=0,
        pay_type="Без оплаты",
    )
    await callback.answer(
        "Залог будет возвращён."
        if decision == "return"
        else "Залог будет удержан."
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(ReturnForm.notes)
    await callback.message.answer(
        "Введите комментарий к внеплановому возврату или отправьте «нет»:"
    )


async def return_total_paid(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("partner_mode"):
        await state.update_data(
            total_paid=0,
            pay_type="Без оплаты",
        )
        await state.set_state(ReturnForm.notes)
        await message.answer("Введите комментарий о состоянии автомобиля или отправьте «нет»:")
        return
    total_paid, error = _parse_int(
        message.text or "",
        "фактически полученная доплата за повреждения/штрафы",
        minimum=0,
    )
    if error:
        await message.answer(error)
        return
    await state.update_data(total_paid=total_paid)
    data = await state.get_data()
    if total_paid == 0:
        await state.update_data(pay_type="Без оплаты")
        await state.set_state(ReturnForm.notes)
        required_payment = int(data.get("required_additional_payment", 0))
        await message.answer(
            "Фактически получено доплаты за повреждения/штрафы: 0 RUB.\n"
            + f"Осталось получить с клиента: {required_payment:,} RUB.\n".replace(",", " ")
            + "Способ оплаты автоматически установлен: Без оплаты.\n\n"
            "Введите примечание или отправьте «нет»:"
        )
        return
    await state.set_state(ReturnForm.pay_type)
    payment_message = await message.answer(
        "Выберите способ получения доплаты за повреждения/штрафы:",
        reply_markup=payment_methods_keyboard(),
    )
    await state.update_data(payment_message_id=payment_message.message_id)


async def return_pay_type(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("partner_mode"):
        await state.update_data(pay_type="Без оплаты")
        await state.set_state(ReturnForm.notes)
        await message.answer("Введите комментарий о состоянии автомобиля или отправьте «нет»:")
        return
    payment_message = await message.answer(
        "Выберите способ получения доплаты за повреждения/штрафы кнопкой ниже:",
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
    if data.get("partner_mode"):
        await callback.answer(
            "Платёжные действия недоступны в партнёрском режиме.",
            show_alert=True,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await state.set_state(
            BookingForm.confirmation
            if data.get("booking_mode") in {"reservation", "issuance", "direct_issuance"}
            else ReturnForm.notes
        )
        return
    valid_flow = (
        flow == "reservation"
        and current_state == BookingForm.pay_type.state
        and data.get("booking_mode") == "reservation"
    ) or (
        flow == "issuance_deposit"
        and current_state == BookingForm.deposit_pay_type.state
        and data.get("booking_mode") in {"issuance", "direct_issuance"}
    ) or (
        flow == "direct_rental"
        and current_state == BookingForm.pay_type.state
        and data.get("booking_mode") == "direct_issuance"
    ) or (
        flow == "return_rental"
        and current_state == ReturnForm.rental_pay_type.state
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
    if flow == "issuance_deposit":
        await state.update_data(deposit_pay_type=pay_type)
        await _send_booking_confirmation(callback.message, state)
        return
    if flow == "direct_rental":
        await state.update_data(pay_type=pay_type)
        await state.set_state(BookingForm.start_mileage)
        await callback.message.answer(
            "Введите пробег автомобиля на момент выдачи в км:"
        )
        return
    if flow == "return_rental":
        await state.update_data(rental_pay_type=pay_type)
        await _continue_return_after_rental_payment(
            callback.message,
            state,
        )
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
    if data.get("partner_mode"):
        await state.update_data(
            manager_name=_manager_display_name(message.from_user),
        )
        data = await state.get_data()
        await message.answer(
            _partner_return_card_text(data),
            reply_markup=return_confirmation_keyboard(),
        )
        return
    delta = int(data["end_mileage"]) - int(data["start_mileage"])
    calculation = _return_calculation(data)
    deposit_balance = int(calculation["deposit_balance"])
    total_penalties = int(calculation["total_penalties"])
    rental_total = int(calculation["rental_total"])
    prepayment = int(calculation["prepayment"])
    rental_due = int(calculation["rental_due"])
    prepayment_overpayment = int(calculation["prepayment_overpayment"])
    rental_paid = int(data.get("rental_paid", 0))
    rental_outstanding = max(rental_due - rental_paid, 0)
    received_additional = int(data.get("total_paid", 0))
    required_additional = max(-deposit_balance, 0)
    outstanding_additional = max(
        required_additional - received_additional,
        0,
    )
    additional_payment_text = ""
    if deposit_balance < 0:
        additional_payment_text = (
            "\n• Требуемая доплата за повреждения/штрафы: "
            f"{_format_number(required_additional)} RUB"
            "\n• Фактически получено доплаты: "
            f"{_format_number(received_additional)} RUB"
            f"\n• Способ получения доплаты: {data.get('pay_type', 'Без оплаты')}"
            "\n• Осталось получить: "
            f"{_format_number(outstanding_additional)} RUB"
        )
    prepayment_overpayment_text = ""
    if prepayment_overpayment:
        prepayment_overpayment_text = (
            "\n• Переплата задатка к возврату: "
            f"{_format_number(prepayment_overpayment)} RUB"
        )
    manager = message.from_user
    manager_name = (
        getattr(manager, "full_name", "")
        or getattr(manager, "username", "")
        or str(getattr(manager, "id", "—"))
    )
    await state.update_data(manager_name=manager_name)
    phone = _value(
        data["booking"],
        "Телефон",
        "Телефон клиента",
        default="—",
    )
    issued_datetime = _booking_issued_datetime(data["booking"])
    issued_at = (
        _timestamp(issued_datetime)
        if issued_datetime
        else _value(
            data["booking"],
            "Дата и время выдачи",
            "Фактическая дата выдачи",
            "Дата выдачи",
            "Дата начала",
            default="—",
        )
    )
    late_warning = ""
    if int(calculation.get("chargeable_late_hours", 0)) > 0:
        late_warning = (
            f"\n⚠️ Опоздание на {int(calculation['late_hours'])} ч. "
            f"(первые {RENTAL_GRACE_PERIOD_MINUTES // 60} ч. — бесплатно). "
            "Удержание применяется только по решению менеджера.\n"
        )
    rental_total_label = (
        "Полная стоимость аренды (со скидкой)"
        if calculation.get("rental_total_override") is not None
        else "Полная стоимость аренды"
    )
    if calculation.get("rental_total_override") is not None:
        rental_total_line = (
            f"• {rental_total_label}: {_format_number(rental_total)} RUB\n"
            f"• Расчёт по тарифу: {_format_number(calculation['daily_rate'])} × "
            f"{_format_number(calculation['rental_days'])} сут. = "
            f"{_format_number(calculation['calculated_rental_total'])} RUB"
        )
    else:
        rental_total_line = (
            f"• {rental_total_label}: {_format_number(rental_total)} RUB "
            f"({_format_number(calculation['daily_rate'])} × "
            f"{_format_number(calculation['rental_days'])} сут.)"
        )
    act_text = (
        "🏁 АКТ ПРИЁМА АВТОМОБИЛЯ\n"
        f"• Бронь №: {_booking_id(data['booking'])}\n"
        f"• Автомобиль: {_car_name(data['car'])} ({_car_plate(data['car'])})\n"
        f"• Клиент: {_value(data['booking'], 'ФИО Клиента', 'Клиент', default='—')} / {phone}\n"
        f"• Менеджер приёмки: {manager_name}\n"
        f"• Фактический период: {_format_datetime_ru(issued_at)} — "
        f"{_format_datetime_ru(data.get('returned_at', _timestamp()))}\n\n"
        f"{late_warning}"
        "\n💰 РАСЧЕТ АРЕНДЫ:\n"
        f"{rental_total_line}\n"
        f"• Оплачено при выдаче: "
        f"{_format_number(calculation['rental_paid_at_issuance'])} RUB\n"
        f"• Учтен задаток (предоплата): -{_format_number(prepayment)} RUB\n"
        f"• Остаток к оплате за аренду: {_format_number(rental_due)} RUB\n"
        f"• Фактически оплачено за аренду: {_format_number(rental_paid)} RUB\n"
        f"• Способ оплаты аренды: {data.get('rental_pay_type', 'Без оплаты')}\n"
        f"• Осталось оплатить за аренду: {_format_number(rental_outstanding)} RUB"
        f"{prepayment_overpayment_text}\n\n"
        "🛡 СТРАХОВОЙ ЗАЛОГ И УДЕРЖАНИЯ:\n"
        f"• Внесённый страховой залог: {_format_number(data['deposit'])} RUB\n"
        f"• Пробег: Выдача {_format_number(data['start_mileage'])} км / "
        f"Возврат {_format_number(data['end_mileage'])} км "
        f"(Итого: {_format_number(delta)} км, "
        f"Лимит: {_format_number(calculation['mileage_limit'])} км)\n"
        f"• Обнаружено опоздание: {int(calculation['late_hours'])} ч. и "
        f"перепробег: {_format_number(calculation['over_mileage'])} км\n"
        f"• Удержание за опоздание (отдельно от аренды): "
        f"{_format_number(calculation['time_mileage_penalty'])} RUB\n"
        f"• Чистота: {calculation['cleanliness']} -> "
        f"Мойка: {_format_number(calculation['wash_fee'])} RUB\n"
        f"• Доп. штрафы / удержания: {_format_number(data['extra_fee'])} RUB\n"
        f"• Суммарно удержано из залога: {_format_number(total_penalties)} RUB\n\n"
        "ИТОГ К ВЫПЛАТЕ/ДОПЛАТЕ:\n"
        f"• {_deposit_outcome_text(calculation)}"
        f"{additional_payment_text}\n"
        f"• Примечание: {notes or '—'}\n\n"
        "Сохранить приём автомобиля?"
    )
    await message.answer(
        act_text,
        reply_markup=return_confirmation_keyboard(),
    )


async def confirm_return_save_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    if await state.get_state() != ReturnForm.confirmation.state:
        await callback.answer(
            "Этот шаг приёма уже завершён или устарел.",
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message is not None:
        await _save_return(
            callback.message,
            state,
            bot,
            manager_id=callback.from_user.id,
        )


async def cancel_return_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(
            "Операция отменена.",
            reply_markup=get_main_keyboard(callback.from_user.id),
        )


async def return_confirmation(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    confirmation = (message.text or "").strip().casefold()
    if confirmation not in {
        "да",
        "подтвердить",
        "подтвердить сохранение",
    }:
        await message.answer(
            "Для сохранения приёма нажмите «✅ Подтвердить сохранение» "
            "или отправьте «Да» / «Подтвердить». "
            "Для отмены используйте кнопку «❌ Отмена» или отправьте «Отмена».",
            reply_markup=return_confirmation_keyboard(),
        )
        return
    await _save_return(message, state, bot)


async def _save_return(
    message: Message,
    state: FSMContext,
    bot: Bot,
    manager_id: int | None = None,
) -> None:
    data = await state.get_data()
    booking = data["booking"]
    car = data["car"]
    manager_id = (
        manager_id
        if manager_id is not None
        else (message.from_user.id if message.from_user else 0)
    )
    calculation = _return_calculation(data)
    issued_datetime = _booking_issued_datetime(booking)
    issued_at = (
        _timestamp(issued_datetime)
        if issued_datetime
        else _value(
            booking,
            "Дата и время выдачи",
            "Фактическая дата выдачи",
            "Дата выдачи",
            "Дата начала",
            default="—",
        )
    )
    direct_return = bool(data.get("direct_return"))
    return_id = (
        _booking_id(booking)
        if direct_return
        else int(_booking_id(booking))
    )
    try:
        save_actual_return(
            booking_id=None if direct_return else return_id,
            car_name=_value(
                booking,
                "Автомобиль",
                "Авто",
                "Название авто",
                "Модель",
            ),
            plate=_car_plate(car),
            client=_value(
                booking,
                "ФИО Клиента",
                "Клиент",
                "Клиент ФИО",
                "Имя клиента",
            ),
            start_mileage=int(data["start_mileage"]),
            end_mileage=int(data["end_mileage"]),
            delta_mileage=int(data["end_mileage"]) - int(data["start_mileage"]),
            base_price=int(calculation["rental_total"]),
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
            time_mileage_penalty=int(
                calculation["time_mileage_penalty"]
            ),
            cleanliness=str(calculation["cleanliness"]),
            wash_fee=int(calculation["wash_fee"]),
            deposit=int(calculation["deposit"]),
            deposit_balance=int(calculation["deposit_balance"]),
            rental_total=int(calculation["rental_total"]),
            prepayment=int(calculation["prepayment"]),
            rental_balance=int(calculation["rental_due"]),
            prepayment_overpayment=int(
                calculation["prepayment_overpayment"]
            ),
            deposit_pay_type=data.get(
                "deposit_pay_type",
                "Без оплаты",
            ),
            prepayment_pay_type=data.get(
                "prepayment_pay_type",
                "Без оплаты",
            ),
            rental_paid_at_issuance=int(
                calculation["rental_paid_at_issuance"]
            ),
            rental_paid=int(data.get("rental_paid", 0)),
            rental_pay_type=data.get(
                "rental_pay_type",
                "Без оплаты",
            ),
            rental_outstanding=max(
                int(calculation["rental_due"])
                - int(data.get("rental_paid", 0)),
                0,
            ),
            manager_name=data.get("manager_name", ""),
            phone=_value(
                booking,
                "Телефон",
                "Телефон клиента",
                default="—",
            ),
            issued_at=issued_at,
            act_created_at=_timestamp(),
            allow_unlinked=direct_return,
            car_id=_car_id(car),
            idempotency_key=return_id if direct_return else "",
            source=_operation_source(data),
        )
    except ValueError:
        logger.warning(
            "Return rejected because booking %s is no longer returnable",
            _booking_id(booking),
            exc_info=True,
        )
        await state.clear()
        await message.answer(
            "Эта аренда уже была завершена или её статус изменился. "
            "Данные повторно не записаны.",
            reply_markup=get_main_keyboard(manager_id),
        )
        return
    except Exception as error:
        logging.error(
            f"[RETURN_ERROR] Ошибка записи возврата: {error}",
            exc_info=True,
        )
        error_text = str(error)
        if "429" in error_text or "quota exceeded" in error_text.casefold():
            reason = (
                "Google Sheets временно отклонил запись из-за превышения "
                "лимита запросов (429). Повторите сохранение через минуту."
            )
        elif isinstance(error, KeyError):
            reason = f"Не найдена колонка Google Sheets: {error}"
        else:
            reason = f"{type(error).__name__}: {error}"
        await message.answer(
            "Не удалось сохранить возврат в Google Sheets.\n"
            f"Причина: {reason}\n\n"
            "Данные приёма сохранены в текущем шаге. "
            "Исправьте причину и нажмите подтверждение ещё раз.",
            reply_markup=return_confirmation_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        (
            f"Внеплановый возврат автомобиля {_car_name(car)} оформлен. "
            "Автомобиль освобождён, пробег обновлён."
            if direct_return
            else (
                f"Возврат брони №{_booking_id(booking)} оформлен. "
                "Бронь завершена, автомобиль освобождён, пробег обновлён."
            )
        ),
        reply_markup=get_main_keyboard(manager_id),
    )
    rental_paid = int(data.get("rental_paid", 0))
    additional_paid = int(data.get("total_paid", 0))
    total_received = rental_paid + additional_paid
    rental_pay_type = data.get("rental_pay_type", "Без оплаты")
    additional_pay_type = data.get("pay_type", "Без оплаты")
    if rental_paid and additional_paid:
        payment_method = (
            f"аренда: {rental_pay_type}; "
            f"доплата: {additional_pay_type}"
        )
    elif rental_paid:
        payment_method = rental_pay_type
    else:
        payment_method = additional_pay_type
    end_mileage = safe_int(data.get("end_mileage", 0))
    start_mileage = safe_int(data.get("start_mileage", 0))
    delta_mileage = end_mileage - start_mileage
    return_notification = (
        "🏁 <b>Автомобиль принят (Касса)!</b>\n"
        f"🚘 Авто: <b>{_html_value(_car_name(car))} "
        f"({_html_value(_car_plate(car))})</b>\n"
        f"👤 Клиент: <b>{_html_value(_value(booking, 'ФИО Клиента', 'Клиент'))}</b>\n"
        f"⏱ Фактический период: <b>{_html_value(issued_at)} — "
        f"{_html_value(data.get('returned_at', _timestamp()))}</b>\n"
        f"📈 Пробег за поездку: <b>+{_html_value(delta_mileage)} км</b> "
        f"(текущий: {_html_value(end_mileage)} км)\n"
        f"💰 Оплачено: <b>{_format_number(total_received)} ₽</b> "
        f"({_html_value(payment_method)})\n"
        f"📝 Комментарий/состояние: <i>{_html_value(data.get('notes'), '—')}</i>\n"
        f"👨‍💼 Принял менеджер: <b>{_html_value(data.get('manager_name') or _manager_display_name(message.from_user))}</b>"
    )
    await send_operation_notification(
        bot,
        return_notification,
        _car_name(car),
    )


async def unknown_message(message: Message) -> None:
    if message.from_user and is_partner_user(message.from_user.id):
        await message.answer(
            "Выберите действие кнопками меню.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        return
    if await ensure_manager(message):
        await message.answer(
            "Выберите действие кнопками меню.",
            reply_markup=get_main_keyboard(message.from_user.id),
        )


async def error_handler(event: object) -> None:
    logger.exception("Unhandled bot event: %s", event)


def create_dispatcher(client_bot: Bot | None = None) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.callback_query.register(calendar_callback, F.data.startswith("calendar:"))
    dispatcher.callback_query.register(payment_method_callback, F.data.startswith("payment:"))
    dispatcher.callback_query.register(cleanliness_callback, F.data.startswith("cleanliness:"))
    dispatcher.callback_query.register(
        booking_delivery_callback,
        F.data.in_({"delivery_yes", "delivery_no"}),
    )
    dispatcher.callback_query.register(
        issuance_date_callback,
        F.data.in_(
            {"issue_date_now", "issue_date_calendar"}
        ),
    )
    dispatcher.callback_query.register(
        direct_issuance_car_confirmation,
        F.data.startswith("direct_issuance_car:"),
    )
    dispatcher.callback_query.register(
        direct_deposit_callback,
        F.data.startswith("direct_deposit:"),
    )
    dispatcher.callback_query.register(
        time_mileage_penalty_callback,
        F.data.startswith("return_penalty:"),
    )
    dispatcher.callback_query.register(
        rental_total_callback,
        F.data.startswith("return_rental_total:"),
    )
    dispatcher.callback_query.register(
        confirm_return_save_callback,
        F.data == "confirm_return_save",
    )
    dispatcher.callback_query.register(
        cancel_return_callback,
        F.data == "cancel_return",
    )
    dispatcher.callback_query.register(
        overdue_return_callback,
        F.data.startswith("return_car:"),
    )
    dispatcher.callback_query.register(
        overdue_extend_callback,
        F.data.startswith("extend_rent:"),
    )
    dispatcher.callback_query.register(
        handover_car_callback,
        F.data.startswith("handover_car:"),
    )
    dispatcher.callback_query.register(
        expense_category_callback,
        F.data.startswith("expense_category:"),
    )
    dispatcher.callback_query.register(
        expense_cancel_select_callback,
        F.data.startswith("expense_cancel_select:"),
    )
    dispatcher.callback_query.register(
        financial_month_callback,
        F.data.startswith("financial_month:"),
    )
    dispatcher.callback_query.register(
        financial_report_type_callback,
        F.data.startswith("financial_type:"),
    )
    dispatcher.callback_query.register(
        financial_car_toggle_callback,
        F.data.startswith("financial_car_toggle:"),
    )
    dispatcher.callback_query.register(
        financial_car_calculate_callback,
        F.data == "financial_car_calculate",
    )
    dispatcher.callback_query.register(
        expense_history_month_callback,
        F.data.startswith("expense_history_month:"),
    )
    dispatcher.callback_query.register(
        expense_history_filter_callback,
        F.data.startswith("expense_history_filter:"),
    )
    dispatcher.callback_query.register(
        expense_history_car_callback,
        F.data.startswith("expense_history_car:"),
    )
    dispatcher.callback_query.register(
        expense_history_export_callback,
        F.data.startswith("expense_export:"),
    )
    dispatcher.callback_query.register(
        cancellation_booking_callback,
        F.data.startswith("cancel_booking:"),
    )
    dispatcher.callback_query.register(
        cancellation_confirmation_callback,
        F.data.in_({"cancel_booking_confirm", "cancel_booking_back"}),
    )
    dispatcher.callback_query.register(
        cancellation_deposit_callback,
        F.data.startswith("cancel_deposit:"),
    )
    async def admin_booking_callback_handler(
        callback: CallbackQuery,
        bot: Bot,
    ) -> None:
        await client_booking_admin_callback(
            callback,
            bot,
            client_bot=client_bot,
        )

    dispatcher.callback_query.register(
        admin_booking_callback_handler,
        F.data.startswith("client_booking:"),
    )
    dispatcher.message.register(start_handler, CommandStart())
    dispatcher.message.register(show_menu, Command("menu"))
    dispatcher.message.register(cancel_flow, Command("cancel"))
    dispatcher.message.register(cancel_flow, F.text.casefold() == "отмена")

    dispatcher.message.register(partner_bookings, F.text == "🔎 Брони Dodge")
    dispatcher.message.register(partner_status, F.text == "🚗 Статус Dodge")
    dispatcher.message.register(partner_expenses, F.text == "📑 Расходы Dodge")

    dispatcher.message.register(start_availability, F.text == "Доступные авто")
    dispatcher.message.register(start_booking, F.text == "Новая бронь")
    dispatcher.message.register(start_booking, F.text == "📝 Новая бронь")
    dispatcher.message.register(start_issuance, F.text == "🚗 Выдача авто")
    dispatcher.message.register(list_active_rentals, F.text == "Активные аренды")
    dispatcher.message.register(start_return, F.text == "🏁 Приём авто")
    dispatcher.message.register(start_return, F.text == "Оформить возврат")
    dispatcher.message.register(start_occupancy, F.text == "🔎 Проверка броней")
    dispatcher.message.register(start_occupancy, F.text == "🔍 Проверка броней")
    dispatcher.message.register(start_cancellation, F.text == "🚫 Отмена брони")
    dispatcher.message.register(start_expense, F.text == "💸 Внести расход")
    dispatcher.message.register(
        start_expense_cancellation,
        F.text == "❌ Отменить расход",
    )
    dispatcher.message.register(
        start_financial_report,
        F.text == "📈 Фин. отчет",
    )
    dispatcher.message.register(
        start_expense_history,
        F.text == "📑 Просмотр расходов",
    )

    dispatcher.message.register(availability_start_date, AvailabilityForm.start_date)
    dispatcher.message.register(availability_end_date, AvailabilityForm.end_date)

    dispatcher.message.register(booking_start_date, BookingForm.start_date)
    dispatcher.message.register(booking_end_date, BookingForm.end_date)
    dispatcher.message.register(
        issuance_mode_selection,
        BookingForm.issuance_mode,
    )
    dispatcher.message.register(issue_time, BookingForm.issue_time)
    dispatcher.message.register(direct_end_date, BookingForm.direct_end_date)
    dispatcher.message.register(
        issuance_booking,
        BookingForm.issuance_booking,
    )
    dispatcher.message.register(booking_car, BookingForm.car)
    dispatcher.message.register(booking_client_name, BookingForm.client_name)
    dispatcher.message.register(booking_phone, BookingForm.phone)
    dispatcher.message.register(
        booking_delivery_address,
        BookingForm.delivery_address,
    )
    dispatcher.message.register(
        booking_delivery_price,
        BookingForm.delivery_price,
    )
    dispatcher.message.register(booking_price, BookingForm.price)
    dispatcher.message.register(booking_prepayment, BookingForm.prepayment)
    dispatcher.message.register(booking_start_mileage, BookingForm.start_mileage)
    dispatcher.message.register(booking_deposit, BookingForm.deposit)
    dispatcher.message.register(booking_confirmation, BookingForm.confirmation)

    dispatcher.message.register(occupancy_car, OccupancyForm.car)

    dispatcher.message.register(cancellation_car, CancellationForm.car)
    dispatcher.message.register(cancellation_reason, CancellationForm.reason)

    dispatcher.message.register(booking_source_selection, BookingForm.source)
    dispatcher.message.register(return_source_selection, ReturnForm.source)
    dispatcher.message.register(return_booking, ReturnForm.booking)
    dispatcher.message.register(
        overdue_extension_input,
        OverdueExtensionForm.end_at,
    )
    dispatcher.message.register(return_end_mileage, ReturnForm.end_mileage)
    dispatcher.message.register(return_fuel_level, ReturnForm.fuel_level)
    dispatcher.message.register(return_wash_fee, ReturnForm.wash_fee)
    dispatcher.message.register(return_extra_fee, ReturnForm.extra_fee)
    dispatcher.message.register(
        return_manual_penalty,
        ReturnForm.manual_penalty,
    )
    dispatcher.message.register(
        rental_total_override_input,
        ReturnForm.rental_total_override,
    )
    dispatcher.message.register(return_rental_paid, ReturnForm.rental_paid)
    dispatcher.message.register(
        return_rental_pay_type,
        ReturnForm.rental_pay_type,
    )
    dispatcher.message.register(return_total_paid, ReturnForm.total_paid)
    dispatcher.message.register(return_pay_type, ReturnForm.pay_type)
    dispatcher.message.register(return_notes, ReturnForm.notes)
    dispatcher.message.register(return_confirmation, ReturnForm.confirmation)

    dispatcher.message.register(expense_amount, ExpenseForm.amount)
    dispatcher.message.register(expense_vehicle, ExpenseForm.vehicle)
    dispatcher.message.register(expense_note, ExpenseForm.note)
    dispatcher.message.register(
        expense_confirmation,
        ExpenseForm.confirmation,
    )
    dispatcher.message.register(
        expense_cancel_confirmation,
        ExpenseCancellationForm.confirmation,
    )
    dispatcher.message.register(
        unknown_message,
        ExpenseHistoryForm.month,
    )
    dispatcher.message.register(
        unknown_message,
        ExpenseHistoryForm.filter,
    )
    dispatcher.message.register(
        unknown_message,
        ExpenseHistoryForm.car,
    )

    dispatcher.message.register(unknown_message)
    dispatcher.errors.register(error_handler)
    return dispatcher


def create_client_dispatcher(admin_bot: Bot) -> Dispatcher:
    dispatcher = Dispatcher()

    async def client_receipt_handler(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        await client_booking_receipt(
            message,
            state,
            bot,
            admin_bot=admin_bot,
        )

    dispatcher.callback_query.register(
        calendar_callback,
        F.data.startswith("calendar:"),
    )
    dispatcher.callback_query.register(
        client_calendar_callback,
        SimpleCalendarCallback.filter(),
    )
    dispatcher.callback_query.register(
        client_delivery_callback,
        F.data.startswith("client_delivery:"),
    )
    dispatcher.callback_query.register(
        client_booking_car_callback,
        F.data.startswith("client_booking:car:"),
    )
    dispatcher.callback_query.register(
        client_booking_confirm_callback,
        F.data == "client_booking:confirm",
    )
    dispatcher.callback_query.register(
        client_booking_cancel_callback,
        F.data == "client_booking:cancel",
    )
    dispatcher.callback_query.register(
        client_pulkovo_booking_callback,
        F.data == "client_booking:pulkovo",
    )
    dispatcher.callback_query.register(
        client_ai_booking_callback,
        F.data == "client_ai:book",
    )
    dispatcher.callback_query.register(
        client_ai_booking_callback,
        F.data == "client_booking:start",
    )
    dispatcher.message.register(client_start_handler, CommandStart())
    dispatcher.message.register(client_start_handler, Command("menu"))
    dispatcher.message.register(start_client_booking, Command("booking"))
    dispatcher.message.register(start_client_booking, Command("cars"))
    dispatcher.message.register(client_pulkovo_handler, Command("pulkovo"))
    dispatcher.message.register(client_rules_handler, Command("rules"))
    dispatcher.message.register(client_manager_handler, Command("manager"))
    dispatcher.message.register(
        client_fleet_handler,
        F.text == "🏎 Автопарк и цены",
    )
    dispatcher.message.register(
        start_client_booking,
        F.text == "📅 Забронировать онлайн",
    )
    dispatcher.message.register(
        client_pulkovo_handler,
        F.text == "✈️ Подача в Пулково",
    )
    dispatcher.message.register(
        client_rules_handler,
        F.text == "📋 Условия и залог",
    )
    dispatcher.message.register(
        client_manager_handler,
        F.text == "👤 Связь с менеджером",
    )
    dispatcher.message.register(
        client_cancel_flow,
        F.text.casefold().in_({"отмена", "❌ отменить / в меню"}),
    )
    dispatcher.message.register(client_cancel_flow, Command("cancel"))
    dispatcher.message.register(
        client_booking_date_text,
        ClientBookingState.start_date,
    )
    dispatcher.message.register(
        client_booking_date_text,
        ClientBookingState.end_date,
    )
    dispatcher.message.register(
        client_booking_name,
        ClientBookingState.client_name,
    )
    dispatcher.message.register(
        client_delivery_address,
        ClientBookingState.delivery_address,
    )
    dispatcher.message.register(
        client_booking_phone,
        ClientBookingState.phone,
    )
    dispatcher.message.register(client_ai_message)
    dispatcher.errors.register(error_handler)
    return dispatcher


async def run_fleet_bot(bot: Bot, client_bot: Bot) -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    dispatcher = create_dispatcher(client_bot=client_bot)
    scheduler = create_notification_scheduler(bot)
    catchup_task: asyncio.Task | None = None
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            colored_rows = await asyncio.to_thread(
                colorize_existing_booking_rows,
            )
            logger.info(
                "Booking row colors synchronized for %s existing rows",
                colored_rows,
            )
        except Exception:
            logger.exception(
                "Could not synchronize existing booking row colors",
            )
        scheduler.start()
        catchup_task = asyncio.create_task(
            send_missed_tomorrow_notifications(bot)
        )
        logger.info(
            "Daily tomorrow plan scheduled for 20:00 (%s)",
            NOTIFICATION_TIMEZONE_NAME,
        )
        logger.info(
            "Expiring-rental alerts scheduled every 5 minutes (%s)",
            NOTIFICATION_TIMEZONE_NAME,
        )
        logger.info(
            "Overdue-rental alerts scheduled every 5 minutes (%s)",
            NOTIFICATION_TIMEZONE_NAME,
        )
        logger.info(
            "Upcoming-booking alerts scheduled every 5 minutes (%s)",
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


async def run_client_bot(bot: Bot, admin_bot: Bot) -> None:
    if not CLIENT_BOT_TOKEN:
        raise RuntimeError("CLIENT_BOT_TOKEN is not configured.")
    dispatcher = create_client_dispatcher(admin_bot=admin_bot)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Client booking bot started")
        await dispatcher.start_polling(bot)
    except TelegramAPIError:
        logger.exception("Client bot Telegram API error")
        raise
    finally:
        with suppress(Exception):
            await bot.session.close()


async def run_bot() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not CLIENT_BOT_TOKEN:
        raise RuntimeError("CLIENT_BOT_TOKEN is not configured.")
    ensure_sheets_ready()
    fleet_bot = Bot(token=TOKEN)
    client_bot = Bot(token=CLIENT_BOT_TOKEN)
    try:
        await asyncio.gather(
            run_fleet_bot(fleet_bot, client_bot),
            run_client_bot(client_bot, fleet_bot),
        )
    finally:
        for bot in (fleet_bot, client_bot):
            with suppress(Exception):
                await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except SheetsStartupError as error:
        logger.error("Bot startup aborted: %s", error)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()
