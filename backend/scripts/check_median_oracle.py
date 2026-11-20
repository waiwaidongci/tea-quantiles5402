"""点侧独立 oracle：直接读取各检验点原值重算合批中位数，与总台结果核对。

用法（在 points 容器内，因原值只在点侧）：
    python -m scripts.check_median_oracle <run_id>

仅当各检验点当前版本与运行锁定版本一致时才可核对；
掉线或版本已变时会明确报告无法核对，而不是给出似是而非的结论。
"""
import os
import statistics
import sys

from src.config.database import Session
from src.models.median import MedianRun
from src.point_store import PointStore

POINT_DIR = os.environ.get('POINT_DIR', '/point-data')


def main(run_id):
    with Session() as db:
        run = db.get(MedianRun, int(run_id))
        if not run:
            print(f'运行 {run_id} 不存在')
            return 2
        if run.status != 'completed':
            print(f'运行 {run_id} 状态为 {run.status}，仅核对已完成运行')
            return 2
        locked = run.points
        expected = run.result['median']
    values = []
    for p in locked:
        store = PointStore(os.path.join(POINT_DIR, p['code'] + '.db'))
        if store.version() != p['version']:
            print(f"检验点{p['name']}当前版本v{store.version()}与锁定v{p['version']}不一致，"
              '原值已变化，无法核对本次运行')
            return 3
        values.extend(store.all_values())
    if len(values) != run.total_count:
        print(f'份数不符：点侧合计{len(values)}，锁定{run.total_count}')
        return 3
    oracle = round(statistics.median(values) / 100, 4)
    ok = abs(oracle - expected) < 1e-9
    print(f"总台结果 {expected} · 点侧重算 {oracle} · {'一致 PASS' if ok else '不一致 FAIL'}"
          f'（{len(values)}份，{len(locked)}个检验点）')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
