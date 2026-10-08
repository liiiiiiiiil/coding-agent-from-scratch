import unittest
from src.example import transform

class PublicBehavior(unittest.TestCase):
    def test_doubles_positive_and_negative(self):
        self.assertEqual(transform(3), 6)
        self.assertEqual(transform(-2), -4)
