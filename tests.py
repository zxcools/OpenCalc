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

# v50.69: 单个品种的报价单位不是「元/吨」时, 乘数是「报价→每手金额」的乘数
#   鸡蛋 jd: 交易单位 5 吨/手, 但报价单位是 元/500千克(大商所鸡蛋期货合约第八/九条)
#   → 报价 3500 元/500千克, 每手金额 = 3500 × 10 = 35000 元, 所以乘数写 10
_jd = get_contract("jd")
check("鸡蛋乘数(v50.69): 报价单位 元/500千克 → 乘数必须是 10(不是吨位 5)",
      _jd and _jd["mult"] == 10 and _jd["unit"] == "500千克/手", str(_jd))
_rjd = calc_futures({"equity": 1000000, "code": "jd", "direction": "long",
                     "entry": 3500, "stop": 3450, "target": 3650})
check("鸡蛋每手风险(v50.69): 价差 50 × 乘数 10 = 500 元(按吨位 5 会少算一半)",
      abs(_rjd["per_lot_risk"] - 500) < 0.01, str(_rjd["per_lot_risk"]))
check("鸡蛋每手保证金(v50.69): 3500 × 10 × 16% = 5600 元",
      abs(_rjd["margin_per_lot"] - 5600) < 0.01, str(_rjd["margin_per_lot"]))

# ===== v50.70: 手续费(交易所标准 + 0.01 元/手) =====
print("\n== 手续费 / 交易日 ==")
from main import (row_fee, trade_day, fifo_open_day, FEE_TABLE, fee_product,  # noqa: E402
                  trade_groups, FEE_MARKUP)

_missing_fee = [c[0] for c in CONTRACTS if c[0] not in FEE_TABLE]
check("手续费表(v50.70): 合约表里 %d 个品种全部有费率" % len(CONTRACTS), not _missing_fee,
      "缺: %s" % _missing_fee)
check("手续费加成(v50.70): 宏源 = 交易所 + 0.01 元/手", abs(FEE_MARKUP - 0.01) < 1e-12, str(FEE_MARKUP))


def _frow(**kw):
    r = {"mode": "futures", "op_type": "open", "underlying": "", "contract": "",
         "open_price": 0, "qty": 0, "premium": 0, "open_date": "", "open_time": "",
         "close_price": None, "close_qty": None, "close_date": "", "close_time": ""}
    r.update(kw)
    return r


check("手续费(v50.70): 定额 铝 3元/手 × 2手 + 0.01×2 = 6.02",
      abs(row_fee(_frow(underlying="al", open_price=20000, qty=2), "futures", "ex") - 6.02) < 1e-9,
      str(row_fee(_frow(underlying="al", open_price=20000, qty=2), "futures", "ex")))
check("手续费(v50.70): 比例 螺纹 万分之0.2, 3500×10×2手 = 1.4 + 0.02 = 1.42",
      abs(row_fee(_frow(underlying="rb", open_price=3500, qty=2), "futures", "ex") - 1.42) < 1e-9,
      str(row_fee(_frow(underlying="rb", open_price=3500, qty=2), "futures", "ex")))
check("手续费(v50.70): 不计算模式一律 0",
      row_fee(_frow(underlying="al", open_price=20000, qty=2), "futures", "off") == 0, '')
check("手续费(v50.70): 表里没有的品种算 0(不猜)",
      row_fee(_frow(underlying="zzz", open_price=100, qty=1), "futures", "ex") == 0, '')
from main import fee_known  # noqa: E402
check("手续费(v50.70): 表里没有的品种算「未收录」而不是 0",
      fee_known(_frow(underlying="zzz")) is False, '')
check("手续费(v50.70): 欧线只有期货费率 → 期权记录标记未收录(不假装免费)",
      fee_known(_frow(underlying="ec")) is True and fee_known(_frow(mode="options", underlying="ec")) is False, '')
check("手续费(v50.70): 有期权费率的品种(玻璃)期权记录算得出",
      fee_known(_frow(mode="options", underlying="fg")) is True, '')
_cf_close = dict(underlying="CF", contract="CF2701", op_type="close", close_price=15000,
                 close_qty=2, close_date="2026-10-10", close_time="10:00")
check("手续费(v50.70): 平今 棉花(平今0) → 只收 0.01/手 = 0.02",
      abs(row_fee(_frow(**_cf_close), "futures", "ex", open_day="2026-10-10") - 0.02) < 1e-9,
      str(row_fee(_frow(**_cf_close), "futures", "ex", open_day="2026-10-10")))
check("手续费(v50.70): 平昨(不同交易日) → 按基准 4.3元/手 = 8.62",
      abs(row_fee(_frow(**_cf_close), "futures", "ex", open_day="2026-10-09") - 8.62) < 1e-9,
      str(row_fee(_frow(**_cf_close), "futures", "ex", open_day="2026-10-09")))
check("手续费(v50.70): 平今比例 铜 万分之1 → 70000×5×1/10000 + 0.01 = 35.01",
      abs(row_fee(_frow(underlying="cu", contract="cu2612", op_type="close", close_price=70000,
                        close_qty=1, close_date="2026-10-10"), "futures", "ex",
                  open_day="2026-10-10") - 35.01) < 1e-9, '')
check("手续费(v50.70): 期权用「期权费率」玻璃 0.5元/手 × 3手 + 0.03 = 1.53",
      abs(row_fee(_frow(mode="options", underlying="fg", contract="fg2701-P-800",
                        open_price=7, qty=3), "options", "ex") - 1.53) < 1e-9, '')
check("手续费(v50.70): 鸡蛋按 元/500千克 口径 → 3500×10×1.5/10000 + 0.01 = 5.26",
      abs(row_fee(_frow(underlying="jd", open_price=3500, qty=1), "futures", "ex") - 5.26) < 1e-9,
      str(row_fee(_frow(underlying="jd", open_price=3500, qty=1), "futures", "ex")))
check("手续费(v50.70): 从「标的」或「合约」都能解析品种(fg2701空 / fg2701-P-800)",
      fee_product("fg2701空", "") == "FG" and fee_product("", "fg2701-P-800") == "FG", '')
check("交易日夜盘(v50.70): 21:30 开仓 → 算下一个交易日",
      trade_day("2026-10-08", "21:30") == "2026-10-09", trade_day("2026-10-08", "21:30"))
# v50.77: 接入交易日历 —— 夜盘要跳到「下一个交易日」, 不是简单 +1 天
check("交易日历(v50.77): 周五 21:30 → 下周一(不是周六)",
      trade_day("2026-10-09", "21:30") == "2026-10-12", trade_day("2026-10-09", "21:30"))
check("交易日历(v50.77): 周五夜盘跨到周六凌晨 → 交易日仍是下周一",
      trade_day("2026-10-10", "01:00") == "2026-10-12", trade_day("2026-10-10", "01:00"))
check("交易日(v50.70): 没填时间 → 日期即交易日(日盘, 不推算)",
      trade_day("2026-10-10", "") == "2026-10-10", trade_day("2026-10-10", ""))
# 夜盘开的仓, 下一个交易日早盘平 —— 日期跨天但同一交易日 → 平今
_cf_night = dict(underlying="CF", contract="CF2701", op_type="close", close_price=15000,
                 close_qty=1, close_date="2026-10-12", close_time="09:30")
check("平今判定(v50.77): 周五夜盘(10-09 21:30)开的仓, 下周一 09:30 平 = 平今(只 0.01)",
      abs(row_fee(_frow(**_cf_night), "futures", "ex", open_day=trade_day("2026-10-09", "21:30")) - 0.01) < 1e-9, '')
check("平今判定(v50.70): 09:30 开、当天 21:30 平 → 一个日盘一个夜盘, 是平昨",
      trade_day("2026-10-09", "09:30") != trade_day("2026-10-09", "21:30"), '')

# ===== v50.77: 最小交易日历(周末 + 法定节假日) =====
print("\n== 交易日历 v50.77 ==")
from main import (is_trading_day, next_trading_day, MARKET_HOLIDAYS,  # noqa: E402
                  MARKET_HOLIDAY_YEARS, _import_fail)

check("交易日历(v50.77): 节假日表覆盖 2025/2026(每年要更新一次)",
      {2025, 2026} <= MARKET_HOLIDAY_YEARS, str(sorted(MARKET_HOLIDAY_YEARS)))

# 周末判定
check("交易日历(v50.77): 周六/周日不是交易日",
      is_trading_day("2026-10-10") is False and is_trading_day("2026-10-11") is False,
      '%s/%s' % (is_trading_day("2026-10-10"), is_trading_day("2026-10-11")))
check("交易日历(v50.77): 普通工作日是交易日",
      is_trading_day("2026-10-12") is True and is_trading_day("2026-10-13") is True, '')
# 法定节假日(含调休: 2026 春节 2/15~2/23 连休 9 天)
check("交易日历(v50.77): 元旦/春节/清明/劳动节/端午/中秋/国庆 都休市",
      all(is_trading_day(d) is False for d in
          ("2026-01-01", "2026-02-16", "2026-04-06", "2026-05-01",
           "2026-06-19", "2026-09-25", "2026-10-01")), '')
check("交易日历(v50.77): 2025 春节(含除夕)/国庆+中秋 也休市",
      all(is_trading_day(d) is False for d in ("2025-01-28", "2025-10-08")), '')
check("交易日历(v50.77): 节假日表里周末之外的调休工作日仍**不开市**(期货只看工作日)",
      is_trading_day("2026-02-14") is False and is_trading_day("2026-10-10") is False, '')
# 非法输入不炸
check("交易日历(v50.77): 空串/乱码输入不抛异常",
      is_trading_day("") is False and is_trading_day("bad") is False
      and next_trading_day("") == "" and next_trading_day("bad") == "bad", '')

# next_trading_day: 跨周末、跨节假日、跨整段长假
check("交易日历(v50.77): 周五的下一个交易日 = 下周一",
      next_trading_day("2026-10-09") == "2026-10-12", next_trading_day("2026-10-09"))
check("交易日历(v50.77): 长假前一天 → 跳过整段假期(9/30 → 10/8)",
      next_trading_day("2026-09-30") == "2026-10-08", next_trading_day("2026-09-30"))
check("交易日历(v50.77): 春节前最后一个交易日 → 节后第一个交易日(2/13 → 2/24)",
      next_trading_day("2026-02-13") == "2026-02-24", next_trading_day("2026-02-13"))
check("交易日历(v50.77): 节假日中间也能算出节后第一个交易日",
      next_trading_day("2026-10-03") == "2026-10-08", next_trading_day("2026-10-03"))

# 夜盘落在长假前 → 交易日是节后第一天(不是假期间某天)
check("交易日历(v50.77): 国庆前夜(9/30 21:30) → 交易日 10/08",
      trade_day("2026-09-30", "21:30") == "2026-10-08", trade_day("2026-09-30", "21:30"))
check("交易日历(v50.77): 周四 21:30 → 周五(普通夜盘, 不变)",
      trade_day("2026-10-08", "21:30") == "2026-10-09", trade_day("2026-10-08", "21:30"))
check("交易日历(v50.77): 日盘时间不受交易日历影响(只按日期)",
      trade_day("2026-10-10", "09:30") == "2026-10-10", trade_day("2026-10-10", "09:30"))
# 周五夜盘 vs 下周一早盘: 同一交易日 → 平今(这是这次修的核心场景)
check("交易日历(v50.77): 周五夜盘与下周一早盘属同一交易日",
      trade_day("2026-10-09", "21:30") == trade_day("2026-10-12", "09:30"), '')

# ===== v50.77: 导入/迁移的失败明细(以前静默 pass) =====
print("\n== 导入失败明细 v50.77 ==")
_imp_ok, _imp_strat, _imp_fail = fund_import_backup(
    {"records": [{"strategy": "期权买方", "year": 2032, "month": 3,
                  "initial_equity": 1000, "end_equity": 1000}],
     "trades": [{"underlying": "okrec", "contract": "okrecC1", "op_type": "open",
                 "direction": "buy", "open_date": "2026-01-05", "open_price": 1, "qty": 1,
                 "premium": 1, "mode": "options"},
                {"op_type": "open"},                       # 缺 underlying/contract → 失败
                {"underlying": "badop", "contract": "x", "op_type": "nope",
                 "direction": "buy"}]})                    # op_type 非法 → 失败
check("导入失败明细(v50.77): 返回三元组且成功条数正确",
      _imp_ok == 2 and isinstance(_imp_fail, list), "ok=%s fail=%s" % (_imp_ok, _imp_fail))
check("导入失败明细(v50.77): 失败条数 = 2 且带条号与原因",
      len(_imp_fail) == 2 and all("交易记录 第" in x for x in _imp_fail), str(_imp_fail))
check("导入失败明细(v50.77): 明细含可读原因(缺字段 / op_type 非法)",
      any("缺少必填字段" in x for x in _imp_fail) and any("op_type" in x for x in _imp_fail),
      str(_imp_fail))
# 监控池 / 复盘 的失败也要收集
_pf = fund_import_backup({"records": [], "trade_pools": [{"contracts": "not-a-list"},
                                                        {"contracts": 123}]})[2]
check("导入失败明细(v50.77): 监控池失败也收集", len(_pf) == 2 and all("监控池" in x for x in _pf), str(_pf))
_rf = fund_import_backup({"records": [], "trade_reviews": [{"mode": "options"}]})[2]
check("导入失败明细(v50.77): 复盘失败也收集(空内容)",
      len(_rf) == 1 and "复盘" in _rf[0] and "不能为空" in _rf[0], str(_rf))
# 全成功时明细为空
_no_fail = fund_import_backup({"records": [], "trades": []})[2]
check("导入失败明细(v50.77): 全部成功时明细为空", _no_fail == [], str(_no_fail))
check("导入失败明细(v50.77): 明细文案经过截断(超长异常不撑爆提示)",
      len(_import_fail("交易记录", 1, Exception("x" * 400))) <= 140,
      str(len(_import_fail("交易记录", 1, Exception("x" * 400)))))
# 时间字段统一截成 HH:MM(v50.77: trade_import_record 与 trade_upsert 同口径)
from main import trade_import_record  # noqa: E402
_tid = trade_import_record({"underlying": "tcut", "contract": "tcutC1", "op_type": "open",
                            "direction": "buy", "open_date": "2026-02-24",
                            "open_time": "21:20:00", "close_time": "09:05:59",
                            "open_price": 1, "qty": 1, "premium": 1, "mode": "options"})
_got = [x for x in trade_list_records() if x["underlying"] == "tcut"][0]
check("导入(v50.77): open_time/close_time 统一截成 HH:MM",
      _got["open_time"] == "21:20" and _got["close_time"] == "09:05",
      "%r / %r" % (_got["open_time"], _got["close_time"]))
for _x in [x for x in trade_list_records() if x["underlying"] in ("tcut", "okrec")]:
    trade_delete(_x["id"])
fund_delete("期权买方", 2032, 3)

# ===== v50.78: 平仓两档手续费估算 / 未平仓风险汇总 / 备份差异预览 =====
print("\n== v50.78 新增能力 ==")
from main import (fee_estimate, open_risk_summary, fund_backup_diff,  # noqa: E402
                  fund_auto_backup, DEFAULT_MARGIN_RATE,
                  norm_date_field, calendar_missing_years)

