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
APP_VERSION = 55              # 程序版本号(用于单实例接管判断: 旧版实例自动让位)
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
    ⚠ APPDATA 缺失时(少见: 精简环境/非 Explorer 启动)回退到 ~/AppData/Roaming —
      不能直接回退到 ~, 否则会另开一个 <用户目录>/OpenCalc, 用户会以为数据丢了"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "OpenCalc")
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


def _app_dir():
    """程序自身所在目录: 打包后 = exe 所在目录; 开发时 = 源码目录.
    ⚠ 更新软件 = 替换/清理这个目录 → 数据绝不能放在它里面"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _is_inside(child, parent):
    """child 是否位于 parent 目录内(含自身); 路径大小写/分隔符不敏感"""
    try:
        c = os.path.normcase(os.path.abspath(child)).rstrip("\\/")
        p = os.path.normcase(os.path.abspath(parent)).rstrip("\\/")
        return c == p or c.startswith(p + os.sep)
    except Exception:  # noqa: BLE001
        return False


# 启动时决定数据目录, 优先级:
#   1) 环境变量 OC_DATA_DIR (测试/便携模式: 一次运行不碰真实数据)
#   2) config.json 里的 data_dir (用户自定义/网盘)
#   3) 程序默认目录 (%APPDATA%/OpenCalc/data)
_config = _load_config()
_custom_data_dir = (_config.get("data_dir") or "").strip()
_env_data_dir = (os.environ.get("OC_DATA_DIR") or "").strip()
if _env_data_dir:
    FUND_DB_PATH = os.path.join(os.path.abspath(os.path.expanduser(_env_data_dir)), "funds.db")
elif _custom_data_dir:
    FUND_DB_PATH = os.path.join(os.path.abspath(os.path.expanduser(_custom_data_dir)), "funds.db")
else:
    FUND_DB_PATH = os.path.join(_persistent_data_dir(), "funds.db")
FUND_DB_CONN = None
FUND_STRATEGIES = ["abe", "威科夫"]

# 自动备份: 每次数据变动后把库快照到 <数据目录>/backup/, 只保留最近 N 份
AUTO_BACKUP_KEEP = 10
BACKUP_DIRNAME = "backup"
_last_backup_ts = 0.0


def data_dir_risky():
    """数据目录是否落在软件目录内 — 更新软件(替换该目录)会连带删掉数据, 必须提醒用户迁出"""
    return _is_inside(os.path.dirname(FUND_DB_PATH), _app_dir())


def fund_backup_dir():
    return os.path.join(os.path.dirname(FUND_DB_PATH), BACKUP_DIRNAME)


def suggest_safe_data_dir():
    """建议一个「软件目录之外」的数据目录, 供「一键迁出」使用.
    取软件目录的上一级 + OpenCalc数据 (如 D:\\Workbuddy\\开仓计算器 → D:\\Workbuddy\\OpenCalc数据);
    软件若装在盘根(没有上一级)则退回 <盘>:\\OpenCalc数据"""
    app = _app_dir().rstrip("\\/")
    parent = os.path.dirname(app)
    if not parent or os.path.normcase(parent) == os.path.normcase(app):
        drive = os.path.splitdrive(app)[0]
        parent = (drive + os.sep) if drive else ""
    if not parent:
        return ""
    return os.path.join(parent, "OpenCalc数据")


def fund_auto_backup(reason="", force=False, min_interval=2.0):
    """把当前库快照到 <数据目录>/backup/funds_<时间戳>.db, 轮转只留最近 AUTO_BACKUP_KEEP 份.
    返回备份文件路径; 未备份返回 None.
    ⚠ 用 sqlite3 的 backup API 而非文件复制 — 库可能正被写入, 直接拷文件可能拿到半截状态
    ⚠ min_interval 内的重复触发合并成一次(导入/批量保存时会连续调用, 避免备份风暴)"""
    global _last_backup_ts
    now = time.time()
    if not force and min_interval and (now - _last_backup_ts) < min_interval:
        return None
    conn_in = conn_out = None
    out_path = None
    try:
        if not os.path.exists(FUND_DB_PATH):
            return None
        bdir = fund_backup_dir()
        os.makedirs(bdir, exist_ok=True)
        out_path = os.path.join(bdir, "funds_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3])
        conn_in = sqlite3.connect(FUND_DB_PATH)
        conn_out = sqlite3.connect(out_path)
        with conn_out:
            conn_in.backup(conn_out)
        conn_out.close()
        conn_in.close()
        conn_in = conn_out = None
        # 轮转: 只保留最近 N 份(文件名内嵌时间戳, 按名排序即按时间排序)
        files = sorted(f for f in os.listdir(bdir) if f.startswith("funds_") and f.endswith(".db"))
        for f in files[:-AUTO_BACKUP_KEEP]:
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
        _last_backup_ts = now
        return out_path
    except Exception:  # noqa: BLE001
        for h in (conn_in, conn_out):
            try:
                if isinstance(h, sqlite3.Connection):
                    h.close()
            except Exception:  # noqa: BLE001
                pass
        # 失败时清掉可能写了一半的备份文件
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return None


def fund_backup_list():
    """返回备份文件列表(新→旧): [{name, path, size, mtime, records, trades}]"""
    bdir = fund_backup_dir()
    out = []
    try:
        for f in os.listdir(bdir):
            if f.startswith("funds_") and f.endswith(".db"):
                p = os.path.join(bdir, f)
                st = os.stat(p)
                item = {"name": f, "path": p, "size": st.st_size, "mtime": st.st_mtime,
                        "records": None, "trades": None}
                # 顺带读出条数, 让用户在恢复前能看清这份备份里有什么
                try:
                    c = sqlite3.connect("file:%s?mode=ro" % p.replace("?", "%3f"), uri=True)
                    item["records"] = c.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                    item["trades"] = c.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
                out.append(item)
    except OSError:
        pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def fund_restore_backup(name):
    """用 <数据目录>/backup/<name> 这份 .db 快照覆盖当前库.

    ⚠ 恢复前先把「当前状态」再存一份快照 → 恢复错了还能退回
    ⚠ 用 sqlite3 backup API 反向写入, 不直接拷文件(库可能有关联的 -wal/-journal)
    返回 {records, trades, pools, restored}
    """
    global FUND_DB_CONN
    safe = os.path.basename((name or "").strip())
    if not safe or not safe.startswith("funds_") or not safe.endswith(".db"):
        raise ValueError("备份文件名不合法")
    src = os.path.join(fund_backup_dir(), safe)
    if not os.path.isfile(src):
        raise ValueError("备份不存在：%s" % safe)
    # 校验确实是本程序的备份(必须含三张表, 避免选到别的 sqlite 文件)
    chk = sqlite3.connect("file:%s?mode=ro" % src.replace("?", "%3f"), uri=True)
    try:
        tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        chk.close()
    for t in ("records", "trade_records", "trade_pool_snapshots"):
        if t not in tables:
            raise ValueError("该文件不是本软件的完整备份（缺少 %s 表）" % t)
    # 1) 恢复前保底: 当前状态再存一份
    fund_auto_backup("恢复前", force=True)
    # 2) 关连接, 用 backup API 把快照写回当前库
    if FUND_DB_CONN is not None:
        try:
            FUND_DB_CONN.close()
        except Exception:  # noqa: BLE001
            pass
        FUND_DB_CONN = None
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(FUND_DB_PATH)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    _fund_db()   # 重新打开并确保表结构
    return {
        "restored": safe,
        "records": len(fund_list_records()),
        "trades": len(trade_list_records()),
        "pools": len(trade_pool_list()),
    }



def fund_set_data_dir(new_dir):
    """设置新的数据目录(可指向网盘同步文件夹), 并把现有记录合并迁移过去
    返回 (迁移条数, 说明文字); 相同目录返回 (0, "same")
    ⚠ 必须迁移「全部三张表」: 资金曲线 records + 交易记录 trade_records + 监控池 trade_pool_snapshots.
      早期版本只迁移了 records → 换数据目录后交易记录全没了(用户会以为数据丢了)
    """
    global FUND_DB_PATH, FUND_DB_CONN
    if _env_data_dir:
        raise ValueError("当前由 OC_DATA_DIR 环境变量锁定数据目录，无法在界面修改")
    new_dir = os.path.abspath(os.path.expanduser((new_dir or "").strip()))
    if not new_dir:
        raise ValueError("数据目录不能为空")
    cur_dir = os.path.dirname(FUND_DB_PATH)
    if os.path.normcase(new_dir) == os.path.normcase(cur_dir):
        return 0, "same"
    # 读旧库全部数据(迁移用, 在关闭连接前) — 三张表都要
    old_records = fund_list_records()
    old_trades = trade_list_records()
    old_pools = trade_pool_list()
    # 切换前先给旧库留一份完整快照(切完即分离, 出问题可回退)
    fund_auto_backup("切换数据目录前", force=True)
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
    # 交易记录 / 监控池: 用保留原 id 的导入函数(幂等, 重复切换不会翻倍)
    for t in old_trades:
        try:
            trade_import_record(t)
            migrated += 1
        except Exception:  # noqa: BLE001
            pass
    for p in old_pools:
        try:
            trade_pool_import_record(p)
            migrated += 1
        except Exception:  # noqa: BLE001
            pass
    # 持久化配置
    cfg = _load_config()
    cfg["data_dir"] = new_dir
    _save_config(cfg)
    return migrated, "migrated"

def fund_data_info():
    """返回当前数据位置信息(UI 显示用)"""
    bks = fund_backup_list()
    return {
        "data_dir": os.path.dirname(FUND_DB_PATH),
        "db_path": FUND_DB_PATH,
        "db_exists": os.path.exists(FUND_DB_PATH),
        "record_count": len(fund_list_records()),
        "is_default": os.path.normcase(os.path.dirname(FUND_DB_PATH)) == os.path.normcase(_persistent_data_dir()),
        # ⚠ 数据是否落在软件目录内: 更新软件会连带删除, 前端需醒目提醒并提供一键迁出
        "risky": data_dir_risky(),
        "app_dir": _app_dir(),
        "suggest_dir": suggest_safe_data_dir(),     # 软件目录之外的推荐位置
        "env_locked": bool(_env_data_dir),          # 由 OC_DATA_DIR 锁定(测试模式)时禁止改
        "backup_dir": fund_backup_dir(),
        "backup_count": len(bks),
        "last_backup": bks[0] if bks else None,
    }


# ---------------------------------------------------------------------------
# 应用设置 (config.json 的 settings 字段): 默认权益 / 期货默认风险额度百分比
# ---------------------------------------------------------------------------
def get_settings():
    """读取用户设置: 返回 {futures_default_equity, options_default_equity, futures_risk_pct,
    options_risk_pct, frequent_futures, frequent_options} (无则 None/[])

    权益默认值期货/期权**互相独立**; 旧版单一 default_equity 作为两者的回退(向后兼容)。"""
    s = (_load_config().get("settings") or {})
    legacy = s.get("default_equity")   # v50.24 及以前的共用权益, 仅作回退

    def _pick(key):
        v = s.get(key)
        return v if v is not None else legacy

    return {
        "futures_default_equity": _pick("futures_default_equity"),
        "options_default_equity": _pick("options_default_equity"),
        "default_equity": legacy,   # 兼容旧前端读取
        "futures_risk_pct": s.get("futures_risk_pct"),
        "options_risk_pct": s.get("options_risk_pct"),
        "frequent_futures": s.get("frequent_futures") or [],
        "frequent_options": s.get("frequent_options") or [],
    }


def save_settings(patch):
    """保存用户设置 (合并, 只更新传入字段; None/空串 = 清除该默认值; 数组 = 覆盖)"""
    cfg = _load_config()
    s = cfg.setdefault("settings", {})
    for k in ("futures_default_equity", "options_default_equity", "default_equity"):
        if k in patch:
            v = patch[k]
            s[k] = None if v in (None, "") else float(v)
    # 已写入任一独立权益键 → 完成迁移, 清掉旧版共用键(避免清除后被 legacy 回退覆盖)
    if "futures_default_equity" in patch or "options_default_equity" in patch:
        s.pop("default_equity", None)
    # 风险额度: 只接受五档合法值(0.5/1/1.5/2/3), 其余只允许清除
    #   ⚠ 之前任意数字都能存进去(如 999), 虽前端按 <option> 匹配后不会应用, 但配置里留脏值易误导
    for _k, _opts in (("futures_risk_pct", FUTURES_RISK_OPTIONS),
                      ("options_risk_pct", OPTIONS_RISK_OPTIONS)):
        if _k not in patch:
            continue
        v = patch[_k]
        if v in (None, ""):
            s[_k] = None
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("风险额度必须为数字")
        if fv not in _opts:
            raise ValueError("风险额度只支持 %s" % " / ".join("%g" % x for x in _opts))
        s[_k] = fv
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


def fs_list_dirs(path=""):
    """列出目录下的子文件夹与可用盘符, 供软件内置的「文件夹浏览器」使用.

    ⚠ 不用 Windows 原生对话框: 原实现走 COM IFileOpenDialog, vtable 槽位算错
      (SetOptions 应在 9 / SetTitle 17 / GetResult 20, 却写成 19/21/22) → 调用直接失败;
      且对话框无属主窗口, 在浏览器窗口前面弹不出来, 用户体感就是「点了没反应」。
    返回: {ok, path, parent, dirs:[名称], drives:[C:\\], sep}
    """
    raw = (path or "").strip().strip('"')
    if raw:
        target = os.path.abspath(os.path.expanduser(raw))
    else:
        target = os.path.dirname(FUND_DB_PATH)
    # 路径不可用时回退: 先回盘根, 再回用户目录
    if not os.path.isdir(target):
        drive, _ = os.path.splitdrive(os.path.abspath(target))
        for cand in ((drive + os.sep) if drive else "", os.path.expanduser("~")):
            if cand and os.path.isdir(cand):
                target = cand
                break
        else:
            target = os.path.expanduser("~")
    drives = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = letter + ":\\"
            if os.path.exists(root):
                drives.append(root)
    dirs = []
    err = ""
    try:
        with os.scandir(target) as it:
            for e in it:
                if e.name.startswith("."):
                    continue                      # 跳过隐藏目录(.git 等)
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                # 跳过系统隐藏目录(如 C:\$Recycle.Bin)
                try:
                    if getattr(e.stat(), "st_file_attributes", 0) & 0x2:
                        continue
                except OSError:
                    pass
                dirs.append(e.name)
    except OSError as e:
        err = "无法读取该目录: %s" % e
    dirs.sort(key=lambda n: n.lower())
    parent = os.path.dirname(target.rstrip("\\/"))
    if not parent or os.path.normcase(parent) == os.path.normcase(target):
        parent = ""                                # 已在盘根 → 没有上一级
    return {
        "ok": not err,
        "error": err,
        "path": target,
        "parent": parent,
        "dirs": dirs,
        "drives": drives,
        "sep": os.sep,
        "count": len(dirs),
    }


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
        # 期权交易记录 (策略=abe, 与资金曲线共用 SQLite, 一次导出备份包含所有数据)
        FUND_DB_CONN.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy      TEXT    NOT NULL DEFAULT 'abe',
                underlying    TEXT    NOT NULL,     -- 开仓标的(合约基础), 如 ao611
                contract      TEXT    NOT NULL,     -- 完整合约代码, 如 ao611P2500
                op_type       TEXT    NOT NULL,     -- 'open' / 'close'
                open_date     TEXT,                 -- YYYY-MM-DD
                open_delta    REAL,
                target_delta  REAL,
                call_put      TEXT,                 -- 'C' 看涨 / 'P' 看跌
                direction     TEXT    NOT NULL,     -- 'buy' 买入 / 'sell' 卖出
                open_price    REAL,                 -- 开仓价(开仓记录)
                qty           INTEGER NOT NULL,     -- 开仓/平仓数量(平仓记录存 close_qty)
                premium       REAL    NOT NULL,     -- 权利金(元/手 × 数量)
                close_qty     INTEGER,              -- 仅平仓: 平仓数量
                close_price   REAL,                 -- 仅平仓: 平仓价
                pnl           REAL,                 -- 仅平仓: 逐笔盈亏
                close_date    TEXT,                 -- 仅平仓: 平仓日期
                note          TEXT,
                created_at    TEXT,
                updated_at    TEXT
            )
            """
        )
        # 监控池快照历史
        FUND_DB_CONN.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_pool_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy       TEXT    NOT NULL DEFAULT 'abe',
                snapshot_date  TEXT    NOT NULL,    -- YYYY-MM-DD
                contracts      TEXT    NOT NULL,    -- JSON 数组: ["si","lc","fu",...]
                note           TEXT,
                created_at     TEXT
            )
            """
        )
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
    """导出全部数据为备份 JSON (资金曲线 + 期权交易记录 + 监控池快照, 跨电脑迁移/定期备份用)"""
    return {
        "app": "期货开仓计算器",
        "backup_version": 2,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "records": fund_list_records(),
        "trades": trade_list_records(),
        "trade_pools": trade_pool_list(),
    }


def fund_import_backup(payload):
    """导入备份: 资金曲线 + 期权交易记录 + 监控池快照 逐条 upsert 合并(同主键覆盖, 其余保留)."""
    if not isinstance(payload, dict):
        raise ValueError("备份文件格式不正确")
    if not isinstance(payload.get("records"), list):
        raise ValueError("备份文件格式不正确（缺少 records 列表）")
    strategies = set()
    imported = 0
    if isinstance(payload.get("records"), list):
        for r in payload["records"]:
            if not isinstance(r, dict):
                raise ValueError("资金曲线条目格式不正确")
            try:
                year = int(r["year"])
                month = int(r["month"])
                initial_equity = float(r["initial_equity"])
                end_equity = float(r["end_equity"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("资金曲线条目缺少必要字段")
            cash_flow = float(r.get("cash_flow") or 0)
            cash = float(r.get("cash") or 0)
            note = str(r.get("note") or "")
            fund_upsert(r.get("strategy", "abe"), year, month, initial_equity,
                        end_equity, cash_flow, note, cash=cash)
            strategies.add(r.get("strategy", "abe"))
            imported += 1
    if isinstance(payload.get("trades"), list):
        for r in payload["trades"]:
            try:
                trade_import_record(r)   # 保留原 id 直接写入(不是 trade_upsert — 带 id 会走 UPDATE 而失败)
                imported += 1
            except Exception:
                pass   # 单条记录不合法则跳过, 不中断整批
    if isinstance(payload.get("trade_pools"), list):
        for r in payload["trade_pools"]:
            try:
                trade_pool_import_record(r)
                imported += 1
            except Exception:
                pass
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
# 期权交易记录 (abe 策略) — 与资金曲线共用 funds.db, 一次导出含所有数据
# ===========================================================================
TRADE_STRATEGY_DEFAULT = "abe"
ALLOWED_CALL_PUT = ("C", "P")
ALLOWED_DIRECTION = ("buy", "sell")
ALLOWED_OP_TYPE = ("open", "close")


_CP_SEP = "-_/ "   # C/P 前后允许的分隔符(国内期权代码写法多样)

# 国内期权合约的月份段: 四位(2611)或三位(611, 郑商所/上期所简写)
_MONTH_RE = re.compile(r"[0-9]{3,4}")
# 行权价段: 任意长度数字
_STRIKE_RE = re.compile(r"[0-9]+")


def _has_month(block):
    """block 内是否存在独立的 3-4 位月份数字段(前后都不是数字)"""
    return bool(re.search(r"(?<![0-9])[0-9]{3,4}(?![0-9])", block))


def _has_digits(block):
    return bool(_STRIKE_RE.search(block))


def _digit_run_left(s, i):
    """返回 s[:i] 末尾连续的纯数字串(碰到非数字即停)"""
    j = i - 1
    while j >= 0 and s[j].isdigit():
        j -= 1
    return s[j + 1:i]


def _digit_run_right(s, i):
    """返回 s[i+1:] 开头连续的纯数字串"""
    j = i + 1
    while j < len(s) and s[j].isdigit():
        j += 1
    return s[i + 1:j]


def find_cp_index(s):
    """在合约代码里定位「看涨看跌 C/P」那一位, 返回索引; 没有则 None.
    兼容写法:
      紧凑式   lc2611c144000 / sf611c6800 / ao611p2500  -> C/P 左右紧邻月份与行权价数字
      分隔式   lc2611-c-144000 / lc2611_c_144000        -> C/P 自成一段, 两侧是分隔符
      半分隔式 lc2611-c144000                           -> 左边分隔、右边直接接行权价
    ⚠ 三重坑:
      1) 24 个品种代码自带 C/P(玉米 c / 棉花 cf / 纸浆 sp / 聚丙烯 pp / 苹果 ap / 花生 pk),
         「有 c/p 就当标志位」会错判;
      2) 品种前缀里的 c/p 也可能「看起来合法」, 例如 hc2610 的 c 右边紧跟月份 2610、
         hc2610P6000 里 c 与 P 都满足结构 — 必须取「最右边」那个满足条件的位置;
      3) 判定必须依赖月份: 真实期权代码一定含月份(3-4 位), 没有月份的一律不认
         (aoc5000 / aoC5000 这类缺月份的写法无法与品种前缀区分, 不作为合法合约)."""
    if not s:
        return None
    n = len(s)

    def _is_sep(ch):
        return ch == "" or ch in _CP_SEP

    def _month_run_len(run):
        return 3 <= len(run) <= 4

    found = None
    for i, ch in enumerate(s):
        if ch not in "cpCP":
            continue
        left = s[i - 1] if i > 0 else ""
        right = s[i + 1] if i + 1 < n else ""
        leftblock = s[:i]
        rightblock = s[i + 1:]
        lrun = _digit_run_left(s, i)
        rrun = _digit_run_right(s, i)

        ok = False
        # 1) 紧凑式: 一侧是月份(3-4 位数字 run), 另一侧是行权价数字
        if left.isdigit() and right.isdigit():
            if _month_run_len(lrun) or _month_run_len(rrun):
                ok = True
        # 2) 分隔式: 左右紧邻都是分隔符/边界, 两侧块里须出现月份与行权价
        #    (月份可能在 C/P 左侧 lc2611-c-144000, 也可能在右侧 au-c-2611)
        elif _is_sep(left) and _is_sep(right):
            if (_has_month(leftblock) and _has_digits(rightblock)) or \
               (_has_digits(leftblock) and _has_month(rightblock)):
                ok = True
        # 3) 半分隔式: 一侧分隔、另一侧数字, 且分隔侧出现月份
        elif _is_sep(left) and right.isdigit() and _has_month(leftblock):
            ok = True
        elif left.isdigit() and _is_sep(right) and _has_month(rightblock):
            ok = True

        if ok:
            found = i   # 继续往后找, 取最右边一个(避开品种前缀里的 c/p)
    return found


def _looks_variety(block):
    """block 去掉分隔符后是否是纯字母(可能是品种代码), 且至少 1 个字母"""
    t = block.replace("-", "").replace("_", "").replace("/", "").strip()
    return bool(t) and t.isalpha()


# 分隔符统一为 "-"(用户实际写法), 避免同一合约因 _ / / 空格 不同又分裂
#   ⚠ 空白是「打字随手敲的」, 一律删掉; 只有 - _ / 才视为分段符
_SPACE_RE = re.compile(r"\s+")
_SEP_NORM = re.compile(r"[_/]+")


def _tidy_seps(s):
    """把空白去掉、把 _ / 统一成 '-'、合并连续 '-'、去掉首尾 '-'.
    例: 'br 2610 C 15800' -> 'br-2610-C-15800' ; 'lc2611_C_144000' -> 'lc2611-C-144000'"""
    s = _SPACE_RE.sub("", s.strip())
    s = _SEP_NORM.sub("-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _collapse_seps(s, cp_i):
    """归一化后把「非 C/P 两侧」的 '-' 去掉, 得到统一写法.
    cp_i 为小写 s 中 C/P 的索引.
    例: br-2610-C-15800 -> br2610C15800(C/P 两侧原本无 '-') ;
        lc2611-C-144000 -> lc2611-C-144000(C/P 两侧原本有 '-')"""
    ch = s[cp_i].upper()
    has_l = cp_i > 0 and s[cp_i - 1] == "-"
    has_r = cp_i + 1 < len(s) and s[cp_i + 1] == "-"
    left = s[:cp_i].replace("-", "")
    right = s[cp_i + 1:].replace("-", "")
    if not left:
        return (ch + ("-" if has_r else "") + right) if right else ch
    if not right:
        return left + ("-" if has_l else "") + ch
    return left + ("-" if has_l else "") + ch + ("-" if has_r else "") + right


def normalize_contract(c):
    """合约代码归一化: 品种代码小写, 看涨看跌的 C/P 大写, 分隔符统一.
    例: BR2610c15800 -> br2610C15800 ; LC2611-c-144000 -> lc2611-C-144000 ;
        AO611P2500 -> ao611P2500 ; br 2610 C 15800 -> br2610C15800
    ⚠ 只改「月份/行权价之间」的 C/P — 品种代码自带 C/P 的很多(玉米 c / 棉花 cf / 纸浆 sp / 棕榈油 p),
      在整串里盲目大写会把品种代码改坏."""
    if not isinstance(c, str):
        return c   # 非字符串原样返回(监控池 contracts 元素可能是 dict)
    low = _tidy_seps(c).lower()
    if not low:
        return low
    i = find_cp_index(low)
    if i is None:
        return low.replace("-", "")
    return _collapse_seps(low, i)


def detect_call_put(c):
    """从合约代码推断看涨(C)/看跌(P); 识别不出返回 ''"""
    if not isinstance(c, str):
        return ""
    s = _tidy_seps(c).lower()
    i = find_cp_index(s)
    return s[i].upper() if i is not None else ""


def _trade_record_to_dict(r):
    return {
        "id": r["id"],
        "strategy": r["strategy"],
        "underlying": r["underlying"],
        "contract": r["contract"],
        "op_type": r["op_type"],
        "open_date": r["open_date"] or "",
        "open_delta": r["open_delta"],
        "target_delta": r["target_delta"],
        "call_put": r["call_put"] or "",
        "direction": r["direction"],
        "open_price": r["open_price"],
        "qty": r["qty"],
        "premium": r["premium"] or 0.0,
        "close_qty": r["close_qty"],
        "close_price": r["close_price"],
        "pnl": r["pnl"],
        "close_date": r["close_date"] or "",
        "note": r["note"] or "",
        "created_at": r["created_at"] or "",
        "updated_at": r["updated_at"] or "",
    }


def trade_list_records(strategy=None):
    """列出交易记录; strategy=None 时返回所有策略的记录."""
    db = _fund_db()
    if strategy:
        rows = db.execute(
            "SELECT * FROM trade_records WHERE strategy=? ORDER BY id ASC",
            (strategy,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM trade_records ORDER BY id ASC").fetchall()
    return [_trade_record_to_dict(r) for r in rows]


def trade_upsert(payload):
    """新增/更新交易记录; 不传 id=新增; 传 id=更新. 自动维护 created_at/updated_at.
    ⚠ 先做一次合约归一化(品种小写 + C/P 大写), 并把「归一化后与本次保存内容完全相同的旧行」删掉 —
      否则 同一合约会因大小写被存成两条(旧行残留), 在筛选下拉里出现两次。"""
    db = _fund_db()
    now = datetime.now().isoformat(timespec="seconds")
    required = ("underlying", "contract", "op_type", "direction")
    for k in required:
        if not payload.get(k):
            raise ValueError("缺少必填字段: %s" % k)
    if payload["op_type"] not in ALLOWED_OP_TYPE:
        raise ValueError("op_type 必须为 open / close")
    if payload["direction"] not in ALLOWED_DIRECTION:
        raise ValueError("direction 必须为 buy / sell")

    strategy = TRADE_STRATEGY_DEFAULT   # 固定 abe(自定义策略 UI 已撤除)

    # 合约代码归一化(品种小写 + C/P 大写), 避免同一合约因大小写不同被当成两个
    payload["contract"] = normalize_contract(payload.get("contract"))
    _dup_id = payload.get("id")

    # 平仓数量校验: 不可超过开仓剩余(按合约大小写不敏感匹配)
    def _k(c): return (c or "").strip().upper()
    if payload["op_type"] == "close" and not payload.get("id"):
        cq = int(payload.get("close_qty") or 0)
        contract = payload.get("contract")
        underlying = payload.get("underlying")
        if cq <= 0:
            raise ValueError("平仓数量必须 > 0")
        opened = db.execute(
            "SELECT COALESCE(SUM(qty),0) FROM trade_records WHERE op_type='open' AND strategy=? AND underlying=? AND UPPER(TRIM(contract))=?",
            (strategy, underlying, _k(contract)),
        ).fetchone()[0]
        closed = db.execute(
            "SELECT COALESCE(SUM(close_qty),0) FROM trade_records WHERE op_type='close' AND strategy=? AND underlying=? AND UPPER(TRIM(contract))=?",
            (strategy, underlying, _k(contract)),
        ).fetchone()[0]
        if cq > opened - closed:
            raise ValueError("平仓数量(%d)超过剩余可平(%d)" % (cq, opened - closed))

    # 开仓数量/价格校验: 手数须为正整数、开仓价与权利金不得为负
    #   ⚠ 负数手数曾能存进去: 持仓按 qty>0 汇总 → 负数行不出现在持仓里,
    #     用户看到「已保存」却什么都查不到(隐形脏数据); 必须拦在入口
    if payload["op_type"] == "open":
        try:
            _qty = float(payload.get("qty"))
        except (TypeError, ValueError):
            raise ValueError("开仓手数必须为数字")
        if _qty <= 0:
            raise ValueError("开仓手数必须 > 0")
        if _qty != int(_qty):
            raise ValueError("开仓手数必须为整数")
        for _f, _label in (("open_price", "开仓价"), ("premium", "权利金")):
            _v = payload.get(_f)
            if _v in (None, ""):
                continue
            try:
                _fv = float(_v)
            except (TypeError, ValueError):
                raise ValueError("%s必须为数字" % _label)
            if _fv < 0:
                raise ValueError("%s不能为负数" % _label)

    rec_id = payload.get("id")
    fields = (
        "underlying", "contract", "op_type",
        "open_date", "open_delta", "target_delta", "call_put",
        "direction", "open_price", "qty", "premium",
        "close_qty", "close_price", "pnl", "close_date", "note",
    )
    # 兜底: close 类型允许 qty/premium 为空(平仓字段用 close_qty/pnl 表达; API 直调漏传不报错)
    if payload.get("op_type") == "close":
        if payload.get("qty") is None:
            payload["qty"] = 0
        if payload.get("premium") is None:
            payload["premium"] = 0.0
        if payload.get("open_price") is None:
            payload["open_price"] = 0.0
        if payload.get("open_date") is None:
            payload["open_date"] = ""
    vals = [payload.get(f) for f in fields]
    if rec_id:
        existing = db.execute("SELECT created_at FROM trade_records WHERE id=?", (rec_id,)).fetchone()
        if not existing:
            raise ValueError("记录不存在 id=%s" % rec_id)
        if payload["op_type"] == "close":
            cq = int(payload.get("close_qty") or 0)
            contract = payload.get("contract")
            underlying = payload.get("underlying")
            opened = db.execute(
                "SELECT COALESCE(SUM(qty),0) FROM trade_records WHERE op_type='open' AND strategy=? AND underlying=? AND UPPER(TRIM(contract))=?",
                (strategy, underlying, _k(contract)),
            ).fetchone()[0]
            closed_ex = db.execute(
                "SELECT COALESCE(SUM(close_qty),0) FROM trade_records WHERE op_type='close' AND strategy=? AND underlying=? AND UPPER(TRIM(contract))=? AND id<>?",
                (strategy, underlying, _k(contract), rec_id),
            ).fetchone()[0]
            if cq > opened - closed_ex:
                raise ValueError("平仓数量(%d)超过剩余可平(%d)" % (cq, opened - closed_ex))
        set_clause = ", ".join("%s=?" % f for f in fields)
        db.execute(
            "UPDATE trade_records SET " + set_clause + ", updated_at=? WHERE id=?",
            (*vals, now, rec_id),
        )
        return rec_id
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    db.execute(
        "INSERT INTO trade_records (strategy, " + cols + ", created_at, updated_at) VALUES (?, "
        + placeholders + ", ?, ?)",
        (strategy, *vals, now, now),
    )
    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    # 清理大小写旧行: 同一标的+合约(大小写不敏感)且「除 id/时间戳外全部字段相同」的旧行 → 只留新写入这条
    # ⚠ 必须逐字段全量比较, 只比 qty/日期 会把价格不同的两条真实记录误删
    _cmp_cols = [f for f in fields if f != "contract"]
    _cmp_sql = " AND ".join("IFNULL(%s,'')=IFNULL(?,'')" % c for c in _cmp_cols)
    _cmp_vals = [payload.get(c) for c in _cmp_cols]
    db.execute(
        "DELETE FROM trade_records WHERE id<>? AND strategy=? AND underlying=? "
        "AND UPPER(TRIM(contract))=? AND " + _cmp_sql,
        (new_id, strategy, payload["underlying"], payload["contract"].upper(), *_cmp_vals),
    )
    return new_id


def trade_delete(rec_id):
    db = _fund_db()
    db.execute("DELETE FROM trade_records WHERE id=?", (rec_id,))


def trade_groups(strategy=None):
    """主表汇总: 按 underlying 分组(主键=开仓标的), 输出每组:
       direction(主要方向), open_date(首次开仓), close_status(未平/部分平/全平),
       total_pnl(已实现盈亏), last_close_date(最后平仓日)."""
    recs = trade_list_records(strategy)
    if not recs:
        return []
    by_u = {}
    for r in recs:
        by_u.setdefault(r["underlying"], []).append(r)

    groups = []
    for u, items in by_u.items():
        opens = [x for x in items if x["op_type"] == "open"]
        closes = [x for x in items if x["op_type"] == "close"]
        # 首次开仓日期
        open_dates = [x["open_date"] for x in opens if x["open_date"]]
        first_open = min(open_dates) if open_dates else ""
        # 持仓数量 = 开仓 - 平仓 (按合约, 大小写不敏感避免扣减失败)
        def _k(c): return (c or "").strip().upper()
        by_c = {}
        for x in opens:
            by_c.setdefault(_k(x["contract"]), {"opened": 0, "closed": 0, "open_cost": 0.0,
                                                "direction": x.get("direction", "")})
            k = _k(x["contract"])
            by_c[k]["opened"] += x["qty"]
            by_c[k]["open_cost"] += (x["open_price"] or 0) * x["qty"]
        for x in closes:
            k = _k(x["contract"])
            if k not in by_c:
                by_c[k] = {"opened": 0, "closed": 0, "open_cost": 0.0, "direction": ""}
            by_c[k]["closed"] += (x["close_qty"] or 0)
        has_open_position = any(
            (c["opened"] - c["closed"]) > 0 for c in by_c.values()
        )
        total_pnl = sum((x["pnl"] or 0) for x in closes)
        close_dates = [x["close_date"] for x in closes if x["close_date"]]
        last_close = max(close_dates) if close_dates else ""
        # 方向: 主方向=持仓最多的合约的方向; 无持仓取最近开仓的方向
        direction = ""
        if has_open_position:
            for c_key, st in by_c.items():
                if (st["opened"] - st["closed"]) > 0:
                    rec = next((x for x in opens if _k(x["contract"]) == c_key), None)
                    if rec:
                        direction = rec["direction"]
                        break
        if not direction and opens:
            direction = opens[-1]["direction"]
        if not has_open_position and opens:
            close_status = "已平仓"
        elif has_open_position and total_pnl != 0:
            close_status = "部分平仓"
        else:
            close_status = "未平仓"
        groups.append({
            "underlying": u,
            "direction": direction,
            "open_date": first_open,
            "close_status": close_status,
            "total_pnl": round(total_pnl, 2),
            "last_close_date": last_close,
            # 该标的用到的合约(主表搜索要能按合约号命中)
            "contracts": sorted({(x.get("contract") or "").strip() for x in items if x.get("contract")}),
        })
    # 按开仓日期倒序: 最近开仓在最上
    groups.sort(key=lambda g: (g["open_date"] or ""), reverse=True)
    return groups


def trade_detail(underlying, strategy=TRADE_STRATEGY_DEFAULT):
    """单个标的详情: 上方持仓汇总(按 contract 分组, 仅算未平仓部分加权均价), 下方操作记录(按日期升序)."""
    db = _fund_db()
    rows = db.execute(
        "SELECT * FROM trade_records WHERE strategy=? AND underlying=? ORDER BY id ASC",
        (strategy, underlying),
    ).fetchall()
    items = [_trade_record_to_dict(r) for r in rows]
    opens = [x for x in items if x["op_type"] == "open"]
    closes = [x for x in items if x["op_type"] == "close"]

    # 按合约分组: 每个 open 维护自己的剩余手数, close 按时间 FIFO 逐单扣减(不按比例摊薄, 确保均价只算未平仓部分)
    def _k(c): return (c or "").strip().upper()
    groups = {}  # k -> {orig_contract, call_put, direction, opens:[{qty_left, price_each, premium_each}], left, cost_left, premium_left, last_open_date}
    for x in opens:
        k = _k(x["contract"])
        g = groups.setdefault(k, {
            "orig_contract": x["contract"], "call_put": x["call_put"], "direction": x["direction"],
            "opens": [], "left": 0, "cost_left": 0.0, "premium_left": 0.0, "last_open_date": "",
        })
        qty = x["qty"] or 0
        if qty <= 0:
            continue
        # 每手均价 + 每手权利金(平均到每手), close 时按手数精确扣减
        g["opens"].append({
            "qty_left": qty,
            "price_each": (x["open_price"] or 0),
            "premium_each": (x["premium"] or 0) / qty if qty else 0,
        })
        g["left"] += qty
        g["cost_left"] += (x["open_price"] or 0) * qty
        g["premium_left"] += x["premium"] or 0
        if (x["open_date"] or "") > g["last_open_date"]:
            g["last_open_date"] = x["open_date"] or g["last_open_date"]

    # close 按时间 FIFO(按 close_date 升序逐单扣减, 同一合约内按 open_date 升序的 opens 顺序扣)
    closes_sorted = sorted(closes, key=lambda x: x.get("close_date") or "")
    for x in closes_sorted:
        g = groups.get(_k(x["contract"]))
        if not g:
            continue
        cq = x["close_qty"] or 0
        if cq <= 0:
            continue
        for o in g["opens"]:
            if cq <= 0:
                break
            if o["qty_left"] <= 0:
                continue
            take = min(cq, o["qty_left"])
            o["qty_left"] -= take
            g["left"] -= take
            g["cost_left"] -= take * o["price_each"]
            g["premium_left"] -= take * o["premium_each"]
            cq -= take

    holdings = []
    for k, g in groups.items():
        if g["left"] <= 0:
            continue
        avg = round(g["cost_left"] / g["left"], 4) if g["left"] > 0 else 0
        holdings.append({
            "contract": g["orig_contract"],
            "call_put": g["call_put"],
            "direction": g["direction"],
            "open_price": avg,           # 仅未平仓部分加权均价
            "qty": g["left"],
            "premium": round(g["premium_left"], 2),
            "last_open_date": g["last_open_date"],
        })

    # 操作记录: 按日期升序(open_date 或 close_date)
    def _op_dt(x):
        return x["open_date"] if x["op_type"] == "open" else x["close_date"]
    ops = sorted(items, key=_op_dt)
    return {"underlying": underlying, "holdings": holdings, "operations": ops}


# ----- 监控池快照 -----
def trade_pool_list(strategy=TRADE_STRATEGY_DEFAULT):
    db = _fund_db()
    rows = db.execute(
        "SELECT * FROM trade_pool_snapshots WHERE strategy=? ORDER BY snapshot_date DESC, id DESC",
        (strategy,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            cs = json.loads(r["contracts"]) if r["contracts"] else []
        except Exception:
            cs = []
        out.append({
            "id": r["id"],
            "snapshot_date": r["snapshot_date"] or "",
            "contracts": cs,
            "note": r["note"] or "",
            "created_at": r["created_at"] or "",
        })
    return out


def trade_pool_upsert(payload):
    """新增/更新监控池快照; 不传 id=新建快照; 传 id=修改内容; 自动设 snapshot_date=今天"""
    db = _fund_db()
    now = datetime.now().isoformat(timespec="seconds")
    contracts = payload.get("contracts") or []
    if not isinstance(contracts, list):
        raise ValueError("contracts 必须是数组")
    contracts = [normalize_contract(c) for c in contracts]
    cs = json.dumps(contracts, ensure_ascii=False)
    note = payload.get("note") or ""
    rec_id = payload.get("id")
    if rec_id:
        existing = db.execute("SELECT snapshot_date FROM trade_pool_snapshots WHERE id=?", (rec_id,)).fetchone()
        if not existing:
            raise ValueError("快照不存在 id=%s" % rec_id)
        db.execute(
            "UPDATE trade_pool_snapshots SET contracts=?, note=?, snapshot_date=? WHERE id=?",
            (cs, note, payload.get("snapshot_date") or existing["snapshot_date"], rec_id),
        )
        return rec_id
    today = payload.get("snapshot_date") or datetime.now().date().isoformat()
    db.execute(
        "INSERT INTO trade_pool_snapshots (strategy, snapshot_date, contracts, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (TRADE_STRATEGY_DEFAULT, today, cs, note, now),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def trade_pool_delete(rec_id):
    db = _fund_db()
    db.execute("DELETE FROM trade_pool_snapshots WHERE id=?", (rec_id,))


# ----- 备份导入专用: 保留原 id 直接写入(同 id 覆盖), 使重复导入幂等 -----
# ⚠ 不能复用 trade_upsert / trade_pool_upsert — 它们见到 id 就走 UPDATE 分支,
#   目标库(如换电脑后的空库)没有该 id 会抛"记录不存在", 导致整批记录被静默丢弃。
_TRADE_IMPORT_COLS = (
    "id", "strategy", "underlying", "contract", "op_type", "open_date", "open_delta",
    "target_delta", "call_put", "direction", "open_price", "qty", "premium",
    "close_qty", "close_price", "pnl", "close_date", "note", "created_at", "updated_at",
)


def trade_import_record(r):
    """导入单条交易记录(备份恢复用): 保留原 id, 同 id 覆盖 → 重复导入幂等"""
    if not isinstance(r, dict):
        raise ValueError("交易记录条目格式不正确")
    for k in ("underlying", "contract", "op_type", "direction"):
        if not r.get(k):
            raise ValueError("交易记录缺少必填字段: %s" % k)
    if r["op_type"] not in ALLOWED_OP_TYPE:
        raise ValueError("op_type 必须为 open / close")
    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "id": r.get("id"),
        "strategy": r.get("strategy") or TRADE_STRATEGY_DEFAULT,
        "underlying": r["underlying"],
        "contract": normalize_contract(r["contract"]),
        "op_type": r["op_type"],
        "open_date": r.get("open_date") or "",
        "open_delta": r.get("open_delta"),
        "target_delta": r.get("target_delta"),
        "call_put": r.get("call_put"),
        "direction": r["direction"],
        "open_price": r.get("open_price") or 0,
        "qty": int(r.get("qty") or 0),
        "premium": float(r.get("premium") or 0),
        "close_qty": r.get("close_qty"),
        "close_price": r.get("close_price"),
        "pnl": r.get("pnl"),
        "close_date": r.get("close_date") or "",
        "note": r.get("note") or "",
        "created_at": r.get("created_at") or now,
        "updated_at": r.get("updated_at") or now,
    }
    db = _fund_db()
    if row["id"] is None:
        cols = [c for c in _TRADE_IMPORT_COLS if c != "id"]
        db.execute(
            "INSERT INTO trade_records (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" for _ in cols)),
            [row[c] for c in cols],
        )
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT OR REPLACE INTO trade_records (%s) VALUES (%s)" % (
            ", ".join(_TRADE_IMPORT_COLS), ", ".join("?" for _ in _TRADE_IMPORT_COLS)),
        [row[c] for c in _TRADE_IMPORT_COLS],
    )
    return row["id"]


def trade_pool_import_record(r):
    """导入单条监控池快照(备份恢复用): 保留原 id, 同 id 覆盖"""
    if not isinstance(r, dict):
        raise ValueError("监控池条目格式不正确")
    contracts = r.get("contracts") or []
    if not isinstance(contracts, list):
        raise ValueError("contracts 必须是数组")
    contracts = [normalize_contract(c) for c in contracts]
    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "id": r.get("id"),
        "strategy": r.get("strategy") or TRADE_STRATEGY_DEFAULT,
        "snapshot_date": r.get("snapshot_date") or datetime.now().date().isoformat(),
        "contracts": json.dumps(contracts, ensure_ascii=False),
        "note": r.get("note") or "",
        "created_at": r.get("created_at") or now,
    }
    cols = ("id", "strategy", "snapshot_date", "contracts", "note", "created_at")
    db = _fund_db()
    if row["id"] is None:
        cols2 = [c for c in cols if c != "id"]
        db.execute(
            "INSERT INTO trade_pool_snapshots (%s) VALUES (%s)" % (", ".join(cols2), ", ".join("?" for _ in cols2)),
            [row[c] for c in cols2],
        )
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT OR REPLACE INTO trade_pool_snapshots (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" for _ in cols)),
        [row[c] for c in cols],
    )
    return row["id"]


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
        elif path == "/api/version":
            self._send(200, _json({"ok": True, "app": APP_NAME, "version": APP_VERSION}))
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

        # ---- 期权交易记录 ----
        elif path == "/api/trades/groups":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            strategy = (qs.get("strategy") or [None])[0]   # None → 全部策略
            self._send(200, _json({"ok": True, "groups": trade_groups(strategy)}))

        elif path == "/api/trades/detail":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            underlying = qs.get("underlying", [""])[0]
            strategy = (qs.get("strategy") or [TRADE_STRATEGY_DEFAULT])[0]
            if not underlying:
                self._send(200, _json({"ok": False, "error": "缺少 underlying"}))
                return
            self._send(200, _json({"ok": True, **trade_detail(underlying, strategy)}))

        elif path == "/api/trades/pool":
            self._send(200, _json({"ok": True, "snapshots": trade_pool_list()}))

        elif path == "/api/funds/data-info":
            self._send(200, _json({"ok": True, **fund_data_info()}))

        elif path == "/api/funds/backups":
            self._send(200, _json({"ok": True, "backups": fund_backup_list()}))

        elif path == "/api/fs/list":
            # 内置文件夹浏览器: 列子目录 + 盘符(替代原来会静默失败的 Windows 原生对话框)
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            want = (qs.get("path") or [""])[0]
            self._send(200, _json(fs_list_dirs(want)))

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
                    fund_auto_backup("资金曲线-删除")
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
                    fund_auto_backup("资金曲线-保存")
                    self._send(200, _json({"ok": True, "msg": "已保存"}))
                return
            elif path == "/api/funds/import":
                # 导入前先留一份(万一导入的内容不对还能退回)
                fund_auto_backup("导入前", force=True)
                imported, strategies = fund_import_backup(params)
                fund_auto_backup("导入后", force=True)
                self._send(200, _json({
                    "ok": True,
                    "msg": "导入完成",
                    "imported": imported,
                    "strategies": strategies,
                }))
                return
            elif path == "/api/funds/clear-all":
                # ⚠ 清空前先备份: 清完再备份就只剩空库了, 起不到保护作用
                fund_auto_backup("清除前", force=True)
                ok, info = fund_clear_all_safe()
                if ok:
                    self._send(200, _json({"ok": True, "remaining": info, "msg": "已清除全部记录"}))
                else:
                    self._send(200, _json({"ok": False, "error": info}))
                return
            elif path == "/api/funds/backup":
                p = fund_auto_backup("手动", force=True)
                if p:
                    self._send(200, _json({"ok": True, "path": p, "msg": "已备份"}))
                else:
                    self._send(200, _json({"ok": False, "error": "备份失败：数据文件不存在或不可写"}))
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
            elif path == "/api/funds/restore":
                # 用 backup 目录里的某个 .db 快照恢复当前库(恢复前会给当前库再存一份)
                cnt = fund_restore_backup(params.get("name", ""))
                self._send(200, _json({"ok": True, **cnt, "msg": "已恢复"}))
                return

            # ---- 期权交易记录 (POST) ----
            elif path == "/api/trades/upsert":
                rec_id = trade_upsert(params)
                fund_auto_backup("交易记录-保存")
                self._send(200, _json({"ok": True, "id": rec_id, "msg": "已保存"}))
                return
            elif path == "/api/trades/delete":
                trade_delete(int(params["id"]))
                fund_auto_backup("交易记录-删除")
                self._send(200, _json({"ok": True, "msg": "已删除"}))
                return
            elif path == "/api/trades/pool/upsert":
                rec_id = trade_pool_upsert(params)
                fund_auto_backup("监控池-保存")
                self._send(200, _json({"ok": True, "id": rec_id, "msg": "已保存"}))
                return
            elif path == "/api/trades/pool/delete":
                trade_pool_delete(int(params["id"]))
                fund_auto_backup("监控池-删除")
                self._send(200, _json({"ok": True, "msg": "已删除"}))
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
    """优先使用 prefer 端口, 被占用则随机分配; 支持环境变量 OC_PORT 强制指定(测试/多开用)
    ⚠ 不再设置 SO_REUSEADDR: Windows 上该选项允许多进程同绑一端口, 会导致 HTTP 请求
    随机路由到旧实例(页面/接口版本错配)。默认独占绑定, 第二个实例会绑定失败自动换端口。"""
    try:
        prefer = int(os.environ.get("OC_PORT") or prefer)
    except (TypeError, ValueError):
        pass
    for port in (prefer, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            actual = s.getsockname()[1]
            s.close()
            return actual
        except OSError:
            continue
    return 0


def _peer_info(port=8765):
    """探测 port 上是否运行着本应用实例(单实例接管用)。
    返回: None=无本应用(空闲或被他程序占用); 0=本应用旧版(无 /api/version); >0=该实例版本号"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % port, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
            if d.get("ok") and d.get("app") == APP_NAME:
                v = d.get("version")
                return int(v) if str(v).isdigit() else 0
    except Exception:  # noqa: BLE001
        pass
    # 旧版本实例没有 /api/version 路由 → 用 /api/health 兜底识别(响应体小, 避免大 HTML 中断)
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
            if d.get("app") == APP_NAME:
                return 0
    except Exception:  # noqa: BLE001
        pass
    return None


