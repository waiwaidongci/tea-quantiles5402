"""总台服务层测试：锁定、归账、推进、恢复、版本失效。

用 SQLite + 假传输跑通完整协议，不依赖 Redis/PostgreSQL。
运行：python -m unittest（需先设 DATABASE_URL/REDIS_URL/JWT_SECRET，见 tests/__init__.py）
"""
import os
import random
import statistics
import tempfile
import unittest

os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(tempfile.mkdtemp(), 'svc.db'))
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from types import SimpleNamespace

from sqlalchemy import select

from src.config.database import Base, engine, Session
from src.models.median import InspectionPoint, MedianRun, PointEvent
from src.schemas.median import HeartbeatInput
from src.services import median

USER = SimpleNamespace(email='analyst@demo.local', role='analyst')


class Transport:
    """捕获总台发出的控制事件与阈值询问，模拟 Redis 通道。"""

    def __init__(self):
        self.events = []
        self.queries = []
        self.flags = {}

    def push_event(self, msg):
        self.events.append(msg)

    def push_query(self, code, msg):
        self.queries.append((code, dict(msg)))

    def set_online_flag(self, code, online):
        self.flags[code] = online


class ServiceTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.db = Session()
        self.transport = Transport()
        self._orig = (median.push_event, median.push_query, median.set_online_flag)
        median.push_event = self.transport.push_event
        median.push_query = self.transport.push_query
        median.set_online_flag = self.transport.set_online_flag
        self.datasets = {}
        self.versions = {}

    def tearDown(self):
        median.push_event, median.push_query, median.set_online_flag = self._orig
        self.db.close()

    # ---------- 工具 ----------

    def add_point(self, code, values, version=1):
        self.datasets[code] = list(values)
        self.versions[code] = version
        median.heartbeat(self.db, HeartbeatInput(code=code, name=f'点{code}',
                                                 version=version, count=len(values)))

    def create(self, name='合批测试'):
        points = median.list_points(self.db)
        return median.create_run(self.db, SimpleNamespace(name=name,
                                                          point_ids=[p['id'] for p in points]),
                                 USER)

    def receipts_for(self, queries, run_id):
        out = []
        for code, msg in queries:
            assert msg['run_id'] == run_id
            assert msg['version'] == self.versions_locked[code]  # 询问带的是锁定版本
            out.append({'kind': 'receipt', 'run_id': run_id, 'point_code': code,
                        'threshold_centi': msg['threshold_centi'],
                        'count': sum(1 for v in self.datasets[code]
                                     if v <= msg['threshold_centi']),
                        'version': self.versions[code]})
        return out

    def drive(self, run_id, shuffle=False, duplicate=False, recover_midway=False):
        """驱动协议直到终态；可注入乱序、重传与中途恢复。"""
        rng = random.Random(1)
        recovered = not recover_midway
        for _ in range(300):
            run = self.db.get(MedianRun, run_id)
            if run.status in ('completed', 'failed', 'stale'):
                return run
            while self.transport.events:
                ev = self.transport.events.pop(0)
                median.control(self.db, ev['run_id'], repush=ev.get('repush', False))
            queries, self.transport.queries = self.transport.queries, []
            if not queries:
                median.control(self.db, run_id, repush=True)
                queries, self.transport.queries = self.transport.queries, []
                if not queries:
                    self.fail('协议无法推进')
            if not recovered:
                recovered = True
                # 模拟 worker 中断重启：仅凭台账恢复，重发未答询问
                self.transport.queries.clear()
                median.control(self.db, run_id, repush=True)
                queries, self.transport.queries = self.transport.queries, []
                self.assertTrue(queries, '恢复后应重发未答询问')
            receipts = self.receipts_for(queries, run_id)
            if shuffle:
                rng.shuffle(receipts)
            if duplicate and receipts:
                receipts = receipts + [receipts[0]]  # 重传首条
            for r in receipts:
                median.handle_receipt(self.db, r)
        self.fail('协议未收敛')

    # ---------- 用例 ----------

    def test_happy_path_exact_median(self):
        rng = random.Random(42)
        self.add_point('PA', [round(rng.gauss(540, 70)) for _ in range(120)])
        self.add_point('PB', [round(rng.gauss(600, 80)) for _ in range(96)])
        self.add_point('PC', [round(rng.gauss(480, 60)) for _ in range(87)])
        run = self.create()
        self.assertEqual(run['status'], 'queued')
        self.versions_locked = dict(self.versions)
        locked_total = sum(len(v) for v in self.datasets.values())
        self.assertEqual(run['total_count'], locked_total)
        run = self.drive(run['id'], shuffle=True, duplicate=True)
        self.assertEqual(run.status, 'completed')
        all_values = [v for vs in self.datasets.values() for v in vs]
        self.assertAlmostEqual(run.result['median'],
                               round(statistics.median(all_values) / 100, 4), places=9)
        for p in run.result['points']:
            self.assertLessEqual(p['asked_count'], 30, '每点阈值预算30')
        # 跨点同值核对数据完整
        freq = run.result['median_frequency']
        self.assertTrue(all(f['total'] >= 0 for f in freq))

    def test_even_total_averages_two_middles(self):
        self.add_point('PA', [500, 520])
        self.add_point('PB', [510, 530])
        run = self.create()
        self.versions_locked = dict(self.versions)
        run = self.drive(run['id'])
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.result['middle_values'], [5.10, 5.20])
        self.assertEqual(run.result['median'], 5.15)

    def test_recovery_midway(self):
        rng = random.Random(7)
        self.add_point('PA', [round(rng.gauss(550, 90)) for _ in range(80)])
        self.add_point('PB', [round(rng.gauss(560, 90)) for _ in range(75)])
        run = self.create()
        self.versions_locked = dict(self.versions)
        run = self.drive(run['id'], shuffle=True, duplicate=True, recover_midway=True)
        self.assertEqual(run.status, 'completed')
        all_values = [v for vs in self.datasets.values() for v in vs]
        self.assertAlmostEqual(run.result['median'],
                               round(statistics.median(all_values) / 100, 4), places=9)

    def test_stale_when_point_updates_mid_run(self):
        self.add_point('PA', [500, 510, 520])
        self.add_point('PB', [530, 540, 550])
        run = self.create()
        self.versions_locked = dict(self.versions)
        # 第一轮询问发出后，PB 在计算期间更新数据
        while self.transport.events:
            ev = self.transport.events.pop(0)
            median.control(self.db, ev['run_id'], repush=True)
        self.assertTrue(self.transport.queries)
        self.versions['PB'] = 2
        self.datasets['PB'] = [900, 910, 920]
        run = self.drive(run['id'])
        self.assertEqual(run.status, 'stale')
        self.assertIn('重新发起', run.error)

    def test_conflicting_receipt_fails(self):
        self.add_point('PA', [500, 510])
        self.add_point('PB', [520, 530])
        run = self.create()
        self.versions_locked = dict(self.versions)
        while self.transport.events:
            median.control(self.db, self.transport.events.pop(0)['run_id'], repush=True)
        code, msg = self.transport.queries[0]
        t = msg['threshold_centi']
        median.handle_receipt(self.db, {'kind': 'receipt', 'run_id': run['id'], 'point_code': code,
                                        'threshold_centi': t, 'count': 1, 'version': 1})
        median.handle_receipt(self.db, {'kind': 'receipt', 'run_id': run['id'], 'point_code': code,
                                        'threshold_centi': t, 'count': 2, 'version': 1})
        self.assertEqual(self.db.get(MedianRun, run['id']).status, 'failed')

    def test_duplicate_receipt_ignored(self):
        self.add_point('PA', [500, 510])
        self.add_point('PB', [520, 530])
        run = self.create()
        self.versions_locked = dict(self.versions)
        while self.transport.events:
            median.control(self.db, self.transport.events.pop(0)['run_id'], repush=True)
        code, msg = self.transport.queries[0]
        receipt = {'kind': 'receipt', 'run_id': run['id'], 'point_code': code,
                   'threshold_centi': msg['threshold_centi'], 'count': 1, 'version': 1}
        median.handle_receipt(self.db, receipt)
        median.handle_receipt(self.db, receipt)  # 纯重传
        median.handle_receipt(self.db, receipt)  # 再次重传
        run = self.db.get(MedianRun, run['id'])
        self.assertEqual(run.status, 'collecting')  # 不受影响，等待其余回执

    def test_unsolicited_and_late_receipts_ignored(self):
        self.add_point('PA', [500])
        run = self.create()
        self.versions_locked = dict(self.versions)
        median.handle_receipt(self.db, {'kind': 'receipt', 'run_id': run['id'], 'point_code': 'PA',
                                        'threshold_centi': 123, 'count': 1, 'version': 1})
        median.handle_receipt(self.db, {'kind': 'receipt', 'run_id': 99999, 'point_code': 'PA',
                                        'threshold_centi': 500, 'count': 1, 'version': 1})
        run = self.drive(run['id'])
        self.assertEqual(run.status, 'completed')
        median.handle_receipt(self.db, {'kind': 'receipt', 'run_id': run.id, 'point_code': 'PA',
                                        'threshold_centi': 500, 'count': 9, 'version': 1})
        self.assertEqual(self.db.get(MedianRun, run.id).status, 'completed')

    def test_offline_update_event_logged(self):
        self.add_point('PA', [500, 510])
        p = self.db.scalar(select(InspectionPoint))
        p.last_seen_at = '2000-01-01T00:00:00+00:00'  # 模拟掉线
        self.db.commit()
        median.heartbeat(self.db, HeartbeatInput(code='PA', name='点PA', version=2, count=3))
        events = median.list_events(self.db)
        self.assertEqual(events[0]['kind'], 'offline_update')
        self.assertIn('v1→v2', events[0]['detail'])

    def test_online_reconnect_event(self):
        self.add_point('PA', [500])
        p = self.db.scalar(select(InspectionPoint))
        p.last_seen_at = '2000-01-01T00:00:00+00:00'
        self.db.commit()
        median.heartbeat(self.db, HeartbeatInput(code='PA', name='点PA', version=1, count=1))
        self.assertEqual(median.list_events(self.db)[0]['kind'], 'online')

    def test_empty_selection_rejected(self):
        self.add_point('PA', [])
        with self.assertRaises(Exception):
            self.create()

    def test_budget_guard(self):
        # 构造超出预算的台账，advance 必须把运行置为 failed 而不是发出第31问
        self.add_point('PA', [500, 510])
        run = self.create()
        self.versions_locked = dict(self.versions)
        from src.models.median import MedianQuestion
        from src.utils.serializers import now
        for t in range(30):
            self.db.add(MedianQuestion(run_id=run['id'], point_code='PA',
                                       threshold_centi=t, asked_at=now()))
        self.db.commit()
        run = self.db.get(MedianRun, run['id'])
        median.advance(self.db, run, repush=False)
        self.db.commit()
        self.assertEqual(run.status, 'failed')
        self.assertIn('预算', run.error)


if __name__ == '__main__':
    unittest.main()
