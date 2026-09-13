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
    _next_id = 1

    def __init__(self, title: str, headers: list[str] | None = None):
        self.title = title
        self.id = _Worksheet._next_id
        _Worksheet._next_id += 1
        self.headers = list(headers or [])
        self.col_count = 10
        self.title_updates = []
        self.updated_ranges = []
        self.added_columns = 0

    def update_title(self, title: str):
        self.title_updates.append((self.title, title))
        self.title = title

    def row_values(self, row_number: int):
        if row_number != 1:
            raise AssertionError("startup fake only supports header reads")
        return list(self.headers)

    def update(self, values, range_name=None, value_input_option=None):
        del value_input_option
        self.updated_ranges.append((range_name, values))
        if range_name == f"A1:{sheets_client._column_letter(len(values[0]))}1":
            self.headers = list(values[0])

    def add_cols(self, count: int):
        self.added_columns += count
        self.col_count += count


class _Spreadsheet:
    def __init__(self, worksheets):
        self._worksheets = list(worksheets)
        self.metadata_reads = 0
        self.added_titles = []

    def worksheets(self):
        self.metadata_reads += 1
        return list(self._worksheets)

    def add_worksheet(self, title: str, rows: int, cols: int):
        del rows
        worksheet = _Worksheet(title)
        worksheet.col_count = cols
        self._worksheets.append(worksheet)
        self.added_titles.append(title)
        return worksheet


class _SheetsClient:
    def __init__(self, spreadsheet, *, failures=None):
        self.spreadsheet = spreadsheet
        self.failures = list(failures or [])
        self.open_calls = 0

    def open_by_key(self, spreadsheet_key):
        del spreadsheet_key
        self.open_calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            raise failure
        return self.spreadsheet


def _startup_worksheets(*titles):
    return [_Worksheet(title) for title in titles]


def _patch_startup_worksheets():
    return patch.multiple(
        sheets_client,
        ws_cars=None,
        ws_bookings=None,
        ws_returns=None,
        ws_managers=None,
        ws_acceptance_acts=None,
        ws_expenses=None,
        ws_monthly_summary=None,
        ws_car_profit_summary=None,
        ws_notification_state=None,
        _worksheet_cache={},
    )