def _ask_shutdown(port=8765):
    """请已运行的实例退出(供新版本接管端口); /api/shutdown 为 GET 路由, 响应后 0.5s 自杀"""
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/shutdown" % port, timeout=1.5)
    except Exception:  # noqa: BLE001
        pass


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
    # 单实例接管: 若 8765 已有实例在跑 → 旧版(<本版)请其退出后本实例接管; 同版/更新版则直接复用其窗口
    #   设 OC_NO_TAKEOVER=1 可跳过此检查(配合 OC_PORT 另起一个实例做测试, 不影响正在用的窗口)
    if not os.environ.get("OC_NO_TAKEOVER"):
        peer = _peer_info()
        if peer is not None:
            if peer == 0 or peer < APP_VERSION:
                _ask_shutdown()
                for _ in range(10):            # 最多等 ~2s 让旧实例退出释放端口
                    time.sleep(0.2)
                    if _peer_info() is None:
                        break
            else:
                open_browser("http://127.0.0.1:8765/")
                return

    port = find_free_port()
    url = "http://127.0.0.1:%d/" % port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.last_request_time = time.time()

    # 启动即备份一次(即使本次不修改任何数据, 也留下一份"上次打开时"的状态)
    try:
        _bk = fund_auto_backup("启动", force=True)
        if _bk:
            print("已自动备份: %s" % _bk)
        if data_dir_risky():
            print("⚠ 数据目录在软件目录内(%s) — 更新软件会删除数据, 建议在「⚙ 数据位置」里迁到软件目录之外"
                  % os.path.dirname(FUND_DB_PATH))
    except Exception:  # noqa: BLE001
        pass

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
<link rel="icon" type="image/x-icon" href="/favicon.ico?v=55">
<script src="/chart.min.js"></script>
<style>
:root{
  /* 护眼暗色(夜间): 低蓝光墨绿黑, 减少刺激 */
  --bg:#0e1612; --panel:#152019; --panel2:#1b2920; --border:rgba(150,205,175,.13);
  --text:#e2efe6; --sub:#93a898; --accent:#4cd493; --accent2:#93e6b8;
  --good:#2ecc8f; --bad:#ff6b6b; --warn:#ffb86b; --gold:#f5c76b;
  --modal-mask:rgba(8,14,10,.62);
  --shadow:0 18px 50px rgba(0,0,0,.45);
  --glow1:rgba(90,190,150,.14);   /* 背景右上光晕(护眼绿) */
  --glow2:rgba(130,210,170,.08);  /* 背景左下光晕 */
  --chart-grid:rgba(150,205,175,.12); --chart-tick:#9db3a2;  /* 图表网格/刻度 */
  --chart-stroke:rgba(11,16,32,.85); --chart-label:#d9e6dc;    /* 数值标签描边/填充 */
  /* ===== 字号档位变量 (默认=中) =====
     切换: <body class="fz-sm|fz-md|fz-lg">, 覆盖整组变量即整体放大/缩小 */
  --fz-th:14px;      /* 表格表头 */
  --fz-td:15px;      /* 表格正文(资金曲线 .tbl td) */
  --fz-td2:14.5px;   /* 交易记录 td */
  --fz-tag:13.5px;   /* 标签 */
  --fz-btn:13.5px;   /* 小按钮 xs/ghost */
  --fz-btn2:14.5px;  /* 中按钮 sm */
  --fz-h2:16.5px;    /* 卡片标题 */
  --fz-seg:15px;     /* seg 切换按钮 */
  --fz-chk:14.5px;   /* 复选框 */
  --fz-tip:14px;     /* 提示 */
  --fz-icon:17px;    /* 行内 ✎🗑 图标 */
  --fz-opf:14px;     /* 筛选行 */
  --fz-lbl:13.5px;   /* 表单标签 */
  --fz-input:15px;   /* 输入框/下拉 */
  --fz-micro:12px;   /* 极小说明(mode small/ladder-note 等) */
  --fz-small:12.5px; /* 次级说明(bignum .s/unit .u 等) */
  --fz-mid:13px;     /* 常规小字(quote qname/bignum .t 等) */
  --fz-fml:12px;     /* 预算公式行 */
  --fz-legend:13px;  /* 图表图例 */
  --fz-chart:13px;    /* Chart.js 刻度字号(实时读取) */
  --fz-chart2:14px;   /* Chart.js 数值标签 */
}
body.fz-sm{
  --fz-th:12.5px; --fz-td:13.5px; --fz-td2:13px; --fz-tag:12px;
  --fz-btn:12px; --fz-btn2:13px; --fz-h2:15px; --fz-seg:13.5px;
  --fz-chk:13px; --fz-tip:12.5px; --fz-icon:15px; --fz-opf:12.5px;
  --fz-lbl:12.5px; --fz-input:13.5px;
  --fz-micro:11px; --fz-small:11.5px; --fz-mid:12px; --fz-fml:11px;
  --fz-legend:11.5px; --fz-chart:11.5px; --fz-chart2:12.5px;
}
body.fz-lg{
  --fz-th:15.5px; --fz-td:17px; --fz-td2:16px; --fz-tag:15px;
  --fz-btn:15px; --fz-btn2:16.5px; --fz-h2:18.5px; --fz-seg:17px;
  --fz-chk:16px; --fz-tip:15.5px; --fz-icon:19px; --fz-opf:15.5px;
  --fz-lbl:15px; --fz-input:17px;
  --fz-micro:13.5px; --fz-small:14px; --fz-mid:14.5px; --fz-fml:13.5px;
  --fz-legend:14.5px; --fz-chart:15px; --fz-chart2:16px;
}
[data-theme="light"]{
  /* 护眼浅色(白天): 豆绿底 + 米绿卡片(明显非纯白) */
  --bg:#e6efe0; --panel:#f0f6e9; --panel2:#dde8d3; --border:#c9d8c0;
  --text:#2c3a2e; --sub:#5f7663; --accent:#18a163; --accent2:#0f9e6e;
  --good:#16a06b; --bad:#e05252; --warn:#d98a1f; --gold:#b8860b;
  --modal-mask:rgba(60,80,60,.28);
  --shadow:0 18px 44px rgba(50,90,60,.10);
  --glow1:rgba(120,190,130,.18);  /* 背景右上光晕(柔和绿) */
  --glow2:rgba(170,215,160,.16);  /* 背景左下光晕 */
  --chart-grid:rgba(90,130,100,.14); --chart-tick:#54685a;  /* 图表网格/刻度(浅底深字) */
  --chart-stroke:rgba(50,80,58,.65); --chart-label:#ffffff;   /* 数值标签: 深绿描边+白字(浅色柱顶) */
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  transition:background .35s,color .35s;
  background-image:
    radial-gradient(900px 420px at 85% -10%, var(--glow1), transparent 60%),
    radial-gradient(700px 380px at -10% 30%, var(--glow2), transparent 55%);
}
.wrap{max-width:1100px;margin-left:0;margin-right:auto;padding:28px 20px 60px}