# --- ① 平仓弹窗的两档手续费: 必须与 row_fee 同源(不能前后端各算一套) ---
_fe1 = fee_estimate("CF", "CF2701", "futures", 15000, 2)
check("费率估算(v50.78): 棉花 2 手 → 平今 0.02 / 平昨 8.62(两档不同)",
      abs(_fe1["fee_today"] - 0.02) < 1e-9 and abs(_fe1["fee_prev"] - 8.62) < 1e-9
      and _fe1["same"] is False and _fe1["known"] is True, str(_fe1))
check("费率估算(v50.78): 与 row_fee 同源(同一笔平仓算出来一模一样)",
      abs(_fe1["fee_prev"] - row_fee(_frow(underlying="CF", contract="CF2701", op_type="close",
                                           close_price=15000, close_qty=2, close_today="0"),
                                     "futures", "ex")) < 1e-9, str(_fe1))
check("费率估算(v50.78): 没单列平今的品种(铝) → 两档相同(same=True)",
      (lambda f: f["same"] is True and f["fee_today"] == f["fee_prev"])(fee_estimate("al2611", "", "futures", 20000, 1)),
      str(fee_estimate("al2611", "", "futures", 20000, 1)))
check("费率估算(v50.78): 费率表未收录 → known=False(前端提示按 ¥0 计, 不假装 0)",
      fee_estimate("zzzz9999", "", "futures", 100, 1)["known"] is False,
      str(fee_estimate("zzzz9999", "", "futures", 100, 1)))
_fe4 = fee_estimate("", "fg2701-P-800", "options", 100, 2)
check("费率估算(v50.78): 期权走期权费率(is_option=True 且金额>0)",
      _fe4["is_option"] is True and _fe4["fee_today"] > 0, str(_fe4))
check("费率估算(v50.78): 0 手 → 0 元(不抛异常)",
      fee_estimate("CF", "CF2701", "futures", 15000, 0)["fee_today"] == 0.0, '')
# v50.79: 开仓弹窗也要金额 —— 开仓不分平今/平昨, 永远走基准费率(数值上 = 平昨那档)
check("费率估算(v50.79): 开仓档 = 基准费率(棉花 2 手 8.62, 与平昨同值)",
      abs(_fe1["fee_open"] - 8.62) < 1e-9 and abs(_fe1["fee_open"] - _fe1["fee_prev"]) < 1e-9,
      str(_fe1))
check("费率估算(v50.79): 开仓档与 row_fee 的开仓行同源",
      abs(_fe1["fee_open"] - row_fee(_frow(underlying="CF", contract="CF2701", op_type="open",
                                           open_price=15000, qty=2),
                                     "futures", "ex")) < 1e-9, str(_fe1))
check("费率估算(v50.79): 比例费率的品种开仓档按 价格×乘数×手数(螺纹 3200×1 手)",
      abs(fee_estimate("rb", "rb2701", "futures", 3200, 1)["fee_open"]
          - row_fee(_frow(underlying="rb", contract="rb2701", op_type="open",
                          open_price=3200, qty=1), "futures", "ex")) < 1e-9, '')
check("费率估算(v50.79): 期权开仓档 > 0 且 0 手 = 0",
      fee_estimate("", "fg2701-P-800", "options", 100, 2)["fee_open"] > 0
      and fee_estimate("", "fg2701-P-800", "options", 100, 0)["fee_open"] == 0.0, '')

# --- ② 未平仓头寸风险汇总(用独立策略名隔离, 不干扰其它用例) ---
# ⚠ trade_upsert 把 strategy 固定成「期权买方」(自定义策略 UI 已撤除), 隔离不了;
#   这里走 trade_import_record —— 它会保留传入的 strategy
_SU_RISK = "风险汇总测试"
def _mk_open(u, contract, qty, price, stop=None, mode="futures", premium=0, calc=None):
    return trade_import_record({"strategy": _SU_RISK, "underlying": u, "contract": contract,
                                "op_type": "open", "direction": "buy", "open_date": "2026-09-01",
                                "open_price": price, "qty": qty, "premium": premium,
                                "mode": mode, "init_stop": stop, "calc_json": calc})

_mi = get_contract("i")["mult"]
_mrb = get_contract("rb")["mult"]
_id_i = _mk_open("i", "i2701", 2, 700, stop=690)                      # 有初始止损
_id_rb = _mk_open("rb", "rb2701", 3, 3200)                            # 没设止损
_id_cu = _mk_open("cu", "cu2612C80000", 3, 55, mode="options", premium=1500)   # 期权买方
_id_sm = _mk_open("sm", "sm2701", 4, 6200, calc='{"margin_rate": 0.10}')       # 自定义保证金率

_rk = open_risk_summary(_SU_RISK, "futures")
check("风险汇总(v50.78): 期货未平仓 3 笔 / 手数 2+3+4=9",
      _rk["positions"] == 3 and _rk["lots"] == 9, str({k: _rk[k] for k in ("positions", "lots")}))
_exp_occ = 700 * _mi * 2 * DEFAULT_MARGIN_RATE + 3200 * _mrb * 3 * DEFAULT_MARGIN_RATE + 6200 * get_contract("sm")["mult"] * 4 * 0.10
check("风险汇总(v50.78): 占用保证金 = 各仓 均价×乘数×手数×保证金率(取 calc_json 里的 10%)",
      abs(_rk["occupied"] - round(_exp_occ, 2)) < 0.02, 'got=%s exp=%s' % (_rk["occupied"], round(_exp_occ, 2)))
check("风险汇总(v50.78): 理论最大亏损只算「有初始止损」的 i 仓 = |700−690|×乘数×2",
      abs(_rk["max_loss"] - round(abs(700 - 690) * _mi * 2, 2)) < 0.02, str(_rk["max_loss"]))
check("风险汇总(v50.78): 没设止损的手数被单列(3+4=7), 并标记 max_loss 只是下限",
      _rk["no_stop_lots"] == 7 and _rk["max_loss_is_partial"] is True,
      str({"no_stop": _rk["no_stop_lots"], "partial": _rk["max_loss_is_partial"]}))
check("风险汇总(v50.78): 明细按占用资金从大到小",
      [x["occupied"] for x in _rk["items"]] == sorted([x["occupied"] for x in _rk["items"]], reverse=True),
      str([(x["underlying"], x["occupied"]) for x in _rk["items"]]))
check("风险汇总(v50.78): 明细含合约与手数", all(x["contracts"] and x["lots"] > 0 for x in _rk["items"]),
      str(_rk["items"]))

_rko = open_risk_summary(_SU_RISK, "options")
check("风险汇总(v50.78): 期权买方 → 占用 = 最大亏损 = 剩余权利金(1500)",
      _rko["positions"] == 1 and abs(_rko["occupied"] - 1500) < 0.01
      and abs(_rko["max_loss"] - 1500) < 0.01 and _rko["max_loss_is_partial"] is False,
      str({k: _rko[k] for k in ("positions", "occupied", "max_loss")}))
check("风险汇总(v50.78): 期权不该出现「未设止损手数」(权利金即最大亏损)",
      _rko["no_stop_lots"] == 0, str(_rko["no_stop_lots"]))

# 全平掉之后应该没有未平仓
trade_import_record({"strategy": _SU_RISK, "underlying": "i", "contract": "i2701", "op_type": "close",
                     "direction": "sell", "close_date": "2026-09-10", "close_qty": 2,
                     "close_price": 720, "pnl": 400, "mode": "futures"})
_rk2 = open_risk_summary(_SU_RISK, "futures")
check("风险汇总(v50.78): 平掉 i 仓后 → 2 笔 / 7 手 / 不再有止损部分",
      _rk2["positions"] == 2 and _rk2["lots"] == 7 and _rk2["max_loss"] == 0.0,
      str({k: _rk2[k] for k in ("positions", "lots", "max_loss")}))

# --- v50.80: 同一合约分两次开仓(中途改过保证金率) → 占用必须逐笔按各自的率算 ---
#   以前是 g["mr"] 一把覆盖(取最后一次开仓的率) → 全部手数都按 22% 算, 占用偏大
_mk_open("rb", "rb2701", 2, 3500, calc='{"margin_rate": 0.22}')
_rk3 = open_risk_summary(_SU_RISK, "futures")
_rb_item = [x for x in _rk3["items"] if x["underlying"] == "rb"][0]
_exp_rb = 3200 * _mrb * 3 * DEFAULT_MARGIN_RATE + 3500 * _mrb * 2 * 0.22
check("风险汇总(v50.80): 同合约两笔开仓率不同 → 占用 = 各笔 价×手数×自己的率 之和",
      abs(_rb_item["occupied"] - round(_exp_rb, 2)) < 0.02,
      'got=%s exp=%s' % (_rb_item["occupied"], round(_exp_rb, 2)))
check("风险汇总(v50.80): 顶层给出「实际用到的保证金率」(不再恒定返回 16%)",
      abs(_rk3["margin_rate"] - DEFAULT_MARGIN_RATE) < 1e-9 and _rk3["margin_rate_custom"] is True
      and [round(x, 4) for x in _rk3["margin_rates"]] == [0.10, 0.16, 0.22],
      str({"rates": _rk3["margin_rates"], "custom": _rk3["margin_rate_custom"]}))
check("风险汇总(v50.80): 明细里带该笔用到的保证金率",
      [round(x, 4) for x in _rb_item["margin_rates"]] == [0.16, 0.22], str(_rb_item["margin_rates"]))
check("风险汇总(v50.80): 没有自定义率时 margin_rate_custom=False(前端才敢写「按 16% 估」)",
      (lambda r: r["margin_rate_custom"] is False)(open_risk_summary("不存在的策略", "futures")),
      str(open_risk_summary("不存在的策略", "futures")["margin_rate_custom"]))

# --- v50.80: 日期字段归一 + 校验(不校验 = 详情页存储型 XSS 的入口) ---
check("日期归一(v50.80): 标准日期原样、单位数补零",
      norm_date_field("2026-10-14") == "2026-10-14" and norm_date_field("2026-1-4") == "2026-01-04", '')
check("日期归一(v50.80): 空 / None → 空串",
      norm_date_field("") == "" and norm_date_field(None) == "" and norm_date_field("   ") == "", '')
check("日期归一(v50.80): 带时间的长串截成日期",
      norm_date_field("2026-10-14T09:30") == "2026-10-14"
      and norm_date_field("2026-10-14 09:30") == "2026-10-14", '')
for _bad_d in ("<img src=x onerror=alert(1)>", "昨天", "2026-13-01", "2026-02-30", "20261014"):
    try:
        norm_date_field(_bad_d)
        _rejected = False
    except ValueError:
        _rejected = True
    check("日期归一(v50.80): 非法串被拒(%s)" % _bad_d[:14], _rejected, '')
try:
    trade_upsert({"underlying": "xss80", "contract": "xss802701", "op_type": "open",
                  "direction": "buy", "open_date": "<b>坏日期</b>", "open_price": 100, "qty": 1})
    _up_rejected = False
except ValueError:
    _up_rejected = True
check("写库(v50.80): trade_upsert 拒绝非法 open_date(脏串进不了库就不会进 innerHTML)",
      _up_rejected and not [r for r in trade_list_records() if r["underlying"] == "xss80"], '')
try:
    trade_import_record({"underlying": "xss80", "contract": "xss802701", "op_type": "open",
                         "direction": "buy", "open_date": "<b>坏日期</b>", "open_price": 100, "qty": 1})
    _im_rejected = False
except ValueError:
    _im_rejected = True
check("写库(v50.80): trade_import_record 同样拒绝(备份导入这条路也堵住了)", _im_rejected, '')
_fail_xss = fund_import_backup({"records": [], "trades": [
    {"underlying": "xss80", "contract": "xss802701", "op_type": "open", "direction": "buy",
     "open_date": "<b>坏日期</b>", "open_price": 100, "qty": 1}]})[2]
check("写库(v50.80): 导入含脏日期的备份 → 记成失败明细(不静默丢, 也不写进去)",
      len(_fail_xss) == 1 and "交易记录" in _fail_xss[0], str(_fail_xss))

# --- v50.80: 交易日历年度提醒(表里没有的年份会静默退化成「只跳周末」) ---
import datetime as _dtapi  # noqa: E402
check("日历提醒(v50.80): 当年在表里 + 10 月(次年安排还没公布) → 不提醒",
      calendar_missing_years() == [], str(calendar_missing_years()))
check("日历提醒(v50.80): 表里覆盖的年份都算「已录」",
      2025 in _main_mod.MARKET_HOLIDAY_YEARS, '')


class _FakeNow(object):
    def __init__(self, y, m):
        self._d = _dtapi.datetime(y, m, 5)

    def now(self):
        return self._d

    def __getattr__(self, k):        # 只替换 now(), 其它属性透传给真 datetime
        return getattr(_dtapi.datetime, k)


_m_real_dt = _main_mod.datetime
try:
    _main_mod.datetime = _FakeNow(2027, 1)       # 当年(2027)表里没有 → 必须提醒
    check("日历提醒(v50.80): 当年不在表里 → 提醒该年份",
          calendar_missing_years() == [2027], str(calendar_missing_years()))
    _main_mod.datetime = _FakeNow(2026, 10)      # 当年有、10 月 → 不提次年
    check("日历提醒(v50.80): 10 月不提前催次年安排",
          calendar_missing_years() == [], str(calendar_missing_years()))
    _main_mod.datetime = _FakeNow(2026, 11)      # 11 月起 → 提醒次年(2027 未录)
    check("日历提醒(v50.80): 11 月起提醒次年安排",
          calendar_missing_years() == [2027], str(calendar_missing_years()))
finally:
    _main_mod.datetime = _m_real_dt
check("日历提醒(v50.80): 恢复真实 datetime(不能污染后面的用例)",
      calendar_missing_years() == [], str(calendar_missing_years()))

# --- ③ 恢复备份前的差异预览 ---
_bk78 = fund_auto_backup("差异预览测试", force=True)
check("备份差异(v50.78): 先生成一份快照", bool(_bk78) and os.path.exists(_bk78), str(_bk78))
_id_extra = _mk_open("extra78", "extra782701", 1, 100)
_d78 = fund_backup_diff(os.path.basename(_bk78))
_tr78 = [t for t in _d78["tables"] if t["key"] == "trade_records"][0]
check("备份差异(v50.78): 备份比现在少 1 条交易记录(delta=−1)", _tr78["delta"] == -1, str(_tr78))
check("备份差异(v50.78): 四张表都给条数(备份/当前/差值)",
      len(_d78["tables"]) == 4 and all(("backup" in t and "current" in t) for t in _d78["tables"]),
      str(_d78["tables"]))
check("备份差异(v50.78): 给出可读差异行(含「交易记录」与「现在」)",
      any("交易记录" in x and "现在" in x for x in _d78["lines"]), str(_d78["lines"]))
check("备份差异(v50.78): 带备份时间与资金曲线跨度字段",
      _d78["mtime"] > 0 and "records_from" in _d78["span"] and "trades_from" in _d78["span"],
      str({k: _d78[k] for k in ("mtime", "span")}))
