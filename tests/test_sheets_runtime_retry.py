import unittest
from unittest.mock import Mock, patch

import gspread
import requests

import sheets_client


def _api_error(status_code: int) -> gspread.exceptions.APIError:
    response = requests.Response()
    response.status_code = status_code
    response._content = (
        '{"error":{"code":%s,"message":"temporary Sheets failure",'
        '"status":"UNAVAILABLE"}}' % status_code
    ).encode()
    response.url = "https://sheets.googleapis.com/v4/spreadsheets/test"
    return gspread.exceptions.APIError(response)


class _RuntimeWorksheet:
    title = "Тестовый лист"

    def __init__(self, *, append_mode: str = "success"):
        self.values = [["ID Записи", "Значение"]]
        self.append_mode = append_mode
        self.append_calls = 0
        self.read_calls = 0

    def get_all_values(self):
        self.read_calls += 1
        return [list(row) for row in self.values]

    def append_row(self, row, **kwargs):
        del kwargs
        self.append_calls += 1
        if self.append_mode == "fail_before_write" and self.append_calls == 1:
            raise _api_error(503)
        self.values.append(list(row))
        if self.append_mode == "fail_after_write" and self.append_calls == 1:
            raise _api_error(503)


class SheetsRuntimeRetryTest(unittest.TestCase):
    def test_expense_accepts_reusable_idempotency_key(self):
        with patch.object(sheets_client, "_upsert_record") as upsert:
            first_id = sheets_client.add_expense(
                100,
                "Мойка",
                "Общие расходы",
                123,
                "Менеджер",
                "Повтор операции",
                "2026-09-13",
                "EXP-RETRY-1",
            )
            second_id = sheets_client.add_expense(
                100,
                "Мойка",
                "Общие расходы",
                123,
                "Менеджер",
                "Повтор операции",
                "2026-09-13",
                "EXP-RETRY-1",
            )

        self.assertEqual(first_id, "EXP-RETRY-1")
        self.assertEqual(second_id, "EXP-RETRY-1")
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(upsert.call_args_list[0].args[2], "EXP-RETRY-1")
        self.assertEqual(upsert.call_args_list[1].args[2], "EXP-RETRY-1")

    def test_runtime_retry_retries_all_transient_gspread_api_statuses(self):
        for status_code in (408, 429, 500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                operation = Mock(
                    side_effect=[_api_error(status_code), "ready"],
                )
                with patch.object(sheets_client.time, "sleep") as sleep:
                    result = sheets_client._retry_runtime_operation(
                        operation,
                        "reading test data",
                    )

                self.assertEqual(result, "ready")
                self.assertEqual(operation.call_count, 2)
                sleep.assert_called_once_with(1)

    def test_runtime_retry_recovers_from_real_network_errors(self):
        for failure in (
            requests.exceptions.Timeout("request timed out"),
            requests.exceptions.ConnectionError("connection reset"),
        ):
            with self.subTest(failure=type(failure).__name__):
                operation = Mock(side_effect=[failure, "ready"])
                with patch.object(sheets_client.time, "sleep") as sleep:
                    result = sheets_client._retry_runtime_operation(
                        operation,
                        "reading test data",
                    )

                self.assertEqual(result, "ready")
                self.assertEqual(operation.call_count, 2)
                sleep.assert_called_once_with(1)

    def test_runtime_retry_does_not_retry_permanent_failure(self):
        failure = ValueError("invalid data")
        operation = Mock(side_effect=failure)
        with patch.object(sheets_client.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                sheets_client._retry_runtime_operation(
                    operation,
                    "reading test data",
                )

        operation.assert_called_once_with()
        sleep.assert_not_called()

    def test_append_does_not_duplicate_when_response_is_lost(self):
        worksheet = _RuntimeWorksheet(append_mode="fail_after_write")
        with patch.object(sheets_client.time, "sleep") as sleep:
            sheets_client._append_row_with_recovery(
                worksheet,
                ["EXP-1", "100"],
                key_fields=(("ID Записи",),),
                key_values=("EXP-1",),
                operation_name="creating expense",
            )

        self.assertEqual(worksheet.append_calls, 1)
        self.assertEqual(len(worksheet.values), 2)
        sleep.assert_not_called()

    def test_append_retries_when_transient_failure_happens_before_write(self):
        worksheet = _RuntimeWorksheet(append_mode="fail_before_write")
        with patch.object(sheets_client.time, "sleep") as sleep:
            sheets_client._append_row_with_recovery(
                worksheet,
                ["EXP-2", "200"],
                key_fields=(("ID Записи",),),
                key_values=("EXP-2",),
                operation_name="creating expense",
            )

        self.assertEqual(worksheet.append_calls, 2)
        self.assertEqual(len(worksheet.values), 2)
        sleep.assert_called_once_with(1)

    def test_runtime_retry_stops_after_bounded_attempts(self):
        failure = _api_error(503)
        operation = Mock(side_effect=failure)
        with patch.object(sheets_client.time, "sleep") as sleep:
            with self.assertRaises(gspread.exceptions.APIError) as raised:
                sheets_client._retry_runtime_operation(
                    operation,
                    "reading test data",
                )

        self.assertIs(raised.exception, failure)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_any_call(1)
        sleep.assert_any_call(3)

    def test_runtime_failure_hint_does_not_expose_raw_error(self):
        failure = _api_error(429)
        failure.secret = "do-not-display"

        hint = sheets_client.sheets_operation_failure_hint(failure)

        self.assertIn("ограничил", hint)
        self.assertNotIn("do-not-display", hint)


if __name__ == "__main__":
    unittest.main()