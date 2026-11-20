# 验证记录（2026-11-20）

本环境无 Docker，以下验证在本机 Python 3.11 直接执行；依赖按 `backend/requirements.txt`
版本装入隔离目录（SQLAlchemy 2.0.36 / FastAPI 0.115.6 / redis 5.2.1 / httpx / fakeredis 2.40）。
Redis 与 PostgreSQL 在测试中分别由 fakeredis 与 SQLite 替代；生产路径（compose）未在本环境启动。

## 后端单元与协议测试（32 项，连续两轮全过）

```
cd backend && python -m unittest discover -s tests -p 'test_*.py'
# Ran 32 tests — OK（两轮均 OK）
```

- `tests/test_median_engine.py`（15 项，纯函数）
  - 奇/偶总量、单样本、单点、空点、全等值、边界值 0.00/100.00、跨点同值频率；
  - 60 组随机数据集与 `statistics.median` 逐一相符；
  - 200 组随机数据验证每点阈值数 ≤30（两次二分各14问+验证轮≤2问）。
- `tests/test_point_store.py`（3 项）：种子不覆盖、replace 版本+1 且重启后可恢复、非法值拒绝。
- `tests/test_median_service.py`（11 项，SQLite+假传输跑完整协议）
  - 正常出数并与原值中位数一致；偶数取中间两值平均；
  - 回执乱序+重传不影啊结果；中途模拟 worker 重启后仅凭台账恢复并出数一致；
  - 计算期间某点更新 → 运行置 stale 并提示重新发起；同键回执冲突 → failed；
  - 纯重传/未询问/终态迟到回执均被忽略；掉线期间更新记 `offline_update` 事件；
  - 空选择拒绝；阈值预算第31问被拦截并置 failed。
- `tests/test_e2e_stack.py`（6 项，FastAPI 应用+点代理线程+worker 事件处理+fakeredis）
  - 四个检验点注册上线；心跳令牌错误 403；
  - API 发起合批研究 → 等待 → completed，**点侧 oracle 输出
    「总台结果 5.75 · 点侧重算 5.75 · 一致 PASS（432份，4个检验点）」**；
  - 掉线→掉线期间下发更新→恢复：事件流出现 `offline_update`，状态页可见掉线；
  - 计算期间点侧更新 → 运行 stale；viewer 发起/操作被 403、读取 200。

## 前端渲染冒烟（19 项，jsdom + 模拟 API）

```
cd frontend/tests && npm init -y && npm i jsdom@25 && node smoke.mjs   # 19 项全 PASS
```

覆盖：合批发起表单（版本锁定说明、每点30阈值）、等待页（等待回执点位、等待中轮次、
每2秒更新）、完成页（精确中位数、跨点同值分点份数、台账SHA256、各点阈值清单）、
检验点页（在线/掉线、掉线期间更新事件、模拟掉线按钮）、结果档案与总览回归。

## 静态检查

- `python -m py_compile` 全部后端源码通过；`node --check` 全部前端 JS 通过；
- `docker-compose.yml` 经 YAML 解析校验：6 个服务（db/redis/backend/worker/points/frontend）与 4 个卷齐全。

## 待 Docker 环境复验

- `docker compose up -d --build` 全链路（PostgreSQL16+Redis7 持久队列）；
- `docker compose exec points python -m scripts.check_median_oracle <run_id>` 点侧核对。
