"""引擎纯函数测试：精确中位数、阈值预算、跨点同值频率。运行：python -m unittest"""
import random
import statistics
import unittest

from src.services import median_engine as engine


def run_protocol(datasets):
    """模拟完整询问协议：datasets = {code: [centi...]}，返回 (found, asked, counts)。"""
    points = [{'code': c, 'name': c, 'version': 1, 'count': len(v)} for c, v in datasets.items()]
    total = sum(len(v) for v in datasets.values())
    ranks = engine.ranks_for(total)
    asked, counts = set(), {}
    for _ in range(1000):
        kind, payload = engine.next_steps(points, ranks, asked, counts)
        if kind == 'done':
            return payload, asked, counts
        if kind == 'wait':
            raise AssertionError('模拟中不应出现 wait（回执即时到账）')
        for t in payload:
            for c, values in datasets.items():
                if (c, t) in asked:
                    continue
                asked.add((c, t))
                counts[(c, t)] = sum(1 for v in values if v <= t)
    raise AssertionError('协议未在1000轮内收敛')


def expected_middle(values):
    s = sorted(values)
    n = len(s)
    return [s[n // 2]] if n % 2 else [s[n // 2 - 1], s[n // 2]]


class EngineTest(unittest.TestCase):
    def check(self, datasets):
        found, asked, counts = run_protocol(datasets)
        all_values = [v for vs in datasets.values() for v in vs]
        self.assertEqual(found, expected_middle(all_values))
        for code in datasets:
            n = len([1 for (c, _) in asked if c == code])
            self.assertLessEqual(n, engine.BUDGET, f'{code} 超出阈值预算: {n}')
        result = engine.build_result(
            [{'code': c, 'name': c, 'version': 1, 'count': len(v)} for c, v in datasets.items()],
            engine.ranks_for(len(all_values)), asked, counts, found)
        self.assertAlmostEqual(result['median'], statistics.median(all_values) / 100, places=9)
        return result

    def test_odd_total(self):
        self.check({'A': [500, 510, 520], 'B': [530, 540]})

    def test_even_total_average_of_two_middles(self):
        result = self.check({'A': [500, 520], 'B': [510, 530]})
        self.assertEqual(result['middle_values'], [5.10, 5.20])
        self.assertEqual(result['median'], 5.15)

    def test_single_sample(self):
        result = self.check({'A': [777]})
        self.assertEqual(result['median'], 7.77)

    def test_single_point(self):
        self.check({'A': [100, 200, 300, 300, 9000]})

    def test_empty_point_allowed(self):
        self.check({'A': [], 'B': [505, 515, 525]})

    def test_all_equal_values(self):
        result = self.check({'A': [600] * 5, 'B': [600] * 4})
        self.assertEqual(result['median'], 6.0)
        freq = result['median_frequency'][0]
        self.assertEqual(freq['total'], 9)
        self.assertEqual(freq['points_sharing'], 2)

    def test_boundary_values(self):
        self.check({'A': [0, 0, 10000], 'B': [10000, 5000]})

    def test_zero_only(self):
        result = self.check({'A': [0, 0], 'B': [0]})
        self.assertEqual(result['median'], 0.0)

    def test_cross_point_ties_reported(self):
        result = self.check({'A': [500, 501, 502], 'B': [501, 503], 'C': [900]})
        freq = {f['value']: f for f in result['median_frequency']}
        self.assertEqual(freq[5.01]['per_point'], {'A': 1, 'B': 1, 'C': 0})
        self.assertEqual(freq[5.01]['points_sharing'], 2)

    def test_random_datasets_match_statistics(self):
        rng = random.Random(42)
        for trial in range(60):
            datasets = {}
            for i in range(rng.randint(1, 5)):
                n = rng.randint(0, 140)
                mean = rng.uniform(3.0, 9.0)
                datasets[f'P{i}'] = [min(10000, max(0, round(rng.gauss(mean, 0.8) * 100)))
                                     for _ in range(n)]
            if sum(len(v) for v in datasets.values()) < 1:
                continue
            self.check(datasets)

    def test_budget_worst_case(self):
        # 网格 10001 个刻度：两次二分各 14 问 + 验证轮至多 2 问 = 30，恰好在预算内
        rng = random.Random(7)
        worst = 0
        for _ in range(200):
            datasets = {f'P{i}': [rng.randint(0, 10000) for _ in range(rng.randint(1, 200))]
                        for i in range(4)}
            _, asked, _ = run_protocol(datasets)
            worst = max(worst, max(len([1 for (c, _) in asked if c == code]) for code in datasets))
        self.assertLessEqual(worst, engine.BUDGET)

    def test_ranks(self):
        self.assertEqual(engine.ranks_for(1), [1])
        self.assertEqual(engine.ranks_for(5), [3])
        self.assertEqual(engine.ranks_for(10), [5, 6])
        with self.assertRaises(ValueError):
            engine.ranks_for(0)


if __name__ == '__main__':
    unittest.main()
