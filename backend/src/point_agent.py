"""检验点代理进程：原值留在点侧，只回答阈值份数。

行为模型：
- 每个检验点一个线程、一个本地 SQLite 库（POINT_DIR 下），版本号随数据变更 +1。
- 经 Redis 队列 point:{code} 接收消息：query（阈值询问）、update（数据变更）、ping。
- 回执只含（运行, 点位, 阈值, 份数, 当前版本），经 median-events 回到总台。
- Redis 键 point:{code}:online 模拟连接开关；掉线时不处理队列也不发心跳，
  积压在队列里的更新会在恢复后按序执行，新版本随心跳上报（掉线期间更新）。
"""
import json
import logging
import os
import random
import threading
import time
import urllib.request

from redis import Redis

from src.point_store import PointStore

POINT_DIR = os.environ.get('POINT_DIR', '/point-data')
API_URL = os.environ.get('API_URL', 'http://backend:8000')
POINT_TOKEN = os.environ.get('POINT_TOKEN', 'local-demo-point-token')
EVENTS_QUEUE = 'median-events'
HEARTBEAT_SECONDS = 4

POINTS = [
    {'code': 'PT-EAST', 'name': '城东检验点', 'n': 120, 'mean': 5.40, 'sd': 0.70},
    {'code': 'PT-NORTH', 'name': '山口检验点', 'n': 96, 'mean': 5.95, 'sd': 0.80},
    {'code': 'PT-RIVER', 'name': '江畔检验点', 'n': 132, 'mean': 6.30, 'sd': 0.65},
    {'code': 'PT-HILL', 'name': '坡地检验点', 'n': 84, 'mean': 4.85, 'sd': 0.75},
]


def gen_values(n, mean, sd, seed):
    """确定性演示数据：厘百分点整数网格上的含水率。"""
    rng = random.Random(seed)
    return [min(10000, max(0, round(rng.gauss(mean, sd) * 100))) for _ in range(n)]


def heartbeat(spec, store):
    payload = json.dumps({'code': spec['code'], 'name': spec['name'],
                          'version': store.version(), 'count': store.count()}).encode()
    req = urllib.request.Request(API_URL + '/points/heartbeat', data=payload,
                                 headers={'Content-Type': 'application/json',
                                          'X-Point-Token': POINT_TOKEN})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        logging.warning('heartbeat failed for %s', spec['code'])


def handle(spec, store, redis, msg):
    kind = msg.get('type')
    if kind == 'query':
        receipt = {'kind': 'receipt', 'run_id': msg['run_id'], 'point_code': spec['code'],
                   'threshold_centi': int(msg['threshold_centi']),
                   'count': store.count_le(int(msg['threshold_centi'])),
                   'version': store.version()}
        redis.rpush(EVENTS_QUEUE, json.dumps(receipt, ensure_ascii=False))
    elif kind == 'update':
        if msg.get('mode') == 'set':
            values = [int(v) for v in msg['values_centi']]
        else:
            values = gen_values(int(msg.get('n', 100)), spec['mean'], spec['sd'],
                                seed=msg.get('seed', random.randrange(1 << 30)))
        store.replace(values)
        heartbeat(spec, store)
    elif kind == 'ping':
        heartbeat(spec, store)


def point_loop(spec, seed_offset):
    store = PointStore(os.path.join(POINT_DIR, spec['code'] + '.db'))
    store.seed_if_empty(gen_values(spec['n'], spec['mean'], spec['sd'], 42 + seed_offset))
    redis = Redis.from_url(os.environ['REDIS_URL'])
    online_key = f"point:{spec['code']}:online"
    queue = f"point:{spec['code']}"
    last_beat = 0.0
    was_offline = False
    while True:
        try:
            flag = redis.get(online_key)
            if flag is not None and flag != b'1':
                was_offline = True
                time.sleep(0.5)  # 掉线：不处理队列、不发心跳
                continue
            if was_offline:
                # 恢复连接：先按序处理积压消息（掉线期间下达的更新先生效、版本号变更），
                # 再上报心跳——总台由此识别「掉线期间更新」。
                was_offline = False
                while True:
                    pending = redis.lpop(queue)
                    if pending is None:
                        break
                    handle(spec, store, redis, json.loads(pending))
                heartbeat(spec, store)
                last_beat = time.time()
            if time.time() - last_beat > HEARTBEAT_SECONDS:
                heartbeat(spec, store)
                last_beat = time.time()
            msg = redis.blpop(queue, timeout=2)
            if msg:
                handle(spec, store, redis, json.loads(msg[1]))
        except Exception:
            logging.exception('point %s loop error', spec['code'])
            time.sleep(1)


def main():
    logging.basicConfig(level=logging.INFO)
    threads = [threading.Thread(target=point_loop, args=(spec, i), daemon=True, name=spec['code'])
               for i, spec in enumerate(POINTS)]
    for t in threads:
        t.start()
    while True:
        open('/tmp/points-heartbeat', 'w').close()
        time.sleep(2)


if __name__ == '__main__':
    main()
