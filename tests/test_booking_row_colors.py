import unittest
from unittest.mock import Mock, patch

import sheets_client


class _FakeBookingsSheet:
    def __init__(self, values, sheet_id=321, col_count=72):
        self.values = values
        self.id = sheet_id
        self.col_count = col_count

    def get_all_values(self):
        return self.values


class BookingRowColorsTest(unittest.TestCase):
    def test_colorize_booking_row_uses_heavy_lux_green(self):
        spreadsheet = Mock()
        bookings = _FakeBookingsSheet([["ID Брони"]])
        with patch.object(sheets_client, "sh", spreadsheet), patch.object(
            sheets_client,
            "ws_bookings",
            bookings,
        ):
            sheets_client.colorize_booking_row("spreadsheet", 321, 7, "heavy_lux_cr")

        body = spreadsheet.batch_update.call_args.args[0]
        request = body["requests"][0]["repeatCell"]
        self.assertEqual(request["range"]["startRowIndex"], 6)
        self.assertEqual(request["range"]["endRowIndex"], 7)
        self.assertEqual(request["range"]["endColumnIndex"], 72)
        self.assertEqual(
            request["cell"]["userEnteredFormat"]["backgroundColor"],
            {"red": 0.85, "green": 0.95, "blue": 0.85},
        )
        self.assertEqual(
            request["fields"],
            "userEnteredFormat.backgroundColor",
        )

    def test_existing_rows_are_colored_in_one_batch_by_br_or_client_name(self):
        headers = ["ID Брони", "ФИО Клиента"] + [""] * 68
        heavy_by_br = ["113", "Обычный клиент"] + [""] * 67 + ["heavy_lux_cr"]
        heavy_by_name = ["117", "Хэви люкс"] + [""] * 69
        leon = ["118", "Обычный клиент"] + [""] * 69
        bookings = _FakeBookingsSheet(
            [headers, heavy_by_br, heavy_by_name, leon],
            sheet_id=456,
            col_count=80,
        )
        spreadsheet = Mock()

        with patch.object(sheets_client, "sh", spreadsheet), patch.object(
            sheets_client,
            "ws_bookings",
            bookings,
        ):
            count = sheets_client.colorize_existing_booking_rows()

        self.assertEqual(count, 3)
        body = spreadsheet.batch_update.call_args.args[0]
        requests = body["requests"]
        self.assertEqual(len(requests), 3)
        self.assertEqual(
            requests[0]["repeatCell"]["cell"]["userEnteredFormat"][
                "backgroundColor"
            ],
            {"red": 0.85, "green": 0.95, "blue": 0.85},
        )
        self.assertEqual(
            requests[1]["repeatCell"]["cell"]["userEnteredFormat"][
                "backgroundColor"
            ],
            {"red": 0.85, "green": 0.95, "blue": 0.85},
        )
        self.assertEqual(
            requests[2]["repeatCell"]["cell"]["userEnteredFormat"][
                "backgroundColor"
            ],
            {"red": 0.98, "green": 0.86, "blue": 0.86},
        )
        for request in requests:
            self.assertEqual(
                request["repeatCell"]["range"]["sheetId"],
                456,
            )


if __name__ == "__main__":
    unittest.main()