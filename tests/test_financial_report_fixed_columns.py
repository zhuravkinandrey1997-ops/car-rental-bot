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
        headers[5] = "ФИО Клиента"
        headers[7] = "Сумма брони (RUB)"
        headers[14] = "Способ оплаты"
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
            14: "Карта",
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
            14: "Карта",
            11: "Завершена",
            12: "0",
            18: "Леон Рентал",
            45: "80000",
            69: "heavy_lux_cr",
        })
        booking_107 = _wide_row({
            0: "107",
            1: "8",
            3: "2026-09-07",
            5: "Клиент Dodge",
            7: "0",
            11: "В аренде",
            12: "14000",
            14: "Наличные",
        })
        booking_117 = _wide_row({
            0: "117",
            1: "9",
            3: "2026-09-07",
            5: "Хэви люкс",
            7: "0",
            11: "В аренде",
            12: "25000",
            14: "СБП",
        })
        bookings_sheet = _FakeSheet(
            [headers, booking_113, booking_116, booking_107, booking_117]
        )
        cars_sheet = _FakeSheet(
            [
                ["ID", "Марка и Модель", "Госномер"],
                ["7", "Lamborghini Urus", "URUS-1"],
                ["8", "Dodge Challenger", "DODGE-1"],
                ["9", "Jaguar F-Type", "JAG-1"],
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
            patch.object(sheets_client, "_upsert_record") as upsert_record,
            patch.object(sheets_client, "_upsert_record_by_keys"),
        ):
            report = sheets_client.get_financial_report("2026-09")

        summary = upsert_record.call_args.args[3]
        self.assertEqual(summary["Общий приход (RUB)"], 189000)
        self.assertEqual(
            summary["Партнёрская комиссия heavy_lux_cr (RUB)"],
            52500,
        )
        self.assertEqual(summary["Чистая прибыль компании (RUB)"], 125300)
        self.assertEqual(bookings_sheet.requested_range, "A1:BT1000")
        self.assertEqual(report["client_payments"], 189000)
        self.assertEqual(report["partner_fee"], 52500)
        self.assertEqual(report["gross_income"], 189000)
        self.assertEqual(report["company_revenue"], 136500)
        self.assertEqual(report["company_profit"], 125300)
        self.assertEqual(report["payment_breakdown"]["Безнал"], 150000)
        self.assertEqual(report["payment_breakdown"]["Наличные"], 14000)
        self.assertEqual(report["payment_breakdown"]["СБП"], 25000)

        urus = next(
            row
            for row in report["cars"]
            if row["ID Авто"] == "7"
        )
        self.assertEqual(urus["Приход / Выручка (RUB)"], 105000)
        self.assertEqual(urus["Затраты на авто (RUB)"], 11200)
        self.assertEqual(urus["Чистая прибыль авто (RUB)"], 93800)

        jaguar = next(
            row
            for row in report["cars"]
            if row["ID Авто"] == "9"
        )
        self.assertEqual(jaguar["Приход / Выручка (RUB)"], 17500)


if __name__ == "__main__":
    unittest.main()