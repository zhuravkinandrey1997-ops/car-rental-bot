import unittest

import main


class OperationRetryStateTest(unittest.TestCase):
    def test_sheets_failure_prompt_hides_raw_provider_error(self):
        raw_error = RuntimeError("private provider response: token=do-not-display")

        prompt = main._sheets_retry_prompt("сохранить расход", raw_error)

        self.assertIn("Введённые данные и текущий шаг сохранены", prompt)
        self.assertIn("Подтвердить", prompt)
        self.assertNotIn("do-not-display", prompt)
        self.assertNotIn("private provider response", prompt)


if __name__ == "__main__":
    unittest.main()