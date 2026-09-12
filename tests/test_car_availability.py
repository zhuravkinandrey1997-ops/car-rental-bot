import unittest
from unittest.mock import patch

import sheets_client
from main import car_choice_keyboard


class _CarsSheet:
    def __init__(self, rows):
        self.rows = rows

    def get_all_records(self):
        headers, *data = self.rows
        return [dict(zip(headers, row)) for row in data]


class _BookingsSheet:
    def __init__(self, rows):
        self.rows = rows

    def get_all_values(self):
        return self.rows


class CarAvailabilityTest(unittest.TestCase):
    def test_future_dates_ignore_finished_current_rental_and_keep_all_non_blocked_cars(
        self,
    ):
        cars = _CarsSheet(
            [
                ["ID", "Марка и Модель", "Госномер", "Статус"],
                ["7", "Porsche Panamera", "7882 TP-7", "В аренде"],
                ["8", "Porsche Panamera", "4601 AH-8", "Активен"],
                ["9", "Maybach", "MAY-1", "В ремонте"],
                ["10", "S500", "S500-1", "Списан"],
            ]
        )
        bookings = _BookingsSheet(
            [
                ["ID Брони", "ID Авто", "Дата начала", "Дата окончания", "Статус"],
                ["105", "7", "2026-09-10", "2026-09-13", "В аренде"],
                ["106", "8", "2026-09-14", "2026-09-18", "Подтверждена"],
            ]
        )

        with (
            patch.object(sheets_client, "ws_cars", cars),
            patch.object(sheets_client, "ws_bookings", bookings),
        ):
            available = sheets_client.get_available_cars(
                "2026-09-14",
                "2026-09-18",
            )

        self.assertEqual([car["ID"] for car in available], ["7"])
        self.assertEqual(available[0]["Госномер"], "7882 TP-7")

    def test_car_choice_keyboard_contains_every_car(self):
        cars = [
            {
                "Марка и Модель": name,
                "Госномер": f"PLATE-{index}",
            }
            for index, name in enumerate(
                [
                    "Panamera 4601 AH-8",
                    "Panamera 7882 TP-7",
                    "Maybach",
                    "S500",
                    "Jaguar F-Type",
                ],
                start=1,
            )
        ]

        keyboard = car_choice_keyboard(cars)
        labels = [row[0].text for row in keyboard.keyboard]

        self.assertEqual(len(labels), len(cars) + 1)
        self.assertEqual(labels[-1], "Отмена")
        self.assertEqual(
            [label.split(". ", 1)[1].split(" (", 1)[0] for label in labels[:-1]],
            [car["Марка и Модель"] for car in cars],
        )


if __name__ == "__main__":
    unittest.main()