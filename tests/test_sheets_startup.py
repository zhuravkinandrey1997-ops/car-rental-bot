import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sheets_client


class _QuotaError(Exception):
    def __init__(self, status_code: int = 429):
        super().__init__(f"Google Sheets returned {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


class _Worksheet:
    def __init__(self, title: str):
        self.title = title


class _Spreadsheet:
    def __init__(self):
        self.metadata_reads = 0

    def worksheets(self):
        self.metadata_reads += 1
        return [_Worksheet("Автомобили"), _Worksheet("Менеджеры")]


class SheetsStartupTest(unittest.TestCase):
    def test_cached_worksheet_does_not_refresh_metadata(self):
        spreadsheet = _Spreadsheet()
        cache = {
            worksheet.title: worksheet
            for worksheet in spreadsheet.worksheets()
        }
        with patch.object(sheets_client, "_worksheet_cache", cache):
            worksheet = sheets_client._cached_worksheet("Менеджеры")

        self.assertEqual(worksheet.title, "Менеджеры")
        self.assertEqual(spreadsheet.metadata_reads, 1)

    def test_startup_retries_transient_quota_failure_in_process(self):
        with (
            patch.object(
                sheets_client,
                "_initialize_worksheets",
                side_effect=[_QuotaError(), None],
            ) as initialize,
            patch.object(sheets_client.time, "sleep") as sleep,
        ):
            sheets_client._initialize_worksheets_with_retry(max_retries=2)

        self.assertEqual(initialize.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_non_transient_startup_error_is_not_retried(self):
        failure = RuntimeError("invalid spreadsheet configuration")
        with (
            patch.object(
                sheets_client,
                "_initialize_worksheets",
                side_effect=failure,
            ) as initialize,
            patch.object(sheets_client.time, "sleep") as sleep,
        ):
            with self.assertRaises(RuntimeError):
                sheets_client._initialize_worksheets_with_retry()

        initialize.assert_called_once_with()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()