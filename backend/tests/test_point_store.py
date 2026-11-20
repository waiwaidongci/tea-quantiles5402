"""检验点本地存储测试。运行：python -m unittest"""
import os
import tempfile
import unittest

from src.point_store import PointStore


class PointStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'PT-T.db')

    def test_seed_version_and_counts(self):
        s = PointStore(self.path)
        self.assertEqual(s.version(), 0)
        self.assertTrue(s.seed_if_empty([100, 200, 200, 350]))
        self.assertEqual(s.version(), 1)
        self.assertEqual(s.count(), 4)
        self.assertEqual(s.count_le(199), 1)
        self.assertEqual(s.count_le(200), 3)  # 含等值
        self.assertEqual(s.count_le(10000), 4)
        self.assertFalse(s.seed_if_empty([1]))  # 已有数据不覆盖

    def test_replace_bumps_version(self):
        s = PointStore(self.path)
        s.seed_if_empty([100])
        s.replace([500, 600])
        self.assertEqual(s.version(), 2)
        self.assertEqual(s.count(), 2)
        self.assertEqual(s.count_le(100), 0)
        # 重新打开（模拟进程重启）版本与数据仍在
        s2 = PointStore(self.path)
        self.assertEqual(s2.version(), 2)
        self.assertEqual(s2.all_values(), [500, 600])

    def test_validation(self):
        s = PointStore(self.path)
        with self.assertRaises(ValueError):
            s.replace([10001])
        with self.assertRaises(ValueError):
            s.replace([-1])
        with self.assertRaises(ValueError):
            s.replace([5.5])


if __name__ == '__main__':
    unittest.main()
