"""独立 worker：分布计算任务 + 合批中位数事件循环。

- analysis-queue：分布运行（原有逻辑，单 worker，重启恢复 queued/running）。
- median-events：合批中位数的 control / receipt 事件，BLMOVE 可靠搬运，
  处理中列表在重启时倒回主队列；回执按（点位, 阈值）归账，重复投递幂等。
- 周期性 sweep：为未终结的合批运行补发 control(repush)，
  中断后恢复原版锁定与已收回执，重发未答询问。
"""
import json
import logging
import os
import threading
import time
from pathlib import Path

from redis import Redis
from sqlalchemy import select

from src.config.database import Session
from src.models import user, study, observation, review, audit  # noqa: F401 注册模型
from src.models import median as median_model  # noqa: F401
from src.models.median import MedianRun
from src.models.run import Run
from src.services import median as median_svc
from src.services.algorithm import calculate
from src.utils.serializers import checksum

redis = Redis.from_url(os.environ['REDIS_URL'])
EVENTS = median_svc.EVENTS_QUEUE
PROCESSING = EVENTS + ':processing'
SWEEP_SECONDS = 15


def process_analysis(run_id):
    with Session() as db:
        run = db.get(Run, run_id)
        if not run or run.status in ('completed', 'failed'):
            return
        run.status = 'running'
        db.commit()
        try:
            if checksum(run.snapshot) != run.checksum:
                raise ValueError('输入快照校验失败')
            result = calculate(run.snapshot, run.parameters)
            result['input_sha256'] = run.checksum
            run.result = result
            run.status = 'completed'
            Path(f'/app/artifacts/run-{run_id}.json').write_text(
                json.dumps(result, ensure_ascii=False, allow_nan=False))
        except Exception as e:
            logging.exception('analysis failed')
            run.status = 'failed'
            run.error = str(e)
        db.commit()


def process_median_event(raw):
    msg = json.loads(raw)
    with Session() as db:
        if msg.get('kind') == 'receipt':
            median_svc.handle_receipt(db, msg)
        else:
            median_svc.control(db, int(msg['run_id']), repush=bool(msg.get('repush')))


def recover():
    """中断恢复：倒回处理中事件；重排分布任务；恢复合批运行的原版锁定与已收回执。"""
    while redis.lmove(PROCESSING, EVENTS, 'LEFT', 'RIGHT') is not None:
        pass
    with Session() as db:
        for run in db.scalars(select(Run).where(Run.status.in_(['queued', 'running']))):
            redis.rpush('analysis-queue', run.id)
        for rid in db.scalars(select(MedianRun.id)
                              .where(MedianRun.status.in_(['queued', 'collecting']))):
            redis.rpush(EVENTS, json.dumps({'kind': 'control', 'run_id': rid, 'repush': True}))


def analysis_loop():
    while True:
        Path('/tmp/worker-heartbeat').touch()
        job = redis.blpop('analysis-queue', timeout=2)
        if not job:
            continue
        try:
            process_analysis(int(job[1]))
        except Exception:
            logging.exception('analysis job failed')


def median_loop():
    last_sweep = 0.0
    while True:
        try:
            raw = redis.blmove(EVENTS, PROCESSING, timeout=2, src='LEFT', dest='RIGHT')
            if raw is not None:
                try:
                    process_median_event(raw)
                except Exception:
                    logging.exception('median event failed; requeue')
                    redis.lrem(PROCESSING, 1, raw)
                    redis.rpush(EVENTS, raw)
                    time.sleep(2)
                    continue
                redis.lrem(PROCESSING, 1, raw)
            if time.time() - last_sweep > SWEEP_SECONDS:
                last_sweep = time.time()
                with Session() as db:
                    ids = list(db.scalars(select(MedianRun.id)
                                          .where(MedianRun.status.in_(['queued', 'collecting']))))
                for rid in ids:
                    redis.rpush(EVENTS, json.dumps({'kind': 'control', 'run_id': rid,
                                                    'repush': True}))
        except Exception:
            logging.exception('median loop error')
            time.sleep(1)


def main():
    logging.basicConfig(level=logging.INFO)
    Path('/app/artifacts').mkdir(exist_ok=True)
    recover()
    threading.Thread(target=median_loop, daemon=True, name='median').start()
    analysis_loop()


if __name__ == '__main__':
    main()
