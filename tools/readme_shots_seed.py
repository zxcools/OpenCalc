# -*- coding: utf-8 -*-
"""README 截图演示数据: 通过真实 API 灌入临时库(不碰用户真实数据)。

- 期货 / 期权各一组交易记录, 走 /api/trades/upsert (真实校验 + FIFO)
- 期货首条的测算快照走 /api/calc/futures 真算, 详情页测算卡才是真实结果
- 资金曲线两组策略走 /api/funds/import
"""
import json
import os
import urllib.request

BASE = os.environ.get('OC_E2E_BASE', 'http://127.0.0.1:8899')
for k in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '127.0.0.1,localhost'


def post(path, obj):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


def ok(r, tag):
    if not r.get('ok'):
        print('  !! %s -> %s' % (tag, r.get('error')))
    return r.get('ok')


# ---------- 1) 期货测算快照(真实调用) ----------
def snap_futures(code, direction, equity, entry, stop, target, risk_pct):
    r = post('/api/calc/futures', {
        'equity': equity, 'code': code, 'direction': direction,
        'entry': entry, 'stop': stop, 'target': target,
        'risk_ratio': risk_pct / 100.0,
    })
    if not r.get('ok'):
        print('  !! calc %s -> %s' % (code, r.get('error')))
        return None
    d = r
    return {
        'code': code, 'name': d.get('name') or code,
        'dir': 'long' if direction == 'long' else 'short',
        'entry': entry, 'stop': stop, 'target': target,
        'riskPct': risk_pct,
        'budget': d.get('budget'),
        'lots': d.get('max_lots'),
        'pl_ratio': d.get('pl_ratio'),
        'per_lot_risk': d.get('per_lot_risk'),
        'risk_used': d.get('risk_used'),
        'margin_used': d.get('margin_used'),
        'ladder': d.get('ladder') or [],
    }


def fut(code, contract, name, direction, qty, entry, stop, target, risk_pct,
        opens, closes, note, with_calc=False):
    """opens/closes: [(date, ...)]; closes: [(date, qty, price, pnl)]"""
    batch = '2026%02d%02d%02d0000' % (9, 10, abs(hash(contract)) % 90 + 1)
    snap = snap_futures(code, direction, 900000, entry, stop, target, risk_pct) if with_calc else None
    payload = {
        'mode': 'futures', 'underlying': code, 'contract': contract, 'batch': batch,
        'op_type': 'open', 'direction': 'buy' if direction == 'long' else 'sell',
        'open_date': opens[0], 'open_price': entry, 'qty': qty,
        'premium': (snap or {}).get('margin_used') or entry * qty,
        'init_stop': stop, 'init_target': target,
        'calc_json': json.dumps(snap, ensure_ascii=False) if snap else None,
        'note': note,
    }
    ok(post('/api/trades/upsert', payload), 'open %s' % contract)
    for (cd, cq, cp, pnl) in closes:
        r = post('/api/trades/upsert', {
            'mode': 'futures', 'underlying': code, 'contract': contract, 'batch': batch,
            'op_type': 'close', 'direction': 'buy' if direction == 'long' else 'sell',
            'close_date': cd, 'close_qty': cq, 'close_price': cp, 'pnl': pnl,
            'open_date': opens[0], 'note': '',
        })
        if not r.get('ok'):
            print('  !! close %s -> %s' % (contract, r.get('error')))
    return batch


print('=== 期货记录 ===')
b_cu = fut('cu', 'cu2611', '沪铜', 'long', 5, 72150, 70800, 75000, 1,
           ['2026-09-01'], [('2026-09-08', 2, 73500, 1350), ('2026-09-11', 3, 72700, 755)],
           '来自开仓计算器 · 沪铜 多头 @72150 止损70800 止盈75000', with_calc=True)
fut('rb', 'rb2610', '螺纹钢', 'short', 10, 3180, 3270, 2960, 1,
    ['2026-08-24'], [('2026-09-03', 5, 2980, 1000)], '来自开仓计算器 · 螺纹钢 空头')
fut('m', 'm2701', '豆粕', 'long', 8, 2980, 2915, 3120, 1,
    ['2026-08-18'], [('2026-08-27', 8, 3192, 1700)], '来自开仓计算器 · 豆粕 多头')
fut('SA', 'SA2611', '纯碱', 'short', 6, 1420, 1468, 1310, 1,
    ['2026-08-12'], [('2026-08-21', 6, 1533, -680)], '来自开仓计算器 · 纯碱 空头')
fut('lc', 'lc2611', '碳酸锂', 'long', 2, 78600, 75200, 89400, 2,
    ['2026-07-30'], [('2026-08-14', 2, 81300, 5400)], '来自开仓计算器 · 碳酸锂 多头')
fut('SM', 'SM2610', '锰硅', 'long', 6, 6240, 6080, 6640, 1,
    ['2026-07-22'], [('2026-08-05', 6, 6413, 860)], '来自开仓计算器 · 锰硅 多头')
fut('pb', 'pb2610', '沪铅', 'long', 4, 16880, 16560, 17550, 1,
    ['2026-09-09'], [], '来自开仓计算器 · 沪铅 多头 @16880 止损16560 止盈17550')
fut('FG', 'FG2611', '玻璃', 'short', 12, 1180, 1226, 1080, 1,
    ['2026-09-10'], [], '来自开仓计算器 · 玻璃 空头 @1180 止损1226 止盈1080')
fut('UR', 'UR2611', '尿素', 'long', 8, 1830, 1792, 1908, 1,
    ['2026-08-28'], [('2026-09-05', 4, 1731, -1250)], '来自开仓计算器 · 尿素 多头')
