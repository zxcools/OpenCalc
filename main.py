# -*- coding: utf-8 -*-
"""
期货 / 期权开仓计算器
==================================
技术架构: Python 标准库 (http.server) + 内置浏览器 UI
- 零第三方运行时依赖, pyinstaller 打包为单文件 exe
- 双击 exe 后自动启动本地服务并打开默认浏览器

风控逻辑:
- 期货模式: 开仓金额 = 总权益 x 风险额度(可选 0.5%/1%/1.5%/2%/3%, 默认 1%)
  最大手数 = 开仓金额 / 每手风险金额(止损价差 x 乘数); 保证金仅参考展示
  盈亏比 = 止盈距离 / 止损距离, >= 1.5 建议参与, < 1.5 建议不参与
- 期权模式: 开仓金额统一 = 权益 x 3% (不再按 IV 分级)
  买入期权按权利金占用资金, 不计算盈亏比
"""

import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "期货开仓计算器"
DEFAULT_MARGIN_RATE = 0.16   # 期货保证金率 16%
FUTURES_RISK_RATIO = 0.01    # 期货默认开仓金额比例 1% (可选项 0.5/1/1.5/2/3, 默认 1%)
FUTURES_RISK_OPTIONS = [0.5, 1.0, 1.5, 2.0, 3.0]   # 期货风险额度可选档位(%)
OPTIONS_RISK_OPTIONS = [0.5, 1.0, 1.5, 2.0, 3.0]   # 期权风险额度可选档位(%)
OPTIONS_RISK_RATIO = 0.03    # 期权默认开仓金额比例 3% (可选项 0.5/1/1.5/2/3, 默认 3%)
MIN_PROFIT_LOSS_RATIO = 1.5  # 盈亏比阈值

# 行情相关
QUOTE_CACHE = {}             # code -> (quote_dict, timestamp)
QUOTE_CACHE_TTL = 5          # 行情缓存秒数(尽量接近实时)
IDLE_EXIT_SECONDS = 7200     # 无请求空闲自动退出(2小时, 避免页面开着服务中途停)

# ---------------------------------------------------------------------------
# 合约数据表 (合约乘数: 每手对应的数量单位)
# 结构: [code, 名称, 市场, 期货乘数, 期权乘数(None 表示与期货一致), 备注]
# ---------------------------------------------------------------------------
CONTRACTS = [
    # 上期所 (SHFE)
    ["cu", "沪铜", "上期所", 5, None, "吨/手", 10],
    ["al", "沪铝", "上期所", 5, None, "吨/手", 5],
    ["zn", "沪锌", "上期所", 5, None, "吨/手", 5],
    ["pb", "沪铅", "上期所", 5, None, "吨/手", 5],
    ["ni", "沪镍", "上期所", 1, None, "吨/手", 10],
    ["sn", "沪锡", "上期所", 1, None, "吨/手", 10],
    ["au", "沪金", "上期所", 1000, None, "克/手", 0.02],
    ["ag", "沪银", "上期所", 15, None, "千克/手", 1],
    ["rb", "螺纹钢", "上期所", 10, None, "吨/手", 1],
    ["hc", "热卷", "上期所", 10, None, "吨/手", 1],
    ["ss", "不锈钢", "上期所", 5, None, "吨/手", 5],
    ["ru", "橡胶", "上期所", 10, None, "吨/手", 5],
    ["sp", "纸浆", "上期所", 10, None, "吨/手", 2],
    ["bu", "沥青", "上期所", 10, None, "吨/手", 1],
    ["fu", "燃料油", "上期所", 10, None, "吨/手", 1],
    ["wr", "线材", "上期所", 10, None, "吨/手", 1],
    ["ao", "氧化铝", "上期所", 20, None, "吨/手", 1],
    ["br", "丁二烯橡胶", "上期所", 5, None, "吨/手", 5],
    # 能源中心 (INE)
    ["sc", "原油", "能源中心", 1000, None, "桶/手", 0.1],
    ["lu", "低硫燃料油", "能源中心", 10, None, "吨/手", 1],
    ["nr", "20号胶", "能源中心", 10, None, "吨/手", 5],
    ["bc", "国际铜", "能源中心", 5, None, "吨/手", 10],
    # 大商所 (DCE)
    ["m", "豆粕", "大商所", 10, None, "吨/手", 1],
    ["y", "豆油", "大商所", 10, None, "吨/手", 2],
    ["a", "豆一", "大商所", 10, None, "吨/手", 1],
    ["b", "豆二", "大商所", 10, None, "吨/手", 1],
    ["p", "棕榈油", "大商所", 10, None, "吨/手", 2],
    ["c", "玉米", "大商所", 10, None, "吨/手", 1],
    ["cs", "玉米淀粉", "大商所", 10, None, "吨/手", 1],
    ["jd", "鸡蛋", "大商所", 5, None, "吨/手", 1],
    ["lh", "生猪", "大商所", 16, None, "吨/手", 5],
    ["i", "铁矿石", "大商所", 100, None, "吨/手", 0.5],
    ["j", "焦炭", "大商所", 100, None, "吨/手", 0.5],
    ["jm", "焦煤", "大商所", 60, None, "吨/手", 0.5],
    ["l", "塑料", "大商所", 5, None, "吨/手", 1],
    ["pp", "聚丙烯", "大商所", 5, None, "吨/手", 1],
    ["v", "PVC", "大商所", 5, None, "吨/手", 1],
    ["eg", "乙二醇", "大商所", 10, None, "吨/手", 1],
    ["eb", "苯乙烯", "大商所", 5, None, "吨/手", 1],
    ["pg", "液化石油气", "大商所", 20, None, "吨/手", 1],
    ["rr", "粳米", "大商所", 10, None, "吨/手", 1],
    ["lg", "原木", "大商所", 90, None, "立方米/手", 0.5],
    # 郑商所 (CZCE)
    ["SR", "白糖", "郑商所", 10, None, "吨/手", 1],
    ["CF", "棉花", "郑商所", 5, None, "吨/手", 5],
    ["TA", "PTA", "郑商所", 5, None, "吨/手", 2],
    ["MA", "甲醇", "郑商所", 10, None, "吨/手", 1],
    ["FG", "玻璃", "郑商所", 20, None, "吨/手", 1],
    ["SA", "纯碱", "郑商所", 20, None, "吨/手", 1],
    ["UR", "尿素", "郑商所", 20, None, "吨/手", 1],
    ["RM", "菜粕", "郑商所", 10, None, "吨/手", 1],
    ["OI", "菜油", "郑商所", 10, None, "吨/手", 1],
    ["AP", "苹果", "郑商所", 10, None, "吨/手", 1],
    ["CJ", "红枣", "郑商所", 5, None, "吨/手", 5],
    ["PF", "短纤", "郑商所", 5, None, "吨/手", 2],
    ["PK", "花生", "郑商所", 5, None, "吨/手", 2],
    ["CY", "棉纱", "郑商所", 5, None, "吨/手", 5],
    ["ZC", "动力煤", "郑商所", 100, None, "吨/手", 0.2],
    ["SF", "硅铁", "郑商所", 5, None, "吨/手", 2],
    ["SM", "锰硅", "郑商所", 5, None, "吨/手", 2],
    ["SH", "烧碱", "郑商所", 30, None, "吨/手", 1],
    ["PX", "对二甲苯", "郑商所", 5, None, "吨/手", 2],
    ["PR", "瓶片", "郑商所", 15, None, "吨/手", 1],
    ["RS", "菜籽", "郑商所", 10, None, "吨/手", 1],
    ["WH", "强麦", "郑商所", 20, None, "吨/手", 1],
    # 中金所 (CFFEX)
    ["IF", "沪深300", "中金所", 300, None, "元/点", 0.2],
    ["IH", "上证50", "中金所", 300, None, "元/点", 0.2],
    ["IC", "中证500", "中金所", 200, None, "元/点", 0.2],
    ["IM", "中证1000", "中金所", 200, None, "元/点", 0.2],
    ["T", "十年国债", "中金所", 10000, None, "元/点", 0.005],
    ["TF", "五年国债", "中金所", 10000, None, "元/点", 0.005],
    ["TL", "三十年期国债", "中金所", 10000, None, "元/点", 0.005],
    # 广期所 (GFEX)
    ["si", "工业硅", "广期所", 5, None, "吨/手", 5],
    ["lc", "碳酸锂", "广期所", 1, None, "吨/手", 50],
    ["ps", "多晶硅", "广期所", 3, None, "吨/手", 5],
    # 集运指数(欧线)
    ["ec", "欧线集运", "能源中心", 50, None, "元/点", 0.1],
]

# 主要品种的期权乘数 (与期货不同的单独列出; None = 与期货一致)
OPTION_MULT_OVERRIDES = {
    "IO": 100,   # 沪深300股指期权 (中金所, 元/点)
    "HO": 100,   # 上证50股指期权
    "MO": 100,   # 中证1000股指期权
}


def get_contract(code: str):
    """按代码查找合约, 返回 dict 或 None"""
    for row in CONTRACTS:
        if row[0].lower() == code.lower():
            return {
                "code": row[0],
                "name": row[1],
                "exchange": row[2],
                "mult": row[3],
                "opt_mult": row[4] if row[4] else row[3],
                "unit": row[5],
                "tick": row[6],
            }
    return None


def contract_list():
    """返回合约下拉列表数据"""
    out = []
    for row in CONTRACTS:
        out.append({
            "code": row[0],
            "name": row[1],
            "exchange": row[2],
            "mult": row[3],
            "opt_mult": row[4] if row[4] else row[3],
            "unit": row[5],
            "tick": row[6],
            "label": "%s %s (%s)" % (row[1], row[0], row[2]),
        })
    return out


def _num(value, name):
    """解析正数, 非法抛 ValueError"""
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        v = float(str(value).replace(",", "").replace("，", "").strip())
    if v <= 0:
        raise ValueError("%s必须大于 0" % name)
    return v