for _bad78, _msg78 in (("../../funds.db", "非法文件名"), ("abc.db", "非法文件名"), ("funds_不存在78.db", "不存在")):
    try:
        fund_backup_diff(_bad78)
        check("备份差异(v50.78): 拒绝[%s]" % _msg78, False, "未拒绝")
    except ValueError:
        check("备份差异(v50.78): 拒绝[%s]" % _msg78, True)

# ===== v50.80: 接口兜底 + 备份差异的异常类型 =====
print("\n== v50.80 接口兜底 / 异常类型 ==")
import threading as _thr        # noqa: E402
import http.client as _httpc    # noqa: E402
import json as _j80            # noqa: E402
import sqlite3 as _sq80        # noqa: E402

check("异常文案(v50.80): 数据库异常给出人话前缀",
      _main_mod._err_text(_sq80.OperationalError("unable to open database file"))
      == "数据库出错：unable to open database file", '')
check("异常文案(v50.80): ValueError 原样、无消息时退回类名",
      _main_mod._err_text(ValueError("备份不存在：a.db")) == "备份不存在：a.db"
      and _main_mod._err_text(ValueError()) == "ValueError", '')

# 备份差异: 当前库缺失时抛的是 sqlite3.Error 而**不是** ValueError
#   → handler 若只捕 ValueError, 异常会冒到 socketserver 直接断连(前端只看到 Failed to fetch)
#   ⚠ 备份目录是从 FUND_DB_PATH 推出来的, 所以要把备份文件也挪进那个假数据目录才测得到
import shutil as _sh80         # noqa: E402
_saved_dbp80 = _main_mod.FUND_DB_PATH
_fake_dir80 = os.path.join(_TEST_ROOT, "缺失库测试")
os.makedirs(os.path.join(_fake_dir80, "backup"), exist_ok=True)
_sh80.copy2(_bk78, os.path.join(_fake_dir80, "backup", os.path.basename(_bk78)))
_main_mod.FUND_DB_PATH = os.path.join(_fake_dir80, "funds.db")     # ← 这个库不存在
try:
    try:
        fund_backup_diff(os.path.basename(_bk78))
        _raise80 = None
    except Exception as _e80:  # noqa: BLE001
        _raise80 = _e80
finally:
    _main_mod.FUND_DB_PATH = _saved_dbp80
check("备份差异(v50.80): 当前库缺失时抛 sqlite3.Error(所以 handler 必须捕 Exception)",
      isinstance(_raise80, _sq80.Error), repr(_raise80))
check("备份差异(v50.80): 恢复数据目录后接口照旧可用",
      fund_backup_diff(os.path.basename(_bk78))["tables"], '')

# 处理器兜底: 让 _do_GET 抛异常, 用真 socket 发一次请求, 看是不是回 JSON 而不是断连
_srv80 = _main_mod.ThreadingHTTPServer(("127.0.0.1", 0), _main_mod.Handler)
_srv80_port = _srv80.server_address[1]
_thr.Thread(target=_srv80.serve_forever, daemon=True).start()
_saved_do_get80 = _main_mod.Handler._do_GET
_main_mod.Handler._do_GET = lambda self: (_ for _ in ()).throw(RuntimeError("测试用炸弹"))
import contextlib as _ctx80    # noqa: E402
import io as _io80             # noqa: E402
_buf80 = _io80.StringIO()      # 兜底会往 stderr 打堆栈(这是设计), 测试里收起来别刷屏
try:
    with _ctx80.redirect_stderr(_buf80):
        _conn80 = _httpc.HTTPConnection("127.0.0.1", _srv80_port, timeout=8)
        _conn80.request("GET", "/api/version")
        _resp80 = _conn80.getresponse()
        _body80 = _resp80.read().decode("utf-8")
        _code80 = _resp80.status
        _conn80.close()
finally:
    _main_mod.Handler._do_GET = _saved_do_get80
    _srv80.shutdown()
    _srv80.server_close()
check("接口兜底(v50.80): 处理器内部抛异常时回 JSON(不是断连)", _code80 == 200 and "ok" in _body80,
      "%s %s" % (_code80, _body80[:120]))
check("接口兜底(v50.80): 兜底响应带 ok=false 与人话原因",
      _j80.loads(_body80).get("ok") is False and "测试用炸弹" in _j80.loads(_body80).get("error", ""),
      _body80[:160])
check("接口兜底(v50.80): 堆栈仍打到控制台(异常没被吞掉, 只是不炸连接)",
      "测试用炸弹" in _buf80.getvalue(), _buf80.getvalue()[:120])
# 撤销打桩后接口恢复正常
_srv80b = _main_mod.ThreadingHTTPServer(("127.0.0.1", 0), _main_mod.Handler)
_thr.Thread(target=_srv80b.serve_forever, daemon=True).start()
try:
    _c80b = _httpc.HTTPConnection("127.0.0.1", _srv80b.server_address[1], timeout=8)
    _c80b.request("GET", "/api/version")
    _b80b = _j80.loads(_c80b.getresponse().read().decode("utf-8"))
    _c80b.close()
finally:
    _srv80b.shutdown()
    _srv80b.server_close()
check("接口兜底(v50.80): 打桩撤销后 /api/version 正常", _b80b.get("ok") is True and _b80b.get("version"),
      str(_b80b)[:160])

# 清理本段造的数据(按独立策略名删)
for _x in [x for x in trade_list_records() if x["strategy"] == _SU_RISK]:
    trade_delete(_x["id"])
check("v50.78: 测试数据清理干净", len([x for x in trade_list_records() if x["strategy"] == _SU_RISK]) == 0)

# ===== v50.71: 费率表更新到《保证金、手续费、申报费》20261009 =====
print("\n== 手续费费率表 v50.71 ==")
check("费率表(v50.71): 共 91 个品种(含 16 个尚未进合约表的, 先留档)",
      len(FEE_TABLE) == 91, str(len(FEE_TABLE)))
check("费率表(v50.71): 补齐燃料油 fu 的期权费率 → rt0.1/平今0 / 期权1元/手 平今0",
      FEE_TABLE["fu"] == (("rt", 0.1), 0.0, 1.0, 0.0), str(FEE_TABLE["fu"]))
check("费率表(v50.71): 焦炭 j / 焦煤 jm 补上期权费率 0.5元/手",
      FEE_TABLE["j"][2] == 0.5 and FEE_TABLE["jm"][2] == 0.5, str((FEE_TABLE["j"], FEE_TABLE["jm"])))
check("费率表(v50.71): 螺纹 rb 期权费率 2.0 → 1.5元/手",
      FEE_TABLE["rb"] == (("rt", 0.2), None, 1.5, 0.0), str(FEE_TABLE["rb"]))
check("费率表(v50.71): 热卷 hc / 石油沥青 bu / 不锈钢 ss 都补上了期权费率",
      FEE_TABLE["hc"][2] == 2.0 and FEE_TABLE["bu"][2] == 1.0 and FEE_TABLE["ss"][2] == 1.0,
      str((FEE_TABLE["hc"], FEE_TABLE["bu"], FEE_TABLE["ss"])))
check("费率表(v50.71): 玻璃 FG 期货费率按新表改成 2元/手(期权 0.5 不变)",
      FEE_TABLE["FG"] == (2.0, None, 0.5, 0.0), str(FEE_TABLE["FG"]))
check("费率表(v50.71): 碳酸锂 lc 平今 = 万分之3.2(通用基准, 非仅某月)",
      FEE_TABLE["lc"] == (("rt", 0.8), ("rt", 3.2), 3.0, 0.0), str(FEE_TABLE["lc"]))
check("费率表(v50.71): 液化石油气只收通用平今 → pg 平今仍为 None(2605-2606 的 12元/手 属临时加收, 不收录)",
      FEE_TABLE["pg"] == (6.0, None, 1.0, None), str(FEE_TABLE["pg"]))
check("费率表(v50.71): 股指期权 IO/HO/MO 只有期权费率 15元/手(没有对应期货 → 期货费率 None)",
      FEE_TABLE["IO"] == (None, None, 15.0, None) and FEE_TABLE["HO"] == (None, None, 15.0, None)
      and FEE_TABLE["MO"] == (None, None, 15.0, None), str(FEE_TABLE["IO"]))
check("费率表(v50.71): 未进合约表的品种不会误伤(取不到品种 → 0 元, fee_known=False)",
      row_fee(_frow(underlying="ad", open_price=20000, qty=1), "futures", "ex") == 0
      and fee_known(_frow(underlying="ad")) is False, '')
check("手续费(v50.71): 燃料油期权 1元/手 × 2 手 + 0.02 = 2.02",
      abs(row_fee(_frow(mode="options", underlying="fu", contract="fu2612C5200", open_price=120, qty=2),
                  "options", "ex") - 2.02) < 1e-9,
      str(row_fee(_frow(mode="options", underlying="fu", contract="fu2612C5200", open_price=120, qty=2),
                  "options", "ex")))

# ===== v50.74: 平今 / 平昨 手选(有些品种两档费率差很多) =====
print("\n== 平今/平昨 手选 v50.74 ==")
from main import norm_close_today, close_today_str  # noqa: E402
check("平今平昨(v50.74): 归一化 —— ''/null/无法识别 = 自动, 1/'1'/True/平今 = 平今, 0/'0'/False/平昨 = 平昨",
      [norm_close_today(v) for v in ("", None, "1", 1, True, "平今", "today",
                                     "0", 0, False, "平昨", "yesterday", "x", "  ")]
      == [None, None, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, None, None], '')
check("平今平昨(v50.74): 存库字符串统一成 '' / '1' / '0'",
      [close_today_str(v) for v in (None, "", "x", 1, "1", 0, "0")] == ["", "", "", "1", "1", "0", "0"], '')
_cfauto = dict(underlying="CF", contract="CF2701", op_type="close", close_price=15000, close_qty=2,
               close_date="2026-10-10", close_time="10:00", open_date="2026-10-09")
check("平今平昨(v50.74): 不填(自动) 隔日平 → 平昨 4.3×2 + 0.02 = 8.62",
      abs(row_fee(_frow(**_cfauto), "futures", "ex") - 8.62) < 1e-9,
      str(row_fee(_frow(**_cfauto), "futures", "ex")))
check("平今平昨(v50.74): 手选「平今」→ 忽略日期的自动判定(棉花平今 0 → 只 0.02)",
      abs(row_fee(_frow(**dict(_cfauto, close_today="1")), "futures", "ex") - 0.02) < 1e-9,
      str(row_fee(_frow(**dict(_cfauto, close_today="1")), "futures", "ex")))
check("平今平昨(v50.74): 手选「平昨」→ 同一天平也按平昨算 = 8.62",
      abs(row_fee(_frow(**dict(_cfauto, open_date="2026-10-10", close_today="0")), "futures", "ex") - 8.62) < 1e-9,
      str(row_fee(_frow(**dict(_cfauto, open_date="2026-10-10", close_today="0")), "futures", "ex")))
check("平今平昨(v50.74): 交易所没单列平今的品种(比率相同) → 手选也不改变金额(铝 3.01/手)",
      abs(row_fee(_frow(underlying="al", contract="al2611", op_type="close", close_price=20000, close_qty=1,
                        close_date="2026-10-10", open_date="2026-10-10", close_today="0"),
                  "futures", "ex") - 3.01) < 1e-9, '')

# 集成: 落库 → 手选影响费用合计; 编辑不带该字段要保留; 显式传空串回到自动
_u74 = "e2ev74"
trade_upsert({'mode': 'futures', 'underlying': _u74, 'contract': 'CF2701', 'batch': 'Bv74',
              'op_type': 'open', 'direction': 'buy', 'open_date': '2026-10-10', 'open_time': '09:30',
              'open_price': 15000, 'qty': 2, 'premium': 0})
_cid74 = trade_upsert({'mode': 'futures', 'underlying': _u74, 'contract': 'CF2701', 'batch': 'Bv74',
                       'op_type': 'close', 'direction': 'sell', 'close_date': '2026-10-10',
                       'close_time': '14:00', 'close_qty': 2, 'close_price': 15100, 'pnl': 100})
_g74 = [x for x in trade_groups(mode="futures", fee_mode="ex") if x["underlying"] == _u74]
check("平今平昨集成(v50.74): 自动(同交易日)→ 平今, 开 8.62 + 平 0.02 = 8.64",
      _g74 and abs(_g74[0]["fee_total"] - 8.64) < 1e-6, str(_g74 and _g74[0]["fee_total"]))
_cid74 = trade_upsert({'id': _cid74, 'mode': 'futures', 'underlying': _u74, 'contract': 'CF2701',
                       'batch': 'Bv74', 'op_type': 'close', 'direction': 'sell',
                       'close_date': '2026-10-10', 'close_time': '14:00', 'close_qty': 2,
                       'close_price': 15100, 'pnl': 100, 'close_today': '0'}) or _cid74
_g74b = [x for x in trade_groups(mode="futures", fee_mode="ex") if x["underlying"] == _u74]
check("平今平昨集成(v50.74): 改成手选「平昨」→ 平仓那笔变 8.62, 合计 8.62+8.62 = 17.24",
      _g74b and abs(_g74b[0]["fee_total"] - 17.24) < 1e-6, str(_g74b and _g74b[0]["fee_total"]))
_d74 = trade_detail(_u74, mode="futures", batch="Bv74", fee_mode="ex")
_c74 = [o for o in _d74["operations"] if o["op_type"] == "close"][0]
check("平今平昨集成(v50.74): 详情返回 close_today='0' + fee_is_today=False + fee_today_manual=True",
      _c74["close_today"] == "0" and _c74["fee_is_today"] is False and _c74["fee_today_manual"] is True,
      str({k: _c74[k] for k in ("close_today", "fee_is_today", "fee_today_manual")}))
# 编辑时不带 close_today(null) → 保留原值(显式空串才是改回自动)
trade_upsert({'id': _cid74, 'mode': 'futures', 'underlying': _u74, 'contract': 'CF2701', 'batch': 'Bv74',
              'op_type': 'close', 'direction': 'sell', 'close_date': '2026-10-10', 'close_time': '14:00',
              'close_qty': 2, 'close_price': 15100, 'pnl': 120})
_c74b = [o for o in trade_detail(_u74, mode="futures", batch="Bv74", fee_mode="ex")["operations"]
         if o["op_type"] == "close"][0]
check("平今平昨集成(v50.74): 编辑不带该字段 → 保留手选(不被抹成自动)",
      _c74b["close_today"] == "0" and _c74b["pnl"] == 120, str(_c74b["close_today"]))
trade_upsert({'id': _cid74, 'mode': 'futures', 'underlying': _u74, 'contract': 'CF2701', 'batch': 'Bv74',
              'op_type': 'close', 'direction': 'sell', 'close_date': '2026-10-10', 'close_time': '14:00',
              'close_qty': 2, 'close_price': 15100, 'pnl': 120, 'close_today': ''})
_c74c = [o for o in trade_detail(_u74, mode="futures", batch="Bv74", fee_mode="ex")["operations"]
         if o["op_type"] == "close"][0]
check("平今平昨集成(v50.74): 显式传空串 → 改回「自动」(同日 → 平今)",
      _c74c["close_today"] == "" and _c74c["fee_is_today"] is True and _c74c["fee_today_manual"] is False,
      str({k: _c74c[k] for k in ("close_today", "fee_is_today", "fee_today_manual")}))
