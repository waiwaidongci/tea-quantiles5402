"""检验点本地存储：原值只存在于点侧 SQLite，总台与 worker 均不读取。

每个检验点一个库文件：
- meta(k,v)：version 等元数据，数据每次变更版本号 +1
- readings(sample_key, value_centi)：含水率原值，厘百分点整数（0.01% 精度）
"""
import os
import sqlite3
import threading


class PointStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS readings('
                        'sample_key TEXT PRIMARY KEY, value_centi INTEGER NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_readings_value ON readings(value_centi)')
        self.db.commit()
        self.lock = threading.Lock()
        if self._meta('version') is None:
            self._set_meta('version', '0')

    def _meta(self, k):
        row = self.db.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k, v):
        self.db.execute('INSERT INTO meta(k,v) VALUES(?,?) '
                        'ON CONFLICT(k) DO UPDATE SET v=excluded.v', (k, str(v)))

    def version(self):
        return int(self._meta('version'))

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM readings').fetchone()[0]

    def count_le(self, threshold_centi):
        """阈值询问：不超过阈值（含等值）的份数。这是总台唯一被允许的问题。"""
        return self.db.execute('SELECT COUNT(*) FROM readings WHERE value_centi<=?',
                               (threshold_centi,)).fetchone()[0]

    def seed_if_empty(self, values_centi):
        with self.lock:
            if self.count() > 0:
                return False
            self._replace(values_centi)
            return True

    def replace(self, values_centi):
        with self.lock:
            self._replace(values_centi)

    def _replace(self, values_centi):
        for v in values_centi:
            if not isinstance(v, int) or not 0 <= v <= 10000:
                raise ValueError('含水率必须是0.00到100.00之间、0.01精度的数值')
        self.db.execute('DELETE FROM readings')
        self.db.executemany('INSERT INTO readings(sample_key, value_centi) VALUES(?,?)',
                            [(f'S-{i + 1:05}', v) for i, v in enumerate(values_centi)])
        self._set_meta('version', str(self.version() + 1))
        self.db.commit()

    def all_values(self):
        """仅供点侧 oracle 复核使用，不对总台暴露。"""
        return [r[0] for r in self.db.execute('SELECT value_centi FROM readings')]