def calc_futures(params):
    """
    期货模式计算
    params: equity(总权益), code(标的), direction(long/short),
            entry(开仓价), stop(止损价), target(止盈价),
            margin_rate(保证金率, 默认0.16), risk_ratio(开仓比例, 默认0.15)
    返回: dict
    """
    equity = _num(params.get("equity"), "总权益")
    entry = _num(params.get("entry"), "开仓价")
    stop = _num(params.get("stop"), "止损价")
    target = _num(params.get("target"), "止盈价")
    direction = params.get("direction", "long")
    margin_rate = float(params.get("margin_rate") or DEFAULT_MARGIN_RATE)
    risk_ratio = float(params.get("risk_ratio") or FUTURES_RISK_RATIO)

    contract = get_contract(params.get("code", ""))
    if contract is None:
        raise ValueError("未找到该标的, 请从列表中选择")
    mult = contract["mult"]

    # 方向与价格合理性校验
    if direction == "long":
        if not (stop < entry < target):
            raise ValueError("做多时需满足: 止损价 < 开仓价 < 止盈价")
        risk_dist = entry - stop
        reward_dist = target - entry
    else:
        if not (target < entry < stop):
            raise ValueError("做空时需满足: 止盈价 < 开仓价 < 止损价")
        risk_dist = stop - entry
        reward_dist = entry - target

    if risk_dist <= 0:
        raise ValueError("止损距离必须大于 0")

    per_lot_risk = risk_dist * mult              # 每手风险金额(止损价差 × 乘数)
    per_lot_reward = reward_dist * mult          # 每手止盈金额(名义)

    # 阶梯止盈: 以止损价差为 1R, 按 2R~5R 推算逐级止盈价(供分批止盈/移动止损参考)
    # 做多: 止盈价 = 开仓价 + N×风险距离; 做空: 止盈价 = 开仓价 - N×风险距离
    # 价格按最小变动价位对齐: 做多向上取整(价格更高才触发), 做空向下取整(价格更低才触发)
    # per_lot_profit 为理论价(N×每手风险)的名义浮盈, 仅作参考
    tick = 0.0
    try:
        tick = float(contract["tick"])
    except (KeyError, TypeError, ValueError):
        tick = 0.0
    ladder = []
    for n in (2, 3, 4, 5):
        raw = entry + n * risk_dist if direction == "long" else entry - n * risk_dist
        if tick > 0:
            steps = raw / tick
            if direction == "long":
                aligned = (math.ceil(steps - 1e-9)) * tick   # 整除时保持不变
            else:
                aligned = (math.floor(steps + 1e-9)) * tick
            price = round(aligned, 6)
        else:
            price = round(raw, 2)
        ladder.append({
            "r": n,
            "price": price,
            "per_lot_profit": round(n * per_lot_risk, 2),   # 该档每手名义浮盈(≈N×每手风险)
        })

    # 风险额度(占权益的百分比, 如 1.5 = 1.5%): 用户可自定义; 留空则默认 1.5%
    risk_percent = params.get("risk_percent")
    if risk_percent not in (None, ""):
        rp = _num(risk_percent, "风险额度百分比")
        if rp <= 0:
            raise ValueError("风险额度百分比必须大于 0")
        budget = equity * rp / 100.0
    else:
        # 兼容旧参数: risk_amount 为具体金额
        risk_amount = params.get("risk_amount")
        if risk_amount not in (None, ""):
            budget = _num(risk_amount, "风险额度")
            if budget <= 0:
                raise ValueError("风险额度必须大于 0")
        else:
            budget = equity * risk_ratio
    # 实际使用的风险百分比(回显用, 如 权益 × 1.5%)
    if risk_percent not in (None, ""):
        risk_pct_used = float(rp)
    elif params.get("risk_amount") not in (None, ""):
        risk_pct_used = budget / equity * 100.0
    else:
        risk_pct_used = risk_ratio * 100.0
    margin_per_lot = entry * mult * margin_rate  # 每手保证金(展示用, 不参与推手数)
    max_lots = int(budget // per_lot_risk)      # 最大手数=按风险金额倒推
    margin_used = max_lots * margin_per_lot      # 最大占用保证金
    pl_ratio = reward_dist / risk_dist           # 盈亏比
    risk_used = max_lots * per_lot_risk          # 实际最大风险金额(应<=budget)
    max_reward = max_lots * per_lot_reward       # 按最大手数止盈可盈利金额

    return {
        "ok": True,
        "contract": contract["name"],
        "code": contract["code"],
        "exchange": contract["exchange"],
        "mult": mult,
        "unit": contract["unit"],
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_percent": risk_pct_used,
        "budget": budget,
        "margin_rate": margin_rate,
        "margin_per_lot": margin_per_lot,
        "max_lots": max_lots,
        "margin_used": margin_used,
        "pl_ratio": pl_ratio,
        "per_lot_risk": per_lot_risk,
        "per_lot_reward": per_lot_reward,
        "risk_used": risk_used,
        "max_reward": max_reward,
        "ladder": ladder,
        "enough_lots": max_lots >= 1,
        "participate": pl_ratio >= MIN_PROFIT_LOSS_RATIO,
        "min_ratio": MIN_PROFIT_LOSS_RATIO,
    }


def calc_options(params):
    """
    期权模式计算 (买入开仓)
    params: equity(总权益), code(标的), entry(开仓价/权利金)
    开仓额度 = 权益 × risk_percent% (默认 3%, 可选项 0.5/1/1.5/2/3)
    返回: dict
    """
    equity = _num(params.get("equity"), "总权益")
    entry = _num(params.get("entry"), "开仓价")

    contract = get_contract(params.get("code", ""))
    if contract is None:
        raise ValueError("未找到该标的, 请从列表中选择")
    opt_mult = contract["opt_mult"]
    if contract["code"] in OPTION_MULT_OVERRIDES:
        opt_mult = OPTION_MULT_OVERRIDES[contract["code"]]

    # 风险额度(占权益的百分比, 如 3 = 3%): 用户可自定义; 留空则默认 3%
    risk_percent = params.get("risk_percent")
    if risk_percent not in (None, ""):
        rp = _num(risk_percent, "风险额度百分比")
        if rp <= 0:
            raise ValueError("风险额度百分比必须大于 0")
        risk_ratio = rp / 100.0
    else:
        risk_ratio = OPTIONS_RISK_RATIO

    budget = equity * risk_ratio
    premium_per_lot = entry                   # 每手权利金 = 用户输入的开仓价(已含合约乘数, 即1手价格)
    max_lots = int(budget // premium_per_lot)  # 最大手数
    funds_used = max_lots * premium_per_lot    # 占用资金

    return {
        "ok": True,
        "contract": contract["name"],
        "code": contract["code"],
        "exchange": contract["exchange"],
        "opt_mult": opt_mult,
        "unit": contract["unit"],
        "risk_ratio": risk_ratio,
        "risk_percent": risk_ratio * 100,
        "budget": budget,
        "premium_per_lot": premium_per_lot,
        "max_lots": max_lots,
        "funds_used": funds_used,
        "enough_lots": max_lots >= 1,
    }


# ===========================================================================
# 实时行情 (新浪财经, 主力连续合约)
# ===========================================================================
def _quote_candidates(symbol):
    """生成未来12个自然月的候选合约代码 (如 RB2608...RB2707)"""
    now = datetime.now()
    out = []
    for i in range(12):
        mm = now.month + i
        yy = now.year + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append("%s%02d%02d" % (symbol, yy % 100, mm))
    return out


def fetch_quote(code):
    """
    获取品种主力【具体合约】行情 (新浪 nf_ 接口)
    1. 批量探测未来12个自然月候选合约, 按持仓量最大确定主力合约(如 RB2610)
    2. 返回该合约最新价/昨收(上一根日K收盘)/涨跌幅/合约代码
    商品格式: 名称,时间,最新,昨收,今开,...,成交量[13],持仓量[14],...
    股指格式: 最新,昨收,今开,...,成交量[4],...,持仓量[6],...
    """
    symbol = code.upper()
    cands = _quote_candidates(symbol)
    url = "https://hq.sinajs.cn/list=" + ",".join("nf_" + c for c in cands)
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=4) as resp:
        raw = resp.read().decode("gbk", "replace")

    best = None
    for m in re.finditer(r'nf_([A-Z]+\d{4})="([^"]*)"', raw):
        ccode, body = m.group(1), m.group(2)
        parts = [p.strip() for p in body.split(",")]
        if not body or len(parts) < 10:
            continue
        try:
            float(parts[0])
            is_index = True
        except ValueError:
            is_index = False
        try:
            if is_index:
                latest = float(parts[0])
                prev_close = float(parts[1])
                oi = float(parts[6])          # 持仓量
            else:
                latest = float(parts[2])
                prev_close = float(parts[3])
                oi = float(parts[14])         # 持仓量
        except (ValueError, IndexError):
            continue
        if latest <= 0:
            continue
        if best is None or oi > best["oi"]:
            best = {"code": ccode, "latest": latest, "prev_close": prev_close,
                    "oi": oi, "is_index": is_index, "parts": parts}

    if best is None:
        raise ValueError("行情数据为空(接口异常或休市)")

    parts = best["parts"]
    if best["is_index"]:
        qt = ""
        for p in parts:
            if len(p) == 8 and p[2] == ":" and p[5] == ":":
                qt = p
                break
    else:
        qt = parts[1]
        if qt and len(qt) == 6 and qt.isdigit():
            qt = "%s:%s:%s" % (qt[0:2], qt[2:4], qt[4:6])

    change = best["latest"] - best["prev_close"]
    change_pct = change / best["prev_close"] * 100 if best["prev_close"] else 0
    return {
        "contract_code": best["code"],       # 具体主力合约, 如 RB2610
        "name": best["code"],
        "latest": best["latest"],
        "prev_close": best["prev_close"],
        "change": change,
        "change_pct": change_pct,
        "time": qt,
    }


def get_quote(code):
    """带缓存的行情获取"""
    now = time.time()
    hit = QUOTE_CACHE.get(code)
    if hit and now - hit[1] < QUOTE_CACHE_TTL:
        return hit[0]
    q = fetch_quote(code)
    QUOTE_CACHE[code] = (q, now)
    return q


# ===========================================================================
# 资金曲线记录模块 (SQLite 持久化)
# - 两个策略分开记录: 'abe' (主) + '威科夫' (预留)
# - 月度明细 + 年度汇总, 年度汇总由月度数据自动累加
# - ⚠ 数据必须存在持久目录: 打包成 exe 后 __file__ 指向临时解压目录(%TEMP%\_MEI*),
#   若按 __file__ 存数据, 程序退出后会被 bootloader 清理 → 记录丢失(已踩坑修复)
# - 数据目录可自定义(云盘同步/换电脑迁移): 优先读 config.json 的 data_dir
# ===========================================================================
def _app_config_dir():
    """配置目录(存 config.json):
    - 打包后 (frozen): %APPDATA%/OpenCalc
    - 开发时: 源码目录
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OpenCalc")
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_app_config_dir(), "config.json")


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _persistent_data_dir():
    """默认数据目录:
    - 打包后 (frozen): %APPDATA%/OpenCalc/data
    - 开发时: 源码目录/data
    """
    return os.path.join(_app_config_dir(), "data")


# 启动时决定数据目录: 优先 config.json 里的 data_dir (用户自定义/云盘)
_config = _load_config()
_custom_data_dir = (_config.get("data_dir") or "").strip()
if _custom_data_dir:
    FUND_DB_PATH = os.path.join(os.path.abspath(os.path.expanduser(_custom_data_dir)), "funds.db")
else:
    FUND_DB_PATH = os.path.join(_persistent_data_dir(), "funds.db")
FUND_DB_CONN = None
FUND_STRATEGIES = ["abe", "威科夫"]


def fund_set_data_dir(new_dir):
    """设置新的数据目录(可指向网盘同步文件夹), 并把现有记录合并迁移过去
    返回 (迁移条数, 说明文字); 相同目录返回 (0, "same")
    """
    global FUND_DB_PATH, FUND_DB_CONN
    new_dir = os.path.abspath(os.path.expanduser((new_dir or "").strip()))
    if not new_dir:
        raise ValueError("数据目录不能为空")
    cur_dir = os.path.dirname(FUND_DB_PATH)
    if os.path.normcase(new_dir) == os.path.normcase(cur_dir):
        return 0, "same"
    # 读旧库全部记录(迁移用, 在关闭连接前)
    old_records = fund_list_records()
    # 关闭旧连接, 避免文件锁
    if FUND_DB_CONN is not None:
        try:
            FUND_DB_CONN.close()
        except Exception:  # noqa: BLE001
            pass
        FUND_DB_CONN = None
    # 切到新目录并确保新库存在
    FUND_DB_PATH = os.path.join(new_dir, "funds.db")
    _fund_db()
    # 合并迁移(逐条 upsert; 目标库已有且本机没有的记录保留)
    migrated = 0
    for r in old_records:
        fund_upsert(
            r["strategy"], r["year"], r["month"],
            r["initial_equity"], r["end_equity"], r["cash_flow"], r["note"],
            cash=r.get("cash", 0),
        )
        migrated += 1
    # 持久化配置
    cfg = _load_config()
    cfg["data_dir"] = new_dir
    _save_config(cfg)
    return migrated, "migrated"


def fund_data_info():
    """返回当前数据位置信息(UI 显示用)"""
    return {
        "data_dir": os.path.dirname(FUND_DB_PATH),
        "db_path": FUND_DB_PATH,
        "db_exists": os.path.exists(FUND_DB_PATH),
        "record_count": len(fund_list_records()),
        "is_default": os.path.normcase(os.path.dirname(FUND_DB_PATH)) == os.path.normcase(_persistent_data_dir()),
    }


# ---------------------------------------------------------------------------
# 应用设置 (config.json 的 settings 字段): 默认权益 / 期货默认风险额度百分比
# ---------------------------------------------------------------------------
def get_settings():
    """读取用户设置: 返回 {default_equity, futures_risk_pct, options_risk_pct, frequent_futures, frequent_options} (无则 None/[])"""
    s = (_load_config().get("settings") or {})
    return {
        "default_equity": s.get("default_equity"),
        "futures_risk_pct": s.get("futures_risk_pct"),
        "options_risk_pct": s.get("options_risk_pct"),
        "frequent_futures": s.get("frequent_futures") or [],
        "frequent_options": s.get("frequent_options") or [],
    }


def save_settings(patch):
    """保存用户设置 (合并, 只更新传入字段; None/空串 = 清除该默认值; 数组 = 覆盖)"""
    cfg = _load_config()
    s = cfg.setdefault("settings", {})
    if "default_equity" in patch:
        v = patch["default_equity"]
        s["default_equity"] = None if v in (None, "") else float(v)
    if "futures_risk_pct" in patch:
        v = patch["futures_risk_pct"]
        s["futures_risk_pct"] = None if v in (None, "") else float(v)
    if "options_risk_pct" in patch:
        v = patch["options_risk_pct"]
        s["options_risk_pct"] = None if v in (None, "") else float(v)
    for k in ("frequent_futures", "frequent_options"):
        if k in patch:
            v = patch[k]
            s[k] = list(v) if v else []
    _save_config(cfg)
    return get_settings()


# 窗口置顶 (Always on Top): 按页面标题找主窗口, SetWindowPos 置顶/取消
def set_window_pin(pin):
    """把主窗口置顶(pin=True)或取消置顶(pin=False); 成功返回 True"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = user32.FindWindowW(None, APP_NAME)   # 窗口标题 = 页面 title
        if not hwnd:
            return False
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        flag = HWND_TOPMOST if pin else HWND_NOTOPMOST
        return bool(user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0,
                                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
    except Exception:  # noqa: BLE001
        return False


def browse_folder():
    """弹出 Windows 原生文件夹选择对话框, 返回选中的绝对路径(取消返回 None)
    用 IFileOpenDialog (Vista+ 现代 COM 接口), 原生 Unicode 解决 SHBrowseForFolder 在 Win10/11 1809+ 上的中文乱码
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import POINTER, byref, c_void_p, c_wchar_p, c_ulong, c_ushort, c_ubyte, c_int, c_uint, c_long
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", c_ulong),
                ("Data2", c_ushort),
                ("Data3", c_ushort),
                ("Data4", c_ubyte * 8),
            ]
        def G(d1, d2, d3, b):
            return GUID(d1, d2, d3, (c_ubyte * 8)(*b))

        CLSID_FileOpenDialog = G(0xDC1C5A9C, 0xE88A, 0x4DDE, (0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7))
        IID_IUnknown        = G(0x00000000, 0x0000, 0x0000, (0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46))
        IID_IShellItem      = G(0x43826D1E, 0xE718, 0x42EE, (0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE))

        # CoCreateInstance(CLSID_FileOpenDialog) → IUnknown*
        ppv = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None, 0x1, byref(IID_IUnknown), byref(ppv))
        if hr < 0 or not ppv.value:
            ole32.CoUninitialize()
            return None
        p = ppv.value
        vt = ctypes.cast(p, POINTER(c_void_p))

        FOS_PICKFOLDERS    = 0x00000020
        FOS_FORCEFILESYSTEM= 0x00000040
        SIGDN_FILESYSPATH  = 0x80058000

        # vtable 槽位 (IFileOpenDialog + 父接口继承顺序):
        # 0-2 IUnknown; 3 IModalWindow::Show; 4-20 IFileDialog(17 个); 21-22 IFileOpenDialog::GetResults/GetSelectedItems
        # IFileDialog 方法顺序: SetFileTypes..SetFilter(17 项) + SetOptions(19) + GetOptions(20) + SetTitle(21)
        IFileDialog_SetOptions = ctypes.WINFUNCTYPE(c_long, c_void_p, c_uint)(vt[19])
        IFileDialog_SetOptions(p, FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
        IFileDialog_SetTitle   = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p)(vt[21])
        IFileDialog_SetTitle(p, c_wchar_p("请选择资金数据目录"))
        IModalWindow_Show      = ctypes.WINFUNCTYPE(c_long, c_void_p, c_void_p)(vt[3])
        hr = IModalWindow_Show(p, None)
        path = None
        if hr == 0:   # 用户选了文件夹
            IFileOpenDialog_GetResult = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(c_void_p))(vt[22])
            pItem = c_void_p()
            if IFileOpenDialog_GetResult(p, byref(pItem)) == 0 and pItem.value:
                # IShellItem vtable: 5=GetDisplayName
                sit = ctypes.cast(pItem.value, POINTER(c_void_p))
                IShellItem_GetDisplayName = ctypes.WINFUNCTYPE(c_long, c_void_p, c_int, POINTER(c_wchar_p))(sit[5])
                pStr = c_wchar_p()
                if IShellItem_GetDisplayName(pItem.value, SIGDN_FILESYSPATH, ctypes.byref(pStr)) == 0 and pStr.value:
                    path = pStr.value
                ole32.CoTaskMemFree(pStr)
                ctypes.WINFUNCTYPE(c_ulong, c_void_p)(sit[2])(pItem.value)  # Release
        ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vt[2])(p)  # Release
        ole32.CoUninitialize()
        return path
    except Exception:  # noqa: BLE001
        try:
            ole32.CoUninitialize()
        except Exception:
            pass
        return None


def _fund_db():
    """懒加载 SQLite, 进程内单连接(后台线程 + 简单事务足够桌面应用)"""
    global FUND_DB_CONN
    if FUND_DB_CONN is None:
        os.makedirs(os.path.dirname(FUND_DB_PATH), exist_ok=True)
        FUND_DB_CONN = sqlite3.connect(FUND_DB_PATH, check_same_thread=False, isolation_level=None)
        FUND_DB_CONN.row_factory = sqlite3.Row
        FUND_DB_CONN.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                strategy    TEXT    NOT NULL,
                year        INTEGER NOT NULL,
                month       INTEGER NOT NULL,
                initial_equity REAL NOT NULL,
                end_equity    REAL NOT NULL,
                cash_flow     REAL NOT NULL DEFAULT 0,
                cash          REAL NOT NULL DEFAULT 0,
                note        TEXT,
                monthly_pnl REAL,
                month_return_rate REAL,
                created_at  TEXT,
                updated_at  TEXT,
                PRIMARY KEY (strategy, year, month)
            )
            """
        )
        # 兼容旧库: 若缺少 cash 列则 ALTER 添加
        cols = [row[1] for row in FUND_DB_CONN.execute("PRAGMA table_info(records)").fetchall()]
        if "cash" not in cols:
            FUND_DB_CONN.execute("ALTER TABLE records ADD COLUMN cash REAL NOT NULL DEFAULT 0")
    return FUND_DB_CONN


def _calc_record_metrics(initial_equity, end_equity, cash_flow):
    """根据用户输入自动推算: 本月盈亏 + 本月收益率
    公式 (用户明确约定):
      本月盈亏 = 本月末权益 - 月初权益 + 出入金   (出金为正)
      本月收益率 = 本月盈亏 / 月初权益
    """
    monthly_pnl = end_equity - initial_equity + cash_flow
    rate = monthly_pnl / initial_equity if initial_equity > 0 else 0.0
    return monthly_pnl, rate


def _fund_record_to_dict(r):
    return {
        "strategy": r["strategy"],
        "year": r["year"],
        "month": r["month"],
        "record_date": "%04d-%02d-01" % (r["year"], r["month"]),  # 用月初当作时间戳; UI 实际显示年月
        "initial_equity": r["initial_equity"],
        "end_equity": r["end_equity"],
        "cash_flow": r["cash_flow"],
        "cash": r["cash"] if "cash" in r.keys() else 0.0,
        "monthly_pnl": r["monthly_pnl"] or 0.0,
        "month_return_rate": r["month_return_rate"] or 0.0,
        "note": r["note"] or "",
    }


def fund_list_records(strategy=None):
    db = _fund_db()
    # 默认按年月降序: 最新在最上(用户阅读顺序)
    if strategy:
        rows = db.execute(
            "SELECT * FROM records WHERE strategy=? ORDER BY year DESC, month DESC",
            (strategy,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM records ORDER BY strategy, year DESC, month DESC"
        ).fetchall()
    return [_fund_record_to_dict(r) for r in rows]


def fund_upsert(strategy, year, month, initial_equity, end_equity, cash_flow, note, cash=0):
    if strategy not in FUND_STRATEGIES:
        raise ValueError("未知策略: %s (支持: %s)" % (strategy, FUND_STRATEGIES))
    if not (1 <= month <= 12):
        raise ValueError("月份必须 1-12")
    initial_equity = float(initial_equity)
    end_equity = float(end_equity)
    cash_flow = float(cash_flow or 0)
    cash = float(cash or 0)
    monthly_pnl, rate = _calc_record_metrics(initial_equity, end_equity, cash_flow)
    now = datetime.now().isoformat(timespec="seconds")
    db = _fund_db()
    try:
        db.execute("BEGIN")
        db.execute(
            """
            INSERT INTO records (strategy, year, month, initial_equity, end_equity,
                                 cash_flow, cash, note, monthly_pnl, month_return_rate,
                                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(strategy, year, month) DO UPDATE SET
              initial_equity=excluded.initial_equity,
              end_equity=excluded.end_equity,
              cash_flow=excluded.cash_flow,
              cash=excluded.cash,
              note=excluded.note,
              monthly_pnl=excluded.monthly_pnl,
              month_return_rate=excluded.month_return_rate,
              updated_at=excluded.updated_at
            """,
            (strategy, year, month, initial_equity, end_equity, cash_flow, cash, note,
             monthly_pnl, rate, now, now),
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return True


def fund_delete(strategy, year, month):
    db = _fund_db()
    db.execute(
        "DELETE FROM records WHERE strategy=? AND year=? AND month=?",
        (strategy, year, month),
    )


def fund_clear_all():
    """一键清除所有记录(双策略都清)"""
    db = _fund_db()
    db.execute("DELETE FROM records")
    return db.execute("SELECT COUNT(*) FROM records").fetchone()[0]  # 返回剩余(应为0)


def fund_clear_all_safe():
    """带错误检测的清除, 返回 (ok, msg); 区分只读/被锁等具体原因"""
    import os as _os
    db_path = FUND_DB_PATH
    db_dir = _os.path.dirname(db_path)
    # 1. 目录可写?
    if not _os.access(db_dir, _os.W_OK):
        return False, "数据目录不可写（可能指向了网盘或被锁的路径）：%s\n请在「⚙ 数据位置」里改回默认（%%APPDATA%%\\OpenCalc\\data）" % db_dir
    # 2. 文件存在?
    if _os.path.exists(db_path):
        if not _os.access(db_path, _os.W_OK):
            return False, "数据库文件被设为只读或被其他程序占用：%s\n请关闭其他 OpenCalc.exe 实例后重试，或右键文件去掉「只读」属性" % db_path
    # 3. SQLite 检测: 试写一行再回滚, 确认连接可写
    try:
        db = _fund_db()
        # SQLite PRAGMA quick_check + journal_mode
        row = db.execute("PRAGMA quick_check").fetchone()
        if row and row[0] != "ok":
            return False, "数据库已损坏: %s\n请尝试「⬇ 导出备份」后改用其他数据目录" % (row[0],)
        # 试执行 DELETE 后立刻 ROLLBACK, 检测是否只读
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM records")
            db.execute("ROLLBACK")
        except Exception as e:
            db.execute("ROLLBACK")
            err = str(e).lower()
            if "readonly" in err or "locked" in err or "lock" in err:
                return False, "数据库被锁/只读: %s\n请检查:\n① 是否多个 OpenCalc.exe 在运行(任务管理器结束重复进程)\n② 数据目录文件属性是否有「只读」\n③ 数据目录是否在网盘(网盘未同步会临时锁文件)" % str(e)
            raise
    except Exception as e:
        return False, "数据库连接失败: %s" % str(e)
    # 4. 实际执行清除
    try:
        db.execute("DELETE FROM records")
        n = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return True, n
    except Exception as e:
        return False, "清除失败: %s" % str(e)


def fund_yearly_summary(strategy):
    """年度汇总 = 由月度明细自动累加
    年初权益 = 该年首个有记录的月份的"月初权益"
    年末权益 = 该年最后一个有记录的月份的"本月末权益"
    总出入金 = sum(月度出入金)
    年度总盈亏 = (年末权益 - 年初权益 + 总出入金)
    年化收益率 = 年度总盈亏 / 年初权益
    """
    rows = fund_list_records(strategy)
    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)
    summary = []
    # 年度按降序: 最新年份在最上
    for year in sorted(by_year.keys(), reverse=True):
        recs = sorted(by_year[year], key=lambda x: x["month"])
        if not recs:
            continue
        initial_eq = recs[0]["initial_equity"]
        end_eq = recs[-1]["end_equity"]
        total_cf = sum(m["cash_flow"] for m in recs)
        yearly_pnl = end_eq - initial_eq + total_cf
        annualized = yearly_pnl / initial_eq if initial_eq > 0 else 0.0
        summary.append({
            "year": year,
            "initial_equity": initial_eq,
            "end_equity": end_eq,
            "total_cash_flow": total_cf,
            "yearly_pnl": yearly_pnl,
            "annualized_return_rate": annualized,
            "month_count": len(recs),
        })
    return summary


def fund_combined_summary():
    """汇总: 各策略按月合并, 缺失月份用最近月末权益延续
    例: abe 8月 月末70k, 9月无记录 → 9月 abe 视为延续(月初/月末=70k, 出金=0)
    再与其他策略当月数据相加, 得汇总月初/月末/出入金
    """
    all_records = fund_list_records()
    if not all_records:
        return []
    by_strategy = {}
    for r in all_records:
        by_strategy.setdefault(r["strategy"], []).append(r)
    for s in by_strategy:
        by_strategy[s].sort(key=lambda x: (x["year"], x["month"]))

    months = sorted({(r["year"], r["month"]) for r in all_records})

    def _latest_before(recs, year, month):
        latest = None
        for r in recs:
            if r["year"] < year or (r["year"] == year and r["month"] < month):
                latest = r
            else:
                break
        return latest

    out = []
    for year, month in months:
        active = set()
        for s, recs in by_strategy.items():
            if any(r["year"] == year and r["month"] == month for r in recs):
                active.add(s)
        if not active:
            continue
        total_init = total_end = total_cf = 0
        carried = []
        for s, recs in by_strategy.items():
            cur = next((r for r in recs if r["year"] == year and r["month"] == month), None)
            if cur:
                total_init += cur["initial_equity"]
                total_end += cur["end_equity"]
                total_cf += cur["cash_flow"]
            else:
                prev = _latest_before(recs, year, month)
                if prev:
                    total_init += prev["end_equity"]
                    total_end += prev["end_equity"]
                    carried.append(s)
        if total_init == 0 and total_end == 0:
            continue
        total_pnl = total_end - total_init + total_cf
        total_rate = total_pnl / total_init if total_init > 0 else 0.0
        out.append({
            "year": year,
            "month": month,
            "initial_equity": total_init,
            "end_equity": total_end,
            "cash_flow": total_cf,
            "monthly_pnl": total_pnl,
            "month_return_rate": total_rate,
            "strategies": sorted(list(active)),
            "carried_strategies": carried,
        })
    return out


def fund_dashboard(strategy):
    """仪表盘数据: 给出每月/每年的所有绘图字段"""
    monthly = fund_list_records(strategy)
    yearly = fund_yearly_summary(strategy)
    return {"monthly": monthly, "yearly": yearly}


def fund_withdrawal_summary():
    """各策略累计提现(出金为正的 cash_flow 合计) + 汇总
    约定: cash_flow 正 = 出金(提现), 负 = 入金; 只累加出金
    """
    db = _fund_db()
    result = {}
    for strategy in FUND_STRATEGIES:
        rows = db.execute(
            "SELECT cash_flow FROM records WHERE strategy=?", (strategy,)
        ).fetchall()
        result[strategy] = round(sum(max(float(r[0] or 0), 0.0) for r in rows), 2)
    result["combined"] = round(result.get(FUND_STRATEGIES[0], 0) + result.get(FUND_STRATEGIES[1], 0), 2)
    return result


def fund_export_backup():
    """导出全部记录为备份 JSON (跨电脑迁移 / 定期备份用)"""
    return {
        "app": "期货开仓计算器",
        "backup_version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "records": fund_list_records(),
    }


def fund_import_backup(payload):
    """导入备份: 逐条 upsert 合并 (相同 strategy/year/month 覆盖, 其余保留)
    返回 (导入条数, 涉及策略列表)
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("备份文件格式不正确（缺少 records 列表）")
    records = payload["records"]
    if not records:
        return 0, []
    strategies = set()
    imported = 0
    for r in records:
        if not isinstance(r, dict):
            raise ValueError("备份条目格式不正确")
        strategy = r.get("strategy")
        try:
            year = int(r["year"])
            month = int(r["month"])
            initial_equity = float(r["initial_equity"])
            end_equity = float(r["end_equity"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("备份条目缺少必要字段（strategy/year/month/initial_equity/end_equity）")
        cash_flow = float(r.get("cash_flow") or 0)
        cash = float(r.get("cash") or 0)
        note = str(r.get("note") or "")
        fund_upsert(strategy, year, month, initial_equity, end_equity, cash_flow, note, cash=cash)
        strategies.add(strategy)
        imported += 1
    return imported, sorted(strategies)


def fund_combined_dashboard():
    monthly = fund_combined_summary()
    # 年度汇总
    by_year = {}
    for r in monthly:
        by_year.setdefault(r["year"], []).append(r)
    yearly = []
    for year in sorted(by_year.keys()):
        recs = sorted(by_year[year], key=lambda x: x["month"])
        if not recs:
            continue
        initial_eq = recs[0]["initial_equity"]
        end_eq = recs[-1]["end_equity"]
        total_cf = sum(m["cash_flow"] for m in recs)
        total_pnl = end_eq - initial_eq + total_cf
        yearly.append({
            "year": year,
            "initial_equity": initial_eq,
            "end_equity": end_eq,
            "total_cash_flow": total_cf,
            "yearly_pnl": total_pnl,
            "annualized_return_rate": total_pnl / initial_eq if initial_eq > 0 else 0.0,
        })
    return {"monthly": monthly, "yearly": yearly}


# ===========================================================================
# HTTP 服务
# ===========================================================================
def _json(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


FAVICON = None
CHARTJS_BYTES = None


def _favicon_bytes():
    """读取打包进 exe 的 icon.ico (作为页面 favicon / 窗口图标)"""
    global FAVICON
    if FAVICON is None:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "icon.ico"), "rb") as f:
            FAVICON = f.read()
    return FAVICON


def _chartjs_bytes():
    """读取打包进 exe 的 chart.min.js (本地 Chart.js, 不依赖网络)"""
    global CHARTJS_BYTES
    if CHARTJS_BYTES is None:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "chart.min.js"), "rb") as f:
            CHARTJS_BYTES = f.read()
    return CHARTJS_BYTES


# 静态资源(收款码等) MIME 映射
_ASSET_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _read_asset_bytes(name):
    """读取 assets/<name>(开发=源码目录, 打包=_MEIPASS 临时目录), 返回 (bytes, mime) 或 (None, None)"""
    # 安全: 禁止路径穿越
    safe = (name or "").replace("\\", "/").lstrip("/")
    if not safe or ".." in safe or safe.startswith("/"):
        return None, None
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    fp = os.path.join(base, "assets", safe)
    if not os.path.isfile(fp):
        return None, None
    ext = os.path.splitext(fp)[1].lower()
    return open(fp, "rb").read(), _ASSET_MIME.get(ext, "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenPosCalc/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默日志

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.server.last_request_time = time.time()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/contracts":
            self._send(200, _json({"ok": True, "contracts": contract_list()}))
        elif path == "/api/health":
            self._send(200, _json({"ok": True, "app": APP_NAME}))
        elif path == "/favicon.ico":
            try:
                self._send(200, _favicon_bytes(), "image/x-icon")
            except Exception:  # noqa: BLE001
                self._send(404, _json({"ok": False, "error": "icon not found"}))
        elif path == "/chart.min.js":
            try:
                self._send(200, _chartjs_bytes(), "application/javascript; charset=utf-8")
            except Exception:  # noqa: BLE001
                self._send(404, _json({"ok": False, "error": "chart.js not bundled"}))
        elif path == "/api/quote":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            code = (qs.get("code") or [""])[0].strip()
            if not code:
                self._send(200, _json({"ok": False, "error": "缺少 code 参数"}))
                return
            try:
                q = get_quote(code)
                self._send(200, _json({"ok": True, **q}))
            except Exception as e:  # noqa: BLE001
                self._send(200, _json({"ok": False, "error": "行情获取失败: %s" % e}))
        elif path == "/api/shutdown":
            self._send(200, _json({"ok": True, "msg": "应用已退出"}))
            threading.Timer(0.5, lambda: os._exit(0)).start()

        # ---------- 资金曲线模块 ----------
        elif path == "/api/funds/strategies":
            self._send(200, _json({"ok": True, "strategies": FUND_STRATEGIES}))

        elif path == "/api/funds/withdrawals":
            self._send(200, _json({"ok": True, **fund_withdrawal_summary()}))

        elif path == "/api/funds/records":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            strategy = (qs.get("strategy") or [None])[0]
            self._send(200, _json({"ok": True, "records": fund_list_records(strategy)}))

        elif path == "/api/funds/yearly":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            strategy = (qs.get("strategy") or [None])[0]
            self._send(200, _json({"ok": True, "yearly": fund_yearly_summary(strategy)}))

        elif path == "/api/funds/dashboard":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            strategy = (qs.get("strategy") or [None])[0]
            self._send(200, _json({"ok": True, **({"combined": True, **fund_combined_dashboard()} if strategy in (None, "combined") else fund_dashboard(strategy))}))

        elif path == "/api/funds/combined":
            self._send(200, _json({"ok": True, "records": fund_combined_summary()}))

        elif path == "/api/funds/export":
            self._send(200, _json({"ok": True, **fund_export_backup()}))

        elif path == "/api/funds/data-info":
            self._send(200, _json({"ok": True, **fund_data_info()}))

        elif path == "/api/settings":
            self._send(200, _json({"ok": True, "settings": get_settings()}))

        elif path.startswith("/api/assets/"):
            name = path[len("/api/assets/"):]
            data, ctype = _read_asset_bytes(name)
            if data is None:
                self._send(404, _json({"ok": False, "error": "asset not found"}))
            else:
                self._send(200, data, ctype)

        else:
            self._send(404, _json({"ok": False, "error": "Not Found"}))

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            self._send(200, _json({"ok": False, "error": "JSON 解析失败: %s" % e}))
            return

        try:
            if path == "/api/calc/futures":
                result = calc_futures(params)
            elif path == "/api/calc/options":
                result = calc_options(params)
            elif path == "/api/funds/records":
                action = params.get("action", "upsert")
                if action == "delete":
                    fund_delete(params["strategy"], int(params["year"]), int(params["month"]))
                    self._send(200, _json({"ok": True, "msg": "已删除"}))
                else:
                    fund_upsert(
                        params["strategy"],
                        int(params["year"]),
                        int(params["month"]),
                        params["initial_equity"],
                        params["end_equity"],
                        params.get("cash_flow", 0),
                        params.get("note", ""),
                        cash=params.get("cash", 0),
                    )
                    self._send(200, _json({"ok": True, "msg": "已保存"}))
                return
            elif path == "/api/funds/import":
                imported, strategies = fund_import_backup(params)
                self._send(200, _json({
                    "ok": True,
                    "msg": "导入完成",
                    "imported": imported,
                    "strategies": strategies,
                }))
                return
            elif path == "/api/funds/clear-all":
                ok, info = fund_clear_all_safe()
                if ok:
                    self._send(200, _json({"ok": True, "remaining": info, "msg": "已清除全部记录"}))
                else:
                    self._send(200, _json({"ok": False, "error": info}))
                return
            elif path == "/api/funds/data-dir":
                migrated, status = fund_set_data_dir(params.get("dir", ""))
                self._send(200, _json({
                    "ok": True,
                    "data_dir": os.path.dirname(FUND_DB_PATH),
                    "migrated": migrated,
                    "status": status,
                    "msg": "数据位置已更新" if status != "same" else "当前已是该位置，无需迁移",
                }))
                return
            elif path == "/api/funds/browse":
                p = browse_folder()
                if p:
                    self._send(200, _json({"ok": True, "path": p}))
                else:
                    self._send(200, _json({"ok": False, "error": "未选择文件夹（已取消）"}))
                return
            elif path == "/api/settings":
                self._send(200, _json({"ok": True, "settings": save_settings(params)}))
                return
            elif path == "/api/pin":
                pinned = set_window_pin(bool(params.get("pin", True)))
                self._send(200, _json({"ok": True, "pinned": pinned}))
                return
            else:
                self._send(404, _json({"ok": False, "error": "Not Found"}))
                return
            self._send(200, _json(result))
        except (ValueError, KeyError) as e:
            self._send(200, _json({"ok": False, "error": str(e)}))
        except Exception as e:  # noqa: BLE001
            self._send(200, _json({"ok": False, "error": "请求处理失败: %s" % e}))


def find_free_port(prefer=8765):
    """优先使用 prefer 端口, 被占用则随机分配; 支持环境变量 OC_PORT 强制指定(测试/多开用)"""
    try:
        prefer = int(os.environ.get("OC_PORT") or prefer)
    except (TypeError, ValueError):
        pass
    for port in (prefer, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            actual = s.getsockname()[1]
            s.close()
            return actual
        except OSError:
            continue
    return 0


def _find_browser():
    """查找可用于 --app 独立窗口模式的浏览器 (Chrome 优先, 其次 Edge)"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    la = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    candidates = [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(la, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(la, "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _screen_left_half():
    """返回窗口尺寸与位置, 使软件窗口默认占主显示器左半边(全高, 减少上下翻):
    (width, height, x, y); 获取失败时回退默认 1180x840 居中(位置 None)
    """
    try:
        if os.name == "nt":
            import ctypes
            u = ctypes.windll.user32
            sw = u.GetSystemMetrics(48)   # SM_CXWORKAREA 工作区宽(排除任务栏)
            sh = u.GetSystemMetrics(49)   # SM_CYWORKAREA 工作区高
            if sw >= 800 and sh >= 500:   # 下限保护, 异常小值(无头/受限会话)回退默认
                return sw // 2, sh, 0, 0
    except Exception:  # noqa: BLE001
        pass
    return 1180, 840, None, None


def open_browser(url):
    """优先 Chromium --app 独立窗口(无地址栏, 体验接近原生软件); 失败回退系统默认浏览器"""
    browser = _find_browser()
    if browser:
        try:
            w, h, x, y = _screen_left_half()
            args = [browser, "--app=%s" % url, "--window-size=%d,%d" % (w, h)]
            if x is not None:
                args.append("--window-position=%d,%d" % (x, y))
            subprocess.Popen(args, close_fds=True)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def idle_watchdog(server, timeout=IDLE_EXIT_SECONDS):
    """长时间无请求自动退出, 避免后台残留进程"""
    while True:
        time.sleep(20)
        if time.time() - server.last_request_time > timeout:
            os._exit(0)


def main():
    port = find_free_port()
    url = "http://127.0.0.1:%d/" % port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.last_request_time = time.time()

    # 空闲自动退出(用户关闭窗口后自动回收进程)
    threading.Thread(target=idle_watchdog, args=(server,), daemon=True).start()
    # 延迟打开浏览器, 确保服务先就绪
    threading.Timer(0.4, open_browser, args=(url,)).start()

    if getattr(sys, "frozen", False):
        import ctypes
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("期货开仓计算器 - 端口 %d" % port)
        except Exception:  # noqa: BLE001
            pass

    print("服务已启动: %s  (关闭窗口后 15 分钟无操作自动退出)" % url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ===========================================================================
# 前端界面 (内嵌 HTML)
# ===========================================================================
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>期货开仓计算器</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<script src="/chart.min.js"></script>
<style>
:root{
  --bg:#0b1020; --panel:#141b33; --panel2:#1a2342; --border:rgba(148,163,255,.14);
  --text:#e8ecff; --sub:#9aa6c8; --accent:#5b8cff; --accent2:#7de3ff;
  --good:#2ecc8f; --bad:#ff6b6b; --warn:#ffb86b; --gold:#f5c76b;
  --shadow:0 18px 50px rgba(0,0,0,.45);
}
[data-theme="light"]{
  --bg:#eef1f8; --panel:#ffffff; --panel2:#f4f6fd; --border:#dfe4f2;
  --text:#1c2340; --sub:#66708f; --accent:#3b6cf6; --accent2:#0e9fc8;
  --good:#16a06b; --bad:#e05252; --warn:#d98a1f; --gold:#b8860b;
  --shadow:0 18px 44px rgba(30,50,120,.10);
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  transition:background .35s,color .35s;
  background-image:
    radial-gradient(900px 420px at 85% -10%, rgba(91,140,255,.16), transparent 60%),
    radial-gradient(700px 380px at -10% 30%, rgba(125,227,255,.10), transparent 55%);
}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}

/* 顶部 */
header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:26px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:14px}
.logo{width:46px;height:46px;border-radius:14px;flex:none;
  background:linear-gradient(135deg,#5b8cff,#7de3ff);
  display:flex;align-items:center;justify-content:center;font-size:22px;
  box-shadow:0 8px 24px rgba(91,140,255,.4)}
.brand h1{font-size:21px;letter-spacing:.5px}
.brand p{font-size:12px;color:var(--sub);margin-top:2px}
.topbtns{display:flex;align-items:center;gap:10px}
.iconbtn{width:38px;height:38px;border-radius:11px;border:1px solid var(--border);
  background:var(--panel);color:var(--text);cursor:pointer;font-size:16px;
  transition:transform .2s,background .2s;display:flex;align-items:center;justify-content:center}
.iconbtn:hover{transform:translateY(-2px);background:var(--panel2)}
.iconbtn.pinned{background:linear-gradient(135deg,#f5b942,#d98a1f);color:#fff;border-color:transparent;
  box-shadow:0 6px 16px rgba(217,138,31,.4)}

/* 模式切换 */
.modes{display:grid;grid-template-columns:1fr 1fr;gap:10px;background:var(--panel);
  border:1px solid var(--border);border-radius:16px;padding:6px;margin-bottom:22px}
.mode{padding:13px;text-align:center;border-radius:12px;cursor:pointer;font-weight:600;
  color:var(--sub);transition:all .28s;user-select:none;font-size:15px;position:relative}
.mode small{display:block;font-weight:400;font-size:11px;margin-top:2px;opacity:.75}
.mode.active{background:linear-gradient(135deg,#a78bfa,#7c3aed);color:#fff;
  box-shadow:0 10px 26px rgba(139,92,246,.4)}
.mode.active small{opacity:.9}
.mode:not(.active):hover{background:var(--panel2);color:var(--text)}

/* 主体布局 */
.grid{display:grid;grid-template-columns:400px 1fr;gap:22px;align-items:start}
@media(max-width:900px){.grid{grid-template-columns:1fr}}

.card{background:var(--panel);border:1px solid var(--border);border-radius:20px;
  padding:22px;box-shadow:var(--shadow)}
.card h2{font-size:14px;color:var(--sub);font-weight:600;letter-spacing:1px;
  margin-bottom:16px;display:flex;align-items:center;gap:8px}
.card h2 .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}

label{display:block;font-size:12.5px;color:var(--sub);margin:14px 0 6px}
input,select{width:100%;padding:11px 13px;border-radius:11px;border:1px solid var(--border);
  background:var(--panel2);color:var(--text);font-size:15px;outline:none;
  transition:border .2s,box-shadow .2s;font-family:inherit}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,140,255,.18)}
input::placeholder{color:var(--sub);opacity:.55}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:stretch}
.row2>div{display:flex;flex-direction:column}
.row2>div .unit-suffix{margin-top:auto}
.unit-suffix{position:relative}
.unit-suffix input{padding-right:44px}
.unit-suffix .u{position:absolute;right:12px;top:50%;transform:translateY(-50%);
  color:var(--sub);font-size:12px;pointer-events:none}
