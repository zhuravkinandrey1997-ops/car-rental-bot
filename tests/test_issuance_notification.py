import html
import re
import unittest

from main import build_issuance_notification


def _telegram_text(notification: str) -> str:
    """Remove presentation markup before checking the message content."""
    return html.unescape(re.sub(r"<[^>]+>", "", notification))


class IssuanceNotificationTest(unittest.TestCase):
    def test_issuance_amounts_and_payment_methods_are_reported_together(self):
        notification = build_issuance_notification(
            {
                "client_name": "Тестовый клиент",
                "price": 22000,
                "pay_type": "Наличные",
                "deposit": 50000,
                "deposit_pay_type": "Карта",
                "start_mileage": 10000,
                "issued_at": "2026-09-04 12:00",
            },
            booking_id=123,
            car={"Марка и Модель": "Тестовое авто", "Госномер": "А000АА"},
            manager_name="Тестовый менеджер",
        )

        message = _telegram_text(notification)
        self.assertIn("Аренда: 22 000 ₽ (Наличные)", message)
        self.assertIn("Залог: 50 000 ₽ (Карта)", message)


if __name__ == "__main__":
    unittest.main()