import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class _ReadOnlySpreadsheet:
    def __init__(self, titles):
        self._worksheets = [SimpleNamespace(title=title) for title in titles]
        self.metadata_reads = 0
        self.write_attempts = 0

    def worksheets(self):
        self.metadata_reads += 1
        return list(self._worksheets)

    def add_worksheet(self, *args, **kwargs):
        del args, kwargs
        self.write_attempts += 1
        raise AssertionError("diagnostics must not create worksheets")


class _ReadOnlySheetsClient:
    def __init__(self, spreadsheet, *, failures=None):
        self.spreadsheet = spreadsheet
        self.failures = list(failures or [])
        self.open_calls = 0

    def open(self, spreadsheet_name):
        del spreadsheet_name
        return self._open()

    def open_by_key(self, spreadsheet_key):
        del spreadsheet_key
        return self._open()

    def _open(self):
        self.open_calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.spreadsheet


class _DiagnosticMessage:
    def __init__(self, user_id):
        self.from_user = SimpleNamespace(id=user_id)
        self.responses = []

    async def answer(self, text, **kwargs):
        del kwargs
        self.responses.append(text)


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

    def test_full_bootstrap_rejects_every_legacy_title_conflict(self):
        for old_title, new_title in sheets_client.SHEET_TITLE_MAP.items():
            spreadsheet = _Spreadsheet(
                _startup_worksheets(old_title, new_title),
            )
            with (
                _patch_startup_worksheets(),
                patch.object(sheets_client, "sh", spreadsheet),
            ):
                with self.assertRaises(sheets_client.SheetsStructureError) as context:
                    sheets_client._initialize_worksheets()

            self.assertEqual(
                context.exception.conflicts,
                ((old_title, new_title),),
            )
            self.assertEqual(spreadsheet.metadata_reads, 1)
            self.assertEqual(
                [worksheet.title_updates for worksheet in spreadsheet._worksheets],
                [[], []],
            )

    def test_full_bootstrap_checks_all_conflicts_before_renaming_any_sheet(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Cars",
                "Автомобили",
                "Bookings",
                "Бронирования",
            )
        )
        with (
            _patch_startup_worksheets(),
            patch.object(sheets_client, "sh", spreadsheet),
        ):
            with self.assertRaises(sheets_client.SheetsStructureError) as context:
                sheets_client._initialize_worksheets()

        self.assertEqual(
            context.exception.conflicts,
            (
                ("Cars", "Автомобили"),
                ("Bookings", "Бронирования"),
            ),
        )
        self.assertEqual(
            [worksheet.title_updates for worksheet in spreadsheet._worksheets],
            [[], [], [], []],
        )

    def test_conflict_listing_uses_one_read_only_metadata_snapshot(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets("Cars", "Автомобили", "Менеджеры"),
        )

        conflicts = sheets_client.find_worksheet_title_conflicts(spreadsheet)

        self.assertEqual(conflicts, (("Cars", "Автомобили"),))
        self.assertEqual(spreadsheet.metadata_reads, 1)
        self.assertEqual(
            [worksheet.title_updates for worksheet in spreadsheet._worksheets],
            [[], [], []],
        )

    def test_conflict_resolution_requires_confirmation_before_any_write(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets("Cars", "Автомобили"),
        )

        with self.assertRaises(ValueError):
            sheets_client.resolve_worksheet_title_conflict(
                "Cars",
                "Автомобили",
                keep_title="Cars",
                confirmed=False,
                spreadsheet=spreadsheet,
            )

        self.assertEqual(
            [worksheet.title_updates for worksheet in spreadsheet._worksheets],
            [[], []],
        )

    def test_conflict_resolution_archives_legacy_sheet_and_bootstrap_can_continue(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Cars",
                "Автомобили",
                "Бронирования",
                "Возвраты_Касса",
                "Менеджеры",
            )
        )

        actions = sheets_client.resolve_worksheet_title_conflict(
            "Cars",
            "Автомобили",
            keep_title="Автомобили",
            confirmed=True,
            spreadsheet=spreadsheet,
        )

        self.assertEqual(actions, [("Cars", "Cars (архив)")])
        self.assertEqual(
            {worksheet.title for worksheet in spreadsheet.worksheets()},
            {"Cars (архив)", "Автомобили", "Бронирования", "Возвраты_Касса", "Менеджеры"},
        )
        self.assertEqual(
            sheets_client.find_worksheet_title_conflicts(spreadsheet),
            (),
        )

    def test_conflict_resolution_preserves_legacy_sheet_data_by_moving_current_title(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets("Cars", "Автомобили"),
        )

        actions = sheets_client.resolve_worksheet_title_conflict(
            "Cars",
            "Автомобили",
            keep_title="Cars",
            confirmed=True,
            spreadsheet=spreadsheet,
        )

        self.assertEqual(
            actions,
            [
                ("Автомобили", "Автомобили (архив)"),
                ("Cars", "Автомобили"),
            ],
        )
        self.assertEqual(
            {worksheet.title for worksheet in spreadsheet.worksheets()},
            {"Автомобили", "Автомобили (архив)"},
        )
        self.assertEqual(
            sheets_client.find_worksheet_title_conflicts(spreadsheet),
            (),
        )

    def test_conflict_resolution_uses_a_new_archive_title_when_archive_exists(self):
        spreadsheet = _Spreadsheet(
            _startup_worksheets(
                "Cars",
                "Автомобили",
                "Cars (архив)",
            ),
        )

        actions = sheets_client.resolve_worksheet_title_conflict(
            "Cars",
            "Автомобили",
            keep_title="Автомобили",
            confirmed=True,
            spreadsheet=spreadsheet,
        )

        self.assertEqual(actions, [("Cars", "Cars (архив) 2")])
        self.assertEqual(
            {worksheet.title for worksheet in spreadsheet.worksheets()},
            {"Cars (архив)", "Cars (архив) 2", "Автомобили"},
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

    def test_startup_does_not_retry_worksheet_title_conflict_as_quota(self):
        failure = sheets_client.SheetsStructureError(
            (("Cars", "Автомобили"),),
        )
        with (
            patch.object(
                sheets_client,
                "_initialize_worksheets",
                side_effect=failure,
            ) as initialize,
            patch.object(sheets_client.time, "sleep") as sleep,
        ):
            with self.assertRaises(sheets_client.SheetsStructureError):
                sheets_client._initialize_worksheets_with_retry()

        initialize.assert_called_once_with()
        sleep.assert_not_called()

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

    def test_startup_status_classifies_title_conflict_without_raw_exception(self):
        failure = sheets_client.SheetsStructureError(
            (("Cars", "Автомобили"),),
        )

        status = sheets_client._startup_failure_status(failure, 1)

        self.assertEqual(status.reason, "structure")
        self.assertIn("конфликтующие названия листов", status.hint)
        self.assertIn("Cars / Автомобили", status.hint)
        self.assertNotIn("Google Sheets worksheet structure is invalid", status.hint)

    def test_read_only_diagnostic_reports_ready_without_writes(self):
        spreadsheet = _ReadOnlySpreadsheet(
            sheets_client.REQUIRED_WORKSHEET_TITLES,
        )
        client = _ReadOnlySheetsClient(spreadsheet)
        with (
            patch.object(sheets_client, "SERVICE_ACCOUNT_JSON", None),
            patch.object(sheets_client, "SERVICE_ACCOUNT_FILE", "credentials.json"),
            patch.object(sheets_client, "SPREADSHEET_NAME", "rental-fleet"),
            patch.object(
                sheets_client.gspread,
                "service_account",
                return_value=client,
            ),
        ):
            status = sheets_client.diagnose_sheets_access()

        self.assertTrue(status.ready)
        self.assertEqual(status.reason, "ready")
        self.assertEqual(status.attempts, 1)
        self.assertEqual(client.open_calls, 1)
        self.assertEqual(spreadsheet.metadata_reads, 1)
        self.assertEqual(spreadsheet.write_attempts, 0)

    def test_read_only_diagnostic_classifies_missing_required_sheet(self):
        spreadsheet = _ReadOnlySpreadsheet(("Автомобили", "Менеджеры"))
        client = _ReadOnlySheetsClient(spreadsheet)
        with (
            patch.object(sheets_client, "SERVICE_ACCOUNT_JSON", None),
            patch.object(sheets_client, "SERVICE_ACCOUNT_FILE", "credentials.json"),
            patch.object(
                sheets_client.gspread,
                "service_account",
                return_value=client,
            ),
        ):
            status = sheets_client.diagnose_sheets_access()

        self.assertFalse(status.ready)
        self.assertEqual(status.reason, "structure")
        self.assertEqual(status.attempts, 1)
        self.assertEqual(client.open_calls, 1)
        self.assertEqual(spreadsheet.write_attempts, 0)

    def test_read_only_diagnostic_retries_quota_without_exposing_error(self):
        spreadsheet = _ReadOnlySpreadsheet(sheets_client.REQUIRED_WORKSHEET_TITLES)
        quota_error = _QuotaError()
        quota_error.secret = "do-not-show"
        client = _ReadOnlySheetsClient(spreadsheet, failures=[quota_error])
        with (
            patch.object(sheets_client, "SERVICE_ACCOUNT_JSON", None),
            patch.object(sheets_client, "SERVICE_ACCOUNT_FILE", "credentials.json"),
            patch.object(sheets_client, "SPREADSHEET_NAME", "rental-fleet"),
            patch.object(
                sheets_client.gspread,
                "service_account",
                return_value=client,
            ),
            patch.object(sheets_client.time, "sleep"),
        ):
            status = sheets_client.diagnose_sheets_access()

        self.assertTrue(status.ready)
        self.assertEqual(status.attempts, 2)
        self.assertEqual(client.open_calls, 2)
        self.assertNotIn("do-not-show", status.hint)

    def test_operator_command_rejects_non_operator_without_sheet_access(self):
        message = _DiagnosticMessage(123)
        with patch.object(main, "diagnose_sheets_access") as diagnostic:
            asyncio.run(main.sheets_status_handler(message))

        diagnostic.assert_not_called()
        self.assertEqual(message.responses, ["Команда доступна только оператору."])

    def test_operator_command_returns_safe_category_and_attempts(self):
        message = _DiagnosticMessage(next(iter(main.ADMIN_IDS)))
        status = sheets_client.SheetsStartupStatus(
            ready=False,
            reason="quota",
            attempts=4,
            hint="external secret and raw error must not be displayed",
        )
        with patch.object(main, "diagnose_sheets_access", return_value=status):
            asyncio.run(main.sheets_status_handler(message))

        self.assertEqual(len(message.responses), 1)
        response = message.responses[0]
        self.assertIn("Категория: квота Google Sheets", response)
        self.assertIn("Попытки: 4", response)
        self.assertIn("Проверьте квоту Google Sheets", response)
        self.assertNotIn("external secret", response)
        self.assertNotIn("raw error", response)

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

    def test_run_bot_starts_both_polling_processes_once_after_sheets_are_ready(self):
        events = []
        fleet_bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
        client_bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))

        def mark_sheets_ready():
            events.append("sheets_ready")

        async def start_fleet_polling(bot, admin_bot):
            self.assertIs(bot, fleet_bot)
            self.assertIs(admin_bot, client_bot)
            self.assertIn("sheets_ready", events)
            events.append("fleet_polling")

        async def start_client_polling(bot, admin_bot):
            self.assertIs(bot, client_bot)
            self.assertIs(admin_bot, fleet_bot)
            self.assertIn("sheets_ready", events)
            events.append("client_polling")

        with (
            patch.object(main, "TOKEN", "fleet-token"),
            patch.object(main, "CLIENT_BOT_TOKEN", "client-token"),
            patch.object(main, "ensure_sheets_ready", side_effect=mark_sheets_ready) as ready,
            patch.object(main, "Bot", side_effect=[fleet_bot, client_bot]) as bot,
            patch.object(main, "run_fleet_bot", side_effect=start_fleet_polling) as run_fleet_bot,
            patch.object(main, "run_client_bot", side_effect=start_client_polling) as run_client_bot,
        ):
            asyncio.run(main.run_bot())

        ready.assert_called_once_with()
        self.assertEqual(bot.call_count, 2)
        run_fleet_bot.assert_awaited_once_with(fleet_bot, client_bot)
        run_client_bot.assert_awaited_once_with(client_bot, fleet_bot)
        self.assertEqual(events[0], "sheets_ready")
        self.assertCountEqual(events[1:], ["fleet_polling", "client_polling"])
        fleet_bot.session.close.assert_awaited_once_with()
        client_bot.session.close.assert_awaited_once_with()

    def test_run_bot_propagates_polling_error_without_reordering_startup(self):
        events = []
        fleet_bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
        client_bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
        polling_error = RuntimeError("fleet polling failed")

        def mark_sheets_ready():
            events.append("sheets_ready")

        async def fail_fleet_polling(bot, admin_bot):
            self.assertIs(bot, fleet_bot)
            self.assertIs(admin_bot, client_bot)
            self.assertEqual(events, ["sheets_ready"])
            events.append("fleet_polling")
            raise polling_error

        async def start_client_polling(bot, admin_bot):
            self.assertIs(bot, client_bot)
            self.assertIs(admin_bot, fleet_bot)
            self.assertEqual(events, ["sheets_ready", "fleet_polling"])
            events.append("client_polling")

        with (
            patch.object(main, "TOKEN", "fleet-token"),
            patch.object(main, "CLIENT_BOT_TOKEN", "client-token"),
            patch.object(main, "ensure_sheets_ready", side_effect=mark_sheets_ready),
            patch.object(main, "Bot", side_effect=[fleet_bot, client_bot]),
            patch.object(main, "run_fleet_bot", side_effect=fail_fleet_polling) as run_fleet_bot,
            patch.object(main, "run_client_bot", side_effect=start_client_polling) as run_client_bot,
        ):
            with self.assertRaisesRegex(RuntimeError, "fleet polling failed"):
                asyncio.run(main.run_bot())

        run_fleet_bot.assert_awaited_once_with(fleet_bot, client_bot)
        run_client_bot.assert_awaited_once_with(client_bot, fleet_bot)
        self.assertEqual(events, ["sheets_ready", "fleet_polling", "client_polling"])
        fleet_bot.session.close.assert_awaited_once_with()
        client_bot.session.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()