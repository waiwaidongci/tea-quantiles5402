import os

from fastapi import APIRouter, Depends, Header, HTTPException

from src.config.database import get_db
from src.middlewares.auth import current_user, writer
from src.schemas.median import HeartbeatInput, MedianRunInput, PointOnlineInput, PointUpdateInput
from src.services import median

router = APIRouter()
POINT_TOKEN = os.environ.get('POINT_TOKEN', 'local-demo-point-token')


@router.post('/points/heartbeat')
def beat(data: HeartbeatInput, db=Depends(get_db), x_point_token: str = Header(default='')):
    # 检验点代理专用通道：只上报元数据（版本、份数），不传输原值
    if x_point_token != POINT_TOKEN:
        raise HTTPException(403, '检验点令牌无效')
    return median.heartbeat(db, data)


@router.get('/points')
def points(db=Depends(get_db), user=Depends(current_user)):
    return median.list_points(db)


@router.get('/points/events')
def events(db=Depends(get_db), user=Depends(current_user)):
    return median.list_events(db)


@router.post('/points/{point_id}/update')
def update(point_id: int, data: PointUpdateInput, db=Depends(get_db), user=Depends(writer)):
    return median.request_update(db, point_id, data, user)


@router.post('/points/{point_id}/online')
def online(point_id: int, data: PointOnlineInput, db=Depends(get_db), user=Depends(writer)):
    return median.set_online(db, point_id, data, user)


@router.get('/median-runs')
def runs(db=Depends(get_db), user=Depends(current_user)):
    return median.list_runs(db)


@router.post('/median-runs')
def create(data: MedianRunInput, db=Depends(get_db), user=Depends(writer)):
    return median.create_run(db, data, user)


@router.get('/median-runs/{run_id}')
def get(run_id: int, db=Depends(get_db), user=Depends(current_user)):
    return median.get_run(db, run_id)
