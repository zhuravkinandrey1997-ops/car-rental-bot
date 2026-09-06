import datetime
import unittest
from unittest.mock import patch

import main


class ExpiringRentalNotificationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.EXPIRING_RENTAL_ALERTED_BOOKING_IDS.clear()
        self.current = datetime.datetime(
            2026,
            9,
            6,
            12,
            0,
            tzinfo=main.NOTIFICATION_TZ,
        )

    def tearDown(self):
        main.EXPIRING_RENTAL_ALERTED_BOOKING_IDS.clear()

    def _booking(self, booking_id=1, minutes_left=60, **overrides):
        booking = {
            "ID Брони": booking_id,
            "Статус": "В аренде",
            "Источник": "manager",
            "ФИО Клиента": "Тестовый клиент",
            "Телефон": "+79990000000",
            "Плановое окончание": (
                self.current + datetime.timedelta(minutes=minutes_left)
            ).strftime("%Y-%m-%d %H:%M"),
            "__car_name__": "Тестовый автомобиль",
            "__car_plate__": "А000АА",
        }
        booking.update(overrides)
        return booking

    async def _run_check(self, bookings):
        deliveries = []

        async def capture_delivery(bot, recipient_id, chunks):
            deliveries.append((recipient_id, chunks))

        with (
            patch.object(main, "get_active_rentals", return_value=bookings),
            patch.object(
                main,
                "_send_notification_chunks",
                new=capture_delivery,
            ),
        ):
            await main.check_expiring_rentals(object(), now=self.current)

        return deliveries

    async def test_sends_at_each_boundary_of_the_50_to_70_minute_window(self):
        for minutes_left in (50, 60, 70):
            with self.subTest(minutes_left=minutes_left):
                main.EXPIRING_RENTAL_ALERTED_BOOKING_IDS.clear()
                deliveries = await self._run_check(
                    [self._booking(booking_id=minutes_left, minutes_left=minutes_left)]
                )

                self.assertEqual(
                    len(deliveries),
                    len(main.NOTIFY_MANAGERS),
                )
                self.assertEqual(
                    {recipient_id for recipient_id, _ in deliveries},
                    set(main.NOTIFY_MANAGERS),
                )

    async def test_does_not_send_outside_the_50_to_70_minute_window(self):
        for minutes_left in (49, 71):
            with self.subTest(minutes_left=minutes_left):
                main.EXPIRING_RENTAL_ALERTED_BOOKING_IDS.clear()
                deliveries = await self._run_check(
                    [self._booking(booking_id=minutes_left, minutes_left=minutes_left)]
                )

                self.assertEqual(deliveries, [])

    async def test_same_booking_is_not_notified_twice(self):
        booking = self._booking(booking_id=42, minutes_left=60)
        first_deliveries = await self._run_check([booking])
        second_deliveries = await self._run_check([booking])

        self.assertEqual(len(first_deliveries), len(main.NOTIFY_MANAGERS))
        self.assertEqual(second_deliveries, [])

    async def test_heavy_lux_booking_also_reaches_partner_without_financial_fields(self):
        booking = self._booking(
            booking_id=77,
            minutes_left=60,
            **{"Источник": "heavy_lux_cr"},
        )
        deliveries = await self._run_check([booking])

        recipient_ids = [recipient_id for recipient_id, _ in deliveries]
        self.assertEqual(
            recipient_ids,
            [*main.NOTIFY_MANAGERS, main.PARTNER_ID],
        )
        self.assertTrue(all(chunks == deliveries[0][1] for _, chunks in deliveries))
        notification = deliveries[0][1][0].casefold()
        for financial_marker in ("стоимость", "залог", "предоплат", "штраф", "₽"):
            self.assertNotIn(financial_marker, notification)

    async def test_skips_completed_bookings_and_invalid_planned_end(self):
        bookings = [
            self._booking(
                booking_id=100,
                minutes_left=60,
                **{"Статус": "Завершена"},
            ),
            self._booking(
                booking_id=101,
                **{"Плановое окончание": "not-a-date"},
            ),
            self._booking(
                booking_id=102,
                **{"Плановое окончание": ""},
            ),
        ]

        deliveries = await self._run_check(bookings)

        self.assertEqual(deliveries, [])
        self.assertEqual(main.EXPIRING_RENTAL_ALERTED_BOOKING_IDS, set())


if __name__ == "__main__":
    unittest.main()