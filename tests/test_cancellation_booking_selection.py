import unittest
from unittest.mock import patch

import sheets_client
from main import (
    cancellation_booking_keyboard,
    cancellation_confirmation_keyboard,
)


class _Sheet:
    def __init__(self, values=None, records=None):
        self.values = values or []
        self.records = records or []

    def get_all_values(self):
        return self.values

    def get_all_records(self):
        return self.records


class CancellationBookingSelectionTest(unittest.TestCase):
    def test_cancellable_cars_come_from_confirmed_bookings_not_car_status(self):
        bookings = _Sheet(
            values=[
                ["ID Брони", "ID Авто", "Автомобиль", "Статус"],
                ["105", "7", "Panamera", "Подтверждена"],
                ["108", "7", "Panamera", "Подтверждена"],
                ["109", "7", "Panamera", "Подтверждена"],
                ["120", "7", "Panamera", "Подтверждена"],
                ["121", "8", "S500", "Подтверждена"],
                ["122", "9", "Maybach", "Отменена"],
            ]
        )
        cars = _Sheet(
            records=[
                {"ID": "7", "Марка и Модель": "Panamera", "Госномер": "P-7", "Статус": "В аренде"},
                {"ID": "8", "Марка и Модель": "S500", "Госномер": "S-8", "Статус": "Активен"},
            ]
        )

        with (
            patch.object(sheets_client, "ws_bookings", bookings),
            patch.object(sheets_client, "ws_cars", cars),
        ):
            result = sheets_client.get_cancellable_cars()

        self.assertEqual([car["ID"] for car in result], ["7", "8"])
        self.assertEqual(result[0]["Статус"], "В аренде")

    def test_booking_buttons_and_confirmation_buttons_are_explicit(self):
        keyboard = cancellation_booking_keyboard(
            [
                {
                    "ID Брони": "105",
                    "Дата начала": "2026-09-15",
                    "Дата окончания": "2026-09-18",
                    "ФИО Клиента": "Иван Иванов",
                }
            ]
        )
        booking_button = keyboard.inline_keyboard[0][0]
        self.assertIn("#105:", booking_button.text)
        self.assertIn("Иван Иванов", booking_button.text)
        self.assertEqual(booking_button.callback_data, "cancel_booking:0")

        confirmation = cancellation_confirmation_keyboard()
        self.assertEqual(
            confirmation.inline_keyboard[0][0].callback_data,
            "cancel_booking_confirm",
        )
        self.assertEqual(
            confirmation.inline_keyboard[0][1].callback_data,
            "cancel_booking_back",
        )


if __name__ == "__main__":
    unittest.main()