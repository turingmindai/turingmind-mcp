"""Tests for SQLite write serialization."""

import threading
import time
import unittest

from turingmind_mcp.sqlite_guard import (
    run_serialized_write,
    serialized_sqlite_write,
)


class TestSerializedWrite(unittest.TestCase):
    def test_run_serialized_write_serializes_threads(self):
        order: list[str] = []
        barrier = threading.Barrier(2)

        def worker(name: str) -> None:
            barrier.wait()
            run_serialized_write(lambda: order.append(f"{name}-start") or time.sleep(0.05) or order.append(f"{name}-end"))

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)

        self.assertEqual(len(order), 4)
        self.assertIn("-start", order[0])
        self.assertIn("-end", order[1])
        self.assertIn("-start", order[2])
        self.assertIn("-end", order[3])
        self.assertEqual(order[0][0], order[1][0])
        self.assertEqual(order[2][0], order[3][0])
        self.assertNotEqual(order[0][0], order[2][0])

    def test_rlock_is_reentrant(self):
        depth = []

        def nested() -> None:
            with serialized_sqlite_write():
                depth.append(1)
                with serialized_sqlite_write():
                    depth.append(2)

        run_serialized_write(nested)
        self.assertEqual(depth, [1, 2])


if __name__ == "__main__":
    unittest.main()
