import datetime
import calendar
import unittest
from unittest.mock import patch

from main import calendar_keyboard, issuance_date_keyboard


def _button_for_day(keyboard, year: int, month: int, day: int):
    for week_index, week in enumerate(calendar.monthcalendar(year, month)):
        if day in week:
            return keyboard.inline_keyboard[3 + week_index][week.index(day)]
    raise AssertionError(f"Day {day} was not found in calendar")


class IssuanceCalendarTest(unittest.TestCase):
    def test_issue_calendar_disables_future_dates_but_allows_backdating(self):
        keyboard = calendar_keyboard(
            "issuance",
            "issue",
            year=2026,
            month=9,
            maximum_date=datetime.date(2026, 9, 12),
            allow_past=True,
        )

        self.assertEqual(
            _button_for_day(keyboard, 2026, 9, 11).callback_data,
            "calendar:issuance:issue:day:2026-09-11",
        )
        self.assertEqual(
            _button_for_day(keyboard, 2026, 9, 13).callback_data,
            "calendar:noop",
        )

    def test_end_calendar_disables_dates_before_issue(self):
        keyboard = calendar_keyboard(
            "issuance",
            "end",
            year=2026,
            month=9,
            minimum_date=datetime.date(2026, 9, 10),
        )

        self.assertEqual(
            _button_for_day(keyboard, 2026, 9, 9).callback_data,
            "calendar:noop",
        )
        self.assertEqual(
            _button_for_day(keyboard, 2026, 9, 10).callback_data,
            "calendar:issuance:end:day:2026-09-10",
        )

    def test_issuance_date_keyboard_has_current_time_and_calendar_options(self):
        fixed_now = datetime.datetime(
            2026,
            9,
            12,
            14,
            37,
            tzinfo=datetime.timezone(datetime.timedelta(hours=3)),
        )
        with patch("main._now_moscow", return_value=fixed_now):
            keyboard = issuance_date_keyboard()

        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(buttons[0].text, "⏱ Сейчас (14:37)")
        self.assertEqual(buttons[0].callback_data, "issue_date_now")
        self.assertEqual(buttons[1].callback_data, "issue_date_calendar")


if __name__ == "__main__":
    unittest.main()