/* 顶部 */
header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:26px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:14px}
/* 顶部字号切换(置顶左侧, 小/中/大) */
.fzseg{display:flex;gap:2px;background:var(--panel);border:1px solid var(--border);
  border-radius:12px;padding:3px;flex:none;align-self:center}
.fzseg button{padding:6px 11px;border-radius:8px;cursor:pointer;border:0;
  background:transparent;color:var(--sub);font-weight:600;font-size:var(--fz-mid);
  line-height:1;transition:all .2s}
.fzseg button.active{background:linear-gradient(135deg,#3ecf8f,#28b470);color:#fff}
.fzseg button:not(.active):hover{background:var(--panel2);color:var(--text)}
.logo{width:46px;height:46px;border-radius:14px;flex:none;
  background:#0E1620;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 8px 24px rgba(10,18,28,.5)}
.logo svg{width:34px;height:34px;display:block}
.brand h1{font-size:21px;letter-spacing:.5px}
.brand p{font-size:var(--fz-mid);color:var(--sub);margin-top:2px}
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
.card h2{font-size:var(--fz-h2);color:var(--sub);font-weight:600;letter-spacing:1px;
  margin-bottom:16px;display:flex;align-items:center;gap:8px}
.card h2 .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}

label{display:block;font-size:var(--fz-lbl);color:var(--sub);margin:14px 0 6px}
input,select{width:100%;padding:11px 13px;border-radius:11px;border:1px solid var(--border);
  background:var(--panel2);color:var(--text);font-size:var(--fz-input);outline:none;
  transition:border .2s,box-shadow .2s;font-family:inherit}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(62,207,143,.18)}
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
  background:radial-gradient(160px 90px at 50% -10%,rgba(62,207,143,.22),transparent 70%)}
