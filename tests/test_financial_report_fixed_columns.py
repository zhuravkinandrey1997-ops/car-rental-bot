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


def _wide_row(values):
    row = [""] * 72
    for column, value in values.items():
        row[column] = value
    return row


class FinancialReportFixedColumnsTest(unittest.TestCase):
    def test_urus_uses_m_or_at_and_source_from_br(self):
        headers = [""] * 72
        headers[0] = "ID Брони"
        headers[1] = "ID Авто"
        headers[3] = "Дата начала"
        headers[7] = "Сумма брони (RUB)"
        headers[8] = "Способ оплаты"
        headers[11] = "Статус"
        headers[12] = "Принятая сумма аренды"
        headers[18] = "Источник"
        headers[45] = "Фактически принято"
        headers[69] = "Источник"

        booking_113 = _wide_row({
            0: "113",
            1: "7",
            3: "2026-09-05",
            7: "0",
            8: "Карта",
            11: "Завершена",
            12: "70000",
            18: "Леон Рентал",
            69: " heavy_lux_cr ",
        })
        booking_116 = _wide_row({
            0: "116",
            1: "7",
            3: "2026-09-06",
            7: "0",
            8: "Карта",
            11: "Завершена",
            12: "0",
            18: "Леон Рентал",
            45: "80000",
            69: "heavy_lux_cr",
        })
        bookings_sheet = _FakeSheet([headers, booking_113, booking_116])
        cars_sheet = _FakeSheet(
            [
                ["ID", "Марка и Модель", "Госномер"],
                ["7", "Lamborghini Urus", "URUS-1"],
            ]
        )
        returns_sheet = _FakeSheet([["Дата возврата"]])
        expenses_sheet = _FakeSheet(
            [
                ["Дата", "Сумма (RUB)", "Автомобиль (Госномер / Общие)"],
                ["2026-09-06", "11200", "URUS-1"],
            ]
        )

        with (
            patch.object(sheets_client, "ws_bookings", bookings_sheet),
            patch.object(sheets_client, "ws_cars", cars_sheet),
            patch.object(sheets_client, "ws_returns", returns_sheet),
            patch.object(sheets_client, "ws_expenses", expenses_sheet),
            patch.object(sheets_client, "_upsert_record"),
            patch.object(sheets_client, "_upsert_record_by_keys"),
        ):
            report = sheets_client.get_financial_report("2026-09")

        self.assertEqual(bookings_sheet.requested_range, "A1:BT1000")
        self.assertEqual(report["client_payments"], 150000)
        self.assertEqual(report["partner_fee"], 45000)
        self.assertEqual(report["gross_income"], 105000)
        self.assertEqual(report["company_profit"], 93800)
        self.assertEqual(report["payment_breakdown"]["Безнал"], 105000)

        urus = next(
            row
            for row in report["cars"]
            if row["ID Авто"] == "7"
        )
        self.assertEqual(urus["Приход / Выручка (RUB)"], 105000)
        self.assertEqual(urus["Затраты на авто (RUB)"], 11200)
        self.assertEqual(urus["Чистая прибыль авто (RUB)"], 93800)


if __name__ == "__main__":
    unittest.main()