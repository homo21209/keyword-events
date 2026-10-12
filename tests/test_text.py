"""Проверки нормализации текста."""

import unittest

from core import text


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_punctuation(self):
        self.assertEqual(text.normalize("Часы остановились!"), "часы остановились")

    def test_yo_to_ye(self):
        self.assertEqual(text.normalize("Ещё идут"), "еще идут")
        self.assertEqual(text.normalize("ЕЩЁ"), "еще")

    def test_spaces_collapsed(self):
        self.assertEqual(text.normalize("  часы   пошли  "), "часы пошли")

    def test_empty(self):
        self.assertEqual(text.normalize(""), "")
        self.assertEqual(text.words("   !!! "), [])

    def test_punctuation_inside(self):
        self.assertEqual(text.normalize("часы, остановились."), "часы остановились")


class TestWords(unittest.TestCase):
    def test_split(self):
        self.assertEqual(text.words("Часы остановились!"), ["часы", "остановились"])

    def test_numbers_normalized(self):
        self.assertEqual(text.words("второй раз"), ["2", "раз"])

    def test_number_declension(self):
        self.assertEqual(text.words("на третьем часе"), ["на", "3", "часе"])

    def test_key_tuple(self):
        self.assertEqual(text.key("Часы! Пошли"), ("часы", "пошли"))


if __name__ == "__main__":
    unittest.main()