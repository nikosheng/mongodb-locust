import unittest

from pydantic import ValidationError

from bench.config import Load


class AutomaticWarmupTest(unittest.TestCase):
    def test_scales_with_users_and_spawn_rate(self):
        self.assertEqual(Load(users=100, spawn_rate=10, duration_seconds=10).warmup_seconds, 30)
        self.assertEqual(Load(users=200, spawn_rate=10, duration_seconds=10).warmup_seconds, 40)
        self.assertEqual(Load(users=101, spawn_rate=10, duration_seconds=10).warmup_seconds, 31)
        self.assertEqual(Load(users=100, spawn_rate=20, duration_seconds=10).warmup_seconds, 25)

    def test_manual_warmup_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Remove load.warmup_seconds"):
            Load(users=100, duration_seconds=10, warmup_seconds=30)

    def test_spawn_rate_must_be_positive(self):
        with self.assertRaises(ValidationError):
            Load(users=100, duration_seconds=10, spawn_rate=0)


if __name__ == "__main__":
    unittest.main()
