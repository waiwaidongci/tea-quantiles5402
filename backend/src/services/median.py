"""合批中位数研究的总台服务。

职责边界：
- 开算时锁定各检验点版本与份数（MedianRun.points 快照 + checksum）。
- 只向检验点询问「不超过阈值的份数」，原值不离开点侧。
- 回执按（运行, 点位, 阈值）归账：乱序与重传幂等，冲突即失败。
- 只采纳锁定版本的回执；版本不符说明计算期间数据已更新，运行置 stale。
- 全部状态由台账（questions + receipts）决定，中断后恢复原版与已收回执即可续算。
"""
import json
import os
from datetime import datetime, timezone

from fastapi import HTTPException
from redis import Redis
from sqlalchemy import select

from src.models.median import InspectionPoint, MedianQuestion, MedianReceipt, MedianRun, PointEvent
from src.services import median_engine as engine
from src.services.audits import log
from src.utils.serializers import checksum, now, record

EVENTS_QUEUE = 'median-events'
OFFLINE_AFTER = float(os.environ.get('POINT_OFFLINE_AFTER', '20'))  # 秒；心跳超过此时长视为掉线
POINT_TOKEN = os.environ.get('POINT_TOKEN', 'local-demo-point-token')

_redis_client = None


def _client():
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(os.environ['REDIS_URL'])
    return _redis_client


def push_event(msg):
    """向 worker 的中位事件队列投递（control / receipt）。"""
    _client().rpush(EVENTS_QUEUE, json.dumps(msg, ensure_ascii=False))


def push_query(code, msg):
    """向某个检验点的队列投递消息（query / update / ping）。"""
    _client().rpush(f'point:{code}', json.dumps(msg, ensure_ascii=False))


def set_online_flag(code, online):
    _client().set(f'point:{code}:online', '1' if online else '0')


def _parse(ts):
    return datetime.fromisoformat(ts)


def _is_online(point):
    return (datetime.now(timezone.utc) - _parse(point.last_seen_at)).total_seconds() < OFFLINE_AFTER


def point_view(p):
    x = record(p)
    x['online'] = _is_online(p)
    return x


def list_points(db):
    return [point_view(p) for p in db.scalars(select(InspectionPoint).order_by(InspectionPoint.id))]


def list_events(db, limit=80):
    return [record(e) for e in db.scalars(select(PointEvent).order_by(PointEvent.id.desc()).limit(limit))]


def _event(db, p, kind, detail):
    db.add(PointEvent(point_code=p.code, point_name=p.name, kind=kind, detail=detail,
                      version=p.version, sample_count=p.sample_count, created_at=now()))


def heartbeat(db, data):
    """检验点上报元数据（版本、份数）；识别掉线期间的版本变化。"""
    p = db.scalar(select(InspectionPoint).where(InspectionPoint.code == data.code))
    if not p:
        p = InspectionPoint(code=data.code, name=data.name, version=data.version,
                            sample_count=data.count, last_seen_at=now())
        db.add(p)
        db.flush()
        _event(db, p, 'register', f'检验点注册，v{data.version}，{data.count}份')
        db.commit()
        return point_view(p)
    was_online = _is_online(p)
    if data.version != p.version:
        kind = 'update' if was_online else 'offline_update'
        label = '在线更新' if was_online else '掉线期间更新'
        detail = f'{label}：v{p.version}→v{data.version}，{data.count}份'
        p.version = data.version
        p.sample_count = data.count
        _event(db, p, kind, detail)
    elif not was_online:
        _event(db, p, 'online', f'恢复连接，v{p.version}，{data.count}份')
    p.name = data.name
    p.version = data.version
    p.sample_count = data.count
    p.last_seen_at = now()
    db.commit()
    return point_view(p)


def _require_point(db, point_id):
    p = db.get(InspectionPoint, point_id)
    if not p:
        raise HTTPException(404, '检验点不存在')
    return p