.bignum .t{font-size:12px;color:var(--sub);position:relative}
.bignum .n{font-size:46px;font-weight:800;position:relative;line-height:1.15;
  background:linear-gradient(135deg,#fff,#b9ccff);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
[data-theme="light"] .bignum .n{background:linear-gradient(135deg,#1e5f3c,#18a163);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
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
  background:linear-gradient(135deg,rgba(62,207,143,.07),transparent 60%)}
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
/* 平仓状态 tag 浅色主题(保证浅底可读): 未平仓=深绿 / 部分=深橙 / 已平仓=灰 */
[data-theme="light"] .tag.closed{background:rgba(110,120,115,.13);color:#5d6b63}
[data-theme="light"] .tag.partial{background:rgba(217,138,31,.15);color:#b8720f}
[data-theme="light"] .tag.unclosed{background:rgba(22,160,107,.15);color:#16a06b}
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
  background:rgba(62,207,143,.1);border:1px solid rgba(62,207,143,.35);border-radius:8px;padding:4px 12px}
.plans-item:hover .go{background:rgba(62,207,143,.2)}
.plans-item .go:active{transform:scale(.96)}

.tip{margin-top:16px;font-size:var(--fz-tip);color:var(--sub);line-height:1.8;
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
.maintab .mi{width:22px;height:22px;flex:none;display:flex;align-items:center;justify-content:center}
.maintab .mi svg{width:100%;height:100%;display:block}
.maintab small{font-weight:400;font-size:10px;opacity:.72}
.maintab.active{background:linear-gradient(135deg,#3ecf8f,#28b470);color:#fff;
  box-shadow:0 10px 26px rgba(62,207,143,.35)}
.maintab.active small{opacity:.92}
.maintab:not(.active):hover{background:var(--panel2);color:var(--text)}
.main{flex:1;min-width:0}

/* 侧边栏额外操作区(图标按钮, 不重叠) */
.side-extras{margin-top:auto;width:100%;display:flex;flex-direction:column;gap:8px;
  padding-top:14px;border-top:1px solid var(--panel2)}
.side-btn{padding:10px 4px;border-radius:10px;border:1px solid var(--panel2);
  background:var(--panel);color:var(--text);font-size:18px;cursor:pointer;
  text-align:center;line-height:1;min-height:36px;
  transition:background .15s,border-color .15s,transform .1s}
.side-btn:hover{background:var(--panel2);border-color:var(--accent)}
.side-btn:active{transform:scale(0.94)}

.trades-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;gap:16px;flex-wrap:wrap}
/* 交易记录页 toolbar: 左侧筛选, 右侧操作按钮 */
.trades-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;width:100%}
.trades-toolbar > .spacer{flex:1}
.chk{display:flex;align-items:center;gap:6px;font-size:var(--fz-chk);color:var(--text);cursor:pointer;white-space:nowrap}
.chk input{accent-color:var(--accent);margin:0}
/* 交易记录页 - 主表保持原宽(拉宽窗口位置不变), 分页面 fixed 浮在右侧(不挤压主表) */
.trades-layout{display:block;position:relative}
.trades-layout.has-detail .trades-main{width:100%;min-width:0}
.trades-layout.has-detail .trades-side{
  position:fixed;top:60px;right:0;width:min(1240px,86vw);height:calc(100vh - 60px);
  z-index:50;background:var(--panel);border-left:1px solid var(--panel2);
  box-shadow:-4px 0 18px rgba(0,0,0,.4);overflow:auto;animation:fadeSlide .25s ease
}
@media (max-width:1100px){
  .trades-layout.has-detail .trades-side{
    position:relative;top:auto;right:auto;width:100%;height:auto;
    border-left:none;box-shadow:none
  }
}
.trades-side{animation:fadeSlide .25s ease}
.trades-tbl th,.trades-tbl td{white-space:nowrap}
.tblwrap{overflow-x:auto}
/* 表头帮助问号 tooltip(仅算未平仓部分说明等) */
.help-tip{position:relative;cursor:help;border-bottom:1px dashed var(--sub)}
.help-tip:hover::after{
  content:attr(data-tip);position:absolute;left:50%;top:calc(100% + 6px);transform:translateX(-50%);
  background:var(--panel2);color:var(--text);padding:6px 10px;border-radius:6px;
  border:1px solid var(--border);box-shadow:0 4px 12px rgba(0,0,0,.3);
  font-size:11px;line-height:1.4;white-space:normal;width:max-content;max-width:260px;
  z-index:100;pointer-events:none
}
@keyframes fadeSlide{from{opacity:0;transform:translateX(10px)}to{opacity:1;transform:translateX(0)}}

/* 详情面板 header: 左标题 + 右操作按钮组(新建开仓/新建平仓/关闭) */
.td-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.td-head-actions{display:flex;gap:8px;flex-wrap:wrap}
/* 详情页操作记录: 标题 + 合约筛选 同行 */
.op-filter-row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin:18px 0 8px}
.op-filter{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--sub);white-space:nowrap}
.op-filter select{padding:3px 6px;border-radius:6px;border:1px solid var(--panel2);
  background:var(--panel2);color:var(--text);font-size:12px;max-width:180px}
.trades-tbl th{font-size:11px}
.trades-tbl td{font-size:12px;padding:6px 8px}
.trades-tbl tr.row-open td{background:rgba(62,207,143,.04)}
.trades-tbl tr.row-close td{background:rgba(255,107,155,.04)}
.trades-tbl tr.clickable{cursor:pointer}
.trades-tbl tr.clickable:hover{background:var(--panel2)}
.tag{display:inline-block;padding:2px 7px;border-radius:6px;font-size:var(--fz-tag);font-weight:600}
.tag.long{background:rgba(255,107,107,.18);color:#ff8484}
.tag.short{background:rgba(70,214,234,.18);color:#46d6ea}
.tag.open{background:rgba(62,207,143,.18);color:#63d89c}
.tag.close{background:rgba(255,107,155,.18);color:#ff84b9}
/* 平仓状态三态区分: 未平仓=绿(持仓中) / 部分平仓=橙(进行中) / 已平仓=灰(已了结归档) */
.tag.closed{background:rgba(150,160,155,.16);color:#8d9a92}
.tag.partial{background:rgba(255,180,80,.18);color:#ffb450}
.tag.unclosed{background:rgba(62,207,143,.18);color:#63d89c}
/* 方向色(中国习惯): 买入红 / 卖出青; 看涨红 / 看跌青 */
.tag.buy{background:rgba(255,107,107,.18);color:#ff8484}
.tag.sell{background:rgba(70,214,234,.18);color:#46d6ea}
.iconbtn{background:transparent;border:none;color:var(--sub);cursor:pointer;font-size:14px;padding:2px 6px;border-radius:6px}
.iconbtn:hover{background:var(--panel2);color:var(--accent)}
.row-actions{display:flex;gap:4px;justify-content:flex-end}
/* 操作记录「备注」列(在操作列前): 限宽省略, 悬停 title 看全文 */
.op-note{color:var(--sub)}
.op-note-txt{display:inline-block;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}

/* 监控池 */
.pool-block{margin-bottom:14px}
.pool-title{font-size:var(--fz-mid);font-weight:600;color:var(--accent2);display:flex;align-items:center;gap:8px;cursor:pointer}
.pool-title .arrow{transition:transform .15s}
.pool-title.collapsed .arrow{transform:rotate(-90deg)}
.pool-content{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;font-size:var(--fz-small);line-height:1.7}
.pool-chip{background:var(--panel2);padding:2px 8px;border-radius:6px;font-family:Consolas,monospace;font-size:var(--fz-small)}

/* 录入对话框(共用) */
.formgrid{display:grid;grid-template-columns:1fr 1fr;column-gap:14px;row-gap:12px;margin-top:12px}
.formgrid label{display:flex;flex-direction:column;gap:4px;font-size:var(--fz-lbl);color:var(--sub);align-items:stretch;min-width:0}
.formgrid label>input,
.formgrid label>select{width:100%;margin:0;box-sizing:border-box;min-width:0}
.formgrid .full{grid-column:1/-1}
.formgrid .req{display:inline-flex;align-items:center;gap:3px;white-space:nowrap;font-weight:600;color:var(--text)}
.formgrid .req i{color:#ff8484;font-style:normal;font-weight:bold;margin-left:1px}
/* 平仓时字段禁用样式 */
.formgrid label.disabled{opacity:.55}
.formgrid label.disabled input,.formgrid label.disabled select{pointer-events:none}
@media (max-width:640px){
  .side{width:64px;padding:16px 6px;gap:10px}
  .maintab{font-size:11px;padding:12px 2px}
  .maintab .mi{width:18px;height:18px}
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
.funds-bar .seg button.active{background:linear-gradient(135deg,#3ecf8f,#28b470);color:#fff}
.funds-bar .seg button:not(.active):hover{background:var(--panel2);color:var(--text)}
/* 累计提现展示 */
.wchip{padding:4px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;font-size:11.5px;white-space:nowrap}
.wchip b{color:var(--accent)}
.btn{padding:10px 18px;border-radius:11px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;cursor:pointer;transition:all .2s;font-size:var(--fz-btn)}
.btn:hover{transform:translateY(-1px);background:var(--panel);border-color:var(--accent)}
.btn.primary{background:linear-gradient(135deg,#3ecf8f,#28b470);border:0;color:#fff;
  box-shadow:0 6px 18px rgba(40,180,112,.3)}
.btn.primary:hover{box-shadow:0 10px 24px rgba(40,180,112,.45)}
.btn.danger{background:rgba(255,107,107,.12);border-color:rgba(255,107,107,.3);color:var(--bad)}
.btn.danger:hover{background:rgba(255,107,107,.22)}
.btn.sm{padding:6px 12px;font-size:var(--fz-btn2)}
.btn.xs{padding:3px 10px;font-size:var(--fz-btn);border-radius:8px;vertical-align:middle}
.btn.ghost{background:transparent;border-color:var(--border);color:var(--sub)}
.btn.ghost:hover{color:var(--accent);border-color:var(--accent)}
/* 交易记录动作按钮配色: 开仓(买入/做多)红, 平仓(卖出/做空)青 — 中国习惯 */
.btn.rose{background:linear-gradient(135deg,#ff8484,#ff5f6d);border:0;color:#fff;
  box-shadow:0 4px 12px rgba(255,95,109,.28)}
.btn.rose:hover{box-shadow:0 8px 20px rgba(255,95,109,.42)}
.btn.cyan{background:linear-gradient(135deg,#46d6ea,#21b6cf);border:0;color:#0b2230;
  box-shadow:0 4px 12px rgba(70,214,234,.25)}
.btn.cyan:hover{box-shadow:0 8px 20px rgba(70,214,234,.4)}

/* 期权品种单选按钮组 */
.chipgroup{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px}
.chip{padding:8px 14px;border-radius:10px;border:1px solid var(--border);background:var(--panel2);
  color:var(--text);font-weight:600;font-size:12.5px;cursor:pointer;transition:all .2s;user-select:none}
.chip small{display:block;font-weight:400;font-size:var(--fz-micro);opacity:.65;margin-top:1px}
.chip:hover{border-color:var(--accent);transform:translateY(-1px)}
.chip.active{background:linear-gradient(135deg,#3ecf8f,#28b470);border-color:transparent;color:#fff;
  box-shadow:0 6px 16px rgba(40,180,112,.35)}
.chip.active small{opacity:.85}
/* 常用区 (favorites): 横向 chip 列表, 悬停右上角 X 可删除 */
.freq{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 6px;align-items:center}
.freq .lbl{font-size:12px;color:var(--sub);margin-right:4px}
.fchip{position:relative;padding:6px 24px 6px 12px;border-radius:9px;border:1px solid var(--border);
  background:var(--panel2);color:var(--text);font-size:12.5px;cursor:pointer;transition:all .2s;user-select:none}
.fchip:hover{border-color:var(--accent)}
.fchip.active{background:linear-gradient(135deg,#3ecf8f,#28b470);border-color:transparent;color:#fff}
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
  background:linear-gradient(135deg,#3ecf8f,#28b470);color:#fff;border:0;cursor:pointer;
  box-shadow:0 6px 20px rgba(62,207,143,.45);font-size:22px;z-index:100;transition:transform .2s,box-shadow .2s}
.floating-contact:hover{transform:scale(1.1);box-shadow:0 8px 26px rgba(62,207,143,.6)}

.tbl{width:100%;border-collapse:collapse;font-size:var(--fz-td2);margin-top:6px}
.tbl th{text-align:left;padding:10px 10px;color:var(--sub);font-weight:600;font-size:var(--fz-th);
  letter-spacing:.5px;border-bottom:1px solid var(--border);background:var(--panel2);
  position:sticky;top:0;z-index:2}   /* 表头固定: 滚动时首行不消失 */
.tbl td{padding:11px 10px;border-bottom:1px solid var(--border)}
.tbl tr:hover td{background:var(--panel2)}
.tbl .num{text-align:right;font-variant-numeric:tabular-nums}

/* ===== 交易记录 / 资金曲线 字号放大 (2026-09-09 v3, 变量驱动支持三档) ===== */
#tradesArea .tbl th, #fundsArea .tbl th{font-size:var(--fz-th)}
#tradesArea .tbl td, #fundsArea .tbl td{font-size:var(--fz-td)}
#tradesArea .trades-tbl th{font-size:var(--fz-th)}
#tradesArea .trades-tbl td{font-size:var(--fz-td2);padding:8px 10px}
#tradesArea .tag, #fundsArea .tag{font-size:var(--fz-tag);padding:3px 9px}
#tradesArea .btn.xs, #fundsArea .btn.xs{font-size:var(--fz-btn);padding:5px 13px}
#tradesArea .btn.sm, #fundsArea .btn.sm{font-size:var(--fz-btn2);padding:7px 15px}
#tradesArea .btn.ghost, #fundsArea .btn.ghost{font-size:var(--fz-btn)}
#tradesArea .chk{font-size:var(--fz-chk)}
#tradesArea .op-filter{font-size:var(--fz-opf)}
#tradesArea .op-filter select{font-size:var(--fz-opf);padding:5px 9px}
#tradesArea .iconbtn{font-size:var(--fz-icon)}
#tradesArea .card h2, #fundsArea .card h2{font-size:var(--fz-h2)}
#fundsArea .funds-bar .seg button{font-size:var(--fz-seg);padding:9px 18px}
#fundsArea .funds-bar .btn.sm{font-size:var(--fz-btn2);padding:7px 13px}
#tradesArea .tip, #fundsArea .tip{font-size:var(--fz-tip)}
#fundsArea .chartbox .legend{font-size:var(--fz-legend)}

/* ===== 开仓计算页 字号放大 (变量驱动) ===== */
#calcArea label{font-size:var(--fz-lbl)}
#calcArea .dirrow .dirlabel{font-size:var(--fz-lbl)}
#calcArea .unit-suffix .u{font-size:var(--fz-lbl)}
#calcArea .budgetbar .k{font-size:var(--fz-lbl)}
#calcArea .budgetbar .k .fml{font-size:var(--fz-fml)}
#calcArea .budgetbar .v small{font-size:var(--fz-mid)}
#calcArea .bignum .t{font-size:var(--fz-mid)}
#calcArea .bignum .s{font-size:var(--fz-small)}
#calcArea .ratio-strip .l{font-size:var(--fz-lbl)}
#calcArea .ratio-strip .r small{font-size:var(--fz-mid)}
#calcArea .details .drow{font-size:var(--fz-td2);padding:12px 18px}
#calcArea .drow .k{font-size:var(--fz-mid)}
#calcArea .mode small{font-size:var(--fz-micro)}
#calcArea .plans-item .meta{font-size:var(--fz-small)}
#calcArea .plans-empty{font-size:var(--fz-lbl)}
#calcArea .quote .qname{font-size:var(--fz-mid)}
#calcArea .quote .qchg{font-size:var(--fz-lbl)}
#calcArea .ladder-hd .ladder-sub{font-size:var(--fz-small)}
#calcArea .ladder-hd .ladder-note{font-size:var(--fz-micro)}
#calcArea .rung .rt{font-size:var(--fz-micro)}
#calcArea .rung .rt b{font-size:var(--fz-lbl)}
#calcArea .rung .rp{font-size:var(--fz-micro)}
#calcArea .tip{font-size:13.5px}
/* 月度明细限高滚动(记录多时默认只显示最近 5 条, 其余可滚动) */
.tbl-scroll{max-height:420px;overflow-y:auto;overflow-x:auto}
.tbl-scroll tr.hidden{display:none}
/* 图表放大按钮 */
.chartbox .ct{display:flex;align-items:center;gap:8px}
.zoombtn{margin-left:auto;font-size:11.5px}
/* 中国习惯: 盈利=红, 亏损=绿 (与开仓模块一致) */
.tbl .pos{color:var(--bad)}
.tbl .neg{color:var(--good)}
/* 详情面板顶部 meta 里的 <b class="pos/neg"> 不在 .tbl 内, 单独覆盖 */
#tradesArea .pos{color:var(--bad);font-weight:600}
#tradesArea .neg{color:var(--good);font-weight:600}
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

.modalbg{position:fixed;inset:0;background:var(--modal-mask);z-index:999;
  display:flex;align-items:center;justify-content:center;animation:pop .18s ease}
.modal{background:var(--panel);border:1px solid var(--border);border-radius:18px;
  padding:22px 24px;width:min(480px,90vw);box-shadow:var(--shadow);animation:pop .25s cubic-bezier(.16,1,.3,1)}
.modal h3{margin-bottom:16px;font-size:16px;display:flex;align-items:center;gap:8px}
.modal .row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.modal .modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.selpill{padding:6px 14px;border-radius:20px;background:var(--panel2);border:1px solid var(--border);
  font-size:12.5px;color:var(--sub);cursor:pointer;transition:.2s}
.selpill.active{background:linear-gradient(135deg,#3ecf8f,#28b470);color:#fff;border:0}

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
<!-- 数据安全横幅: 数据落在软件目录内时由 checkDataSafety() 显示(非阻塞, 不打断使用) -->
<div id="dataRiskBanner" class="hidden" style="position:fixed;top:0;left:0;right:0;z-index:9999;background:#8c2f2f;color:#fff;padding:9px 14px;font-size:13px;line-height:1.6;display:flex;gap:12px;align-items:center;box-shadow:0 2px 10px rgba(0,0,0,.35)">
  <span style="flex:1">
    <b>⚠ 数据存在软件自己的文件夹里</b>（<span id="dataRiskPath" style="opacity:.85;word-break:break-all"></span>）
    —— 以后更新软件（替换/清理该文件夹）会把记录一起删掉，建议立刻迁到软件目录之外。
  </span>
  <button class="btn xs" id="dataRiskMigrate" style="flex:none">一键迁出</button>
  <button class="btn xs" id="dataRiskLater" style="flex:none">稍后</button>
  <button class="btn xs" id="dataRiskClose" style="flex:none">✕</button>
</div>
<div class="app-shell">
  <aside class="side" id="mainTabs">
    <div class="maintab active" data-tab="calc"><span class="mi"><svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="currentColor"/><rect x="26" y="10" width="48" height="56" rx="10" fill="none" stroke="currentColor" stroke-width="8"/><rect x="35" y="18" width="30" height="11" rx="3" fill="currentColor"/><rect x="34" y="34" width="13" height="13" rx="1.5" fill="currentColor"/><rect x="53" y="34" width="13" height="13" rx="1.5" fill="currentColor"/><rect x="34" y="49" width="13" height="13" rx="1.5" fill="currentColor"/><rect x="53" y="49" width="13" height="13" rx="1.5" fill="currentColor"/></svg></span><span class="mt">开仓计算</span><small>期货 · 期权</small></div>
    <div class="maintab" data-tab="trades"><span class="mi"><svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="currentColor"/><rect x="26" y="36" width="48" height="11" rx="3" fill="currentColor"/><rect x="26" y="53" width="30" height="11" rx="3" fill="currentColor" opacity=".55"/></svg></span><span class="mt">交易记录</span><small>abe 期权</small></div>
    <div class="maintab" data-tab="funds"><span class="mi"><svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="currentColor"/><path d="M25 62 L42 48 L57 57 L74 31" fill="none" stroke="currentColor" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="30" r="7" fill="currentColor"/></svg></span><span class="mt">资金曲线</span><small>abe · 威科夫</small></div>
    <div class="side-extras">
      <button class="side-btn" id="btnExport" title="导出全部数据(资金曲线 + 期权交易记录 + 监控池)">⬆</button>
      <button class="side-btn" id="btnImport" title="导入备份(合并资金曲线 + 期权交易记录 + 监控池)">⬇</button>
      <button class="side-btn" id="btnDataDir" style="position:relative" title="把数据存到网盘同步文件夹，换电脑不丢记录">⚙<span id="dataRiskDot" class="hidden" style="position:absolute;top:2px;right:2px;width:8px;height:8px;border-radius:50%;background:#e5484d;box-shadow:0 0 0 2px var(--panel)"></span></button>
      <button class="side-btn" id="btnContact" title="联系作者 / 赞赏">💬</button>
    </div>
  </aside>
  <div class="main">
  <div class="wrap">

  <header>
    <div class="brand">
      <div class="logo"><span id="logoIco"><svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="#E8EDF2"/><rect x="26" y="10" width="48" height="56" rx="10" fill="none" stroke="#7FA8CC" stroke-width="8"/><rect x="35" y="18" width="30" height="11" rx="3" fill="#E8B255"/><rect x="34" y="34" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="53" y="34" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="34" y="49" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="53" y="49" width="13" height="13" rx="1.5" fill="#7FA8CC"/></svg></span></div>
      <div>
        <h1 id="appTitle">期货开仓计算器</h1>
        <p id="appSubtitle">风控仓位计算 · 盈亏比决策 · 保证金测算</p>
      </div>
    </div>
    <div class="topbtns">
      <div class="fzseg" id="fontSeg" title="界面字号">
        <button data-fz="sm">A-</button>
        <button data-fz="md" class="active">A</button>
        <button data-fz="lg">A+</button>
      </div>
      <button class="iconbtn" id="refreshBtn" title="刷新页面(网络中断/数据异常时点此恢复)">🔄</button>
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
        <button class="btn danger sm" id="btnClearAll" title="一键清除资金曲线记录(不影响交易记录)">🗑 清除全部</button>
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
          <span class="lg"><i style="background:#a58ae0"></i>本月末权益（扣掉出入金之后）</span>
          <span class="lg"><i style="background:#e78fb5"></i>本月盈亏</span>
          <span class="lg"><i style="background:#e2c985"></i>本月收益率（折线，右轴）</span>
        </div>
        <canvas id="chartMonthly"></canvas>
      </div>
      <div class="chartbox">
        <div class="ct"><span class="dot"></span><span id="chartYearlyTitle">abe · 年盈亏分析</span>
          <button class="btn sm zoombtn" data-zoom="yearly" title="放大查看">⤢ 放大</button></div>
        <div class="legend">
          <span class="lg"><i style="background:#a58ae0"></i>年末权益</span>
          <span class="lg"><i style="background:#e78fb5"></i>年度总盈亏</span>
          <span class="lg"><i style="background:#e2c985"></i>年化收益率（折线，右轴）</span>
        </div>
        <canvas id="chartYearly"></canvas>
      </div>
    </div>
  </div><!-- /fundsArea -->

  <!-- ============================ 期权交易记录 ============================ -->
  <div id="tradesArea" class="hidden">
    <div class="wrap">
      <header class="trades-header">
        <div class="trades-toolbar">
          <label class="chk"><input type="checkbox" id="tradesOnlyOpen"> 只展示未平仓</label>
          <button class="btn xs ghost" id="btnShowAll" hidden>📜 显示全部</button>
          <span class="spacer"></span>
          <button class="btn xs rose" id="btnNewOpen">➕ 新建开仓</button>
        </div>
      </header>
      <div class="trades-layout">
        <div class="trades-main">
          <div class="card results">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
              <h2 style="margin:0;flex:1;min-width:0"><span class="dot"></span>期权交易(按开仓时间倒序, 最近在最上, 最多 10 条)</h2>
              <span id="tradesSearchHint" class="dim" style="font-size:12px;flex:none"></span>
              <input id="tradesSearch" type="text" autocomplete="off" spellcheck="false"
                     placeholder="搜索 标的 / 合约 / 状态 / 日期" style="width:240px;flex:none">
              <button class="btn xs ghost" id="tradesSearchClear" hidden style="flex:none">清除</button>
            </div>
            <div class="tblwrap">
              <table class="tbl trades-tbl" id="tradesTable">
                  <thead><tr>
                    <th>开仓标的</th><th>方向</th><th>开仓时间</th>
                    <th>是否平仓</th><th>平仓盈亏</th><th>平仓时间</th>
                    <th>操作</th>
                  </tr></thead>
                  <tbody></tbody>
                </table>
            </div>
          </div>

          <div class="card results" style="margin-top:14px">
            <h2><span class="dot"></span>abe 期权监控池</h2>
            <div id="poolArea"><div class="tip">暂无监控池快照，点下面按钮新建</div></div>
            <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn xs" id="btnNewPool">➕ 新建监控池快照</button>
              <button class="btn xs ghost" id="btnPoolHistory" hidden>📜 查看历史快照</button>
            </div>
          </div>
        </div>

        <aside class="trades-side hidden" id="tradeDetailPanel">
          <div class="card results">
            <div class="td-head">
              <h2><span class="dot"></span><span id="tdTitle">—</span></h2>
              <div class="td-head-actions">
                <button class="btn xs rose" id="tdNewOpen">➕ 新建开仓</button>
                <button class="btn xs cyan" id="tdNewClose">➖ 新建平仓</button>
                <button class="btn xs ghost" id="tdClose">✕ 关闭</button>
              </div>
            </div>
            <div class="tip" id="tdMeta" style="margin:6px 0 10px"></div>
            <h3 style="font-size:13px;margin:6px 0 8px;color:var(--accent2)">当前持仓(按合约汇总, 仅算未平仓部分)</h3>
            <div class="tblwrap">
              <table class="tbl trades-tbl">
                <thead><tr>
                  <th>合约代码</th><th>看涨看跌</th><th>方向</th>
                  <th><span class="help-tip" data-tip="仅算未平仓部分(扣减已平仓后剩余的开仓手数)的加权均价, 不会受已平仓的开仓成本影响">开仓均价 ?</span></th><th>数量</th><th>权利金</th>
                </tr></thead>
                <tbody id="tdHoldings"></tbody>
              </table>
            </div>
                        <div class="op-filter-row">
              <h3 style="font-size:13px;margin:6px 0 8px;color:var(--accent2)">操作记录(按时间升序, 最近在最下方)</h3>
              <label class="op-filter">筛选合约
                <select id="tdContractFilter">
                  <option value="">全部合约</option>
                </select>
              </label>
            </div>
            <div class="tblwrap">
              <table class="tbl trades-tbl">
                <thead><tr>
                  <th>合约</th><th>日期</th><th>操作</th>
                  <th>delta</th><th>目标</th><th>看涨看跌</th>
                  <th>方向</th><th>数量</th><th>价格</th><th>权利金</th>
                  <th>平仓盈亏</th><th>状态</th><th>备注</th><th>操作</th>
                </tr></thead>
                <tbody id="tdOps"></tbody>
              </table>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div><!-- /tradesArea -->

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
      <!-- ⚠ 数据落在软件目录内时的醒目警告: 更新软件会连数据一起删掉 -->
      <div id="setRisk" class="hidden" style="background:#8c2f2f;color:#fff;border-radius:8px;padding:10px 12px;font-size:12.5px;line-height:1.75;margin-bottom:12px">
        <b>⚠ 危险：数据存在软件自己的文件夹里</b><br>
        当前路径属于软件目录（<span id="setRiskApp" style="opacity:.85"></span>）。<br>
        <b>以后更新软件（替换/清理这个文件夹）会把记录一起删掉。</b><br>
        请点下面的「一键迁出」，把数据挪到软件目录之外（比如 D:\Workbuddy 下另建一个数据文件夹）。
      </div>
      <div style="color:var(--sub);font-size:12.5px;line-height:1.8;margin-bottom:10px">
        当前数据目录：<b id="setCurDir" style="color:var(--text);word-break:break-all">—</b><br>
        当前记录：<b id="setCurCnt" style="color:var(--accent)">—</b> 条
      </div>
      <div style="color:var(--sub);font-size:12.5px;line-height:1.8;margin-bottom:12px;border-top:1px dashed var(--line);padding-top:10px">
        💾 <b>自动备份</b>：每次交易记录 / 资金曲线发生变动都会自动存一份快照，<b>最多保留最近 10 份</b>。<br>
        备份位置：<b id="setBkDir" style="color:var(--text);word-break:break-all">—</b><br>
        已有备份：<b id="setBkCnt" style="color:var(--accent)">—</b> 份
        <span id="setBkLast" style="opacity:.75"></span>
        <button class="btn xs" id="setBkNow" style="margin-left:6px">立即备份</button>
        <div class="tip" style="margin-top:6px">点任意一份右侧的「恢复」即可把数据退回到那一刻（恢复前会自动把当前状态再存一份，可以再退回来）。</div>
        <div id="setBkList" style="margin-top:8px;max-height:190px;overflow:auto;border:1px solid var(--line);border-radius:8px"></div>
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
        <button class="btn" id="setMigrateSafe">一键迁出软件目录</button>
        <button class="btn primary" id="setSave">保存并迁移</button>
      </div>
    </div>
  </div>

  <!-- 文件夹浏览器 弹窗 (替代原来会静默失败的 Windows 原生对话框) -->
  <div class="modalbg hidden" id="pickBg">
    <div class="modal" style="width:min(680px,94vw)">
      <h3><span class="dot"></span>选择文件夹</h3>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <button class="btn xs" id="pickUp" style="flex:none">↑ 上一级</button>
        <input id="pickPath" type="text" autocomplete="off" spellcheck="false" style="flex:1"
               placeholder="可直接输入或粘贴路径后回车">
      </div>
      <div id="pickDrives" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px"></div>
      <div id="pickList" style="height:260px;overflow:auto;border:1px solid var(--line);border-radius:8px"></div>
      <div class="tip" style="margin-top:8px">双击文件夹进入；选中后点「选择此文件夹」。</div>
      <div class="modal-actions">
        <button class="btn" id="pickCancel">取消</button>
        <button class="btn primary" id="pickOk">选择此文件夹</button>
      </div>
    </div>
  </div>

  <!-- 期权交易记录 录入/修改 弹窗 -->
  <div class="modalbg hidden" id="tradeModalBg">
    <div class="modal" style="max-width:640px">
      <h3><span class="dot"></span><span id="tmTitle">新建开仓</span></h3>
      <div class="formgrid">
        <label><span class="req">开仓标的 <i>*</i></span>
          <input id="tmUnderlying" type="text" placeholder="如 ao611">
        </label>
        <label id="tmContractWrap"><span class="req">合约代码 <i>*</i></span>
          <input id="tmContract" type="text" placeholder="如 ao611P2500">
        </label>
        <label id="tmOpTypeWrap" style="display:none">
          <span style="font-size:11px;color:var(--sub)">操作类型</span>
          <select id="tmOpType">
            <option value="open">开仓</option>
            <option value="close">平仓</option>
          </select>
        </label>

        <label><span class="req">开仓日期 <i>*</i></span>
          <input id="tmOpenDate" type="date">
        </label>
        <label>平仓日期
          <input id="tmCloseDate" type="date">
        </label>

        <label>看涨/看跌
          <select id="tmCallPut">
            <option value="">—</option>
            <option value="C">看涨</option>
            <option value="P">看跌</option>
          </select>
        </label>
        <label id="tmDirectionWrap"><span class="req">方向 <i>*</i></span>
          <select id="tmDirection">
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
        </label>

        <label>开仓 delta
          <input id="tmOpenDelta" type="number" step="0.01" min="0" max="1" placeholder="0.19">
        </label>
        <label>目标 delta
          <input id="tmTargetDelta" type="number" step="0.01" min="0" max="1" placeholder="0.45">
        </label>

        <label>开仓价
          <input id="tmOpenPrice" type="number" step="0.0001" min="0" placeholder="700">
        </label>
        <label id="tmClosePriceWrap">平仓价
          <input id="tmClosePrice" type="number" step="0.0001" min="0" placeholder="510">
        </label>

        <label><span class="req">数量 <i>*</i></span>
          <input id="tmQty" type="number" step="1" min="1" placeholder="4">
        </label>
        <label id="tmCloseQtyWrap">平仓数量
          <input id="tmCloseQty" type="number" step="1" min="1" placeholder="4">
          <span id="tmCloseQtyHint" style="font-size:10.5px;color:var(--sub);margin-top:2px"></span>
        </label>

        <label>权利金(元)
          <input id="tmPremium" type="number" step="0.01" min="0" placeholder="1400">
        </label>
        <label id="tmPnlWrap">平仓盈亏
          <input id="tmPnl" type="number" step="0.01" placeholder="-760">
        </label>

        <label class="full">备注
          <input id="tmNote" type="text" placeholder="可选">
        </label>
      </div>
      <div id="tmError" style="color:#ff8484;font-size:12px;min-height:18px;margin-top:8px"></div>
      <div class="modal-actions">
        <button class="btn" id="tmCancel">取消</button>
        <button class="btn primary" id="tmSave">保存</button>
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
const lastPicked = {F: null, O: null}; // 上次真正选中的品种(用于"切换品种→自动清空价格"判断)
let dirF = 'long';                     // 期货持仓方向(做多红/做空青 双按钮)

/* 清空价格输入: 期货=开仓/止损/止盈价, 期权=开仓价(每手权利金); 供手动按钮与切换品种共用 */
function clearPrices(which){
  if (which === 'F'){
    ['entry','stop','target'].forEach(id=>{ $(id).value=''; });
  } else if (which === 'O'){
    $('entryO').value='';
  }
  updateTickHint();
  onInput();
}

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
  function onRiskChange(mode){
    updateRiskHint(mode);
    onInput();   // 立即重新测算
  }
  // 权益输入: 同步记到当前模式(切模式时各自恢复), 并刷新风险提示
  const _onEqInput = ()=>{
    if (curMode) equityByMode[curMode] = $('equity').value;
    const hint = $('defEquityHint');
    if (hint && $('equity').value) hint.classList.add('hidden');   // 用户手动改过 → 隐藏"已自动填入"
    updateRiskHint('futures'); updateRiskHint('options');
  };
  $('equity').addEventListener('input', _onEqInput);
  $('equity').addEventListener('change', _onEqInput);
  $('riskAmount').addEventListener('change', ()=>onRiskChange('futures'));
  $('riskAmountO').addEventListener('change', ()=>onRiskChange('options'));
  // 存为默认: 权益 / 风险百分比(期货+期权)
  $('btnDefEquity').addEventListener('click', saveDefaultEquity);
  $('btnDefRisk').addEventListener('click', ()=>saveDefaultRisk('futures'));
  $('btnDefRiskO').addEventListener('click', ()=>saveDefaultRisk('options'));
  // 一键清空开仓/止损/止盈价(与"切换品种自动清空"共用同一逻辑)
  $('btnClearPrices').addEventListener('click', ()=>clearPrices('F'));
  updateRiskHint('futures');
  updateRiskHint('options');
}

/* ---- 默认值设置: 权益 / 期货风险百分比 (持久化到 config.json) ---- */
/* 风险额度预算提示(期货 + 期权共用, 内部按 mode 取对应 select 的值) — 全局函数: setMode/applyEquityForMode 也会调 */
function updateRiskHint(mode){
  const eqEl = $('equity');
  if (!eqEl) return;
  const eq = (parseFloat(eqEl.value) || 0) * 10000;   // 万元 → 元
  const defPct = mode === 'options' ? 3 : 1;
  const selId = mode === 'options' ? 'riskAmountO' : 'riskAmount';
  const hintId = mode === 'options' ? 'riskHintO' : 'riskHint';
  const sel = $(selId), hint = $(hintId);
  if (!sel || !hint) return;
  const pct = parseFloat(sel.value);
  if (isNaN(eq) || eq <= 0) { hint.innerHTML = ''; return; }
  const p = isNaN(pct) || pct <= 0 ? defPct : pct;
  const budget = eq * p / 100;
  hint.innerHTML = '预算 = 权益 × ' + p + '% = <b style="color:var(--accent)">¥' + budget.toLocaleString('en-US', {maximumFractionDigits:2}) + '</b>';
}
let settingsCache = {futures_default_equity:null, options_default_equity:null, futures_risk_pct:null, options_risk_pct:null, frequent_futures:[], frequent_options:[]};
/* 期货/期权各自的当前权益输入值(万元, null=未填过) — 两模式互不影响 */
let equityByMode = {futures:null, options:null};
/* 取某模式的默认权益(元) */
function defaultEquityOf(mode){
  return mode === 'options' ? settingsCache.options_default_equity : settingsCache.futures_default_equity;
}
/* 把当前 equity 输入框的值切到指定模式: 有记忆值用记忆值, 否则用该模式默认值, 都没有则清空 */
function applyEquityForMode(mode){
  const eq = $('equity');
  if (!eq) return;
  const remembered = equityByMode[mode];
  const def = defaultEquityOf(mode);
  if (remembered != null && remembered !== '') eq.value = remembered;
  else if (def != null) eq.value = def / 10000;   // 元 → 万元
  else eq.value = '';
  bindWanEquity();
  const hint = $('defEquityHint');
  if (hint){
    const isDefault = (remembered == null || remembered === '') && def != null;
    if (isDefault){
      hint.classList.remove('hidden');
      hint.innerHTML = '✓ 已自动填入' + (mode==='options'?'期权':'期货') + '默认权益：<b>'
        + (def/10000).toLocaleString('en-US',{maximumFractionDigits:2})
        + ' 万元</b>（可点「存为默认」更换）';
    } else {
      hint.classList.add('hidden');
    }
  }
  updateRiskHint('futures');
  updateRiskHint('options');
}
function loadSettings(){
  fetchT('/api/settings').then(r=>r.json()).then(d=>{
    if(!d.ok) return;
    const s = d.settings || {};
    settingsCache = s;
    // 按当前模式填入该模式自己的默认权益(期货/期权独立)
    applyEquityForMode(curMode);
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
    // 刷新提示(风险额度提示由 applyEquityForMode 内 updateRiskHint 处理; 不派发 change 以免误清"已自动填入"提示)
    // 渲染常用区
    if (typeof initContractSearch._renderFreqAll === 'function') initContractSearch._renderFreqAll();
    onInput();
  });
}
function saveDefaultEquity(){
  const eqWan = parseFloat($('equity').value);
  if (isNaN(eqWan) || eqWan <= 0) { alert('请先填写有效的权益金额'); return; }
  const eqYuan = eqWan * 10000;   // 万元 → 元(存储)
  const key = curMode === 'options' ? 'options_default_equity' : 'futures_default_equity';
  const label = curMode === 'options' ? '期权' : '期货';
  fetchT('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({[key]: eqYuan})}).then(r=>r.json()).then(d=>{
    if(d.ok){
      settingsCache[key] = eqYuan;
      equityByMode[curMode] = null;   // 值已等于默认值 → 不算手动记忆, 让它显示"已自动填入"提示
      applyEquityForMode(curMode);
      alert('✅ 已把 ' + label + '默认权益保存为：' + eqWan.toLocaleString('en-US',{maximumFractionDigits:2}) + ' 万元（下次打开' + label + '模式自动填入，不影响' + (curMode==='options'?'期货':'期权') + '模式）');
    }
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
  if (!el.dataset.wanBound){   // 只绑一次(切模式会多次调用)
    el.addEventListener('input', update);
    el.addEventListener('change', update);
    el.dataset.wanBound = '1';
  }
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
    // 切换品种(与上次选中不同) → 自动清空开仓/止损/止盈价, 免手动点清空
    // 注意: 用 lastPicked 而非 selCode 判断 — 搜索输入会把 selCode 置 null, 同品种重选不应清空
    const isSwitch = !!lastPicked[mKey] && String(lastPicked[mKey]).toLowerCase() !== String(c.code).toLowerCase();
    lastPicked[mKey] = c.code;
    input.value = c.name + ' ' + c.code + ' · ' + c.exchange;
    selCode[mKey] = c.code;
    list.classList.add('hidden');
    input.blur();
    if (isSwitch) clearPrices(mKey);   // 先清空再加载新品种
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
  selCode[which] = c.code;   // 统一标记选中(所有调用路径生效; 搜索点击路径此前已赋值, 幂等)
  lastPicked[which] = c.code;   // 同步记录(方案调出路径也在此, 使后续"切换品种"判断一致)
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
  // 切走前: 记住当前模式的权益输入(两模式权益互相独立)
  if (curMode && $('equity') && curMode !== m) equityByMode[curMode] = $('equity').value;
  document.querySelectorAll('.mode').forEach(x=>x.classList.toggle('active', x.dataset.mode===m));
  curMode = m;
  $('futuresFields').classList.toggle('hidden', curMode!=='futures');
  $('optionsFields').classList.toggle('hidden', curMode!=='options');
  if (typeof renderPlans === 'function') renderPlans();   // 最近方案区仅期货显示
  if (typeof applyEquityForMode === 'function') applyEquityForMode(curMode);   // 切到该模式自己的权益
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

/* 刷新页面: 网络中断/数据异常时一键 reload(数据存本地, 不会丢失) */
$('refreshBtn').addEventListener('click',()=>location.reload());

/* 字号档位: 小(sm) / 中(md) / 大(lg) — body class 覆盖 CSS 变量组 */
const FONT_LEVELS = ['sm','md','lg'];
let fontLevel = localStorage.getItem('oc-font') || 'md';
function applyFontLevel(lv, skipChart){
  if (!FONT_LEVELS.includes(lv)) lv = 'md';
  fontLevel = lv;
  document.body.classList.remove('fz-sm','fz-md','fz-lg');
  document.body.classList.add('fz-' + lv);
  localStorage.setItem('oc-font', lv);
  document.querySelectorAll('#fontSeg button').forEach(b=>{
    b.classList.toggle('active', b.dataset.fz === lv);
  });
  // 图表字号实时读 CSS 变量 → 已开图表需重建(含放大图); 页面初始化时 FundUI 尚未定义, 跳过
  if (!skipChart){
    try{
      if (FundUI && (FundUI.chartMonthly || FundUI.chartYearly)) FundUI.renderCharts();
      if (FundUI && FundUI.chartZoom) FundUI.closeChartZoom();
    }catch(e){}
  }
}
document.querySelectorAll('#fontSeg button').forEach(b=>{
  b.addEventListener('click', ()=>applyFontLevel(b.dataset.fz));
});
applyFontLevel(fontLevel, true);   // 初始: 只需设 class, 图表由 FundUI.init 后续按变量渲染

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
   期权交易记录模块（命名空间 TradeUI）
   ================================================================= */
// 网络/连接错误友好提示(TypeError: Failed to fetch 多为服务被关闭/接管)
function fmtNetError(e){
  if (e && (e.name === 'TypeError' || (e.message || '').includes('fetch') || (e.message || '').includes('NetworkError'))) {
    return '网络中断或服务已退出，请刷新页面后重试';
  }
  return (e && e.message) ? e.message : String(e || '未知错误');
}
const TradeUI = {
  groups: [],           // 主表行(按 underlying 聚合)
  detail: null,         // 当前详情(underlying)
  pool: [],             // 监控池快照历史
  poolCollapsed: {},    // {date: true/false} 历史快照折叠状态
  onlyOpen: false,      // 只展示未平仓
  mainQuery: '',        // 主表搜索关键词(空=不过滤)
  showAll: false,       // 是否展开全部(>10)

  // 预处理: 空白是打字随手敲的 → 直接删; _ / 视为有意分段 → 统一成 '-'; 合并连续'-'; 去首尾'-'
  //   'br 2610 C 15800' -> 'br-2610-C-15800' ; 'lc2611_C_144000' -> 'lc2611-C-144000'
  tidySeps(s){
    s = String(s || '').trim().replace(/\s+/g, '');
    s = s.replace(/[_/]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
    return s;
  },

  // 合约代码归一化: 品种代码小写, C/P(看涨/看跌)大写, 分隔符统一
  //   ⚠ 只改「月份/行权价之间」的 C/P — 品种代码自带 C/P 的多(玉米 c / 棉花 cf / 纸浆 sp / 棕榈油 p),
  //     整串盲目大写会把品种代码改坏
  normalizeContract(c){
    const low = this.tidySeps(c).toLowerCase();
    if (!low) return low;
    const i = this.findCpIndex(low);
    if (i < 0) return low.replace(/-/g, '');
    // C/P 两侧原本带 '-' 就保留分段, 否则输出紧凑写法
    const ch = low[i].toUpperCase();
    const hasL = i > 0 && low[i - 1] === '-';
    const hasR = i + 1 < low.length && low[i + 1] === '-';
    const left = low.slice(0, i).replace(/-/g, '');
    const right = low.slice(i + 1).replace(/-/g, '');
    if (!left) return right ? (ch + (hasR ? '-' : '') + right) : ch;
    if (!right) return left + (hasL ? '-' : '') + ch;
    return left + (hasL ? '-' : '') + ch + (hasR ? '-' : '') + right;
  },

  // 定位看涨看跌 C/P 的位置(与后端 find_cp_index 规则一致), 找不到返回 -1
  //   紧凑式 lc2611c144000 / sf611c6800 | 分隔式 lc2611-c-144000 | 半分隔 lc2611-c144000
  //   ⚠ 三重坑:
  //     1) 24 个品种代码自带 C/P(玉米 c / 棉花 cf / 纸浆 sp / 聚丙烯 pp / 苹果 ap / 花生 pk);
  //     2) 品种前缀的 c/p 也可能「看着合法」(hc2610P6000 里 c 与 P 都满足结构) → 取最右侧;
  //     3) 判定依赖月份(3-4 位), 没有月份的写法不认(aoc5000 无法与品种前缀区分)
  findCpIndex(s){
    if (!s) return -1;
    const SEP = '-_/ ';
    const isSep = (v) => v === '' || SEP.indexOf(v) >= 0;
    const isDigit = (v) => v >= '0' && v <= '9';
    const runLeft = (i) => { let j = i - 1; while (j >= 0 && isDigit(s[j])) j--; return s.slice(j + 1, i); };
    const runRight = (i) => { let j = i + 1; while (j < s.length && isDigit(s[j])) j++; return s.slice(i + 1, j); };
    const hasMonth = (b) => /(^|[^0-9])[0-9]{3,4}([^0-9]|$)/.test(b);
    const hasDigit = (b) => /[0-9]/.test(b);
    const isMonthRun = (r) => r.length >= 3 && r.length <= 4;
    let found = -1;
    for (let i = 0; i < s.length; i++){
      const ch = s[i];
      if (ch !== 'c' && ch !== 'p') continue;
      const left = i > 0 ? s[i - 1] : '';
      const right = i + 1 < s.length ? s[i + 1] : '';
      const lb = s.slice(0, i), rb = s.slice(i + 1);
      let ok = false;
      if (isDigit(left) && isDigit(right)){
        // 紧凑式: 一侧是月份(3-4 位 run), 另一侧是行权价数字
        if (isMonthRun(runLeft(i)) || isMonthRun(runRight(i))) ok = true;
      } else if (isSep(left) && isSep(right)){
        // 分隔式: 两侧块里须出现月份与行权价(月份可能在 C/P 左侧或右侧)
        if ((hasMonth(lb) && hasDigit(rb)) || (hasDigit(lb) && hasMonth(rb))) ok = true;
      } else if (isSep(left) && isDigit(right) && hasMonth(lb)){
        ok = true;    // 半分隔式: lc2611-c144000
      } else if (isDigit(left) && isSep(right) && hasMonth(rb)){
        ok = true;    // 半分隔式(镜像)
      }
      if (ok) found = i;   // 继续往后找, 取最右侧
    }
    return found;
  },

  // 从合约代码推断看涨(C)/看跌(P), 识别不出返回 ''
  detectCallPut(c){
    const s = this.tidySeps(c).toLowerCase();
    const i = this.findCpIndex(s);
    return i >= 0 ? s[i].toUpperCase() : '';
  },
  contractFilter: '',   // 详情操作记录合约筛选(空=全部)
  MAX: 10,

  fmtDate(d){
    if (!d) return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) return d;
    return d;
  },

  async init(){
    $('tradesOnlyOpen').addEventListener('change', e => { this.onlyOpen = e.target.checked; this.renderMain(); });
    // 主表搜索框: 输入即筛选(与「只展示未平仓」叠加生效)
    $('tradesSearch').addEventListener('input', e => {
      this.mainQuery = e.target.value;
      $('tradesSearchClear').hidden = !this.mainQuery.trim();
      this.renderMain();
    });
    $('tradesSearchClear').addEventListener('click', () => {
      this.mainQuery = '';
      $('tradesSearch').value = '';
      $('tradesSearchClear').hidden = true;
      this.renderMain();
      $('tradesSearch').focus();
    });
    // 输入框内按 Esc 快速清空
    $('tradesSearch').addEventListener('keydown', e => {
      if (e.key === 'Escape') $('tradesSearchClear').click();
    });
    $('btnShowAll').addEventListener('click', () => { this.showAll = true; this.renderMain(); });
    $('btnNewOpen').addEventListener('click', () => this.openEditModal('open'));
    $('btnNewPool').addEventListener('click', () => this.openPoolModal());
    $('btnPoolHistory').addEventListener('click', () => this.togglePoolHistory());
    $('tdClose').addEventListener('click', () => this.closeDetail());
    $('tdNewOpen').addEventListener('click', () => this.openEditModal('open', { underlying: this.detail ? this.detail.underlying : '' }));
    // 操作记录合约筛选
    $('tdContractFilter').addEventListener('change', e => {
      this.contractFilter = e.target.value;
      this.renderOps();
    });
    $('tdNewClose').addEventListener('click', () => {
      if (!this.detail){ alert('请先在主表点开一个标的的详情'); return; }
      if (!this.detail.holdings || !this.detail.holdings.length){ alert('当前持仓为空, 无可平仓的合约'); return; }
      this.openEditModal('close', { underlying: this.detail.underlying });
    });
    // modal 关闭/保存
    $('tmCancel').addEventListener('click', () => $('tradeModalBg').classList.add('hidden'));
    $('tradeModalBg').addEventListener('click', e => { if (e.target === $('tradeModalBg')) $('tradeModalBg').classList.add('hidden'); });
    $('tmSave').addEventListener('click', () => this.submitModal());
    // 回车保存: 在 tradeModalBg 内任意 input/select 按 Enter 直接保存(shift+enter/textarea 不触发)
    $('tradeModalBg').addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      if ($('tradeModalBg').classList.contains('hidden')) return;
      if (e.target.tagName === 'TEXTAREA') return;
      e.preventDefault();
      $('tmSave').click();
    });
    // 侧边栏全局按钮: 委托给 FundUI(导出已含交易记录)
    const se = $('btnExport'); const si = $('btnImport');
    if (se) se.addEventListener('click', () => FundUI.exportBackup());
    if (si) si.addEventListener('click', () => $('importFile').click());
    const contact = $('btnContact');
    if (contact) contact.addEventListener('click', () => {
      const bg = $('contactBg'); if (bg) bg.classList.remove('hidden');
    });
    await this.refresh();
  },

  async refresh(){
    try {
      const [gr, pl] = await Promise.all([
        fetchT('/api/trades/groups').then(r => r.json()),
        fetchT('/api/trades/pool').then(r => r.json()),
      ]);
      this.groups = gr.ok ? gr.groups : [];
      this.pool = pl.ok ? pl.snapshots : [];
    } catch (e) { console.error('TradeUI refresh', e); this.groups = []; this.pool = []; }
    this.renderMain();
    this.renderPool();
    if (this.detail) {
      const u = this.detail.underlying;
      this.loadDetail(u);
    }
  },

  renderMain(){
    const tb = document.querySelector('#tradesTable tbody');
    let rows = this.groups.slice();
    if (this.onlyOpen) rows = rows.filter(g => g.close_status !== '已平仓');
    const q = (this.mainQuery || '').trim();
    if (q) rows = rows.filter(g => this.matchGroup(g, q));
    const total = rows.length;
    // 搜索命中数提示
    const hint = $('tradesSearchHint');
    if (hint) hint.textContent = q ? ('筛选出 ' + total + ' 条') : '';
    const limit = this.showAll ? total : this.MAX;
    const show = rows.slice(0, limit);
    $('btnShowAll').hidden = !this.showAll && total > this.MAX;
    if (!show.length){
      const msg = q ? ('没有匹配「' + q + '」的记录') : '暂无记录，点上面「新建开仓」添加';
      tb.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--sub)">'
        + escHtml(msg) + '</td></tr>';
      return;
    }
    tb.innerHTML = show.map(g => {
      const dirTxt = g.direction === 'buy' ? '买入' : (g.direction === 'sell' ? '卖出' : '—');
      const dirTag = g.direction === 'buy' ? 'buy' : (g.direction === 'sell' ? 'sell' : '');
      const statusTag = g.close_status === '已平仓' ? 'closed' : (g.close_status === '部分平仓' ? 'partial' : 'unclosed');
      const pnl = g.total_pnl;
      const pnlCls = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '');
      return `<tr class="clickable" data-u="${escHtml(g.underlying)}">
        <td><b>${escHtml(g.underlying)}</b></td>
        <td><span class="tag ${dirTag}">${dirTxt}</span></td>
        <td>${this.fmtDate(g.open_date)}</td>
        <td><span class="tag ${statusTag}">${escHtml(g.close_status)}</span></td>
        <td class="${pnlCls}" style="font-weight:600">${pnl ? (pnl > 0 ? '+' : '') + 'CN¥' + pnl.toLocaleString('en-US',{maximumFractionDigits:2}) : '—'}</td>
        <td>${this.fmtDate(g.last_close_date)}</td>
        <td class="row-actions">
          <button class="iconbtn" data-act="edit" data-u="${escHtml(g.underlying)}" title="修改开仓内容">✎</button>
          <button class="iconbtn" data-act="del" data-u="${escHtml(g.underlying)}" title="删除该标的全部记录">🗑</button>
        </td>
      </tr>`;
    }).join('');
    // 行点击打开详情(操作列按钮不触发)
    tb.querySelectorAll('tr.clickable').forEach(tr => {
      tr.addEventListener('click', e => {
        const btn = e.target.closest('[data-act]');
        if (btn) {
          e.stopPropagation();
          const u = btn.dataset.u;
          if (btn.dataset.act === 'edit') this.editUnderlying(u);
          else if (btn.dataset.act === 'del') this.deleteUnderlying(u);
          return;
        }
        this.loadDetail(tr.dataset.u);
      });
    });
  },

  // 主表搜索: 命中 标的/合约/方向/状态/日期/盈亏; 空格分隔多个关键词时需全部命中
  matchGroup(g, q){
    const dirTxt = g.direction === 'buy' ? '买入' : (g.direction === 'sell' ? '卖出' : '');
    const pnl = g.total_pnl;
    const hay = [
      g.underlying || '',
      (g.contracts || []).join(' '),
      dirTxt, g.direction || '',
      g.close_status || '',
      g.open_date || '', g.last_close_date || '',
      pnl == null ? '' : String(pnl),
      pnl ? ('CN¥' + pnl) : '',
    ].join(' ').toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every(t => hay.indexOf(t) >= 0);
  },

  async loadDetail(underlying){
    try {
      const r = await fetchT('/api/trades/detail?underlying=' + encodeURIComponent(underlying));
      const d = await r.json();
      if (!d.ok) { alert('加载详情失败: ' + d.error); return; }
      this.detail = d;
      this.renderDetail();
    } catch (e) { alert('加载详情失败: ' + e); }
  },

  renderDetail(){
    const d = this.detail;
    if (!d) return;
    document.querySelector('.trades-layout').classList.add('has-detail');
    $('tradeDetailPanel').classList.remove('hidden');
    $('tdTitle').textContent = d.underlying + ' 详情';
    // 顶部 meta: 方向/开仓时间/是否平仓/平仓盈亏/平仓时间
    const g = this.groups.find(x => x.underlying === d.underlying) || {};
    const dirTxt = g.direction === 'buy' ? '买入' : (g.direction === 'sell' ? '卖出' : '—');
    const pnl = g.total_pnl;
    const pnlStr = pnl ? (pnl > 0 ? '+CN¥' : (pnl < 0 ? '-CN¥' : 'CN¥')) + Math.abs(pnl).toLocaleString('en-US',{maximumFractionDigits:2}) : '—';
    $('tdMeta').innerHTML = `
      <span class="tag ${g.direction==='buy'?'buy':(g.direction==='sell'?'sell':'')}">${dirTxt}</span>
      &nbsp;开仓时间 <b>${this.fmtDate(g.open_date)}</b>
      &nbsp;状态 <span class="tag ${g.close_status==='已平仓'?'closed':(g.close_status==='部分平仓'?'partial':'unclosed')}">${escHtml(g.close_status||'—')}</span>
      &nbsp;平仓盈亏 <b class="${pnl>0?'pos':(pnl<0?'neg':'')}">${pnlStr}</b>
      &nbsp;平仓时间 <b>${this.fmtDate(g.last_close_date)}</b>`;

    // 持仓汇总
    const hb = $('tdHoldings');
    if (!d.holdings.length){
      hb.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:14px;color:var(--sub)">当前无持仓(全部已平仓)</td></tr>';
    } else {
      hb.innerHTML = d.holdings.map(h => `<tr>
        <td><b>${escHtml(h.contract)}</b></td>
        <td><span class="tag ${h.call_put==='P'?'short':(h.call_put==='C'?'long':'')}">${h.call_put==='P'?'看跌':(h.call_put==='C'?'看涨':'—')}</span></td>
        <td><span class="tag ${h.direction==='buy'?'buy':'sell'}">${h.direction==='buy'?'买入':'卖出'}</span></td>
        <td>${h.open_price.toLocaleString('en-US',{maximumFractionDigits:4})}</td>
        <td>${h.qty}</td>
        <td>${h.premium.toLocaleString('en-US',{maximumFractionDigits:2})}</td>
      </tr>`).join('');
    }
    // 合约筛选下拉: 选项 = 该标的下全部合约(按归一化后去重, 避免 br2610C15800 / br2610c15800 出现两行)
    const _seen = {};
    const contracts = [];
    d.operations.forEach(o => {
      const n = this.normalizeContract(o.contract);
      const k = n.toUpperCase();
      if (!_seen[k]){ _seen[k] = 1; contracts.push(n); }
    });
    const selF = $('tdContractFilter');
    const prevSel = selF.value;
    selF.innerHTML = '<option value="">全部合约</option>' + contracts.map(c =>
      `<option value="${escHtml(c)}">${escHtml(c)}</option>`).join('');
    if (contracts.indexOf(prevSel) >= 0) selF.value = prevSel;
    this.contractFilter = selF.value;
    this.renderOps();
  },

  renderOps(){
    const d = this.detail;
    if (!d) return;
    const ob = $('tdOps');
    const _f = this.contractFilter ? this.contractFilter.toUpperCase() : '';
    const ops = _f
      ? d.operations.filter(o => this.normalizeContract(o.contract).toUpperCase() === _f)
      : d.operations;
    if (!ops.length){
      ob.innerHTML = '<tr><td colspan="14" style="text-align:center;padding:14px;color:var(--sub)">' +
        (d.operations.length ? '该合约无操作记录' : '暂无操作记录') + '</td></tr>';
      return;
    }
    ob.innerHTML = ops.map(o => {
      const isOpen = o.op_type === 'open';
      const dt = isOpen ? o.open_date : o.close_date;
      const price = isOpen ? o.open_price : o.close_price;
      // 计算该合约当前持仓状态(为该行提供 "状态" 字段)
      const st = this._rowStatus(o, d);
      const pnlCls = (o.pnl||0) > 0 ? 'pos' : ((o.pnl||0) < 0 ? 'neg' : '');
      const opTag = isOpen ? 'open' : 'close';
      const opTxt = isOpen ? '开仓' : '平仓';
      // 权利金: 开仓显示金额, 平仓填 "/"(平仓不产生新权利金); 数量: 平仓显示 close_qty
      const premiumTxt = isOpen ? o.premium.toLocaleString('en-US',{maximumFractionDigits:2}) : '/';
      const qtyTxt = isOpen ? o.qty : (o.close_qty != null ? o.close_qty : o.qty);
      return `<tr class="row-${opTag}">
        <td><b>${escHtml(o.contract)}</b></td>
        <td>${this.fmtDate(dt)}</td>
        <td><span class="tag ${opTag}">${opTxt}</span></td>
        <td>${o.open_delta!=null ? o.open_delta : '—'}</td>
        <td>${o.target_delta!=null ? o.target_delta : '—'}</td>
        <td><span class="tag ${o.call_put==='P'?'short':(o.call_put==='C'?'long':'')}">${o.call_put==='P'?'看跌':(o.call_put==='C'?'看涨':'—')}</span></td>
        <td><span class="tag ${o.direction==='buy'?'buy':'sell'}">${o.direction==='buy'?'买入':'卖出'}</span></td>
        <td>${qtyTxt}</td>
        <td>${(price||0).toLocaleString('en-US',{maximumFractionDigits:4})}</td>
        <td>${premiumTxt}</td>
        <td class="${pnlCls}">${o.pnl!=null ? (o.pnl>0?'+CN¥':(o.pnl<0?'-CN¥':'CN¥'))+Math.abs(o.pnl).toLocaleString('en-US',{maximumFractionDigits:2}) : '—'}</td>
        <td><span class="tag ${st.cls}">${st.txt}</span></td>
        <td class="op-note"><span class="op-note-txt"${o.note ? ' title="' + escHtml(o.note) + '"' : ''}>${o.note ? escHtml(o.note) : '—'}</span></td>
        <td class="row-actions">
          <button class="iconbtn" data-edit="${o.id}" title="修改">✎</button>
          <button class="iconbtn" data-del="${o.id}" title="删除">🗑</button>
        </td>
      </tr>`;
    }).join('');
    ob.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => {
      const op = d.operations.find(x => x.id === +b.dataset.edit);
      if (op) this.openEditModal(op.op_type, op);
    }));
    ob.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => this.deleteOp(+b.dataset.del)));
  },

  _rowStatus(op, detail){
    // 统计该合约 open qty - close qty (FIFO): 当前剩余数量
    const ops = detail.operations;
    let opened = 0, closed = 0;
    for (const x of ops){
      if (x.contract !== op.contract) continue;
      if (x.op_type === 'open') opened += x.qty;
      else closed += (x.close_qty||0);
    }
    if (op.op_type === 'open'){
      // 此 open 自身被后续 close 平掉的数量
      let closedAfter = 0;
      let seen = false;
      for (const x of ops){
        if (x === op){ seen = true; continue; }
        if (!seen) continue;
        if (x.contract !== op.contract) continue;
        if (x.op_type === 'close') closedAfter += (x.close_qty||0);
      }
      const left = op.qty - closedAfter;
      if (left <= 0) return { cls: 'closed', txt: '已平仓' };
      if (closedAfter > 0) return { cls: 'partial', txt: '部分平仓' };
      return { cls: 'unclosed', txt: '未平仓' };
    } else {
      // close 行: 显示此 close 是否为"全平"该 open
      return { cls: 'close', txt: '平仓' };
    }
  },

  closeDetail(){
    this.detail = null;
    $('tradeDetailPanel').classList.add('hidden');
    document.querySelector('.trades-layout').classList.remove('has-detail');
  },

  renderPool(){
    const box = $('poolArea');
    if (!this.pool.length){
      box.innerHTML = '<div class="tip">暂无监控池快照，点「➕ 新建监控池快照」记录当前品种</div>';
      $('btnPoolHistory').hidden = true;
      return;
    }
    $('btnPoolHistory').hidden = false;
    // 最新快照展开, 历史快照折叠(可点击展开)
    box.innerHTML = this.pool.map((s, idx) => {
      const collapsed = idx > 0 && !this.poolCollapsed[s.snapshot_date];
      const content = collapsed ? '' : `<div class="pool-content">${
        s.contracts.map(c => `<span class="pool-chip">${escHtml(c)}</span>`).join('')
      }</div>`;
      const head = `<div class="pool-title${collapsed?' collapsed':''}" data-date="${escHtml(s.snapshot_date)}" data-id="${s.id}">
        <span class="arrow">▼</span>
        <span>${escHtml(s.snapshot_date)} 更新</span>
        <span class="row-actions" style="margin-left:auto">
          <button class="iconbtn" data-edit-pool="${s.id}" data-date="${escHtml(s.snapshot_date)}" data-cs='${escHtml(JSON.stringify(s.contracts))}' title="修改">✎</button>
          <button class="iconbtn" data-del-pool="${s.id}" title="删除">🗑</button>
        </span>
      </div>`;
      return `<div class="pool-block">${head}${content}</div>`;
    }).join('');
    box.querySelectorAll('.pool-title').forEach(t => t.addEventListener('click', e => {
      const btn = e.target.closest('[data-edit-pool], [data-del-pool]');
      if (btn) {
        e.stopPropagation();
        if (btn.hasAttribute('data-edit-pool')) {
          this.openPoolModal({
            id: +btn.dataset.editPool,
            snapshot_date: btn.dataset.date,
            contracts: JSON.parse(btn.dataset.cs),
          });
        } else {
          this.deletePool(+btn.dataset.delPool);
        }
        return;
      }
      this.poolCollapsed[t.dataset.date] = !this.poolCollapsed[t.dataset.date];
      this.renderPool();
    }));
    this.updatePoolHistoryBtn();
  },

  // 「查看历史快照」一键展开/收起历史快照(最新快照始终展开)
  togglePoolHistory(){
    const historical = this.pool.slice(1);   // 最新一条是 idx=0 始终展开
    if (!historical.length) return;
    const allExpanded = historical.every(s => this.poolCollapsed[s.snapshot_date] === true);
    if (allExpanded){
      // 当前全部展开 → 收起
      historical.forEach(s => { this.poolCollapsed[s.snapshot_date] = false; });
    } else {
      // 当前有收起 → 全部展开
      historical.forEach(s => { this.poolCollapsed[s.snapshot_date] = true; });
    }
    this.renderPool();
  },

  // 按当前折叠状态更新按钮文字
  updatePoolHistoryBtn(){
    const historical = this.pool.slice(1);
    if (!historical.length){ $('btnPoolHistory').textContent = '📜 查看历史快照'; return; }
    const allExpanded = historical.every(s => this.poolCollapsed[s.snapshot_date] === true);
    $('btnPoolHistory').textContent = allExpanded ? '📜 收起历史快照' : '📜 查看历史快照';
  },

  /* ---------- 新建/编辑 开仓/平仓 弹框 (统一 modal) ---------- */
  openEditModal(type, preset){
    preset = preset || {};
    const bg = $('tradeModalBg');
    if (!bg) return;
    // 重置
    ['tmUnderlying','tmContract','tmOpenDate','tmCloseDate','tmOpenDelta','tmTargetDelta',
     'tmOpenPrice','tmClosePrice','tmQty','tmCloseQty','tmPremium','tmPnl','tmNote'].forEach(id=>{ $(id).value=''; });
    $('tmCallPut').value = preset.call_put || '';
    $('tmDirection').value = preset.direction || 'buy';
    $('tmOpType').value = type;
    $('tmError').textContent = '';
    const isOpen = type === 'open';
    const isEdit = !!preset.id;
    $('tmTitle').textContent = isEdit ? ('修改' + (isOpen?'开仓':'平仓')) : ('新建' + (isOpen?'开仓':'平仓'));

    // 平仓模式下: 锁定 underlying, contract 改成下拉选择(从 holdings 取); 方向自动取反且隐藏
    const contractInput = $('tmContract');
    if (!isOpen && isEdit){
      // 修改已有平仓记录: 直接用 preset 数据填充, 不依赖 holdings(全部平完时 holdings 为空也能编辑)
      const inp = document.createElement('input');
      inp.id = 'tmContract';
      inp.type = 'text';
      contractInput.replaceWith(inp);
      $('tmUnderlying').value = preset.underlying || '';
      $('tmUnderlying').readOnly = true;
      $('tmDirectionWrap').classList.add('disabled');
      $('tmDirection').disabled = true;
      $('tmDirection').value = preset.direction || 'sell';
      $('tmCallPut').value = preset.call_put || '';
      $('tmCallPut').disabled = true;
      $('tmCloseQtyHint').textContent = '';
    } else if (!isOpen && this.detail && this.detail.holdings && this.detail.holdings.length){
      const sel = document.createElement('select');
      sel.id = 'tmContract';
      sel.dataset.contractSelect = '1';
      this.detail.holdings.forEach((h, i) => {
        const o = document.createElement('option');
        o.value = h.contract;
        o.dataset.remaining = h.qty;
        o.dataset.opendir = h.direction;
        o.dataset.callput = h.call_put || '';
        o.textContent = `${h.contract} 看${h.call_put==='P'?'跌':'涨'} ${h.direction==='buy'?'买入':'卖出'} 余${h.qty}手 @均价${h.open_price}`;
        if (i === 0) o.selected = true;
        sel.appendChild(o);
      });
      contractInput.replaceWith(sel);
      $('tmUnderlying').value = this.detail.underlying;
      $('tmUnderlying').readOnly = true;
      // 方向: 自动取反(跟随所选合约的开仓方向; 开仓买→平仓卖, 开仓卖→平仓买)
      // 看涨看跌: 自动与原合约一致; 数量按实际平仓写; 权利金填 "/"(平仓不占用新权利金)
      const _syncCp = () => {
        const selEl = $('tmContract');
        const opt = selEl.options[selEl.selectedIndex];
        $('tmCallPut').value = opt ? (opt.dataset.callput || '') : '';
      };
      const _syncDir = () => {
        const selEl = $('tmContract');
        const opt = selEl.options[selEl.selectedIndex];
        const openDir = opt ? (opt.dataset.opendir || 'buy') : 'buy';
        $('tmDirection').value = openDir === 'buy' ? 'sell' : 'buy';
      };
      _syncDir(); _syncCp();
      $('tmDirectionWrap').classList.add('disabled');
      $('tmDirection').disabled = true;
      $('tmContract').addEventListener('change', () => { _syncDir(); _syncCp(); this._refreshCloseQtyHint(); });
      // 提示 close_qty 上限
      this._refreshCloseQtyHint();
    } else if (!isOpen){
      alert('请先在详情页里打开一个标的, 再点击「新建平仓」');
      return;
    } else {
      // 开仓: contract 是文本输入; 输入合约自动推断看涨看跌(带 P→看跌, 带 C→看涨, 忽略大小写)
      const inp = document.createElement('input');
      inp.id = 'tmContract';
      inp.type = 'text';
      inp.placeholder = '如 lc2611-C-144000 或 ao611P2500';
      contractInput.replaceWith(inp);
      $('tmUnderlying').value = preset.underlying || '';
      $('tmUnderlying').readOnly = !!preset.underlying;
      $('tmDirectionWrap').classList.remove('disabled');
      $('tmDirection').disabled = false;
      $('tmCloseQtyHint').textContent = '';
      // 输入即自动填看涨看跌; 兼容 lc2611-C-144000(分隔) / ao611P2500(紧凑) 两种写法
      // ⚠ 不能只按「有 C/P」判断 — 24 个品种代码自带 C/P(玉米 c / 棉花 cf / 纸浆 sp / 棕榈油 p / 聚丙烯 pp / 苹果 ap / 花生 pk)
      const _autoCp = () => {
        const cp = this.detectCallPut(inp.value);
        if (cp) $('tmCallPut').value = cp;
      };
      inp.addEventListener('input', _autoCp);
      // 失焦时把输入框内容格式化成规范写法(品种小写 + C/P 大写 + 分隔符统一), 与后端保存口径一致
      inp.addEventListener('blur', () => {
        const v = this.normalizeContract(inp.value);
        if (v && v !== inp.value) inp.value = v;
      });
      if (!preset.call_put) _autoCp();
    }

    // 字段显示: 开仓需要 open_date/qty/premium/open_price; 平仓需要 close_date/close_qty/close_price/pnl
    document.getElementById('tmOpenDate').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmOpenDelta').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmTargetDelta').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmOpenPrice').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmQty').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmPremium').parentElement.style.display = isOpen ? '' : 'none';
    document.getElementById('tmCallPut').parentElement.style.display = '';   // 两模式都显示
    document.getElementById('tmCallPut').disabled = !isOpen;                // 平仓自动, 只读
    document.getElementById('tmCloseDate').parentElement.style.display = isOpen ? 'none' : '';
    document.getElementById('tmCloseQtyWrap').style.display = isOpen ? 'none' : '';
    document.getElementById('tmClosePriceWrap').style.display = isOpen ? 'none' : '';
    document.getElementById('tmPnlWrap').style.display = isOpen ? 'none' : '';

    // 预设值(编辑模式)
    if (preset.id){
      $('tmUnderlying').value = preset.underlying || '';
      const contractEl = $('tmContract');
      if (contractEl.tagName === 'INPUT') contractEl.value = preset.contract || '';
      else if (contractEl.dataset.contractSelect){ /* 平仓 select 设 value */ contractEl.value = preset.contract || ''; }
      $('tmOpenDate').value = preset.open_date || '';
      $('tmCloseDate').value = preset.close_date || '';
      $('tmOpenDelta').value = preset.open_delta != null ? preset.open_delta : '';
      $('tmTargetDelta').value = preset.target_delta != null ? preset.target_delta : '';
      $('tmOpenPrice').value = preset.open_price != null ? preset.open_price : '';
      $('tmClosePrice').value = preset.close_price != null ? preset.close_price : '';
      $('tmQty').value = preset.qty || '';
      $('tmCloseQty').value = preset.close_qty || '';
      $('tmPremium').value = preset.premium || '';
      $('tmPnl').value = preset.pnl != null ? preset.pnl : '';
      $('tmCallPut').value = preset.call_put || '';
      $('tmDirection').value = preset.direction || 'buy';
      $('tmNote').value = preset.note || '';
    } else {
      // 默认日期
      const today = new Date().toISOString().slice(0,10);
      if (isOpen) $('tmOpenDate').value = today;
      else $('tmCloseDate').value = today;
      // 开仓默认 contract = underlying (用户可改)
      if (isOpen && preset.underlying) $('tmContract').value = preset.underlying;
      // 行内+开仓默认 delta 0.3, 目标 0.45
      if (isOpen && preset.underlying){
        $('tmOpenDelta').value = '0.3';
        $('tmTargetDelta').value = '0.45';
      }
    }
    $('tmUnderlying').readOnly = preset.id ? true : (preset.underlying ? true : false);
    bg.classList.remove('hidden');
    bg.dataset.editing = preset.id || '';
  },

  _refreshCloseQtyHint(){
    const sel = $('tmContract');
    if (!sel || sel.tagName !== 'SELECT') return;
    const opt = sel.options[sel.selectedIndex];
    const remaining = opt ? +opt.dataset.remaining : 0;
    const hint = $('tmCloseQtyHint');
    if (hint) hint.textContent = remaining ? ('已开仓剩余 ' + remaining + ' 手, 最多可平 ' + remaining) : '该合约无可平仓余量';
  },

  collectFromModal(){
    const isOpen = $('tmOpType').value === 'open';
    const contractEl = $('tmContract');
    const contractVal = contractEl.tagName === 'SELECT' ? contractEl.value : contractEl.value.trim();
    const fields = {
      id: $('tradeModalBg').dataset.editing || null,
      op_type: isOpen ? 'open' : 'close',
      underlying: $('tmUnderlying').value.trim(),
      contract: TradeUI.normalizeContract(contractVal),
      open_date: $('tmOpenDate').value,
      open_delta: $('tmOpenDelta').value === '' ? null : parseFloat($('tmOpenDelta').value),
      target_delta: $('tmTargetDelta').value === '' ? null : parseFloat($('tmTargetDelta').value),
      call_put: $('tmCallPut').value,
      direction: $('tmDirection').value,
      open_price: $('tmOpenPrice').value === '' ? null : parseFloat($('tmOpenPrice').value),
      qty: parseInt($('tmQty').value || '0', 10),
      premium: parseFloat($('tmPremium').value || '0') || 0,
      close_qty: parseInt($('tmCloseQty').value || '0', 10),
      close_price: $('tmClosePrice').value === '' ? null : parseFloat($('tmClosePrice').value),
      pnl: $('tmPnl').value === '' ? null : parseFloat($('tmPnl').value),
      close_date: $('tmCloseDate').value,
      note: $('tmNote').value,
    };
    return fields;
  },

  async submitModal(){
    const f = this.collectFromModal();
    const errBox = $('tmError');
    if (!f.underlying) return errBox.textContent = '请填写开仓标的', false;
    if (!f.contract) return errBox.textContent = '请填写合约代码', false;
    if (f.op_type === 'open'){
      if (!f.open_date) return errBox.textContent = '请填写开仓日期', false;
      if (!(f.qty > 0)) return errBox.textContent = '请填写有效数量(>0)', false;
    } else {
      if (!f.close_date) return errBox.textContent = '请填写平仓日期', false;
      if (!(f.close_qty > 0)) return errBox.textContent = '请填写平仓数量(>0)', false;
    }
    errBox.textContent = '';
    try {
      const r = await fetchT('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(f)});
      const d = await r.json();
      if (!d.ok) { errBox.textContent = '保存失败: ' + d.error; return false; }
      $('tradeModalBg').classList.add('hidden');
      await this.refresh();
      return true;
    } catch (e) { errBox.textContent = '保存失败: ' + fmtNetError(e); return false; }
  },

  async deleteOp(id){
    if (!confirm('确认删除这条操作记录?')) return;
    try {
      const r = await fetchT('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
      const d = await r.json();
      if (!d.ok) { alert('删除失败: ' + d.error); return; }
      await this.refresh();
    } catch (e) { alert('删除失败: ' + e); }
  },

  /* 主表行修改/删除: 编辑最新一条 open 记录 / 删除该 underlying 全部记录 */
  async editUnderlying(underlying){
    try {
      const r = await fetchT('/api/trades/detail?underlying=' + encodeURIComponent(underlying));
      const d = await r.json();
      if (!d.ok || !d.operations.length){ alert('无记录可编辑'); return; }
      // 取最早一条 open 记录(代表性"开仓内容")
      const open = d.operations.find(x => x.op_type === 'open');
      if (!open){ alert('该标的没有开仓记录, 无需修改'); return; }
      this.loadDetail(underlying);   // 同时展开分页面, 让用户看到全貌
      this.openEditModal('open', open);
    } catch (e) { alert('加载失败: ' + e); }
  },

  async deleteUnderlying(underlying){
    if (!confirm('确认删除 ' + underlying + ' 的全部交易记录? 此操作不可恢复')) return;
    try {
      const r = await fetchT('/api/trades/detail?underlying=' + encodeURIComponent(underlying));
      const d = await r.json();
      if (!d.ok){ alert('加载失败'); return; }
      for (const op of d.operations){
        await fetchT('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
      }
      this.closeDetail();
      await this.refresh();
    } catch (e) { alert('删除失败: ' + e); }
  },

  /* ---------- 监控池弹框 ---------- */
  openPoolModal(preset){
    preset = preset || {};
    const cur = preset.contracts ? preset.contracts.join('、') : '';
    const input = prompt('监控品种(逗号或顿号分隔, 如 si、lc、fu、ao、br):', cur);
    if (input === null) return;
    const contracts = input.split(/[,、， \t\n]+/).map(s => s.trim()).filter(Boolean);
    if (!contracts.length) { alert('至少输入一个品种'); return; }
    const date = preset.snapshot_date || new Date().toISOString().slice(0,10);
    const payload = { id: preset.id || null, snapshot_date: date, contracts, note: '' };
    fetchT('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)})
      .then(r => r.json()).then(d => {
        if (!d.ok) { alert('保存失败: ' + d.error); return; }
        this.refresh();
      }).catch(e => alert('保存失败: ' + fmtNetError(e)));
  },

  async deletePool(id){
    if (!confirm('确认删除这个监控池快照?')) return;
    try {
      const r = await fetchT('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
      const d = await r.json();
      if (!d.ok) { alert('删除失败: ' + d.error); return; }
      await this.refresh();
    } catch (e) { alert('删除失败: ' + e); }
  },
};
TradeUI.init();

/* =================================================================
   资金曲线模块（独立命名空间 FundUI）
   ================================================================= */
/* 图表数值标签插件: 柱状图顶部显示金额(万), 折线图节点上方显示 % */
function fmtWan(v){
  const abs = Math.abs(v);
  if (abs >= 10000) return (v/10000).toFixed(2) + '万';
  return v.toLocaleString('en-US');
}
// 读 CSS 变量字号(px), 供 Chart.js 随字号档位联动
function cssFz(name, fallback){
  const v = getComputedStyle(document.body).getPropertyValue(name).trim();
  const n = parseFloat(v);
  return isNaN(n) ? fallback : n;
}
// 读 CSS 变量颜色值, 供 Chart.js 随明暗主题联动
function cssClr(name, fallback){
  const v = getComputedStyle(document.body).getPropertyValue(name).trim();
  return v || fallback;
}
const valueLabelPlugin = {
  id: 'valueLabel',
  afterDatasetsDraw(chart, args, opts){
    const {ctx} = chart;
    ctx.save();
    const fs = (chart.options.plugins && chart.options.plugins.valueLabel && chart.options.plugins.valueLabel.fontSize)
      || cssFz('--fz-chart2', 14);
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
        ctx.strokeStyle = cssClr('--chart-stroke','rgba(11,16,32,.85)');   // 描边色(随主题)
        ctx.strokeText(label, pt.x, pt.y + (isLine ? -10 : (v >= 0 ? -5 : 16)));
        ctx.fillStyle = isLine ? '#e2c985' : cssClr('--chart-label','#d9e6dc');
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
        localStorage.setItem('oc-last-tab', tab);
        $('calcArea').classList.toggle('hidden', tab !== 'calc');
        $('fundsArea').classList.toggle('hidden', tab !== 'funds');
        $('tradesArea').classList.toggle('hidden', tab !== 'trades');
        // header 标题随 tab 联动
        const titles = {
          calc:   {t:'期货开仓计算器', s:'风控仓位计算 · 盈亏比决策 · 保证金测算'},
          trades: {t:'abe 期权交易记录', s:'策略: abe · 期权买方代替期货开仓 · 逐笔记录 + 自动汇总'},
          funds:  {t:'资金曲线',        s:'abe · 威科夫 多策略记录'}
        };
        // header logo 跟随当前 tab, 与左侧栏图标保持一致
        const logos = {
          calc:   '<svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="#E8EDF2"/><rect x="26" y="10" width="48" height="56" rx="10" fill="none" stroke="#7FA8CC" stroke-width="8"/><rect x="35" y="18" width="30" height="11" rx="3" fill="#E8B255"/><rect x="34" y="34" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="53" y="34" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="34" y="49" width="13" height="13" rx="1.5" fill="#7FA8CC"/><rect x="53" y="49" width="13" height="13" rx="1.5" fill="#7FA8CC"/></svg>',
          trades: '<svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="#E8EDF2"/><rect x="26" y="36" width="48" height="11" rx="3" fill="#7FA8CC"/><rect x="26" y="53" width="30" height="11" rx="3" fill="#E8B255"/></svg>',
          funds:  '<svg viewBox="0 0 100 100" aria-hidden="true"><rect x="16" y="74" width="68" height="8" rx="4" fill="#E8EDF2"/><path d="M25 62 L42 48 L57 57 L74 31" fill="none" stroke="#7FA8CC" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/><circle cx="75" cy="30" r="7" fill="#E8B255"/></svg>'
        };
        const ti = titles[tab];
        if (ti) { $('appTitle').textContent = ti.t; $('appSubtitle').textContent = ti.s; }
        if (logos[tab] && $('logoIco')) $('logoIco').innerHTML = logos[tab];
        if (tab === 'trades' && typeof TradeUI !== 'undefined') TradeUI.refresh();
        if (tab === 'funds' && (!this.monthly.length && !this.yearly.length)) {
          this.refreshAll();
        }
      });
    });
    // 刷新后恢复上次所在页面(默认 calc)
    const saved = localStorage.getItem('oc-last-tab');
    if (saved && saved !== 'calc' && ['trades','funds'].includes(saved)) {
      document.querySelector('#mainTabs .maintab[data-tab="' + saved + '"]').click();
    }
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
      // 备份含三部分: 资金曲线 / 交易记录 / 监控池 — 任一部分有数据即可导出
      const nRec = (d.records||[]).length;
      const nTrd = (d.trades||[]).length;
      const nPool = (d.trade_pools||[]).length;
      const total = nRec + nTrd + nPool;
      if (!total) { alert('当前没有可导出的数据（资金曲线 / 交易记录 / 监控池均为空）'); return; }
      const summary = '资金曲线 ' + nRec + ' 条 · 交易记录 ' + nTrd + ' 条 · 监控池 ' + nPool + ' 条';
      const blob = new Blob([JSON.stringify(d, null, 2)], {type:'application/json'});
      const now = new Date();
      const pad = n=>String(n).padStart(2,'0');
      const fname = 'OpenCalc备份_' + now.getFullYear() + pad(now.getMonth()+1) + pad(now.getDate()) + '_' + pad(now.getHours()) + pad(now.getMinutes()) + '.opcalc';
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
          alert('✅ 已导出 → ' + handle.name + '\n' + summary + '\n请把该文件拷贝到新电脑，用「导入」恢复。');
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
      alert('✅ 已导出 → ' + fname + '\n' + summary + '\n文件已保存到浏览器默认「下载」目录，如需自选位置请使用新版弹窗。');
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
        alert('✅ 导入成功：共 ' + d.imported + ' 条（资金曲线 + 交易记录 + 监控池，同主键覆盖合并）'
          + (d.strategies && d.strategies.length ? '\n资金曲线策略：' + d.strategies.join('、') : ''));
        this.refreshAll();
        if (typeof TradeUI !== 'undefined' && TradeUI.refresh) TradeUI.refresh();   // 交易记录页同步刷新
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
      // 数据落在软件目录内 → 醒目警告(更新软件会连数据一起删)
      const risk = $('setRisk');
      if (d.risky) { $('setRiskApp').textContent = d.app_dir; risk.classList.remove('hidden'); }
      else { risk.classList.add('hidden'); }
      // 自动备份信息
      $('setBkDir').textContent = d.backup_dir || '—';
      $('setBkCnt').textContent = d.backup_count || 0;
      $('setBkLast').textContent = d.last_backup
        ? '（最近 ' + new Date(d.last_backup.mtime*1000).toLocaleString('zh-CN', {hour12:false}) + '）' : '';
      $('setMigrateSafe').style.display = d.risky ? '' : 'none';
      $('setBg').classList.remove('hidden');
      this.loadBackups();                      // 备份列表(含每份的条数 + 恢复按钮)
      setTimeout(()=>$('setDir').focus(), 60);
    } catch (e) {
      alert('读取数据位置失败：' + e);
    }
  },

  // 立即手动备份一份
  async backupNow(){
    try {
      const r = await fetchT('/api/funds/backup', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      const d = await r.json();
      if (d.ok) { this.openDataDir(); }
      else { alert('备份失败：' + (d.error||'')); }
    } catch (e) { alert('备份失败：' + e); }
  },

  /* ---- 备份列表: 展示每份快照的时间/条数, 支持一键恢复 ---- */
  async loadBackups(){
    const box = $('setBkList');
    if (!box) return;
    box.innerHTML = '<div style="padding:10px;color:var(--sub);font-size:12.5px">读取中…</div>';
    try {
      const d = await (await fetchT('/api/funds/backups')).json();
      const list = d.backups || [];
      if (!list.length) {
        box.innerHTML = '<div style="padding:12px;color:var(--sub);font-size:12.5px">还没有备份（做一次增删改就会自动生成）</div>';
        return;
      }
      box.innerHTML = list.map((b, i) => {
        const t = new Date(b.mtime * 1000).toLocaleString('zh-CN', {hour12:false});
        const info = (b.records == null) ? '' :
          ('资金 ' + b.records + ' 条 · 交易 ' + (b.trades==null?'?':b.trades) + ' 条');
        return '<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;'
          + (i ? 'border-top:1px solid var(--line);' : '') + '">'
          + '<span style="flex:1;min-width:0">'
          + '<b style="font-size:12.5px">' + t + '</b>'
          + (i === 0 ? ' <span style="color:var(--accent);font-size:11.5px">最新</span>' : '')
          + '<br><span style="color:var(--sub);font-size:11.5px">' + info + '</span>'
          + '</span>'
          + '<button class="btn xs" data-bkrestore="' + b.name + '">恢复</button>'
          + '</div>';
      }).join('');
      box.querySelectorAll('[data-bkrestore]').forEach(btn => {
        btn.addEventListener('click', () => this.restoreBackup(btn.dataset.bkrestore));
      });
    } catch (e) {
      box.innerHTML = '<div style="padding:10px;color:var(--sub);font-size:12.5px">读取备份失败：' + e + '</div>';
    }
  },

  async restoreBackup(name){
    if (!confirm('⚠ 将用这份备份【覆盖当前数据】：\n' + name
      + '\n\n当前的数据会先自动备份一份，所以还能再退回来。\n\n确定恢复吗？')) return;
    try {
      const r = await fetchT('/api/funds/restore', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})}, 30000);
      const d = await r.json();
      if (!d.ok) { alert('恢复失败：' + (d.error||'')); return; }
      alert('✅ 已恢复\n\n资金曲线 ' + d.records + ' 条 · 交易记录 ' + d.trades + ' 条 · 监控池 ' + d.pools + ' 条');
      this.openDataDir();
      this.refreshAll();
    } catch (e) { alert('恢复失败：' + e); }
  },

  /* ---- 内置文件夹浏览器(不依赖 COM/tkinter, 原生对话框在部分机器上会静默失败) ---- */
  async browseDataDir(){
    $('pickBg').classList.remove('hidden');
    await this.pickLoad($('setDir').value.trim() || '');
  },

  async pickLoad(path){
    try {
      const d = await (await fetchT('/api/fs/list?path=' + encodeURIComponent(path || ''))).json();
      if (!d.ok && d.error) { /* 目录读不了也照样展示盘符, 不静默 */ }
      this.pickCur = d.path || '';
      $('pickPath').value = this.pickCur;
      // 盘符快捷区
      $('pickDrives').innerHTML = (d.drives || []).map(function (dr) {
        return '<button class="btn xs" data-pickdrive="' + dr + '">' + dr + '</button>';
      }).join('');
      $('pickDrives').querySelectorAll('[data-pickdrive]').forEach(btn => {
        btn.addEventListener('click', () => this.pickLoad(btn.dataset.pickdrive));
      });
      // 目录列表
      const rows = (d.dirs || []).map(function (n) {
        return '<div data-pickdir="' + n + '" style="display:flex;align-items:center;gap:8px;padding:7px 10px;'
          + 'border-bottom:1px solid var(--line);cursor:pointer">📁 <span style="flex:1">' + n + '</span></div>';
      });
      $('pickList').innerHTML = rows.length ? rows.join('')
        : '<div style="padding:12px;color:var(--sub);font-size:12.5px">'
          + (d.error ? ('读取失败：' + d.error) : '该目录下没有子文件夹') + '</div>';
      $('pickList').querySelectorAll('[data-pickdir]').forEach(el => {
        el.addEventListener('click', () => this.pickLoad(this.pickJoin(this.pickCur, el.dataset.pickdir)));
        el.addEventListener('dblclick', () => this.pickLoad(this.pickJoin(this.pickCur, el.dataset.pickdir)));
      });
    } catch (e) {
      $('pickList').innerHTML = '<div style="padding:12px;color:var(--sub);font-size:12.5px">读取失败：' + e + '</div>';
    }
  },

  pickJoin(base, name){
    if (!base) return name;
    const sep = (base.indexOf('\\') >= 0) ? '\\' : '/';
    return base.replace(/[\\/]+$/, '') + sep + name;
  },

  // 一键迁出: 把数据挪到软件目录之外的推荐位置(后端算好, 避免前端拼路径出错)
  async migrateOutOfAppDir(){
    let suggested = '';
    try {
      suggested = (await (await fetchT('/api/funds/data-info')).json()).suggest_dir || '';
    } catch (e) { /* 下面兜底 */ }
    if (!suggested) { alert('未能自动推荐安全位置，请在下面手动填写一个软件目录之外的路径。'); return; }
    if (!confirm('将把数据迁移到下面这个位置（软件目录之外，更新软件不会影响）：\n\n' + suggested + '\n\n确定继续吗？')) return;
    try {
      const r = await fetchT('/api/funds/data-dir', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({dir: suggested})}, 30000);
      const d = await r.json();
      if (d.ok) {
        alert('✅ 已迁出到：\n' + d.data_dir + '\n\n共迁移 ' + d.migrated + ' 条记录。\n以后更新软件不会再动到这份数据。');
        this.openDataDir();
        this.refreshAll();
      } else {
        alert('迁出失败：' + (d.error||''));
      }
    } catch (e) { alert('迁出失败：' + e); }
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

  async saveDataDir(){
    const dir = $('setDir').value.trim();
    if (!dir) { alert('请输入数据目录路径'); return; }
    try {
      // ⚠ 若目标目录落在软件目录内, 明确警告(否则用户又把数据放进会被更新的位置)
      try {
        const info = await (await fetchT('/api/funds/data-info')).json();
        const norm = (p) => String(p||'').replace(/\//g,'\\').replace(/\\+$/,'').toLowerCase();
        const t = norm(dir), a = norm(info.app_dir);
        if (a && (t === a || t.startsWith(a + '\\'))) {
          if (!confirm('⚠ 这个目录在软件自己的文件夹里面：\n' + dir + '\n\n以后更新软件（替换/清理该文件夹）会把记录一起删掉。\n\n确定还要用这里吗？')) return;
        }
      } catch (e) { /* 校验失败不阻断 */ }
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
    $('setBkNow').addEventListener('click', ()=>this.backupNow());
    $('setMigrateSafe').addEventListener('click', ()=>this.migrateOutOfAppDir());
    // 数据安全横幅按钮
    $('dataRiskMigrate').addEventListener('click', ()=>this.migrateOutOfAppDir());
    $('dataRiskLater').addEventListener('click', ()=>$('dataRiskBanner').classList.add('hidden'));
    $('dataRiskClose').addEventListener('click', ()=>{
      sessionStorage.setItem('dataRiskClosed', '1');    // 本次会话不再提示
      $('dataRiskBanner').classList.add('hidden');
    });
    // 一键清除所有记录
    $('btnClearAll').addEventListener('click', ()=>this.clearAllRecords());
    // 联系作者(左下角浮动按钮)
    $('floatingContact').addEventListener('click', ()=>$('contactBg').classList.remove('hidden'));
    $('contactClose').addEventListener('click', ()=>$('contactBg').classList.add('hidden'));
    $('contactBg').addEventListener('click', e=>{ if (e.target===$('contactBg')) $('contactBg').classList.add('hidden'); });
    $('setBrowse').addEventListener('click', ()=>this.browseDataDir());
    // 内置文件夹浏览器
    $('pickCancel').addEventListener('click', ()=>$('pickBg').classList.add('hidden'));
    $('pickBg').addEventListener('click', e=>{ if (e.target===$('pickBg')) $('pickBg').classList.add('hidden'); });
    $('pickUp').addEventListener('click', async ()=>{
      const d = await (await fetchT('/api/fs/list?path=' + encodeURIComponent(this.pickCur || ''))).json();
      this.pickLoad(d.parent || this.pickCur || '');
    });
    $('pickOk').addEventListener('click', ()=>{
      const cur = (this.pickCur || '').trim();
      if (cur) $('setDir').value = cur;
      $('pickBg').classList.add('hidden');
    });
    $('pickPath').addEventListener('keydown', e=>{ if (e.key === 'Enter') this.pickLoad($('pickPath').value); });
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
      alert('保存失败：' + fmtNetError(e));
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
  // 启动时检查数据是否落在软件目录内: 是则亮红点 + 顶部横幅提醒(更新软件会删数据)
  // ⚠ 用横幅而不是 confirm(): 阻塞对话框会打断用户, 也会卡住自动化测试
  async checkDataSafety(){
    try {
      const d = await (await fetchT('/api/funds/data-info')).json();
      const dot = $('dataRiskDot');
      const banner = $('dataRiskBanner');
      if (d.risky) {
        if (dot) dot.classList.remove('hidden');
        $('btnDataDir').title = '⚠ 数据存在软件目录内，更新软件会删除！点这里迁出';
        if (banner && !sessionStorage.getItem('dataRiskClosed')) {
          $('dataRiskPath').textContent = d.data_dir;
          banner.classList.remove('hidden');
        }
      } else {
        if (dot) dot.classList.add('hidden');
        if (banner) banner.classList.add('hidden');
        $('btnDataDir').title = '把数据存到网盘同步文件夹，换电脑不丢记录';
      }
    } catch (e) { /* 静默 */ }
  },

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
          {label: '本月末权益', data: equity, backgroundColor: 'rgba(165,138,224,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '本月盈亏',  data: pnl,    backgroundColor: 'rgba(231,143,181,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '本月收益率(%)', data: rate, type: 'line', borderColor: '#e2c985', backgroundColor: '#e2c985',
            tension: 0.35, pointRadius: 4, borderWidth: 2.5, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {display: false},
          tooltip: {intersect: false, mode: 'index', titleFont: {size: cssFz('--fz-chart2',14)}, bodyFont: {size: cssFz('--fz-chart',13)},
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:cssClr('--chart-grid','rgba(150,205,175,.12)')}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13)}, callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#e2c985', font:{size: cssFz('--fz-chart',13)}, callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13)}, autoSkip: true, maxRotation: 0}},
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
          {label: '年末权益',   data: endEq, backgroundColor: 'rgba(165,138,224,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '年度总盈亏', data: yPnl,  backgroundColor: 'rgba(231,143,181,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: '年化收益率(%)', data: yRate, type: 'line', borderColor: '#e2c985', backgroundColor: '#e2c985',
            tension: 0.35, pointRadius: 5, borderWidth: 2.5, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {display: false},
          tooltip: {intersect: false, mode: 'index', titleFont: {size: cssFz('--fz-chart2',14)}, bodyFont: {size: cssFz('--fz-chart',13)},
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:cssClr('--chart-grid','rgba(150,205,175,.12)')}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13)}, callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#e2c985', font:{size: cssFz('--fz-chart',13)}, callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13)}}},
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
      ? '<span class="lg"><i style="background:#a58ae0"></i>本月末权益（扣掉出入金之后）</span><span class="lg"><i style="background:#e78fb5"></i>本月盈亏</span><span class="lg"><i style="background:#e2c985"></i>本月收益率（折线，右轴）</span>'
      : '<span class="lg"><i style="background:#a58ae0"></i>年末权益</span><span class="lg"><i style="background:#e78fb5"></i>年度总盈亏</span><span class="lg"><i style="background:#e2c985"></i>年化收益率（折线，右轴）</span>';
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
          {label: t1, data: d1, backgroundColor: 'rgba(165,138,224,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: t2, data: d2, backgroundColor: 'rgba(231,143,181,.9)', borderRadius: 6, order: 2, yAxisID: 'y'},
          {label: t3, data: d3, type: 'line', borderColor: '#e2c985', backgroundColor: '#e2c985',
            tension: 0.35, pointRadius: 6, borderWidth: 3, order: 1, yAxisID: 'y1'},
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {duration: 400},
        plugins: {
          legend: {display: false},
          valueLabel: {fontSize: cssFz('--fz-chart2',14) + 2},   // 放大图数值标签更大
          tooltip: {intersect: false, mode: 'index', titleFont: {size: cssFz('--fz-chart2',14)}, bodyFont: {size: cssFz('--fz-chart',13)},
            callbacks: {label: ctx=> ctx.dataset.label + ': ' + ctx.formattedValue + (ctx.dataset.label.includes('%')?'%':'')}},
        },
        scales: {
          y:  {position:'left',  grid:{color:cssClr('--chart-grid','rgba(150,205,175,.12)')}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13) + 0.5}, callback:v=>v.toLocaleString()}},
          y1: {position:'right', grid:{display:false},            ticks:{color:'#e2c985', font:{size: cssFz('--fz-chart',13) + 0.5}, callback:v=>v.toFixed(0)+'%'}},
          x:  {grid:{display:false}, ticks:{color:cssClr('--chart-tick','#9db3a2'), font:{size: cssFz('--fz-chart',13) + 0.5}, autoSkip: true, maxRotation: 0}},
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
// 启动时检查数据是否落在软件目录内(更新软件会删数据) → 亮红点 + 提示一次
try { FundUI.checkDataSafety(); } catch (e) { /* 启动检查失败不影响使用 */ }
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
