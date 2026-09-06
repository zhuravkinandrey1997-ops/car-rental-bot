import unittest
from unittest.mock import patch
from types import SimpleNamespace

import sheets_client
from main import (
    BookingForm,
    ReturnForm,
    _partner_auto_booking_price,
    _partner_booking_card_text,
    _partner_issuance_card_text,
    _partner_return_card_text,
    booking_start_mileage,
    return_end_mileage,
)


FINANCIAL_MARKERS = (
    "стоимость",
    "залог",
    "предоплат",
    "оплат",
    "штраф",
    "удерж",
    "rub",
    "₽",
)


class FakeFSMState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.from_user = SimpleNamespace(id=123)
        self.responses = []

    async def answer(self, text, **kwargs):
        self.responses.append(text)
        return SimpleNamespace(message_id=len(self.responses))


class HeavyLuxPartnerBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def test_partner_cards_are_operational_only(self):
        booking = {
            "ID Брони": 42,
            "Источник": "heavy_lux_cr",
            "Автомобиль": "Heavy Lux SUV",
            "Госномер": "А001АА",
            "ФИО Клиента": "Тестовый клиент",
            "Телефон": "+79990000000",
            "Дата начала": "2026-09-05",
            "Дата окончания": "2026-09-07",
            "Статус": "В аренде",
            "Сумма брони (RUB)": 90000,
            "Задаток/Предоплата": 30000,
            "Способ оплаты задатка": "Карта",
            "Страховой залог": 100000,
            "Штраф": 5000,
        }
        car = {
            "Марка и Модель": "Heavy Lux SUV",
            "Госномер": "А001АА",
            "Уровень топлива": "Полный",
            "Цена / сутки (RUB)": 45000,
            "Базовый залог (RUB)": 100000,
        }
        issuance_data = {
            "car": car,
            "booking": booking,
            "client_name": "Тестовый клиент",
            "phone": "+79990000000",
            "issued_at": "2026-09-05 10:00",
            "planned_end_at": "2026-09-07 10:00",
            "start_mileage": 1000,
            "price": 90000,
            "deposit": 100000,
            "deposit_pay_type": "Карта",
        }
        return_data = {
            "booking": booking,
            "car": car,
            "returned_at": "2026-09-07 10:00",
            "start_mileage": 1000,
            "end_mileage": 1200,
            "fuel_level": "Полный",
            "cleanliness": "Чистая",
            "notes": "Без замечаний",
            "rental_total": 90000,
            "deposit_balance": 100000,
            "total_paid": 5000,
        }

        cards = (
            _partner_booking_card_text(booking, car),
            _partner_issuance_card_text(issuance_data),
            _partner_return_card_text(return_data),
        )
        for card in cards:
            normalized = card.casefold()
            self.assertFalse(
                any(marker in normalized for marker in FINANCIAL_MARKERS),
                card,
            )

    def test_partner_booking_price_is_derived_from_daily_tariff(self):
        self.assertEqual(
            _partner_auto_booking_price(
                {
                    "car": {"Цена / сутки (RUB)": "45 000"},
                    "start_date": "2026-09-05",
                    "end_date": "2026-09-07",
                }
            ),
            135000,
        )
        self.assertIsNone(
            _partner_auto_booking_price(
                {
                    "car": {},
                    "start_date": "2026-09-05",
                    "end_date": "2026-09-07",
                }
            )
        )

    def test_partner_active_rentals_use_the_partner_source(self):
        with patch.object(
            sheets_client,
            "get_partner_bookings",
            return_value=[
                {"ID Брони": 1, "Источник": "heavy_lux_cr", "Статус": "В аренде"},
                {"ID Брони": 2, "Источник": "heavy_lux_cr", "Статус": "Завершена"},
            ],
        ):
            rentals = sheets_client.get_partner_active_rentals("heavy_lux_cr")

        self.assertEqual([rental["ID Брони"] for rental in rentals], [1])

    def test_partner_bookings_match_the_requested_source(self):
        class FakeBookingsSheet:
            def get_all_values(self):
                return [
                    ["ID Брони", "Источник"],
                    [1, "heavy_lux_cr"],
                    [2, "other"],
                ]

        with patch.object(sheets_client, "ws_bookings", FakeBookingsSheet()):
            bookings = sheets_client.get_partner_bookings("heavy_lux_cr")

        self.assertEqual([booking["ID Брони"] for booking in bookings], [1])

    async def test_partner_issuance_skips_payment_states(self):
        state = FakeFSMState(
            {
                "partner_mode": True,
                "booking_mode": "issuance",
                "car": {
                    "Марка и Модель": "Heavy Lux SUV",
                    "Госномер": "А001АА",
                },
                "booking": {"ID Брони": 42},
                "client_name": "Тестовый клиент",
                "phone": "+79990000000",
                "issued_at": "2026-09-05 10:00",
                "planned_end_at": "2026-09-07 10:00",
            }
        )
        message = FakeMessage("1000")

        await booking_start_mileage(message, state)

        self.assertEqual(state.state, BookingForm.confirmation.state)
        self.assertNotIn(
            state.state,
            {
                BookingForm.pay_type.state,
                BookingForm.deposit.state,
                BookingForm.deposit_pay_type.state,
            },
        )
        self.assertEqual(state.data["deposit_pay_type"], "Без оплаты")

    async def test_partner_return_skips_payment_states(self):
        state = FakeFSMState({"partner_mode": True, "start_mileage": 1000})
        message = FakeMessage("1200")

        await return_end_mileage(message, state)

        self.assertEqual(state.state, ReturnForm.cleanliness.state)
        self.assertNotIn(
            state.state,
            {
                ReturnForm.rental_paid.state,
                ReturnForm.rental_pay_type.state,
                ReturnForm.total_paid.state,
                ReturnForm.pay_type.state,
            },
        )


if __name__ == "__main__":
    unittest.main()