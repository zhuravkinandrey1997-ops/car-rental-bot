"""Telegram entry point for the car-rental management bot."""

from __future__ import annotations

import asyncio
import calendar
import datetime
import logging
import os
from contextlib import suppress

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
from dotenv import load_dotenv

from sheets_client import (
    add_new_booking,
    get_active_rentals,
    get_available_cars,
    get_car_by_id,
    is_manager_authorized,
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
    confirmation = State()


class ReturnForm(StatesGroup):
    booking = State()
    end_mileage = State()
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
) -> InlineKeyboardMarkup:
    minimum_date = minimum_date or datetime.date.today()
    year = year or minimum_date.year
    month = month or minimum_date.month
    minimum_month = (minimum_date.year, minimum_date.month)
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
                    if (previous_year, previous_month) > minimum_month
                    else "calendar:noop"
                ),
            ),
            InlineKeyboardButton(text="Сегодня", callback_data=f"calendar:{flow}:{stage}:{minimum_date.year}:{minimum_date.month}"),
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
            if current_date < minimum_date:
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
                KeyboardButton(text="Активные аренды"),
                KeyboardButton(text="Оформить возврат"),
            ],
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


def rental_choice_keyboard(items: list[dict]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for index, item in enumerate(items, start=1):
        builder.add(KeyboardButton(text=f"{index}. {_booking_label(item)}"))
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


def _booking_label(booking: dict) -> str:
    car = _value(booking, "Авто", "Название авто", "Модель")
    client = _value(booking, "Клиент", "Клиент ФИО", "Имя клиента")
    dates = (
        f"{_value(booking, 'Дата начала (ГГГГ-ММ-ДД)', 'Дата начала')} — "
        f"{_value(booking, 'Дата окончания (ГГГГ-ММ-ДД)', 'Дата окончания')}"
    )
    return f"{car} · {client} · {dates}"


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
    await message.answer(
        _calendar_prompt("availability", "start"),
        reply_markup=ReplyKeyboardRemove(),
    )


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
    await message.answer(
        _calendar_prompt("availability", "end", selected_date),
        reply_markup=calendar_keyboard(
            "availability",
            "end",
            minimum_date=selected_date,
        ),
    )


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


async def start_booking(message: Message, state: FSMContext) -> None:
    if not await ensure_manager(message):
        return
    await state.clear()
    await state.set_state(BookingForm.start_date)
    await message.answer(
        "Новая бронь.\n\n" + _calendar_prompt("booking", "start"),
        reply_markup=ReplyKeyboardRemove(),
    )


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
    await message.answer(
        _calendar_prompt("booking", "end", selected_date),
        reply_markup=calendar_keyboard(
            "booking",
            "end",
            minimum_date=selected_date,
        ),
    )


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


def _calendar_minimum_date(flow: str, stage: str, data: dict) -> datetime.date:
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
    minimum_date = _calendar_minimum_date(flow, stage, data)
    message = callback.message

    if len(parts) == 5 and parts[3] != "day":
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
            )
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
    if selected_date < minimum_date:
        await callback.answer("Эта дата недоступна для выбора.", show_alert=True)
        return

    selected_value = selected_date.isoformat()
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
        await message.edit_text(
            "Новая бронь.\n\n" + _calendar_prompt("booking", "end", selected_date),
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
    await message.answer("Введите итоговую стоимость аренды в RUB:")


async def booking_price(message: Message, state: FSMContext) -> None:
    price, error = _parse_int(message.text or "", "стоимость", minimum=0)
    if error:
        await message.answer(error)
        return
    await state.update_data(price=price)
    data = await state.get_data()
    await state.set_state(BookingForm.confirmation)
    await message.answer(
        "Проверьте данные брони:\n\n"
        f"Авто: {data['car_name']}\n"
        f"Период: {data['start_date']} — {data['end_date']}\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Стоимость: {price:,} RUB".replace(",", " ")
        + "\n\nСохранить бронь?",
        reply_markup=confirmation_keyboard(),
    )


async def booking_confirmation(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() != "Подтвердить":
        await cancel_flow(message, state)
        return
    data = await state.get_data()
    manager_id = message.from_user.id if message.from_user else 0
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
        )
    except Exception:
        logger.exception("Could not save booking")
        await state.clear()
        await message.answer("Не удалось сохранить бронь в Google Sheets.", reply_markup=main_keyboard())
        return
    await state.clear()
    await message.answer(
        f"Бронь №{booking_id} подтверждена и сохранена.",
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
        rentals = get_active_rentals()
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
        "Выберите аренду для оформления возврата:",
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
        _value(booking, "Цена", "Стоимость", "Итоговая стоимость", default="0"),
        "базовая стоимость",
        minimum=0,
    )
    if base_price is None:
        base_price = 0

    await state.update_data(
        booking=booking,
        car=car,
        start_mileage=start_mileage,
        base_price=base_price,
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
    await state.set_state(ReturnForm.extra_fee)
    await message.answer("Введите сумму дополнительных начислений в RUB (0, если нет):")


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
    await state.set_state(ReturnForm.pay_type)
    await message.answer("Введите способ оплаты (наличные, карта, перевод и т. п.):")


async def return_pay_type(message: Message, state: FSMContext) -> None:
    pay_type = (message.text or "").strip()
    if not pay_type:
        await message.answer("Укажите способ оплаты.")
        return
    await state.update_data(pay_type=pay_type)
    await state.set_state(ReturnForm.notes)
    await message.answer("Введите примечание или отправьте «нет»:")


async def return_notes(message: Message, state: FSMContext) -> None:
    notes = (message.text or "").strip()
    if notes.lower() == "нет":
        notes = ""
    await state.update_data(notes=notes)
    data = await state.get_data()
    await state.set_state(ReturnForm.confirmation)
    delta = int(data["end_mileage"]) - int(data["start_mileage"])
    total = int(data["base_price"]) + int(data["extra_fee"])
    await message.answer(
        "Проверьте возврат:\n\n"
        f"Бронь: №{_booking_id(data['booking'])}\n"
        f"Авто: {_value(data['booking'], 'Авто', 'Название авто', 'Модель')}\n"
        f"Пробег: {data['start_mileage']} → {data['end_mileage']} км "
        f"(+{delta} км)\n"
        f"Базовая стоимость: {int(data['base_price']):,} RUB\n"
        f"Доп. начисления: {int(data['extra_fee']):,} RUB\n"
        f"Итого к оплате: {total:,} RUB\n"
        f"Получено: {int(data['total_paid']):,} RUB\n"
        f"Оплата: {data['pay_type']}\n"
        f"Примечание: {notes or '—'}\n\n"
        "Сохранить возврат?".replace(",", " "),
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
    dispatcher.message.register(start_handler, CommandStart())
    dispatcher.message.register(show_menu, Command("menu"))
    dispatcher.message.register(cancel_flow, F.text.casefold() == "отмена")

    dispatcher.message.register(start_availability, F.text == "Доступные авто")
    dispatcher.message.register(start_booking, F.text == "Новая бронь")
    dispatcher.message.register(list_active_rentals, F.text == "Активные аренды")
    dispatcher.message.register(start_return, F.text == "Оформить возврат")

    dispatcher.message.register(availability_start_date, AvailabilityForm.start_date)
    dispatcher.message.register(availability_end_date, AvailabilityForm.end_date)

    dispatcher.message.register(booking_start_date, BookingForm.start_date)
    dispatcher.message.register(booking_end_date, BookingForm.end_date)
    dispatcher.message.register(booking_car, BookingForm.car)
    dispatcher.message.register(booking_client_name, BookingForm.client_name)
    dispatcher.message.register(booking_phone, BookingForm.phone)
    dispatcher.message.register(booking_price, BookingForm.price)
    dispatcher.message.register(booking_confirmation, BookingForm.confirmation)

    dispatcher.message.register(return_booking, ReturnForm.booking)
    dispatcher.message.register(return_end_mileage, ReturnForm.end_mileage)
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
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Car rental bot started")
        await dispatcher.start_polling(bot)
    except TelegramAPIError:
        logger.exception("Telegram API error")
        raise
    finally:
        with suppress(Exception):
            await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()