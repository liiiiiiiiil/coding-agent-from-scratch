import unittest
from src.example import transform


class PublicBehavior(unittest.TestCase):
    def test_doubles_positive_negative_and_zero(self):
        for value in (-17, -2, 0, 1, 3, 100, 1.5):
            with self.subTest(value=value):
                self.assertEqual(transform(value), value * 2)