fut('jd', 'jd2610', '鸡蛋', 'short', 5, 3450, 3560, 3220, 1,
    ['2026-09-11'], [], '来自开仓计算器 · 鸡蛋 空头 @3450 止损3560 止盈3220')


# ---------- 2) 期权记录 ----------
def opt(code, contract, direction, qty, premium, open_date, note,
        closes=None, cp='C'):
    batch = '2026%02d%02d%02d0000' % (9, 12, abs(hash(contract)) % 90 + 1)
    payload = {
        'mode': 'options', 'underlying': code, 'contract': contract, 'batch': batch,
        'op_type': 'open', 'direction': direction, 'call_put': cp,
        'open_date': open_date, 'open_price': premium, 'qty': qty,
        'premium': premium * qty, 'note': note,
    }
    r = post('/api/trades/upsert', payload)
    if not r.get('ok'):
        print('  !! open %s -> %s' % (contract, r.get('error')))
    for (cd, cq, cp_, pnl) in (closes or []):
        r = post('/api/trades/upsert', {
            'mode': 'options', 'underlying': code, 'contract': contract, 'batch': batch,
            'op_type': 'close', 'direction': direction, 'call_put': cp,
            'close_date': cd, 'close_qty': cq, 'close_price': cp_, 'pnl': pnl,
            'open_date': open_date, 'note': '',
        })
        if not r.get('ok'):
            print('  !! close %s -> %s' % (contract, r.get('error')))


print('=== 期权记录 ===')
opt('lc', 'lc2611-C-144000', 'buy', 3, 9800, '2026-08-06', 'IV 低位建仓 · 碳酸锂 看涨',
    [('2026-08-25', 3, 14000, 12600)])
opt('cu', 'cu2611-C-72000', 'buy', 2, 2150, '2026-08-13', 'IV 低位建仓 · 沪铜 看涨',
    [('2026-08-28', 2, 550, -3200)])
opt('rb', 'rb2610-P-3200', 'buy', 10, 85, '2026-09-02', 'IV 低位建仓 · 螺纹钢 看跌')
opt('m', 'm2701-P-2900', 'buy', 6, 62, '2026-08-20', 'IV 低位建仓 · 豆粕 看跌',
    [('2026-09-04', 3, 362, 1800)])
opt('au', 'au2612-C-560', 'buy', 1, 8500, '2026-07-15', 'IV 低位建仓 · 沪金 看涨',
    [('2026-08-11', 1, 10900, 2400)])


# ---------- 3) 监控池 + 复盘 ----------
print('=== 监控池 / 复盘 ===')
for (mode, date, contracts, note) in [
    ('futures', '2026-09-15', ['rb2610', 'FG2611', 'UR2611'], '主力换月观察'),
    ('futures', '2026-09-18', ['cu2611', 'lc2611', 'SM2610'], ''),
    ('options', '2026-09-15', ['lc2611-C-144000', 'rb2610-P-3200'], 'IV 分位回落'),
    ('options', '2026-09-18', ['m2701-P-2900', 'au2612-C-560'], ''),
]:
    r = post('/api/trades/pool/upsert', {
        'mode': mode, 'snapshot_date': date, 'contracts': contracts, 'note': note})
    if not r.get('ok'):
        print('  !! pool ->', r.get('error'))


# ---------- 4) 资金曲线 ----------
print('=== 资金曲线 ===')
ABE = [
    (2025, 10, 190000, 196400, 0), (2025, 11, 196400, 193100, 0),
    (2025, 12, 193100, 204800, 0), (2026, 1, 204800, 215300, 8000),
    (2026, 2, 215300, 212400, 0), (2026, 3, 212400, 224600, 0),
    (2026, 4, 224600, 233100, 0), (2026, 5, 233100, 230500, 0),
    (2026, 6, 230500, 243800, 6000), (2026, 7, 243800, 254200, 0),
    (2026, 8, 254200, 251000, 0), (2026, 9, 251000, 264700, 9000),
]
WK = [
    (2026, 4, 80000, 82900, 0), (2026, 5, 82900, 81200, 0),
    (2026, 6, 81200, 87400, 5000), (2026, 7, 87400, 93100, 0),
    (2026, 8, 93100, 91500, 0), (2026, 9, 91500, 97600, 0),
]
records = []
for (y, m, a, b, cf) in ABE:
    records.append({'strategy': 'abe', 'year': y, 'month': m,
                    'initial_equity': a, 'end_equity': b, 'cash_flow': cf, 'note': ''})
for (y, m, a, b, cf) in WK:
    records.append({'strategy': '威科夫', 'year': y, 'month': m,
                    'initial_equity': a, 'end_equity': b, 'cash_flow': cf, 'note': ''})
r = post('/api/funds/import', {'records': records})
print('  import ->', r.get('ok'), r.get('imported'), r.get('error', ''))

# ---------- 5) 复盘笔记 ----------
r = post('/api/trades/review/upsert', {
    'mode': 'futures', 'underlying': 'cu', 'batch': b_cu,
    'review_at': '2026-09-11T15:10:00',
    'content': ('第一批 2 手在 2R 处了结锁利，剩下的 3 手跟着止损推到成本线之上，'
                '整体到手 12.6%。进场那天铜的仓单在降、月差走强，结构比价格本身更早给出信号。\n'
                '下次注意：日线没站稳之前不要一次开满，等第二根确认再加。')})
print('  review ->', r.get('ok'), r.get('error', ''))
print('\nSEED DONE')