class SheetsStartupTest(unittest.TestCase):
    def test_cached_worksheet_does_not_refresh_metadata(self):
        spreadsheet = _Spreadsheet(_startup_worksheets("Автомобили", "Менеджеры"))
        cache = {
            worksheet.title: worksheet
            for worksheet in spreadsheet.worksheets()
        }
        with patch.object(sheets_client, "_worksheet_cache", cache):
            worksheet = sheets_client._cached_worksheet("Менеджеры")

        self.assertEqual(worksheet.title, "Менеджеры")
        self.assertEqual(spreadsheet.metadata_reads, 1)

    def test_full_bootstrap_migrates_multiple_sheets_from_one_metadata_snapshot(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Cars",
                "Bookings",
                "Returns_Cash",
                "Managers",
                "Acceptance_Acts",
                "Затраты",
                "Сводная_по_месяцам",
                "Прибыль_по_авто",
                "Состояние_уведомлений",
            )
        )
        with _patch_startup_worksheets():
            with patch.object(sheets_client, "sh", spreadsheet):
                sheets_client._initialize_worksheets()

        self.assertEqual(spreadsheet.metadata_reads, 1)
        self.assertEqual(
            {worksheet.title for worksheet in spreadsheet.worksheets()},
            {
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
                "Acceptance_Acts",
                "Затраты",
                "Сводная_по_месяцам",
                "Прибыль_по_авто",
                "Состояние_уведомлений",
            },
        )
        self.assertEqual(
            [worksheet.title_updates for worksheet in spreadsheet._worksheets[:4]],
            [
                [("Cars", "Автомобили")],
                [("Bookings", "Бронирования")],
                [("Returns_Cash", "Возвраты_Касса")],
                [("Managers", "Менеджеры")],
            ],
        )

    def test_full_bootstrap_creates_missing_service_sheets_only_once(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
            )
        )
        with _patch_startup_worksheets():
            with patch.object(sheets_client, "sh", spreadsheet):
                sheets_client._initialize_worksheets()
                sheets_client._initialize_worksheets()

        expected_created = {
            "Acceptance_Acts",
            "Затраты",
            "Сводная_по_месяцам",
            "Прибыль_по_авто",
            "Состояние_уведомлений",
        }
        self.assertEqual(set(spreadsheet.added_titles), expected_created)
        self.assertEqual(len(spreadsheet.added_titles), len(expected_created))
        self.assertEqual(spreadsheet.metadata_reads, 2)
        self.assertEqual(
            {worksheet.title for worksheet in spreadsheet._worksheets},
            {
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
                *expected_created,
            },
        )

    def test_full_bootstrap_continues_after_transient_open_quota_failure(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
                "Acceptance_Acts",
                "Затраты",
                "Сводная_по_месяцам",
                "Прибыль_по_авто",
                "Состояние_уведомлений",
            )
        )
        sheets_client_client = _SheetsClient(
            spreadsheet,
            failures=[_QuotaError()],
        )
        with (
            _patch_startup_worksheets(),
            patch.object(sheets_client, "SERVICE_ACCOUNT_JSON", None),
            patch.object(sheets_client, "SPREADSHEET_NAME", "a" * 40),
            patch.object(
                sheets_client.gspread,
                "service_account",
                return_value=sheets_client_client,
            ),
            patch.object(sheets_client.time, "sleep"),
        ):
            sheets_client._bootstrap_sheets()

        self.assertEqual(sheets_client_client.open_calls, 2)
        self.assertEqual(spreadsheet.metadata_reads, 1)
        self.assertIs(sheets_client.sh, spreadsheet)
        self.assertIs(sheets_client.ws_managers, sheets_client._worksheet_cache["Менеджеры"])

    def test_full_bootstrap_recovers_from_quota_and_marks_sheets_ready(self):
        self._assert_full_bootstrap_recovers_from_transient_failure(
            _QuotaError(),
        )

    def test_full_bootstrap_recovers_from_network_failure_and_marks_sheets_ready(
        self,
    ):
        self._assert_full_bootstrap_recovers_from_transient_failure(
            ConnectionError("temporary network failure"),
        )

    def _assert_full_bootstrap_recovers_from_transient_failure(self, failure):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
                "Acceptance_Acts",
                "Затраты",
                "Сводная_по_месяцам",
                "Прибыль_по_авто",
                "Состояние_уведомлений",
            )
        )
        sheets_client_client = _SheetsClient(
            spreadsheet,
            failures=[failure],
        )
        initial_status = sheets_client.SheetsStartupStatus(
            ready=False,
            reason="not_started",
            attempts=0,
            hint="Инициализация Google Sheets ещё не завершена.",
        )
        with (
            _patch_startup_worksheets(),
            patch.object(sheets_client, "SHEETS_READY", False),
            patch.object(sheets_client, "SHEETS_STARTUP_STATUS", initial_status),
            patch.object(sheets_client, "SERVICE_ACCOUNT_JSON", None),
            patch.object(sheets_client, "SPREADSHEET_NAME", "a" * 40),
            patch.object(
                sheets_client.gspread,
                "service_account",
                return_value=sheets_client_client,
            ),
            patch.object(sheets_client.time, "sleep") as sleep,
        ):
            self.assertTrue(sheets_client._run_sheets_startup())

            self.assertTrue(sheets_client.SHEETS_READY)
            self.assertEqual(
                sheets_client.SHEETS_STARTUP_STATUS.reason,
                "ready",
            )
            self.assertEqual(
                sheets_client.SHEETS_STARTUP_STATUS.attempts,
                1,
            )

        self.assertEqual(sheets_client_client.open_calls, 2)
        sleep.assert_called_once_with(3)
        self.assertIs(sheets_client.sh, spreadsheet)
        self.assertIs(
            sheets_client.ws_managers,
            sheets_client._worksheet_cache["Менеджеры"],
        )

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

    def test_run_bot_does_not_start_polling_before_sheets_are_ready(self):
        status = sheets_client.SheetsStartupStatus(
            ready=False,
            reason="network",
            attempts=2,
            hint="Проверьте сетевой доступ к Google Sheets.",
        )
        with (
            patch.object(main, "TOKEN", "fleet-token"),
            patch.object(main, "CLIENT_BOT_TOKEN", "client-token"),
            patch.object(
                main,
                "ensure_sheets_ready",
                side_effect=sheets_client.SheetsStartupError(status),
            ),
            patch.object(main, "run_fleet_bot") as run_fleet_bot,
            patch.object(main, "run_client_bot") as run_client_bot,
        ):
            with self.assertRaises(sheets_client.SheetsStartupError):
                asyncio.run(main.run_bot())

        run_fleet_bot.assert_not_called()
        run_client_bot.assert_not_called()


if __name__ == "__main__":
    unittest.main()