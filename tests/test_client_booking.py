import unittest
from unittest.mock import AsyncMock, patch

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


class _FakeState:
    def __init__(self):
        self.clear_calls = 0

    async def clear(self):
        self.clear_calls += 1


class _FakeMessage:
    from_user = type("User", (), {"id": 123456})()

    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class ClientBookingTest(unittest.IsolatedAsyncioTestCase):
    def test_client_welcome_keyboard_has_persistent_public_menu(self):
        buttons = [
            button.text
            for row in main.client_welcome_keyboard().keyboard
            for button in row
        ]
        self.assertEqual(
            buttons,
            [
                "🏎 Автопарк и цены",
                "📅 Забронировать онлайн",
                "✈️ Подача в Пулково",
                "📋 Условия и залог",
                "👤 Связь с менеджером",
            ],
        )
        self.assertTrue(main.client_welcome_keyboard().is_persistent)

    def test_ai_booking_keyboard_has_booking_callback(self):
        button = main.client_ai_booking_keyboard().inline_keyboard[0][0]
        self.assertEqual(button.text, "🏎 Забронировать автомобиль")
        self.assertEqual(button.callback_data, "client_ai:book")

    def test_local_faq_matches_keywords_without_case_sensitivity(self):
        self.assertEqual(
            main._client_local_faq_response("Какой ДЕПОЗИТ и когда возврат?"),
            "Страховой депозит обязателен для каждого автомобиля, зависит от выбранной модели и возвращается в полном объеме после проверки штрафов.",
        )
        self.assertEqual(
            main._client_local_faq_response("Какие документы нужны и какой возраст?"),
            "Требования к арендатору: возраст от 21 года, водительский стаж от 2 лет. Для оформления требуются паспорт РФ и водительское удостоверение.",
        )
        self.assertEqual(
            main._client_local_faq_response("Есть доставка в ПУЛКОВО?"),
            "Доставка автомобилей по Санкт-Петербургу и встреча в аэропорту Пулково осуществляются круглосуточно (24/7). Стоимость и время подачи согласовываются при оформлении.",
        )
        self.assertEqual(
            main._client_local_faq_response("Какие машины сейчас свободны?"),
            "Актуальный автопарк и занятость синхронизируются в реальном времени. Нажмите кнопку ниже, чтобы открыть каталог и выбрать свободный автомобиль на ваши даты.",
        )
        self.assertEqual(
            main._client_local_faq_response("Куда делать перевод?"),
            "Официальные реквизиты для бронирования и депозита: Сбербанк +79110326262.",
        )

    def test_local_faq_uses_fallback_for_unknown_questions(self):
        self.assertEqual(
            main._client_local_faq_response("Можно ли арендовать машину с собакой?"),
            main.CLIENT_FAQ_FALLBACK,
        )

    async def test_client_escape_handlers_clear_fsm_state(self):
        state = _FakeState()
        message = _FakeMessage()
        await main.client_start_handler(message, state)
        await main.client_cancel_flow(message, state)
        await main.client_manager_handler(message, state)
        self.assertEqual(state.clear_calls, 3)

    async def test_menu_clears_fsm_before_access_check(self):
        state = _FakeState()
        message = _FakeMessage()
        with (
            patch.object(main, "is_partner_user", return_value=False),
            patch.object(
                main,
                "ensure_manager",
                new=AsyncMock(return_value=False),
            ),
        ):
            await main.show_menu(message, state)
        self.assertEqual(state.clear_calls, 1)

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
                "✈️ Подача в Пулково (+ 3 000 RUB)",
                "🏢 Забрать в офисе",
                "❌ Отменить / В меню",
            ],
        )

    def test_client_car_card_contains_required_catalog_fields(self):
        card = main._client_fleet_card_text(
            {
                "Марка и Модель": "BMW 5",
                "Класс": "Бизнес",
                "Цена / сутки (RUB)": "12 000",
                "Залог (RUB)": "50 000",
                "Суточный пробег": "300",
            }
        )
        self.assertIn("BMW 5", card)
        self.assertIn("Класс: Бизнес", card)
        self.assertIn("Цена: 12 000 RUB/сутки", card)
        self.assertIn("Залог: 50 000 RUB", card)
        self.assertIn("Суточный пробег: 300 км", card)

    async def test_client_fleet_loader_includes_rented_cars_but_excludes_retired(self):
        with patch.object(
            main,
            "get_all_cars",
            return_value=[
                {"ID": "1", "Марка и Модель": "Urus", "Статус": "В аренде"},
                {"ID": "2", "Марка и Модель": "Maybach", "Статус": "Активен"},
                {"ID": "3", "Марка и Модель": "Списанный авто", "Статус": "Списан"},
                {"ID": "4", "Марка и Модель": "Ремонтный авто", "Статус": "В ремонте"},
            ],
        ):
            cars = await main._load_client_active_cars()

        self.assertEqual(
            [car["Марка и Модель"] for car in cars],
            ["Urus", "Maybach"],
        )

    def test_client_date_states_only_offer_calendar(self):
        calendar = main._client_calendar("start")
        self.assertIn("Отменить", calendar._labels.cancel_caption)
        self.assertEqual(calendar._labels.months[0], "Январь")
        self.assertEqual(
            main.ClientBookingState.confirmation.state,
            "ClientBookingState:confirmation",
        )

    def test_client_summary_has_confirmation_and_cancel_actions(self):
        labels = [
            button.text
            for row in main.client_booking_confirmation_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(labels, ["✅ Подтвердить заявку", "❌ Отменить / В меню"])

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