"""端到端栈测试：FastAPI 应用 + 检验点代理线程 + worker 事件处理 + fakeredis。

覆盖：登录、检验点上线、发起合批研究、等待到出数、点侧 oracle 核对、
掉线期间更新事件、计算期间更新导致版本失效（stale）。
运行：python -m unittest tests.test_e2e_stack
"""
import os
import statistics
import tempfile
import threading
import time
import unittest

_tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_tmp, 'e2e.db')
os.environ['REDIS_URL'] = 'redis://fakeredis:6379/0'
os.environ['JWT_SECRET'] = 'e2e-secret'
os.environ['SEED_DEMO'] = 'false'
os.environ['POINT_DIR'] = os.path.join(_tmp, 'points')
os.environ['POINT_TOKEN'] = 'e2e-point-token'
os.environ['POINT_OFFLINE_AFTER'] = '2'

import fakeredis
import redis as redis_lib

FAKE = fakeredis.FakeStrictRedis()
redis_lib.Redis.from_url = lambda url, **kw: FAKE  # 所有 src 模块共享同一假 Redis

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config.database import Base, engine, Session
from src.main import app
from src.models.median import MedianRun
from src.models.user import User
from src.point_store import PointStore
from src.schemas.median import HeartbeatInput
from src.services import median as median_svc
from src.utils.security import hash_password
from src import point_agent, worker
from scripts.check_median_oracle import main as oracle_main

EVENTS, PROC = worker.EVENTS, worker.PROCESSING


def pump():
    """把 median-events 队列处理到空（模拟 worker 的 median_loop）。"""
    while True:
        raw = FAKE.lmove(EVENTS, PROC, 'LEFT', 'RIGHT')
        if raw is None:
            return
        worker.process_median_event(raw)
        FAKE.lrem(PROC, 1, raw)


