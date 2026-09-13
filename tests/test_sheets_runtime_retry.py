import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sheets_client


class _QuotaError(Exception):
    def __init__(self, status_code: int = 429):
        super().__init__("temporary quota failure")
        self.response = SimpleNamespace(status_code=status_code)


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
            raise _QuotaError()
        self.values.append(list(row))
        if self.append_mode == "fail_after_write" and self.append_calls == 1:
            raise _QuotaError()


class SheetsRuntimeRetryTest(unittest.TestCase):
    def test_runtime_retry_retries_transient_failure_once(self):
        operation = Mock(
            side_effect=[_QuotaError(), "ready"],
        )
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

    def test_runtime_failure_hint_does_not_expose_raw_error(self):
        failure = _QuotaError()
        failure.secret = "do-not-display"

        hint = sheets_client.sheets_operation_failure_hint(failure)

        self.assertIn("ограничил", hint)
        self.assertNotIn("do-not-display", hint)


if __name__ == "__main__":
    unittest.main()