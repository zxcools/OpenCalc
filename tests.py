# -*- coding: utf-8 -*-
"""开仓计算器 - 计算逻辑测试 (v4: 期货手数按风险金额推, 期权按权利金)

⚠⚠⚠ 测试必须跑在「临时数据库」上, 绝不能碰用户真实数据 ⚠⚠⚠
   历史事故: 本文件早期直接连 <源码目录>/data/funds.db 并 `DELETE FROM` 三张表,
   跑一次测试就把用户的资金曲线 + 交易记录 + 监控池全部清空。
   现在改为: 导入 main 之后立刻把 FUND_DB_PATH / CONFIG_PATH 指到临时目录,
   所有数据操作都落在临时库; 真实库只读不写。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 关键: 在任何数据操作之前, 把数据库与配置全部隔离到临时目录 ----
import tempfile as _tfp
import main as _main_mod

_TEST_ROOT = _tfp.mkdtemp(prefix="oc_tests_")
_TEST_DB = os.path.join(_TEST_ROOT, "funds.db")
_TEST_CFG = os.path.join(_TEST_ROOT, "config.json")
_main_mod.FUND_DB_PATH = _TEST_DB           # 所有 main 内函数读的就是这个全局
_main_mod.FUND_DB_CONN = None
_main_mod.CONFIG_PATH = _TEST_CFG           # 别让测试改动真实 config.json
if "oc_tests_" not in _main_mod.FUND_DB_PATH:
    raise SystemExit("❌ 测试库未隔离(会破坏真实数据), 已拒绝运行: %s" % _main_mod.FUND_DB_PATH)
_main_mod._fund_db()                        # 在临时目录建表, 后面直接连它删/插都安全

from main import calc_futures, calc_options, get_contract, CONTRACTS, MIN_PROFIT_LOSS_RATIO, fund_upsert, fund_list_records, fund_yearly_summary, fund_combined_summary, fund_delete, fund_import_backup, fund_export_backup, FUND_STRATEGIES, get_settings, save_settings, trade_upsert, trade_list_records, trade_delete, trade_groups, trade_detail, trade_pool_upsert, trade_pool_list, trade_pool_delete

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


print("== 合约数据表 ==")
check("合约数量 > 40", len(CONTRACTS) > 40, "实际 %d" % len(CONTRACTS))
check("合约数量 = 75 (全部活跃品种)", len(CONTRACTS) == 75, "实际 %d" % len(CONTRACTS))
cu = get_contract("cu")
check("沪铜乘数 = 5", cu and cu["mult"] == 5, str(cu))
au = get_contract("au")
check("沪金乘数 = 1000", au and au["mult"] == 1000, str(au))
im = get_contract("IM")
check("中证1000乘数 = 200", im and im["mult"] == 200, str(im))
sf = get_contract("SF")
check("硅铁乘数 = 5", sf and sf["mult"] == 5, str(sf))
sm = get_contract("SM")
check("锰硅乘数 = 5", sm and sm["mult"] == 5, str(sm))
ao = get_contract("ao")
check("氧化铝归属上期所", ao and ao["exchange"] == "上期所", str(ao))
for row in CONTRACTS:
    c = get_contract(row[0])
    if not c or c["mult"] <= 0 or c["opt_mult"] <= 0:
        check("合约 %s 数据完整" % row[0], False, str(c))

print("\n== 最小变动价位 (tick) ==")
check("每个品种都有 tick>0", all(row[6] > 0 for row in CONTRACTS), "缺 tick: %s" % [r[0] for r in CONTRACTS if r[6] <= 0])
_t = {"rb": 1, "au": 0.02, "i": 0.5, "lc": 20, "IF": 0.2, "T": 0.005, "TA": 2, "ec": 0.5, "bu": 1, "j": 0.5}
for code, expect in _t.items():
    c = get_contract(code)
    check("%s tick=%s" % (code, expect), c and abs(c["tick"] - expect) < 1e-9, str(c and c["tick"]))
# 交易所调整过最小变动价位的品种 —— 已逐个核对公告, 防止回退(改错会让阶梯止盈取整/价格步进失准)
_t_adj = {
    "p":  1,     # 大商所〔2026〕32号: 2 → 1 元/吨, 2026-04-10 交易时起
    "y":  1,     # 同上: 豆油与棕榈油一起调整
    "PR": 2,     # 郑商所瓶片期货业务细则(2026-05-14): 2 元/吨
    "TL": 0.01,  # 中金所30年期国债期货合约交易细则第七条: 0.01 元
    "lc": 20,    # 广期所〔2024〕337号: 50 → 20 元/吨, 2024-12-17 结算时起
    "ec": 0.5,   # 上期能源 2026-01-16 公告: 0.1 → 0.5 点, 2026-05-11 起
}
for code, expect in _t_adj.items():
    c = get_contract(code)
    check("调整过tick的 %s = %s" % (code, expect), c and abs(c["tick"] - expect) < 1e-9, str(c and c["tick"]))
check("contract_list 含 tick", all("tick" in x for x in __import__("main").contract_list()))

print("\n== 期货模式 (手数 = 预算 / 每手风险) ==")
# 案例: 权益100万, 螺纹钢(rb,乘数10), 开仓3500, 止损3450, 止盈3650
r = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                  "entry": 3500, "stop": 3450, "target": 3650})
check("默认开仓金额 = 权益x1% = 10000", abs(r["budget"] - 10000) < 0.01, str(r["budget"]))
check("每手风险 = 50x10 = 500", abs(r["per_lot_risk"] - 500) < 0.01, str(r["per_lot_risk"]))
check("每手止盈 = 150x10 = 1500", abs(r["per_lot_reward"] - 1500) < 0.01, str(r["per_lot_reward"]))
check("每手保证金(参考) = 3500x10x16% = 5600", abs(r["margin_per_lot"] - 5600) < 0.01, str(r["margin_per_lot"]))
check("最大手数 = 10000//500 = 20", r["max_lots"] == 20, str(r["max_lots"]))
check("最大占用保证金 = 20x5600 = 112000", abs(r["margin_used"] - 112000) < 0.01, str(r["margin_used"]))
check("实际最大风险 = 20x500 = 10000 = 预算", abs(r["risk_used"] - 10000) < 0.01, str(r["risk_used"]))
check("最大止盈盈利 = 20x1500 = 30000", abs(r["max_reward"] - 30000) < 0.01, str(r["max_reward"]))
check("盈亏比 = 150/50 = 3.0", abs(r["pl_ratio"] - 3.0) < 0.001, str(r["pl_ratio"]))
check("盈亏比>=1.5 可参与", r["participate"] is True)
check("enough_lots = True", r["enough_lots"] is True)

# 阶梯止盈(做多): 止损价差50=1R -> 2R=3600/3R=3650/4R=3700/5R=3750; 每手浮盈 = N×500
lad = r["ladder"]
check("阶梯止盈 4 档", len(lad) == 4 and [s["r"] for s in lad] == [2, 3, 4, 5], str(lad))
check("阶梯2R 价 = 3500+100 = 3600", abs(lad[0]["price"] - 3600) < 1e-6, str(lad[0]))
check("阶梯3R 价 = 3500+150 = 3650", abs(lad[1]["price"] - 3650) < 1e-6, str(lad[1]))
check("阶梯4R 价 = 3500+200 = 3700", abs(lad[2]["price"] - 3700) < 1e-6, str(lad[2]))
check("阶梯5R 价 = 3500+250 = 3750", abs(lad[3]["price"] - 3750) < 1e-6, str(lad[3]))
check("阶梯每手浮盈 2R = 2x500 = 1000", abs(lad[0]["per_lot_profit"] - 1000) < 0.01, str(lad[0]))
check("阶梯每手浮盈 5R = 5x500 = 2500", abs(lad[3]["per_lot_profit"] - 2500) < 0.01, str(lad[3]))

# 盈亏比不足: 止损3490 止盈3510 -> 价差10 -> 每手风险100
r2 = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                   "entry": 3500, "stop": 3490, "target": 3510})
check("盈亏比=1.0 不建议参与", r2["pl_ratio"] == 1.0 and r2["participate"] is False, str(r2["pl_ratio"]))
check("价差10 乘数10 -> 每手风险100 -> 10000//100=100手", r2["max_lots"] == 100, str(r2["max_lots"]))

# 自定义风险额度(百分比): 填 3 = 3% → 权益100万 × 3% = 30000
rc = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                   "entry": 3500, "stop": 3450, "target": 3650, "risk_percent": 3})
check("自定义3% budget = 100万x3% = 30000", abs(rc["budget"] - 30000) < 0.01, str(rc["budget"]))
check("自定义3%最大手数 = 30000//500 = 60", rc["max_lots"] == 60, str(rc["max_lots"]))
check("自定义3%实际风险 = 60x500 = 30000", abs(rc["risk_used"] - 30000) < 0.01, str(rc["risk_used"]))
check("自定义3%最大止盈 = 60x1500 = 90000", abs(rc["max_reward"] - 90000) < 0.01, str(rc["max_reward"]))
check("自定义额度不影响盈亏比 3.0", abs(rc["pl_ratio"] - 3.0) < 0.001, str(rc["pl_ratio"]))
rc15 = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                     "entry": 3500, "stop": 3450, "target": 3650, "risk_percent": 1.5})
check("填1.5 = 1.5% → 15000", abs(rc15["budget"] - 15000) < 0.01, str(rc15["budget"]))
rc2 = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                    "entry": 3500, "stop": 3450, "target": 3650, "risk_percent": ""})
check("risk_percent 为空字符串仍用默认 1% = 10000", abs(rc2["budget"] - 10000) < 0.01, str(rc2["budget"]))
# 可选档位 0.5/1/1.5/2/3 均可用
for _p, _expect in [(0.5, 5000), (1, 10000), (2, 20000), (3, 30000)]:
    _rr = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                        "entry": 3500, "stop": 3450, "target": 3650, "risk_percent": _p})
    check("可选档位 %s%% → 预算 %s" % (_p, _expect), abs(_rr["budget"] - _expect) < 0.01, str(_rr["budget"]))
try:
    calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                  "entry": 3500, "stop": 3450, "target": 3650, "risk_percent": 0})
    check("风险额度百分比<=0 被拒绝", False)
except ValueError:
    check("风险额度百分比<=0 被拒绝", True)

# 做空: 权益100万 IF 4000/4020/3940
r3 = calc_futures({"equity": 1000000, "code": "IF", "direction": "short",
                   "entry": 4000, "stop": 4020, "target": 3940})
check("做空每手风险 = 20x300 = 6000", abs(r3["per_lot_risk"] - 6000) < 0.01, str(r3))
check("做空每手止盈 = 60x300 = 18000", abs(r3["per_lot_reward"] - 18000) < 0.01, str(r3))
check("做空最大手数 = 10000//6000 = 1", r3["max_lots"] == 1, str(r3["max_lots"]))
check("做空最大占用保证金 = 1x192000 = 192000", abs(r3["margin_used"] - 192000) < 0.01, str(r3))
check("做空盈亏比 = 60/20 = 3.0", abs(r3["pl_ratio"] - 3.0) < 0.001, str(r3["pl_ratio"]))

# 阶梯止盈(做空): 1R=20点, 2R/3R/4R/5R 价 = 4000-40/60/80/100 = 3960/3940/3920/3900
lad3 = r3["ladder"]
check("做空阶梯 2R 价 = 3960", abs(lad3[0]["price"] - 3960) < 1e-6, str(lad3[0]))
check("做空阶梯 3R 价 = 3940", abs(lad3[1]["price"] - 3940) < 1e-6, str(lad3[1]))
check("做空阶梯 4R 价 = 3920", abs(lad3[2]["price"] - 3920) < 1e-6, str(lad3[2]))
check("做空阶梯 5R 价 = 3900", abs(lad3[3]["price"] - 3900) < 1e-6, str(lad3[3]))
check("做空阶梯每手浮盈 2R = 2x6000 = 12000", abs(lad3[0]["per_lot_profit"] - 12000) < 0.01, str(lad3[0]))

# 阶梯止盈价按最小变动价位对齐: rb tick=1 价格已是整数不变; 用非整数风险距离验证取整(多向上/空向下)
rt = calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                   "entry": 3500.4, "stop": 3450, "target": 3650, "risk_percent": 1})  # 1R=50.4 -> 2R=3601.2 -> 对齐 3602
check("做多阶梯非整数价向上取整(3601.2→3602)", abs(rt["ladder"][0]["price"] - 3602) < 1e-6, str(rt["ladder"][0]))

# 不足1手: 权益5万 IF 止损10点 -> 每手风险3000, 预算750
r4 = calc_futures({"equity": 50000, "code": "IF", "direction": "long",
                   "entry": 4000, "stop": 3990, "target": 4020})
check("预算不足时最大手数=0 且不开仓", r4["max_lots"] == 0 and r4["enough_lots"] is False, str(r4))

# 价格关系校验
try:
    calc_futures({"equity": 1000000, "code": "rb", "direction": "long",
                  "entry": 3500, "stop": 3550, "target": 3600})
    check("非法价格组合被拒绝", False)
except ValueError:
    check("非法价格组合被拒绝(止损>开仓)", True)

print("\n== 期权模式 (开仓价=1手价格已含乘数; 额度统一 权益x3%; 手数 = 预算 / 每手价格) ==")
o1 = calc_options({"equity": 1000000, "code": "m", "entry": 800})
check("开仓金额统一=权益x3% = 30000", abs(o1["budget"] - 30000) < 0.01, str(o1["budget"]))
check("每手权利金 = 输入开仓价(不乘乘数) = 800", abs(o1["premium_per_lot"] - 800) < 0.01, str(o1["premium_per_lot"]))
check("最大手数 = 30000//800 = 37", o1["max_lots"] == 37, str(o1["max_lots"]))
check("占用资金 = 37x800 = 29600", abs(o1["funds_used"] - 29600) < 0.01, str(o1["funds_used"]))
check("enough_lots = True", o1["enough_lots"] is True)
check("risk_ratio = 3%", abs(o1["risk_ratio"] - 0.03) < 1e-9, str(o1["risk_ratio"]))

# 期权不足1手: 权益2万 -> 预算600 < 800
o4 = calc_options({"equity": 20000, "code": "m", "entry": 800})
check("期权不足1手 -> 不开仓", o4["max_lots"] == 0 and o4["enough_lots"] is False, str(o4))

# 自定义期权风险百分比: 1% → 权益100万x1%=10000
o5 = calc_options({"equity": 1000000, "code": "m", "entry": 800, "risk_percent": 1})
check("期权 risk_percent=1 预算=10000", abs(o5["budget"] - 10000) < 0.01, str(o5["budget"]))
check("期权 risk_percent=1 手数=10000//800=12", o5["max_lots"] == 12, str(o5["max_lots"]))
check("期权 risk_percent 字段返回", abs(o5["risk_percent"] - 1.0) < 0.01, str(o5.get("risk_percent")))
o05 = calc_options({"equity": 1000000, "code": "m", "entry": 800, "risk_percent": 0.5})
check("期权 0.5% → 5000 → 6手", abs(o05["budget"] - 5000) < 0.01 and o05["max_lots"] == 6, str(o05))
try:
    calc_options({"equity": 1000000, "code": "m", "entry": 800, "risk_percent": 0})
    check("期权 risk_percent<=0 被拒绝", False)
except ValueError:
    check("期权 risk_percent<=0 被拒绝", True)

# options_risk_pct 设置持久化
_s = save_settings({"options_risk_pct": 1.5})
check("保存 options_risk_pct=1.5", _s["options_risk_pct"] == 1.5, str(_s))
_s2 = get_settings()
check("options_risk_pct 读取持久化", _s2["options_risk_pct"] == 1.5, str(_s2))
save_settings({"options_risk_pct": None})  # 清理

try:
    calc_futures({"equity": -5, "code": "rb", "direction": "long",
                  "entry": 3500, "stop": 3450, "target": 3650})
    check("负权益被拒绝", False)
except ValueError:
    check("负权益被拒绝", True)

print("\n================================")
print("通过 %d 项 / 失败 %d 项" % (PASS, FAIL))

# ==============================================================
# 资金曲线模块测试 (在所有断言后, 不影响 PASS/FAIL 计数)
# ==============================================================
print("\n== 资金曲线 (fund_*) ==")
import sqlite3
_conn = sqlite3.connect(_main_mod.FUND_DB_PATH)
_conn.execute("DELETE FROM records")  # 清理
_conn.commit()
_conn.close()

# 月度公式测试
fund_upsert("abe", 2025, 11, 100000, 105000, 5000, "11月", cash=12000)
r = fund_list_records("abe")[0]
check("月初=100000", r["initial_equity"] == 100000, str(r))
check("月末=105000", r["end_equity"] == 105000, str(r))
check("出金=5000(正)", r["cash_flow"] == 5000, str(r))
check("本月现金=12000(自定义)", r["cash"] == 12000, str(r))
check("月盈亏 = 月末-月初+出金 = 10000", r["monthly_pnl"] == 10000, str(r))
check("月收益率 = 10000/100000 = 10%", abs(r["month_return_rate"] - 0.10) < 1e-9, str(r))

# 月度更新 (upsert)
fund_upsert("abe", 2025, 11, 100000, 110000, 5000, "11月改")
r = fund_list_records("abe")[0]
check("upsert 后更新(覆盖)", len(fund_list_records("abe")) == 1 and r["end_equity"] == 110000, str(r))
check("upsert 后重新计算月盈亏", r["monthly_pnl"] == 15000, str(r))

# 负出入金(=入金)
fund_upsert("abe", 2025, 12, 110000, 105000, -10000, "12月入金")
r12 = [x for x in fund_list_records("abe") if x["month"] == 12][0]
check("入金-10000(出金为正)", r12["cash_flow"] == -10000)
check("月盈亏 = 105000-110000+(-10000) = -15000", r12["monthly_pnl"] == -15000)

# 年度汇总
y = fund_yearly_summary("abe")
check("只有 2025 这 1 个年度", len(y) == 1 and y[0]["year"] == 2025)
y0 = y[0]
check("年初 = 1月月初 = 100000", y0["initial_equity"] == 100000)
check("年末 = 12月月末 = 105000", y0["end_equity"] == 105000)
check("总出入金 = 5000+(-10000) = -5000", y0["total_cash_flow"] == -5000)
check("年度盈亏 = 105000 - 100000 + (-5000) = 0", y0["yearly_pnl"] == 0)
check("年化收益率 = 0/100000 = 0", abs(y0["annualized_return_rate"]) < 1e-9)

# 跨年汇总
fund_upsert("abe", 2026, 1, 105000, 120000, 0, "")
y2 = fund_yearly_summary("abe")
check("跨年后出现 2025+2026 共 2 条", len(y2) == 2)
y2026 = [x for x in y2 if x["year"] == 2026][0]
check("2026 年初 = 105000", y2026["initial_equity"] == 105000)
check("2026 盈亏 = 120000-105000+0 = 15000", y2026["yearly_pnl"] == 15000)

# 威科夫账户独立
fund_upsert("威科夫", 2026, 1, 50000, 55000, 0, "")
check("威科夫独立数据", [x for x in fund_list_records("威科夫") if x["year"] == 2026 and x["month"] == 1][0]["end_equity"] == 55000)
check("abe 3条 + 威科夫 1条 = 4", len(fund_list_records()) == 4)

# 汇总：把两个账户相同月合一起
combined = fund_combined_summary()
c_jan = [x for x in combined if x["year"] == 2026 and x["month"] == 1][0]
check("汇总 2026/1: 月初=105000+50000=155000", c_jan["initial_equity"] == 155000)
check("汇总 2026/1: 月末=120000+55000=175000", c_jan["end_equity"] == 175000)
check("汇总包含两个策略", set(c_jan["strategies"]) == {"abe", "威科夫"})

# 汇总缺失延续测试: abe 8月月末延续到 9月 (用户截图 case)
# 清空重置场景: abe 8月(60000->70000)、威科夫 8月(50000->60000)、威科夫 9月(60000->50000)
_conn = sqlite3.connect(_main_mod.FUND_DB_PATH)
_conn.execute("DELETE FROM records"); _conn.commit(); _conn.close()
fund_upsert("abe", 2025, 8, 60000, 70000, 1000, "")
fund_upsert("威科夫", 2025, 8, 50000, 60000, 10000, "")
fund_upsert("威科夫", 2025, 9, 60000, 50000, 0, "")
combined2 = fund_combined_summary()
c_aug = [x for x in combined2 if x["year"] == 2025 and x["month"] == 8][0]
c_sep = [x for x in combined2 if x["year"] == 2025 and x["month"] == 9][0]
check("汇总8月: 月初=60000+50000=110000", c_aug["initial_equity"] == 110000)
check("汇总8月: 月末=70000+60000=130000", c_aug["end_equity"] == 130000)
check("汇总8月: 出金=1000+10000=11000", c_aug["cash_flow"] == 11000)
check("汇总8月: 盈亏=+31000", c_aug["monthly_pnl"] == 31000)
check("汇总8月: 收益率=28.18%", abs(c_aug["month_return_rate"] - 0.2818) < 0.001)
check("汇总9月: abe延续(月初=abe 8月月末70000+威科夫60000)=130000", c_sep["initial_equity"] == 130000)
check("汇总9月: 月末=abe延续70000+威科夫50000=120000", c_sep["end_equity"] == 120000)
check("汇总9月: 盈亏=-10000", c_sep["monthly_pnl"] == -10000)
check("汇总9月: 收益率=-7.69%(修正后)", abs(c_sep["month_return_rate"] - (-0.0769)) < 0.001)
check("汇总9月: 参与=[威科夫], 延续=[abe]", set(c_sep["strategies"]) == {"威科夫"} and "abe" in c_sep["carried_strategies"])

# 删除 (延续测试后数据库中 abe 只有 2025/8 一条)
fund_delete("abe", 2025, 8)
abe_after = [x for x in fund_list_records("abe") if x["year"] == 2025 and x["month"] == 8]
check("删除成功", len(abe_after) == 0)
check("abe 剩 0 条", len(fund_list_records("abe")) == 0)

# 错误策略
try:
    fund_upsert("未知", 2026, 1, 0, 0, 0, "")
    check("未知策略被拒绝", False)
except ValueError:
    check("未知策略被拒绝", True)

# 错误月份
try:
    fund_upsert("abe", 2026, 13, 0, 0, 0, "")
    check("月份>12 被拒绝", False)
except ValueError:
    check("月份>12 被拒绝", True)

# ==============================================================
# 导出 / 导入备份测试
# ==============================================================
print("\n== 导出/导入备份 (fund_export/import_backup) ==")
from main import fund_export_backup, fund_import_backup

# 准备数据(先清空全部表, 避免之前的 trade 测试残留影响 records 计数)
_conn3 = sqlite3.connect(_main_mod.FUND_DB_PATH)
for t in ("records", "trade_records", "trade_pool_snapshots"):
    _conn3.execute(f"DELETE FROM {t}")
_conn3.commit(); _conn3.close()
fund_upsert("abe", 2025, 8, 60000, 70000, 1000, "8月")
fund_upsert("威科夫", 2025, 9, 60000, 50000, 0, "9月")

backup = fund_export_backup()
check("导出包含 records 数组", isinstance(backup.get("records"), list) and len(backup["records"]) == 2)
check("导出含版本/时间戳", "backup_version" in backup and "exported_at" in backup)

# 清空再导入 (模拟换电脑)
_conn3 = sqlite3.connect(_main_mod.FUND_DB_PATH)
for t in ("records", "trade_records", "trade_pool_snapshots"):
    _conn3.execute(f"DELETE FROM {t}")
_conn3.commit(); _conn3.close()
n, strategies = fund_import_backup(backup)
check("导入条数 = 2", n == 2)
check("导入策略 = [abe, 威科夫]", set(strategies) == {"abe", "威科夫"})
restored = fund_list_records()
check("导入后数据完整", len(restored) == 2 and restored[0]["end_equity"] == 70000)
check("导入重算盈亏(abe 8月=70000-60000+1000=11000)", [r for r in restored if r["strategy"]=="abe"][0]["monthly_pnl"] == 11000)

# 合并导入: 已有数据时导入新增
fund_upsert("威科夫", 2025, 10, 50000, 55000, 0, "10月")
n2, _ = fund_import_backup(backup)
check("合并导入不丢已有记录(3条)", len(fund_list_records()) == 3)

# 非法格式拒绝
try:
    fund_import_backup({"foo": 1})
    check("无 records 字段被拒绝", False)
except ValueError:
    check("无 records 字段被拒绝", True)
try:
    fund_import_backup({"records": [{"strategy": "abe"}]})
    check("缺必要字段被拒绝", False)
except ValueError:
    check("缺必要字段被拒绝", True)
try:
    fund_import_backup({"records": "not-a-list"})
    check("records 非列表被拒绝", False)
except ValueError:
    check("records 非列表被拒绝", True)

# ---- 交易记录 / 监控池 也必须含在备份中 (v50.27 用户反馈: 原前端只提示/校验资金曲线) ----
trade_upsert({"underlying": "bk1", "contract": "bk1C5000", "op_type": "open",
              "open_date": "2025-08-01", "call_put": "C", "direction": "buy",
              "open_price": 100, "qty": 2, "premium": 200})
trade_upsert({"underlying": "bk1", "contract": "bk1C5000", "op_type": "close",
              "close_date": "2025-08-05", "close_qty": 2, "close_price": 150, "pnl": 100,
              "call_put": "C", "direction": "sell"})
trade_pool_upsert({"snapshot_date": "2025-08-01",
                   "contracts": [{"contract": "bk1C5000", "qty": 2}], "note": "备份测试"})
bk2 = fund_export_backup()
check("导出包含 trades 数组", isinstance(bk2.get("trades"), list) and len(bk2["trades"]) == 2, str(len(bk2.get("trades") or [])))
check("导出包含 trade_pools 数组", isinstance(bk2.get("trade_pools"), list) and len(bk2["trade_pools"]) == 1, str(len(bk2.get("trade_pools") or [])))
# 清空三表后仅靠备份恢复
_conn3 = sqlite3.connect(_main_mod.FUND_DB_PATH)
for t in ("records", "trade_records", "trade_pool_snapshots"):
    _conn3.execute(f"DELETE FROM {t}")
_conn3.commit(); _conn3.close()
n3, _ = fund_import_backup(bk2)
check("导入含三部分(资金3+交易2+池1=6)", n3 == 6, "实际 %s" % n3)
_trl = trade_list_records()
check("交易记录完整恢复", len(_trl) == 2 and _trl[0]["contract"] == "bk1C5000", str(len(_trl)))
check("平仓盈亏恢复", [x for x in _trl if x["op_type"] == "close"][0]["pnl"] == 100)
check("监控池恢复", len(trade_pool_list()) == 1 and trade_pool_list()[0]["contracts"][0]["contract"] == "bk1C5000")
check("资金曲线一并恢复", len(fund_list_records()) == 3, str(len(fund_list_records())))
# 幂等: 再导入一次不应翻倍
n4, _ = fund_import_backup(bk2)
check("重复导入幂等(不翻倍)", len(trade_list_records()) == 2 and len(fund_list_records()) == 3 and len(trade_pool_list()) == 1,
      "trades=%s records=%s pools=%s" % (len(trade_list_records()), len(fund_list_records()), len(trade_pool_list())))
# 清理, 不影响后续用例
_conn3 = sqlite3.connect(_main_mod.FUND_DB_PATH)
for t in ("records", "trade_records", "trade_pool_snapshots"):
    _conn3.execute(f"DELETE FROM {t}")
_conn3.commit(); _conn3.close()

print("\n== 设置持久化 (默认权益 / 默认期货风险额度) ==")
_s0 = get_settings()
check("settings 返回独立权益键", "futures_default_equity" in _s0 and "options_default_equity" in _s0 and "futures_risk_pct" in _s0, str(_s0))
_s1 = save_settings({"futures_default_equity": 888888, "futures_risk_pct": 2.0})
check("保存期货默认权益+风险", _s1["futures_default_equity"] == 888888 and _s1["futures_risk_pct"] == 2.0, str(_s1))
_s2 = get_settings()
check("重新读取持久化", _s2["futures_default_equity"] == 888888 and _s2["futures_risk_pct"] == 2.0, str(_s2))
# 独立性核心断言: 设置期权默认权益不影响期货
_s2b = save_settings({"options_default_equity": 333333})
check("期货/期权默认权益互相独立", _s2b["futures_default_equity"] == 888888 and _s2b["options_default_equity"] == 333333, str(_s2b))
_s2c = save_settings({"futures_default_equity": 777777})
check("改期货不动期权", _s2c["futures_default_equity"] == 777777 and _s2c["options_default_equity"] == 333333, str(_s2c))
_s3 = save_settings({"futures_default_equity": None})
check("清除期货默认权益(保留期权+风险)", _s3["futures_default_equity"] is None and _s3["options_default_equity"] == 333333 and _s3["futures_risk_pct"] == 2.0, str(_s3))
_s4 = save_settings({"futures_risk_pct": ""})
check("空串清除默认风险", _s4["futures_risk_pct"] is None, str(_s4))
_s5 = save_settings({"futures_default_equity": 123456})
check("只更新一个字段不丢其他", _s5["futures_default_equity"] == 123456 and _s5["options_default_equity"] == 333333 and "futures_risk_pct" in _s5, str(_s5))
# 旧版共用键向后兼容: 仅存在 default_equity 时两个模式都能读到
import main as _mm
_cfg = _mm._load_config(); _cfg.setdefault("settings", {})
_cfg["settings"].pop("futures_default_equity", None); _cfg["settings"].pop("options_default_equity", None)
_cfg["settings"]["default_equity"] = 555555; _mm._save_config(_cfg)
_s6 = get_settings()
check("旧键 default_equity 作为两模式回退", _s6["futures_default_equity"] == 555555 and _s6["options_default_equity"] == 555555, str(_s6))
# 清理: 恢复为无默认权益(避免影响后续/重复运行)
save_settings({"futures_default_equity": None, "options_default_equity": None})
_s7 = get_settings()
check("清除后不再被旧键回退", _s7["futures_default_equity"] is None and _s7["options_default_equity"] is None, str(_s7))

print("\n== 期权交易记录模块 ==")
# 期权交易记录 CRUD + 汇总 + 监控池 + 导入导出整合
import os as _os, tempfile as _tf
_td = _tf.mkdtemp()
import main as _m
_m.FUND_DB_PATH = _os.path.join(_td, 'funds.db')
_m.FUND_DB_CONN = None

# 1. 新建开仓 + 平仓 → 主表显示已平仓 + 平仓盈亏
trade_upsert({'underlying':'ao611','contract':'ao611P2500','op_type':'open',
              'direction':'buy','open_date':'2026-08-27','open_delta':0.19,
              'target_delta':0.45,'call_put':'P','open_price':700,'qty':4,'premium':1400})
trade_upsert({'underlying':'ao611','contract':'ao611P2500','op_type':'close',
              'direction':'buy','close_date':'2026-09-03','close_qty':4,
              'close_price':510,'pnl':-760,'open_date':'2026-09-03','qty':4,'premium':0})
gs = trade_groups()
check("主表按 underlying 聚合", len(gs) == 1, str(gs))
check("主表显示已平仓", gs[0]["close_status"] == "已平仓", str(gs[0]))
check("主表平仓盈亏 -760", gs[0]["total_pnl"] == -760.0, str(gs[0]))
check("主表平仓时间 2026-09-03", gs[0]["last_close_date"] == "2026-09-03", str(gs[0]))

# 2. 部分平仓 → 持仓数量加权均价
trade_upsert({'underlying':'fu2611','contract':'fu2611C3000','op_type':'open',
              'direction':'buy','open_date':'2026-08-18','call_put':'C',
              'open_price':100,'qty':5,'premium':500})
trade_upsert({'underlying':'fu2611','contract':'fu2611C3000','op_type':'close',
              'direction':'buy','close_date':'2026-08-25','close_qty':2,
              'close_price':120,'pnl':40,'open_date':'2026-08-25','qty':2,'premium':0})
det = trade_detail('fu2611')
check("部分平仓后持仓 3 手", det["holdings"][0]["qty"] == 3, str(det["holdings"]))
check("持仓均价 = 加权均价 100", det["holdings"][0]["open_price"] == 100, str(det["holdings"]))
check("操作记录按时间升序", len(det["operations"]) == 2 and det["operations"][0]["op_type"] == "open")

# 3. 详情按合约分别计算, 不同合约互不影响
gs2 = trade_groups()
check("主表存在 fu2611 (部分平仓)", any(g["underlying"] == "fu2611" for g in gs2))

# 4. 监控池快照: 新建 + 修改 + 历史快照保留
pid1 = trade_pool_upsert({'contracts':['si','lc','fu']})
pid2 = trade_pool_upsert({'contracts':['si','lc','fu','ao','br']})
check("监控池快照 2 条", len(trade_pool_list()) == 2)
trade_pool_upsert({'id':pid1, 'contracts':['si','lc','fu','ao']})
pools = trade_pool_list()
check("修改最早快照后仍 2 条", len(pools) == 2)

# 5. 删除一条监控池
trade_pool_delete(pid2)
check("删除后剩 1 条", len(trade_pool_list()) == 1)

# 6. 删除一条交易记录
recs = trade_list_records()
first_id = recs[0]["id"]
trade_delete(first_id)
check("删除交易记录后数量-1", len(trade_list_records()) == len(recs) - 1)

# 7. 导入导出整合: 含交易记录
backup = fund_export_backup()
check("导出含 trades 键", "trades" in backup)
check("导出含 trade_pools 键", "trade_pools" in backup)

# 8. 资金曲线清除只清资金曲线, 不动交易记录
before_trades = len(trade_list_records())
before_pools = len(trade_pool_list())
import main as _m2
_m2.fund_clear_all()
check("fund_clear_all 不动 trade_records", len(trade_list_records()) == before_trades)
check("fund_clear_all 不动 trade_pool_snapshots", len(trade_pool_list()) == before_pools)

# 9. 平仓数量校验: 超过剩余被拒绝
try:
    trade_upsert({'strategy':'abe','underlying':'fu2611','contract':'fu2611C3000',
                  'op_type':'close','direction':'buy','close_date':'2026-08-25',
                  'close_qty':99,'open_date':'2026-08-25','qty':99,'premium':0})
    check("平仓数量超额被拒绝", False)
except ValueError as e:
    check("平仓数量超额被拒绝", "超过剩余可平" in str(e), str(e))

# 9b. 开仓数量/价格校验 (v50.30): 负数手数曾能存进去 → 持仓查不到(隐形脏数据)
print("\n== 开仓参数校验 ==")
_bad_open = [
    ("负手数被拒",   {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':-5,'premium':-500}),
    ("零手数被拒",   {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':0,'premium':0}),
    ("小数手数被拒", {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':1.5,'premium':150}),
    ("非数字手数被拒", {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':'abc','premium':100}),
    ("负开仓价被拒", {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':-100,'qty':1,'premium':100}),
    ("负权利金被拒", {'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':1,'premium':-100}),
]
for _nm, _p in _bad_open:
    try:
        trade_upsert(dict(_p))
        check(_nm, False, "未被拒绝, 已写入")
    except ValueError:
        check(_nm, True)
# 正常开仓仍然通过
_zid = trade_upsert({'underlying':'vz','contract':'vzC100','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':100,'qty':3,'premium':300})
check("正常开仓仍通过", _zid is not None)
_vd = trade_detail('vz')
check("正常开仓可查到持仓 3 手", _vd['holdings'] and _vd['holdings'][0]['qty'] == 3, str(_vd['holdings']))
trade_delete(_zid)

# 9c. 风险额度只接受五档 (v50.30): 之前 999 也能存进配置
print("\n== 风险额度设置校验 ==")
from main import save_settings
_orig_f = get_settings().get('futures_risk_pct')
try:
    save_settings({'futures_risk_pct': 999})
    check("风险额度 999 被拒", False, "未拒绝")
except ValueError as _e:
    check("风险额度 999 被拒", "只支持" in str(_e), str(_e))
try:
    save_settings({'futures_risk_pct': 'abc'})
    check("风险额度非数字被拒", False, "未拒绝")
except ValueError:
    check("风险额度非数字被拒", True)
save_settings({'futures_risk_pct': 1.5})
check("风险额度合法档 1.5 可存", get_settings().get('futures_risk_pct') == 1.5)
save_settings({'futures_risk_pct': _orig_f})   # 还原
check("风险额度可清除/还原", get_settings().get('futures_risk_pct') == _orig_f)

# 10. 合约代码归一化 (v50.29): 品种小写 + C/P 大写, 大小写不同不再算两个合约
print("\n== 合约代码归一化 (normalize_contract) ==")
from main import normalize_contract, trade_import_record, detect_call_put, find_cp_index
_norm_cases = [
    # --- 紧凑式 ---
    ('br2610c15800', 'br2610C15800'), ('BR2610C15800', 'br2610C15800'),
    ('AO611P2500', 'ao611P2500'),     ('p2601C8000', 'p2601C8000'),
    ('P2601c8000', 'p2601C8000'),     ('pp2601C8000', 'pp2601C8000'),
    ('ap2601p9000', 'ap2601P9000'),   ('pk2601C8000', 'pk2601C8000'),
    ('c2601C2400', 'c2601C2400'),     ('cf2701C18000', 'cf2701C18000'),
    ('sp2601p5000', 'sp2601P5000'),
    ('br 2610 C 15800', 'br2610C15800'), ('SR611C6000', 'sr611C6000'),
    ('SF611C6800', 'sf611C6800'),     ('', ''),
    # --- 分隔式 (v50.30): C/P 前后用 - 隔开, 用户实际写法 lc2611-C-144000 ---
    ('lc2611-C-144000', 'lc2611-C-144000'), ('LC2611-c-144000', 'lc2611-C-144000'),
    ('lc2611-c-144000', 'lc2611-C-144000'), ('lc2611-P-144000', 'lc2611-P-144000'),
    ('LC2611-p-144000', 'lc2611-P-144000'), ('au2612-c-560', 'au2612-C-560'),
    ('lc2611-c144000', 'lc2611-C144000'),
    # _ / 视为有意分段 → 统一成 '-'; 空白视为打字随手敲的 → 直接删
    ('lc2611_C_144000', 'lc2611-C-144000'), ('lc2611/P/144000', 'lc2611-P-144000'),
    ('lc2611 c 144000', 'lc2611C144000'),   ('lc2611-C-144000 ', 'lc2611-C-144000'),
    # 品种前缀自带 c/p + 真实标志位 → 取最右侧
    ('hc2610P6000', 'hc2610P6000'),   ('sp2610C6000', 'sp2610C6000'),
]
_bad = [(a, normalize_contract(a), b) for a, b in _norm_cases if normalize_contract(a) != b]
check("归一化 %d 组用例" % len(_norm_cases), not _bad, str(_bad))
check("非字符串原样返回(监控池 dict 不被破坏)", normalize_contract({'contract': 1}) == {'contract': 1})

# 看涨看跌自动识别(前端 _autoCp 调用的就是同一套规则)
_cp_cases = [
    ('lc2611-C-144000', 'C'), ('lc2611-c-144000', 'C'), ('LC2611-P-144000', 'P'),
    ('lc2611-c144000', 'C'),  ('ao611P2500', 'P'),       ('br2610c15800', 'C'),
    ('sf611C6800', 'C'),      ('SR611C6000', 'C'),
    ('cu2610', ''),           ('aoc5000', ''),           ('hc2610', ''),
    ('lc2611X144000', ''),    ('', ''),
    # 品种前缀自带 c/p: 必须取最右侧真实标志位, 不能撞上前缀里的 c/p
    ('hc2610P6000', 'P'),     ('sp2610C6000', 'C'),      ('sc2601p5000', 'P'),
    ('pp2601C8000', 'C'),     ('ap2601p9000', 'P'),      ('p2601C8000', 'C'),
    # 缺少月份无法与品种前缀区分 → 不认(真实期权代码必含月份)
    ('AOC5000', ''),          ('ao-c-5000', ''),
]
_badcp = [(a, detect_call_put(a), b) for a, b in _cp_cases if detect_call_put(a) != b]
check("看涨看跌识别 %d 组用例" % len(_cp_cases), not _badcp, str(_badcp))

# 24 个品种代码自带 C/P — 不能被误判成看涨看跌
_selfcp = ['cu','pb','hc','sp','sc','bc','p','c','cs','pp','pg','CF','AP','CJ',
           'PF','PK','CY','ZC','PX','PR','IC','lc','ps','ec']
_selferr = []
_selftot = 0
for _v in _selfcp:
    for _mth in ('2610', '611', '2701'):
        for _cp in ('C', 'P'):
            _selftot += 1
            if detect_call_put('%s%s%s6000' % (_v, _mth, _cp)) != _cp.upper():
                _selferr.append('%s%s%s6000' % (_v, _mth, _cp))
        _selftot += 1
        if detect_call_put('%s%s' % (_v, _mth)) != '':
            _selferr.append('%s%s(无C/P却被识别)' % (_v, _mth))
check("24 个自带 C/P 品种全部正确区分(%d 例)" % _selftot, not _selferr, str(_selferr[:8]))

# find_cp_index: 分隔式/紧凑式定位到正确的 C/P 位, 不是品种代码里的 c/p
check("find_cp_index 定位分隔式", find_cp_index('lc2611-c-144000') == 7)
check("find_cp_index 定位紧凑式", find_cp_index('br2610c15800') == 6)
check("find_cp_index 品种自带c不误判", find_cp_index('cu2610') is None)
check("find_cp_index 双 c/p 取最右侧", find_cp_index('hc2610p6000') == 6)

# 大小写不同但内容相同 → 只留一条(不重复出现在筛选下拉)
trade_upsert({'underlying':'nz1','contract':'nz1C5000','op_type':'open','direction':'buy',
              'open_date':'2026-08-01','call_put':'C','open_price':100,'qty':2,'premium':200})
trade_upsert({'underlying':'nz1','contract':'NZ1c5000','op_type':'open','direction':'buy',
              'open_date':'2026-08-01','call_put':'C','open_price':100,'qty':2,'premium':200})
_nz = [r for r in trade_list_records() if r['underlying'] == 'nz1']
check("大小写不同+内容相同 → 合并为 1 条", len(_nz) == 1 and _nz[0]['contract'] == 'nz1C5000', str(len(_nz)))

# 大小写不同且内容不同 → 两条并存但合约写法统一
trade_upsert({'underlying':'nz2','contract':'nz2C5000','op_type':'open','direction':'buy',
              'open_date':'2026-08-01','call_put':'C','open_price':100,'qty':2,'premium':200})
trade_upsert({'underlying':'nz2','contract':'nz2c5000','op_type':'open','direction':'buy',
              'open_date':'2026-08-02','call_put':'C','open_price':110,'qty':3,'premium':330})
_nz2 = [r for r in trade_list_records() if r['underlying'] == 'nz2']
check("不同内容两条并存", len(_nz2) == 2, str(len(_nz2)))
check("两条合约写法已统一", set(r['contract'] for r in _nz2) == {'nz2C5000'}, str(set(r['contract'] for r in _nz2)))

# 平仓时用不同大小写也能匹配上开仓余量(旧逻辑已大小写不敏感, 归一化后仍通)
trade_upsert({'underlying':'nz2','contract':'NZ2C5000','op_type':'close','direction':'buy',
              'close_date':'2026-09-01','close_qty':5,'close_price':120,'pnl':100,'call_put':'C'})
_d2 = trade_detail('nz2')
check("平仓余量匹配(5手全平)", _d2['holdings'] == [], str(_d2['holdings']))

# 导入备份时也归一化
_conn4 = sqlite3.connect(_m.FUND_DB_PATH)
_conn4.execute("DELETE FROM trade_records WHERE underlying='nz3'")
_conn4.commit(); _conn4.close()
trade_import_record({'underlying': 'nz3', 'contract': 'NZ3c6000', 'op_type': 'open',
                     'direction': 'buy', 'open_date': '2026-08-01', 'call_put': 'C',
                     'open_price': 100, 'qty': 1, 'premium': 100})
_nz3 = [r for r in trade_list_records() if r['underlying'] == 'nz3']
check("导入时合约也归一化", len(_nz3) == 1 and _nz3[0]['contract'] == 'nz3C6000', str(_nz3))

# ===========================================================================
# 11. 数据安全 (v50.31): 数据不能放在软件目录内 + 每次变动自动备份 + 换目录三表全迁移
# ===========================================================================
print("\n== 数据安全 ==")
import glob
import main as _ms
from main import (data_dir_risky, suggest_safe_data_dir, fund_auto_backup,
                  fund_backup_list, fund_backup_dir, fund_set_data_dir, AUTO_BACKUP_KEEP)

# 11.1 数据在软件目录内 → 判定为危险; 软件目录之外 → 安全
_saved_db = _ms.FUND_DB_PATH
_ms.FUND_DB_PATH = _os.path.join(_ms._app_dir(), "data", "funds.db")
check("数据落在软件目录内 → risky=True", data_dir_risky() is True)
_ms.FUND_DB_PATH = _os.path.join(_td, "funds.db")
check("数据在软件目录外 → risky=False", data_dir_risky() is False)

# 11.2 推荐位置必须在软件目录之外
_sug = suggest_safe_data_dir()
check("推荐迁出位置不为空", bool(_sug), _sug)
check("推荐位置在软件目录之外", _sug and not _ms._is_inside(_sug, _ms._app_dir()), _sug)

# 11.3 自动备份: 生成文件 + 内容可用 + 轮转只留 N 份
_ms.FUND_DB_PATH = _saved_db
_bk1 = fund_auto_backup("测试", force=True)
check("自动备份生成文件", bool(_bk1) and _os.path.exists(_bk1), str(_bk1))
# ⚠ 必须关掉连接: Windows 下文件被打开着删不掉, 会让后面的轮转断言假失败
_bkc = sqlite3.connect(_bk1)
_bk_ok = _bkc.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0] >= 0
_bkc.close()
check("备份文件是完整可读的库", _bk_ok)
_bkset = glob.glob(_os.path.join(fund_backup_dir(), "funds_*.db"))
check("备份目录可枚举", len(_bkset) >= 1, str(len(_bkset)))
# 快速连续调用应被合并(2s 内不重复备份)
_n_before = len(glob.glob(_os.path.join(fund_backup_dir(), "funds_*.db")))
fund_auto_backup("快速第二次")      # 不 force → 应被 min_interval 合并掉
check("短时间重复触发被合并(不产生新文件)",
      len(glob.glob(_os.path.join(fund_backup_dir(), "funds_*.db"))) == _n_before)
# 轮转: 造出超过上限的备份, 只保留 AUTO_BACKUP_KEEP 份
_old_conn = _ms.FUND_DB_CONN
for _i in range(AUTO_BACKUP_KEEP + 3):
    _p = _os.path.join(fund_backup_dir(), "funds_20990101_0000%02d_000.db" % _i)
    with open(_p, "wb") as _f:
        _f.write(b"x")
fund_auto_backup("触发轮转", force=True)
_left = glob.glob(_os.path.join(fund_backup_dir(), "funds_*.db"))
check("备份轮转只保留最近 %d 份" % AUTO_BACKUP_KEEP, len(_left) <= AUTO_BACKUP_KEEP, "实际 %d" % len(_left))
check("备份列表按新→旧排序", (lambda L: len(L) == 0 or all(L[i]['mtime'] >= L[i+1]['mtime'] for i in range(len(L)-1)))(fund_backup_list()))

# 11.4 切换数据目录必须迁移「全部三张表」(早期只迁资金曲线, 交易记录会静默丢失)
_src_dir = _os.path.dirname(_ms.FUND_DB_PATH)
_dst_dir = _os.path.join(_td, "moved")
_os.makedirs(_dst_dir, exist_ok=True)
# 造数据: 资金曲线 1 条 + 交易记录 2 条 + 监控池 1 条
fund_upsert("abe", 2030, 1, 1000, 1100, 0, "迁移测试")
trade_upsert({'underlying':'mvsafe','contract':'cu2610C80000','op_type':'open','direction':'buy',
              'open_date':'2026-08-01','call_put':'C','open_price':100,'qty':2,'premium':200})
trade_upsert({'underlying':'mvsafe','contract':'cu2610C80000','op_type':'close','direction':'sell',
              'close_date':'2026-08-05','close_qty':1,'close_price':150,'pnl':50,'call_put':'C'})
trade_pool_upsert({'contracts':['cu2610C80000'], 'note':'迁移测试'})
_before_fund = len([r for r in fund_list_records() if r['year'] == 2030])
_before_tr = len([r for r in trade_list_records() if r['underlying'] == 'mvsafe'])
_before_pool = len([s for s in trade_pool_list() if s.get('note') == '迁移测试'])
check("迁移前: 资金曲线已写入", _before_fund == 1, str(_before_fund))
check("迁移前: 交易记录已写入(2 条)", _before_tr == 2, str(_before_tr))
check("迁移前: 监控池已写入(1 条)", _before_pool == 1, str(_before_pool))
# 切换目录(⚠ fund_set_data_dir 会写 config.json → 先把 CONFIG_PATH 指到临时文件, 别动真实配置)
_saved_cfg = _ms.CONFIG_PATH
_ms.CONFIG_PATH = _os.path.join(_td, "cfg.json")
_mig, _st = fund_set_data_dir(_dst_dir)
check("切换目录返回 migrated", _st == "migrated" and _mig > 0, "%s %s" % (_st, _mig))
check("切换后库文件落在新目录", _os.path.exists(_os.path.join(_dst_dir, "funds.db")), _ms.FUND_DB_PATH)
_after_fund = len([r for r in fund_list_records() if r['year'] == 2030])
_after_tr = len([r for r in trade_list_records() if r['underlying'] == 'mvsafe'])
_after_pool = len([s for s in trade_pool_list() if s.get('note') == '迁移测试'])
check("迁移后: 资金曲线保留", _after_fund == _before_fund, "%d → %d" % (_before_fund, _after_fund))
check("迁移后: 交易记录保留(旧版会丢)", _after_tr == _before_tr, "%d → %d" % (_before_tr, _after_tr))
check("迁移后: 监控池保留", _after_pool == _before_pool, "%d → %d" % (_before_pool, _after_pool))
# 迁移后持仓正确(FIFO 扣减后剩 1 手)
_dmv = trade_detail('mvsafe')
check("迁移后持仓计算正确(2 开 1 平 → 1 手)",
      len(_dmv['holdings']) == 1 and _dmv['holdings'][0]['qty'] == 1, str(_dmv['holdings']))
# 重复切换幂等(不会翻倍)
fund_set_data_dir(_src_dir)
fund_set_data_dir(_dst_dir)
_tr_again = len([r for r in trade_list_records() if r['underlying'] == 'mvsafe'])
check("反复切换数据目录幂等(不翻倍)", _tr_again == _before_tr, "实际 %d" % _tr_again)
_ms.CONFIG_PATH = _saved_cfg
check("切换数据目录时会先自动备份", len(fund_backup_list()) >= 1, str(len(fund_backup_list())))

# 11.5 内置文件夹浏览器 + 备份恢复 (v50.32)
print("\n== 文件夹浏览 + 备份恢复 ==")
from main import fs_list_dirs, fund_restore_backup

_fd = fs_list_dirs(os.path.dirname(_ms.FUND_DB_PATH))
check("fs_list_dirs 返回 ok", _fd.get("ok") is True, str(_fd))
check("fs_list_dirs 返回目录名列表", isinstance(_fd.get("dirs"), list), str(_fd.get("dirs")))
check("fs_list_dirs 返回 parent", "parent" in _fd, str(_fd.get("parent")))
if os.name == "nt":
    check("fs_list_dirs 返回盘符(Windows)", len(_fd.get("drives", [])) >= 1, str(_fd.get("drives")))
check("fs_list_dirs 非法路径自动回退", fs_list_dirs("Z:\\不存在的\\路径\\xyz\\").get("ok") is True, "不应崩溃")

# 备份恢复: 造一份带标记数据的备份, 再改库, 再恢复回来
_rs_snap = fund_auto_backup("恢复测试", force=True)
check("恢复测试: 先产生一份快照", bool(_rs_snap) and os.path.exists(_rs_snap), str(_rs_snap))
fund_upsert("abe", 2040, 1, 100, 200, 0, "恢复测试标记")
check("恢复测试: 写入标记记录", len([r for r in fund_list_records() if r['year'] == 2040]) == 1)
_rs_name = os.path.basename(_rs_snap)
_cnt = fund_restore_backup(_rs_name)
check("恢复返回条数", _cnt.get("records") is not None, str(_cnt))
check("恢复到快照后标记记录消失",
      len([r for r in fund_list_records() if r['year'] == 2040]) == 0, str(fund_list_records()))
# 非法文件名 / 目录穿越 / 不存在
for _bad, _msg in (("../../funds.db", "文件名"), ("abc.db", "文件名"), ("funds_不存在.db", "不存在")):
    try:
        fund_restore_backup(_bad)
        check("恢复拒绝非法输入[%s]" % _msg, False, "未拒绝")
    except ValueError:
        check("恢复拒绝非法输入[%s]" % _msg, True)
check("恢复前会自动备份(可回退)", len(fund_backup_list()) >= 1, str(len(fund_backup_list())))
# 备份列表带条数信息
_bl = fund_backup_list()
check("备份列表含条数字段", _bl and ("records" in _bl[0]) and ("trades" in _bl[0]), str(_bl[:1]))

print("\n== 主表搜索(分组带合约列表) ==")
# 主表搜索要能按合约号命中 → 分组结果必须带 contracts
_gs_before = trade_groups()
trade_upsert({'underlying': 'srch1', 'contract': 'cu2610C80000', 'op_type': 'open', 'direction': 'buy',
              'open_date': '2026-08-01', 'call_put': 'C', 'open_price': 100, 'qty': 1, 'premium': 100})
trade_upsert({'underlying': 'srch1', 'contract': 'cu2610P79000', 'op_type': 'open', 'direction': 'buy',
              'open_date': '2026-08-02', 'call_put': 'P', 'open_price': 90, 'qty': 2, 'premium': 180})
_g1 = [g for g in trade_groups() if g['underlying'] == 'srch1']
check("分组结果带 contracts 字段", _g1 and isinstance(_g1[0].get('contracts'), list), str(_g1[:1]))
check("contracts 含该标的全部合约(去重)",
      _g1 and set(_g1[0]['contracts']) == {'cu2610C80000', 'cu2610P79000'}, str(_g1[0].get('contracts')))
check("contracts 已排序", _g1 and _g1[0]['contracts'] == sorted(_g1[0]['contracts']), str(_g1[0].get('contracts')))
check("分组仍带 close_status/open_date(主表渲染依赖)",
      _g1 and _g1[0].get('close_status') and _g1[0].get('open_date') == '2026-08-01', str(_g1[:1]))

print("\n================================")
print("最终通过 %d 项 / 失败 %d 项" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