def run_status(run_id):
    with Session() as db:
        return db.get(MedianRun, run_id).status


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        with Session() as db:
            for role in ('admin', 'analyst', 'viewer'):
                db.add(User(email=f'{role}@demo.local', role=role,
                            password_hash=hash_password('Research2026!')))
            db.commit()
        # 检验点心跳走服务层（等价于 HTTP 通道，HTTP 路由单独测）；
        # sqlite 测试库并发写入较弱，这里串行化心跳（PostgreSQL 下无此需要）
        hb_lock = threading.Lock()

        def _heartbeat(spec, store):
            with hb_lock, Session() as db:
                median_svc.heartbeat(db, HeartbeatInput(
                    code=spec['code'], name=spec['name'],
                    version=store.version(), count=store.count()))

        point_agent.heartbeat = _heartbeat
        point_agent.HEARTBEAT_SECONDS = 0.5
        cls.threads = [threading.Thread(target=point_agent.point_loop, args=(spec, i),
                                        daemon=True)
                       for i, spec in enumerate(point_agent.POINTS)]
        for t in cls.threads:
            t.start()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def token(self, email):
        r = self.client.post('/auth/login', json={'email': email, 'password': 'Research2026!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def wait_for(self, fn, timeout=15, label='condition'):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if fn():
                return True
            time.sleep(0.1)
        self.fail(f'超时：{label}')

    def drive_run(self, run_id, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pump()
            if run_status(run_id) in ('completed', 'failed', 'stale'):
                return run_status(run_id)
            time.sleep(0.05)
        self.fail(f'运行 {run_id} 未在{timeout}s内出数')

    def test_00_points_come_online(self):
        auth = self.token('viewer@demo.local')
        self.wait_for(lambda: len(self.client.get('/points', headers=auth).json()) ==
                      len(point_agent.POINTS), label='检验点注册')
        points = self.client.get('/points', headers=auth).json()
        self.assertTrue(all(p['online'] for p in points), '全部在线')
        self.assertTrue(all(p['version'] == 1 for p in points))
        self.assertTrue(all(p['sample_count'] > 0 for p in points))

    def test_01_heartbeat_route_auth(self):
        r = self.client.post('/points/heartbeat',
                             json={'code': 'PT-X', 'name': '非法点', 'version': 1, 'count': 1},
                             headers={'X-Point-Token': 'wrong'})
        self.assertEqual(r.status_code, 403)
        r = self.client.post('/points/heartbeat',
                             json={'code': 'PT-X', 'name': '手动点', 'version': 1, 'count': 1},
                             headers={'X-Point-Token': 'e2e-point-token'})
        self.assertEqual(r.status_code, 200)

    def test_02_median_run_completes_and_matches_oracle(self):
        auth = self.token('analyst@demo.local')
        points = self.client.get('/points', headers=auth).json()
        real = [p for p in points if p['code'] != 'PT-X']
        r = self.client.post('/median-runs', headers=auth,
                             json={'name': '秋季合批中位数', 'point_ids': [p['id'] for p in real]})
        self.assertEqual(r.status_code, 200, r.text)
        run_id = r.json()['id']
        self.assertEqual(self.drive_run(run_id), 'completed')
        detail = self.client.get(f'/median-runs/{run_id}', headers=auth).json()
        # 点侧 oracle：直接读原值重算
        self.assertEqual(oracle_main(run_id), 0)
        # 结果结构：预算、跨点同值、锁定版本
        for p in detail['result']['points']:
            self.assertLessEqual(p['asked_count'], 30)
        self.assertTrue(detail['result']['median_frequency'])
        self.assertEqual(detail['result']['total_count'],
                         sum(p['sample_count'] for p in real))
        type(self).completed_run_id = run_id

    def test_03_offline_update_is_flagged(self):
        auth = self.token('analyst@demo.local')
        points = [p for p in self.client.get('/points', headers=auth).json()
                  if p['code'] == 'PT-EAST']
        pid = points[0]['id']
        r = self.client.post(f'/points/{pid}/online', headers=auth, json={'online': False})
        self.assertEqual(r.status_code, 200)
        time.sleep(2.6)  # 超过 POINT_OFFLINE_AFTER，总台判定掉线
        gone = [p for p in self.client.get('/points', headers=auth).json()
                if p['id'] == pid][0]
        self.assertFalse(gone['online'], '掉线状态应可见')
        # 掉线期间下发数据更新（指令积压在点侧队列）
        r = self.client.post(f'/points/{pid}/update', headers=auth,
                             json={'mode': 'regenerate', 'n': 60})
        self.assertEqual(r.status_code, 200)
        old_version = points[0]['version']
        r = self.client.post(f'/points/{pid}/online', headers=auth, json={'online': True})
        self.assertEqual(r.status_code, 200)
        self.wait_for(lambda: [p for p in self.client.get('/points', headers=auth).json()
                               if p['id'] == pid][0]['version'] > old_version,
                      label='恢复后版本上报')
        events = self.client.get('/points/events', headers=auth).json()
        kinds = [e['kind'] for e in events if e['point_code'] == 'PT-EAST']
        self.assertIn('offline_update', kinds, '掉线期间更新必须被标注')

    def test_04_run_goes_stale_when_point_updates_mid_run(self):
        auth = self.token('analyst@demo.local')
        points = [p for p in self.client.get('/points', headers=auth).json()
                  if p['code'] != 'PT-X']
        victim = next(p for p in points if p['code'] == 'PT-NORTH')
        # 先让 victim 掉线，发起运行（锁定当前版本），询问积压在点侧队列
        self.client.post(f"/points/{victim['id']}/online", headers=auth, json={'online': False})
        r = self.client.post('/median-runs', headers=auth,
                             json={'name': '中途变更演练',
                                   'point_ids': [p['id'] for p in points]})
        run_id = r.json()['id']
        pump()  # 总台发出第一轮阈值询问
        # 掉线期间数据更新（排在积压询问之后，点侧按序执行）
        self.client.post(f"/points/{victim['id']}/update", headers=auth,
                         json={'mode': 'regenerate', 'n': 50})
        self.client.post(f"/points/{victim['id']}/online", headers=auth, json={'online': True})
        self.assertEqual(self.drive_run(run_id), 'stale')
        detail = self.client.get(f'/median-runs/{run_id}', headers=auth).json()
        self.assertIn('重新发起', detail['error'])

    def test_05_permissions(self):
        viewer = self.token('viewer@demo.local')
        points = self.client.get('/points', headers=viewer).json()
        r = self.client.post('/median-runs', headers=viewer,
                             json={'name': '越权测试', 'point_ids': [points[0]['id']]})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/points/{points[0]['id']}/online", headers=viewer,
                             json={'online': False})
        self.assertEqual(r.status_code, 403)
        r = self.client.get('/median-runs', headers=viewer)
        self.assertEqual(r.status_code, 200)


if __name__ == '__main__':
    unittest.main()
