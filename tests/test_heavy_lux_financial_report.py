import unittest

from main import (
    _format_heavy_lux_partner_report,
    get_main_keyboard,
)


class HeavyLuxFinancialReportTest(unittest.TestCase):
    def test_partner_menu_contains_financial_report_button(self):
        keyboard = get_main_keyboard(123, "partner_heavy_lux")
        labels = [
            button.text
            for row in keyboard.keyboard
            for button in row
        ]

        self.assertIn("📈 Фин. отчет", labels)

    def test_partner_report_does_not_expose_company_financial_fields(self):
        report = _format_heavy_lux_partner_report(
            {
                "month": "2026-09",
                "booking_count": 2,
                "gross_income": 100000,
                "partner_fee": 30000,
                "total_expenses": 9000,
                "company_profit": 61000,
                "company_revenue": 70000,
                "cars": [
                    {
                        "Марка и Модель": "Heavy Lux SUV",
                        "Госномер": "А001АА",
                        "Количество сдач": 2,
                        "Валовый сбор (RUB)": 100000,
                        "Комиссия heavy_lux_cr (RUB)": 30000,
                        "Затраты на авто (RUB)": 9000,
                        "Чистая прибыль авто (RUB)": 61000,
                    }
                ],
            }
        )

        self.assertIn("📈 ФИНАНСОВЫЙ ОТЧЁТ HEAVY LUX ЗА 2026-09", report)
        self.assertIn("🤝 Всего сдач через партнёра: 2", report)
        self.assertIn("💵 Валовый сбор с клиентов: 100 000 RUB", report)
        self.assertIn("💎 Ваша комиссия к выплате (30%): 30 000 RUB", report)
        self.assertIn("  - Поездок: 2", report)
        self.assertIn("  - Оборот: 100 000 RUB", report)
        self.assertIn("  - Ваша комиссия (30%): 30 000 RUB", report)
        self.assertNotIn("расход", report.casefold())
        self.assertNotIn("прибыль", report.casefold())
        self.assertNotIn("доля leon", report.casefold())


if __name__ == "__main__":
    unittest.main()