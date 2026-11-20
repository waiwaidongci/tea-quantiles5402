from sqlalchemy import Column, Integer, String, Text, JSON, ForeignKey, UniqueConstraint
from src.config.database import Base


class InspectionPoint(Base):
    """检验点注册表：只存元数据（版本、份数、心跳），原值留在点侧。"""
    __tablename__ = 'inspection_points'
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    sample_count = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(String(40), nullable=False)


class PointEvent(Base):
    """检验点版本与连接事件，用于核对掉线期间发生的数据更新。"""
    __tablename__ = 'point_events'
    id = Column(Integer, primary_key=True)
    point_code = Column(String(40), nullable=False)
    point_name = Column(String(120), nullable=False)
    kind = Column(String(20), nullable=False)  # register / update / offline_update / online
    detail = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False)
    sample_count = Column(Integer, nullable=False)
    created_at = Column(String(40), nullable=False)


class MedianRun(Base):
    """合批中位数研究：开算时锁定各点版本与份数。"""
    __tablename__ = 'median_runs'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    status = Column(String(20), default='queued')  # queued/collecting/completed/failed/stale
    points = Column(JSON, nullable=False)          # 锁定快照 [{code,name,version,count}]
    ranks = Column(JSON, nullable=False)           # 中位次（1 起始）
    total_count = Column(Integer, nullable=False)
    result = Column(JSON, nullable=True)
    error = Column(Text, default='')
    checksum = Column(String(64), nullable=False)  # 锁定输入 SHA256
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


class MedianQuestion(Base):
    """总台向检验点发出的阈值询问（每点每阈值唯一）。"""
    __tablename__ = 'median_questions'
    __table_args__ = (UniqueConstraint('run_id', 'point_code', 'threshold_centi'),)
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('median_runs.id'), nullable=False)
    point_code = Column(String(40), nullable=False)
    threshold_centi = Column(Integer, nullable=False)
    asked_at = Column(String(40), nullable=False)


class MedianReceipt(Base):
    """检验点回执：按（运行, 点位, 阈值）归账，乱序或重传幂等。"""
    __tablename__ = 'median_receipts'
    __table_args__ = (UniqueConstraint('run_id', 'point_code', 'threshold_centi'),)
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('median_runs.id'), nullable=False)
    point_code = Column(String(40), nullable=False)
    threshold_centi = Column(Integer, nullable=False)
    count = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)  # 点侧回答时的实际版本
    received_at = Column(String(40), nullable=False)
