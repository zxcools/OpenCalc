# -*- coding: utf-8 -*-
"""备份往返验证: 模拟「A 电脑导出 → B 电脑导入」, 逐表核对一条不少。

覆盖: 资金曲线 / 期货交易记录 / 期权交易记录 / 监控池(两模式) / 复盘笔记(两模式) / 计算器最近方案。

用法(在项目根目录):
    python tools/backup_roundtrip.py
退出码: 0 = 全部一致; 1 = 有缺失/不一致。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

for k in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '127.0.0.1,localhost'

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT_A, PORT_B = 8897, 8896
BASE_A = 'http://127.0.0.1:%d' % PORT_A
BASE_B = 'http://127.0.0.1:%d' % PORT_B

fails = []


def check(name, ok, extra=''):
    print(('  ✅ ' if ok else '  ❌ ') + name + (('  [' + str(extra) + ']') if extra else ''))
    if not ok:
        fails.append(name)


def get(path, base):
    return json.loads(urllib.request.urlopen(base + path, timeout=30).read().decode())


def post(path, obj, base):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())


def wait(base, tag):
    for _ in range(80):
        try:
            return get('/api/version', base)
        except Exception:
            time.sleep(0.5)
    raise SystemExit('%s 服务未就绪' % tag)


def start(dirpath, port):
    env = dict(os.environ, OC_NO_TAKEOVER='1', OC_PORT=str(port), OC_DATA_DIR=dirpath,
               OC_NO_AUTO_BACKUP='1')
    p = subprocess.Popen([PY, os.path.join(ROOT, 'main.py')], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def port_free(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(('127.0.0.1', port))
        return False
    except Exception:
        return True
    finally:
        s.close()


for port in (PORT_A, PORT_B):
    if not port_free(port):
        raise SystemExit('端口 %d 已被占用, 请先关掉占用它的服务' % port)

dir_a = tempfile.mkdtemp(prefix='oc_rt_A_')
dir_b = tempfile.mkdtemp(prefix='oc_rt_B_')
pa = pb = None
try:
    pa = start(dir_a, PORT_A)
    pb = start(dir_b, PORT_B)
    print('A 库(源):', wait(BASE_A, 'A'), dir_a)
    print('B 库(目标):', wait(BASE_B, 'B'), dir_b)

    # ---------- 1) 往 A 灌演示数据 ----------
    seed = subprocess.run([PY, os.path.join(ROOT, 'tools', 'readme_shots_seed.py')],
                          cwd=ROOT, env=dict(os.environ, OC_E2E_BASE=BASE_A),
                          capture_output=True, text=True)
    check('演示数据灌入 A 库', 'SEED DONE' in seed.stdout, seed.stdout[-200:] if 'SEED DONE' not in seed.stdout else '')

    # ---------- 2) A 导出(模拟前端: 附上 localStorage 里的最近方案) ----------
    exp = get('/api/funds/export', BASE_A)
    assert exp.get('ok'), exp.get('error')
    exp['plans'] = {'futures': [{'name': '沪铜 多头 72150', 'code': 'cu', 'entry': 72150}],
                    'options': [{'name': '碳酸锂 买入 9800', 'code': 'lc', 'entry': 9800}]}
    src = {k: len(exp.get(k) or []) for k in ('records', 'trades', 'trade_pools', 'trade_reviews')}
    src_fut = len([x for x in exp['trades'] if (x.get('mode') or 'options') == 'futures'])
    src_opt = len([x for x in exp['trades'] if (x.get('mode') or 'options') == 'options'])
    print('\n=== A 库导出 ===')
    print('  资金曲线 %d · 交易记录 %d(期货 %d/期权 %d) · 监控池 %d · 复盘 %d · 方案 %d'
          % (src['records'], src['trades'], src_fut, src_opt, src['trade_pools'],
             src['trade_reviews'], len(exp['plans']['futures']) + len(exp['plans']['options'])))
    json.dump(exp, open(os.path.join(ROOT, '_tmp_backup.opcalc'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # ---------- 3) 导入 B ----------
    imp = post('/api/funds/import', exp, BASE_B)
    check('导入返回 ok', imp.get('ok') is True, imp.get('error'))

    # ---------- 4) 逐表核对 ----------
    print('\n=== B 库导入后核对 ===')
    gf = get('/api/trades/groups?mode=futures', BASE_B).get('groups') or []
    go = get('/api/trades/groups?mode=options', BASE_B).get('groups') or []
    pf = get('/api/trades/pool?mode=futures', BASE_B)
    po = get('/api/trades/pool?mode=options', BASE_B)
    rec = get('/api/funds/records', BASE_B).get('records') or []
    rv_f = get('/api/trades/reviews?mode=futures', BASE_B).get('reviews') or []
    rv_o = get('/api/trades/reviews?mode=options', BASE_B).get('reviews') or []
    n_pf = len(pf.get('snapshots') or pf.get('pools') or [])
    n_po = len(po.get('snapshots') or po.get('pools') or [])
    exp_pf = len([x for x in exp['trade_pools'] if x.get('mode') == 'futures'])
    exp_po = len([x for x in exp['trade_pools'] if x.get('mode') == 'options'])
    exp_rv_f = len([x for x in exp['trade_reviews'] if x.get('mode') == 'futures'])
    exp_rv_o = len([x for x in exp['trade_reviews'] if x.get('mode') == 'options'])

    check('期货交易记录条数一致', len(gf) == len(set((x['underlying'], x.get('batch')) for x in exp['trades']
                                                    if x.get('mode') == 'futures')), '%d 条' % len(gf))
    check('期权交易记录条数一致', len(go) == len(set((x['underlying'], x.get('batch')) for x in exp['trades']
                                                    if x.get('mode') == 'options')), '%d 条' % len(go))
    check('期货监控池', n_pf == exp_pf, '%d/%d' % (n_pf, exp_pf))
    check('期权监控池', n_po == exp_po, '%d/%d' % (n_po, exp_po))
    check('资金曲线', len(rec) == src['records'], '%d/%d' % (len(rec), src['records']))
    check('期货复盘', len(rv_f) == exp_rv_f, '%d/%d' % (len(rv_f), exp_rv_f))
    check('期权复盘', len(rv_o) == exp_rv_o, '%d/%d' % (len(rv_o), exp_rv_o))

    # 期货那条详情: 测算快照 / 初次止损止盈 / 开仓价 都要在
    cu = [x for x in exp['trades'] if x.get('mode') == 'futures' and x.get('underlying') == 'cu']
    if cu:
        u, b = cu[0]['underlying'], cu[0].get('batch') or ''
        d = get('/api/trades/detail?mode=futures&underlying=%s&batch=%s' % (u, b), BASE_B)
        ops = d.get('operations') or []
        check('期货详情: 含开仓测算快照', any(o.get('calc_json') for o in ops))
        check('期货详情: 初次止损/止盈保留',
              any(o.get('init_stop') and o.get('init_target') for o in ops))
        check('期货详情: 平仓盈亏与状态保留',
              d.get('close_status') == '已平仓' and float(d.get('total_pnl') or 0) == 2105.0,
              '%s / %s' % (d.get('close_status'), d.get('total_pnl')))

    # 方案(前端层, 这里只验证备份文件里带着)
    pl = exp.get('plans') or {}
    check('备份文件含最近方案(两模式)',
          len(pl.get('futures') or []) == 1 and len(pl.get('options') or []) == 1)
finally:
    for port in (PORT_A, PORT_B):
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/api/shutdown' % port, timeout=5).read()
        except Exception:
            pass
    for p in (pa, pb):
        if p:
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
    for d in (dir_a, dir_b):
        shutil.rmtree(d, ignore_errors=True)

f = os.path.join(ROOT, '_tmp_backup.opcalc')
if os.path.exists(f):
    os.remove(f)

print('\n==== 备份往返: %s (%d 项失败) ====' % ('全部一致' if not fails else '有不一致', len(fails)))
if fails:
    for x in fails:
        print('  ❌', x)
sys.exit(0 if not fails else 1)
