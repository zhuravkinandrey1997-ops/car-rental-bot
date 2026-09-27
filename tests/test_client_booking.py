import unittest
import datetime
from types import SimpleNamespace
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
        self.current_state = None

    async def clear(self):
        self.clear_calls += 1

    async def set_state(self, state):
        self.current_state = state.state


class _FakeMessage:
    from_user = type(
        "User",
        (),
        {
            "id": 123456,
            "full_name": "Иван Иванов",
            "username": "ivan",
        },
    )()

    def __init__(self):
        self.answers = []
        self.photos = []
        self.contact = None
        self.text = None

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def answer_photo(self, photo, caption, **kwargs):
        self.photos.append((photo, caption, kwargs))


class _BookingState:
    def __init__(self, data=None):
        self.data = data or {}
        self.cleared = False
        self.current_state = main.ClientBookingState.confirmation.state

    async def get_state(self):
        return self.current_state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, state):
        self.current_state = state.state

    async def clear(self):
        self.cleared = True


class ClientBookingTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_rules_uses_structured_html_and_persistent_menu(self):
        message = _FakeMessage()
        state = _FakeState()

        await main.client_rules_handler(message, state)

        self.assertEqual(state.clear_calls, 1)
        text, kwargs = message.answers[-1]
        self.assertIn("📋 <b>Условия аренды в Leon Rental</b>", text)
        self.assertIn("Аренда доступна <b>с 18 лет</b>", text)
        self.assertIn("250 км/сутки", text)
        self.assertIn("«👤 Связь с менеджером»", text)
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertIs(kwargs["reply_markup"].__class__, main.ReplyKeyboardMarkup)

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
                "📋 Условия и залог",
                "👤 Связь с менеджером",
            ],
        )
        self.assertTrue(main.client_welcome_keyboard().is_persistent)

    async def test_client_start_sends_html_welcome_with_persistent_menu(self):
        message = _FakeMessage()
        state = _FakeState()

        await main.client_start_handler(message, state)

        text, kwargs = message.answers[-1]
        self.assertEqual(
            text,
            "✨ <b>Добро пожаловать в Leon Rental!</b>\n\n"
            "Премиальный автопарк в Санкт-Петербурге: спорткары, "
            "представительские седаны и люксовые внедорожники без лишних формальностей.\n\n"
            "• <b>Идеальное состояние:</b> авто передаются чистыми и с полным баком.\n"
            "• <b>Прозрачные тарифы:</b> автоматическая скидка при аренде от 2 суток.\n"
            "• <b>Подача 24/7:</b> доставим автомобиль к терминалу Пулково, "
            "в отель или к дому.\n\n"
            "Выберите автомобиль в каталоге или забронируйте поездку онлайн "
            "через меню ниже 👇",
        )
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertTrue(kwargs["reply_markup"].is_persistent)
        self.assertEqual(
            [
                [button.text for button in row]
                for row in kwargs["reply_markup"].keyboard
            ],
            [
                ["🏎 Автопарк и цены", "📅 Забронировать онлайн"],
                ["📋 Условия и залог", "👤 Связь с менеджером"],
            ],
        )

    async def test_manager_contact_starts_contact_fsm_with_html_prompt(self):
        message = _FakeMessage()
        state = _FakeState()
        await main.client_manager_handler(message, state)

        self.assertEqual(
            state.current_state,
            main.ManagerContactState.waiting_for_contact.state,
        )
        text, kwargs = message.answers[-1]
        self.assertIn("📞 <b>Связь с менеджером Leon Rental</b>", text)
        self.assertIn("<b>номер телефона</b>", text)
        self.assertEqual(kwargs["parse_mode"], "HTML")
        buttons = [
            button
            for row in kwargs["reply_markup"].keyboard
            for button in row
        ]
        self.assertEqual(
            [button.text for button in buttons],
            ["📱 Отправить мой номер телефона", "❌ Отмена"],
        )
        self.assertTrue(buttons[0].request_contact)

    async def test_manager_contact_submission_notifies_fleet_and_returns_menu(self):
        message = _FakeMessage()
        state = _FakeState()
        message.contact = SimpleNamespace(phone_number="+79112223344")
        fleet_bot = SimpleNamespace(send_message=AsyncMock())

        with patch.object(
            main,
            "_booking_notification_ids",
            new=AsyncMock(return_value=[987654]),
        ):
            await main.client_manager_contact_input(
                message,
                state,
                fleet_bot,
            )

        self.assertEqual(state.clear_calls, 1)
        self.assertIn("✅ <b>Запрос отправлен!</b>", message.answers[-1][0])
        self.assertEqual(message.answers[-1][1]["parse_mode"], "HTML")
        self.assertTrue(message.answers[-1][1]["reply_markup"].is_persistent)
        fleet_bot.send_message.assert_awaited_once()
        notification = fleet_bot.send_message.await_args.kwargs
        self.assertEqual(notification["chat_id"], 987654)
        self.assertEqual(notification["parse_mode"], "HTML")
        self.assertIn(
            "🔔 <b>ЗАПРОС СВЯЗИ С МЕНЕДЖЕРОМ!</b>",
            notification["text"],
        )
        self.assertIn("• <b>Имя:</b> Иван Иванов", notification["text"])
        self.assertIn(
            "• <b>Контакт:</b> <code>+79112223344</code>",
            notification["text"],
        )
        self.assertIn(
            "• <b>Telegram:</b> @ivan (ID: <code>123456</code>)",
            notification["text"],
        )
        self.assertRegex(
            notification["text"],
            r"• <b>Время:</b> \d{2}\.\d{2}\.\d{4} \d{2}:\d{2} МСК",
        )

    async def test_manager_contact_submission_survives_manager_send_error(self):
        message = _FakeMessage()
        state = _FakeState()
        message.text = "@ivan"
        fleet_bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=RuntimeError("blocked by Telegram"))
        )

        with patch.object(
            main,
            "_booking_notification_ids",
            new=AsyncMock(return_value=[987654]),
        ):
            await main.client_manager_contact_input(message, state, fleet_bot)

        self.assertEqual(len(message.answers), 1)
        self.assertIn("✅ <b>Запрос отправлен!</b>", message.answers[0][0])
        self.assertEqual(state.clear_calls, 1)

    def test_ai_booking_keyboard_has_booking_callback(self):
        button = main.client_ai_booking_keyboard().inline_keyboard[0][0]
        self.assertEqual(button.text, "🏎 Забронировать автомобиль")
        self.assertEqual(button.callback_data, "client_ai:book")

    def test_client_booking_selection_buttons_include_year_and_daily_price(self):
        buttons = [
            button
            for row in main.client_fleet_selection_keyboard(
                [
                    {
                        "ID": "1",
                        "Марка и Модель": "Jaguar F-Type",
                        "Год": "2020",
                        "Цена / сутки (RUB)": "22000",
                    },
                    {
                        "ID": "2",
                        "Марка и Модель": "Mercedes-Benz Maybach S580 4Matic",
                        "Год": "2022",
                        "Цена / сутки (RUB)": "50000",
                    },
                    {
                        "ID": "3",
                        "Марка и Модель": "Porsche Cayenne",
                        "Цена / сутки (RUB)": "18000",
                    },
                ]
            ).inline_keyboard
            for button in row
        ]

        self.assertEqual(
            [button.text for button in buttons[:3]],
            [
                "📅 Jaguar F-Type (2020) — 22 000 ₽/сут",
                "📅 MB Maybach S580 4Matic (2022) — 50 000 ₽/сут",
                "📅 Porsche Cayenne — 18 000 ₽/сут",
            ],
        )
        self.assertEqual(
            [button.callback_data for button in buttons[:3]],
            [
                "client_booking:car:1",
                "client_booking:car:2",
                "client_booking:car:3",
            ],
        )

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
        message.text = "❌ Отмена"
        await main.client_manager_contact_input(message, state, None)
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
        self.assertEqual(main._client_booking_days("2026-09-15", "2026-09-17"), 2)
        self.assertEqual(main._client_booking_days("2026-09-15", "2026-09-15"), 1)
        self.assertEqual(main._client_prepayment(100_000), 30_000)
        self.assertGreaterEqual(main._client_prepayment(1), 1)

    def test_client_tariffs_select_by_period_and_show_savings(self):
        car = {
            "Цена / сутки (RUB)": "25 000",
            "Тариф 1 день": "25000",
            "Тариф 2-3 дня": "23500",
            "Тариф 4-6 дней": "22000",
            "Тариф 7-15 дней": "21500",
            "Тариф 16-30 дней": "16000",
        }

        self.assertEqual(main._car_rate_for_days(car, 1), 25_000)
        self.assertEqual(main._car_rate_for_days(car, 3), 23_500)
        self.assertEqual(main._car_rate_for_days(car, 6), 22_000)
        self.assertEqual(main._car_rate_for_days(car, 15), 21_500)
        self.assertEqual(main._car_rate_for_days(car, 30), 16_000)
        self.assertEqual(
            main._client_period_pricing_text(
                "2026-09-15",
                "2026-09-18",
                3,
                23_500,
                70_500,
                4_500,
            ),
            "Период: 15.09.2026 — 18.09.2026 (3 сут.)\n"
            "Суточный тариф: 23 500 RUB/сутки\n"
            "Итого аренда: 70 500 RUB\n"
            "🔥 Скидка за срок аренды: экономия 4 500 RUB!",
        )

    def test_ford_tariffs_use_shared_sheet_day_boundaries(self):
        car = {
            "Цена / сутки (RUB)": "20000",
            "Тариф 1 день": "20000",
            "Тариф 2-3 дня": "20000",
            "Тариф 4-6 дней": "18000",
            "Тариф 7-15 дней": "16000",
            "Тариф 16-30 дней": "14000",
        }

        for days, expected_rate in (
            (1, 20_000),
            (3, 20_000),
            (4, 18_000),
            (6, 18_000),
            (7, 16_000),
            (8, 16_000),
            (15, 16_000),
            (16, 14_000),
            (30, 14_000),
        ):
            with self.subTest(days=days):
                self.assertEqual(
                    main._car_rate_for_days(car, days),
                    expected_rate,
                )
        self.assertEqual(
            main._client_fleet_price_range_text(car),
            "от 14 000 до 20 000 RUB/сутки",
        )

    def test_client_delivery_step_uses_readable_html_blocks(self):
        text = main._client_delivery_step_text(
            {
                "Марка и Модель": "Jaguar F-Type",
                "Год": "2020",
            },
            "2026-09-15",
            "2026-09-18",
            3,
            23_500,
            70_500,
            4_500,
        )
        self.assertIn("🏎 <b>Jaguar F-Type</b> (2020 г.)", text)
        self.assertIn("📅 <b>Период аренды:</b>", text)
        self.assertIn("• с 15.09.2026 по 18.09.2026 (3 сут.)", text)
        self.assertIn("💰 <b>Финансовый расчет:</b>", text)
        self.assertIn("• Суточный тариф: <b>23 500 ₽/сут</b>", text)
        self.assertIn("• Общая сумма: <b>70 500 ₽</b>", text)
        self.assertIn(
            "🔥 <i>Скидка за срок аренды составила: 4 500 ₽</i>",
            text,
        )
        self.assertTrue(text.endswith(
            "📍 <b>Шаг 3 из 5.</b> Выберите способ получения автомобиля:"
        ))

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
                "✈️ Подача в Пулково (+3 000 ₽)",
                "📍 Доставка по адресу (+3 000 ₽)",
                "🏢 Забрать в офисе (Бесплатно)",
                "❌ Отменить бронирование",
            ],
        )

    def test_client_car_card_contains_required_catalog_fields(self):
        card = main._client_fleet_card_text(
            {
                "Марка и Модель": "BMW 5",
                "Год выпуска": "2024",
                "Класс": "Бизнес",
                "Цена / сутки (RUB)": "12 000",
                "Залог": "50000.0",
                "Суточный пробег": "300",
            }
        )
        self.assertIn("BMW 5", card)
        self.assertIn("2024 г.", card)
        self.assertIn("Класс: Бизнес", card)
        self.assertIn("Стоимость: от 12 000 до 12 000 RUB/сутки", card)
        self.assertIn("Страховой залог: 50 000 RUB", card)
        self.assertIn("Суточный пробег: 300 км", card)

    def test_client_car_card_shows_engine_and_age_with_shared_tariffs(self):
        car = {
            "Марка и Модель": "Ford Mustang",
            "Год": "2020",
            "Класс": "Маслкар / Спорт",
            "Двигатель": "5.0 бензин (V8)",
            "Цена / сутки (RUB)": "20000",
            "Тариф 1 день": "20000",
            "Тариф 2-3 дня": "20000",
            "Тариф 4-6 дней": "18000",
            "Тариф 7-15 дней": "16000",
            "Тариф 16-30 дней": "14000",
            "Залог": "30000",
            "Суточный пробег": "250",
            "Возраст от (лет)": "21",
        }

        card = main._client_fleet_card_text(car)
        caption = main._client_fleet_card_caption(car)

        self.assertIn("Ford Mustang (2020 г.)", card)
        self.assertIn("Двигатель: 5.0 бензин (V8)", card)
        self.assertIn("Стоимость: от 14 000 до 20 000 RUB/сутки", card)
        self.assertIn("Страховой залог: 30 000 RUB", card)
        self.assertIn("Суточный пробег: 250 км", card)
        self.assertIn("Возрастное ограничение: с 21 года", card)
        self.assertNotIn("Тарифы:", card)
        self.assertIn("<b>Двигатель:</b> 5.0 бензин (V8)", caption)
        self.assertIn(
            "<b>Стоимость:</b> от 14 000 до 20 000 RUB/сутки",
            caption,
        )
        self.assertIn("<b>Возрастное ограничение:</b> с 21 года", caption)

    def test_client_car_characteristics_use_exact_sheet_columns_and_defaults(self):
        self.assertEqual(
            main._client_car_deposit_text({"Залог": "50000.0"}),
            "50 000 RUB",
        )
        self.assertEqual(
            main._client_car_deposit_text({"Залог": "по договору"}),
            "по договору",
        )
        self.assertEqual(
            main._client_car_deposit_text({}),
            "от 15 000 RUB",
        )
        self.assertEqual(
            main._client_car_mileage_text({"Суточный пробег": "350"}),
            "350 км",
        )
        self.assertEqual(
            main._client_car_mileage_text({}),
            "250 км",
        )
        self.assertNotIn(
            "уточняется",
            main._client_fleet_card_text({}),
        )

    def test_client_fleet_caption_contains_premium_html_format(self):
        caption = main._client_fleet_card_caption(
            {
                "ID": "1",
                "Марка и Модель": "Lamborghini Urus",
                "Год": "2023",
                "Класс": "Премиум",
                "Цена / сутки (RUB)": "25 000",
                "Залог": "150000",
                "Суточный пробег": "300",
            }
        )
        self.assertIn("🏎 <b>Lamborghini Urus</b> (2023 г.)", caption)
        self.assertIn(
            "• <b>Стоимость:</b> от 25 000 до 25 000 RUB/сутки",
            caption,
        )
        self.assertIn("• <b>Страховой залог:</b> 150 000 RUB", caption)

    def test_client_photo_sources_prioritize_url_and_find_model_asset(self):
        car = {
            "ID": "1",
            "Марка и Модель": "Lamborghini Urus",
            "Фото": "https://example.com/urus.jpg",
        }
        sources = main._client_car_photo_sources(car)
        self.assertEqual(sources[0], "https://example.com/urus.jpg")
        self.assertTrue(any(str(source).endswith("assets/cars/urus.jpg") for source in sources))

    async def test_client_fleet_handler_sends_photo_with_booking_markup(self):
        car = {
            "ID": "1",
            "Марка и Модель": "Lamborghini Urus",
            "Год": "2023",
            "Класс": "Премиум",
            "Цена / сутки (RUB)": "25 000",
            "Залог": "150000",
            "Суточный пробег": "300",
        }
        message = _FakeMessage()
        state = _FakeState()
        with patch.object(main, "_load_client_active_cars", return_value=[car]):
            await main.client_fleet_handler(message, state)

        self.assertEqual(len(message.photos), 1)
        _, caption, photo_kwargs = message.photos[0]
        self.assertIn("<b>Lamborghini Urus</b> (2023 г.)", caption)
        self.assertEqual(photo_kwargs["parse_mode"], "HTML")
        self.assertEqual(
            photo_kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "client_booking:car:1",
        )

    async def test_client_fleet_loader_includes_rented_cars_but_excludes_retired(self):
        with patch.object(
            main,
            "get_all_cars",
            return_value=[
                {
                    "ID": "1",
                    "Марка и Модель": "Urus",
                    "Статус": "В аренде",
                    "Витрина": "да",
                },
                {
                    "ID": "2",
                    "Марка и Модель": "Maybach",
                    "Статус": "Активен",
                    "Витрина": "ДА",
                },
                {
                    "ID": "3",
                    "Марка и Модель": "Списанный авто",
                    "Статус": "Списан",
                    "Витрина": "true",
                },
                {
                    "ID": "4",
                    "Марка и Модель": "Ремонтный авто",
                    "Статус": "В ремонте",
                    "Витрина": "1",
                },
                {
                    "ID": "5",
                    "Марка и Модель": "Hidden",
                    "Статус": "Активен",
                    "Витрина": "нет",
                },
                {
                    "ID": "6",
                    "Марка и Модель": "Blank",
                    "Статус": "Активен",
                },
            ],
        ):
            cars = await main._load_client_active_cars()

        self.assertEqual(
            [car["Марка и Модель"] for car in cars],
            ["Urus", "Maybach"],
        )

    def test_client_visibility_flag_accepts_only_explicit_enabled_values(self):
        self.assertTrue(main._car_show_in_client_bot({"Витрина": "да"}))
        self.assertTrue(main._car_show_in_client_bot({"Витрина": " YES "}))
        self.assertTrue(main._car_show_in_client_bot({"Витрина": "true"}))
        self.assertTrue(main._car_show_in_client_bot({"Витрина": "1"}))
        self.assertTrue(main._car_show_in_client_bot({"витрина": "ДА"}))
        self.assertFalse(main._car_show_in_client_bot({"Витрина": "нет"}))
        self.assertFalse(main._car_show_in_client_bot({"Витрина": ""}))
        self.assertFalse(main._car_show_in_client_bot({}))

    def test_client_date_states_only_offer_calendar(self):
        calendar = main._client_calendar("start")
        self.assertIn("Отменить", calendar._labels.cancel_caption)
        self.assertEqual(calendar._labels.months[0], "Январь")
        self.assertEqual(
            main.ClientBookingState.confirmation.state,
            "ClientBookingState:confirmation",
        )

    def test_client_calendar_marks_busy_dates_and_disables_past_dates(self):
        keyboard = main.calendar_keyboard(
            "client_booking",
            "start",
            minimum_date=datetime.date(2026, 10, 15),
            year=2026,
            month=10,
            occupied_dates={"2026-10-20": ["501"]},
        )
        buttons = {
            button.text: button
            for row in keyboard.inline_keyboard
            for button in row
        }

        busy_button = next(
            button
            for button in buttons.values()
            if button.callback_data == "calendar:client_booking:start:busy:2026-10-20"
        )
        self.assertIn("🔴", busy_button.text)
        self.assertEqual(
            buttons["·"].callback_data,
            "calendar:noop",
        )
        free_button = next(
            button
            for button in buttons.values()
            if button.callback_data == "calendar:client_booking:start:day:2026-10-21"
        )
        self.assertEqual(free_button.text, "21")

    def test_client_period_check_includes_every_day_between_dates(self):
        occupied_dates = {
            "2026-10-16": ["501"],
            "2026-10-20": ["501"],
        }
        self.assertTrue(
            main._client_period_has_occupied_dates(
                "2026-10-15",
                "2026-10-18",
                occupied_dates,
            )
        )
        self.assertFalse(
            main._client_period_has_occupied_dates(
                "2026-10-21",
                "2026-10-22",
                occupied_dates,
            )
        )

    def test_client_summary_has_confirmation_and_cancel_actions(self):
        labels = [
            button.text
            for row in main.client_booking_confirmation_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(labels, ["✅ Подтвердить заявку", "❌ Отменить бронирование"])

    def test_address_delivery_is_visible_in_summary_and_manager_notification(self):
        data = {
            "car_name": "Porsche Panamera",
            "start_date": "2026-09-15",
            "end_date": "2026-09-17",
            "delivery_method": "Доставка по адресу",
            "delivery_address": "Санкт-Петербург, Невский проспект, дом 1, отель",
            "client_name": "Иван Иванов",
            "phone": "@ivan",
            "rent_price": 100_000,
            "delivery_price": 3_000,
            "total_price": 103_000,
        }

        summary = main._client_booking_summary_text(data)
        manager_text = main._client_booking_admin_text(501, data)

        for text in (summary, manager_text):
            self.assertIn("Способ получения: Доставка по адресу", text)
            self.assertIn(
                "Адрес доставки: Санкт-Петербург, Невский проспект, дом 1, отель",
                text,
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
        self.assertEqual(kwargs["status"], "Новая заявка")
        self.assertEqual(kwargs["source"], "Сайт")
        self.assertEqual(kwargs["manager_id"], 123456)
        self.assertEqual(kwargs["prepayment"], 30_000)
        self.assertEqual(kwargs["delivery_price"], 3_000)

    def test_public_booking_headers_and_id_fallback_are_safe(self):
        appended = []
        sheet = object()
        records = [{"ID Брони": "not-a-number", "__row_number__": 2}]
        with (
            patch.object(sheets_client, "_get_bookings_sheet", return_value=sheet),
            patch.object(
                sheets_client,
                "_get_sheet_records_safely",
                return_value=records,
            ),
            patch.object(
                sheets_client,
                "_ensure_headers",
                return_value=sheets_client.PUBLIC_BOOKING_HEADERS,
            ),
            patch.object(
                sheets_client,
                "_append_row_with_recovery",
                side_effect=lambda _sheet, row, **_kwargs: appended.append(row),
            ),
            patch.object(sheets_client, "colorize_booking_row"),
        ):
            booking_id = sheets_client.add_new_booking(
                car_id=7,
                car_name="Porsche Panamera",
                plate="A123BC",
                start_date="2026-09-15",
                end_date="2026-09-17",
                client_name="Иван Иванов",
                phone="+375291112233",
                price=100_000,
                manager_id=123456,
                delivery_address="Санкт-Петербург, Невский проспект, дом 1",
                delivery_price=3_000,
                status="Новая заявка",
            )

        self.assertEqual(booking_id, 1)
        self.assertEqual(
            appended,
            [[
                1,
                "Porsche Panamera / ID 7",
                "A123BC",
                "2026-09-15",
                "2026-09-17",
                "Иван Иванов",
                "+375291112233",
                100_000,
                3_000,
                103_000,
                "Санкт-Петербург, Невский проспект, дом 1",
                "Новая заявка",
            ]],
        )

    async def test_confirm_only_offers_deposit_without_saving_booking(self):
        callback_message = SimpleNamespace(
            edit_reply_markup=AsyncMock(),
            answer=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=callback_message,
            from_user=SimpleNamespace(id=123456),
            answer=AsyncMock(),
        )
        state = _BookingState(
            {
                "car_id": "7",
                "car_name": "Porsche Panamera",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "client_name": "Иван Иванов",
                "phone": "+375291112233",
                "rent_price": 100_000,
            }
        )
        with patch.object(main, "add_public_booking") as add_booking:
            await main.client_booking_confirm_callback(
                callback,
                state,
                bot=SimpleNamespace(),
            )

        add_booking.assert_not_called()
        self.assertIn(
            "Для фиксации бронирования за вами необходимо внести задаток",
            callback_message.answer.await_args.args[0],
        )
        labels = [
            button.text
            for row in callback_message.answer.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertEqual(
            labels,
            [
                "💳 Внести задаток 3 000 RUB",
                "💬 Уточнить детали с менеджером",
                "❌ Отменить бронирование",
            ],
        )
        self.assertFalse(state.cleared)

    async def test_deposit_choice_moves_to_receipt_state_and_shows_requisites(self):
        callback_message = SimpleNamespace(
            edit_reply_markup=AsyncMock(),
            answer=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=callback_message,
            from_user=SimpleNamespace(id=123456),
            answer=AsyncMock(),
        )
        state = _BookingState({"car_name": "Porsche Panamera"})

        await main.client_booking_deposit_callback(callback, state)

        self.assertEqual(
            state.current_state,
            main.ClientBookingState.receipt.state,
        )
        self.assertIn("Сумма к оплате: 3 000 RUB", callback_message.answer.await_args.args[0])
        self.assertIn("Лунева К.Д.", callback_message.answer.await_args.args[0])

    async def test_manager_choice_notifies_without_saving_booking(self):
        callback_message = SimpleNamespace(
            edit_reply_markup=AsyncMock(),
            answer=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=callback_message,
            from_user=SimpleNamespace(id=123456),
            answer=AsyncMock(),
        )
        state = _BookingState(
            {
                "car_name": "Porsche Panamera",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "client_name": "Иван Иванов",
                "phone": "+375291112233",
                "total_price": 103_000,
                "delivery_method": "Доставка по адресу",
                "delivery_address": "Невский проспект, дом 1",
            }
        )
        manager_bot = SimpleNamespace(send_message=AsyncMock())
        client_bot = SimpleNamespace(send_message=AsyncMock())
        with (
            patch.object(main, "_booking_notification_ids", AsyncMock(return_value=[987654])),
            patch.object(main, "add_public_booking") as add_booking,
        ):
            await main.client_booking_manager_callback(
                callback,
                state,
                bot=client_bot,
                admin_bot=manager_bot,
            )

        add_booking.assert_not_called()
        manager_bot.send_message.assert_awaited_once()
        client_bot.send_message.assert_not_awaited()
        manager_text = manager_bot.send_message.await_args.kwargs["text"]
        self.assertIn("БЕЗ ОПЛАТЫ / ждёт звонка", manager_text)
        self.assertIn("Porsche Panamera", manager_text)
        self.assertIn("Невский проспект, дом 1", manager_text)
        self.assertIn("ID: 123456", manager_text)
        self.assertEqual(
            callback_message.answer.await_args.args[0],
            "Спасибо за обращение! Менеджер Leon Rental свяжется с вами "
            "в течение 10-15 минут для уточнения деталей и подтверждения бронирования.",
        )
        self.assertTrue(state.cleared)

    async def test_receipt_creates_confirmed_booking_and_notifies_managers(self):
        message = SimpleNamespace(
            document=SimpleNamespace(
                file_id="receipt.pdf",
                file_name="receipt.pdf",
            ),
            photo=[],
            from_user=SimpleNamespace(id=123456),
            chat=SimpleNamespace(id=555),
            message_id=42,
            answer=AsyncMock(),
        )
        state = _BookingState(
            {
                "car_id": "7",
                "car_name": "Porsche Panamera",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "client_name": "Иван Иванов",
                "phone": "+375291112233",
                "rent_price": 100_000,
                "total_price": 103_000,
                "prepayment": 3_000,
                "delivery_method": "Доставка по адресу",
                "delivery_address": "Невский проспект, дом 1",
            }
        )
        client_bot = SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace()),
            download=AsyncMock(),
            forward_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        manager_bot = SimpleNamespace(
            send_document=AsyncMock(),
            send_message=AsyncMock(),
        )
        with (
            patch.object(main, "add_public_booking", return_value=701) as add_booking,
            patch.object(main, "_booking_notification_ids", AsyncMock(return_value=[987654])),
        ):
            await main.client_booking_receipt(
                message,
                state,
                client_bot,
                admin_bot=manager_bot,
            )

        kwargs = add_booking.call_args.kwargs
        self.assertEqual(kwargs["prepayment"], 3_000)
        self.assertEqual(kwargs["status"], "Подтверждена")
        self.assertEqual(kwargs["receipt_payment_method"], "Перевод (СБП)")
        manager_bot.send_document.assert_awaited_once()
        self.assertIn(
            "Статус оплаты:</b> Чек прикреплен",
            manager_bot.send_document.await_args.kwargs["caption"],
        )
        self.assertIn(
            "✅ Чек получен! Бронирование успешно подтверждено",
            message.answer.await_args.args[0],
        )
        self.assertTrue(state.cleared)

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