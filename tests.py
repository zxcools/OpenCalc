# -*- coding: utf-8 -*-
"""开仓计算器 - 计算逻辑测试 (v4: 期货手数按风险金额推, 期权按权利金)"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import calc_futures, calc_options, get_contract, CONTRACTS, MIN_PROFIT_LOSS_RATIO, fund_upsert, fund_list_records, fund_yearly_summary, fund_combined_summary, fund_delete, FUND_STRATEGIES, get_settings, save_settings

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
_t = {"rb": 1, "au": 0.02, "i": 0.5, "lc": 50, "IF": 0.2, "T": 0.005, "TA": 2, "ec": 0.1, "bu": 1, "j": 0.5}
for code, expect in _t.items():
    c = get_contract(code)
    check("%s tick=%s" % (code, expect), c and abs(c["tick"] - expect) < 1e-9, str(c and c["tick"]))
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
_conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "funds.db"))
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
_conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "funds.db"))
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

# 准备数据
_conn3 = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "funds.db"))
_conn3.execute("DELETE FROM records"); _conn3.commit(); _conn3.close()
fund_upsert("abe", 2025, 8, 60000, 70000, 1000, "8月")
fund_upsert("威科夫", 2025, 9, 60000, 50000, 0, "9月")

backup = fund_export_backup()
check("导出包含 records 数组", isinstance(backup.get("records"), list) and len(backup["records"]) == 2)
check("导出含版本/时间戳", "backup_version" in backup and "exported_at" in backup)

# 清空再导入 (模拟换电脑)
_conn3 = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "funds.db"))
_conn3.execute("DELETE FROM records"); _conn3.commit(); _conn3.close()
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

print("\n== 设置持久化 (默认权益 / 默认期货风险额度) ==")
_s0 = get_settings()
check("settings 返回两个键", "default_equity" in _s0 and "futures_risk_pct" in _s0, str(_s0))
_s1 = save_settings({"default_equity": 888888, "futures_risk_pct": 2.0})
check("保存默认权益+风险", _s1["default_equity"] == 888888 and _s1["futures_risk_pct"] == 2.0, str(_s1))
_s2 = get_settings()
check("重新读取持久化", _s2["default_equity"] == 888888 and _s2["futures_risk_pct"] == 2.0, str(_s2))
_s3 = save_settings({"default_equity": None})
check("清除默认权益(保留风险)", _s3["default_equity"] is None and _s3["futures_risk_pct"] == 2.0, str(_s3))
_s4 = save_settings({"futures_risk_pct": ""})
check("空串清除默认风险", _s4["futures_risk_pct"] is None, str(_s4))
_s5 = save_settings({"default_equity": 123456})
check("只更新一个字段不丢其他", _s5["default_equity"] == 123456 and "futures_risk_pct" in _s5, str(_s5))

print("\n================================")
print("最终通过 %d 项 / 失败 %d 项" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
