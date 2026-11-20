"""合批精确中位数的阈值询问引擎（纯函数，无 IO，可独立测试）。

口径：
- 含水率按百分之一百分点记值，取值落在 0.00%..100.00% 的离散网格上，
  引擎内部一律用「厘百分点」整数（值 x100，0..10000）避免浮点误差。
- 总台不接触原值，只向各检验点询问「不超过某阈值的份数」（含等值）。
- 每个检验点每次合批研究最多被问 BUDGET 个阈值。
- 中位次按 1 起始计：奇数总量取中间一位，偶数取中间两位并求平均。

next_steps 由「已问阈值集合 + 已归账回执」完全决定下一步，
因此中断后只需恢复台账即可继续，天然支持乱序与重传归账。
"""
import hashlib
import json

GRID_MAX = 10000  # 100.00% 的厘百分点表示
BUDGET = 30       # 每点每次运行最多询问的阈值个数


def ranks_for(total):
    """合批总量 total 的中位次（1 起始）。偶数总量返回相邻两位。"""
    if total < 1:
        raise ValueError('合批为空，无法计算中位数')
    return [total // 2 + 1] if total % 2 else [total // 2, total // 2 + 1]


def _need_or_wait(asked, counts, codes, thresholds):
    """若阈值已全部问齐则返回 wait（等回执），否则 ask（需补问）。"""
    pending = [t for t in thresholds if not all((c, t) in counts for c in codes)]
    if not pending:
        return None
    if any((c, t) not in asked for c in codes for t in pending):
        return ('ask', pending)
    return ('wait', pending)


def next_steps(points, ranks, asked, counts):
    """根据台账计算下一步。

    points: 锁定快照 [{'code','name','version','count'}, ...]
    ranks:  ranks_for(总量)
    asked:  {(code, threshold_centi)} 已发出的询问
    counts: {(code, threshold_centi): 份数} 已归账的回执（仅原版）
    返回 ('ask'|'wait', [threshold_centi...]) 或 ('done', [found_centi...])。
    """
    codes = [p['code'] for p in points]

    def cum(t):
        return sum(counts[(c, t)] for c in codes)

    found = []
    for rank in ranks:
        lo = found[-1] if found else 0  # 第二位次不小于第一位次的解
        hi = GRID_MAX
        while lo < hi:
            mid = (lo + hi) // 2
            if all((c, mid) in counts for c in codes):
                if cum(mid) >= rank:
                    hi = mid
                else:
                    lo = mid + 1
            else:
                return _need_or_wait(asked, counts, codes, [mid])
        found.append(lo)
    # 验证轮：为核对跨点同值，需要各点在中位值前一刻度的累计份数。
    need = set()
    g1 = found[0]
    if g1 > 0:
        need.add(g1 - 1)
    if len(found) == 2 and found[1] - 1 > g1:
        need.add(found[1] - 1)
    step = _need_or_wait(asked, counts, codes, sorted(need))
    if step:
        return step
    return ('done', found)


def frequency_at(counts, codes, g):
    """各点取值恰为 g（厘百分点）的份数：cum(g) - cum(g-1)。"""
    return {c: counts[(c, g)] - (counts[(c, g - 1)] if g > 0 else 0) for c in codes}


def build_result(points, ranks, asked, counts, found):
    """汇总最终结果（含跨点同值核对与台账校验摘要）。"""
    codes = [p['code'] for p in points]
    total = sum(p['count'] for p in points)
    per_point = []
    for p in points:
        ts = sorted(t for (c, t) in asked if c == p['code'])
        per_point.append({
            'code': p['code'], 'name': p['name'], 'version': p['version'],
            'count': p['count'], 'asked_count': len(ts),
            'thresholds': [round(t / 100, 2) for t in ts],
        })
    median_frequency = []
    for g in sorted(set(found)):
        per = frequency_at(counts, codes, g)
        median_frequency.append({
            'value': round(g / 100, 2),
            'per_point': per,
            'total': sum(per.values()),
            'points_sharing': sum(1 for n in per.values() if n > 0),
        })
    median = round(sum(found) / len(found) / 100, 4)
    middle_values = [round(g / 100, 2) for g in found]
    ledger = sorted([c, t, n] for (c, t), n in counts.items())
    ledger_sha256 = hashlib.sha256(json.dumps(ledger).encode()).hexdigest()
    rounds_used = max((len([1 for (c, _) in asked if c == code]) for code in codes), default=0)
    if len(found) == 1:
        head = f"合批共{total}份茶样，中位次为第{ranks[0]}位，精确中位数为 {middle_values[0]:.2f}%。"
    else:
        head = (f"合批共{total}份茶样（偶数），中间两位为第{ranks[0]}位的 {middle_values[0]:.2f}% "
                f"与第{ranks[1]}位的 {middle_values[1]:.2f}%，取平均得中位数 {median}%。")
    ties = '；'.join(
        f"数值 {f['value']:.2f}% 在 {f['points_sharing']} 个检验点同值出现（合计 {f['total']} 份）"
        for f in median_frequency)
    return {
        'median': median,
        'middle_values': middle_values,
        'ranks': ranks,
        'total_count': total,
        'grid_step': 0.01,
        'threshold_budget': BUDGET,
        'rounds_used': rounds_used,
        'points': per_point,
        'median_frequency': median_frequency,
        'ledger_sha256': ledger_sha256,
        'interpretation': head + ' 跨点同值核对：' + ties + '。',
        'caveat': '原值始终留在各检验点，总台只回收阈值以下份数；结果对应开算时锁定的各点版本，'
                  '此后检验点的数据更新不影响本结论。',
    }