_e74 = fund_export_backup()
_b74 = [x for x in _e74["trades"] if x.get("underlying") == _u74 and x["op_type"] == "close"]
check("平今平昨(v50.74): 导出备份带上 close_today(不带上 → 恢复后手选档位丢失)",
      _b74 and "close_today" in _b74[0], str(_b74[:1]))
for _o in trade_detail(_u74, mode="futures", batch="Bv74")["operations"]:
    trade_delete(_o["id"])

# 集成: 一组记录(开+平) → fee_total / net_pnl
_fu = "e2efee70"
trade_upsert({'mode': 'futures', 'underlying': _fu, 'contract': 'al2612', 'batch': 'Bfee70',
              'op_type': 'open', 'direction': 'buy', 'open_date': '2026-10-10', 'open_time': '10:00',
              'open_price': 20000, 'qty': 2, 'premium': 0})
trade_upsert({'mode': 'futures', 'underlying': _fu, 'contract': 'al2612', 'batch': 'Bfee70',
              'op_type': 'close', 'direction': 'sell', 'close_date': '2026-10-10', 'close_time': '14:00',
              'close_qty': 2, 'close_price': 20100, 'pnl': 1000})
_g = [x for x in trade_groups(mode="futures", fee_mode="ex") if x["underlying"] == _fu]
check("手续费合计(v50.70): 铝 开2手+平2手 = 3.01×2 + 3.01×2 = 12.04",
      _g and abs(_g[0]["fee_total"] - 12.04) < 1e-6, str(_g and _g[0]["fee_total"]))
check("净盈亏(v50.70): = 平仓盈亏 1000 − 手续费 12.04 = 987.96",
      _g and abs(_g[0]["net_pnl"] - 987.96) < 1e-6, str(_g and _g[0]["net_pnl"]))
check("手续费口径(v50.70): 切成「不计算」→ 手续费 0 且净盈亏 = 平仓盈亏",
      (lambda gg: bool(gg) and gg[0]["fee_total"] == 0 and abs(gg[0]["net_pnl"] - 1000) < 1e-6)(
          [x for x in trade_groups(mode="futures", fee_mode="off") if x["underlying"] == _fu]), '')
for _o in trade_detail(_fu, mode="futures", batch="Bfee70")["operations"]:
    trade_delete(_o["id"])
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

print("\n== 期权模式 (v50.83: 开仓价 = 期权报价; 每手权利金 = 报价 × 乘数; 手数 = 预算 / 每手权利金) ==")
# 用户实测那组: 棕榈油 p 乘数 10, 报价 72 → 每手 720; 权益 12 万 x 3% = 3600 → 5 手
_op = calc_options({"equity": 120000, "code": "p", "entry": 72})
check("开仓金额统一=权益x3% = 3600", abs(_op["budget"] - 3600) < 0.01, str(_op["budget"]))
check("每手权利金 = 报价 72 × 乘数 10 = 720", abs(_op["premium_per_lot"] - 720) < 1e-9, str(_op["premium_per_lot"]))
check("最大手数 = 3600//720 = 5", _op["max_lots"] == 5, str(_op["max_lots"]))
check("占用资金 = 5x720 = 3600", abs(_op["funds_used"] - 3600) < 0.01, str(_op["funds_used"]))
check("返回开仓价(报价)原值 72", abs(_op["entry_price"] - 72) < 1e-9, str(_op.get("entry_price")))
check("enough_lots = True", _op["enough_lots"] is True)
check("risk_ratio = 3%", abs(_op["risk_ratio"] - 0.03) < 1e-9, str(_op["risk_ratio"]))

# 换乘数: 玻璃 fg 乘数 20, 报价 63 → 每手 1260(权益 10 万 x 3% = 3000 → 2 手)
_ofg = calc_options({"equity": 100000, "code": "fg", "entry": 63})
check("每手权利金 = 报价 63 × 乘数 20 = 1260", abs(_ofg["premium_per_lot"] - 1260) < 1e-9, str(_ofg["premium_per_lot"]))
check("最大手数 = 3000//1260 = 2", _ofg["max_lots"] == 2, str(_ofg["max_lots"]))

# 期权不足1手: 权益2万 -> 预算600 < 每手 8000(m 乘数 10)
o4 = calc_options({"equity": 20000, "code": "m", "entry": 800})
check("期权不足1手 -> 不开仓", o4["max_lots"] == 0 and o4["enough_lots"] is False, str(o4))

# 自定义期权风险百分比: 1% → 权益100万x1%=10000, 每手 8000(m 报价 800) → 1 手
o5 = calc_options({"equity": 1000000, "code": "m", "entry": 800, "risk_percent": 1})
check("期权 risk_percent=1 预算=10000", abs(o5["budget"] - 10000) < 0.01, str(o5["budget"]))
check("期权 risk_percent=1 手数=10000//8000=1", o5["max_lots"] == 1, str(o5["max_lots"]))
check("期权 risk_percent 字段返回", abs(o5["risk_percent"] - 1.0) < 0.01, str(o5.get("risk_percent")))
o05 = calc_options({"equity": 1000000, "code": "m", "entry": 80, "risk_percent": 0.5})   # 每手 800
check("期权 0.5% → 5000 → 5000//800=6 手", abs(o05["budget"] - 5000) < 0.01 and o05["max_lots"] == 6, str(o05))
# v50.83 新增档位 4% / 5% 也要能算
_o4p = calc_options({"equity": 120000, "code": "p", "entry": 72, "risk_percent": 4})
_o5p = calc_options({"equity": 120000, "code": "p", "entry": 72, "risk_percent": 5})
check("期权 4% → 预算 4800 → 6 手", abs(_o4p["budget"] - 4800) < 0.01 and _o4p["max_lots"] == 6, str(_o4p["max_lots"]))
check("期权 5% → 预算 6000 → 8 手", abs(_o5p["budget"] - 6000) < 0.01 and _o5p["max_lots"] == 8, str(_o5p["max_lots"]))
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
fund_upsert("期权买方", 2025, 11, 100000, 105000, 5000, "11月", cash=12000)
r = fund_list_records("期权买方")[0]
check("月初=100000", r["initial_equity"] == 100000, str(r))
check("月末=105000", r["end_equity"] == 105000, str(r))
check("出金=5000(正)", r["cash_flow"] == 5000, str(r))
check("本月现金=12000(自定义)", r["cash"] == 12000, str(r))
check("月盈亏 = 月末-月初+出金 = 10000", r["monthly_pnl"] == 10000, str(r))
check("月收益率 = 10000/100000 = 10%", abs(r["month_return_rate"] - 0.10) < 1e-9, str(r))

# 月度更新 (upsert)
fund_upsert("期权买方", 2025, 11, 100000, 110000, 5000, "11月改")
r = fund_list_records("期权买方")[0]
check("upsert 后更新(覆盖)", len(fund_list_records("期权买方")) == 1 and r["end_equity"] == 110000, str(r))
check("upsert 后重新计算月盈亏", r["monthly_pnl"] == 15000, str(r))

# 负出入金(=入金)
fund_upsert("期权买方", 2025, 12, 110000, 105000, -10000, "12月入金")
r12 = [x for x in fund_list_records("期权买方") if x["month"] == 12][0]
check("入金-10000(出金为正)", r12["cash_flow"] == -10000)
check("月盈亏 = 105000-110000+(-10000) = -15000", r12["monthly_pnl"] == -15000)

# 年度汇总
y = fund_yearly_summary("期权买方")
check("只有 2025 这 1 个年度", len(y) == 1 and y[0]["year"] == 2025)
y0 = y[0]
check("年初 = 1月月初 = 100000", y0["initial_equity"] == 100000)
check("年末 = 12月月末 = 105000", y0["end_equity"] == 105000)
check("总出入金 = 5000+(-10000) = -5000", y0["total_cash_flow"] == -5000)
check("年度盈亏 = 105000 - 100000 + (-5000) = 0", y0["yearly_pnl"] == 0)
check("年化收益率 = 0/100000 = 0", abs(y0["annualized_return_rate"]) < 1e-9)

# 跨年汇总
fund_upsert("期权买方", 2026, 1, 105000, 120000, 0, "")
y2 = fund_yearly_summary("期权买方")
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
check("汇总包含两个策略", set(c_jan["strategies"]) == {"期权买方", "威科夫"})

# 汇总缺失延续测试: abe 8月月末延续到 9月 (用户截图 case)
# 清空重置场景: abe 8月(60000->70000)、威科夫 8月(50000->60000)、威科夫 9月(60000->50000)
_conn = sqlite3.connect(_main_mod.FUND_DB_PATH)
_conn.execute("DELETE FROM records"); _conn.commit(); _conn.close()
fund_upsert("期权买方", 2025, 8, 60000, 70000, 1000, "")
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
check("汇总9月: 参与=[威科夫], 延续=[abe]", set(c_sep["strategies"]) == {"威科夫"} and "期权买方" in c_sep["carried_strategies"])

# 删除 (延续测试后数据库中 abe 只有 2025/8 一条)
fund_delete("期权买方", 2025, 8)
abe_after = [x for x in fund_list_records("期权买方") if x["year"] == 2025 and x["month"] == 8]
check("删除成功", len(abe_after) == 0)
check("abe 剩 0 条", len(fund_list_records("期权买方")) == 0)

# 错误策略
try:
    fund_upsert("未知", 2026, 1, 0, 0, 0, "")
    check("未知策略被拒绝", False)
except ValueError:
    check("未知策略被拒绝", True)

# 错误月份
try:
    fund_upsert("期权买方", 2026, 13, 0, 0, 0, "")
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
fund_upsert("期权买方", 2025, 8, 60000, 70000, 1000, "8月")
fund_upsert("威科夫", 2025, 9, 60000, 50000, 0, "9月")

backup = fund_export_backup()
check("导出包含 records 数组", isinstance(backup.get("records"), list) and len(backup["records"]) == 2)
check("导出含版本/时间戳", "backup_version" in backup and "exported_at" in backup)

# 清空再导入 (模拟换电脑)
_conn3 = sqlite3.connect(_main_mod.FUND_DB_PATH)
for t in ("records", "trade_records", "trade_pool_snapshots"):
    _conn3.execute(f"DELETE FROM {t}")
_conn3.commit(); _conn3.close()
n, strategies, _f1 = fund_import_backup(backup)
check("导入条数 = 2", n == 2)
check("导入策略 = [abe, 威科夫]", set(strategies) == {"期权买方", "威科夫"})
restored = fund_list_records()
check("导入后数据完整", len(restored) == 2 and any(r["strategy"] == "期权买方" and r["end_equity"] == 70000 for r in restored), str([(r["strategy"], r["year"], r["month"], r["end_equity"]) for r in restored]))
check("导入重算盈亏(abe 8月=70000-60000+1000=11000)", [r for r in restored if r["strategy"]=="期权买方"][0]["monthly_pnl"] == 11000)

# 合并导入: 已有数据时导入新增
fund_upsert("威科夫", 2025, 10, 50000, 55000, 0, "10月")
n2, _f2s, _f2 = fund_import_backup(backup)
check("合并导入不丢已有记录(3条)", len(fund_list_records()) == 3)

# 非法格式拒绝
try:
    fund_import_backup({"foo": 1})
    check("无 records 字段被拒绝", False)
except ValueError:
    check("无 records 字段被拒绝", True)