def request_update(db, point_id, data, user):
    """把数据更新指令排入检验点队列；点侧执行后版本号随心跳变化。"""
    p = _require_point(db, point_id)
    if data.mode == 'regenerate':
        msg = {'type': 'update', 'mode': 'regenerate', 'n': data.n or 100}
        detail = f'{p.code} 重新生成{msg["n"]}份'
    else:
        if not data.values:
            raise HTTPException(422, 'set 方式需要给出数值列表')
        centi = []
        for v in data.values:
            c = round(v * 100)
            if abs(v * 100 - c) > 1e-6 or not 0 <= c <= engine.GRID_MAX:
                raise HTTPException(422, f'数值 {v} 不是0到100之间、0.01个百分点精度的含水率')
            centi.append(c)
        if not 1 <= len(centi) <= 5000:
            raise HTTPException(422, '数值份数须在1到5000之间')
        msg = {'type': 'update', 'mode': 'set', 'values_centi': centi}
        detail = f'{p.code} 录入{len(centi)}份给定数值'
    push_query(p.code, msg)
    log(db, user, 'point.update', detail)
    db.commit()
    return {'queued': True, 'point': p.code, 'note': '更新指令已入队；检验点在线时执行，新版本随心跳上报'}


def set_online(db, point_id, data, user):
    """模拟检验点掉线/恢复（演示与核对掉线期间更新用）。"""
    p = _require_point(db, point_id)
    set_online_flag(p.code, data.online)
    if data.online:
        push_query(p.code, {'type': 'ping'})
    log(db, user, 'point.online', f'{p.code} -> {"在线" if data.online else "掉线"}')
    db.commit()
    return {'point': p.code, 'online': data.online}


def create_run(db, data, user):
    """发起合批研究：锁定所选检验点的当前版本与份数。"""
    ids = list(dict.fromkeys(data.point_ids))
    points = [db.get(InspectionPoint, i) for i in ids]
    if any(p is None for p in points):
        raise HTTPException(404, '所选检验点不存在')
    locked = [{'id': p.id, 'code': p.code, 'name': p.name, 'version': p.version, 'count': p.sample_count}
              for p in points]
    total = sum(p['count'] for p in locked)
    if total < 1:
        raise HTTPException(422, '所选检验点合计0份，无法计算中位数')
    ranks = engine.ranks_for(total)
    run = MedianRun(name=data.name, status='queued', points=locked, ranks=ranks,
                    total_count=total, checksum=checksum({'points': locked, 'ranks': ranks}),
                    created_at=now(), updated_at=now())
    db.add(run)
    db.flush()
    log(db, user, 'median.create', f'#{run.id} {data.name}：锁定{len(locked)}点共{total}份')
    db.commit()
    push_event({'kind': 'control', 'run_id': run.id, 'repush': True})
    return record(run)


def _asked_set(db, run_id):
    return {(q.point_code, q.threshold_centi)
            for q in db.scalars(select(MedianQuestion).where(MedianQuestion.run_id == run_id))}


def _counts(db, run_id):
    return {(r.point_code, r.threshold_centi): r.count
            for r in db.scalars(select(MedianReceipt).where(MedianReceipt.run_id == run_id))}


def _dispatch(run, point, threshold_centi):
    push_query(point['code'], {'type': 'query', 'run_id': run.id,
                               'threshold_centi': threshold_centi, 'version': point['version']})


def advance(db, run, repush=False):
    """按台账推进一次：补问新阈值、收尾出数，或（恢复时）重发未答询问。"""
    asked = _asked_set(db, run.id)
    counts = _counts(db, run.id)
    kind, payload = engine.next_steps(run.points, run.ranks, asked, counts)
    newly = set()
    if kind == 'ask':
        for t in payload:
            for p in run.points:
                key = (p['code'], t)
                if key in asked:
                    continue
                if sum(1 for (c, _) in asked if c == p['code']) >= engine.BUDGET:
                    run.status = 'failed'
                    run.error = f"检验点{p['name']}的阈值预算（{engine.BUDGET}个）已耗尽，无法继续"
                    run.updated_at = now()
                    return
                db.add(MedianQuestion(run_id=run.id, point_code=p['code'],
                                      threshold_centi=t, asked_at=now()))
                asked.add(key)
                newly.add(key)
                _dispatch(run, p, t)
        run.status = 'collecting'
    elif kind == 'done':
        run.result = engine.build_result(run.points, run.ranks, asked, counts, payload)
        run.status = 'completed'
    if repush and run.status == 'collecting':
        # 中断恢复：重发已问未答的询问（点侧重算、总台按点位+阈值归账，幂等）
        for (code, t) in sorted(asked):
            if (code, t) in counts or (code, t) in newly:
                continue
            point = next(p for p in run.points if p['code'] == code)
            _dispatch(run, point, t)
    run.updated_at = now()


