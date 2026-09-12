import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import (
    ADMIN_PLUS_ROLE,
    ensure_admin_plus,
    get_main_keyboard,
)


def _menu_labels(role: str) -> list[str]:
    keyboard = get_main_keyboard(123, role)
    return [
        button.text
        for row in keyboard.keyboard
        for button in row
    ]


class _Message:
    def __init__(self):
        self.from_user = SimpleNamespace(id=123)
        self.responses = []

    async def answer(self, text, **kwargs):
        self.responses.append(text)


class AdminPlusPermissionsTest(unittest.IsolatedAsyncioTestCase):
    def test_only_admin_plus_sees_cancellation_buttons(self):
        admin_plus_labels = _menu_labels(ADMIN_PLUS_ROLE)
        administrator_labels = _menu_labels("администратор")
        manager_labels = _menu_labels("менеджер")
        partner_labels = _menu_labels("партнёр")
        heavy_lux_labels = _menu_labels("partner_heavy_lux")

        self.assertIn("🚫 Отмена брони", admin_plus_labels)
        self.assertIn("❌ Отменить расход", admin_plus_labels)
        for labels in (
            administrator_labels,
            manager_labels,
            partner_labels,
            heavy_lux_labels,
        ):
            self.assertNotIn("🚫 Отмена брони", labels)
            self.assertNotIn("❌ Отменить расход", labels)

    async def test_non_admin_plus_is_rejected_by_protected_handler_guard(self):
        message = _Message()
        with patch(
            "main._manager_access_for_user",
            return_value={
                "user_id": "123",
                "role": "администратор",
                "active": True,
            },
        ):
            allowed = await ensure_admin_plus(message)

        self.assertFalse(allowed)
        self.assertEqual(
            message.responses,
            [
                "🚫 Ошибка доступа: отменять брони и расходы может только "
                "Администратор плюс."
            ],
        )

    async def test_admin_plus_passes_protected_handler_guard(self):
        message = _Message()
        with patch(
            "main._manager_access_for_user",
            return_value={
                "user_id": "123",
                "role": "Администратор плюс",
                "active": True,
            },
        ):
            allowed = await ensure_admin_plus(message)

        self.assertTrue(allowed)
        self.assertEqual(message.responses, [])


if __name__ == "__main__":
    unittest.main()