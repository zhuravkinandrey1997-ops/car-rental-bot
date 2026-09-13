import unittest
from unittest.mock import MagicMock, patch
import json

import main
import sheets_client


class _BookingsSheet:
    def __init__(self):
        self.values = [
            ["ID Брони", "Статус", "ФИО Клиента"],
            ["501", "Ожидает подтверждения", "Иван Иванов"],
        ]
        self.updated = []

    def get_all_values(self):
        return self.values

    def update_cell(self, row, column, value):
        self.updated.append((row, column, value))
        self.values[row - 1][column - 1] = value


class ClientBookingTest(unittest.TestCase):
    def test_client_welcome_keyboard_has_single_booking_entrypoint(self):
        buttons = [
            button.text
            for row in main.client_welcome_keyboard().keyboard
            for button in row
        ]
        self.assertEqual(buttons, ["🏎 Выбрать автомобиль"])

    def test_ai_booking_keyboard_has_booking_callback(self):
        button = main.client_ai_booking_keyboard().inline_keyboard[0][0]
        self.assertEqual(button.text, "🏎 Забронировать автомобиль")
        self.assertEqual(button.callback_data, "client_ai:book")

    def test_ai_fleet_context_contains_only_current_car_data(self):
        context = main._client_ai_fleet_context(
            [
                {
                    "Марка и Модель": "Porsche Panamera",
                    "Цена / сутки (RUB)": "25 000",
                }
            ]
        )
        self.assertIn("Porsche Panamera", context)
        self.assertIn("25 000 RUB/сутки", context)
        self.assertIn("Актуальный список активных автомобилей", context)

    def test_ai_system_prompt_contains_current_rental_rules(self):
        prompt = main.CLIENT_AI_SYSTEM_PROMPT
        for rule in (
            "Санкт-Петербург и Ленинградская область",
            "только по предварительному согласованию",
            "возраст от 21 года",
            "стаж вождения от 2 лет",
            "паспорт РФ",
            "водительское удостоверение",
            "Страховой депозит обязателен",
            "проверки штрафов",
            "Лимит суточного пробега уточняй у менеджера",
            "только Сбербанк, +79110326262",
            "листа\n«Автомобили» Google Таблицы",
        ):
            self.assertIn(rule, prompt)

    def test_openai_client_uses_requested_model_and_system_prompt(self):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {"message": {"content": "Здравствуйте! Подскажу условия аренды."}}
                ]
            }
        ).encode("utf-8")
        urlopen_result = MagicMock()
        urlopen_result.__enter__.return_value = response

        with (
            patch.object(main, "OPENAI_API_KEY", "test-key"),
            patch.object(main, "urlopen", return_value=urlopen_result) as urlopen,
        ):
            answer = main._openai_generate_content("Какие есть автомобили?")

        self.assertIn("Здравствуйте", answer)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][0]["content"], main.CLIENT_AI_SYSTEM_PROMPT)
        self.assertEqual(body["messages"][1]["content"], "Какие есть автомобили?")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_public_booking_helpers_calculate_days_and_prepayment(self):
        self.assertEqual(main._client_booking_days("2026-09-15", "2026-09-17"), 3)
        self.assertEqual(main._client_prepayment(100_000), 30_000)
        self.assertGreaterEqual(main._client_prepayment(1), 1)

    def test_payment_instruction_contains_official_details_and_receipt_requirement(self):
        instruction = main._client_payment_instruction(30_900)
        self.assertIn("Банк: Сбербанк", instruction)
        self.assertIn("Номер телефона: +79110326262", instruction)
        self.assertIn("Получатель: Лунева К.Д.", instruction)
        self.assertIn("Сумма к оплате: 30 900 RUB", instruction)
        self.assertIn("скриншот или PDF чека об оплате", instruction)
        self.assertIn("Предоставление чека фиксирует бронь за вами!", instruction)

    def test_delivery_keyboard_contains_required_fixed_options(self):
        labels = [
            button.text
            for row in main.client_delivery_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(
            labels,
            [
                "🏢 Заберу сам в офисе (Бесплатно)",
                "✈️ Аэропорт Пулково (+ 3 000 RUB)",
                "📍 Доставка по адресу в СПб (+ 3 000 RUB)",
            ],
        )

    def test_public_booking_uses_pending_status_and_site_source(self):
        with (
            patch.object(
                sheets_client,
                "get_available_cars",
                return_value=[{"ID": "7"}],
            ),
            patch.object(
                sheets_client,
                "add_new_booking",
                return_value=501,
            ) as add_booking,
        ):
            result = sheets_client.add_public_booking(
                car_id="7",
                car_name="Panamera",
                start_date="2026-09-15",
                end_date="2026-09-17",
                client_name="Иван Иванов",
                phone="+375291112233",
                client_telegram_id=123456,
                price=100_000,
                prepayment=30_000,
                delivery_address="Аэропорт",
                delivery_price=3_000,
            )

        self.assertEqual(result, 501)
        kwargs = add_booking.call_args.kwargs
        self.assertEqual(kwargs["status"], "Ожидает подтверждения")
        self.assertEqual(kwargs["source"], "Сайт")
        self.assertEqual(kwargs["manager_id"], 123456)
        self.assertEqual(kwargs["prepayment"], 30_000)
        self.assertEqual(kwargs["delivery_price"], 3_000)

    def test_public_booking_status_update_requires_pending_status(self):
        sheet = _BookingsSheet()
        with patch.object(sheets_client, "ws_bookings", sheet):
            updated = sheets_client.update_public_booking_status(
                "501",
                "Подтверждена",
            )

        self.assertEqual(updated["Статус"], "Подтверждена")
        self.assertEqual(sheet.updated, [(2, 2, "Подтверждена")])

        with self.assertRaises(ValueError):
            with patch.object(sheets_client, "ws_bookings", sheet):
                sheets_client.update_public_booking_status(
                    "501",
                    "Отклонена",
                )


if __name__ == "__main__":
    unittest.main()