try:
    fund_import_backup({"records": [{"strategy": "期权买方"}]})
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
n3, _f3s, _f3 = fund_import_backup(bk2)
check("导入含三部分(资金3+交易2+池1=6)", n3 == 6, "实际 %s" % n3)
_trl = trade_list_records()
check("交易记录完整恢复", len(_trl) == 2 and _trl[0]["contract"] == "bk1C5000", str(len(_trl)))
check("平仓盈亏恢复", [x for x in _trl if x["op_type"] == "close"][0]["pnl"] == 100)
check("监控池恢复", len(trade_pool_list()) == 1 and trade_pool_list()[0]["contracts"][0]["contract"] == "bk1C5000")
check("资金曲线一并恢复", len(fund_list_records()) == 3, str(len(fund_list_records())))
# 幂等: 再导入一次不应翻倍
n4, _f4s, _f4 = fund_import_backup(bk2)
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
    trade_upsert({'strategy':'期权买方','underlying':'fu2611','contract':'fu2611C3000',
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

# 9b. v50.45: 编辑开仓记录不能把测算快照弄丢 + 持仓带 underlying
print("\n== 测算快照保留 (v50.45) ==")
import json as _json
_snap = _json.dumps({'code':'cu','name':'沪铜','dir':'long','budget':1350,'lots':3,'pl_ratio':2.96})
_sid = trade_upsert({'underlying':'snapu','contract':'cu2610','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':78000,'qty':3,'premium':0,
                     'mode':'futures','calc_json':_snap})
_sd = trade_detail('snapu', mode='futures')
check("开仓记录带回测算快照", bool(_sd['operations'][0].get('calc_json')))
# ⚠ 前端「修改开仓」不会传 calc_json: 以前会把它 UPDATE 成空 → 详情顶部测算卡整块消失
trade_upsert({'id':_sid,'underlying':'snapu','contract':'cu2612','op_type':'open','direction':'buy',
              'open_date':'2026-09-01','open_price':78000,'qty':3,'premium':0,'mode':'futures'})
_sd2 = trade_detail('snapu', mode='futures')
_op2 = [o for o in _sd2['operations'] if o['id'] == _sid][0]
check("改合约号后测算快照仍在", _op2.get('calc_json') == _snap, str(_op2.get('calc_json'))[:60])
check("改合约号确实生效", _op2['contract'] == 'cu2612', _op2['contract'])
# 显式传空串才允许清空
trade_upsert({'id':_sid,'underlying':'snapu','contract':'cu2612','op_type':'open','direction':'buy',
              'open_date':'2026-09-01','open_price':78000,'qty':3,'premium':0,'mode':'futures',
              'calc_json':''})
_sd3 = trade_detail('snapu', mode='futures')
check("显式传空可清空快照", not [o for o in _sd3['operations'] if o['id'] == _sid][0].get('calc_json'))
trade_delete(_sid)
# 持仓带 underlying(v50.45): 前端要在持仓表里显示中文标的
_hid = trade_upsert({'underlying':'snapu','contract':'cu2610','op_type':'open','direction':'buy',
                     'open_date':'2026-09-01','open_price':78000,'qty':2,'premium':0,'mode':'futures'})
_hd = trade_detail('snapu', mode='futures')
check("持仓项带 underlying 字段", bool(_hd['holdings']) and _hd['holdings'][0].get('underlying') == 'snapu',
      str(_hd['holdings'][:1]))
trade_delete(_hid)

# 9b2. 批次号 (v50.47): 同品种不同批次 → 主页各自成一条记录, 详情/平仓都限定在同批次
print("\n== 批次号分组 (v50.47) ==")
_bu = 'batchu'
_b1, _b2 = 'B20260915010101aa', 'B20260915020202bb'
_i1 = trade_upsert({'underlying': _bu, 'contract': 'cu2610', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-01', 'open_price': 78000, 'qty': 2, 'premium': 0,
                    'mode': 'futures', 'batch': _b1})
_i2 = trade_upsert({'underlying': _bu, 'contract': 'cu2610', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-02', 'open_price': 78100, 'qty': 3, 'premium': 0,
                    'mode': 'futures', 'batch': _b2})
_gs = trade_groups(mode='futures')
_gm = [g for g in _gs if g['underlying'] == _bu]
check("同品种两个批次 → 主页 2 条记录", len(_gm) == 2, str([(g['batch'], g['open_date']) for g in _gm]))
check("分组带 batch 字段", sorted(g['batch'] for g in _gm) == sorted([_b1, _b2]), str(_gm))
_d1 = trade_detail(_bu, mode='futures', batch=_b1)
check("详情按批次过滤(只看到本批 1 条开仓)", len(_d1['operations']) == 1 and _d1['operations'][0]['qty'] == 2,
      str(_d1['operations']))
check("详情返回 batch", _d1.get('batch') == _b1, str(_d1.get('batch')))
_d2 = trade_detail(_bu, mode='futures', batch=_b2)
check("另一批次详情独立", len(_d2['operations']) == 1 and _d2['operations'][0]['qty'] == 3, str(_d2['operations']))
# 平仓不能跨批次占用: 批次2 只开了 3 手, 平 5 手应被拒(不限批次的话会被允许)
try:
    trade_upsert({'underlying': _bu, 'contract': 'cu2610', 'op_type': 'close', 'direction': 'sell',
                  'close_date': '2026-09-03', 'close_qty': 5, 'close_price': 78200, 'pnl': 100,
                  'mode': 'futures', 'batch': _b2})
    check("平仓数量不跨批次(超本批可平被拒)", False, "未拒绝")
except ValueError as _e:
    check("平仓数量不跨批次(超本批可平被拒)", "超过剩余可平" in str(_e), str(_e))
_ci = trade_upsert({'underlying': _bu, 'contract': 'cu2610', 'op_type': 'close', 'direction': 'sell',
                    'close_date': '2026-09-03', 'close_qty': 3, 'close_price': 78200, 'pnl': 300,
                    'mode': 'futures', 'batch': _b2})
check("本批次内平仓正常", trade_detail(_bu, mode='futures', batch=_b1)['operations'][0]['qty'] == 2,
      "批次1 不该被批次2 的平仓影响")
# 无批次的老数据仍按标的合并(向后兼容)
_i3 = trade_upsert({'underlying': _bu, 'contract': 'cu2612', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-04', 'open_price': 78200, 'qty': 1, 'premium': 0, 'mode': 'futures'})
_i4 = trade_upsert({'underlying': _bu, 'contract': 'cu2612', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-05', 'open_price': 78300, 'qty': 1, 'premium': 0, 'mode': 'futures'})
_gm2 = [g for g in trade_groups(mode='futures') if g['underlying'] == _bu and not g['batch']]
check("无批次记录仍合并成 1 条(老行为不变)", len(_gm2) == 1, str(_gm2))
check("空批次详情能看到两条开仓", len(trade_detail(_bu, mode='futures')['operations']) == 2,
      str(len(trade_detail(_bu, mode='futures')['operations'])))
# 导入/导出要带上批次(否则备份恢复后又被合并)
from main import trade_import_record
_im = trade_import_record({'id': 99001, 'underlying': _bu, 'contract': 'cu2610', 'op_type': 'open',
                           'direction': 'buy', 'open_date': '2026-09-06', 'open_price': 78400,
                           'qty': 1, 'premium': 0, 'mode': 'futures', 'batch': _b1,
                           'calc_json': '{"lots":1}', 'init_stop': 77800, 'init_target': 79000})
_imp = [o for o in trade_detail(_bu, mode='futures', batch=_b1)['operations'] if o['id'] == 99001]
check("导入保留批次号", bool(_imp) and _imp[0]['batch'] == _b1, str(_imp[:1]))
check("导入保留 mode/止损/测算快照(v50.47 修)", bool(_imp) and _imp[0]['mode'] == 'futures'
      and _imp[0]['init_stop'] == 77800 and _imp[0]['calc_json'] == '{"lots":1}', str(_imp[:1]))
for _x in (_i1, _i2, _ci, _i3, _i4, 99001):
    trade_delete(_x)

# 9b3. v50.50: 平仓校验按模式隔离 + 监控池/复盘能完整进备份
print("\n== 模式隔离 & 备份完整性 (v50.50) ==")
from main import (fund_export_backup, trade_pool_upsert, trade_pool_list,
                  trade_review_upsert, trade_review_list, trade_review_delete,
                  trade_pool_delete, trade_review_import_record, trade_pool_import_record)
_mu = 'modeu'
_o1 = trade_upsert({'underlying': _mu, 'contract': 'cu2610', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-01', 'open_price': 78000, 'qty': 2, 'premium': 0})   # options
_f1 = trade_upsert({'underlying': _mu, 'contract': 'cu2610', 'op_type': 'open', 'direction': 'buy',
                    'open_date': '2026-09-01', 'open_price': 78000, 'qty': 5, 'premium': 0, 'mode': 'futures'})
# options 只开了 2 手 → 平 5 手必须被拒(修复前 SQL 不带 mode, 会算上期货那 5 手 → 误放行)
try:
    trade_upsert({'underlying': _mu, 'contract': 'cu2610', 'op_type': 'close', 'direction': 'sell',
                  'close_date': '2026-09-02', 'close_qty': 5, 'close_price': 78100, 'pnl': 10})
    check("平仓校验按模式隔离(options 不能平期货开的量)", False, "未拒绝")
except ValueError as _e:
    check("平仓校验按模式隔离(options 不能平期货开的量)", "超过剩余可平" in str(_e), str(_e))
_cf = trade_upsert({'underlying': _mu, 'contract': 'cu2610', 'op_type': 'close', 'direction': 'sell',
                    'close_date': '2026-09-02', 'close_qty': 5, 'close_price': 78100, 'pnl': 50,
                    'mode': 'futures'})
check("期货模式平 5 手正常", bool(_cf), str(_cf))
# 监控池: 期货模式必须落在 futures(之前前端不传 mode → 全进 options)
_pf = trade_pool_upsert({'contracts': ['cu'], 'mode': 'futures'})
_po = trade_pool_upsert({'contracts': ['ao'], 'mode': 'options'})
check("期货监控池落在 futures 模式", any(x['id'] == _pf for x in trade_pool_list(mode='futures')))
check("期货监控池不出现在 options 模式", not any(x['id'] == _pf for x in trade_pool_list(mode='options')))
check("期权监控池不出现在 futures 模式", not any(x['id'] == _po for x in trade_pool_list(mode='futures')))
# 导出: 两个模式的池都要在 + 带 mode 字段 + 含复盘笔记
_rv = trade_review_upsert({'mode': 'futures', 'underlying': _mu, 'review_at': '2026-09-16T10:00',
                           'content': 'v50.50 复盘导入测试'})
_exp = fund_export_backup()
check("导出 backup_version=5(v50.70 含开/平仓时间)", _exp.get('backup_version') == 5, str(_exp.get('backup_version')))
check("导出含 futures 监控池(之前只导 options)", any(x['id'] == _pf for x in _exp['trade_pools']),
      str([(x['id'], x.get('mode')) for x in _exp['trade_pools']]))
check("导出监控池带 mode 字段", all('mode' in x for x in _exp['trade_pools']))
check("导出含 trade_reviews", isinstance(_exp.get('trade_reviews'), list)
      and any(x['id'] == _rv for x in _exp['trade_reviews']))
# 导入: 池与复盘的 mode 都要保留
trade_pool_delete(_pf)
trade_pool_import_record({'id': _pf, 'snapshot_date': '2026-09-16', 'contracts': ['cu'],
                          'note': '', 'mode': 'futures'})
check("监控池导入保留 futures 模式", any(x['id'] == _pf for x in trade_pool_list(mode='futures')))
check("监控池导入后不在 options", not any(x['id'] == _pf for x in trade_pool_list(mode='options')))
trade_review_delete(_rv)
trade_review_import_record({'id': _rv, 'mode': 'futures', 'underlying': _mu,
                            'review_at': '2026-09-16T10:00', 'content': 'v50.50 复盘导入测试'})
_rb = [x for x in trade_review_list('futures') if x['id'] == _rv]
check("复盘导入保留 id 与模式", bool(_rb) and _rb[0]['mode'] == 'futures', str(_rb[:1]))
check("复盘重复导入幂等(不翻倍)",
      len([x for x in trade_review_list('futures') if x['id'] == _rv]) == 1)
trade_review_delete(_rv)
trade_pool_delete(_pf)
trade_pool_delete(_po)
for _x in (_o1, _f1, _cf):
    trade_delete(_x)

# 9b4. v50.51: 详情页的 方向/开仓时间/状态/平仓盈亏/平仓时间 必须按批次自己算
#      (前端原先去主表 groups 里按 underlying 找 → 同品种多条时会捞到别条的平仓收益)
print("\n== 详情页按批次独立 (v50.51) ==")
_du = 'detu'
_da, _db = 'B20260910000001aa', 'B20260916000002bb'
_do1 = trade_upsert({'underlying': _du, 'contract': 'sr2611', 'op_type': 'open', 'direction': 'buy',
                     'open_date': '2026-09-10', 'open_price': 5400, 'qty': 5, 'premium': 0,
                     'mode': 'futures', 'batch': _da})
_dc1 = trade_upsert({'underlying': _du, 'contract': 'sr2611', 'op_type': 'close', 'direction': 'sell',
                     'close_date': '2026-09-12', 'close_qty': 2, 'close_price': 5600, 'pnl': 2000,
                     'mode': 'futures', 'batch': _da})
_do2 = trade_upsert({'underlying': _du, 'contract': 'sr2611', 'op_type': 'open', 'direction': 'buy',
                     'open_date': '2026-09-16', 'open_price': 5500, 'qty': 5, 'premium': 0,
                     'mode': 'futures', 'batch': _db})
_dA = trade_detail(_du, mode='futures', batch=_da)
_dB = trade_detail(_du, mode='futures', batch=_db)
check("已部分平仓那条: 盈亏 2000 + 部分平仓",
      _dA['total_pnl'] == 2000 and _dA['close_status'] == '部分平仓',
      str({k: _dA[k] for k in ('total_pnl', 'close_status', 'open_date', 'last_close_date')}))
check("新开那条: 盈亏 0 + 未平仓(不串上一条的收益)",
      _dB['total_pnl'] == 0 and _dB['close_status'] == '未平仓',
      str({k: _dB[k] for k in ('total_pnl', 'close_status', 'open_date', 'last_close_date')}))
check("两批次开仓时间各自独立",
      _dA['open_date'] == '2026-09-10' and _dB['open_date'] == '2026-09-16',
      str([_dA['open_date'], _dB['open_date']]))
check("两批次平仓时间各自独立(未平的那条为空)",
      _dA['last_close_date'] == '2026-09-12' and _dB['last_close_date'] == '',
      str([_dA['last_close_date'], _dB['last_close_date']]))
check("两批次方向都取自本批次", _dA['direction'] == 'buy' and _dB['direction'] == 'buy',
      str([_dA['direction'], _dB['direction']]))
check("两批次持仓手数各自独立(3 vs 5)",
      sum(h['qty'] for h in _dA['holdings']) == 3 and sum(h['qty'] for h in _dB['holdings']) == 5,
      str([sum(h['qty'] for h in _dA['holdings']), sum(h['qty'] for h in _dB['holdings'])]))
for _x in (_do1, _dc1, _do2):
    trade_delete(_x)

# 9b5. v50.52: 复盘笔记按 (标的, 批次) 归属, 不再按品种共享
print("\n== 复盘按记录独立 (v50.52) ==")
from main import (trade_review_upsert, trade_review_list, trade_review_delete,
                  trade_review_import_record)
_ru = 'rvu'
_rb1, _rb2 = 'B20260920000001aa', 'B20260920000002bb'
_r1 = trade_review_upsert({'mode': 'futures', 'underlying': _ru, 'batch': _rb1,
                           'review_at': '2026-09-20T10:00', 'content': '第一批的复盘'})
_r2 = trade_review_upsert({'mode': 'futures', 'underlying': _ru, 'batch': _rb2,
                           'review_at': '2026-09-20T11:00', 'content': '第二批的复盘'})
check("两批次的复盘各自只看到自己那条",
      [x['content'] for x in trade_review_list('futures', _ru, _rb1)] == ['第一批的复盘']
      and [x['content'] for x in trade_review_list('futures', _ru, _rb2)] == ['第二批的复盘'],
      str([x['content'] for x in trade_review_list('futures', _ru, _rb1)],
          ) + ' / ' + str([x['content'] for x in trade_review_list('futures', _ru, _rb2)]))
check("复盘带 batch 字段返回",
      all(x['batch'] in (_rb1, _rb2) for x in trade_review_list('futures', _ru, _rb1)), '')
# 不传 batch = 该标的全部(导出备份用), 条数不能少
check("不带批次查=该标的全部(备份导出用)",
      len(trade_review_list('futures', _ru)) == 2, str(len(trade_review_list('futures', _ru))))
# 编辑内容不改归属: 只传 id + content(前端也可能漏传 batch)
trade_review_upsert({'id': _r1, 'mode': 'futures', 'underlying': _ru,
                     'review_at': '2026-09-20T10:00', 'content': '第一批的复盘(改)'})
check("编辑复盘不丢批次归属",
      [x['content'] for x in trade_review_list('futures', _ru, _rb1)] == ['第一批的复盘(改)'], '')
# 备份导入保留 batch
trade_review_delete(_r2)
trade_review_import_record({'id': _r2, 'mode': 'futures', 'underlying': _ru, 'batch': _rb2,
                            'review_at': '2026-09-20T11:00', 'content': '第二批的复盘'})
check("复盘导入保留批次归属",
      [x['content'] for x in trade_review_list('futures', _ru, _rb2)] == ['第二批的复盘'], '')
check("导入后两个批次仍是各自一条(没并到一起)",
      len([x for x in trade_review_list('futures', _ru) if x['batch'] == _rb1]) == 1
      and len([x for x in trade_review_list('futures', _ru) if x['batch'] == _rb2]) == 1, '')
trade_review_delete(_r1)
trade_review_delete(_r2)

# 9b6. v50.57: 期权也能写复盘, 且与期货完全隔离(同标的同批次也不串)
print("\n== 期权复盘与期货隔离 (v50.57) ==")
from main import (trade_review_upsert, trade_review_list, trade_review_delete)
_ou = "e2eopt_rv"
_ob = "20260920120000ab"
_of = trade_review_upsert({'mode': 'options', 'underlying': _ou, 'batch': _ob,
                           'review_at': '2026-09-20T12:00', 'content': '期权这条的复盘'})
_ff = trade_review_upsert({'mode': 'futures', 'underlying': _ou, 'batch': _ob,
                           'review_at': '2026-09-20T12:30', 'content': '期货这条的复盘'})
check("期权复盘能写入并读回",
      [x['content'] for x in trade_review_list('options', _ou, _ob)] == ['期权这条的复盘'],
      str([x['content'] for x in trade_review_list('options', _ou, _ob)]))
check("同标的同批次: 期货复盘不会被期权读到",
      [x['content'] for x in trade_review_list('futures', _ou, _ob)] == ['期货这条的复盘'], '')
check("复盘列表按模式过滤(两边各 1 条, 不混)",
      len(trade_review_list('options', _ou)) == 1 and len(trade_review_list('futures', _ou)) == 1,
      '%d / %d' % (len(trade_review_list('options', _ou)), len(trade_review_list('futures', _ou))))
_exp2 = fund_export_backup()
_ids = [x['id'] for x in _exp2['trade_reviews']]
check("导出同时含期权与期货复盘", _of in _ids and _ff in _ids, str(_ids))
trade_review_delete(_of)
trade_review_import_record({'id': _of, 'mode': 'options', 'underlying': _ou, 'batch': _ob,
                            'review_at': '2026-09-20T12:00', 'content': '期权这条的复盘'})
check("期权复盘导入保留 mode=options",
      [x['mode'] for x in trade_review_list('options', _ou, _ob)] == ['options'],
      str(trade_review_list('options', _ou, _ob)))
trade_review_delete(_of)
trade_review_delete(_ff)

# 9b7. v50.59: 策略改名 abe → 期权买方 + 期权双买策略 + 双买测算
print("\n== 策略改名与期权双买 (v50.59) ==")
from main import (norm_strategy, calc_dual, FUND_STRATEGIES, STRATEGY_OPTIONS_BUY,
                  STRATEGY_DUAL_BUY, TRADE_MODES)

check("策略列表 = 期权买方 / 威科夫 / 期权双买",
      FUND_STRATEGIES == ["期权买方", "威科夫", "期权双买"], str(FUND_STRATEGIES))
check("旧名 abe 自动归一为「期权买方」", norm_strategy("abe") == STRATEGY_OPTIONS_BUY)
check("未知策略名原样返回(调用方再判)", norm_strategy("随便") == "随便")
check("交易模式三值", TRADE_MODES == ("options", "dual", "futures"), str(TRADE_MODES))

# 老库里的 abe 记录: 迁移在 _fund_db() 初始化时完成 → 现在应查得到「期权买方」
_old = fund_list_records("abe")          # 兼容: 传旧名也能查到(内部归一)
check("传旧名也能查到记录(向后兼容)", isinstance(_old, list), str(len(_old)))

# 老备份导入: strategy 写 abe 的条目要落到「期权买方」
_n = fund_import_backup({"records": [{"strategy": "abe", "year": 2031, "month": 1,
                                      "initial_equity": 1000, "end_equity": 1100}]})
_r = [x for x in fund_list_records("期权买方") if x["year"] == 2031 and x["month"] == 1]
check("老备份(abe) 导入后落在「期权买方」下",
      len(_r) == 1 and _r[0]["strategy"] == STRATEGY_OPTIONS_BUY, str(_r[:1]))
fund_delete(STRATEGY_OPTIONS_BUY, 2031, 1)

# 双买算法: 对称两腿
_d1 = calc_dual({'equity': 900000, 'code': 'lc', 'month': '2611', 'risk_percent': 3,
                 'call_strike': 144000, 'call_delta': 0.30, 'call_premium': 9800,
                 'put_strike': 120000, 'put_delta': -0.30, 'put_premium': 8500})
check("双买: 预算 = 权益×3% = 27000", _d1["budget"] == 27000, str(_d1["budget"]))
check("双买: 对称 delta → 两腿手数相同", _d1["call"]["qty"] == _d1["put"]["qty"] == 1,
      "%s / %s" % (_d1["call"]["qty"], _d1["put"]["qty"]))
check("双买: 净 delta ≈ 0", abs(_d1["net_delta"]) < 1e-9, str(_d1["net_delta"]))
check("双买: 权利金合计 = 9800+8500", _d1["total_premium"] == 18300, str(_d1["total_premium"]))
check("双买: 不超预算", _d1["total_premium"] <= _d1["budget"], '')
check("双买: 合约代码带月份与 C/P",
      _d1["call"]["contract_code"] == "lc2611-C-144000"
      and _d1["put"]["contract_code"] == "lc2611-P-120000",
      _d1["call"]["contract_code"] + ' / ' + _d1["put"]["contract_code"])
# 止盈目标: 2 倍权利金 → 单腿每手目标价 = 2×合计 ÷ 该腿手数
_t2 = _d1["targets"][0]
check("双买: 2 倍目标 = 2×总权利金", _t2["n"] == 2 and _t2["value"] == 36600, str(_t2))
check("双买: call 每手目标价 = 2×合计 ÷ call 手数", _t2["call_price"] == 36600, str(_t2["call_price"]))
check("双买: put 每手目标价 = 2×合计 ÷ put 手数", _t2["put_price"] == 36600, str(_t2["put_price"]))
check("双买: 输出 2/3/4/5 四档", [t["n"] for t in _d1["targets"]] == [2, 3, 4, 5], '')

# v50.60: 期权价格口径 —— 单价 = 每手权利金 ÷ 期权乘数; 止盈目标价按同一口径给出
check("双买: 每手权利金换算成期权价格(lc 乘数 1)",
      _d1["call"]["price_per_unit"] == 9800 and _d1["put"]["price_per_unit"] == 8500,
      "%s / %s" % (_d1["call"]["price_per_unit"], _d1["put"]["price_per_unit"]))
check("双买: 目标价按期权价格口径输出",
      _t2["call_price_unit"] == 36600 and _t2["put_price_unit"] == 36600,
      "%s / %s" % (_t2["call_price_unit"], _t2["put_price_unit"]))
_d4 = calc_dual({'equity': 1000000, 'code': 'cu', 'month': '2611', 'risk_percent': 3,
                 'call_strike': 72000, 'call_delta': 0.3, 'call_premium': 2000,
                 'put_strike': 68000, 'put_delta': -0.3, 'put_premium': 1800})
_m4 = _d4["opt_mult"]
check("双买: 乘数≠1 时单价 = 每手权利金 ÷ 期权乘数(cu 乘数 %s)" % _m4,
      _m4 > 1 and abs(_d4["call"]["price_per_unit"] - 2000.0 / _m4) < 1e-6
      and abs(_d4["put"]["price_per_unit"] - 1800.0 / _m4) < 1e-6,
      "call %s / put %s" % (_d4["call"]["price_per_unit"], _d4["put"]["price_per_unit"]))
check("双买: 目标价 × 乘数 = 该腿每手目标权利金",
      abs(_d4["targets"][0]["call_price_unit"] * _m4 - _d4["targets"][0]["call_price"]) < 0.01
      and abs(_d4["targets"][0]["put_price_unit"] * _m4 - _d4["targets"][0]["put_price"]) < 0.01,
      "%s×%s=%s vs %s" % (_d4["targets"][0]["call_price_unit"], _m4,
                          _d4["targets"][0]["call_price_unit"] * _m4,
                          _d4["targets"][0]["call_price"]))

# 非对称 delta: call 手数应少于 put (call delta 更大)
_d2 = calc_dual({'equity': 1000000, 'code': 'cu', 'month': '2611', 'risk_percent': 3,
                 'call_strike': 72000, 'call_delta': 0.30, 'call_premium': 2000,
                 'put_strike': 68000, 'put_delta': -0.25, 'put_premium': 1800})
check("双买: delta 不对称时 call 手数 < put 手数", _d2["call"]["qty"] < _d2["put"]["qty"],
      "%s / %s" % (_d2["call"]["qty"], _d2["put"]["qty"]))
check("双买: 取整后两腿 delta 仍接近中性(差 <5%)", _d2["delta_gap_pct"] < 5,
      str(_d2["delta_gap_pct"]))

# 预算不足 → 两腿都不开
_d3 = calc_dual({'equity': 100000, 'code': 'lc', 'month': '2611', 'risk_percent': 3,
                 'call_strike': 144000, 'call_delta': 0.3, 'call_premium': 9800,
                 'put_strike': 120000, 'put_delta': -0.3, 'put_premium': 8500})
check("双买: 预算装不下 1 组 → 两腿都不开", _d3["enough_lots"] is False
      and _d3["call"]["qty"] == 0 and _d3["put"]["qty"] == 0, str(_d3["message"]))
check("双买: 不开仓时给提示文案", bool(_d3["message"]), '')

# ===== v50.81: 双买手数改成「delta 中性优先」求解 =====
#   用户实测案例: 玻璃 800 跨式, call 1260/手 (Δ 0.8291) / put 150/手 (Δ −0.1973)
#   老算法: 按 unit_cost 估 put 手数 → round 出 call → 逐手递减 put, 3% 预算下给出 1:6(两腿差 42.8%)
#   按规则(先保 delta 中性)应当是 1:4(两腿差 4.8%)
_FG81 = {'code': 'FG', 'month': '2701', 'call_strike': 800, 'call_delta': 0.8291,
         'call_premium': 1260, 'put_strike': 800, 'put_delta': -0.1973, 'put_premium': 150}
_d81a = calc_dual(dict(_FG81, equity=110000, risk_percent=3))
check("双买(v50.81): 玻璃 3% 预算 → 1:4(不是老算法的 1:6), 两腿差 <5%",
      _d81a["call"]["qty"] == 1 and _d81a["put"]["qty"] == 4 and _d81a["delta_gap_pct"] < 5,
      "%s:%s 差 %s%%" % (_d81a["call"]["qty"], _d81a["put"]["qty"], _d81a["delta_gap_pct"]))
check("双买(v50.81): 2% 与 3% 预算装不下「下一档」时给同一组手数",
      (lambda b: (b["call"]["qty"], b["put"]["qty"]) == (1, 4))(
          calc_dual(dict(_FG81, equity=110000, risk_percent=2))), '')
check("双买(v50.81): 5% 预算 → 2:8(规模翻倍且仍中性)",
      (lambda b: (b["call"]["qty"], b["put"]["qty"]) == (2, 8) and b["delta_gap_pct"] < 5)(
          calc_dual(dict(_FG81, equity=110000, risk_percent=5))), '')
check("双买(v50.81): 每 1 手 call 配几手 put 如实返回(4.2019)",
      abs(_d81a["put_per_call"] - 4.2019) < 0.001, str(_d81a["put_per_call"]))
check("双买(v50.81): 预算没用完要如实报出 + 给出「下一档」的成本",
      abs(_d81a["budget_left"] - (3300 - 1860)) < 0.01 and _d81a["next_pair"]["cost"] > _d81a["budget"],
      str({"left": _d81a["budget_left"], "next": _d81a["next_pair"]}))
check("双买(v50.81): 「配不出 1 组」的提示写的是「1 手 call 配 4.2 手 put」(以前误用 ratio 写成 0.24 手)",
      (lambda m: "4.2" in m and "0.24" not in m)(calc_dual(dict(_FG81, equity=1000, risk_percent=100))["message"]),
      calc_dual(dict(_FG81, equity=1000, risk_percent=100))["message"])

# 性质: 各档预算都要满足「① 不超预算 ② 中性(gap<10%) ③ 再加一组就超预算」
_miss81 = []
for _b81 in (1860, 1870, 2200, 3300, 4500, 5500, 9000, 20000, 100000):
    _d = calc_dual(dict(_FG81, equity=_b81, risk_percent=100))
    if _d["enough_lots"]:
        _ok = (_d["total_premium"] <= _b81 + 1e-6) and (_d["delta_gap_pct"] < 10) \
              and (_d["next_pair"] is not None) and (_d["next_pair"]["cost"] > _b81)
    else:
        _ok = _d["next_pair"] is None
    if not _ok:
        _miss81.append((_b81, _d["call"]["qty"], _d["put"]["qty"], _d["delta_gap_pct"],
                       _d["total_premium"], _d["next_pair"]))
check("双买(v50.81): 9 档预算都满足「不超预算 + 中性 + 再加一组就超」", not _miss81, str(_miss81))
check("双买(v50.81): 预算正好不够 1 组时不开仓(1800 < 1860)",
      (lambda b: b["enough_lots"] is False)(
          calc_dual(dict(_FG81, equity=1800, risk_percent=100))), '')
check("双买(v50.81): 对称 delta 的结果没被改坏(k=1 → 手数相同)",
      (lambda b: b["call"]["qty"] == b["put"]["qty"] == 1 and abs(b["net_delta"]) < 1e-9)(
          calc_dual({'equity': 900000, 'code': 'lc', 'month': '2611', 'risk_percent': 3,
                     'call_strike': 144000, 'call_delta': 0.3, 'call_premium': 9800,
                     'put_strike': 120000, 'put_delta': -0.3, 'put_premium': 8500})), '')

for _bad, _desc in [
    ({'equity': 900000, 'code': 'lc', 'call_strike': 144000, 'call_delta': -0.3,
      'call_premium': 9800, 'put_strike': 120000, 'put_delta': -0.3, 'put_premium': 8500},
     "call delta 为负被拒"),
    ({'equity': 900000, 'code': 'lc', 'call_strike': 144000, 'call_delta': 0.3,
      'call_premium': 9800, 'put_strike': 120000, 'put_delta': 0.3, 'put_premium': 8500},
     "put delta 为正被拒"),
    ({'equity': 900000, 'code': 'lc', 'call_strike': 0, 'call_delta': 0.3,
      'call_premium': 9800, 'put_strike': 120000, 'put_delta': -0.3, 'put_premium': 8500},
     "行权价 0 被拒"),
    ({'equity': 900000, 'code': '不存在的品种', 'call_strike': 1, 'call_delta': 0.3,
      'call_premium': 9800, 'put_strike': 1, 'put_delta': -0.3, 'put_premium': 8500},
     "未知品种被拒"),
]:
    try:
        calc_dual(_bad)
        check("双买: " + _desc, False, '未拒绝')
    except ValueError:
        check("双买: " + _desc, True)

# 双买记录: mode=dual 与 options/futures 互相隔离
_du = "e2edual59"
_bd = "B20260923000001dd"
for _cp, _ct in (("C", "lc2611-C-144000"), ("P", "lc2611-P-120000")):
    trade_upsert({'mode': 'dual', 'underlying': _du, 'contract': _ct, 'batch': _bd,
                  'op_type': 'open', 'direction': 'buy', 'call_put': _cp,
                  'open_date': '2026-09-23', 'open_price': 9800, 'qty': 1, 'premium': 9800})
_gd = trade_groups(mode="dual")
_go = trade_groups(mode="options")
check("双买记录能写入并查到", any(g["underlying"] == _du for g in _gd))
check("双买记录不出现在期权买方列表", not any(g["underlying"] == _du for g in _go))
_dd = trade_detail(_du, mode="dual", batch=_bd)
check("双买详情: 两条腿都在持仓里", len(_dd["holdings"]) == 2, str(len(_dd["holdings"])))
_del = trade_detail(_du, mode="dual", batch=_bd)
for _op in _del["operations"]:
    trade_delete(_op["id"])
check("双买记录已清理", not any(g["underlying"] == _du for g in trade_groups(mode="dual")))

# v50.61: 开仓时的 IV / IV 百分位 —— 存得下、详情读得到、编辑不丢、备份带得走
_iu = "e2eiv61"
_ib = "B20261007000001iv"
for _cp, _ct in (("C", "lc2611-C-144000"), ("P", "lc2611-P-120000")):
    trade_upsert({'mode': 'dual', 'underlying': _iu, 'contract': _ct, 'batch': _ib,
                  'op_type': 'open', 'direction': 'buy', 'call_put': _cp,
                  'open_date': '2026-10-07', 'open_price': 9800, 'qty': 1, 'premium': 9800,
                  'iv': 32.5, 'iv_pct': 92})
_idd = trade_detail(_iu, mode="dual", batch=_ib)
check("IV(v50.61): 双买详情返回开仓时的 IV / IV 百分位",
      _idd["iv"] == 32.5 and _idd["iv_pct"] == 92,
      "iv=%s pct=%s" % (_idd["iv"], _idd["iv_pct"]))
_ione = [_o for _o in _idd["operations"] if _o["op_type"] == "open"][0]
check("IV(v50.61): 操作记录本身也带 IV", _ione["iv"] == 32.5 and _ione["iv_pct"] == 92,
      "iv=%s" % _ione.get("iv"))
# 编辑(改手数)不带 iv → 必须保留原值, 不能被抹掉
trade_upsert({'id': _ione["id"], 'mode': 'dual', 'underlying': _iu, 'contract': _ione["contract"],
              'batch': _ib, 'op_type': 'open', 'direction': 'buy', 'call_put': 'C',
              'open_date': '2026-10-07', 'open_price': 9800, 'qty': 2, 'premium': 19600})
_iafter = [_o for _o in trade_detail(_iu, mode="dual", batch=_ib)["operations"] if _o["op_type"] == "open"][0]
check("IV(v50.61): 编辑开仓(不传 IV)后原 IV 保留",
      _iafter["iv"] == 32.5 and _iafter["iv_pct"] == 92 and _iafter["qty"] == 2,
      "iv=%s qty=%s" % (_iafter["iv"], _iafter["qty"]))
# 备份往返: 导出含 iv/iv_pct, 导入后不丢
_exp_iv = fund_export_backup()
_ivrow = [x for x in _exp_iv["trades"] if x.get("iv") == 32.5]
check("IV(v50.61): 备份导出含 iv / iv_pct", len(_ivrow) >= 1 and _ivrow[0]["iv_pct"] == 92,
      "rows=%d" % len(_ivrow))
for _o in trade_detail(_iu, mode="dual", batch=_ib)["operations"]:
    trade_delete(_o["id"])
_imp_iv = trade_import_record(dict(_ivrow[0]))
_get_iv = [_o for _o in trade_detail(_iu, mode="dual", batch=_ib)["operations"]]
check("IV(v50.61): 导入后 iv / iv_pct 原样恢复",
      len(_get_iv) == 1 and _get_iv[0]["iv"] == 32.5 and _get_iv[0]["iv_pct"] == 92,
      str([(x.get("iv"), x.get("iv_pct")) for x in _get_iv]))
for _o in _get_iv:
    trade_delete(_o["id"])

# v50.62: 「目标 delta」从界面去掉 → 编辑开仓(不传该字段)必须保留历史值, 不能抹掉
_tu2 = "e2etd62"
_tb2 = "B20261007000002td"
trade_upsert({'mode': 'options', 'underlying': _tu2, 'contract': 'lc2611-C-144000', 'batch': _tb2,
              'op_type': 'open', 'direction': 'buy', 'call_put': 'C', 'open_date': '2026-10-07',
              'open_price': 9800, 'qty': 1, 'premium': 9800, 'open_delta': 0.3, 'target_delta': 0.45})
_td62 = trade_detail(_tu2, mode="options", batch=_tb2)
_td_op = [x for x in _td62["operations"] if x["op_type"] == "open"][0]
check("目标 delta(v50.62): 老记录里的值能读出来", _td_op["target_delta"] == 0.45, str(_td_op.get("target_delta")))
trade_upsert({'id': _td_op["id"], 'mode': 'options', 'underlying': _tu2, 'contract': _td_op["contract"],
              'batch': _tb2, 'op_type': 'open', 'direction': 'buy', 'call_put': 'C',
              'open_date': '2026-10-07', 'open_price': 9800, 'qty': 2, 'premium': 19600,
              'open_delta': 0.3, 'target_delta': None})
_td_after = [x for x in trade_detail(_tu2, mode="options", batch=_tb2)["operations"] if x["op_type"] == "open"][0]
check("目标 delta(v50.62): 编辑(界面已无该字段)后历史值保留",
      _td_after["target_delta"] == 0.45 and _td_after["qty"] == 2,
      "target=%s qty=%s" % (_td_after["target_delta"], _td_after["qty"]))
for _o in trade_detail(_tu2, mode="options", batch=_tb2)["operations"]:
    trade_delete(_o["id"])

# v50.63: 双买详情自带「止盈价测算」—— 持仓优先, 平完退回开仓记录; 非双买不算
_dt_u, _dt_b = "e2edt63", "B20261007000003dt"
for _cp, _ct, _pp, _q in (("C", "lc2611-C-144000", 9800, 2), ("P", "lc2611-P-120000", 8500, 3)):
    trade_upsert({'mode': 'dual', 'underlying': _dt_u, 'contract': _ct, 'batch': _dt_b,
                  'op_type': 'open', 'direction': 'buy', 'call_put': _cp,
                  'open_date': '2026-10-07', 'open_price': _pp, 'qty': _q, 'premium': _pp * _q})
_dd63 = trade_detail(_dt_u, mode="dual", batch=_dt_b)
_t63 = _dd63["dual_targets"]
check("止盈价测算(v50.63): 双买详情返回 dual_targets",
      bool(_t63) and _t63["source"] == "holdings", str(_t63 and _t63.get("source")))
check("止盈价测算(v50.63): 总权利金 = 各腿权利金之和",
      _t63["total_premium"] == 2 * 9800 + 3 * 8500, str(_t63["total_premium"]))
check("止盈价测算(v50.63): 手数按腿汇总(C 2 / P 3)",
      _t63["call_qty"] == 2 and _t63["put_qty"] == 3,
      "%s / %s" % (_t63["call_qty"], _t63["put_qty"]))
_t2_63 = _t63["targets"][0]
check("止盈价测算(v50.63): 2 倍 = 2×总权利金, Call 每手目标 = 合计 ÷ call 手数",
      _t2_63["n"] == 2 and _t2_63["value"] == 90200 and _t2_63["call_price"] == 45100,
      str(_t2_63))
check("止盈价测算(v50.63): Put 每手目标 = 合计 ÷ put 手数",
      abs(_t2_63["put_price"] - 90200 / 3) < 0.01, str(_t2_63["put_price"]))
check("止盈价测算(v50.63): 输出 2/3/4/5 四档",
      [x["n"] for x in _t63["targets"]] == [2, 3, 4, 5], '')
check("止盈价测算(v50.63): 期权买方模式不返回该字段",
      trade_detail(_dt_u, mode="options", batch=_dt_b)["dual_targets"] is None, '')
# 部分平仓 → 按剩余持仓算(权利金按 FIFO 扣减)
trade_upsert({'mode': 'dual', 'underlying': _dt_u, 'contract': 'lc2611-C-144000', 'batch': _dt_b,
              'op_type': 'close', 'direction': 'buy', 'call_put': 'C', 'close_date': '2026-10-08',
              'close_qty': 1, 'close_price': 12000, 'pnl': 2200})
_tp = trade_detail(_dt_u, mode="dual", batch=_dt_b)["dual_targets"]
check("止盈价测算(v50.63): 部分平仓后按剩余持仓(1C/3P, 权利金扣掉已平那手)",
      _tp["source"] == "holdings" and _tp["call_qty"] == 1 and _tp["put_qty"] == 3
      and _tp["total_premium"] == 9800 + 25500, str(_tp["total_premium"]))
# 全部平掉 → 退回开仓记录
trade_upsert({'mode': 'dual', 'underlying': _dt_u, 'contract': 'lc2611-C-144000', 'batch': _dt_b,
              'op_type': 'close', 'direction': 'buy', 'call_put': 'C', 'close_date': '2026-10-09',
              'close_qty': 1, 'close_price': 12000, 'pnl': 2200})
trade_upsert({'mode': 'dual', 'underlying': _dt_u, 'contract': 'lc2611-P-120000', 'batch': _dt_b,
              'op_type': 'close', 'direction': 'buy', 'call_put': 'P', 'close_date': '2026-10-09',
              'close_qty': 3, 'close_price': 4000, 'pnl': -13500})
_tc = trade_detail(_dt_u, mode="dual", batch=_dt_b)["dual_targets"]
check("止盈价测算(v50.63): 全平后退回开仓记录回算",
      _tc["source"] == "open" and _tc["total_premium"] == 45100 and _tc["targets"][0]["value"] == 90200,
      "%s / %s" % (_tc["source"], _tc["total_premium"]))
for _o in trade_detail(_dt_u, mode="dual", batch=_dt_b)["operations"]:
    trade_delete(_o["id"])

# v50.67: 双买开仓价口径修正 —— 老数据把「每手权利金」当成了价格(被乘过期权乘数)
from main import _migrate_dual_open_price, _option_mult
check("双买乘数(v50.67): _option_mult 按品种取(烧碱 SH = 30)",
      _option_mult("SH") == 30 and _option_mult("lc") == 1,
      "SH=%s lc=%s" % (_option_mult("SH"), _option_mult("lc")))
_mu = "SH"
trade_upsert({'mode': 'dual', 'underlying': _mu, 'contract': 'SH2612-C-1880', 'batch': 'Bm67a',
              'op_type': 'open', 'direction': 'buy', 'call_put': 'C', 'open_date': '2026-10-08',
              'open_price': 1260, 'qty': 1, 'premium': 1260})      # 老数据: 42×30
trade_upsert({'mode': 'dual', 'underlying': _mu, 'contract': 'SH2612-P-1820', 'batch': 'Bm67b',
              'op_type': 'open', 'direction': 'buy', 'call_put': 'P', 'open_date': '2026-10-08',
              'open_price': 39, 'qty': 1, 'premium': 1170})        # 已是价格口径 → 不该动
trade_upsert({'mode': 'options', 'underlying': 'lc', 'contract': 'lc2611-C-144000', 'batch': 'Bm67c',
              'op_type': 'open', 'direction': 'buy', 'call_put': 'C', 'open_date': '2026-10-08',
              'open_price': 9800, 'qty': 1, 'premium': 9800})      # 非双买 → 不该动
_mig_n = _migrate_dual_open_price(_main_mod._fund_db())
check("双买价格修正(v50.67): 只改被乘过乘数的那条(改 1 条)",
      _mig_n == 1, "实际改了 %d 条" % _mig_n)
check("双买价格修正(v50.67): 开仓价改回期权价格(1260 ÷ 30 = 42)",
      abs(trade_detail(_mu, mode="dual", batch='Bm67a')["holdings"][0]["open_price"] - 42) < 0.001,
      str(trade_detail(_mu, mode="dual", batch='Bm67a')["holdings"][0]["open_price"]))
check("双买价格修正(v50.67): 权利金不受影响(仍是每手 1260)",
      abs(trade_detail(_mu, mode="dual", batch='Bm67a')["holdings"][0]["premium"] - 1260) < 0.01, '')
check("双买价格修正(v50.67): 已是价格口径的不动",
      abs(trade_detail(_mu, mode="dual", batch='Bm67b')["holdings"][0]["open_price"] - 39) < 0.001, '')
check("双买价格修正(v50.67): 重复执行安全(第二次改 0 条)",
      _migrate_dual_open_price(_main_mod._fund_db()) == 0, '')
for _b in ('Bm67a', 'Bm67b', 'Bm67c'):
    _u = _mu if _b != 'Bm67c' else 'lc'
    for _o in trade_detail(_u, mode="dual" if _b != 'Bm67c' else "options", batch=_b)["operations"]:
        trade_delete(_o["id"])

# 9c. 风险额度只接受白名单档位 (v50.30): 之前 999 也能存进配置; v50.83 白名单加了 4% / 5%
print("\n== 风险额度设置校验 ==")
from main import save_settings, FUTURES_RISK_OPTIONS as _FRO, OPTIONS_RISK_OPTIONS as _ORO
check("风险额度档位常量含 4% / 5%",
      4.0 in _FRO and 5.0 in _ORO and _FRO == _ORO == [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
      "%s / %s" % (_FRO, _ORO))
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
# v50.83: 新增档位也要能存
save_settings({'futures_risk_pct': 4})
check("风险额度 4% 可存", get_settings().get('futures_risk_pct') == 4.0)
save_settings({'options_risk_pct': 5})
check("期权风险额度 5% 可存", get_settings().get('options_risk_pct') == 5.0)
save_settings({'options_risk_pct': None})
try:
    save_settings({'options_risk_pct': 6})
    check("风险额度 6% 仍被拒(白名单外)", False, "未拒绝")
except ValueError:
    check("风险额度 6% 仍被拒(白名单外)", True)
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
fund_upsert("期权买方", 2030, 1, 1000, 1100, 0, "迁移测试")
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
_mig, _st, _migfail = fund_set_data_dir(_dst_dir)
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
fund_upsert("期权买方", 2040, 1, 100, 200, 0, "恢复测试标记")
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

print("\n== 备份变更说明 (v50.75) ==")
from main import (_backup_meta_path, _read_backup_meta, _write_backup_meta,
                  _trade_change_detail, _pool_change_detail, trade_get, trade_pool_get)

# 11.6.1 说明落在 .db 之外的 sidecar 文件, 不污染备份本体
_snap = fund_auto_backup("交易记录-保存", force=True, detail="期权买方 · 铁矿石 i2701 · 开仓买 6 手")
check("备份说明文件是 sidecar(.db.meta.json)", _snap and _os.path.exists(_backup_meta_path(_snap)),
      str(_backup_meta_path(_snap)))
# ⚠ 说明绝不能进库里, 否则恢复会把说明带进去
_ck = sqlite3.connect(_snap)
_tabs = {r[0] for r in _ck.execute("SELECT name FROM sqlite_master WHERE type='table'")}
_ck.close()
check("备份本体不含 meta 表(说明不污染数据)", not any("meta" in t.lower() for t in _tabs), str(sorted(_tabs)))
_m1 = _read_backup_meta(_snap)
check("说明可读回 reason(含 detail 合并)",
      _m1 and _m1.get("reason") == "交易记录-保存：期权买方 · 铁矿石 i2701 · 开仓买 6 手", str(_m1))
check("说明含版本号", _m1 and _m1.get("version_name"), str(_m1 and _m1.get("version_name")))
check("说明含各表条数", _m1 and isinstance(_m1.get("counts"), dict) and "trades" in _m1["counts"], str(_m1))
# 列表接口应带上说明字段, 且第一份就是刚存的
_bl2 = fund_backup_list()
check("备份列表带 reason 字段", _bl2 and "reason" in _bl2[0], str(_bl2[:1]))
check("列表首条 reason 与写入一致", _bl2 and "铁矿石" in (_bl2[0].get("reason") or ""), str(_bl2[0].get("reason")))
check("列表带 pools/reviews 条数", _bl2 and "pools" in _bl2[0], str(_bl2[:1]))

# 11.6.2 二次写入覆盖旧说明; 读不存在说明返回 None(老备份兼容)
_write_backup_meta(_snap, "只写 reason", "")
check("二次写入覆盖(不留旧文本)",
      (lambda m: m and m.get("reason") == "只写 reason" and "铁矿石" not in m.get("reason", ""))(_read_backup_meta(_snap)))
check("读不存在的说明返回 None(老备份)", _read_backup_meta(_os.path.join(fund_backup_dir(), "funds_不存在.db")) is None)

# 11.6.3 被 min_interval 合并掉的原因要一起写进说明(否则「删了东西却显示上一次的保存」)
_p_keep = _os.path.join(fund_backup_dir(), "funds_20880101_000000_000.db")
# 先制造一次真实落盘, 让 _last_backup_ts 变成「刚刚」
_snap_a = fund_auto_backup("第一次", force=True)
_snap_b = fund_auto_backup("被合并的原因A")                    # 2s 内 → 合并, 不落盘
fund_auto_backup("被合并的原因B")                              # 再合并
_snap_c = fund_auto_backup("第三次落盘", force=True)           # 强制落盘 → 应把 A/B 一起写进去
_rc = (_read_backup_meta(_snap_c) or {}).get("reason", "")
check("合并掉的原因不丢", ("被合并的原因A" in _rc) and ("被合并的原因B" in _rc) and ("第三次落盘" in _rc), _rc)
check("合并去重(同一个原因只留一次)",
      (_read_backup_meta(_snap_c) or {}).get("reason", "").count("被合并的原因A") == 1, _rc)

# 11.6.4 单条描述函数: 空输入 / 开仓 / 平仓 / 监控池
check("描述: 空记录返回空串", _trade_change_detail(None) == "" and _trade_change_detail({}) == "")
_d_open = _trade_change_detail({"mode": "futures", "underlying": "i", "contract": "i2701",
                                "direction": "sell", "qty": 5, "close_qty": 0})
check("描述: 期货开仓含品种/方向(中文化)/手数",
      ("期货" in _d_open) and ("i2701" in _d_open) and ("开仓卖" in _d_open) and ("5 手" in _d_open), _d_open)
_d_close = _trade_change_detail({"mode": "options", "underlying": "i", "contract": "i2701C800",
                                 "call_put": "C", "direction": "buy", "qty": 5, "close_qty": 2,
                                 "close_price": 100, "pnl": 10})
check("描述: 按 close_qty 判定为平仓", "平仓C买" in _d_close and "2 手" in _d_close, _d_close)
_d_del = _trade_change_detail({"mode": "dual", "underlying": "lc", "contract": "lc2611-C-144000"}, "删除")
check("描述: 删除时标注", ("期权双买" in _d_del) and ("删除开仓" in _d_del), _d_del)
check("描述: 未知字段不抛异常", isinstance(_trade_change_detail({"mode": "x"}), str))
check("描述: 监控池含合约数",
      "3 个合约" in _pool_change_detail({"mode": "options", "snapshot_date": "2026-10-12",
                                          "contracts": ["a", "b", "c"]}),
      _pool_change_detail({"mode": "options", "snapshot_date": "2026-10-12", "contracts": ["a", "b", "c"]}))
check("描述: 监控池空合约不抛", isinstance(_pool_change_detail({}), str))

# 11.6.5 trade_get / trade_pool_get (删除前留底)
_newid = trade_upsert({'underlying': 'bkmeta', 'contract': 'cu2610C80000', 'op_type': 'open',
                       'direction': 'buy', 'open_date': '2026-08-01', 'call_put': 'C',
                       'open_price': 100, 'qty': 2, 'premium': 200, 'mode': 'options'})
_g = trade_get(_newid)
check("trade_get 按 id 取回", _g and _g.get("underlying") == "bkmeta", str(_g))
check("trade_get 不存在返回 None", trade_get(99999999) is None)
check("trade_get 非法 id 返回 None(不抛)", trade_get("abc") is None)
_pid = trade_pool_upsert({'contracts': ['cu2610C80000'], 'note': 'bkmeta池'})
_pg = trade_pool_get(_pid)
check("trade_pool_get 按 id 取回", _pg and _pg.get("note") == "bkmeta池", str(_pg))
check("trade_pool_get 不存在返回 None", trade_pool_get(99999999) is None)
trade_delete(_newid)
trade_pool_delete(_pid)

# 11.6.6 轮转时说明文件跟着走, 不留孤儿
for _i in range(AUTO_BACKUP_KEEP + 3):
    _p = _os.path.join(fund_backup_dir(), "funds_20890101_0000%02d_000.db" % _i)
    with open(_p, "wb") as _f:
        _f.write(b"x")
    with open(_p + ".meta.json", "w", encoding="utf-8") as _f:
        _f.write('{"reason":"孤儿测试"}')
fund_auto_backup("触发轮转2", force=True)
_orphan = [f for f in os.listdir(fund_backup_dir())
           if f.endswith(".meta.json") and not _os.path.exists(_os.path.join(fund_backup_dir(), f[:-len(".meta.json")]))]
check("轮转后不留孤儿说明文件", len(_orphan) == 0, str(_orphan))

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

print("\n== 检查更新 ==")
# ⚠ 全部离线测: 打桩替换 _update_manifest, 不依赖 GitHub 可达(CI/断网也要能跑)
import main as M

_orig_manifest = M._update_manifest
_orig_read = M._read_url_bytes

# 1) 版本号格式化: 5040 → v50.40
check("_version_name(5040) = v50.40", M._version_name(5040) == "v50.40", M._version_name(5040))
check("_version_name(5041) = v50.41", M._version_name(5041) == "v50.41", M._version_name(5041))
check("_version_name 三位数兜底", M._version_name(999) == "v9.99", M._version_name(999))

# 2) 清单解析: 相对 url 拼成绝对, notes 支持字符串换行
M._read_url_bytes = lambda url, timeout=None: (
    '{"version": 5999, "version_name": "v59.99", "date": "2099-01-01",'
    ' "url": "dist/OpenCalc.exe", "notes": "第一条\\n第二条\\n"}').encode('utf-8')
_m = M._update_manifest()
check("清单解析 version", _m["version"] == 5999, str(_m.get("version")))
check("清单相对 url 拼成绝对", _m["url"].startswith("https://") and _m["url"].endswith("/dist/OpenCalc.exe"), _m["url"])
check("清单 notes 字符串按行拆开", _m["notes"] == ["第一条", "第二条"], str(_m["notes"]))
check("清单 notes 过滤空行", "" not in _m["notes"], str(_m["notes"]))

# 3) 绝对 url 不被改写
M._read_url_bytes = lambda url, timeout=None: (
    '{"version": 5999, "url": "https://example.com/x/OpenCalc.exe"}').encode('utf-8')
check("清单绝对 url 原样保留", M._update_manifest()["url"] == "https://example.com/x/OpenCalc.exe")

# 4) 缺 version 字段 → 报错(不静默)
def _bad_manifest(url, timeout=None):
    return b'{"version_name": "v9.9"}'
M._read_url_bytes = _bad_manifest
try:
    M._update_manifest()
    _bad_ok = False
except ValueError:
    _bad_ok = True
check("清单缺 version 字段报错", _bad_ok)

# 5) 有新版本 → has_update=True
M._read_url_bytes = lambda url, timeout=None: b'{"version": 9999, "version_name": "v99.99"}'
_r = M.update_check()
check("update_check ok=True", _r["ok"] is True, str(_r)[:120])
check("update_check has_update (远端更新)", _r["has_update"] is True, str(_r.get("has_update")))
check("update_check 带回本机版本号", _r["current"] == M.APP_VERSION, str(_r.get("current")))
check("update_check 带回本机版本名", _r["current_name"] == M._version_name(M.APP_VERSION), str(_r.get("current_name")))
check("update_check 带回 frozen 标志", isinstance(_r.get("frozen"), bool), str(_r.get("frozen")))

# 6) 远端同版本 / 更旧 → has_update=False(不能提示降级)
for _v, _tag in ((M.APP_VERSION, "同版本"), (M.APP_VERSION - 10, "远端更旧")):
    M._read_url_bytes = ('{"version": %d}' % _v).encode('utf-8')
    check("update_check 不误报更新(%s)" % _tag, M.update_check()["has_update"] is False)

# 7) 网络异常 → ok=False 且带人话文案(不抛异常出去)
def _boom(url, timeout=None):
    raise OSError("urlopen error [Errno 11001] getaddrinfo failed")
M._read_url_bytes = _boom
_r2 = M.update_check()
check("update_check 网络失败不抛异常", _r2["ok"] is False, str(_r2)[:120])
check("update_check 网络失败给人话文案", "DNS" in _r2["error"] or "无法解析" in _r2["error"], _r2["error"])
check("update_check 失败仍带回本机版本", _r2["current"] == M.APP_VERSION)

def _timeout(url, timeout=None):
    raise TimeoutError("timed out")
M._read_url_bytes = _timeout
check("网络异常翻译: 超时", "超时" in M.update_check()["error"], M.update_check()["error"])

# 8) 下载: 内容过小 → 拒绝(防把错误页/重定向页存成 exe), 且不留 .part 垃圾
M._read_url_bytes = lambda url, timeout=None: b'{"version": 9999, "url": "https://x.invalid/a.exe"}'
_udir = M._update_dir()
_import_urllib = __import__('urllib.request', fromlist=['urlopen'])


class _FakeResp:
    def __init__(self, data):
        self._d = data
        self.headers = {'Content-Length': str(len(data))}

    def read(self, n=-1):
        out, self._d = self._d, b''
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_real_urlopen = _import_urllib.urlopen
_import_urllib.urlopen = lambda req, timeout=None: _FakeResp(b'tiny')
try:
    M.update_download()
    _small_ok = False
except ValueError as e:
    _small_ok = '异常' in str(e)
check("下载拒绝过小内容(错误页防护)", _small_ok)
check("下载失败不留 .part 残留", not [f for f in os.listdir(_udir) if f.endswith('.part')],
      str([f for f in os.listdir(_udir) if f.endswith('.part')]))

# 9) 下载: 已是最新 → 直接拒绝(不白下 10MB)
M._read_url_bytes = lambda url, timeout=None: ('{"version": %d}' % M.APP_VERSION).encode('utf-8')
try:
    M.update_download()
    _cur_ok = False
except ValueError as e:
    _cur_ok = '最新' in str(e)
check("下载在已是最新时拒绝", _cur_ok)

# 10) 下载: 正常路径 + sha256 校验
_payload = b'X' * (1024 * 1024 + 128)
_sha = __import__('hashlib').sha256(_payload).hexdigest()
M._read_url_bytes = lambda url, timeout=None: (
    '{"version": 9999, "version_name": "v99.99", "url": "https://x.invalid/a.exe", "sha256": "%s"}' % _sha).encode('utf-8')
_import_urllib.urlopen = lambda req, timeout=None: _FakeResp(_payload)
_d = M.update_download()
check("下载成功返回路径", os.path.isfile(_d["path"]), str(_d)[:160])
check("下载文件大小正确", _d["size"] == len(_payload), str(_d.get("size")))
check("下载文件名含版本名", "v99.99" in os.path.basename(_d["path"]), os.path.basename(_d["path"]))
check("下载未留 .part", not os.path.exists(_d["path"] + ".part"))

# 11) sha256 不匹配 → 丢弃(且删掉半成品文件)
M._read_url_bytes = lambda url, timeout=None: (
    '{"version": 9999, "url": "https://x.invalid/a.exe", "sha256": "%s"}' % ('0' * 64)).encode('utf-8')
try:
    M.update_download()
    _sha_ok = False
except ValueError as e:
    _sha_ok = '校验' in str(e)
check("下载 sha256 不匹配被拒绝", _sha_ok)
# 只校验「本次」没留下半成品: 上一条用例成功下载的 v99.99 文件还在, 不能一起算进来
check("sha256 失败不留新的半成品(.part)",
      not [f for f in os.listdir(_udir) if f.endswith('.part')], str(os.listdir(_udir)))
_failed_target = os.path.join(_udir, 'OpenCalc_v99.99.exe')
check("sha256 失败不覆盖旧的成功文件", os.path.isfile(_failed_target) and os.path.getsize(_failed_target) == len(_payload),
      str(os.path.getsize(_failed_target)) if os.path.isfile(_failed_target) else 'missing')

# 12) 备份列表: 空目录安全返回
check("update_backup_list 空目录返回 []", isinstance(M.update_backup_list(), list))

# 12.1) 下载超时放宽到 20 分钟: raw.githubusercontent 限速时 10MB 可能要几分钟
#       (实测 20 分钟只下到 3MB) → 超时给太短会让用户在限速网络下永远下不完
check("下载超时 ≥ 1200 秒(容忍限速网络)", M.UPDATE_DL_TIMEOUT >= 1200, str(M.UPDATE_DL_TIMEOUT))
check("清单超时保持短(8 秒内, 不卡界面)", M.UPDATE_TIMEOUT <= 15, str(M.UPDATE_TIMEOUT))

# 12.2) 下载进度: 可轮询, 下载中 running=True 且字节数递增
_prog0 = dict(M._update_progress)
_payload2 = b'Y' * (1024 * 1024 + 64)
M._read_url_bytes = lambda url, timeout=None: (
    '{"version": 9999, "version_name": "v99.98", "url": "https://x.invalid/b.exe"}').encode('utf-8')
_import_urllib.urlopen = lambda req, timeout=None: _FakeResp(_payload2)
_d2 = M.update_download()
check("下载完成后 running=False", M._update_progress["running"] is False, str(M._update_progress.get("running")))
check("下载完成后进度字节数 = 文件大小",
      M._update_progress["downloaded"] == len(_payload2), str(M._update_progress.get("downloaded")))
check("下载完成后 error 为空", M._update_progress["error"] == "", str(M._update_progress.get("error")))
check("进度 total 取自 Content-Length",
      M._update_progress["total"] == len(_payload2), str(M._update_progress.get("total")))

# 12.3) 下载失败时进度里留下可读错误(前端能显示)
M._read_url_bytes = lambda url, timeout=None: b'{"version": 9999, "url": "https://x.invalid/c.exe"}'
_import_urllib.urlopen = lambda req, timeout=None: _FakeResp(b'x')
try:
    M.update_download()
except ValueError:
    pass
check("下载失败: running=False 且 error 有内容",
      M._update_progress["running"] is False and len(M._update_progress["error"]) > 0,
      str(M._update_progress.get("error")))
M._update_progress.update(_prog0)
_import_urllib.urlopen = _real_urlopen

# 13) 备份当前版本: 开发模式(非 frozen)明确报错, 不假装成功
try:
    M.update_backup_current()
    _bk_ok = False
except ValueError as e:
    _bk_ok = '开发模式' in str(e)
check("非 exe 模式备份当前版本给出明确提示", _bk_ok)

# 14) 更新目录在数据目录内(软件更新不会清掉下载好的新版本)
check("更新目录位于数据目录下", M._is_inside(M._update_dir(), os.path.dirname(M.FUND_DB_PATH)), M._update_dir())
check("更新目录不在软件目录内", not M.data_dir_risky() or not M._is_inside(M._update_dir(), M._app_dir()), M._update_dir())

# 恢复打桩, 避免影响后续用例
_import_urllib.urlopen = _real_urlopen
M._update_manifest = _orig_manifest
M._read_url_bytes = _orig_read

print("\n================================")
print("最终通过 %d 项 / 失败 %d 项" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
