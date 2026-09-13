import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
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

    def test_exhausted_startup_retries_report_all_attempts(self):
        attempts = [0]
        with (
            patch.object(
                sheets_client,
                "_initialize_worksheets",
                side_effect=_QuotaError(),
            ),
            patch.object(sheets_client.time, "sleep"),
        ):
            with self.assertRaises(_QuotaError):
                sheets_client._initialize_worksheets_with_retry(
                    max_retries=3,
                    attempt_counter=attempts,
                )

        self.assertEqual(attempts[0], 3)
        self.assertEqual(sheets_client.SHEETS_STARTUP_ATTEMPTS, 3)

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

    def test_startup_status_classifies_quota_without_raw_error(self):
        failure = _QuotaError()
        failure.secret = "do-not-log-this"

        status = sheets_client._startup_failure_status(failure, 5)

        self.assertEqual(status.reason, "quota")
        self.assertEqual(status.attempts, 5)
        self.assertIn("квоту", status.hint)
        self.assertNotIn("do-not-log-this", str(sheets_client.SheetsStartupError(status)))

    def test_startup_status_classifies_network_failure(self):
        status = sheets_client._startup_failure_status(
            ConnectionError("temporary network failure"),
            3,
        )

        self.assertEqual(status.reason, "network")
        self.assertEqual(status.attempts, 3)
        self.assertIn("сетевой", status.hint)

    def test_startup_status_classifies_missing_worksheet(self):
        status = sheets_client._startup_failure_status(
            sheets_client.gspread.exceptions.WorksheetNotFound("Менеджеры"),
            1,
        )

        self.assertEqual(status.reason, "structure")
        self.assertIn("обязательные листы", status.hint)

    def test_ensure_sheets_ready_raises_safe_diagnostic(self):
        status = sheets_client.SheetsStartupStatus(
            ready=False,
            reason="structure",
            attempts=1,
            hint="Проверьте структуру таблицы.",
        )
        with (
            patch.object(sheets_client, "SHEETS_READY", False),
            patch.object(sheets_client, "SHEETS_STARTUP_STATUS", status),
        ):
            with self.assertRaises(sheets_client.SheetsStartupError) as context:
                sheets_client.ensure_sheets_ready()

        self.assertIs(context.exception.status, status)
        self.assertIn("attempts=1", str(context.exception))

    def test_run_bot_does_not_create_bots_when_sheets_are_unready(self):
        status = sheets_client.SheetsStartupStatus(
            ready=False,
            reason="quota",
            attempts=5,
            hint="Проверьте квоту Google Sheets.",
        )
        with (
            patch.object(main, "TOKEN", "fleet-token"),
            patch.object(main, "CLIENT_BOT_TOKEN", "client-token"),
            patch.object(
                main,
                "ensure_sheets_ready",
                side_effect=sheets_client.SheetsStartupError(status),
            ),
            patch.object(main, "Bot") as bot,
        ):
            with self.assertRaises(sheets_client.SheetsStartupError):
                asyncio.run(main.run_bot())

        bot.assert_not_called()


if __name__ == "__main__":
    unittest.main()