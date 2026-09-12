import unittest
from unittest.mock import patch

import sheets_client


class _FakeSheet:
    def __init__(self, values):
        self.values = values

    def get(self, range_name):
        self.requested_range = range_name
        return self.values

    def get_all_values(self):
        return self.values


def _booking_row(
    booking_id,
    car_id,
    amount,
    payment_method,
    source,
):
    row = [""] * 72
    row[0] = str(booking_id)
    row[1] = str(car_id)
    row[3] = "2026-09-10"
    row[5] = "Клиент"
    row[7] = "0"
    row[11] = "Завершена"
    row[12] = str(amount)
    row[14] = payment_method
    row[69] = source
    return row


class FinancialReportSourcesTest(unittest.TestCase):
    def setUp(self):
        headers = [""] * 72
        headers[0] = "ID Брони"
        headers[1] = "ID Авто"
        headers[3] = "Дата начала"
        headers[5] = "ФИО Клиента"
        headers[7] = "Сумма брони (RUB)"
        headers[11] = "Статус"
        headers[12] = "Принятая сумма аренды"
        headers[14] = "Способ оплаты"
        headers[69] = "Источник"
        self.bookings = _FakeSheet(
            [
                headers,
                _booking_row(201, 1, 10000, "Наличные", "Леон Рентал"),
                _booking_row(202, 2, 20000, "Без оплаты", "heavy_lux_cr"),
            ]
        )
        self.cars = _FakeSheet(
            [
                ["ID", "Марка и Модель", "Госномер"],
                ["1", "Leon Car", "LEON-1"],
                ["2", "Heavy Car", "HEAVY-1"],
                ["3", "Unused Car", "UNUSED-1"],
            ]
        )
        self.returns = _FakeSheet([["Дата возврата"]])
        self.expenses = _FakeSheet(
            [
                [
                    "Дата",
                    "Сумма (RUB)",
                    "Автомобиль (Госномер / Общие)",
                    "Категория / На что",
                ],
                ["2026-09-10", "1000", "LEON-1", "ТО"],
                ["2026-09-11", "2000", "HEAVY-1", "Мойка"],
                ["2026-09-12", "500", "Общие", "Офис"],
            ]
        )

    def _report(self, source_filter):
        with (
            patch.object(sheets_client, "ws_bookings", self.bookings),
            patch.object(sheets_client, "ws_cars", self.cars),
            patch.object(sheets_client, "ws_returns", self.returns),
            patch.object(sheets_client, "ws_expenses", self.expenses),
        ):
            return sheets_client.get_financial_report(
                "2026-09",
                source_filter,
            )

    def test_leon_report_contains_only_leon_deals_and_direct_expenses(self):
        report = self._report("leon")

        self.assertEqual(report["gross_income"], 10000)
        self.assertEqual(report["partner_fee"], 0)
        self.assertEqual(report["company_revenue"], 10000)
        self.assertEqual(report["total_expenses"], 1000)
        self.assertEqual(report["company_profit"], 9000)
        self.assertEqual(len(report["cars"]), 1)
        self.assertEqual(report["cars"][0]["ID Авто"], "1")

    def test_heavy_lux_report_shows_collection_commission_and_company_share(self):
        report = self._report("heavy_lux_cr")

        self.assertEqual(report["booking_count"], 1)
        self.assertEqual(report["gross_income"], 20000)
        self.assertEqual(report["partner_fee"], 6000)
        self.assertEqual(report["company_revenue"], 14000)
        self.assertEqual(report["total_expenses"], 2000)
        self.assertEqual(report["company_profit"], 12000)
        self.assertEqual(report["payment_breakdown"]["Наличные"], 20000)
        self.assertEqual(len(report["cars"]), 1)
        heavy_car = report["cars"][0]
        self.assertEqual(heavy_car["Количество сдач"], 1)
        self.assertEqual(heavy_car["Валовый сбор (RUB)"], 20000)
        self.assertEqual(heavy_car["Комиссия heavy_lux_cr (RUB)"], 6000)
        self.assertEqual(heavy_car["Доля Leon Rental (70%) (RUB)"], 14000)
        self.assertEqual(heavy_car["Чистая прибыль авто (RUB)"], 12000)

    def test_all_report_includes_both_sources_and_general_expenses(self):
        report = self._report("all")

        self.assertEqual(report["gross_income"], 30000)
        self.assertEqual(report["partner_fee"], 6000)
        self.assertEqual(report["company_revenue"], 24000)
        self.assertEqual(report["direct_expenses"], 3000)
        self.assertEqual(report["general_expenses"], 500)
        self.assertEqual(report["total_expenses"], 3500)
        self.assertEqual(report["company_profit"], 20500)
        self.assertEqual(report["payment_breakdown"]["Наличные"], 30000)
        self.assertEqual(
            {car["ID Авто"] for car in report["cars"]},
            {"1", "2", "3"},
        )


if __name__ == "__main__":
    unittest.main()