def control(db, run_id, repush=False):
    """worker 驱动入口：新开算或中断恢复。repush=True 时重发未答询问。"""
    run = db.get(MedianRun, run_id)
    if not run or run.status in ('completed', 'failed', 'stale'):
        return
    advance(db, run, repush=repush)
    db.commit()


def handle_receipt(db, msg):
    """回执归账：按（运行, 点位, 阈值）落账；乱序/重传幂等；收齐原版才推进。"""
    try:
        run_id = int(msg['run_id'])
        code = str(msg['point_code'])
        t = int(msg['threshold_centi'])
        count = int(msg['count'])
        version = int(msg['version'])
    except (KeyError, TypeError, ValueError):
        return
    run = db.get(MedianRun, run_id)
    if not run or run.status not in ('queued', 'collecting'):
        return  # 终态运行的迟到回执直接忽略
    locked = {p['code']: p for p in run.points}
    point = locked.get(code)
    if not point:
        return
    q = db.scalar(select(MedianQuestion).where(MedianQuestion.run_id == run_id,
                                               MedianQuestion.point_code == code,
                                               MedianQuestion.threshold_centi == t))
    if not q:
        return  # 非本运行发出的询问
    existing = db.scalar(select(MedianReceipt).where(MedianReceipt.run_id == run_id,
                                                     MedianReceipt.point_code == code,
                                                     MedianReceipt.threshold_centi == t))
    if existing:
        if existing.count == count and existing.version == version:
            return  # 纯重传
        if existing.version == point['version'] and version != point['version']:
            run.status = 'stale'
            run.error = (f"检验点{point['name']}在计算期间更新：同一阈值先后收到 "
                         f"v{existing.version}与v{version}的回执，锁定版本已失效，请重新发起")
        else:
            run.status = 'failed'
            run.error = (f"检验点{point['name']}阈值{t / 100:.2f}的回执冲突："
                         f"{existing.count}份与{count}份不一致")
        run.updated_at = now()
        db.commit()
        return
    db.add(MedianReceipt(run_id=run_id, point_code=code, threshold_centi=t,
                         count=count, version=version, received_at=now()))
    if version != point['version']:
        run.status = 'stale'
        run.error = (f"检验点{point['name']}在计算期间更新：锁定v{point['version']}，"
                     f"收到v{version}的回执。原版回执已无法收齐，请重新发起合批研究")
        run.updated_at = now()
        db.commit()
        return
    db.flush()
    advance(db, run, repush=False)
    db.commit()


def run_progress(db, run):
    questions = list(db.scalars(select(MedianQuestion).where(MedianQuestion.run_id == run.id)
                                .order_by(MedianQuestion.id)))
    receipts = list(db.scalars(select(MedianReceipt).where(MedianReceipt.run_id == run.id)))
    codes = [p['code'] for p in run.points]
    counts = {(r.point_code, r.threshold_centi): r.count for r in receipts}
    asked = {}
    for q in questions:
        asked.setdefault(q.point_code, []).append(q.threshold_centi)
    order = []
    for q in questions:
        if q.threshold_centi not in order:
            order.append(q.threshold_centi)
    rounds = []
    for t in order:
        got = [(c, counts[(c, t)]) for c in codes if (c, t) in counts]
        rounds.append({
            'threshold': round(t / 100, 2),
            'received': len(got),
            'of': len(codes),
            'cum': sum(n for _, n in got) if len(got) == len(codes) else None,
        })
    awaiting = sorted({c for c in codes for t in asked.get(c, []) if (c, t) not in counts})
    return {
        'asked_count': {c: len(asked.get(c, [])) for c in codes},
        'received_count': {c: sum(1 for (rc, _) in counts if rc == c) for c in codes},
        'awaiting': awaiting,
        'rounds': rounds,
    }


def get_run(db, run_id):
    run = db.get(MedianRun, run_id)
    if not run:
        raise HTTPException(404, '合批研究不存在')
    x = record(run)
    x['progress'] = run_progress(db, run)
    return x


def list_runs(db):
    runs = [record(r) for r in db.scalars(select(MedianRun).order_by(MedianRun.id.desc()))]
    for r in runs:
        r['median'] = (r['result'] or {}).get('median')
        r['point_count'] = len(r['points'])
    return runs