select{cursor:pointer;appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,var(--sub) 50%),linear-gradient(135deg,var(--sub) 50%,transparent 50%);
  background-position:calc(100% - 18px) 50%,calc(100% - 13px) 50%;
  background-size:5px 5px;background-repeat:no-repeat}

/* 持仓方向 双按钮(与标题同行, 做多红 / 做空青) */
.dirrow{display:flex;align-items:center;gap:14px;margin:16px 0 2px}
.dirrow .dirlabel{font-size:12.5px;color:var(--sub);flex:none}
.dseg{flex:1;display:flex;gap:4px;background:var(--panel);border:1px solid var(--border);
  padding:3px;border-radius:11px}
.dseg button{flex:1;border:0;background:transparent;padding:7px 0;border-radius:8px;cursor:pointer;
  font-size:13.5px;font-weight:700;color:var(--sub);transition:background .15s,color .15s}
.dseg button:hover{color:var(--text)}
.dseg button[data-dir="long"].active{background:rgba(255,91,91,.16);color:#ff6b6b}
.dseg button[data-dir="short"].active{background:rgba(70,214,234,.15);color:#46d6ea}
[data-theme="light"] .dseg button[data-dir="long"].active{background:rgba(224,82,82,.14);color:#e05252}
[data-theme="light"] .dseg button[data-dir="short"].active{background:rgba(14,159,200,.13);color:#0e9fc8}

/* 结果区 */
.results{display:flex;flex-direction:column;gap:16px}
.budgetbar{display:flex;align-items:baseline;justify-content:space-between;
  background:var(--panel2);border:1px solid var(--border);border-radius:14px;padding:14px 18px}
.budgetbar .k{font-size:12.5px;color:var(--sub)}
.budgetbar .k .fml{display:block;margin-top:3px;font-size:11px;color:var(--accent);font-weight:600;letter-spacing:.2px}
.budgetbar .v{font-size:24px;font-weight:700;color:var(--accent2)}
.budgetbar .v small{font-size:12px;color:var(--sub);font-weight:400;margin-left:4px}

.hud{display:grid;grid-template-columns:1.2fr 1fr;gap:16px}
.bignum{background:var(--panel2);border:1px solid var(--border);border-radius:18px;
  padding:20px;text-align:center;position:relative;overflow:hidden}
.bignum::before{content:"";position:absolute;inset:0;
  background:radial-gradient(160px 90px at 50% -10%,rgba(91,140,255,.22),transparent 70%)}
.bignum .t{font-size:12px;color:var(--sub);position:relative}
.bignum .n{font-size:46px;font-weight:800;position:relative;line-height:1.15;
  background:linear-gradient(135deg,#fff,#b9ccff);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
[data-theme="light"] .bignum .n{background:linear-gradient(135deg,#24356e,#3b6cf6);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.bignum .s{font-size:11px;color:var(--sub);position:relative}

.badge{display:inline-flex;align-items:center;gap:8px;padding:12px 22px;border-radius:14px;
  font-size:17px;font-weight:700;letter-spacing:1px}
.badge .ico{font-size:20px}
.badge.good{background:rgba(46,204,143,.14);color:var(--good);border:1px solid rgba(46,204,143,.35)}
.badge.bad{background:rgba(255,107,107,.13);color:var(--bad);border:1px solid rgba(255,107,107,.35)}
.ratio-strip{display:flex;align-items:center;justify-content:space-between;gap:14px;
  background:var(--panel2);border:1px solid var(--border);border-radius:16px;padding:16px 20px;flex-wrap:wrap}
.ratio-strip .l{font-size:12.5px;color:var(--sub)}
.ratio-strip .r{font-size:32px;font-weight:800}
.ratio-strip .r small{font-size:12px;color:var(--sub);font-weight:400}

/* 明细表格 */
.details{background:var(--panel2);border:1px solid var(--border);border-radius:16px;overflow:hidden}
.details .drow{display:flex;justify-content:space-between;padding:11px 18px;font-size:13.5px;
  border-bottom:1px dashed var(--border)}
.details .drow:last-child{border-bottom:none}
.details .k{color:var(--sub)}
.details .v{font-weight:600}
.details .v.good{color:var(--good)} .details .v.bad{color:var(--bad)} .details .v.warn{color:var(--warn)} .details .v.gold{color:var(--gold)}

/* 阶梯止盈 (期货, 独立方块) */
.ladder-block{border:1px solid var(--border);border-radius:16px;padding:14px 16px;
  background:linear-gradient(135deg,rgba(91,140,255,.07),transparent 60%)}
.ladder-hd{display:flex;align-items:baseline;justify-content:space-between;gap:10px;
  flex-wrap:wrap;margin-bottom:10px}
.ladder-hd .lb{font-size:13px;font-weight:700;letter-spacing:.4px}
.ladder-hd .ladder-sub{font-size:11.5px;color:var(--sub);font-weight:400;margin-left:4px}
.ladder-hd .ladder-note{font-size:11px;color:var(--sub)}
.ladder-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
@media(max-width:680px){.ladder-grid{grid-template-columns:repeat(2,1fr)}}
.rung{background:var(--panel2);border:1px solid var(--border);border-radius:12px;
  padding:10px 12px;display:flex;flex-direction:column;gap:2px;min-width:0}
.rung .rt{display:flex;align-items:center;justify-content:space-between;font-size:11px;color:var(--sub)}
.rung .rt b{font-size:12.5px;color:var(--text)}
.rung .rt .ad{font-size:13px}
.rung .rq{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums;
  line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rung .rq.long{color:#ff6b6b}
.rung .rq.short{color:#46d6ea}
[data-theme="light"] .rung .rq.long{color:#e05252}
[data-theme="light"] .rung .rq.short{color:#0e9fc8}
.rung .rp{font-size:10.5px;color:var(--sub)}
.rung .rp b{color:var(--bad);font-weight:600}

/* 最近方案 (期货, 最多3组) */
.plans-empty{font-size:12px;color:var(--sub);background:var(--panel2);border:1px dashed var(--border);
  border-radius:10px;padding:10px 14px;line-height:1.6}
.plans-item{display:flex;align-items:center;gap:12px;background:var(--panel2);
  border:1px solid var(--border);border-radius:12px;padding:9px 14px;cursor:pointer;
  transition:border-color .15s,transform .15s}
.plans-item:hover{border-color:var(--accent);transform:translateY(-1px)}
.plans-item .nm{font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.plans-item .nm .d{font-weight:800;margin:0 2px}
.plans-item .nm .d.long{color:#ff6b6b}
.plans-item .nm .d.short{color:#46d6ea}
[data-theme="light"] .plans-item .nm .d.long{color:#e05252}
[data-theme="light"] .plans-item .nm .d.short{color:#0e9fc8}
.plans-item .meta{font-size:11px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.plans-item .go{margin-left:auto;flex:none;font-size:12px;font-weight:600;color:var(--accent);
  background:rgba(91,140,255,.1);border:1px solid rgba(91,140,255,.35);border-radius:8px;padding:4px 12px}
.plans-item:hover .go{background:rgba(91,140,255,.2)}
.plans-item .go:active{transform:scale(.96)}

.tip{margin-top:16px;font-size:12px;color:var(--sub);line-height:1.8;
  background:var(--panel2);border:1px solid var(--border);border-radius:12px;padding:12px 16px}
.tip b{color:var(--accent2);font-weight:600}

.warnbox{border:1px solid rgba(255,184,107,.4);background:rgba(255,184,107,.08);
  border-radius:14px;padding:12px 16px;font-size:13px;color:var(--warn);line-height:1.7}
.errorbox{border:1px solid rgba(255,107,107,.4);background:rgba(255,107,107,.08);
  border-radius:14px;padding:12px 16px;font-size:13px;color:var(--bad);line-height:1.7}

/* 搜索选择器 */
.searchbox{position:relative}
.slist{position:absolute;top:calc(100% + 6px);left:0;right:0;background:var(--panel2);
  border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow);z-index:50;
  max-height:280px;overflow-y:auto}
.sitem{padding:10px 14px;cursor:pointer;font-size:13.5px;display:flex;gap:8px;align-items:baseline;
  border-bottom:1px dashed var(--border)}
.sitem:last-child{border-bottom:none}
.sitem:hover{background:var(--accent);color:#fff}
.sitem b{font-family:Consolas,monospace}
.sitem .dim{color:var(--sub);font-size:11.5px;margin-left:auto;white-space:nowrap}
.sitem:hover .dim{color:rgba(255,255,255,.85)}

/* 行情卡片 */
.quote{display:flex;align-items:center;gap:10px;margin-top:12px;background:var(--panel2);
  border:1px solid var(--border);border-radius:12px;padding:10px 14px;flex-wrap:wrap}
.quote .qname{font-size:12px;color:var(--sub)}
.quote .qprice{font-size:20px;font-weight:700}
.quote .qchg{font-size:12.5px;font-weight:600;padding:3px 9px;border-radius:7px}
.quote .qchg.up{color:#ff5b5b;background:rgba(255,91,91,.13)}
.quote .qchg.down{color:#2ecc8f;background:rgba(46,204,143,.13)}
.quote .qchg.flat{color:var(--sub);background:rgba(154,166,200,.14)}
.qref{margin-left:auto;border:1px solid var(--border);background:var(--panel);color:var(--sub);
  font-size:11.5px;padding:4px 10px;border-radius:8px;cursor:pointer;transition:all .2s}
.qref:hover{color:var(--text);border-color:var(--accent)}
.qerr{font-size:11.5px;color:var(--bad)}

.hidden{display:none!important}
footer{margin-top:34px;text-align:center;font-size:11.5px;color:var(--sub);opacity:.7;line-height:1.8}
.money{font-variant-numeric:tabular-nums}

/* 左侧竖条导航 (开仓计算 / 资金曲线) */
.app-shell{display:flex;min-height:100vh}
.side{width:96px;flex:none;display:flex;flex-direction:column;gap:14px;align-items:center;
  padding:20px 10px;background:var(--panel);border-right:1px solid var(--border);
  position:sticky;top:0;height:100vh;z-index:50}
.maintab{width:100%;padding:16px 4px;text-align:center;border-radius:14px;cursor:pointer;
  font-weight:600;font-size:13px;color:var(--sub);transition:all .25s;user-select:none;
  display:flex;flex-direction:column;align-items:center;gap:6px;line-height:1.2}
.maintab .mi{font-size:22px;line-height:1}
.maintab small{font-weight:400;font-size:10px;opacity:.72}
.maintab.active{background:linear-gradient(135deg,#5b8cff,#3b6cf6);color:#fff;
  box-shadow:0 10px 26px rgba(91,140,255,.35)}
.maintab.active small{opacity:.92}
.maintab:not(.active):hover{background:var(--panel2);color:var(--text)}
.main{flex:1;min-width:0}
@media (max-width:640px){
  .side{width:64px;padding:16px 6px;gap:10px}
  .maintab{font-size:11px;padding:12px 2px}
  .maintab .mi{font-size:18px}
  .maintab small{display:none}
}

/* 资金曲线 */
.funds-bar{display:flex;gap:8px;align-items:center;margin-bottom:18px;flex-wrap:nowrap;white-space:nowrap}
.funds-bar .btn.sm{padding:5px 10px;font-size:12px}
.funds-bar .seg button{padding:6px 12px;font-size:12.5px}
.funds-bar .seg{display:flex;gap:4px;background:var(--panel);border:1px solid var(--border);
  border-radius:12px;padding:4px}
.funds-bar .seg button{padding:8px 16px;border-radius:8px;cursor:pointer;border:0;
  background:transparent;color:var(--sub);font-weight:600;font-size:13px;transition:all .2s}
.funds-bar .seg button.active{background:linear-gradient(135deg,#5b8cff,#3b6cf6);color:#fff}
.funds-bar .seg button:not(.active):hover{background:var(--panel2);color:var(--text)}
/* 累计提现展示 */
.wchip{padding:4px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;font-size:11.5px;white-space:nowrap}
.wchip b{color:var(--accent)}
.btn{padding:10px 18px;border-radius:11px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;cursor:pointer;transition:all .2s;font-size:13.5px}
.btn:hover{transform:translateY(-1px);background:var(--panel);border-color:var(--accent)}
.btn.primary{background:linear-gradient(135deg,#5b8cff,#3b6cf6);border:0;color:#fff;
  box-shadow:0 6px 18px rgba(59,108,246,.3)}
.btn.primary:hover{box-shadow:0 10px 24px rgba(59,108,246,.45)}
.btn.danger{background:rgba(255,107,107,.12);border-color:rgba(255,107,107,.3);color:var(--bad)}
.btn.danger:hover{background:rgba(255,107,107,.22)}
.btn.sm{padding:6px 12px;font-size:12px}
.btn.xs{padding:3px 10px;font-size:11px;border-radius:8px;vertical-align:middle}
.btn.ghost{background:transparent;border-color:var(--border);color:var(--sub)}
.btn.ghost:hover{color:var(--accent);border-color:var(--accent)}

/* 期权品种单选按钮组 */
.chipgroup{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px}
.chip{padding:8px 14px;border-radius:10px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;font-size:12.5px;cursor:pointer;transition:all .2s;user-select:none}
.chip small{display:block;font-weight:400;font-size:10px;opacity:.65;margin-top:1px}
.chip:hover{border-color:var(--accent);transform:translateY(-1px)}
.chip.active{background:linear-gradient(135deg,#5b8cff,#3b6cf6);border-color:transparent;color:#fff;
  box-shadow:0 6px 16px rgba(59,108,246,.35)}
.chip.active small{opacity:.85}
/* 常用区 (favorites): 横向 chip 列表, 悬停右上角 X 可删除 */
.freq{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 6px;align-items:center}
.freq .lbl{font-size:12px;color:var(--sub);margin-right:4px}
.fchip{position:relative;padding:6px 24px 6px 12px;border-radius:9px;border:1px solid var(--border);
  background:var(--panel2);color:var(--text);font-size:12.5px;cursor:pointer;transition:all .2s;user-select:none}
.fchip:hover{border-color:var(--accent)}
.fchip.active{background:linear-gradient(135deg,#5b8cff,#3b6cf6);border-color:transparent;color:#fff}
.fchip .x{position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;
  background:var(--bad);color:#fff;font-size:11px;line-height:18px;text-align:center;
  opacity:0;transition:opacity .15s;font-weight:700;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.3)}
.fchip:hover .x{opacity:1}
.fchip .x:hover{background:#c93b3b}
/* 资金曲线表头点击排序 */
.tbl th.sortable{cursor:pointer;user-select:none}
.tbl th.sortable:hover{color:var(--accent)}
.tbl th .sort-arrow{font-size:10px;margin-left:4px;opacity:.7}

/* 左下角浮动联系作者按钮 */
.floating-contact{position:fixed;bottom:20px;left:20px;width:50px;height:50px;border-radius:50%;
  background:linear-gradient(135deg,#5b8cff,#3b6cf6);color:#fff;border:0;cursor:pointer;
  box-shadow:0 6px 20px rgba(91,140,255,.45);font-size:22px;z-index:100;transition:transform .2s,box-shadow .2s}
.floating-contact:hover{transform:scale(1.1);box-shadow:0 8px 26px rgba(91,140,255,.6)}

.tbl{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
.tbl th{text-align:left;padding:10px 10px;color:var(--sub);font-weight:600;font-size:11.5px;
  letter-spacing:.5px;border-bottom:1px solid var(--border);background:var(--panel2);
  position:sticky;top:0;z-index:2}   /* 表头固定: 滚动时首行不消失 */
.tbl td{padding:11px 10px;border-bottom:1px solid var(--border)}
.tbl tr:hover td{background:var(--panel2)}
.tbl .num{text-align:right;font-variant-numeric:tabular-nums}
/* 月度明细限高滚动(记录多时默认只显示最近 5 条, 其余可滚动) */
.tbl-scroll{max-height:420px;overflow-y:auto;overflow-x:auto}
.tbl-scroll tr.hidden{display:none}
/* 图表放大按钮 */
.chartbox .ct{display:flex;align-items:center;gap:8px}
.zoombtn{margin-left:auto;font-size:11.5px}
/* 中国习惯: 盈利=红, 亏损=绿 (与开仓模块一致) */
.tbl .pos{color:var(--bad)}
.tbl .neg{color:var(--good)}
.tbl .actions{text-align:right}
.tbl .actions button{margin-left:4px}

.fund-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}
@media(max-width:900px){.fund-grid{grid-template-columns:1fr}}
.chartbox{background:var(--panel);border:1px solid var(--border);border-radius:18px;
  padding:18px;box-shadow:var(--shadow)}
.chartbox .ct{font-size:13.5px;font-weight:600;letter-spacing:.5px;margin-bottom:8px;
  display:flex;align-items:center;gap:8px;color:var(--text)}
.chartbox .ct .dot{width:8px;height:8px;border-radius:50%;background:var(--accent2)}
.chartbox .legend{display:flex;gap:14px;font-size:11.5px;color:var(--sub);flex-wrap:wrap;margin-bottom:6px}
.chartbox .legend .lg{display:flex;align-items:center;gap:5px}
.chartbox .legend .lg i{display:inline-block;width:11px;height:11px;border-radius:3px}
.chartbox canvas{width:100%!important;height:260px!important}

.modalbg{position:fixed;inset:0;background:rgba(8,12,28,.55);backdrop-filter:blur(6px);
  z-index:999;display:flex;align-items:center;justify-content:center;animation:pop .18s ease}
.modal{background:var(--panel);border:1px solid var(--border);border-radius:18px;
  padding:22px 24px;width:min(480px,90vw);box-shadow:var(--shadow);animation:pop .25s cubic-bezier(.16,1,.3,1)}
.modal h3{margin-bottom:16px;font-size:16px;display:flex;align-items:center;gap:8px}
.modal .row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.modal .modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.selpill{padding:6px 14px;border-radius:20px;background:var(--panel2);border:1px solid var(--border);
  font-size:12.5px;color:var(--sub);cursor:pointer;transition:.2s}
.selpill.active{background:linear-gradient(135deg,#5b8cff,#3b6cf6);color:#fff;border:0}

/* 金额自动换算"万"提示 */
.wanhint{font-size:11px;color:var(--sub);margin-top:5px;min-height:15px;letter-spacing:.3px}
.wanhint b{color:var(--accent);font-weight:600}
.wanhint .auto{color:var(--gold)}
input[readonly]{background:var(--panel2);color:var(--sub);cursor:not-allowed}
.autohint{font-size:11px;color:var(--gold);margin-top:3px}

@keyframes pop{0%{transform:scale(.96);opacity:0}100%{transform:scale(1);opacity:1}}
.anim{animation:pop .35s cubic-bezier(.16,1,.3,1)}
</style>
</head>
<body data-theme="dark">
<div class="app-shell">
  <aside class="side" id="mainTabs">
    <div class="maintab active" data-tab="calc"><span class="mi">🧮</span><span class="mt">开仓计算</span><small>期货 · 期权</small></div>
    <div class="maintab" data-tab="funds"><span class="mi">📈</span><span class="mt">资金曲线</span><small>abe · 威科夫</small></div>
  </aside>
  <div class="main">
  <div class="wrap">

  <header>
    <div class="brand">
      <div class="logo">◈</div>
      <div>
        <h1 id="appTitle">期货开仓计算器</h1>
        <p id="appSubtitle">风控仓位计算 · 盈亏比决策 · 保证金测算</p>
      </div>
    </div>
    <div class="topbtns">
      <button class="iconbtn" id="pinBtn" title="窗口置顶（始终显示在其他窗口之上）">📌</button>
      <button class="iconbtn" id="themeBtn" title="切换主题">🌙</button>
      <button class="iconbtn" id="exitBtn" title="退出应用">✕</button>
    </div>
  </header>

  <div id="calcArea">
    <div class="modes">
      <div class="mode active" data-mode="futures">期货模式<small>风险额度 0.5%~3% 可选 · 盈亏比决策</small></div>
      <div class="mode" data-mode="options">期权模式<small>风险额度 0.5%~3% 可选 · 权利金占用</small></div>
    </div>

  <div class="grid">
    <!-- 输入区 -->
    <div class="card">
      <h2><span class="dot"></span>开仓参数</h2>

      <label>当前总权益（万元）
        <button class="btn xs ghost" id="btnDefEquity" title="把当前填写的权益保存为默认值，下次打开自动填入" style="margin-left:8px">存为默认</button>
      </label>
      <div class="unit-suffix">
        <input id="equity" type="number" min="0" step="any" placeholder="例如：100（=100 万元）" inputmode="decimal">
        <span class="u">万元</span>
      </div>
      <div class="wanhint" id="wanEquity"></div>
      <div class="tip hidden" id="defEquityHint"></div>

      <div id="futuresFields">
        <label>风险额度（占权益的百分比，单选）
          <button class="btn xs ghost" id="btnDefRisk" title="把当前选中的百分比保存为默认，下次打开自动选中" style="margin-left:8px">设为默认</button>
        </label>
        <select id="riskAmount">
          <option value="0.5">0.5%</option>
          <option value="1" selected>1%</option>
          <option value="1.5">1.5%</option>
          <option value="2">2%</option>
          <option value="3">3%</option>
        </select>
        <div class="wanhint" id="riskHint" style="color:var(--sub)"></div>

        <label>开仓标的（代码 / 中文搜索）</label>
        <div class="searchbox" style="display:flex;gap:6px;align-items:stretch">
          <input id="cSearch" placeholder="搜索代码/名称，如 rb / 螺纹钢" autocomplete="off" style="flex:1">
          <button class="btn xs ghost" id="favF" title="把当前选中的标的加入常用">★ 设为常用</button>
          <div class="slist hidden" id="cList" style="left:0;right:0"></div>
        </div>
        <div class="freq" id="freqF"><div class="tip" style="margin:2px 0 6px">常用：暂未设置，先在搜索框选好标的后点「★ 设为常用」</div></div>
        <div class="quote hidden" id="quoteF" title="行情来自新浪财经，约20秒延迟，与交易软件可能存在细微差异；价格自动每10秒刷新">
          <span class="qname" id="qNameF">—</span>
          <span class="qprice" id="qPriceF">—</span>
          <span class="qchg" id="qChgF"></span>
          <button class="qref" id="qRefF">刷新</button>
        </div>

        <div class="dirrow">
          <span class="dirlabel">持仓方向</span>
          <div class="dseg" id="dirSeg">
            <button type="button" class="active" data-dir="long">做多</button>
            <button type="button" data-dir="short">做空</button>
          </div>
        </div>

        <div class="row2">
          <div>
            <label>开仓价</label>
            <div class="unit-suffix">
              <input id="entry" type="number" min="0" step="any" inputmode="decimal" placeholder="0.00">
              <span class="u" id="unitF">—</span>
            </div>
          </div>
          <div>
            <label>保证金率</label>
            <div class="unit-suffix">
              <input id="marginRate" type="number" min="1" max="100" step="any" value="16">
              <span class="u">%</span>
            </div>
          </div>
        </div>

        <div class="row2">
          <div>
            <label>止损价</label>
            <div class="unit-suffix">
              <input id="stop" type="number" min="0" step="any" inputmode="decimal" placeholder="0.00">
              <span class="u">—</span>
            </div>
          </div>
          <div>
            <label>止盈价</label>
            <div class="unit-suffix">
              <input id="target" type="number" min="0" step="any" inputmode="decimal" placeholder="0.00">
              <span class="u">—</span>
            </div>
          </div>
        </div>
        <button class="btn xs ghost" id="btnClearPrices" title="一键清空开仓价、止损价、止盈价，重新输入" style="margin-top:10px">🧹 清空价格</button>
        <div class="tip">合约乘数、保证金占用等数据已内置常用品种，选择标的后自动带出。<b>价格请手动输入最新行情。</b>开仓/止损/止盈价可点输入框上下箭头，按该品种最小变动价位（1 跳）步进调节。</div>
        <div class="wanhint" id="tickHint" style="color:var(--sub)"></div>
      </div>

      <div id="optionsFields" class="hidden">
        <label>开仓标的（搜索 / 选常用）</label>
        <div class="searchbox" style="display:flex;gap:6px;align-items:stretch">
          <input id="cSearchO" placeholder="搜索代码/名称，如 si / 工业硅" autocomplete="off" style="flex:1">
          <button class="btn xs ghost" id="favO" title="把当前选中的标的加入常用">★ 设为常用</button>
          <div class="slist hidden" id="cListO" style="left:0;right:0"></div>
        </div>
        <div class="freq" id="freqO"><div class="tip" style="margin:2px 0 6px">常用：暂未设置，先在搜索框选好标的后点「★ 设为常用」</div></div>
        <div class="quote hidden" id="quoteO" title="行情来自新浪财经，约20秒延迟，与交易软件可能存在细微差异；价格自动每10秒刷新">
          <span class="qname" id="qNameO">—</span>
          <span class="qprice" id="qPriceO">—</span>
          <span class="qchg" id="qChgO"></span>
          <button class="qref" id="qRefO">刷新</button>
        </div>

        <label>风险额度（占权益的百分比，单选）
          <button class="btn xs ghost" id="btnDefRiskO" title="把当前选中的百分比保存为默认，下次打开自动选中" style="margin-left:8px">设为默认</button>
        </label>
        <select id="riskAmountO">
          <option value="0.5">0.5%</option>
          <option value="1">1%</option>
          <option value="1.5">1.5%</option>
          <option value="2">2%</option>
          <option value="3" selected>3%</option>
        </select>
        <div class="wanhint" id="riskHintO" style="color:var(--sub)"></div>

        <label>开仓价 / 每手权利金（1 手价格，已含乘数）</label>
        <div class="unit-suffix">
          <input id="entryO" type="number" min="0" step="any" inputmode="decimal" placeholder="0.00">
          <span class="u" id="unitO">元/手</span>
        </div>
        <div class="tip">开仓金额 = 权益 × 所选百分比。<b>开仓价 = 1 手期权价格（已含合约乘数）</b>，手数 = 预算 ÷ 每手价格，按权利金全额占用资金。</div>
      </div>
    </div>

    <!-- 结果区 -->
    <div class="card results">
      <h2><span class="dot"></span>测算结果</h2>
      <div id="empty" class="tip" style="text-align:center;padding:34px 16px">
        请填写左侧参数，结果将实时计算显示
      </div>

      <div id="resultF" class="hidden">
        <div class="ratio-strip anim">
          <span class="l">开仓额度 · 当前风险额度</span>
          <span class="badge good" id="rIvBadgeF"><span class="ico">◈</span>权益 × 1%</span>
        </div>
        <div class="budgetbar anim">
          <span class="k">可投入开仓金额（预算）<span class="fml" id="rFormulaF"></span></span>
          <span class="v money" id="rBudgetF">—</span>
        </div>
        <div class="hud">
          <div class="bignum anim">
            <div class="t">建议最大开仓手数</div>
            <div class="n" id="rLotsF">—</div>
            <div class="s">手 · 按风险金额倒推</div>
          </div>
          <div class="bignum anim">
            <div class="t">盈亏比</div>
            <div class="n" id="rRatioF">—</div>
            <div class="s">止盈距离 ÷ 止损距离</div>
          </div>
        </div>
        <div class="ratio-strip anim" id="rBadgeWrap">
          <span class="l" id="rBadgeLabel">决策建议</span>
          <span class="badge good hidden" id="rBadgeGood"><span class="ico">✓</span>可以参与</span>
          <span class="badge bad hidden" id="rBadgeBad"><span class="ico">✕</span>不建议参与</span>
        </div>
        <div class="warnbox hidden" id="rWarn"></div>
        <div class="details anim" id="rDetailF">
          <div class="drow"><span class="k">开仓标的</span><span class="v" id="rContractF">—</span></div>
          <div class="drow"><span class="k">合约乘数</span><span class="v" id="rMultF">—</span></div>
          <div class="drow"><span class="k">每手保证金（参考占用，开仓价 × 乘数 × 16%）</span><span class="v money" id="rMarginF">—</span></div>
          <div class="drow"><span class="k">每手风险金额（止损价差 × 乘数）</span><span class="v money good" id="rRiskF">—</span></div>
          <div class="drow"><span class="k">每手止盈金额（止盈价差 × 乘数）</span><span class="v money bad" id="rRewardF">—</span></div>
          <div class="drow"><span class="k">实际最大风险金额（每手风险 × 手数，≤ 预算）</span><span class="v money good" id="rRiskUsedF">—</span></div>
          <div class="drow"><span class="k">按最大手数止盈可盈利（每手止盈 × 手数）</span><span class="v money bad" id="rMaxRewardF">—</span></div>
          <div class="drow"><span class="k">最大占用保证金（每手 × 手数）</span><span class="v money gold" id="rMarginUsedF">—</span></div>
        </div>
        <!-- 阶梯止盈: 以止损价差为 1R, 2R~5R 逐级目标价 (独立方块) -->
        <div class="ladder-block anim" id="rLadderF">
          <div class="ladder-hd">
            <span class="lb">🪜 阶梯止盈参考<span class="ladder-sub" id="rLadderDirF"></span></span>
            <span class="ladder-note" id="rLadderNoteF"></span>
          </div>
          <div class="ladder-grid" id="rLadderGridF"></div>
        </div>
      </div>

      <div id="resultO" class="hidden">
        <div class="ratio-strip anim">
          <span class="l">开仓额度</span>
          <span class="badge good" id="rIvBadge"><span class="ico">◈</span>固定 权益 × 3%</span>
        </div>
        <div class="budgetbar anim" style="margin-top:14px">
          <span class="k">可投入开仓金额</span>
          <span class="v money" id="rBudgetO">—</span>
        </div>
        <div class="hud" style="margin-top:16px">
          <div class="bignum anim">
            <div class="t">建议最大开仓手数</div>
            <div class="n" id="rLotsO">—</div>
            <div class="s">手 · 按权利金（=最大亏损）倒推</div>
          </div>
          <div class="bignum anim">
            <div class="t">每手权利金（=输入开仓价）</div>
            <div class="n" id="rPremiumO" style="font-size:30px">—</div>
            <div class="s">1 手价格 · 已含乘数</div>
          </div>
        </div>
        <div class="details anim" id="rDetailO" style="margin-top:16px">
          <div class="drow"><span class="k">开仓标的</span><span class="v" id="rContractO">—</span></div>
          <div class="drow"><span class="k">期权合约乘数</span><span class="v" id="rMultO">—</span></div>
          <div class="drow"><span class="k">占用资金（权利金 × 手数）</span><span class="v money gold" id="rFundsO">—</span></div>
        </div>
        <div class="warnbox hidden" id="rWarnO"></div>
        <div class="tip">期权买入不占用保证金，资金按权利金全额占用。期权乘数请以交易所最新规定为准。</div>
      </div>

      <!-- 最近保存的期货方案 (最多3组, 一键调出; 仅期货模式显示) -->
      <div id="recentPlans" class="hidden" style="border-top:1px dashed var(--border);padding-top:14px;margin-top:2px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">
          <span style="font-size:12.5px;color:var(--sub);letter-spacing:.5px">最近方案 <small style="opacity:.75">（最多保留最近 3 组）</small></span>
          <button class="btn xs ghost" id="btnSavePlan" style="margin-left:auto" title="把当前开仓参数（标的/方向/价格/保证金率/风险额度/权益）保存为方案，点击「调出」一键恢复并重算">💾 保存当前方案</button>
        </div>
        <div id="planList" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>
    </div>
  </div>

  <footer>数据仅供风控参考，不构成投资建议 · 保证金率与乘数以交易所最新公告为准<br>本应用本地运行，数据不出本机</footer>
  </div><!-- /calcArea -->

  <!-- ============================================================ -->
  <!-- 资金曲线模块                                                  -->
  <!-- ============================================================ -->
  <div id="fundsArea" class="hidden">
    <div class="funds-bar">
      <div style="font-weight:600;color:var(--sub);font-size:12.5px;letter-spacing:.5px">策略</div>
      <div class="seg" id="stratSeg">
        <button class="active" data-strategy="abe">abe</button>
        <button data-strategy="威科夫">威科夫</button>
        <button data-strategy="combined">汇总</button>
      </div>
      <!-- 各策略累计提现(出金) 展示 -->
      <div id="withdrawBox" style="display:flex;gap:6px;align-items:center;flex-wrap:nowrap;margin-left:8px;font-size:11.5px;white-space:nowrap">
        <span style="color:var(--sub)">提现</span>
        <span class="wchip" id="wdAbe">abe ¥0</span>
        <span class="wchip" id="wdWk">威科夫 ¥0</span>
        <span class="wchip" id="wdAll">汇总 ¥0</span>
      </div>
      <div style="margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:nowrap">
        <button class="btn danger sm" id="btnClearAll" title="一键清除所有策略的全部记录(不可恢复)">🗑 清除全部</button>
        <button class="btn sm" id="btnDataDir" title="把数据存到网盘同步文件夹，换电脑不丢记录">⚙ 数据位置</button>
        <button class="btn sm" id="btnExport" title="导出全部记录为备份文件（可用于换电脑迁移/定期备份）">⬇ 导出</button>
        <button class="btn sm" id="btnImport" title="从备份文件恢复记录（相同年月会覆盖）">⬆ 导入</button>
        <button class="btn primary sm" id="btnAddRecord">＋ 记录月度</button>
        <input type="file" id="importFile" accept=".opcalc,.json,application/json" class="hidden">
      </div>
    </div>

    <!-- 月度明细表 -->
    <div class="card" style="margin-bottom:18px">
      <h2 style="display:flex;align-items:center;gap:10px"><span class="dot"></span><span id="monthlyTitle">abe · 月度明细</span>
        <button class="btn sm" id="btnShowAll" style="margin-left:auto" title="记录较多时默认只显示最近 5 条">显示全部</button>
      </h2>
      <div class="tbl-scroll">
        <table class="tbl" id="tblMonthly">
          <thead><tr>
            <th class="sortable" id="thSortMonthly">年月 <span class="sort-arrow">↓</span></th>
            <th class="num">月初权益</th>
            <th class="num">本月末权益</th>
            <th class="num">出入金</th>
            <th class="num">本月现金</th>
            <th class="num">本月盈亏</th>
            <th class="num">本月收益率</th>
            <th>备注</th>
            <th class="actions">操作</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div id="monthlyEmpty" class="tip" style="text-align:center;padding:30px">暂无记录，点击「＋ 记录月度」开始</div>
    </div>

    <!-- 年度汇总表 -->
    <div class="card" style="margin-bottom:18px">
      <h2><span class="dot"></span><span id="yearlyTitle">abe · 年度汇总</span></h2>
      <div style="overflow-x:auto">
        <table class="tbl" id="tblYearly">
          <thead><tr>
            <th class="sortable" id="thSortYearly">年份 <span class="sort-arrow">↓</span></th>
            <th class="num">年初权益</th>
            <th class="num">年末权益</th>
            <th class="num">总出入金</th>
            <th class="num">年度总盈亏</th>
            <th class="num">年化收益率</th>
            <th class="num">记录月数</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div id="yearlyEmpty" class="tip" style="text-align:center;padding:24px">仅在录入 2 个月以上时显示年度汇总</div>
    </div>

    <!-- 仪表盘 / 图表区 -->
    <div class="fund-grid">
      <div class="chartbox">
        <div class="ct"><span class="dot"></span><span id="chartMonthlyTitle">abe · 月收益图</span>
          <button class="btn sm zoombtn" data-zoom="monthly" title="放大查看">⤢ 放大</button></div>
        <div class="legend">
          <span class="lg"><i style="background:#5b8cff"></i>本月末权益（扣掉出入金之后）</span>
          <span class="lg"><i style="background:#7de3ff"></i>本月盈亏</span>
          <span class="lg"><i style="background:#ff5b9b"></i>本月收益率（折线，右轴）</span>
        </div>
        <canvas id="chartMonthly"></canvas>
      </div>
      <div class="chartbox">
        <div class="ct"><span class="dot"></span><span id="chartYearlyTitle">abe · 年盈亏分析</span>
          <button class="btn sm zoombtn" data-zoom="yearly" title="放大查看">⤢ 放大</button></div>
        <div class="legend">
          <span class="lg"><i style="background:#5b8cff"></i>年末权益</span>
          <span class="lg"><i style="background:#7de3ff"></i>年度总盈亏</span>
          <span class="lg"><i style="background:#ff5b9b"></i>年化收益率（折线，右轴）</span>
        </div>
        <canvas id="chartYearly"></canvas>
      </div>
    </div>
  </div><!-- /fundsArea -->

  <!-- 图表放大 弹窗 -->
  <div class="modalbg hidden" id="chartZoomBg">
    <div class="modal" style="width:min(1120px,94vw);max-height:92vh">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <h3 style="margin:0"><span class="dot"></span><span id="chartZoomTitle">图表</span></h3>
        <span class="dim" style="font-size:12px;margin-left:auto">滚动查看更清晰</span>
        <button class="btn" id="chartZoomClose">关闭</button>
      </div>
      <div class="legend" id="chartZoomLegend"></div>
      <div style="position:relative;height:66vh"><canvas id="chartZoom"></canvas></div>
    </div>
  </div>

  <!-- 记录月度 弹窗 -->
  <div class="modalbg hidden" id="modalBg">
    <div class="modal">
      <h3><span class="dot"></span><span id="modalTitle">记录月度权益</span></h3>
      <div style="color:var(--sub);font-size:12px;margin-bottom:14px">出金记为正数（如取现 1 万填 10000），入金记为负数</div>
      <div class="row2">
        <div>
          <label>年份</label>
          <input id="fldYear" type="number" placeholder="2026" min="2000" max="2100">
        </div>
        <div>
          <label>月份</label>
          <input id="fldMonth" type="number" placeholder="1-12" min="1" max="12">
        </div>
      </div>
      <label>月初权益</label>
      <input id="fldInit" type="number" inputmode="decimal" step="any" placeholder="如 100000">
      <div class="wanhint" id="wanInit"></div>
      <label>本月末权益（扣掉出入金之后）</label>
      <input id="fldEnd" type="number" inputmode="decimal" step="any" placeholder="如 105000">
      <div class="wanhint" id="wanEnd"></div>
      <label>出入金（出金是正，如取现 1 万填 10000）</label>
      <input id="fldCF" type="number" inputmode="decimal" step="any" placeholder="0（不填则按 0）">
      <div class="wanhint" id="wanCF"></div>
      <label>本月现金（自定义记录，如当月现金余额）</label>
      <input id="fldCash" type="number" inputmode="decimal" step="any" placeholder="0（不填则按 0）">
      <div class="wanhint" id="wanCash"></div>
      <label>备注（可选）</label>
      <input id="fldNote" type="text" placeholder="如 11月建仓" maxlength="80">
      <div class="modal-actions">
        <button class="btn" id="btnCancel">取消</button>
        <button class="btn primary" id="btnSave">保存</button>
      </div>
    </div>
  </div>

  <!-- 数据位置设置 弹窗 -->
  <div class="modalbg hidden" id="setBg">
    <div class="modal">
      <h3><span class="dot"></span>⚙ 数据位置设置</h3>
      <div style="color:var(--sub);font-size:12.5px;line-height:1.8;margin-bottom:10px">
        当前数据目录：<b id="setCurDir" style="color:var(--text);word-break:break-all">—</b><br>
        当前记录：<b id="setCurCnt" style="color:var(--accent)">—</b> 条
      </div>
      <label>新的数据目录（建议填<u>网盘同步文件夹</u>，如 百度网盘/OneDrive/坚果云 的某个目录）</label>
      <div style="display:flex;gap:8px">
        <input id="setDir" type="text" placeholder="点击「浏览」选择文件夹，或手动输入路径" autocomplete="off" style="flex:1">
        <button class="btn" id="setBrowse" style="flex:none">浏览…</button>
      </div>
      <div class="tip" style="margin-top:10px">保存后，现有记录会<b>自动迁移（合并）</b>到新位置，不会丢失。<br>
        换电脑时：新电脑装好网盘客户端同步该文件夹 → 在这里填<b>同一个路径</b> → 记录自动恢复。</div>
      <div class="modal-actions">
        <button class="btn" id="setCancel">取消</button>
        <button class="btn primary" id="setSave">保存并迁移</button>
      </div>
    </div>
  </div>

  <!-- 联系作者 modal -->
  <div class="modalbg hidden" id="contactBg">
    <div class="modal" style="max-width:680px">
      <h3><span class="dot"></span>💬 联系作者</h3>
      <div style="margin:8px 0 18px">
        <div style="font-size:13.5px;color:var(--sub);margin-bottom:10px">1. 知乎：<a href="https://www.zhihu.com/people/zhang-xu-11-6-63" target="_blank" style="color:var(--accent);text-decoration:underline"><b>假装很稳定</b></a></div>
        <div style="font-size:13.5px;color:var(--sub);margin-bottom:14px">2. 支持一下（扫码即可）：</div>
        <div style="display:flex;gap:18px;justify-content:center;flex-wrap:wrap">
          <div style="text-align:center">
            <img src="/api/assets/qrcode-wechat.png" alt="微信收款码" style="width:200px;height:200px;border-radius:12px;background:#fff;padding:6px;box-shadow:0 4px 12px rgba(0,0,0,.25)">
            <div style="margin-top:8px;font-size:12px;color:var(--sub)">微信</div>
          </div>
          <div style="text-align:center">
            <img src="/api/assets/qrcode-alipay.png" alt="支付宝收款码" style="width:200px;height:200px;border-radius:12px;background:#fff;padding:6px;box-shadow:0 4px 12px rgba(0,0,0,.25)">
            <div style="margin-top:8px;font-size:12px;color:var(--sub)">支付宝</div>
          </div>
        </div>
      </div>
      <div class="tip">本应用开源分享，你的支持是持续维护的最大动力 🙌</div>
      <div class="modal-actions">
        <button class="btn" id="contactClose">关闭</button>
      </div>
    </div>
  </div>

</div><!-- /wrap -->
  </div><!-- /main -->
</div><!-- /app-shell -->

<!-- 左下角浮动联系作者按钮 -->
<button class="floating-contact" id="floatingContact" title="联系作者">💬</button>

<script>
const $ = id => document.getElementById(id);
let CONTRACTS = [];
let curMode = 'futures';
const selCode = {F: null, O: null};   // 当前选中的标的代码
let dirF = 'long';                     // 期货持仓方向(做多红/做空青 双按钮)

const fmt = v => (v==null||isNaN(v)) ? '—' : Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2,minimumFractionDigits:0});
const fmtMoney = v => (v==null||isNaN(v)) ? '—' : '¥ ' + Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
/* 数字 → 精简显示: 3800 → "3800", 3800.50 → "3800.5", 1.0 → "1" */
const fmtTrim = v => {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return n.toFixed(2).replace(/\.?0+$/, '');
};

/* fetch 带超时(默认8秒), 避免行情网络慢时界面卡住 */
function fetchT(url, opts, ms){
  const ctrl = new AbortController();
  const t = setTimeout(()=>ctrl.abort(), ms || 8000);
  return fetch(url, Object.assign({signal:ctrl.signal}, opts)).finally(()=>clearTimeout(t));
}

function init(){
  fetchT('/api/contracts').then(r=>r.json()).then(d=>{
    if(!d.ok) return;
    CONTRACTS = d.contracts;
    initContractSearch('cSearch','cList','freqF','favF','futures', c => pickContract(c,'F'));
    initContractSearch('cSearchO','cListO','freqO','favO','options', c => pickContract(c,'O'));
  });
  loadTheme();
  loadSettings();
  bindWanEquity();            // 权益输入单位=万元, 实时显示对应元金额
  // 风险额度预算提示(期货 + 期权共用, 内部按 mode 取对应 select 的值)
  function updateRiskHint(mode){
    const eq = (parseFloat($('equity').value) || 0) * 10000;   // 万元 → 元
    const defPct = mode === 'options' ? 3 : 1;
    const selId = mode === 'options' ? 'riskAmountO' : 'riskAmount';
    const hintId = mode === 'options' ? 'riskHintO' : 'riskHint';
    const pct = parseFloat($(selId).value);
    const hint = $(hintId);
    if (isNaN(eq) || eq <= 0) { hint.innerHTML = ''; return; }
    const p = isNaN(pct) || pct <= 0 ? defPct : pct;
    const budget = eq * p / 100;
    hint.innerHTML = '预算 = 权益 × ' + p + '% = <b style="color:var(--accent)">¥' + budget.toLocaleString('en-US', {maximumFractionDigits:2}) + '</b>';
  }
  function onRiskChange(mode){
    updateRiskHint(mode);
    onInput();   // 立即重新测算
  }
  $('equity').addEventListener('input', ()=>{ updateRiskHint('futures'); updateRiskHint('options'); });
  $('equity').addEventListener('change', ()=>{ updateRiskHint('futures'); updateRiskHint('options'); });
  $('riskAmount').addEventListener('change', ()=>onRiskChange('futures'));
  $('riskAmountO').addEventListener('change', ()=>onRiskChange('options'));
  // 存为默认: 权益 / 风险百分比(期货+期权)
  $('btnDefEquity').addEventListener('click', saveDefaultEquity);
  $('btnDefRisk').addEventListener('click', ()=>saveDefaultRisk('futures'));
  $('btnDefRiskO').addEventListener('click', ()=>saveDefaultRisk('options'));
  // 一键清空开仓/止损/止盈价
  $('btnClearPrices').addEventListener('click', ()=>{
    ['entry','stop','target'].forEach(id=>{ $(id).value=''; });
    updateTickHint();
    onInput();
  });
  updateRiskHint('futures');
  updateRiskHint('options');
}

/* ---- 默认值设置: 权益 / 期货风险百分比 (持久化到 config.json) ---- */
let settingsCache = {default_equity:null, futures_risk_pct:null, options_risk_pct:null, frequent_futures:[], frequent_options:[]};
function loadSettings(){
  fetchT('/api/settings').then(r=>r.json()).then(d=>{
    if(!d.ok) return;
    const s = d.settings || {};
    settingsCache = s;
    if (s.default_equity != null && !$('equity').value) {
      $('equity').value = s.default_equity / 10000;   // 元 → 万元
      bindWanEquity();
      const hint = $('defEquityHint');
      hint.classList.remove('hidden');
      hint.innerHTML = '✓ 已自动填入默认权益：<b>' + (s.default_equity/10000).toLocaleString('en-US',{maximumFractionDigits:2}) + ' 万元</b>（可点「存为默认」更换）';
    }
    if (s.futures_risk_pct != null) {
      const sel = $('riskAmount');
      const opts = [...sel.options].map(o=>parseFloat(o.value));
      if (opts.indexOf(parseFloat(s.futures_risk_pct)) >= 0) sel.value = String(s.futures_risk_pct);
    }
    if (s.options_risk_pct != null) {
      const sel = $('riskAmountO');
      const opts = [...sel.options].map(o=>parseFloat(o.value));
      if (opts.indexOf(parseFloat(s.options_risk_pct)) >= 0) sel.value = String(s.options_risk_pct);
    }
    // 刷新提示
    const evt = new Event('change'); $('equity').dispatchEvent(evt);
    // 渲染常用区
    if (typeof initContractSearch._renderFreqAll === 'function') initContractSearch._renderFreqAll();
    onInput();
  });
}
function saveDefaultEquity(){
  const eqWan = parseFloat($('equity').value);
  if (isNaN(eqWan) || eqWan <= 0) { alert('请先填写有效的权益金额'); return; }
  const eqYuan = eqWan * 10000;   // 万元 → 元(存储)
  fetchT('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({default_equity: eqYuan})}).then(r=>r.json()).then(d=>{
    if(d.ok) alert('✅ 已保存默认权益：' + eqWan.toLocaleString('en-US',{maximumFractionDigits:2}) + ' 万元，下次打开自动填入');
    else alert('保存失败：' + (d.error||''));
  });
}
function saveDefaultRisk(mode){
  const selId = mode === 'options' ? 'riskAmountO' : 'riskAmount';
  const key = mode === 'options' ? 'options_risk_pct' : 'futures_risk_pct';
  const label = mode === 'options' ? '期权' : '期货';
  const pct = parseFloat($(selId).value);
  fetchT('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({[key]: pct})}).then(r=>r.json()).then(d=>{
    if(d.ok) alert('✅ 已把 ' + label + ' ' + pct + '% 保存为默认风险额度，下次打开自动选中');
    else alert('保存失败：' + (d.error||''));
  });
}

/* 金额输入自动换算提示: 输入 1000000 → 显示 "= 100.00 万" */
function bindWanHint(inputId, hintId){
  const el = $(inputId), hint = $(hintId);
  if (!el || !hint) return;
  const update = ()=>{
    const v = parseFloat(el.value);
    hint.innerHTML = isNaN(v) ? '' : '≈ <b>' + (v/10000).toFixed(2) + '</b> 万';
  };
  el.addEventListener('input', update);
  el.addEventListener('change', update);
  update();
}

/* 权益输入(单位万元) → 实时显示对应元金额 */
function bindWanEquity(){
  const el = $('equity'), hint = $('wanEquity');
  if (!el || !hint) return;
  const update = ()=>{
    const v = parseFloat(el.value);
    hint.innerHTML = isNaN(v) ? '' : '= <b style="color:var(--accent)">¥' + (v*10000).toLocaleString('en-US', {maximumFractionDigits:0}) + '</b> 元';
  };
  el.addEventListener('input', update);
  el.addEventListener('change', update);
  update();
}

/* 搜索 + 常用组合控件: 输入搜索 + 选标的; ★ 设为常用加入常用区; 常用 chip 悬停右上角 X 删除 */
const _freqRenderers = [];   // 注册所有 freq 渲染器, settings 变化时统一刷新
function initContractSearch(inputId, listId, freqId, favBtnId, mode, onPick){
  const input = $(inputId), list = $(listId), favBtn = $(favBtnId), freqBox = $(freqId);
  const mKey = mode==='futures' ? 'F' : 'O';
  const freqKey = mode==='futures' ? 'frequent_futures' : 'frequent_options';
  let items = [];

  function renderFreq(){
    const codes = settingsCache[freqKey] || [];
    if (!codes.length){
      freqBox.innerHTML = '<div class="tip" style="margin:2px 0 6px">常用：暂未设置，先在搜索框选好标的后点「★ 设为常用」</div>';
      return;
    }
    let html = '<span class="lbl">常用：</span>';
    codes.forEach(code=>{
      const c = CONTRACTS.find(x=>x.code.toLowerCase() === code.toLowerCase());
      if (!c) return;
      const active = selCode[mKey] === c.code;
      html += '<span class="fchip' + (active ? ' active' : '') + '" data-code="' + c.code + '">' + c.name + ' <small style="opacity:.7">' + c.code + '</small><span class="x" title="从常用移除">×</span></span>';
    });
    freqBox.innerHTML = html;
    freqBox.querySelectorAll('.fchip').forEach(chip=>{
      chip.addEventListener('click', e=>{
        if (e.target.classList.contains('x')){
          removeFreq(chip.dataset.code);
          e.stopPropagation();
          return;
        }
        const c = CONTRACTS.find(x=>x.code === chip.dataset.code);
        if (c) pick(c);
      });
    });
  }
  _freqRenderers.push(renderFreq);

  function saveFreq(){
    return fetchT('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({[freqKey]: settingsCache[freqKey] || []})}).then(r=>r.json()).then(d=>{
      if (d.ok && d.settings) settingsCache = d.settings;
      _freqRenderers.forEach(fn=>fn());
    });
  }
  function addFreq(code){
    const cur = settingsCache[freqKey] || [];
    if (cur.indexOf(code) >= 0) { alert('该标的已在常用中'); return; }
    settingsCache[freqKey] = [...cur, code];
    saveFreq();
  }
  function removeFreq(code){
    settingsCache[freqKey] = (settingsCache[freqKey] || []).filter(x=>x !== code);
    saveFreq();
  }

  function render(q){
    q = (q||'').trim().toLowerCase();
    items = CONTRACTS.filter(c => !q || c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q));
    const top = items.slice(0, 14);
    if(!top.length){
      list.innerHTML = '<div class="sitem" style="cursor:default"><span class="dim">无匹配品种</span></div>';
    } else {
      list.innerHTML = top.map((c,i)=>`<div class="sitem" data-i="${i}">${c.name} <b>${c.code}</b><span class="dim">${c.exchange} · 乘数 ${mode==='futures'?c.mult:c.opt_mult}</span></div>`).join('');
    }
    list.classList.remove('hidden');
  }
  input.addEventListener('input', ()=>{ selCode[mKey] = null; render(input.value); });
  input.addEventListener('focus', ()=>render(input.value));
  input.addEventListener('keydown', e=>{
    if(e.key==='Enter' && items.length){ pick(items[0]); }
  });
  list.addEventListener('mousedown', e=>{
    const it = e.target.closest('.sitem');
    if(!it || it.dataset.i === undefined) return;
    pick(items[+it.dataset.i]);
  });
  document.addEventListener('click', e=>{
    if(!input.contains(e.target) && !list.contains(e.target) && !favBtn.contains(e.target)) list.classList.add('hidden');
  });
  function pick(c){
    input.value = c.name + ' ' + c.code + ' · ' + c.exchange;
    selCode[mKey] = c.code;
    list.classList.add('hidden');
    input.blur();
    onPick(c);
    _freqRenderers.forEach(fn=>fn());   // 刷新 active 状态
  }

  favBtn.addEventListener('click', ()=>{
    if (!selCode[mKey]) { alert('请先在搜索框里选好标的'); return; }
    addFreq(selCode[mKey]);
  });

  // 暴露统一刷新入口
  initContractSearch._renderFreqAll = ()=>_freqRenderers.forEach(fn=>fn());
  renderFreq();   // 初次渲染
}

function pickContract(c, which){
  if(which==='F'){
    $('unitF').textContent = c.unit.replace('吨/手','元/吨').replace('克/手','元/克').replace('千克/手','元/千克').replace('桶/手','元/桶');
    // 按品种最小变动价位设置价格步进(上下箭头 1 跳)
    ['entry','stop','target'].forEach(id=>{ $(id).step = c.tick; });
    updateTickHint();
    loadQuote(c.code, 'F');
  } else {
    $('unitO').textContent = '元/手';   // 期权开仓价 = 1手价格(已含合约乘数)
    loadQuote(c.code, 'O');
  }
  onInput();
}

/* 最小变动价位提示 + 价格整数倍软校验(不阻断计算) */
function updateTickHint(){
  const c = selCode.F ? CONTRACTS.find(x=>x.code===selCode.F) : null;
  if(!c){ $('tickHint').innerHTML=''; return; }
  const tick = c.tick;
  const names = {entry:'开仓价', stop:'止损价', target:'止盈价'};
  const bad = ['entry','stop','target'].filter(id=>{
    const v = parseFloat($(id).value);
    return !isNaN(v) && v>0 && Math.abs(v/tick - Math.round(v/tick)) > 1e-6;
  });
  if(bad.length){
    $('tickHint').innerHTML = '⚠ <b style="color:var(--bad)">' + bad.map(id=>names[id]).join('、') + '</b> 不是最小变动价位 ' + tick + ' 的整数倍，请按 ' + tick + ' 的倍数调整';
  } else {
    $('tickHint').innerHTML = '最小变动价位（1 跳）：<b style="color:var(--accent)">' + tick + '</b>，价格上下箭头按此步进调节';
  }
}

/* 主力合约行情 (具体合约, 价格+涨跌幅以昨收为基准; 约20秒延迟; 每10秒自动刷新) */
const quoteTimers = {F:null, O:null};
function loadQuote(code, which, silent){
  const box = which==='F' ? $('quoteF') : $('quoteO');
  const nameEl = which==='F' ? $('qNameF') : $('qNameO');
  const priceEl = which==='F' ? $('qPriceF') : $('qPriceO');
  const chgEl = which==='F' ? $('qChgF') : $('qChgO');
  clearInterval(quoteTimers[which]);
  if(!silent){
    box.classList.remove('hidden');
    nameEl.textContent = '行情加载中…';
    priceEl.textContent = '—'; chgEl.textContent = '';
  }
  fetchT('/api/quote?code='+encodeURIComponent(code)).then(r=>r.json()).then(d=>{
    if(!d.ok || d.latest <= 0){
      /* 拿不到最新价就不显示行情, 避免误导 */
      box.classList.add('hidden');
      return;
    }
    const c = CONTRACTS.find(x=>x.code===code);
    const cname = c ? c.name : code;
    const now = new Date();
    const diff = d.time ? Math.max(0, Math.round((now.getTime() - (new Date(now.toDateString()+' '+d.time)).getTime())/1000)) : null;
    nameEl.textContent = cname + ' 主力 ' + d.contract_code + ' · 更新 ' + d.time
      + (diff!=null ? '（约'+diff+'秒前）' : '');
    priceEl.textContent = fmt(d.latest);
    const up = d.change >= 0;
    chgEl.textContent = (up?'+':'')+d.change.toFixed(2)+'  '+(up?'+':'')+d.change_pct.toFixed(2)+'%';
    chgEl.className = 'qchg ' + (Math.abs(d.change_pct) < 0.005 ? 'flat' : (up ? 'up' : 'down'));
    /* 选中状态保持时每10秒自动刷新 */
    quoteTimers[which] = setInterval(()=>{
      if(selCode[which]===code) loadQuote(code, which, true);
    }, 10000);
  }).catch(()=>{
    /* 网络失败同样隐藏, 不显示误导价格 */
    box.classList.add('hidden');
  });
}
$('qRefF').addEventListener('click',()=>{ if(selCode.F) loadQuote(selCode.F,'F'); });
$('qRefO').addEventListener('click',()=>{ if(selCode.O) loadQuote(selCode.O,'O'); });

/* 模式切换 (期货/期权); 调出方案时也走这里 */
function setMode(m){
  document.querySelectorAll('.mode').forEach(x=>x.classList.toggle('active', x.dataset.mode===m));
  curMode = m;
  $('futuresFields').classList.toggle('hidden', curMode!=='futures');
  $('optionsFields').classList.toggle('hidden', curMode!=='options');
  if (typeof renderPlans === 'function') renderPlans();   // 最近方案区仅期货显示
  onInput();
}
document.querySelectorAll('.mode').forEach(m=>{
  m.addEventListener('click', ()=>setMode(m.dataset.mode));
});

/* 主题 */
function loadTheme(){
  const t = localStorage.getItem('oc-theme') || 'dark';
  applyTheme(t);
}
function applyTheme(t){
  document.body.dataset.theme = t;
  $('themeBtn').textContent = t==='dark' ? '☀️' : '🌙';
  localStorage.setItem('oc-theme', t);
}
$('themeBtn').addEventListener('click',()=>{
  applyTheme(document.body.dataset.theme==='dark' ? 'light' : 'dark');
});

/* 窗口置顶 (Always on Top) */
let pinned = localStorage.getItem('oc-pin') === '1';
function applyPinUI(){
  $('pinBtn').classList.toggle('pinned', pinned);
  $('pinBtn').title = pinned ? '已置顶，点击取消' : '窗口置顶（始终显示在其他窗口之上）';
}
function requestPin(on){
  return fetchT('/api/pin', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pin: on})}, 5000).then(r=>r.json()).then(d=>{
    if(!d.ok || !d.pinned) alert('置顶设置失败（可能窗口已被移动，请重试）');
  }).catch(()=>{ /* 服务未就绪则仅记住本地状态 */ });
}
function togglePin(){
  pinned = !pinned;
  localStorage.setItem('oc-pin', pinned ? '1' : '0');
  applyPinUI();
  requestPin(pinned);
}
$('pinBtn').addEventListener('click', togglePin);
applyPinUI();
if (pinned) requestPin(true);   // 上次置顶 → 本次打开自动恢复置顶

/* 退出 */
$('exitBtn').addEventListener('click',()=>{
  if(confirm('确定退出开仓计算器吗？')){ fetch('/api/shutdown'); }
});

/* 输入事件 */
['equity','entry','stop','target','marginRate','entryO'].forEach(id=>{
  $(id).addEventListener('input',onInput);
});
/* 持仓方向 双按钮: 做多(红) / 做空(青) */
document.querySelectorAll('#dirSeg button').forEach(b=>{
  b.addEventListener('click', ()=>{
    if (b.dataset.dir === dirF) return;
    dirF = b.dataset.dir;
    document.querySelectorAll('#dirSeg button').forEach(x=>x.classList.toggle('active', x===b));
    onInput();
  });
});

/* 计算 */
function onInput(){
  if(curMode==='futures'){ updateTickHint(); calcFutures(); } else calcOptions();
}

function calcFutures(){
  const eq = (parseFloat($('equity').value) || 0) * 10000;   // 万元 → 元
  const entry = parseFloat($('entry').value);
  const stop = parseFloat($('stop').value);
  const target = parseFloat($('target').value);
  const mr = parseFloat($('marginRate').value) || 16;
  if(!selCode.F){
    showEmpty('请先选择开仓标的（支持代码 / 中文搜索）');
    return;
  }
  if(!eq || !entry || !stop || !target){
    showEmpty('请完整填写：总权益、开仓价、止损价、止盈价');
    return;
  }
  fetchT('/api/calc/futures',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({equity:eq,code:selCode.F,direction:dirF,
      entry,stop,target,margin_rate:mr/100,
      risk_percent: $('riskAmount').value ? parseFloat($('riskAmount').value) : 1})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ showError(d.error||'计算失败'); return; }
      renderF(d);
    }).catch(()=>showError('应用服务连接已断开：请关闭窗口后重新双击桌面「期货开仓计算器」图标启动。测算在本机完成，无需联网。'));
}

function renderF(d){
  $('empty').classList.add('hidden');
  $('resultO').classList.add('hidden');
  $('resultF').classList.remove('hidden');
  $('rBudgetF').textContent = fmtMoney(d.budget);
  // 当前风险额度核对: 徽章 权益 × X% + 预算公式行(如 权益 9万 × 1.5%)
  const pctUsed = d.risk_percent != null ? d.risk_percent : (parseFloat($('riskAmount').value) || 1);
  const pctTxt = fmtTrim(pctUsed);
  const eqWanTxt = fmtTrim(parseFloat($('equity').value) || 0);
  $('rIvBadgeF').innerHTML = '<span class="ico">◈</span>权益 × ' + pctTxt + '%';
  $('rFormulaF').innerHTML = '＝ 权益 <b>' + eqWanTxt + ' 万</b> × ' + pctTxt + '%';
  $('rLotsF').textContent = d.max_lots;
  $('rRatioF').textContent = d.pl_ratio.toFixed(2);
  $('rContractF').textContent = d.contract+'（'+d.code+' · '+d.exchange+'）';
  $('rMultF').textContent = d.mult+' '+d.unit;
  $('rMarginF').textContent = fmtMoney(d.margin_per_lot);
  $('rRiskF').textContent = fmtMoney(d.per_lot_risk);
  $('rRewardF').textContent = fmtMoney(d.per_lot_reward);
  $('rRiskUsedF').textContent = fmtMoney(d.risk_used);
  $('rMaxRewardF').textContent = fmtMoney(d.max_reward);
  $('rMarginUsedF').textContent = fmtMoney(d.margin_used);
  $('rBadgeLabel').textContent = d.direction==='long' ? '做多决策' : '做空决策';

  /* 阶梯止盈: 2R~5R 逐级止盈价, 按方向对齐颜色 (做多红/做空青) */
  const dirLong = d.direction === 'long';
  $('rLadderDirF').textContent = dirLong ? '（做多 · 逐级上移）' : '（做空 · 逐级下移）';
  $('rLadderNoteF').textContent = '1R = 止损价差' + (d.entry != null ? ' · 开仓 ' + fmtTrim(d.entry) : '');
  const grid = $('rLadderGridF');
  if (d.ladder && d.ladder.length){
    grid.innerHTML = d.ladder.map(s=>{
      const p = s.price;
      const lvl = s.r + 'R';
      const cls = dirLong ? 'long' : 'short';
      const arrow = dirLong ? '↗' : '↘';
      const profitTxt = fmtMoney(s.per_lot_profit);
      return '<div class="rung" title="分批止盈：每达一档平一部分，剩余仓位目标移到下一档">' +
        '<div class="rt"><b>' + lvl + '</b><span class="ad">' + arrow + '</span></div>' +
        '<div class="rq ' + cls + '">' + fmtTrim(p) + '</div>' +
        '<div class="rp">每手浮盈 <b>' + profitTxt + '</b></div>' +
      '</div>';
    }).join('');
  } else {
    grid.innerHTML = '<div class="plans-empty">暂无阶梯止盈数据</div>';
  }

  const good = $('rBadgeGood'), bad = $('rBadgeBad'), warn = $('rWarn');
  if(!d.enough_lots){
    /* 资金不足 1 手 -> 硬性不开仓 */
    good.classList.add('hidden');
    bad.classList.remove('hidden');
    bad.innerHTML = '<span class="ico">✕</span>不开仓';
    warn.classList.remove('hidden');
    warn.innerHTML = '⛔ 资金不足 1 手（每手保证金 '+fmtMoney(d.margin_per_lot)+'），按风控要求<b>不开仓</b>。';
  } else if(d.participate){
    good.classList.remove('hidden');
    good.innerHTML = '<span class="ico">✓</span>可以参与';
    bad.classList.add('hidden');
    warn.classList.add('hidden');
  } else {
    good.classList.add('hidden');
    bad.classList.remove('hidden');
    bad.innerHTML = '<span class="ico">✕</span>不建议参与';
    warn.classList.remove('hidden');
    warn.innerHTML = '⚠ 盈亏比 '+d.pl_ratio.toFixed(2)+' < 1.5，按风控要求<b>不建议参与</b>。';
  }
}

function calcOptions(){
  const eq = (parseFloat($('equity').value) || 0) * 10000;   // 万元 → 元
  const entry = parseFloat($('entryO').value);
  if(!selCode.O){
    showEmpty('请先选择开仓标的（点击上方品种按钮）');
    return;
  }
  if(!eq || !entry){
    showEmpty('请完整填写：总权益、开仓价（权利金）');
    return;
  }
  fetchT('/api/calc/options',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({equity:eq,code:selCode.O,entry,
      risk_percent: $('riskAmountO').value ? parseFloat($('riskAmountO').value) : 3})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ showError(d.error||'计算失败'); return; }
      renderO(d);
    }).catch(()=>showError('应用服务连接已断开：请关闭窗口后重新双击桌面「期货开仓计算器」图标启动。测算在本机完成，无需联网。'));
}

function renderO(d){
  $('empty').classList.add('hidden');
  $('resultF').classList.add('hidden');
  $('resultO').classList.remove('hidden');
  $('rBudgetO').textContent = fmtMoney(d.budget);
  $('rLotsO').textContent = d.max_lots;
  $('rPremiumO').textContent = fmtMoney(d.premium_per_lot);
  $('rContractO').textContent = d.contract+'（'+d.code+' · '+d.exchange+'）';
  $('rMultO').textContent = d.opt_mult+' '+d.unit;
  $('rFundsO').textContent = fmtMoney(d.funds_used);
  const b = $('rIvBadge');
  b.className = 'badge good';
  const pct = (d.risk_percent != null ? d.risk_percent : 3).toFixed(1).replace(/\.0$/, '');
  b.innerHTML = '<span class="ico">◈</span>权益 × ' + pct + '%';
  const warnO = $('rWarnO');
  if(!d.enough_lots){
    warnO.classList.remove('hidden');
    warnO.innerHTML = '⛔ 资金不足 1 手（每手权利金 '+fmtMoney(d.premium_per_lot)+'），按风控要求<b>不开仓</b>。';
  } else {
    warnO.classList.add('hidden');
  }
}

function showEmpty(msg){
  $('resultF').classList.add('hidden');
  $('resultO').classList.add('hidden');
  $('empty').classList.remove('hidden');
  $('empty').textContent = msg;
}
function showError(msg){
  $('resultF').classList.add('hidden');
  $('resultO').classList.add('hidden');
  $('empty').classList.remove('hidden');
  $('empty').innerHTML = '<span style="color:var(--bad)">⚠ '+msg+'</span>';
}

/* =================================================================
   最近方案 (期货): 保存当前参数, 最多保留 3 组; 平铺列表, 一键调出
   ================================================================= */
const PLAN_KEY = 'oc_futures_plans';
const PLAN_MAX = 3;
let planList = [];
function escHtml(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function loadPlans(){
  try { planList = JSON.parse(localStorage.getItem(PLAN_KEY) || '[]') || []; }
  catch(e){ planList = []; }
}
function persistPlans(){
  if (planList.length > PLAN_MAX) planList.length = PLAN_MAX;
  try { localStorage.setItem(PLAN_KEY, JSON.stringify(planList)); } catch(e){}
  renderPlans();
}
function renderPlans(){
  const box = $('planList');
  if (!box) return;
  const show = curMode === 'futures';
  $('recentPlans').classList.toggle('hidden', !show);
  if (!show) return;
  if (!planList.length){
    box.innerHTML = '<div class="plans-empty">暂无保存方案 — 完成一次期货测算后点「💾 保存当前方案」，这里平铺显示最近 ' + PLAN_MAX + ' 组，点任意一条「调出」一键恢复并重算。</div>';
    return;
  }
  box.innerHTML = planList.map((p,i)=>{
    const dCls = p.dir === 'short' ? 'short' : 'long';
    const dTxt = p.dir === 'short' ? '空' : '多';
    return '<div class="plans-item" data-idx="' + i + '" title="点击调出: ' + escHtml(p.contract) + ' ' + dTxt + ' @' + escHtml(p.entry) + '">' +
      '<span class="nm">' + escHtml(p.contract) + ' <span class="d ' + dCls + '">' + dTxt + '</span> ' + escHtml(p.entry) + '</span>' +
      '<span class="meta">' + escHtml(String(p.code||'').toUpperCase()) + ' · 止损 ' + escHtml(p.stop) + ' → 止盈 ' + escHtml(p.target) + ' · 风险 ' + escHtml(p.riskPct) + '%</span>' +
      '<span class="go">调出</span>' +
    '</div>';
  }).join('');
}
function saveCurrentPlan(){
  if (curMode !== 'futures') return;
  if (!selCode.F){ alert('请先选择开仓标的'); return; }
  const eqWan = parseFloat($('equity').value);
  const entry = parseFloat($('entry').value);
  const stop = parseFloat($('stop').value);
  const target = parseFloat($('target').value);
  if (!(eqWan > 0 && entry > 0 && stop > 0 && target > 0)){
    alert('请先完整填写：总权益、开仓价、止损价、止盈价，再保存方案');
    return;
  }
  const c = CONTRACTS.find(x=>x.code.toLowerCase() === String(selCode.F).toLowerCase())
    || {name: selCode.F, code: selCode.F};
  const sig = String(c.code).toLowerCase() + '|' + dirF + '|' + entry;
  planList = planList.filter(x => (String(x.code).toLowerCase() + '|' + x.dir + '|' + parseFloat(x.entry)) !== sig);
  planList.unshift({
    code: c.code,
    contract: c.name,
    dir: dirF,
    entry: fmtTrim(entry),
    stop: fmtTrim(stop),
    target: fmtTrim(target),
    mr: parseFloat($('marginRate').value) || 16,
    riskPct: fmtTrim(parseFloat($('riskAmount').value) || 1),
    eqWan: fmtTrim(eqWan),
  });
  persistPlans();
}
function recallPlan(p){
  if (!p) return;
  const c = CONTRACTS.find(x=>x.code.toLowerCase() === String(p.code).toLowerCase());
  if (!c){ alert('当前品种表中找不到 ' + p.code + '，请重新搜索选择标的'); return; }
  setMode('futures');                                   // 切回期货模式(也会刷新方案区)
  $('cSearch').value = c.name + ' ' + c.code + ' · ' + c.exchange;   // 同步搜索框显示
  pickContract(c, 'F');                                 // 设置乘数/单位/行情(内部会触发一次计算)
  dirF = (p.dir === 'short') ? 'short' : 'long';
  document.querySelectorAll('#dirSeg button').forEach(b=>b.classList.toggle('active', b.dataset.dir === dirF));
  $('equity').value = p.eqWan;
  $('entry').value = p.entry;
  $('stop').value = p.stop;
  $('target').value = p.target;
  $('marginRate').value = p.mr;
  const want = Number(p.riskPct);
  if ([0.5, 1, 1.5, 2, 3].indexOf(want) >= 0) $('riskAmount').value = String(want);
  $('equity').dispatchEvent(new Event('input'));        // 刷新权益换算提示
  updateTickHint();
  onInput();                                            // 用方案参数重新测算
}
loadPlans();
renderPlans();
$('btnSavePlan').addEventListener('click', saveCurrentPlan);
$('planList').addEventListener('click', e=>{
  const it = e.target.closest('.plans-item');
  if (!it || it.dataset.idx === undefined) return;
  recallPlan(planList[+it.dataset.idx]);
});

init();

/* =================================================================
   资金曲线模块（独立命名空间 FundUI）
   ================================================================= */
/* 图表数值标签插件: 柱状图顶部显示金额(万), 折线图节点上方显示 % */
function fmtWan(v){
  const abs = Math.abs(v);
  if (abs >= 10000) return (v/10000).toFixed(2) + '万';
  return v.toLocaleString('en-US');
}
const valueLabelPlugin = {
  id: 'valueLabel',
  afterDatasetsDraw(chart, args, opts){
    const {ctx} = chart;
    ctx.save();
    const fs = (chart.options.plugins && chart.options.plugins.valueLabel && chart.options.plugins.valueLabel.fontSize) || 12;
    ctx.font = 'bold ' + fs + 'px "Segoe UI","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // 让数字加描边, 在深色背景上更清晰
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    chart.data.datasets.forEach((ds, di)=>{
      const meta = chart.getDatasetMeta(di);
      const isLine = ds.type === 'line';
      meta.data.forEach((pt, i)=>{
        const v = ds.data[i];
        if (v == null || isNaN(v)) return;
        const label = isLine ? v.toFixed(1) + '%' : fmtWan(v);
        ctx.strokeStyle = 'rgba(11,16,32,0.85)';   // 描边色(深底)
        ctx.strokeText(label, pt.x, pt.y + (isLine ? -10 : (v >= 0 ? -5 : 16)));
        ctx.fillStyle = isLine ? '#ff5b9b' : (v >= 0 ? '#c8d4ff' : '#ff8a8a');
        ctx.fillText(label, pt.x, pt.y + (isLine ? -10 : (v >= 0 ? -5 : 16)));
      });
    });
    ctx.restore();
  },
};

const FundUI = {
  strategy: 'abe',          // 当前选中策略: abe / 威科夫 / combined
  monthly: [],
  yearly: [],
  chartMonthly: null,
  chartYearly: null,
  showAllRows: false,       // 月度明细是否显示全部(默认只显示最近 5 条)
  chartZoom: null,          // 放大图实例

  /* ---- 主 tab 切换 ---- */
  initMainTabs(){
    document.querySelectorAll('#mainTabs .maintab').forEach(t=>{
      t.addEventListener('click', ()=>{
        document.querySelectorAll('#mainTabs .maintab').forEach(x=>x.classList.remove('active'));
        t.classList.add('active');
        const tab = t.dataset.tab;
        $('calcArea').classList.toggle('hidden', tab !== 'calc');
        $('fundsArea').classList.toggle('hidden', tab !== 'funds');
        // header 标题随 tab 联动
        const titles = {
          calc:  {t:'期货开仓计算器', s:'风控仓位计算 · 盈亏比决策 · 保证金测算'},
          funds: {t:'资金曲线',        s:'abe · 威科夫 多策略记录'}
        };
        const ti = titles[tab];
        if (ti) { $('appTitle').textContent = ti.t; $('appSubtitle').textContent = ti.s; }
        if (tab === 'funds' && (!this.monthly.length && !this.yearly.length)) {
          this.refreshAll();
        }
      });
    });
  },

  /* ---- 策略切换 ---- */
  initStrategySeg(){
    document.querySelectorAll('#stratSeg button').forEach(b=>{
      b.addEventListener('click', ()=>{
        document.querySelectorAll('#stratSeg button').forEach(x=>x.classList.remove('active'));
        b.classList.add('active');
        this.strategy = b.dataset.strategy;
        // 汇总视图是自动计算, 不允许录入
        $('btnAddRecord').classList.toggle('hidden', this.strategy === 'combined');
        this.refreshAll();
      });
    });
  },

  /* ---- 模态框 + 录入 (新增/修改双模式) ---- */
  editing: null,   // null=新增; {year, month}=修改该月记录

  bindWanHints(){
    [['fldInit','wanInit'],['fldEnd','wanEnd'],['fldCF','wanCF'],['fldCash','wanCash']].forEach(([i,h])=>{
      const el = $(i), hint = $(h);
      if (!el || !hint) return;
      const update = ()=>{
        const v = parseFloat(el.value);
        hint.innerHTML = isNaN(v) ? '' : '≈ <b>' + (v/10000).toFixed(2) + '</b> 万';
      };
      el.addEventListener('input', update);
      el.addEventListener('change', update);
    });
  },

  /* 打开录入框: rec=null 新增; rec=对象 修改预填 */
  openModal(rec){
    const d = new Date();
    this.editing = rec ? {year: rec.year, month: rec.month} : null;
    $('modalTitle').textContent = rec ? ('修改 ' + rec.year + '年' + rec.month + '月 记录') : '记录月度权益';
    $('fldYear').value = rec ? rec.year : d.getFullYear();
    $('fldMonth').value = rec ? rec.month : (d.getMonth() + 1);
    $('fldInit').value = rec ? rec.initial_equity : '';
    $('fldEnd').value = rec ? rec.end_equity : '';
    $('fldCF').value = rec ? rec.cash_flow : '';
    $('fldCash').value = rec ? (rec.cash || '') : '';
    $('fldNote').value = rec ? (rec.note||'') : '';
    // 万提示同步
    ['fldInit','fldEnd','fldCF','fldCash'].forEach(i=>{ const e=$(i); e.dispatchEvent(new Event('input')); });
    this.applyAutoInit(rec);
    $('modalBg').classList.remove('hidden');
    setTimeout(()=>{ (rec ? $('fldEnd') : $('fldInit')).focus(); }, 60);
  },

  /* 月初权益自动调取: 严格匹配相邻上一月(y, m-1); 跨年(1月→上年12月)
     缺失(中间断层/第一次)→ 不自动填, 提示手动输入, 不写死 */
  async applyAutoInit(rec){
    const fld = $('fldInit'), hint = $('wanInit');
    fld.removeAttribute('readonly');
    fld.placeholder = '如 100000';
    hint.innerHTML = '';
    if (rec || this.strategy === 'combined') return;
    try {
      const r = await fetchT('/api/funds/records?strategy=' + encodeURIComponent(this.strategy));
      const d = await r.json();
      if (!d.ok || !d.records || !d.records.length) return;   // 第一次, 手动填
      const y = parseInt($('fldYear').value), m = parseInt($('fldMonth').value);
      if (!y || !m || m < 1 || m > 12) return;
      // 严格匹配相邻上月
      let py = y, pm = m - 1;
      if (pm < 1) { pm = 12; py--; }
      const prev = d.records.find(x => x.year === py && x.month === pm);
      if (prev) {
        fld.value = prev.end_equity;
        fld.setAttribute('readonly', 'readonly');
        fld.placeholder = '';
        hint.innerHTML = '≈ <b>' + (prev.end_equity/10000).toFixed(2) + '</b> 万 · <span class="auto">自动取自 ' + prev.year + '/' + String(prev.month).padStart(2,'0') + ' 月末权益</span>';
      } else {
        // 缺失相邻记录 → 不自动填, 提示手动
        hint.innerHTML = '<span class="auto">⚠ 缺少 ' + py + '/' + String(pm).padStart(2,'0') + ' 记录，请手动输入月初权益</span>';
      }
    } catch (e) { /* 静默, 允许手动填 */ }
  },

  /* ---- 导出备份: 优先系统"另存为"对话框(可自选保存位置), 不支持则回退自动下载 ---- */
  async exportBackup(){
    try {
      const r = await fetchT('/api/funds/export');
      const d = await r.json();
      if (!d.ok) { alert('导出失败：' + (d.error||'')); return; }
      const cnt = (d.records||[]).length;
      if (!cnt) { alert('当前没有可导出的记录'); return; }
      const blob = new Blob([JSON.stringify(d, null, 2)], {type:'application/json'});
      const now = new Date();
      const pad = n=>String(n).padStart(2,'0');
      const fname = '资金曲线备份_' + now.getFullYear() + pad(now.getMonth()+1) + pad(now.getDate()) + '_' + pad(now.getHours()) + pad(now.getMinutes()) + '.opcalc';
      // 优先: 系统保存对话框 (Chrome 桌面版支持, 可自选保存位置)
      if (window.showSaveFilePicker) {
        try {
          const handle = await window.showSaveFilePicker({
            suggestedName: fname,
            types: [{ description: 'OpenCalc 备份文件', accept: {'application/json': ['.opcalc', '.json']} }],
          });
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
          alert('✅ 已导出 ' + cnt + ' 条记录 → ' + handle.name + '\n请把该文件拷贝到新电脑，用「导入备份」恢复。');
          return;
        } catch (err) {
          if (err && err.name === 'AbortError') return;   // 用户点了取消
          /* 其他错误 → 回退自动下载 */
        }
      }
      // 回退: 自动下载到浏览器默认下载目录
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 200);
      alert('✅ 已导出 ' + cnt + ' 条记录 → ' + fname + '\n文件已保存到浏览器默认「下载」目录，如需自选位置请使用新版弹窗。');
    } catch (e) {
      alert('导出失败：' + e);
    }
  },

  /* ---- 导入备份: 读取 .opcalc 文件并恢复 ---- */
  importBackup(file){
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async ()=>{
      try {
        const payload = JSON.parse(reader.result);
        const r = await fetchT('/api/funds/import', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify(payload)});
        const d = await r.json();
        if (!d.ok) { alert('导入失败：' + (d.error||'备份文件格式不正确')); return; }
        alert('✅ 导入成功：' + d.imported + ' 条记录（策略：' + (d.strategies.join('、') || '无') + '）');
        this.refreshAll();
      } catch (e) {
        alert('导入失败：文件不是有效的备份文件（' + e.message + '）');
      }
    };
    reader.onerror = ()=>alert('读取文件失败');
    reader.readAsText(file, 'utf-8');
  },

  /* ---- 数据位置设置: 指向网盘文件夹, 换电脑不丢 ---- */
  async openDataDir(){
    try {
      const r = await fetchT('/api/funds/data-info');
      const d = await r.json();
      if (!d.ok) return;
      $('setCurDir').textContent = d.data_dir;
      $('setCurCnt').textContent = d.record_count;
      $('setDir').value = d.data_dir;
      $('setBg').classList.remove('hidden');
      setTimeout(()=>$('setDir').focus(), 60);
    } catch (e) {
      alert('读取数据位置失败：' + e);
    }
  },

  async clearAllRecords(){
    if (!confirm('⚠ 确定要清除所有策略的所有月度记录吗？\n此操作不可恢复！\n建议先「⬇ 导出备份」再清除。')) return;
    if (!confirm('再次确认：所有数据将被永久删除（不可恢复）！')) return;
    try {
      const r = await fetchT('/api/funds/clear-all', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      const d = await r.json();
      if (d.ok) {
        alert('✅ 已清除全部 ' + d.remaining + ' 条记录');
        this.refreshAll();
      } else {
        alert('清除失败：' + (d.error||''));
      }
    } catch (e) {
      alert('清除失败：' + e);
    }
  },

  async browseDataDir(){
    try {
      const r = await fetchT('/api/funds/browse', {method:'POST'}, 30000);  // 弹框可能停留较久
      const d = await r.json();
      if (d.ok && d.path) {
        $('setDir').value = d.path;
      }
      // 用户取消时不提示, 保持原值
    } catch (e) {
      // 静默
    }
  },

  async saveDataDir(){
    const dir = $('setDir').value.trim();
    if (!dir) { alert('请输入数据目录路径'); return; }
    try {
      const r = await fetchT('/api/funds/data-dir', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({dir})});
      const d = await r.json();
      if (!d.ok) { alert('设置失败：' + (d.error||'')); return; }
      alert(d.status === 'same'
        ? '当前已是该位置，无需迁移。'
        : '✅ 已迁移 ' + d.migrated + ' 条记录到：\n' + d.data_dir + '\n\n以后数据都保存在这里，网盘会自动同步。');
      $('setBg').classList.add('hidden');
      this.refreshAll();
    } catch (e) {
      alert('设置失败：' + e);
    }
  },

  initModal(){
    const close = ()=>$('modalBg').classList.add('hidden');
    $('btnAddRecord').addEventListener('click', ()=>{
      if (this.strategy === 'combined') { alert('汇总视图为自动计算结果，请在 abe / 威科夫 下录入'); return; }
      this.openModal(null);
    });
    $('btnCancel').addEventListener('click', close);
    $('modalBg').addEventListener('click', e=>{ if (e.target===$('modalBg')) close(); });
    $('btnSave').addEventListener('click', ()=>this.saveRecord());
    // 切换年月后重新自动调取月初权益
    $('fldYear').addEventListener('change', ()=>this.applyAutoInit(null));
    $('fldMonth').addEventListener('change', ()=>this.applyAutoInit(null));
    // 导出 / 导入
    $('btnExport').addEventListener('click', ()=>this.exportBackup());
    $('btnImport').addEventListener('click', ()=>$('importFile').click());
    $('importFile').addEventListener('change', e=>{
      this.importBackup(e.target.files[0]);
      e.target.value = '';   // 允许重复选择同一文件
    });
    // 数据位置设置
    $('btnDataDir').addEventListener('click', ()=>this.openDataDir());
    $('setCancel').addEventListener('click', ()=>$('setBg').classList.add('hidden'));
    $('setBg').addEventListener('click', e=>{ if (e.target===$('setBg')) $('setBg').classList.add('hidden'); });
    $('setSave').addEventListener('click', ()=>this.saveDataDir());
    // 一键清除所有记录
    $('btnClearAll').addEventListener('click', ()=>this.clearAllRecords());
    // 联系作者(左下角浮动按钮)
    $('floatingContact').addEventListener('click', ()=>$('contactBg').classList.remove('hidden'));
    $('contactClose').addEventListener('click', ()=>$('contactBg').classList.add('hidden'));
    $('contactBg').addEventListener('click', e=>{ if (e.target===$('contactBg')) $('contactBg').classList.add('hidden'); });
    $('setBrowse').addEventListener('click', ()=>this.browseDataDir());
    // 月度明细「显示全部 / 收起」
    $('btnShowAll').addEventListener('click', ()=>{
      this.showAllRows = !this.showAllRows;
      this.renderTables();
    });
    // 年月/年份列点击排序: desc ↔ asc 来回切换
    const toggleSort = ()=>{
      this.sortDir = (this.sortDir === 'desc') ? 'asc' : 'desc';
      this.renderTables();
    };
    ['thSortMonthly','thSortYearly'].forEach(id=>{
      const el = $(id);
      if (!el) return;
      el.addEventListener('click', toggleSort);
      // 同步箭头(默认 desc ↓)
      const arrow = el.querySelector('.sort-arrow');
      if (arrow) arrow.textContent = this.sortDir === 'desc' ? '↓' : '↑';
    });
    this.bindWanHints();
  },

  async saveRecord(){
    const strategy = this.strategy === 'combined' ? 'abe' : this.strategy; // 不可写入汇总
    const year = parseInt($('fldYear').value);
    const month = parseInt($('fldMonth').value);
    const initial_equity = parseFloat($('fldInit').value);
    const end_equity = parseFloat($('fldEnd').value);
    const cash_flow = parseFloat($('fldCF').value || '0');
    const cash = parseFloat($('fldCash').value || '0');
    const note = $('fldNote').value.trim();
    if (!year || !month || !(month>=1 && month<=12) || isNaN(initial_equity) || isNaN(end_equity)) {
      alert('请完整填写：年份/月份/月初权益/本月末权益'); return;
    }
    try {
      const r = await fetchT('/api/funds/records', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({strategy, year, month, initial_equity, end_equity, cash_flow, cash, note})});
      const d = await r.json();
      if (!d.ok) { alert('保存失败：' + (d.error||'未知错误')); return; }
      $('modalBg').classList.add('hidden');
      this.editing = null;
      this.refreshAll();
    } catch (e) {
      alert('保存失败：' + e);
    }
  },

  async deleteRecord(year, month){
    if (!confirm('确定删除 ' + year + '年' + month + '月 的记录？')) return;
    try {
      const r = await fetchT('/api/funds/records', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({strategy: this.strategy, year, month, action:'delete'})});
      const d = await r.json();
      if (!d.ok) { alert('删除失败：' + (d.error||'')); return; }
      this.refreshAll();
    } catch (e) {
      alert('删除失败：' + e);
    }
  },

  /* ---- 数据获取 ---- */
  async refreshAll(){
    const s = this.strategy;
    if (s === 'combined') {
      const [m, y] = await Promise.all([fetchT('/api/funds/combined'), fetchT('/api/funds/dashboard?strategy=combined')]);
      const dm = await m.json(); const dy = await y.json();
      this.monthly = dm.records || [];
      this.yearly = dy.yearly || [];
    } else {
      const [r, y] = await Promise.all([
        fetchT('/api/funds/records?strategy=' + encodeURIComponent(s)),
        fetchT('/api/funds/yearly?strategy=' + encodeURIComponent(s)),
      ]);
      const dr = await r.json(); const dy = await y.json();
      this.monthly = dr.records || [];
      this.yearly = dy.yearly || [];
    }
    this.renderTables();
    this.renderCharts();
    this.loadWithdrawals();
  },

  /* ---- 各策略累计提现(出金) ---- */
  async loadWithdrawals(){
    try {
      const r = await fetchT('/api/funds/withdrawals');
      const d = await r.json();
      if (!d.ok) return;
      const fmt = v => '¥' + Math.round(v).toLocaleString('en-US');
      $('wdAbe').innerHTML = 'abe <b>' + fmt(d.abe || 0) + '</b>';
      $('wdWk').innerHTML = '威科夫 <b>' + fmt(d['威科夫'] || 0) + '</b>';
      $('wdAll').innerHTML = '汇总 <b>' + fmt(d.combined || 0) + '</b>';
    } catch (e) { /* 静默 */ }
  },

  /* ---- 渲染表格 ---- */
  renderTables(){
    this.sortDir = this.sortDir || 'desc';   // 默认最新在顶
    const fmtMoney = v => v==null ? '—' : (v<0?'-':'') + '¥' + Math.abs(Math.round(v)).toLocaleString('en-US');
    const fmtPct = v => v==null ? '—' : (v>=0?'':'−') + (Math.abs(v)*100).toFixed(2) + '%';
    const isCombined = this.strategy === 'combined';
    const titleStr = (this.strategy === 'combined' ? '汇总账户' : this.strategy);

    // 月度明细 (记录多时默认只显示最近 MAX_SHOW 条, 其余隐藏可滚动)
    const tb = document.querySelector('#tblMonthly tbody');
    tb.innerHTML = '';
    const MAX_SHOW = 5;
    // sortDir: 'desc' 最新在顶(默认) / 'asc' 最早在顶
    const sd = this.sortDir || 'desc';
    const sgn = sd === 'desc' ? -1 : 1;
    const sorted = [...this.monthly].sort((a,b)=> sgn * ((a.year-b.year) || (a.month-b.month)));
    const hasMore = sorted.length > MAX_SHOW;
    for (let i = 0; i < sorted.length; i++) {
      const r = sorted[i];
      const tr = document.createElement('tr');
      const cls = r.monthly_pnl >= 0 ? 'pos' : 'neg';
      tr.innerHTML =
        '<td>' + r.year + '/' + String(r.month).padStart(2,'0') + '</td>' +
        '<td class="num">' + fmtMoney(r.initial_equity) + '</td>' +
        '<td class="num">' + fmtMoney(r.end_equity) + '</td>' +
        '<td class="num">' + (r.cash_flow ? (r.cash_flow>=0?'+':'') + fmtMoney(r.cash_flow) : '¥0') + '</td>' +
        '<td class="num">' + (r.cash ? fmtMoney(r.cash) : '¥0') + '</td>' +
        '<td class="num ' + cls + '">' + fmtMoney(r.monthly_pnl) + '</td>' +
        '<td class="num ' + cls + '">' + fmtPct(r.month_return_rate) + '</td>' +
        '<td>' + (r.note ? r.note.replace(/[<>&]/g, s=>s==='<'?'&lt;':s==='>'?'&gt;':'&amp;') : '') + '</td>' +
        (isCombined ? '<td class="actions"></td>' :
         '<td class="actions">' +
           '<button class="btn sm" data-edit="' + r.year + ',' + r.month + '">修改</button>' +
           '<button class="btn danger sm" data-del="' + r.year + ',' + r.month + '">删除</button>' +
         '</td>');
      // 记录多时: 降序 → 隐藏末尾旧记录(索引 >= MAX_SHOW); 升序 → 隐藏开头旧记录(索引 < length-MAX_SHOW)
      if (hasMore && !this.showAllRows) {
        const hidden = sd === 'desc' ? (i >= MAX_SHOW) : (i < sorted.length - MAX_SHOW);
        if (hidden) tr.classList.add('hidden');
      }
      tb.appendChild(tr);
    }
    $('monthlyTitle').textContent = titleStr + ' · 月度明细';
    $('monthlyEmpty').classList.toggle('hidden', sorted.length > 0);
    // 「显示全部 / 收起」按钮
    $('btnShowAll').classList.toggle('hidden', !hasMore);
    $('btnShowAll').textContent = this.showAllRows ? '收起' : ('显示全部 ' + (sorted.length - MAX_SHOW) + ' 条');
    if (!isCombined) {
      tb.querySelectorAll('button[data-edit]').forEach(b=>{
        b.addEventListener('click', ()=>{
          const [y, m] = b.dataset.edit.split(',').map(Number);
          const rec = this.monthly.find(x=>x.year===y && x.month===m);
          if (rec) this.openModal(rec);
        });
      });
      tb.querySelectorAll('button[data-del]').forEach(b=>{
        b.addEventListener('click', ()=>{
          const [y, m] = b.dataset.del.split(',').map(Number);
          this.deleteRecord(y, m);
        });
      });
    }

    // 年度汇总
    const tb2 = document.querySelector('#tblYearly tbody');
    tb2.innerHTML = '';
    const sorted2 = [...this.yearly].sort((a,b)=> sgn * (a.year-b.year));
    for (const r of sorted2) {
      const cls = r.yearly_pnl >= 0 ? 'pos' : 'neg';
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + r.year + '</td>' +
        '<td class="num">' + fmtMoney(r.initial_equity) + '</td>' +
        '<td class="num">' + fmtMoney(r.end_equity) + '</td>' +
        '<td class="num">' + fmtMoney(r.total_cash_flow) + '</td>' +
        '<td class="num ' + cls + '">' + fmtMoney(r.yearly_pnl) + '</td>' +
        '<td class="num ' + cls + '">' + fmtPct(r.annualized_return_rate) + '</td>' +
        '<td class="num">' + (r.month_count||'') + '</td>';
      tb2.appendChild(tr);
    }
    $('yearlyTitle').textContent = titleStr + ' · 年度汇总';
    $('yearlyEmpty').classList.toggle('hidden', sorted2.length > 0);

    // 图表标题
    $('chartMonthlyTitle').textContent = titleStr + ' · 月收益图';
    $('chartYearlyTitle').textContent = titleStr + ' · 年盈亏分析';
    // 同步排序箭头
    const arrow = sd === 'desc' ? '↓' : '↑';
    ['thSortMonthly','thSortYearly'].forEach(id=>{
      const a = $(id) && $(id).querySelector('.sort-arrow');
      if (a) a.textContent = arrow;
    });
  },

  /* ---- 渲染图表 ---- */
  renderCharts(){
    if (typeof Chart === 'undefined') {
      console.warn('Chart.js 未加载'); return;
    }
    // 月收益图：柱(本月末权益, 本月盈亏) + 折线(本月收益率, 右轴)
    const sorted = [...this.monthly].sort((a,b)=> (a.year-b.year) || (a.month-b.month));
    const labels = sorted.map(r=> r.year + '/' + String(r.month).padStart(2,'0'));
    const equity = sorted.map(r=> r.end_equity);
    const pnl = sorted.map(r=> r.monthly_pnl);
    const rate = sorted.map(r=> r.month_return_rate * 100);  // 百分比

    if (this.chartMonthly) this.chartMonthly.destroy();
    const ctxM = $('chartMonthly').getContext('2d');
    this.chartMonthly = new Chart(ctxM, {
      type: 'bar',
      plugins: [valueLabelPlugin],
      data: {
        labels,
        datasets: [
          {label: '本月末权益', data: equity, backgroundColor: 'rgba(91,140,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '本月盈亏',  data: pnl,    backgroundColor: 'rgba(125,227,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '本月收益率(%)', data: rate, type: 'line', borderColor: '#ff5b9b', backgroundColor: '#ff5b9b',
            tension: 0.35, pointRadius: 4, borderWidth: 2.5, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {display: false},
          tooltip: {intersect: false, mode: 'index',
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:'rgba(148,163,255,.12)'}, ticks:{color:'#9aa6c8', callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#ff5b9b', callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:'#9aa6c8', autoSkip: true, maxRotation: 0}},
        },
      },
    });

    // 年盈亏分析：柱(年末权益, 年度总盈亏) + 折线(年化收益率, 右轴)
    const sortedY = [...this.yearly].sort((a,b)=>a.year-b.year);
    const labelsY = sortedY.map(r=> String(r.year));
    const endEq = sortedY.map(r=> r.end_equity);
    const yPnl  = sortedY.map(r=> r.yearly_pnl);
    const yRate = sortedY.map(r=> r.annualized_return_rate * 100);

    if (this.chartYearly) this.chartYearly.destroy();
    const ctxY = $('chartYearly').getContext('2d');
    this.chartYearly = new Chart(ctxY, {
      type: 'bar',
      plugins: [valueLabelPlugin],
      data: {
        labels: labelsY,
        datasets: [
          {label: '年末权益',   data: endEq, backgroundColor: 'rgba(91,140,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '年度总盈亏', data: yPnl,  backgroundColor: 'rgba(125,227,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '年化收益率(%)', data: yRate, type: 'line', borderColor: '#ff5b9b', backgroundColor: '#ff5b9b',
            tension: 0.35, pointRadius: 5, borderWidth: 2.5, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {display: false},
          tooltip: {intersect: false, mode: 'index',
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:'rgba(148,163,255,.12)'}, ticks:{color:'#9aa6c8', callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#ff5b9b', callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:'#9aa6c8'}},
        },
      },
    });
  },

  /* ---- 图表放大: 弹出大图查看 ---- */
  initZoom(){
    document.querySelectorAll('.zoombtn').forEach(b=>{
      b.addEventListener('click', ()=>this.openChartZoom(b.dataset.zoom));
    });
    $('chartZoomClose').addEventListener('click', ()=>this.closeChartZoom());
    $('chartZoomBg').addEventListener('click', e=>{ if (e.target===$('chartZoomBg')) this.closeChartZoom(); });
    window.addEventListener('keydown', e=>{ if (e.key==='Escape') this.closeChartZoom(); });
  },

  openChartZoom(kind){
    const isMonthly = kind === 'monthly';
    const isCombined = this.strategy === 'combined';
    const titleStr = isCombined ? '汇总账户' : this.strategy;
    $('chartZoomTitle').textContent = titleStr + ' · ' + (isMonthly ? '月收益图' : '年盈亏分析');
    $('chartZoomLegend').innerHTML = isMonthly
      ? '<span class="lg"><i style="background:#5b8cff"></i>本月末权益（扣掉出入金之后）</span><span class="lg"><i style="background:#7de3ff"></i>本月盈亏</span><span class="lg"><i style="background:#ff5b9b"></i>本月收益率（折线，右轴）</span>'
      : '<span class="lg"><i style="background:#5b8cff"></i>年末权益</span><span class="lg"><i style="background:#7de3ff"></i>年度总盈亏</span><span class="lg"><i style="background:#ff5b9b"></i>年化收益率（折线，右轴）</span>';
    this.zoomKind = kind;
    $('chartZoomBg').classList.remove('hidden');
    // modal 显示后再渲染, 否则 canvas 尺寸为 0
    setTimeout(()=>this.renderZoomChart(), 60);
  },

  closeChartZoom(){
    $('chartZoomBg').classList.add('hidden');
    if (this.chartZoom) { this.chartZoom.destroy(); this.chartZoom = null; }
  },

  renderZoomChart(){
    if (typeof Chart === 'undefined') return;
    const isMonthly = this.zoomKind === 'monthly';
    const labels = isMonthly
      ? [...this.monthly].sort((a,b)=> (a.year-b.year) || (a.month-b.month)).map(r=> r.year + '/' + String(r.month).padStart(2,'0'))
      : [...this.yearly].sort((a,b)=>a.year-b.year).map(r=> String(r.year));
    const d1 = isMonthly
      ? [...this.monthly].sort((a,b)=> (a.year-b.year) || (a.month-b.month)).map(r=> r.end_equity)
      : [...this.yearly].sort((a,b)=>a.year-b.year).map(r=> r.end_equity);
    const d2 = isMonthly
      ? [...this.monthly].sort((a,b)=> (a.year-b.year) || (a.month-b.month)).map(r=> r.monthly_pnl)
      : [...this.yearly].sort((a,b)=>a.year-b.year).map(r=> r.yearly_pnl);
    const d3 = isMonthly
      ? [...this.monthly].sort((a,b)=> (a.year-b.year) || (a.month-b.month)).map(r=> r.month_return_rate * 100)
      : [...this.yearly].sort((a,b)=>a.year-b.year).map(r=> r.annualized_return_rate * 100);
    const t1 = isMonthly ? '本月末权益' : '年末权益';
    const t2 = isMonthly ? '本月盈亏' : '年度总盈亏';
    const t3 = isMonthly ? '本月收益率(%)' : '年化收益率(%)';

    if (this.chartZoom) this.chartZoom.destroy();
    const ctx = $('chartZoom').getContext('2d');
    this.chartZoom = new Chart(ctx, {
      type: 'bar',
      plugins: [valueLabelPlugin],
      data: {
        labels,
        datasets: [
          {label: t1, data: d1, backgroundColor: 'rgba(91,140,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: t2, data: d2, backgroundColor: 'rgba(125,227,255,.85)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: t3, data: d3, type: 'line', borderColor: '#ff5b9b', backgroundColor: '#ff5b9b',
            tension: 0.35, pointRadius: 6, borderWidth: 3, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {duration: 400},
        plugins: {
          legend: {display: false},
          valueLabel: {fontSize: 15},   // 放大图数值标签更大
          tooltip: {intersect: false, mode: 'index', titleFont: {size: 14}, bodyFont: {size: 13},
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:'rgba(148,163,255,.12)'}, ticks:{color:'#9aa6c8', font:{size:12}, callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#ff5b9b', font:{size:12}, callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:'#9aa6c8', font:{size:12}, autoSkip: true, maxRotation: 0}},
        },
      },
    });
  },

  init(){
    this.initMainTabs();
    this.initStrategySeg();
    this.initModal();
    this.initZoom();
  },
};
FundUI.init();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
