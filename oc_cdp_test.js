// OpenCalc v49 UI E2E: 填参数→计算→阶梯→保存方案→刷新→调出方案 (13 项断言)
// 运行前置:
//   1. 软件服务运行在 8765, ⚠⚠ 必须用测试数据目录启动, 否则会往真实记录里写测试数据:
//        OC_DATA_DIR=<临时目录> python main.py      (或 OC_DATA_DIR=<临时目录> dist/OpenCalc.exe)
//      脚本会自动检查: 服务未用 OC_DATA_DIR 锁定时拒绝运行(除非设 OC_E2E_FORCE=1)
//   2. Chrome headless 调试端口 9222(可用 OC_CDP_PORT 改):
//      "C:\...\chrome.exe" --headless=new --disable-gpu --no-first-run \
//        --remote-debugging-port=9222 --user-data-dir=<临时目录> about:blank
//      ⚠ 上一轮被强杀会留下卡死的会话 → 换一个新端口 + 新 profile 目录重起最快
//   3. node oc_cdp_test.js   (退出码 0=全过; 1=有断言失败; 2=脚本错误)
const BASE = process.env.OC_E2E_BASE || 'http://127.0.0.1:8765';   // 测试时可指向别的端口
// CDP 端口也可改(OC_CDP_PORT): 上一轮 E2E 被强杀会把旧会话卡死, 换个端口起新 Chrome 最省事
const CDP_PORT = process.env.OC_CDP_PORT || '9222';
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ⚠ 数据安全闸门: E2E 会写入/删除交易记录, 绝不能跑在真实数据上.
//   服务由 OC_DATA_DIR 启动时 /api/funds/data-info 会返回 env_locked=true.
async function guardRealData() {
  if (process.env.OC_E2E_FORCE === '1') {
    console.log('⚠ 已用 OC_E2E_FORCE=1 跳过数据安全检查, 本次会写入当前数据目录');
    return;
  }
  let info = null;
  try {
    info = await (await fetch(BASE + '/api/funds/data-info')).json();
  } catch (e) {
    console.log('无法读取 /api/funds/data-info (服务未启动?), 跳过安全检查:', e.message);
    return;
  }
  if (!info || !info.env_locked) {
    console.error('');
    console.error('❌ 拒绝运行: 服务没有使用测试数据目录。');
    console.error('   当前数据目录:', (info && info.data_dir) || '(未知)');
    console.error('   记录数:', (info && info.record_count) || 0);
    console.error('');
    console.error('   本脚本会新建/删除交易记录, 跑在真实数据上会污染或清空你的记录。');
    console.error('   请改用测试目录启动服务:');
    console.error('     OC_DATA_DIR=<临时目录> python main.py');
    console.error('   确实要在当前目录上跑(自担风险): 设 OC_E2E_FORCE=1 重跑。');
    console.error('');
    process.exit(2);
  }
  console.log('✅ 数据安全检查通过(测试数据目录:', info.data_dir + ')');
}

async function getWsUrl() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch('http://127.0.0.1:' + CDP_PORT + '/json/list')).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch (e) {}
    await sleep(500);
  }
  throw new Error('CDP page not ready');
}

let msgId = 0;
const pending = new Map();
function send(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evalJs(ws, expr) {
  const m = await send(ws, 'Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (m.error) throw new Error('CDP:' + JSON.stringify(m.error));
  const rr = m.result || {};
  if (rr.exceptionDetails) throw new Error('JS: ' + JSON.stringify(rr.exceptionDetails.exception || rr.exceptionDetails.text));
  const rv = rr.result || {};
  return rv.value;
}

const results = [];
function check(name, ok, extra = '') {
  results.push({ name, ok, extra });
  console.log((ok ? '✅' : '❌') + ' ' + name + (extra ? '  [' + extra + ']' : ''));
}

async function main() {
  await guardRealData();          // ⚠ 先确认不是跑在真实数据上, 再动任何记录
  const ws = new WebSocket(await getWsUrl());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m); pending.delete(m.id); }
  };

  await send(ws, 'Page.enable');
  // 固定视口宽度: 分页面/字号切换器/详情布局断言依赖视口 ≥1280px, 不设会随 Chrome 窗口宽度漂移
  await send(ws, 'Emulation.setDeviceMetricsOverride', {
    width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false
  });
  await send(ws, 'Runtime.enable');
  await send(ws, 'Page.navigate', { url: BASE + '/' });
  await sleep(2500);   // 等导航提交完成再 evaluate

  // 等合约加载 + 页面就绪 (CONTRACTS 为顶层 let, 需在同全局词法环境用裸名访问)
  let ready = false;
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    const ok = await evalJs(ws, `!!(typeof CONTRACTS !== 'undefined' && CONTRACTS.length && document.getElementById('equity'))`);
    if (ok) { ready = true; break; }
  }
  check('页面加载 + 合约表就绪', ready);

  // ⚠ v50.77: E2E 常复用同一个 Chrome profile 重跑 → 上一轮写入的「统计区间 / 统计卡显隐」偏好会带过来,
  //   会让后面「统计卡(v50.53)」等按固定口径写的断言假失败。这里统一复位为默认(全部 + 按开仓时间 + 显示)。
  await evalJs(ws, `(() => {
    try {
      localStorage.removeItem('oc-trade-range');
      localStorage.removeItem('oc-trade-range-basis');
      localStorage.removeItem('oc-trade-stats');
    } catch (e) {}
    if (typeof TradeUI !== 'undefined' && TradeUI.applyRangePref) {
      TradeUI.applyRangePref();
      TradeUI.applyStatsPref();
    }
    return true;
  })()`);

  // ---- 场景A: 填表计算, 阶梯止盈应显示 ----
  await evalJs(ws, `
    (() => {
      const eq = document.getElementById('equity');
      eq.value = '9';
      eq.dispatchEvent(new Event('input'));
      const rb = CONTRACTS.find(x => String(x.code).toLowerCase() === 'rb');
      selCode.F = rb.code;          // 选择状态(搜索点击路径内部设置, 直接赋值模拟)
      pickContract(rb, 'F');
      document.getElementById('entry').value = '3500';
      document.getElementById('stop').value = '3450';
      document.getElementById('target').value = '3650';
      document.getElementById('marginRate').value = '16';
      document.getElementById('riskAmount').value = '1';
      onInput();
    })()
  `);
  await sleep(1200);
  const budget = await evalJs(ws, `document.getElementById('rBudgetF').textContent`);
  const ladderHtml = await evalJs(ws, `document.getElementById('rLadderGridF').innerHTML`);
  const emptyVis = await evalJs(ws, `document.getElementById('empty').classList.contains('hidden')`);
  check('测算结果卡显示(非空提示)', emptyVis === true);
  check('预算金额显示', /¥/.test(budget || ''), budget);
  check('阶梯 4 档渲染(2R~5R)', /2R[\s\S]*3R[\s\S]*4R[\s\S]*5R/.test(ladderHtml || ''), (ladderHtml || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 120));
  const ladderTxt = (ladderHtml || '').replace(/<[^>]+>/g, '|').replace(/\|+/g, '|');
  check('阶梯含 2R 价格 3600', ladderTxt.includes('3600'), ladderTxt);
  check('阶梯含每手浮盈', /每手浮盈/.test(ladderHtml || ''));

  // ---- 保存方案 ----
  await evalJs(ws, `document.getElementById('btnSavePlan').click()`);
  await sleep(400);
  const plans = await evalJs(ws, `localStorage.getItem('oc_futures_plans')`);
  const plansParsed = JSON.parse(plans || '[]');
  check('方案已保存到 localStorage', Array.isArray(plansParsed) && plansParsed.length === 1, plans);
  check('方案名=品种+方向+开仓价', /螺纹钢|rb/.test(plans) && /long/.test(plans) && /3500/.test(plans), plans);

  // ---- 场景B: 重新打开(刷新)后点调出, 结果应显示 ----
  await send(ws, 'Page.reload');
  await sleep(2500);
  const loaded = await evalJs(ws, `!!(typeof CONTRACTS !== 'undefined' && CONTRACTS.length && document.getElementById('equity'))`);
  check('刷新后页面就绪', loaded === true);
  const itemCount = await evalJs(ws, `document.querySelectorAll('#planList .plans-item').length`);
  check('最近方案平铺出现', itemCount === 1, 'item=' + itemCount);
  // 点第一个方案的「调出」
  await evalJs(ws, `document.querySelector('#planList .plans-item').click()`);
  await sleep(1500);
  const emptyHidden2 = await evalJs(ws, `document.getElementById('empty').classList.contains('hidden')`);
  const budget2 = await evalJs(ws, `document.getElementById('rBudgetF').textContent`);
  const resultVisible = await evalJs(ws, `!document.getElementById('resultF').classList.contains('hidden')`);
  check('调出后 resultF 显示(核心bug1)', resultVisible === true && emptyHidden2 === true, 'budget=' + budget2);
  check('调出后预算重算', /¥/.test(budget2 || ''), budget2);
  const ladder2 = await evalJs(ws, `document.getElementById('rLadderGridF').innerHTML`);
  const ladder2Txt = (ladder2 || '').replace(/<[^>]+>/g, '|').replace(/\|+/g, '|');
  check('调出后阶梯止盈仍显示价格(核心bug2)', ladder2Txt.includes('3600') && /2R/.test(ladder2 || ''), ladder2Txt);

  // ---- 场景C: 切换品种 → 开仓/止损/止盈价自动清空 ----
  // 当前状态: rb 已选中(方案调出), 价格 3500/3450/3650。通过搜索输入 + Enter 走真实 pick() 路径
  await evalJs(ws, `
    (() => {
      const inp = document.getElementById('cSearch');
      inp.value = 'cu';
      inp.dispatchEvent(new Event('input'));          // 渲染列表(清 selCode.F)
      inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));  // pick(items[0]) = cu
    })()
  `);
  await sleep(600);
  const st = await evalJs(ws, `JSON.stringify({
    sel: selCode.F,
    entry: document.getElementById('entry').value,
    stop: document.getElementById('stop').value,
    target: document.getElementById('target').value,
    last: lastPicked.F
  })`);
  const stO = JSON.parse(st);
  check('切到 cu(selCode.F=cu)', String(stO.sel).toLowerCase() === 'cu', stO.sel);
  check('切换品种→开仓价自动清空', stO.entry === '', 'entry=' + stO.entry);
  check('切换品种→止损价自动清空', stO.stop === '', 'stop=' + stO.stop);
  check('切换品种→止盈价自动清空', stO.target === '', 'target=' + stO.target);

  // ---- 场景D: 同一品种重新选择 → 不清空(避免误伤已填参数) ----
  await evalJs(ws, `
    (() => {
      document.getElementById('entry').value = '70000';
      document.getElementById('stop').value = '69900';
      document.getElementById('target').value = '70600';
      onInput();
      const inp = document.getElementById('cSearch');
      inp.value = 'cu';
      inp.dispatchEvent(new Event('input'));
      inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));  // 再次选 cu
    })()
  `);
  await sleep(600);
  const st2 = await evalJs(ws, `JSON.stringify({entry: document.getElementById('entry').value,
    stop: document.getElementById('stop').value, target: document.getElementById('target').value})`);
  const st2O = JSON.parse(st2);
  check('同品种重选→价格保留', st2O.entry === '70000' && st2O.stop === '69900' && st2O.target === '70600', JSON.stringify(st2O));

  // ---- 场景E: 期权模式切换品种 → 开仓价(每手权利金)自动清空 ----
  await evalJs(ws, `setMode('options')`);
  await sleep(300);
  await evalJs(ws, `
    (() => {
      const inp = document.getElementById('cSearchO');
      inp.value = 'si';
      inp.dispatchEvent(new Event('input'));
      inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));  // 首选 si
      document.getElementById('entryO').value = '800';
      onInput();
      inp.value = 'cu';
      inp.dispatchEvent(new Event('input'));
      inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));  // 切到 cu → 应清空
    })()
  `);
  await sleep(600);
  const entryO = await evalJs(ws, `document.getElementById('entryO').value`);
  check('期权切换品种→每手权利金自动清空', entryO === '', 'entryO=' + entryO);

  // ---- 场景F: 交易记录模块(abe期权) ----
  // 通过页面内 fetch 直接造数(prompt 弹窗无法 headless 自动化), 再验证 UI 渲染链路
  await evalJs(ws, `(async () => {
    const post = (p) => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)}).then(r=>r.json());
    const clean = await fetch('/api/trades/groups').then(r=>r.json());
    // 清理旧的 e2e 测试数据(underlying=e2e)
    const groups = clean.groups || [];
    for (const g of groups) {
      if (String(g.underlying).startsWith('e2e')) {
        const d = await fetch('/api/trades/detail?underlying=' + g.underlying).then(r=>r.json());
        for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})}).then(r=>r.json());
      }
    }
    await post({underlying:'e2eao611', contract:'e2eao611P2500', op_type:'open', direction:'buy',
      open_date:'2026-08-27', open_delta:0.19, target_delta:0.45, call_put:'P', open_price:700, qty:4, premium:1400});
    return 'seeded';
  })()`);
  await sleep(400);
  // 切到交易记录 tab
  await evalJs(ws, `document.querySelector('#mainTabs .maintab[data-tab="trades"]').click()`);
  await sleep(600);
  const tradesVisible = await evalJs(ws, `!document.getElementById('tradesArea').classList.contains('hidden')`);
  check('交易记录 tab 切换可见', tradesVisible === true);
  const rowCount = await evalJs(ws, `document.querySelectorAll('#tradesTable tbody tr.clickable').length`);
  check('主表渲染新开仓行', rowCount >= 1, 'rows=' + rowCount);
  const mainRow = await evalJs(ws, `JSON.stringify((() => {
    const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2eao611');
    return tr ? tr.innerText : null;
  })())`);
  check('主表行显示未平仓 + 开仓日期', /未平仓/.test(mainRow || '') && /2026-08-27/.test(mainRow || ''), mainRow);
  // 点行 → 右侧详情面板
  await evalJs(ws, `[...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2eao611').click()`);
  await sleep(800);
  const panelVisible = await evalJs(ws, `!document.getElementById('tradeDetailPanel').classList.contains('hidden')`);
  const layoutDetail = await evalJs(ws, `document.querySelector('.trades-layout').classList.contains('has-detail')`);
  check('点行后右侧详情面板展开(不挡主表)', panelVisible === true && layoutDetail === true);
  const holdText = await evalJs(ws, `document.getElementById('tdHoldings').innerText`);
  check('详情当前持仓表出现合约', /e2eao611P2500/.test(holdText || ''), holdText.replace(/\s+/g,' ').slice(0,120));
  check('持仓数量 4 手', /4/.test(holdText || ''), holdText.replace(/\s+/g,' '));
  const opText = await evalJs(ws, `document.getElementById('tdOps').innerText`);
  check('操作记录含开仓行', /开仓/.test(opText || '') && /0.19/.test(opText || ''), opText.replace(/\s+/g,' ').slice(0,160));
  // 平仓 → 主表应自动汇总
  await evalJs(ws, `(async () => {
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2eao611', contract:'e2eao611P2500', op_type:'close', direction:'buy',
      close_date:'2026-09-03', open_date:'2026-09-03', close_qty:4, close_price:510, pnl:-760, qty:4, premium:0
    })}).then(r=>r.json());
    return 'closed';
  })()`);
  await evalJs(ws, `TradeUI.refresh()`);
  await sleep(800);
  const rowAfter = await evalJs(ws, `JSON.stringify((() => {
    const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2eao611');
    return tr ? tr.innerText : null;
  })())`);
  check('全平后主表显示已平仓 + 盈亏-760 + 平仓时间', /已平仓/.test(rowAfter || '') && /760/.test(rowAfter || '') && /2026-09-03/.test(rowAfter || ''), rowAfter);
  // 只展示未平仓过滤
  await evalJs(ws, `document.getElementById('tradesOnlyOpen').click()`);
  await sleep(400);
  const filteredRow = await evalJs(ws, `[...document.querySelectorAll('#tradesTable tbody tr.clickable')].some(x => x.dataset.u === 'e2eao611')`);
  check('只展示未平仓 → 已平仓行隐藏', filteredRow === false);
  await evalJs(ws, `document.getElementById('tradesOnlyOpen').click()`);
  await sleep(300);
  // 监控池快照
  await evalJs(ws, `fetch('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({contracts:['si','lc','fu','ao','br','lh','fg','hc','sm','cf','jd','cu']})}).then(r=>r.json())`);
  await evalJs(ws, `TradeUI.refresh()`);
  await sleep(500);
  const poolText = await evalJs(ws, `document.getElementById('poolArea').innerText`);
  // ⚠ 快照日期是「今天」, 不能写死日期(写死会在隔天/新库上失败) → 动态取当天
  const _d = new Date();
  const _today = _d.getFullYear() + '-' + String(_d.getMonth() + 1).padStart(2, '0') + '-' + String(_d.getDate()).padStart(2, '0');
  check('监控池快照展示品种', /si/.test(poolText || '') && /cf/.test(poolText || '') && poolText.includes(_today), poolText.replace(/\s+/g,' ').slice(0,120));
  // 侧边栏导入导出按钮存在
  const sideBtns = await evalJs(ws, `JSON.stringify({ex: !!document.getElementById('btnExport'), im: !!document.getElementById('btnImport'), tabs: document.querySelectorAll('#mainTabs .maintab').length})`);
  const sb = JSON.parse(sideBtns);
  check('侧边栏导出/导入按钮 + 5个tab(计算器/期权买方/期货/期权双买/资金曲线)', sb.ex === true && sb.im === true && sb.tabs === 5, sideBtns);
  // 箭头方向: 导出 ⬆(出去) / 导入 ⬇(进来)
  const arrowDir = await evalJs(ws, `JSON.stringify({ex: document.getElementById('btnExport').textContent.trim(), im: document.getElementById('btnImport').textContent.trim()})`);
  const ad = JSON.parse(arrowDir);
  check('箭头方向 导出⬆ / 导入⬇', ad.ex === '⬆' && ad.im === '⬇', arrowDir);

  // ---- 场景L: 字号三档切换 + 分页面加宽 ----
  // 1. 顶部字号切换存在且默认中(md active)
  const fzSeg = await evalJs(ws, `JSON.stringify({
    seg: !!document.getElementById('fontSeg'),
    btns: [...document.querySelectorAll('#fontSeg button')].map(b=>b.dataset.fz + ':' + b.classList.contains('active')),
    body: document.body.className
  })`);
  const fs1 = JSON.parse(fzSeg);
  check('字号切换器在顶部(小/中/大)', fs1.seg && fs1.btns.length === 3, fzSeg);
  check('默认中号(md active)', fs1.btns.includes('md:true') && /fz-md/.test(fs1.body), fzSeg);
  // 2. 切大号 → body class 变化 + 表格字号实际变大
  const fzGrow = await evalJs(ws, `(async () => {
    const before = getComputedStyle(document.querySelector('#tradesTable td')).fontSize;
    document.querySelector('#fontSeg button[data-fz="lg"]').click();
    await new Promise(r=>setTimeout(r,300));
    const after = getComputedStyle(document.querySelector('#tradesTable td')).fontSize;
    const cls = document.body.className;
    document.querySelector('#fontSeg button[data-fz="md"]').click();
    await new Promise(r=>setTimeout(r,300));
    return JSON.stringify({before, after, cls, back: getComputedStyle(document.querySelector('#tradesTable td')).fontSize});
  })()`);
  const fg = JSON.parse(fzGrow);
  check('切大号后表格字号变大(大>中)', parseFloat(fg.after) > parseFloat(fg.before) && /fz-lg/.test(fg.cls), fzGrow);
  check('切回中号恢复', Math.abs(parseFloat(fg.back) - parseFloat(fg.before)) < 0.01, fzGrow);
  // 3. 分页面宽度 ≥ 1100(加宽消除横滑)
  await evalJs(ws, `[...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2eavg')?.click()`);
  await sleep(600);
  const sideW = await evalJs(ws, `Math.round(document.querySelector('.trades-side').getBoundingClientRect().width)`);
  check('分页面加宽 ≥1100px(表格无滑块)', sideW >= 1100, 'w=' + sideW);
  await evalJs(ws, `document.querySelector('#tdClose')?.click()`);
  await sleep(300);

  // ---- 场景M: 字号切换器挪到右侧 + 监控池字号 + 查看历史快照 ----
  // 1. 字号切换器现在在 topbtns 内(顶栏右侧), 不在 brand 前
  const fzPos = await evalJs(ws, `JSON.stringify((() => {
    const fz = document.getElementById('fontSeg');
    const tb = document.querySelector('.topbtns');
    return {
      inTopbtns: !!(fz && tb && tb.contains(fz)),
      rect: { l: fz.getBoundingClientRect().left, vw: window.innerWidth }
    }
  })())`);
  const fp = JSON.parse(fzPos);
  check('字号切换器在顶栏右侧(topbtns 内)', fp.inTopbtns && fp.rect.l > fp.rect.vw / 2, fzPos);

  // 2. 创建监控池数据, 验证「查看历史快照」按钮能展开
  const poolToggle = await evalJs(ws, `(async () => {
    const cleanup = async (u) => {
      const d = await fetch('/api/trades/pool').then(r=>r.json());
      for (const p of (d.pools||[])) await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: p.id})}).then(r=>r.json());
    };
    await cleanup();
    await fetch('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({snapshot_date:'2026-09-09', contracts:['si','lc','fu']})}).then(r=>r.json());
    await fetch('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({snapshot_date:'2026-09-08', contracts:['ao','br','cu','al']})}).then(r=>r.json());
    await fetch('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({snapshot_date:'2026-09-07', contracts:['rb','hc','i']})}).then(r=>r.json());
    return 'seeded';
  })()`);
  await sleep(300);
  await evalJs(ws, `document.querySelector('#mainTabs .maintab[data-tab="trades"]').click()`);
  await sleep(500);
  // 切档到大号 → 切回中号 → 查监控池字号变化
  const poolFs = await evalJs(ws, `(async () => {
    document.querySelector('#fontSeg button[data-fz="md"]').click();
    await new Promise(r=>setTimeout(r,200));
    const mdTitle = getComputedStyle(document.querySelector('.pool-title')).fontSize;
    document.querySelector('#fontSeg button[data-fz="lg"]').click();
    await new Promise(r=>setTimeout(r,200));
    const lgTitle = getComputedStyle(document.querySelector('.pool-title')).fontSize;
    document.querySelector('#fontSeg button[data-fz="md"]').click();
    await new Promise(r=>setTimeout(r,200));
    return JSON.stringify({mdTitle, lgTitle});
  })()`);
  const pf = JSON.parse(poolFs);
  check('监控池标题字号随档位变化(lg > md)', parseFloat(pf.lgTitle) > parseFloat(pf.mdTitle), JSON.stringify(pf));

  // 3. 验证「查看历史快照」按钮: 点击前最新快照展开/历史收起 → 点击后全部展开 → 再点击收起
  const histToggle = await evalJs(ws, `(async () => {
    const before = document.querySelectorAll('.pool-content').length;
    const btnTxt0 = document.getElementById('btnPoolHistory').textContent;
    document.getElementById('btnPoolHistory').click();
    await new Promise(r=>setTimeout(r,300));
    const afterExpand = document.querySelectorAll('.pool-content').length;
    const btnTxt1 = document.getElementById('btnPoolHistory').textContent;
    document.getElementById('btnPoolHistory').click();
    await new Promise(r=>setTimeout(r,300));
    const afterCollapse = document.querySelectorAll('.pool-content').length;
    const btnTxt2 = document.getElementById('btnPoolHistory').textContent;
    return JSON.stringify({before, afterExpand, afterCollapse, btnTxt0, btnTxt1, btnTxt2});
  })()`);
  const ht = JSON.parse(histToggle);
  check('点击「查看历史快照」展开所有', ht.afterExpand > ht.before, JSON.stringify(ht));
  check('再点击收起', ht.afterCollapse < ht.afterExpand, JSON.stringify(ht));
  check('按钮文字随状态切换(查看/收起)', /收起/.test(ht.btnTxt1) && /查看/.test(ht.btnTxt2), JSON.stringify(ht));

  // ---- 场景G: UI 结构打磨 (v50.1) ----
  // 先关掉场景 F 留下的详情面板, 保证干净的 has-detail 检测
  await evalJs(ws, `document.querySelector('#tdClose')?.click()`);
  await sleep(400);
  // 1. 主页面 toolbar 只保留「新建开仓」(无「新建平仓」)
  const mainToolbar = await evalJs(ws, `JSON.stringify({newOpen: !!document.getElementById('btnNewOpen'), newClose: !!document.getElementById('btnNewClose'), onlyOpen: !!document.getElementById('tradesOnlyOpen'), showAll: !!document.getElementById('btnShowAllTrades')})`);
  const mt = JSON.parse(mainToolbar);
  check('交易记录页含「只展示未平仓」+「新建开仓」+「显示全部」', mt.onlyOpen && mt.newOpen && mt.showAll, mainToolbar);
  check('主页面 toolbar 移除「新建平仓」按钮', mt.newClose === false, mainToolbar);
  // 2. 详情面板 tdNewOpen + tdNewClose 都存在
  const sideBtns2 = await evalJs(ws, `JSON.stringify({tdNewOpen: !!document.getElementById('tdNewOpen'), tdNewClose: !!document.getElementById('tdNewClose')})`);
  const sb2 = JSON.parse(sideBtns2);
  check('分页面含「新建开仓」+「新建平仓」按钮', sb2.tdNewOpen && sb2.tdNewClose, sideBtns2);
  // 3. 详情布局: 无 detail 时是 block, 有 detail 时主表不变 + 分页面 fixed 浮在右侧(由场景 H 第 7 项验证)
  const layoutInfoG = await evalJs(ws, `JSON.stringify((() => {
    const layout = document.querySelector('.trades-layout');
    const has = document.querySelector('.trades-layout.has-detail');
    if (!layout) return null;
    return {display: getComputedStyle(layout).display, hasDetail: !!has};
  })())`);
  const lig = JSON.parse(layoutInfoG);
  check('交易记录页布局(无详情时 block, 分页面隐藏)', lig && lig.display === 'block' && !lig.hasDetail, layoutInfoG);
  // 4. tradesArea 内无重复标题(全局 header 由 appTitle 驱动)
  const tradeTitles = await evalJs(ws, `[...document.querySelectorAll('#tradesArea h1')].length`);
  check('tradesArea 内无重复 h1(标题仅全局 header 一个)', tradeTitles === 0, 'count=' + tradeTitles);
  // 5. 侧边栏 side-extras 含 5 个图标按钮(导出/导入/数据位置/检查更新/联系作者)
  const sideBtnsCount = await evalJs(ws, `document.querySelectorAll('.side-extras .side-btn').length`);
  check('侧边栏底部 5 个图标按钮(导出/导入/数据位置/检查更新/联系作者)', sideBtnsCount === 5, 'count=' + sideBtnsCount);
  const sideBtnIds = await evalJs(ws, `[...document.querySelectorAll('.side-extras .side-btn')].map(b=>b.id).join(',')`);
  check('侧边栏按钮 id 顺序 = btnExport/btnImport/btnDataDir/btnUpdate/btnContact',
        sideBtnIds === 'btnExport,btnImport,btnDataDir,btnUpdate,btnContact', sideBtnIds);
  // 6. 资金曲线页面移除导入/导出/数据位置按钮(只保留「清除全部」, 用 fundsArea 内 querySelector 避免侧边栏 id 干扰)
  await evalJs(ws, `document.querySelector('#mainTabs .maintab[data-tab="funds"]').click()`);
  await sleep(600);
  const fundsBtns = await evalJs(ws, `JSON.stringify({dataDir: !!document.getElementById('fundsArea').querySelector('#btnDataDir'), export: !!document.getElementById('fundsArea').querySelector('#btnExport'), import: !!document.getElementById('fundsArea').querySelector('#btnImport'), clearAll: !!document.getElementById('fundsArea').querySelector('#btnClearAll')})`);
  const fb = JSON.parse(fundsBtns);
  check('资金曲线页移除 导入/导出/数据位置', fb.dataDir === false && fb.export === false && fb.import === false, fundsBtns);
  check('资金曲线页保留「清除全部」', fb.clearAll === true, fundsBtns);

  // ---- 场景H: v50.3 主页面右推 + 操作列 ✎🗑 + 详情 header 三按钮右上 + grid 同时显示 + 移除策略自定义 ----
  await evalJs(ws, `document.querySelector('#mainTabs .maintab[data-tab="trades"]').click()`);
  await sleep(500);
  // 1. 主页面 toolbar 「新建开仓」在最右(右推)
  const mainToolbarOrder = await evalJs(ws, `JSON.stringify((() => {
    const tb = document.querySelector('.trades-toolbar');
    return [...tb.children].map(c => c.id || c.tagName);
  })())`);
  const mto = JSON.parse(mainToolbarOrder);
  check('主页面 toolbar「新建开仓」在最右(右推)', mto[mto.length - 1] === 'btnNewOpen', mainToolbarOrder);
  // 1b. 主页面工具栏按钮配色: btnNewOpen 红(rose), 分页面 tdNewOpen 红/tdNewClose 青
  const btnColors = await evalJs(ws, `JSON.stringify({
    mainOpen: document.getElementById('btnNewOpen').className,
    tdOpen: document.getElementById('tdNewOpen').className,
    tdClose: document.getElementById('tdNewClose').className,
    chkNowrap: getComputedStyle(document.querySelector('.trades-toolbar .chk')).whiteSpace
  })`);
  const bc = JSON.parse(btnColors);
  check('主页面/分页面按钮配色(开仓红/平仓青)', /rose/.test(bc.mainOpen) && /rose/.test(bc.tdOpen) && /cyan/.test(bc.tdClose), btnColors);
  check('「只展示未平仓」不换行', bc.chkNowrap === 'nowrap', btnColors);
  // 2. 策略名输入字段已移除
  const stratGone = await evalJs(ws, `!!document.getElementById('tmStrategy')`);
  check('策略名输入字段已移除', stratGone === false);
  // 3. 清理 e2e 测试数据
  await evalJs(ws, `(async () => {
    const gr = await fetch('/api/trades/groups').then(r=>r.json());
    for (const g of (gr.groups||[])) {
      if (String(g.underlying).startsWith('e2e')) {
        const d = await fetch('/api/trades/detail?underlying=' + g.underlying).then(r=>r.json());
        for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})}).then(r=>r.json());
      }
    }
  })()`);
  await sleep(400);
  // 4. 传 strategy=自定义 仍被忽略(后端固定 abe)
  const customIgnored = await evalJs(ws, `(async () => {
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      strategy:'自定义忽略', underlying:'e2e3jd100', contract:'e2e3jd100P2000',
      op_type:'open', direction:'buy', open_date:'2026-08-01',
      open_delta:0.25, call_put:'P', open_price:80, qty:2, premium:160
    })}).then(r=>r.json());
    const d = await fetch('/api/trades/detail?underlying=e2e3jd100').then(r=>r.json());
    return d.operations ? d.operations[0].strategy : null;
  })()`);
  check('后端固定「期权买方」(忽略传入 strategy)', customIgnored === '期权买方', 'got=' + customIgnored);
  // 5. 平仓数量超额仍被拒绝
  const overClose = await evalJs(ws, `(async () => {
    const r = await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2e3jd100', contract:'e2e3jd100P2000',
      op_type:'close', direction:'buy', close_date:'2026-08-15', open_date:'2026-08-15',
      close_qty:5, qty:5, premium:0
    })}).then(x=>x.json());
    return r;
  })()`);
  check('平仓数量超额被后端拒绝', overClose && overClose.ok === false && /超过剩余可平/.test(overClose.error || ''), JSON.stringify(overClose));
  // 6. 主表行操作列改 ✎/🗑(刷新后取最新数据)
  await evalJs(ws, `TradeUI.refresh()`);
  await sleep(500);
  const rowActs = await evalJs(ws, `JSON.stringify((() => {
    const all = [...document.querySelectorAll('#tradesTable tbody tr.clickable')].map(tr => {
      const btns = [...tr.querySelectorAll('[data-act]')].map(b => b.dataset.act + ':' + b.textContent.trim());
      return {u: tr.dataset.u, btns};
    });
    return {all: all.map(a=>a.u), e2e: all.filter(a => a.u.startsWith('e2e'))};
  })())`);
  const raObj = JSON.parse(rowActs);
  check('主表行操作列含 edit(✎) 和 del(🗑)', raObj.e2e.length >= 1 && raObj.e2e[0].btns.some(b => b.startsWith('edit:')) && raObj.e2e[0].btns.some(b => b.startsWith('del:')), JSON.stringify(raObj));
  // 7. 详情布局: 主表保持原宽(不被挤压), 分页面 fixed 浮在右侧
  // 先关掉 detail 取主表无 detail 时的宽度, 再开 detail 后对比(应近似相等 → "不变")
  await evalJs(ws, `document.querySelector('#tdClose')?.click()`);
  await sleep(400);
  const mainWidthNoDetail = await evalJs(ws, `Math.round(document.querySelector('.trades-main').getBoundingClientRect().width)`);
  await evalJs(ws, `[...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2e3jd100')?.click()`);
  await sleep(700);
  const layoutInfo = await evalJs(ws, `JSON.stringify((() => {
    const layout = document.querySelector('.trades-layout.has-detail');
    const main = document.querySelector('.trades-main');
    const side = document.querySelector('.trades-side');
    if (!layout) return null;
    const sideCS = getComputedStyle(side);
    return {
      sidePosition: sideCS.position,
      sideWidth: Math.round(side.getBoundingClientRect().width),
      mainWidth: Math.round(main.getBoundingClientRect().width),
      sideVisible: side.getBoundingClientRect().width > 100,
    };
  })())`);
  const li = JSON.parse(layoutInfo);
  check('分页面 fixed 浮在右侧(不挤压主表)', li && li.sidePosition === 'fixed' && li.sideVisible && li.sideWidth >= 900, layoutInfo);
  check(`主表宽度保持不变(无详情 ${mainWidthNoDetail}px ≈ 有详情 ${li.mainWidth}px)`, li && Math.abs(li.mainWidth - mainWidthNoDetail) < 5, layoutInfo);
  // 操作记录合约筛选下拉存在且选项正确
  const filterInfo = await evalJs(ws, `JSON.stringify((() => {
    const sel = document.getElementById('tdContractFilter');
    const opts = [...sel.options].map(o => o.value);
    return {count: opts.length, first: opts[0] || '', hasContract: opts.some(v => v.startsWith('e2e3jd100'))};
  })())`);
  const fi = JSON.parse(filterInfo);
  check('操作记录合约筛选下拉(全部 + 合约选项)', fi.count >= 2 && fi.first === '' && fi.hasContract, filterInfo);
  // 8. 详情面板 header: 左标题 + 右三按钮(新建开仓/新建平仓/关闭)
  const headerStructure = await evalJs(ws, `JSON.stringify((() => {
    const head = document.querySelector('#tradeDetailPanel .td-head');
    if (!head) return null;
    const h2 = head.querySelector('h2');
    const acts = [...head.querySelectorAll('.td-head-actions button')].map(b => b.id);
    return {hasH2: !!h2, actions: acts};
  })())`);
  const hs = JSON.parse(headerStructure);
  check('详情面板 header: 左标题 + 右三按钮(新建开仓/新建平仓/关闭)', hs && hs.hasH2 && hs.actions.includes('tdNewOpen') && hs.actions.includes('tdNewClose') && hs.actions.includes('tdClose'), headerStructure);
  // 9. modal 输入框等宽对齐(同列 input 宽度一致)
  await evalJs(ws, `TradeUI.openEditModal('open', {underlying: 'e2e3jd100'})`);
  await sleep(300);
  const modalCols = await evalJs(ws, `JSON.stringify((() => {
    const grid = document.querySelector('#tradeModalBg .formgrid');
    if (!grid) return null;
    // 跳过 display:none 的 label(如 tmOpTypeWrap 隐藏容器)
    const labels = [...grid.querySelectorAll(':scope > label')].filter(l => getComputedStyle(l).display !== 'none');
    // 取第一行两个 label(开仓标的 + 合约代码)
    const widths = labels.slice(0, 2).map(l => Math.round(l.getBoundingClientRect().width));
    const ok = widths.length === 2 && Math.abs(widths[0] - widths[1]) < 2;
    return {widths, ok, all: labels.length};
  })())`);
  const mc = JSON.parse(modalCols);
  check('modal 输入框标签同列等宽', mc && mc.ok, JSON.stringify(mc));
  await evalJs(ws, `document.getElementById('tmCancel').click()`);

  // ---- 场景I: 开仓合约自动推断P/C + 平仓方向/call_put联动 + 平仓行权利金/ ----
  // 1. 开仓 modal: 输入合约代码自动推断看涨看跌(带 P→看跌, 带 C→看涨)
  await evalJs(ws, `TradeUI.openEditModal('open', {underlying: 'e2e3jd100'})`);
  await sleep(300);
  const cpInfer = await evalJs(ws, `(async () => {
    const inp = document.getElementById('tmContract');
    inp.value = 'ao611P2500';
    inp.dispatchEvent(new Event('input'));
    const pVal = document.getElementById('tmCallPut').value;
    inp.value = 'fu2611C3000';
    inp.dispatchEvent(new Event('input'));
    const cVal = document.getElementById('tmCallPut').value;
    document.getElementById('tmCancel').click();
    return JSON.stringify({pVal, cVal});
  })()`);
  const cpi = JSON.parse(cpInfer);
  check('开仓合约带 P → 默认看跌', cpi.pVal === 'P', cpInfer);
  check('开仓合约带 C → 默认看涨', cpi.cVal === 'C', cpInfer);
  // 2. 平仓 modal: 看涨看跌与原合约一致 + 方向取反(卖出开仓→买入平仓)
  //    先造一个卖出开仓 e2e4fu
  await evalJs(ws, `(async () => {
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2e4fu', contract:'e2e4fuC3000', op_type:'open', direction:'sell',
      open_date:'2026-08-10', call_put:'C', open_price:100, qty:3, premium:300
    })}).then(r=>r.json());
  })()`);
  await evalJs(ws, `TradeUI.refresh()`);
  await sleep(500);
  await evalJs(ws, `[...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === 'e2e4fu')?.click()`);
  await sleep(600);
  const closeSync = await evalJs(ws, `(async () => {
    document.getElementById('tdNewClose').click();
    await new Promise(r=>setTimeout(r,300));
    const callPut = document.getElementById('tmCallPut').value;
    const direction = document.getElementById('tmDirection').value;
    const dirDisabled = document.getElementById('tmDirection').disabled;
    const cpDisabled = document.getElementById('tmCallPut').disabled;
    document.getElementById('tmCancel').click();
    return JSON.stringify({callPut, direction, dirDisabled, cpDisabled});
  })()`);
  const cs = JSON.parse(closeSync);
  check('平仓: 看涨看跌自动与原合约一致(C)', cs.callPut === 'C', closeSync);
  check('平仓: 卖出开仓 → 方向自动为买入(buy)', cs.direction === 'buy' && cs.dirDisabled, closeSync);
  // 3. 平仓后操作记录行: 权利金显示 / ; 看涨看跌与原合约一致
  await evalJs(ws, `(async () => {
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2e4fu', contract:'e2e4fuC3000', op_type:'close', direction:'buy',
      close_date:'2026-08-20', open_date:'2026-08-20', close_qty:3, qty:3,
      close_price:90, pnl:-30, premium:0, call_put:'C'
    })}).then(r=>r.json());
  })()`);
  await evalJs(ws, `TradeUI.refresh()`);
  await sleep(600);
  const closeRow = await evalJs(ws, `JSON.stringify((() => {
    const rows = [...document.querySelectorAll('#tdOps tr')];
    // 操作=平仓的行(排除状态"已平仓"的开仓行): 文本含"平仓"且不含"开仓"
    const r = rows.find(x => x.innerText.includes('平仓') && !x.innerText.includes('开仓'));
    return r ? {txt: r.innerText.replace(/\\s+/g, ' ')} : null;
  })())`);
  const cr = JSON.parse(closeRow);
  check('平仓操作行显示权利金 /', cr && cr.txt.includes('/'), cr ? cr.txt : 'null');
  check('平仓操作行看涨看跌与原合约一致(看涨)', cr && /看涨/.test(cr.txt), cr ? cr.txt : 'null');

  // ---- 场景J: 大小写不一致仍能正确扣减(后端按合约大小写不敏感) ----
  const caseFix = await evalJs(ws, `(async () => {
    const cleanup = async (u) => {
      const d = await fetch('/api/trades/detail?underlying=' + u).then(r=>r.json());
      for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})}).then(r=>r.json());
    };
    await cleanup('e2ecase');
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2ecase', contract:'E2ECASEP2500', op_type:'open', direction:'buy',
      open_date:'2026-09-01', call_put:'P', open_price:700, qty:5, premium:2500
    })}).then(r=>r.json());
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2ecase', contract:'e2ecasep2500', op_type:'close', direction:'buy',
      close_date:'2026-09-09', open_date:'2026-09-09', close_qty:3, qty:3, premium:0, pnl:-570
    })}).then(r=>r.json());
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2ecase', contract:'e2ecasep2500', op_type:'close', direction:'buy',
      close_date:'2026-09-09', open_date:'2026-09-09', close_qty:2, qty:2, premium:0, pnl:-200
    })}).then(r=>r.json());
    const det = await fetch('/api/trades/detail?underlying=e2ecase').then(r=>r.json());
    const grp = (await fetch('/api/trades/groups').then(r=>r.json())).groups.find(g => g.underlying === 'e2ecase');
    return JSON.stringify({status: grp ? grp.close_status : null, holdingsLen: det.holdings.length});
  })()`);
  const cf = JSON.parse(caseFix);
  check('合约大小写不一致仍能正确扣减(主表已平仓+持仓空)', cf.status === '已平仓' && cf.holdingsLen === 0, caseFix);

  // ---- 场景K: 持仓均价只算未平仓部分(开5@500→平3→开5@200, 应得7手@285.71) ----
  const avgFix = await evalJs(ws, `(async () => {
    const cleanup = async (u) => {
      const d = await fetch('/api/trades/detail?underlying=' + u).then(r=>r.json());
      for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})}).then(r=>r.json());
    };
    await cleanup('e2eavg');
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2eavg', contract:'e2eavgP2500', op_type:'open', direction:'buy',
      open_date:'2026-09-09', call_put:'P', open_price:500, qty:5, premium:2500
    })}).then(r=>r.json());
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2eavg', contract:'e2eavgP2500', op_type:'close', direction:'sell',
      close_date:'2026-09-09', open_date:'2026-09-09', close_qty:3, qty:3, premium:0, pnl:300
    })}).then(r=>r.json());
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      underlying:'e2eavg', contract:'e2eavgP2500', op_type:'open', direction:'buy',
      open_date:'2026-09-09', call_put:'P', open_price:200, qty:5, premium:1000
    })}).then(r=>r.json());
    const det = await fetch('/api/trades/detail?underlying=e2eavg').then(r=>r.json());
    return JSON.stringify({holdings: det.holdings});
  })()`);
  const af = JSON.parse(avgFix);
  const h0 = af.holdings[0];
  check('持仓均价只算未平仓部分(7手@(2×500+5×200)/7≈285.71)',
    h0 && h0.qty === 7 && Math.abs(h0.open_price - 285.7143) < 0.01 && Math.abs(h0.premium - 2000) < 0.01,
    JSON.stringify(h0));
  const tipText = await evalJs(ws, `document.querySelector('.help-tip[data-tip]')?.dataset.tip || ''`);
  check('「开仓均价 ?」含 tooltip 说明(只算未平仓部分)', /未平仓/.test(tipText), tipText);

  // ---- 场景P: 合约大小写归一化(品种小写 + C/P 大写, 同合约不再分裂成两个) ----
  const normFix = await evalJs(ws, `(async () => {
    const cleanup = async (u) => {
      const d = await fetch('/api/trades/detail?underlying=' + u).then(r=>r.json());
      for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})}).then(r=>r.json());
    };
    await cleanup('e2enorm');
    const post = (b) => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r=>r.json());
    // 开仓用大写 C 写法, 平仓用小写 c 写法
    await post({underlying:'e2enorm', contract:'BR2610C15800', op_type:'open', direction:'buy',
                open_date:'2026-08-01', call_put:'C', open_price:700, qty:4, premium:2800});
    await post({underlying:'e2enorm', contract:'br2610c15800', op_type:'open', direction:'buy',
                open_date:'2026-08-02', call_put:'C', open_price:710, qty:2, premium:1420});
    await post({underlying:'e2enorm', contract:'br2610C15800', op_type:'close', direction:'sell',
                close_date:'2026-09-01', close_qty:4, close_price:800, pnl:2000, call_put:'C'});
    const det = await fetch('/api/trades/detail?underlying=e2enorm').then(r=>r.json());
    return JSON.stringify({
      contracts: [...new Set((det.operations||[]).map(o=>o.contract))],
      opsContracts: [...new Set((det.operations||[]).map(o=>o.contract))],
      holdings: det.holdings,
      normFn: [TradeUI.normalizeContract('br2610c15800'), TradeUI.normalizeContract('AO611P2500'), TradeUI.normalizeContract('p2601C8000')],
      // v50.30: 分隔式(- 隔开) + 半分隔式; 品种前缀自带 c/p 须取最右侧
      sepFn: [TradeUI.normalizeContract('lc2611-C-144000'), TradeUI.normalizeContract('LC2611-c-144000'),
              TradeUI.normalizeContract('lc2611-c144000'), TradeUI.normalizeContract('lc2611_C_144000'),
              TradeUI.normalizeContract('hc2610P6000')],
      sepCp: [TradeUI.detectCallPut('lc2611-C-144000'), TradeUI.detectCallPut('LC2611-P-144000'),
              TradeUI.detectCallPut('hc2610P6000'), TradeUI.detectCallPut('cu2610'),
              TradeUI.detectCallPut('aoc5000')]
    });
  })()`);
  const nf = JSON.parse(normFix);
  check('输入框归一化: 品种小写 + C/P 大写',
    nf.normFn[0] === 'br2610C15800' && nf.normFn[1] === 'ao611P2500' && nf.normFn[2] === 'p2601C8000',
    JSON.stringify(nf.normFn));
  check('大小写不同的合约落库后写法统一(不再分裂)',
    nf.contracts.length === 1 && nf.contracts[0] === 'br2610C15800', JSON.stringify(nf.contracts));
  check('筛选下拉只出现 1 个合约(原 2 个)',
    nf.opsContracts.length === 1 && nf.opsContracts[0] === 'br2610C15800', JSON.stringify(nf.opsContracts));
  check('大小写不同仍能正确匹配平仓(4平2留)',
    nf.holdings.length === 1 && nf.holdings[0].qty === 2 && nf.holdings[0].open_price === 710,
    JSON.stringify(nf.holdings));
  // ---- 场景P2 (v50.30): 分隔式 lc2611-C-144000 也能识别 + 归一化 ----
  check('分隔式 lc2611-C-144000 归一化(保留 - 分段)',
    nf.sepFn[0] === 'lc2611-C-144000' && nf.sepFn[1] === 'lc2611-C-144000', JSON.stringify(nf.sepFn));
  check('半分隔式 lc2611-c144000 归一化', nf.sepFn[2] === 'lc2611-C144000', JSON.stringify(nf.sepFn));
  check('_ 分隔统一成 -', nf.sepFn[3] === 'lc2611-C-144000', JSON.stringify(nf.sepFn));
  check('品种前缀自带 c/p + 真实标志位取最右', nf.sepFn[4] === 'hc2610P6000', JSON.stringify(nf.sepFn));
  check('看涨看跌自动识别: 分隔式 C/P',
    nf.sepCp[0] === 'C' && nf.sepCp[1] === 'P', JSON.stringify(nf.sepCp));
  check('看涨看跌自动识别: 前缀c不误判 / 缺月份不认',
    nf.sepCp[2] === 'P' && nf.sepCp[3] === '' && nf.sepCp[4] === '', JSON.stringify(nf.sepCp));

  // ---- 场景Q (v50.31): 数据安全 — 软件目录内警告 + 自动备份 + 立即备份 ----
  // ⚠ v50.41.1: 测试实例的数据目录**必须在**临时目录(安全铁律), 所以真实 `/api/funds/data-info`
  //   的 risky 恒为 false。早期这里靠「把测试数据放在软件目录内」来触发警告, 与安全铁律直接冲突。
  //   改为对 `fetchT('/api/funds/data-info')` 打桩返回 risky:true 的桩响应, 再调 checkDataSafety()
  //   渲染 —— 验证的是「拿到 risky 数据后 UI 怎么表现」, 不依赖数据真实存放位置。
  const dq = await evalJs(ws, `JSON.stringify({ hasFn: typeof FundUI.checkDataSafety === 'function', hasOpen: typeof FundUI.openDataDir === 'function' })`);
  check('存在数据安全检查方法', JSON.parse(dq).hasFn && JSON.parse(dq).hasOpen, dq);
  await evalJs(ws, `(() => {
    window.__stubInfo = { ok: true, risky: true, data_dir: 'D:\\\\Workbuddy\\\\开仓计算器\\\\e2e-data',
                          app_dir: 'D:\\\\Workbuddy\\\\开仓计算器',
                          backup_dir: 'D:\\\\Workbuddy\\\\开仓计算器\\\\e2e-data\\\\backup',
                          backup_count: 3, record_count: 0 };
    // 两条取数路径都要盖住: checkDataSafety 用 fetchT, openDataDir 用原生 fetch
    window.__realFetch = window.fetch;
    window.fetch = (u, o) => (String(u).indexOf('/api/funds/data-info') >= 0)
      ? Promise.resolve({ ok: true, status: 200, json: async () => window.__stubInfo })
      : window.__realFetch(u, o);
    window.__realFetchT = window.fetchT;
    window.fetchT = (u, o, ms) => (String(u).indexOf('/api/funds/data-info') >= 0)
      ? Promise.resolve({ json: async () => window.__stubInfo })
      : window.__realFetchT(u, o, ms);
    sessionStorage.removeItem('dataRiskClosed');
    return 'ok';
  })()`);
  await evalJs(ws, `FundUI.checkDataSafety()`);
  await sleep(250);
  const banner = await evalJs(ws, `JSON.stringify((() => {
    const b = document.getElementById('dataRiskBanner');
    return {
      shown: !b.classList.contains('hidden'),
      path: (document.getElementById('dataRiskPath')||{}).textContent || '',
      hasMigrate: !!document.getElementById('dataRiskMigrate'),
      dot: !document.getElementById('dataRiskDot').classList.contains('hidden'),
      pos: getComputedStyle(b).position
    };
  })())`);
  const bn0 = JSON.parse(banner);
  check('数据在软件目录内 → 启动显示顶部警告横幅', bn0.shown && bn0.path.length > 0, banner);
  check('警告横幅非阻塞(fixed 定位, 不挡操作)', bn0.pos === 'fixed' && bn0.hasMigrate, banner);
  check('数据在软件目录内 → 侧栏按钮亮红点', bn0.dot, banner);
  // 「稍后」可关闭横幅, 且不影响页面其余功能
  await evalJs(ws, `document.getElementById('dataRiskLater').click()`);
  await sleep(200);
  const afterLater = await evalJs(ws, `JSON.stringify({ hidden: document.getElementById('dataRiskBanner').classList.contains('hidden'), calcUsable: !!document.getElementById('equity') })`);
  check('点「稍后」横幅收起, 页面可正常操作', JSON.parse(afterLater).hidden && JSON.parse(afterLater).calcUsable, afterLater);

  await evalJs(ws, `FundUI.openDataDir()`);
  await sleep(700);
  const dlg = await evalJs(ws, `JSON.stringify((() => {
    const t = (id) => (document.getElementById(id)||{}).textContent || '';
    return {
      riskShown: !document.getElementById('setRisk').classList.contains('hidden'),
      riskApp: t('setRiskApp'),
      migrateVisible: document.getElementById('setMigrateSafe').style.display !== 'none',
      bkDir: t('setBkDir'),
      bkCnt: t('setBkCnt'),
      curDir: t('setCurDir'),
      dotShown: !document.getElementById('dataRiskDot').classList.contains('hidden')
    };
  })())`);
  const dg = JSON.parse(dlg);
  check('数据在软件目录内 → 弹窗显示红色警告', dg.riskShown && dg.riskApp.length > 0, dlg);
  check('数据在软件目录内 → 显示「一键迁出」按钮', dg.migrateVisible, dlg);
  check('数据在软件目录内 → 侧栏按钮亮红点', dg.dotShown, dlg);
  check('弹窗显示自动备份位置与份数', dg.bkDir.length > 0 && /^\d+$/.test(dg.bkCnt), dlg);
  check('弹窗显示当前数据目录', dg.curDir.includes('e2e-data') || dg.curDir.length > 0, dlg);

  // ⚠ 立刻撤掉 data-info 打桩: 下面「立即备份」等用例要走真实接口, 不还原会读到桩数据假通过
  await evalJs(ws, `(() => {
    window.fetch = window.__realFetch; window.fetchT = window.__realFetchT;
    delete window.__realFetch; delete window.__realFetchT;
    sessionStorage.removeItem('dataRiskClosed');
    document.getElementById('dataRiskBanner').classList.add('hidden');
    document.getElementById('dataRiskDot').classList.add('hidden');
    document.getElementById('setRisk').classList.add('hidden');
    document.getElementById('setMigrateSafe').style.display = 'none';
    return 'restored';
  })()`);
  const restored = await evalJs(ws, `(async () => {
    const d = await (await fetch('/api/funds/data-info')).json();
    return JSON.stringify({ risky: !!d.risky, real: d.data_dir.indexOf('e2e') >= 0 });
  })()`);
  check('已还原打桩: data-info 回到真实数据(测试目录 risky=false)', JSON.parse(restored).risky === false, restored);

  // 立即备份: 备份目录满 10 份时会淘汰最旧的 → 份数不一定 +1, 改判「是否写入了新的快照」
  const bkBefore = await evalJs(ws, `(async () => {
    const l = await (await fetch('/api/funds/backups')).json();
    const d = await (await fetch('/api/funds/data-info')).json();
    const arr = l.backups || [];
    return JSON.stringify({count: d.backup_count || 0, newest: arr.length ? arr[0].name : ''});
  })()`);
  const bb = JSON.parse(bkBefore);
  const bkNow = await evalJs(ws, `(async () => {
    const r = await (await fetch('/api/funds/backup', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).json();
    const l = await (await fetch('/api/funds/backups')).json();
    const d = await (await fetch('/api/funds/data-info')).json();
    const arr = l.backups || [];
    return JSON.stringify({ok: r.ok, path: r.path||'', count: d.backup_count||0, newest: arr.length ? arr[0].name : ''});
  })()`);
  const bn = JSON.parse(bkNow);
  check('立即备份: 返回成功且写入了新快照', bn.ok && bn.newest !== '' && bn.newest !== bb.newest,
        'before=' + bb.newest + ' after=' + bn.newest);
  check('备份文件落在数据目录的 backup 子目录', /backup/.test(bn.path), bkNow);
  // 备份列表接口可用
  const bkList = await evalJs(ws, `(async () => { const d = await (await fetch('/api/funds/backups')).json(); return JSON.stringify({ok:d.ok, n:(d.backups||[]).length}); })()`);
  const bl = JSON.parse(bkList);
  check('备份列表接口返回条目', bl.ok && bl.n >= 1, bkList);
  await evalJs(ws, `document.getElementById('setBg').classList.add('hidden')`);

  // ---- 场景R (v50.32): 内置文件夹浏览器 + 备份恢复 ----
  // 1) 备份列表: 弹窗里应有「恢复」按钮, 且每份带时间/条数
  await evalJs(ws, `FundUI.openDataDir()`);
  await sleep(900);
  const bkUi = await evalJs(ws, `JSON.stringify((() => {
    const box = document.getElementById('setBkList');
    return {
      hasRestore: box.querySelectorAll('[data-bkrestore]').length >= 1,
      hasLatest: box.innerText.includes('最新'),
      hasCount: box.innerText.includes('资金')
    };
  })())`);
  const bkU = JSON.parse(bkUi);
  check('备份列表展示「恢复」按钮与最新标记', bkU.hasRestore && bkU.hasLatest && bkU.hasCount, bkUi);

  // ---- v50.75: 备份记录带「最后的变更内容」, 恢复前能看清退回到哪一步 ----
  // ⚠ E2E 全程开着 OC_NO_AUTO_BACKUP=1(避免备份风暴撞批量删除保护) → 写记录不会自动产生快照,
  //    所以这里改走「手动备份」接口造一份, 并顺带验证 reason/detail 能落盘且被界面画出来
  const bkChg = await evalJs(ws, `(async () => {
    // 先用真实写库接口造一条记录, 确认写库链路本身没问题
    const up = await (await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({mode:'options', strategy:'期权买方', underlying:'bktest', contract:'bktest2610C5000',
        op_type:'open', direction:'buy', open_date:'2026-10-12', call_put:'C', open_price:88, qty:3, premium:264})})).json();
    // 再显式备份一次(manual), 这会写 reason/detail
    const mk = await (await fetch('/api/funds/backup', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).json();
    await new Promise(r => setTimeout(r, 300));
    const d = await (await fetch('/api/funds/backups')).json();
    const list = d.backups || [];
    const first = list[0] || {};
    await FundUI.loadBackups();
    const box = document.getElementById('setBkList');
    return JSON.stringify({
      saved: !!up.ok, id: up.id, mkOk: !!mk.ok,
      n: list.length,
      reason: first.reason || '', detail: first.detail || '',
      ver: first.version_name || '',
      hasMetaField: ('reason' in first) && ('pools' in first),
      domHasChg: box.querySelectorAll('.bk-chg').length >= 1,
      domText: (box.querySelector('.bk-chg') || {}).innerText || ''
    });
  })()`);
  await sleep(400);
  const bkC = JSON.parse(bkChg);
  check('写记录 + 手动备份都成功', bkC.saved && bkC.mkOk, bkChg);
  check('新备份带变更说明(reason 非空)', bkC.reason.length > 0, bkChg);
  check('变更说明标出「手动备份」', /手动/.test(bkC.reason), bkChg);
  check('备份列表带 reason/pools 字段', bkC.hasMetaField, bkChg);
  check('备份列表带版本号标记', /^v\d/.test(bkC.ver), bkChg);
  check('界面真的画出变更说明(.bk-chg)', bkC.domHasChg && bkC.domText.length > 0, bkChg);
  // 恢复确认框必须把「最后变更」念出来, 否则用户还是不知道要退到哪
  // ⚠ v50.78: restoreBackup 会先异步取「与现在的差异」再弹 confirm → 这里必须 await,
  //   否则 confirm 还没被调用就去读 msg 了(会读到空串)
  const bkConfirm = await evalJs(ws, `(async () => {
    const msg = [];
    const old = window.confirm; window.confirm = (t) => { msg.push(t); return false; };
    try {
      await FundUI.restoreBackup('funds_不存在_测试.db', {reason:'交易记录-保存', detail:'期权买方 · bktest2610C5000 · 开仓买 3 手', mtime: Math.floor(Date.now()/1000), records:1, trades:3});
      await new Promise(r => setTimeout(r, 200));
    } finally { window.confirm = old; }
    return JSON.stringify({txt: msg.join('|')});
  })()`);
  await sleep(300);
  const bkCf = JSON.parse(bkConfirm);
  check('恢复确认框显示备份时间', /备份时间/.test(bkCf.txt), bkConfirm);
  check('恢复确认框显示「最后变更」与手数', /最后变更：交易记录-保存/.test(bkCf.txt) && /3 手/.test(bkCf.txt), bkConfirm);
  check('恢复确认框显示备份内含条数', /内含：资金 1 条/.test(bkCf.txt), bkConfirm);
  // v50.78: 备份不存在时差异读取失败也不能挡住恢复(确认框照旧弹出, 只是多一行说明)
  check('恢复确认框(v50.78): 差异取不到也不阻断(备份不存在仍能弹确认框)',
        /将用这份备份【覆盖当前数据】/.test(bkCf.txt) && /确定恢复吗/.test(bkCf.txt), bkConfirm);
  // 清掉这条测试记录
  await evalJs(ws, `(async () => { await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:${bkC.id}})}); })()`);
  await sleep(300);

  await evalJs(ws, `document.getElementById('setBg').classList.add('hidden')`);

  // 2) 文件夹浏览器: 打开后能列出盘符/目录, 双击目录可进入, 选好后回填路径
  await evalJs(ws, `FundUI.browseDataDir()`);
  await sleep(700);
  const pk = await evalJs(ws, `JSON.stringify((() => {
    const m = document.getElementById('pickBg');
    return {
      shown: !m.classList.contains('hidden'),
      drives: document.querySelectorAll('#pickDrives [data-pickdrive]').length,
      dirs: document.querySelectorAll('#pickList [data-pickdir]').length,
      path: document.getElementById('pickPath').value
    };
  })())`);
  const pk0 = JSON.parse(pk);
  check('文件夹浏览器: 弹窗打开并列出盘符/目录', pk0.shown && pk0.drives >= 1 && pk0.path.length > 0, pk);
  // v50.77: 目录名必须转义 —— 数据目录下预置了一个名为 a&b'c 的子目录,
  //   转义后 textContent 应原样还原, 且 data-pickdir 能正确往返(否则点进去会拼错路径)
  const pkEsc = await evalJs(ws, `JSON.stringify((() => {
    const els = [...document.querySelectorAll('#pickList [data-pickdir]')];
    const hit = els.find(e => e.textContent.indexOf('a&b') >= 0);
    return { found: !!hit, text: hit ? hit.textContent.trim() : '',
             attr: hit ? hit.dataset.pickdir : '',
             rawHtmlHasEntity: !!hit && /&amp;/.test(hit.outerHTML) };
  })())`);
  const pkE = JSON.parse(pkEsc);
  // 行内是「📁 名字」, 所以用 indexOf 判断(别拿整串做全等)
  check("文件夹浏览器(v50.77): 目录名含 & ' 时按实体转义且文本原样还原",
        pkE.found && pkE.text.indexOf("a&b'c") >= 0 && pkE.attr === "a&b'c" && pkE.rawHtmlHasEntity === true, pkEsc);
  // 进入一个目录(点第一个盘符), 再「选择此文件夹」回填
  const pkNav = await evalJs(ws, `(async () => {
    const dr = document.querySelector('#pickDrives [data-pickdrive]');
    if (!dr) return JSON.stringify({ok:false});
    dr.click();
    await new Promise(r => setTimeout(r, 600));
    const p1 = document.getElementById('pickPath').value;
    document.getElementById('pickOk').click();
    return JSON.stringify({ok:true, picked: p1, into: document.getElementById('setDir').value, modalHidden: document.getElementById('pickBg').classList.contains('hidden')});
  })()`);
  const pn = JSON.parse(pkNav);
  check('文件夹浏览器: 进入目录后回填到数据目录输入框',
    pn.ok && pn.picked === pn.into && pn.picked.length > 0 && pn.modalHidden, pkNav);

  // ---- 场景N: 顶栏刷新按钮存在(在切换器后, 📌 前) ----
  const refreshBtn = await evalJs(ws, `JSON.stringify((() => {
    const tb = document.querySelector('.topbtns');
    const arr = [...tb.children];
    return {
      has: !!document.getElementById('refreshBtn'),
      txt: (document.getElementById('refreshBtn') || {}).textContent || '',
      between: arr.findIndex(el => el.id === 'fontSeg') < arr.findIndex(el => el.id === 'refreshBtn') && arr.findIndex(el => el.id === 'refreshBtn') < arr.findIndex(el => el.id === 'pinBtn')
    };
  })())`);
  const rb = JSON.parse(refreshBtn);
  check('顶栏刷新按钮在 fzseg 后、pinBtn 前', rb.has && /🔄/.test(rb.txt) && rb.between, refreshBtn);

  // ---- 场景O: 刷新后停留在原 tab(不跳回开仓计算) ----
  await evalJs(ws, `document.querySelector('#mainTabs .maintab[data-tab="trades"]').click()`);
  await sleep(400);
  const savedTab = await evalJs(ws, `localStorage.getItem('oc-last-tab')`);
  await send(ws, 'Page.reload');
  // reload 期间 CDP 会短暂不可用, 轮询须 try/catch
  let readyR = false;
  for (let i = 0; i < 30; i++) {
    await sleep(700);
    try {
      const st = await evalJs(ws, `JSON.stringify({cl: typeof CONTRACTS !== 'undefined' ? CONTRACTS.length : -1, hasEq: !!document.getElementById('equity')})`);
      const so = JSON.parse(st);
      if (so && so.cl > 0 && so.hasEq) { readyR = true; break; }
    } catch (e) { /* 导航期间忽略 */ }
  }
  const restoredTab = await evalJs(ws, `JSON.stringify({
    active: document.querySelector('#mainTabs .maintab.active')?.dataset.tab,
    tradesVisible: !document.getElementById('tradesArea').classList.contains('hidden'),
    saved: localStorage.getItem('oc-last-tab')
  })`);
  const rt = JSON.parse(restoredTab);
  check('刷新后恢复原 tab(trades)', readyR && savedTab === 'trades' && rt.active === 'trades' && rt.tradesVisible && rt.saved === 'trades', restoredTab);

  // ---- 场景S (v50.33): 交易记录主表搜索框 ----
  const sBox = await evalJs(ws, `JSON.stringify((() => {
    const inp = document.getElementById('tradesSearch');
    const card = inp && inp.closest('.card');
    return {
      exists: !!inp,
      inTableCard: !!(card && card.contains(document.getElementById('tradesTable'))),
      placeholder: inp ? inp.placeholder : ''
    };
  })())`);
  const sBoxObj = JSON.parse(sBox);
  check('主表卡片头存在搜索框', sBoxObj.exists && sBoxObj.inTableCard, sBox);

  const sRun = await evalJs(ws, `(async () => {
    const inp = document.getElementById('tradesSearch');
    const rows = () => [...document.querySelectorAll('#tradesTable tbody tr.clickable')];
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const before = rows().length;
    if (!before) return JSON.stringify({skip: true});
    const key = rows()[0].dataset.u;                 // 用第一行标的当关键词
    inp.value = key; inp.dispatchEvent(new Event('input'));
    await wait(300);
    const one = rows();
    const filterOk = one.length === 1 && one[0].dataset.u === key;
    // 关键词是标的时, 表格里那一行必须还在
    const hitOk = one.length >= 1 && one[0].dataset.u === key;
    // 大小写不敏感
    inp.value = key.toUpperCase(); inp.dispatchEvent(new Event('input'));
    await wait(250);
    const upperOk = rows().length === one.length;
    // 多关键词(空格分隔) 全部命中
    inp.value = key + ' 买入'; inp.dispatchEvent(new Event('input'));
    await wait(250);
    const multiOk = rows().length >= 1;
    // 无匹配 → 空提示
    inp.value = 'zzz_不存在的关键词'; inp.dispatchEvent(new Event('input'));
    await wait(250);
    const txt = document.querySelector('#tradesTable tbody').innerText;
    const emptyOk = rows().length === 0 && txt.includes('没有匹配');
    const hintEmpty = (document.getElementById('tradesSearchHint').textContent || '');
    // 清除按钮 → 全部恢复
    document.getElementById('tradesSearchClear').click();
    await wait(300);
    const restored = rows().length === before;
    const clearHidden = document.getElementById('tradesSearchClear').hidden;
    return JSON.stringify({before, key, filterOk, hitOk, upperOk, multiOk, emptyOk, restored, clearHidden, hintOnEmpty: hintEmpty});
  })()`);
  const sr = JSON.parse(sRun);
  if (sr.skip) {
    check('主表搜索: 有可筛选数据', false, '主表无行, 无法验证搜索');
  } else {
    check('搜索: 输入标的只显示该行', sr.filterOk, sRun);
    check('搜索: 关键词大小写不敏感', sr.upperOk, sRun);
    check('搜索: 多关键词(空格分隔)全部命中', sr.multiOk, sRun);
    check('搜索: 无匹配显示空提示', sr.emptyOk, sRun);
    check('搜索: 点清除恢复全部记录', sr.restored, sRun);
    check('搜索: 清空后清除按钮自动隐藏', sr.clearHidden === true, sRun);
  }
  // 按合约号也能搜到(后端 groups 提供 contracts)
  const sCon = await evalJs(ws, `(async () => {
    const g = await (await fetch('/api/trades/groups')).json();
    const withC = (g.groups || []).find(x => (x.contracts || []).length);
    if (!withC) return JSON.stringify({skip: true});
    const c = withC.contracts[0];
    const inp = document.getElementById('tradesSearch');
    inp.value = c; inp.dispatchEvent(new Event('input'));
    await new Promise(r => setTimeout(r, 300));
    const rows = [...document.querySelectorAll('#tradesTable tbody tr.clickable')];
    document.getElementById('tradesSearchClear').click();
    return JSON.stringify({contract: c, u: withC.underlying, hit: rows.some(r => r.dataset.u === withC.underlying), n: rows.length});
  })()`);
  const sc = JSON.parse(sCon);
  if (sc.skip) {
    check('搜索: 支持按合约号命中', false, '没有带合约的分组');
  } else {
    check('搜索: 支持按合约号命中', sc.hit, sCon);
  }

  // ---- 场景T (v50.36): 「显示全部」按钮 — 交易记录卡片右上角 + 资金曲线 ----
  // 历史 bug: 两处按钮共用 id="btnShowAll" → getElementById 永远取到资金曲线那个,
  //           交易记录按钮永远 hidden, 资金曲线按钮又被交易记录的渲染逻辑覆盖
  const tBtn = await evalJs(ws, `JSON.stringify((() => {
    const a = document.getElementById('btnShowAllTrades');
    const b = document.getElementById('btnShowAllFunds');
    return {hasT: !!a, hasF: !!b, distinct: !!(a && b && a !== b),
            tInCard: !!(a && a.closest('.card') && a.closest('.card').contains(document.getElementById('tradesTable')))};
  })())`);
  const tb2 = JSON.parse(tBtn);
  check('显示全部按钮: 两个 id 各自独立(不再冲突)', tb2.hasT && tb2.hasF && tb2.distinct, tBtn);
  check('显示全部按钮: 交易记录的在表格卡片右上角', tb2.tInCard, tBtn);

  const tRun = await evalJs(ws, `(async () => {
    const post = (p) => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)}).then(r=>r.json());
    // 造 12 个标的, 保证超过主表上限 10
    for (let i = 1; i <= 12; i++) {
      await post({underlying: 'e2eall' + String(i).padStart(2,'0'), contract: 'e2eall' + i + 'C1000',
                  op_type: 'open', direction: 'buy', open_date: '2026-08-' + String(i).padStart(2,'0'),
                  call_put: 'C', open_price: 100, qty: 1, premium: 100});
    }
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await new Promise(r => setTimeout(r, 900));
    const btn = document.getElementById('btnShowAllTrades');
    const rows = () => [...document.querySelectorAll('#tradesTable tbody tr.clickable')];
    const wrap = document.getElementById('tradesTable').closest('.tblwrap');
    const geo = () => ({h: Math.round(wrap.getBoundingClientRect().height), ch: wrap.clientHeight,
                        sh: wrap.scrollHeight, page: document.documentElement.scrollHeight, maxH: wrap.style.maxHeight});
    const collapsed = {n: rows().length, hidden: btn.hidden, label: btn.textContent.trim(), g: geo()};
    btn.click();
    await new Promise(r => setTimeout(r, 500));
    const expanded = {n: rows().length, hidden: btn.hidden, label: btn.textContent.trim(), g: geo()};
    btn.click();
    await new Promise(r => setTimeout(r, 500));
    const back = {n: rows().length, label: btn.textContent.trim(), g: geo()};
    // 资金曲线按钮: 可见性应只由资金曲线月度数决定(不交易日记录影响)
    const fBtn = document.getElementById('btnShowAllFunds');
    document.querySelector('#mainTabs .maintab[data-tab="funds"]').click();
    await new Promise(r => setTimeout(r, 700));
    const fNowHidden = fBtn.classList.contains('hidden');
    const monthRows = document.querySelectorAll('#tblMonthly tbody tr').length;
    const fShouldHidden = monthRows <= 5;
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await new Promise(r => setTimeout(r, 500));
    // 清理造的数据
    const g = await (await fetch('/api/trades/groups')).json();
    for (const it of (g.groups || [])) {
      if (String(it.underlying).startsWith('e2eall')) {
        const d = await (await fetch('/api/trades/detail?underlying=' + it.underlying)).json();
        for (const op of (d.operations || [])) {
          await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
        }
      }
    }
    return JSON.stringify({collapsed, expanded, back, fExists: !!fBtn, fNowHidden, fShouldHidden, monthRows});
  })()`);
  const tr = JSON.parse(tRun);
  check('显示全部: 默认只显示最近 10 条', tr.collapsed.n === 10 && tr.collapsed.hidden === false, tRun);
  check('显示全部: 按钮文案带剩余条数', /显示全部\s*\d+\s*条/.test(tr.collapsed.label), tRun);
  check('显示全部: 点击后展开全部行', tr.expanded.n > 10 && tr.expanded.label === '收起', tRun);
  check('显示全部: 再点一次收起回 10 条', tr.back.n === 10, tRun);
  // 展开后容器高度锁定为折叠态, 超出的行在容器内滚动(页面布局不跳)
  check('显示全部: 展开后容器高度不变', Math.abs(tr.expanded.g.h - tr.collapsed.g.h) <= 2,
        'collapsed=' + tr.collapsed.g.h + ' expanded=' + tr.expanded.g.h);
  check('显示全部: 展开后超出的行在容器内滚动',
        tr.expanded.g.sh > tr.expanded.g.ch && tr.expanded.g.ch === tr.expanded.g.h,
        'scroll=' + tr.expanded.g.sh + ' client=' + tr.expanded.g.ch);
  // ⚠ 页面总高度给 ±2px 容差(v50.53): 容器折叠时是像素小数高度(实测 596.x), 展开时被锁成整数
  //    maxHeight(597px) → documentElement.scrollHeight 取整后差 1px。已用 dump 确认两态的
  //    tblwrap / 统计卡 / header 高度完全一致, 差值只来自 scrollHeight 取整, 不是布局跳动
  check('显示全部: 展开后页面总高度不变(±2px 取整容差)',
        Math.abs(tr.expanded.g.page - tr.collapsed.g.page) <= 2,
        'collapsed=' + tr.collapsed.g.page + ' expanded=' + tr.expanded.g.page);
  check('显示全部: 收起后解除限高', tr.back.g.maxH === '' && tr.back.g.sh === tr.back.g.ch,
        'maxH="' + tr.back.g.maxH + '"');
  check('显示全部: 资金曲线按钮归属资金曲线(可见性由月度数决定)',
        tr.fExists && tr.fShouldHidden === tr.fNowHidden, tRun);

  // ---- 场景U (v50.39): 期权模式「保存当前方案」(最多10组, 与期货互不挤占) ----
  const optPlan = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click();
    await w(300);
    localStorage.removeItem('oc_options_plans');   // 干净起点
    setMode('options');
    await w(300);
    const vis = !document.getElementById('recentPlans').classList.contains('hidden');
    document.getElementById('equity').value = '100';   // 保证权益>0 才可保存
    const futBefore = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]').length;
    const put = async (code, prem, pct) => {
      const c = CONTRACTS.find(x => x.code.toLowerCase() === String(code).toLowerCase());
      pickContract(c, 'O');
      document.getElementById('entryO').value = String(prem);
      document.getElementById('riskAmountO').value = String(pct);
      saveCurrentPlan();
      await w(150);
    };
    // 存 12 组 → 只应保留最近 10 组(淘汰最旧的 si / lc)
    const seq = [['si',1200,3],['lc',800,1],['p',600,2],['cu',400,1.5],['al',300,1],['zn',310,1],
                 ['rb',350,2],['hc',360,2],['i',420,1.5],['j',430,1.5],['jm',440,1],['TA',500,3]];
    for (const it of seq){ await put(it[0], it[1], it[2]); }
    const items = [...document.querySelectorAll('#planList .plans-item')];
    const n = items.length;
    const names = items.map(x => (x.querySelector('.nm') || {}).textContent.trim());
    const ls = JSON.parse(localStorage.getItem('oc_options_plans') || '[]');
    const futAfter = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]').length;
    const emptyTxt = n ? '' : document.getElementById('planList').textContent.trim();
    // 调出第一条(最近保存的 cu 400) → 应恢复权利金
    document.getElementById('entryO').value = '9999';
    items[0].click();
    await w(600);
    const recalledEntry = document.getElementById('entryO').value;
    const recalledMode = curMode;
    // 切回期货模式: 方案区应显示期货那套(数量与期权不同源)
    setMode('futures');
    await w(300);
    const futShown = document.querySelectorAll('#planList .plans-item').length;
    const futCount = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]').length;
    return JSON.stringify({vis, n, names, lsLen: ls.length, lsCodes: ls.map(x => x.code + '@' + x.entry),
                           futSame: futBefore === futAfter, futShown, futCount, recalledEntry, recalledMode, emptyTxt});
  })()`);
  const op = JSON.parse(optPlan);
  check('期权模式: 也显示「最近方案」区', op.vis, optPlan);
  check('期权模式: 保存方案后出现在列表里', op.n === 10, optPlan);
  check('期权模式: 最多只保留最近 10 组', op.lsLen === 10, optPlan);
  check('期权模式: 超出时淘汰最旧的(si/lc 已被挤掉)', op.lsCodes.indexOf('si@1200') < 0 && op.lsCodes.indexOf('lc@800') < 0 && op.lsCodes[0] === 'TA@500', optPlan);
  check('期权模式: 不挤占期货方案', op.futSame, optPlan);
  check('期权模式: 点「调出」恢复该方案的权利金', op.recalledEntry === String(op.lsCodes[0].split('@')[1]) && op.recalledMode === 'options', optPlan);
  check('期权模式: 切回期货后方案区换成期货那套', op.futShown === op.futCount, optPlan);

  // ---- 场景V (v50.39): 交易记录：期货模式 ----
  const futRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    // 清掉本场景造的数据
    // ⚠ 必须带 batch: 期货记录现在是分批次的, 不带批次只会删到「默认批次」那一条, 残留会把下轮断言污染
    const cleanUp = async (prefix) => {
      const g = await (await fetch('/api/trades/groups?mode=futures')).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying).startsWith(prefix)) {
          const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + it.underlying
            + '&batch=' + encodeURIComponent(it.batch || ''))).json();
          for (const op of (d.operations || [])) {
            await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
          }
        }
      }
    };
    await cleanUp('e2efut');
    // 清掉本场景可能残留的复盘(上次跑崩留下的), 否则条数断言会被污染
    const rvOld = await (await fetch('/api/trades/reviews?mode=futures')).json();
    for (const x of (rvOld.reviews || [])) {
      if (String(x.underlying).startsWith('e2efut')) {
        await fetch('/api/trades/review/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: x.id})});
      }
    }
    // 切到「交易记录：期货模式」tab
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    const tabOk = TradeUI.mode === 'futures';
    const poolTitle = q('poolTitle').textContent;
    const mainTitle = q('mainTableTitle').textContent;
    const layTm = document.querySelector('.trades-layout').dataset.tm;
    // 新建开仓弹窗: 期权专有字段应隐藏, 初次止损止盈应可见
    q('btnNewOpen').click();
    await w(400);
    const vis = id => { const el = q(id); if (!el) return false; const box = el.closest('label'); return !!(box && box.offsetParent !== null); };
    const modal = {
      // v50.44: 期货也要填具体合约号了(原来「标的即合约」被隐藏)
      contractHidden: !vis('tmContract'),
      contractLabel: (q('tmContractLabel') || {}).textContent || '',
      callPutHidden: !vis('tmCallPut'),
      deltaHidden: !vis('tmOpenDelta') && !vis('tmTargetDelta'),
      initStopShown: vis('tmInitStop'),
      initTargetShown: vis('tmInitTarget'),
      dirOpts: [...q('tmDirection').options].map(o => o.textContent),
    };
    // 填一条期货开仓并保存(v50.44 起合约必填)
    q('tmUnderlying').value = 'e2efut01';
    q('tmContract').value = 'e2efut2609';
    q('tmOpenPrice').value = '3500';
    q('tmQty').value = '3';
    q('tmPremium').value = '1680';
    q('tmInitStop').value = '3450';
    q('tmInitTarget').value = '3650';
    q('tmDirection').value = 'buy';
    const saveR = await TradeUI.submitModal();
    await w(700);
    q('tradeModalBg').classList.add('hidden');
    // 期货列表出现, 期权列表不出现
    const gf = await (await fetch('/api/trades/groups?mode=futures')).json();
    const go = await (await fetch('/api/trades/groups?mode=options')).json();
    const inFut = (gf.groups || []).some(x => x.underlying === 'e2efut01');
    const inOpt = (go.groups || []).some(x => x.underlying === 'e2efut01');
    // v50.49: 主页「新建开仓」生产独立批次 → 后面所有详情请求都要带上它
    const FBATCH = ((gf.groups || []).find(x => x.underlying === 'e2efut01') || {}).batch || '';
    const newBatchOk = /^\\d{14}[0-9a-z]{2}$/.test(FBATCH);   // 时间戳14位 + 2位随机
    // 打开详情 → 方向显示「多头」, 测算卡/复盘模块存在
    await TradeUI.loadDetail('e2efut01', FBATCH);
    await w(700);
    const detailMeta = q('tdMeta').textContent;
    const holdDirTxt = [...document.querySelectorAll('#tdHoldings tr td')].map(t => t.textContent).join('|');
    const calcBox = q('tdCalcCard') ? q('tdCalcCard').textContent.trim() : '';
    // (v50.45) 持仓表新增「标的」列: 表头要有, 首行要显示中文(认不出时退回代码 e2efut01)
    const holdHead = [...document.querySelectorAll('#tradeDetailPanel table.tbl')][0];
    const hhFirst = holdHead.tHead.rows[0].children[0].textContent.trim();
    const holdCell0 = (() => {
      const r = holdHead.tBodies[0].rows[0];
      return r ? r.children[0].textContent.trim() : '';
    })();
    // 复盘: 新建 → 出现在最上
    q('btnNewReview').click();
    await w(300);
    const rvAtDefault = q('rvAt').value;
    q('rvContent').value = '第一条复盘(旧)';
    q('rvAt').value = '2026-09-10T09:00';
    await TradeUI.submitReview();
    await w(600);
    q('btnNewReview').click();
    await w(300);
    q('rvContent').value = '第二条复盘(新)';
    q('rvAt').value = '2026-09-16T09:00';
    await TradeUI.submitReview();
    await w(600);
    const rvItems = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    const rvTimes = [...document.querySelectorAll('#reviewList .review-item .rv-t span')].map(x => x.textContent);
    // 复盘按模式隔离: 期权模式查不到
    const rvOpt = await (await fetch('/api/trades/reviews?mode=options')).json();
    //「新建复盘」默认带当前时间
    q('btnNewReview').click();
    await w(250);
    const rvNowDefault = q('rvAt').value;
    TradeUI.closeReviewModal();
    // (v50.40) 分页面「新建开仓」「新建平仓」都不该出现 初次止损/止盈
    q('tdNewOpen').click();
    await w(400);
    const detOpenInit = vis('tmInitStop') || vis('tmInitTarget');
    q('tmCancel').click();
    await w(250);
    q('tdNewClose').click();
    await w(400);
    const detCloseInit = vis('tmInitStop') || vis('tmInitTarget');
    const closeDirOpts = [...q('tmDirection').options].map(o => o.textContent);
    // (v50.44) 期货平仓: 合约改成下拉(带余量), 平仓数量超额要被前端直接拦下
    const csel = q('tmContract');
    const closeIsSel = !!(csel && csel.tagName === 'SELECT');
    const closeOpt = closeIsSel ? (csel.options[csel.selectedIndex] || {}) : {};
    const closeRemaining = closeIsSel ? +((closeOpt.dataset || {}).remaining || 0) : -1;
    let closeSelTxt = closeIsSel ? (closeOpt.textContent || '') : '';
    let overErr = '';
    if (closeIsSel && closeRemaining > 0){
      const oldQty = q('tmCloseQty').value;
      q('tmCloseQty').value = String(closeRemaining + 5);
      const okOver = await TradeUI.submitModal();
      overErr = q('tmError').textContent || '';
      q('tmCloseQty').value = oldQty;
    }
    // (v50.44) 操作记录右上角「筛选合约」: 期货模式也要能看见
    const cfVis = (() => { const el = q('tdContractFilter'); return !!(el && el.offsetParent !== null); })();
    // (v50.44) 合约筛选行为: 选定某个合约后操作表只剩该合约的行, 清空后恢复
    const cfp = await (async () => {
      const f = q('tdContractFilter');
      if (!f || !f.options || f.options.length < 2) return { skip: 1 };
      const target = f.options[1].value;
      const grab = () => {
        const tb = [...document.querySelectorAll('#tradeDetailPanel table.tbl')].pop();
        return [...tb.querySelectorAll('tbody tr')].map(r => ((r.querySelector('td') || {}).innerText || '').trim());
      };
      f.value = target;
      f.dispatchEvent(new Event('change', { bubbles: true }));
      await w(200);
      const r1 = grab();
      f.value = '';
      f.dispatchEvent(new Event('change', { bubbles: true }));
      await w(200);
      const r2 = grab();
      return { target, n1: r1.length, all1: r1.every(c => c === target), n2: r2.length };
    })();
    // (v50.44) 标的中文化: 真实品种代码 a → 豆一; 认不出的原样返回
    const nameA = TradeUI.underlyingText('a');
    const nameUnknown = TradeUI.underlyingText('e2efut01');
    q('tmCancel').click();
    await w(250);
    // (v50.40) 两张明细表: 表头可见列数必须等于数据行可见列数(期货模式曾经整体错位)
    const vN = el => [...el.children].filter(c => c.offsetParent !== null).length;
    const vHead = t => [...t.tHead.rows[0].children].filter(c => c.offsetParent !== null)
                        .map(c => c.textContent.trim());
    const colN = [...document.querySelectorAll('#tradeDetailPanel table.tbl')].map(t => {
      const r = t.tBodies[0].rows[0];
      // 空态行是单个 colspan 单元格: 直接拿 colspan 值当「数据列数」
      const span = (r && r.children.length === 1 && r.children[0].colSpan > 1)
        ? +r.children[0].colSpan : 0;
      return {head: vN(t.tHead.rows[0]), span, body: r ? vN(r) : -1, names: vHead(t)};
    });
    // (v50.40) 阶梯止盈: 详情卡里应是和计算器一样的 rung 方块, 不再是单行文本
    const dt0 = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efut01&batch=' + encodeURIComponent(FBATCH))).json();
    const op0 = (dt0.operations || []).find(o => o.op_type === 'open');
    const snap0 = {code: 'e2efut01', name: '测试期货', dir: 'short', entry: 3500, stop: 3450, target: 3650,
      riskPct: 1.5, budget: 1350, lots: 3, pl_ratio: 2.96, per_lot_risk: 1330, risk_used: 3990,
      margin_used: 1680, ladder: [{r:2,price:3400},{r:3,price:3350},{r:4,price:3300},{r:5,price:3250}]};
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(Object.assign({}, op0, {calc_json: JSON.stringify(snap0)}))});
    await TradeUI.loadDetail('e2efut01', FBATCH);
    await w(700);
    const ladBlk = q('tdCalcCard').querySelector('.ladder-block');
    const ladHtml = ladBlk ? ladBlk.innerHTML : '';
    const ladTxt = ladBlk ? ladBlk.textContent.replace(/\s+/g, ' ').trim() : '';
    const rungN = q('tdCalcCard').querySelectorAll('.rung').length;
    const rqShort = q('tdCalcCard').querySelectorAll('.rung .rq.short').length;
    // (v50.46) 详情测算卡跟随字号: 切大号后明细数值/每手浮盈必须变大(之前写死 px 不跟随)
    const fzCalc = await (async () => {
      const g = sel => getComputedStyle(q('tdCalcCard').querySelector(sel)).fontSize;
      const grab = () => ({v: g('.details.grid2 .v'), rp: g('.rung .rp'),
                           rq: g('.rung .rq'), lb: g('.ladder-hd .lb'), k: g('.details.grid2 .k')});
      const a = grab();
      document.querySelector('#fontSeg button[data-fz="lg"]').click();
      await new Promise(r=>setTimeout(r,300));
      const b = grab();
      document.querySelector('#fontSeg button[data-fz="md"]').click();
      await new Promise(r=>setTimeout(r,300));
      return {v0:a.v, v1:b.v, r0:a.rp, r1:b.rp,
              rq0:a.rq, rq1:b.rq, lb0:a.lb, lb1:b.lb, k0:a.k, k1:b.k};
    })();
    // (v50.43) 详情卡测算明细与计算器同款两列: .details.grid2 + .dcell, 不再有遗留 .drow
    const ccGrid = q('tdCalcCard').querySelector('.details.grid2');
    const ccCells = q('tdCalcCard').querySelectorAll('.details.grid2 .dcell').length;
    const ccDrow = q('tdCalcCard').querySelectorAll('.details.grid2 .drow').length;
    const ccStack = ccGrid ? [...q('tdCalcCard').querySelectorAll('.details.grid2 .dcell')]
      .every(c => { const k = c.querySelector('.k'), v = c.querySelector('.v');
        return k && v && k.textContent.trim() && v.textContent.trim()
          && k.getBoundingClientRect().top <= v.getBoundingClientRect().top; }) : false;
    // (v50.45) 改开仓合约号后, 测算结果不能消失: 模拟前端「修改开仓」(不传 calc_json)
    const dtB = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efut01&batch=' + encodeURIComponent(FBATCH))).json();
    const opB = (dtB.operations || []).find(o => o.op_type === 'open');
    const patchB = Object.assign({}, opB);
    delete patchB.calc_json;                       // 前端 collectFromModal 不带这个字段
    patchB.contract = 'e2efut2609v2';
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(patchB)});
    await TradeUI.loadDetail('e2efut01', FBATCH);
    await w(700);
    const calcAfterEdit = q('tdCalcCard').textContent.indexOf('暂无测算结果') < 0;
    const calcEditName = (q('tdCalcCard').querySelector('.ratio-strip .dim') || {}).textContent || '';
    // 还原合约号, 免得影响后面的断言
    patchB.contract = opB.contract;
    await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(patchB)});
    await TradeUI.loadDetail('e2efut01', FBATCH);
    await w(500);
    // (v50.40) 复盘「修改」: 回填 -> 保存 -> 内容变、条数不变
    const edBtn = document.querySelector('#reviewList [data-rvedit]');
    edBtn.click();
    await w(350);
    const rvEditTitle = q('rvTitle').textContent;
    const rvEditBack = q('rvContent').value;
    const rvEditAt = q('rvAt').value;
    q('rvContent').value = '第二条复盘(已改)';
    await TradeUI.submitReview();
    await w(700);
    const rvAfter = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    // 清理
    await cleanUp('e2efut');
    await fetch('/api/trades/review/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: (await (await fetch('/api/trades/reviews?mode=futures')).json()).reviews[0].id})});
    const rvLeft = (await (await fetch('/api/trades/reviews?mode=futures')).json()).reviews.length;
    return JSON.stringify({tabOk, poolTitle, mainTitle, layTm, modal, saveOk: !!saveR, inFut, inOpt,
                           FBATCH, newBatchOk,
                           detailMeta, holdDirTxt, calcBox, rvAtDefault, rvItems, rvTimes,
                           rvOptLen: (rvOpt.reviews || []).length, rvNowDefault, rvLeft,
                           detOpenInit, detCloseInit, closeDirOpts, colN,
                           ladBlk: !!ladBlk, ladHtml, ladTxt, rungN, rqShort,
                           ccGrid: !!ccGrid, ccCells, ccDrow, ccStack,
                           calcAfterEdit, calcEditName, hhFirst, holdCell0, fzCalc,
                           closeIsSel, closeRemaining, closeSelTxt, overErr, cfVis, cfp, nameA, nameUnknown,
                           rvEditTitle, rvEditBack, rvEditAt, rvAfter});
  })()`);
  const fu = JSON.parse(futRun);
  check('期货模式: tab 切换后 TradeUI.mode = futures', fu.tabOk, futRun);
  check('期货模式: 监控池改名「期货模式监控池」', fu.poolTitle === '期货模式监控池', fu.poolTitle);
  check('期货模式: 主表标题变「期货交易」', fu.mainTitle === '期货交易', fu.mainTitle);
  check('期货模式: 布局 data-tm=futures', fu.layTm === 'futures', fu.layTm);
  check('期货模式新建开仓: 显示「开仓合约」输入框 / 隐藏 看涨看跌/delta',
        !fu.modal.contractHidden && fu.modal.callPutHidden && fu.modal.deltaHidden
        && fu.modal.contractLabel === '开仓合约', JSON.stringify(fu.modal));
  check('期货模式新建开仓: 出现 初次止损价/初次止盈价',
        fu.modal.initStopShown && fu.modal.initTargetShown, JSON.stringify(fu.modal));
  check('期货模式新建开仓: 方向选项 = 多头/空头',
        JSON.stringify(fu.modal.dirOpts) === JSON.stringify(['多头', '空头']), JSON.stringify(fu.modal.dirOpts));
  check('期货模式: 记录写入期货列表且不出现在期权列表', fu.saveOk && fu.inFut && !fu.inOpt, futRun);
  check('批次(v50.49): 主页「新建开仓」自动生成新批次号(14位时间戳+2位随机)',
        fu.newBatchOk, 'FBATCH=' + fu.FBATCH);
  check('期货模式: 详情方向显示「多头」', fu.holdDirTxt.indexOf('多头') >= 0, fu.holdDirTxt.slice(0, 120));
  check('期货模式: 详情 meta 显示初次止损/止盈',
        fu.detailMeta.indexOf('初次止损价') >= 0 && fu.detailMeta.indexOf('初次止盈价') >= 0, fu.detailMeta.slice(0, 160));
  check('期货模式: 详情有复盘模块 + 测算卡占位', !!fu.calcBox, fu.calcBox.slice(0, 90));
  check('新建复盘: 默认带当前时间', /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(fu.rvNowDefault), fu.rvNowDefault);
  check('复盘: 保存后按时间倒序(最近的在上)',
        fu.rvItems.length === 2 && fu.rvItems[0] === '第二条复盘(新)' && fu.rvItems[1] === '第一条复盘(旧)',
        JSON.stringify(fu.rvItems));
  check('复盘: 与期权模式隔离', fu.rvOptLen === 0, '期权复盘数=' + fu.rvOptLen);
  check('复盘: 删除生效', fu.rvLeft === 1, '剩余=' + fu.rvLeft);

  // ---- v50.40: 分页面明细表列对齐 / 阶梯方块 / 复盘修改 / 初次止损止盈字段收敛 ----
  // 表头可见列数 == 数据行可见列数; 空态行(单 colspan 单元格)则比 colspan 值
  const alignOk = c => c.head > 0 && (c.body === c.head || (c.span > 0 && c.span === c.head));
  check('列对齐: 持仓表 表头可见列数 = 数据行列数',
        alignOk(fu.colN[0]), JSON.stringify(fu.colN[0]));
  check('列对齐: 操作表 表头可见列数 = 数据行列数',
        alignOk(fu.colN[1]), JSON.stringify(fu.colN[1]));
  check('列对齐: 持仓表 无「看涨看跌」列、末列是保证金',
        fu.colN[0].names.indexOf('看涨看跌') < 0 && fu.colN[0].names.indexOf('保证金') >= 0,
        JSON.stringify(fu.colN[0].names));
  // v50.44: 期货也有了具体合约号 → 操作表要能看到「合约」列(之前只期权显示)
  check('列对齐: 操作表 有「合约」列且无 delta/目标列',
        fu.colN[1].names.indexOf('合约') >= 0 && fu.colN[1].names.indexOf('delta') < 0
        && fu.colN[1].names.indexOf('目标') < 0, JSON.stringify(fu.colN[1].names));
  check('期货模式: 分页面「新建开仓」不显示 初次止损/止盈', !fu.detOpenInit, 'detOpenInit=' + fu.detOpenInit);
  check('期货模式: 「新建平仓」不显示 初次止损/止盈', !fu.detCloseInit, 'detCloseInit=' + fu.detCloseInit);
  check('期货模式: 新建平仓方向选项 = 卖出平多头/买入平空头',
        JSON.stringify(fu.closeDirOpts) === JSON.stringify(['卖出平多头', '买入平空头']),
        JSON.stringify(fu.closeDirOpts));
  check('阶梯止盈: 详情卡渲染为与计算器一致的方块', fu.ladBlk, fu.ladTxt.slice(0, 120));
  check('阶梯止盈: 4 档 rung(2R~5R) 且按做空取色',
        fu.rungN === 4 && fu.rqShort === 4, 'rung=' + fu.rungN + ' short=' + fu.rqShort);
  check('阶梯止盈: 含「每手浮盈」与「1R = 止损价差」',
        /每手浮盈/.test(fu.ladHtml) && fu.ladTxt.indexOf('1R = 止损价差') >= 0,
        fu.ladTxt.slice(0, 160));
  check('测算明细: 详情卡与计算器同款两列网格(6 dcell 无遗留 drow)',
        fu.ccGrid && fu.ccCells === 6 && fu.ccDrow === 0,
        'grid=' + fu.ccGrid + ' cells=' + fu.ccCells + ' drow=' + fu.ccDrow);
  check('测算明细: 每格「标签在上 · 数值在下」', fu.ccStack);
  check('测算结果(v50.45): 改开仓合约号后快照不丢(仍显示测算明细, 不是「暂无测算结果」)',
        fu.calcAfterEdit && /测试期货/.test(fu.calcEditName),
        'keep=' + fu.calcAfterEdit + ' name=' + fu.calcEditName);
  check('持仓表(v50.45): 首列是「标的」, 认不出代码时原样显示',
        fu.hhFirst === '标的' && fu.holdCell0 === 'e2efut01',
        JSON.stringify([fu.hhFirst, fu.holdCell0]));
  check('测算卡字号(v50.46): 切大号后明细数值与「每手浮盈」都变大',
        parseFloat(fu.fzCalc.v1) > parseFloat(fu.fzCalc.v0)
        && parseFloat(fu.fzCalc.r1) > parseFloat(fu.fzCalc.r0),
        'v ' + fu.fzCalc.v0 + '→' + fu.fzCalc.v1 + ', rp ' + fu.fzCalc.r0 + '→' + fu.fzCalc.r1);
  // v50.48: 之前 .rung .rq(阶梯止盈价格)/ .ladder-hd .lb 写死 px, 切字号纹丝不动 → 整块看着还是小
  check('测算卡字号(v50.48): 阶梯止盈价格/标题也跟随字号(不再写死 px)',
        parseFloat(fu.fzCalc.rq1) > parseFloat(fu.fzCalc.rq0)
        && parseFloat(fu.fzCalc.lb1) > parseFloat(fu.fzCalc.lb0)
        && parseFloat(fu.fzCalc.k1) > parseFloat(fu.fzCalc.k0),
        'rq ' + fu.fzCalc.rq0 + '→' + fu.fzCalc.rq1 + ', lb ' + fu.fzCalc.lb0 + '→' + fu.fzCalc.lb1
        + ', k ' + fu.fzCalc.k0 + '→' + fu.fzCalc.k1);
  check('测算卡字号(v50.48): 中号下明细数值 ≥17px、止盈价 ≥24px(整体放大一档)',
        parseFloat(fu.fzCalc.v0) >= 17 && parseFloat(fu.fzCalc.rq0) >= 24,
        'v=' + fu.fzCalc.v0 + ' rq=' + fu.fzCalc.rq0);
  check('平仓(v50.44): 合约是下拉且带剩余手数',
        fu.closeIsSel && fu.closeRemaining === 3, JSON.stringify([fu.closeIsSel, fu.closeRemaining]));
  check('平仓(v50.44): 下拉文案含合约号与「余N手」(无看涨看跌)',
        /e2efut2609/.test(fu.closeSelTxt) && /余3手/.test(fu.closeSelTxt)
        && fu.closeSelTxt.indexOf('看') < 0, fu.closeSelTxt);
  check('平仓(v50.44): 数量超额被前端拦下(不写库)',
        /超过该合约剩余可平/.test(fu.overErr), fu.overErr);
  check('筛选合约(v50.44): 期货模式操作记录右上角可见', fu.cfVis);
  check('筛选合约(v50.44): 选定合约后只剩该合约的行, 清空后恢复',
        !fu.cfp.skip && fu.cfp.all1 && fu.cfp.n1 > 0 && fu.cfp.n2 >= fu.cfp.n1,
        JSON.stringify(fu.cfp));
  check('标的中文化(v50.44): a → 豆一, 认不出的原样返回',
        fu.nameA === '豆一 (a)' && fu.nameUnknown === 'e2efut01',
        JSON.stringify([fu.nameA, fu.nameUnknown]));
  check('复盘: 点「修改」回填原时间与原内容',
        fu.rvEditTitle === '修改复盘' && fu.rvEditBack === '第二条复盘(新)'
        && fu.rvEditAt === '2026-09-16T09:00',
        JSON.stringify([fu.rvEditTitle, fu.rvEditBack, fu.rvEditAt]));
  check('复盘: 修改后内容更新且条数不变',
        fu.rvAfter.length === 2 && fu.rvAfter[0] === '第二条复盘(已改)', JSON.stringify(fu.rvAfter));

  // ===== v50.47: 同品种不同批次 → 主页分成多条独立记录 =====
  const batRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const U = 'e2ebat01', B1 = 'B20260915010101aa', B2 = 'B20260915020202bb';
    // 清残留
    const g0 = await (await fetch('/api/trades/groups?mode=futures')).json();
    for (const it of (g0.groups || [])) {
      if (String(it.underlying) !== U) continue;
      const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U
        + '&batch=' + encodeURIComponent(it.batch || ''))).json();
      for (const op of (d.operations || [])) await del(op.id);
    }
    // 同品种、同合约, 两个批次各开一次
    const base = {underlying: U, contract: 'e2ebat2609', op_type: 'open', direction: 'buy',
                  open_price: 3500, qty: 2, premium: 1000, mode: 'futures'};
    const i1 = (await post(Object.assign({}, base, {batch: B1, open_date: '2026-09-01'}))).id;
    const i2 = (await post(Object.assign({}, base, {batch: B2, open_date: '2026-09-02'}))).id;
    // 无批次的第三次(老行为: 与默认批次合并, 不额外增加)
    const i3 = (await post(Object.assign({}, base, {open_date: '2026-09-03'}))).id;
    const g = await (await fetch('/api/trades/groups?mode=futures')).json();
    const mine = (g.groups || []).filter(x => x.underlying === U);
    const ui = async () => {
      await TradeUI.refresh(); await w(500);
      return [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
        .filter(tr => tr.dataset.u === U)
        .map(tr => ({b: tr.dataset.b || '', sub: (tr.querySelector('.u-sub') || {}).textContent || ''}));
    };
    // 主页: 同品种 3 条(2 个批次 + 1 个默认批次)
    const rows = await ui();
    // 点第二条(不同批次) → 详情只带该批次的操作记录
    const tr2 = [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
      .filter(x => x.dataset.u === U).find(x => (x.dataset.b || '') === B2);
    if (tr2) tr2.click();
    await w(600);
    const ops2 = (TradeUI.detail.operations || []).length;
    const detBatch = TradeUI.detail.batch || '';
    const holdQty = (TradeUI.detail.holdings || []).reduce((s,h) => s + (+h.qty||0), 0);
    // 平仓 2 手(本批次正好 2 手) → 应成功, 且不影响另一批次
    let closeOk = false, otherLeft = 0;
    if (detBatch === B2) {
      TradeUI.openEditModal('close', {fromDetail: true});
      await w(400);
      document.getElementById('tmCloseDate').value = '2026-09-04';
      document.getElementById('tmCloseQty').value = '2';
      document.getElementById('tmClosePrice').value = '3600';
      document.getElementById('tmPnl').value = '2000';
      closeOk = await TradeUI.submitModal();
      await w(600);
      const d1 = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U + '&batch=' + B1)).json();
      otherLeft = (d1.holdings || []).reduce((s,h) => s + (+h.qty||0), 0);
    }
    // 清理
    for (const it of [...(await (await fetch('/api/trades/groups?mode=futures')).json()).groups]) {
      if (String(it.underlying) !== U) continue;
      const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U
        + '&batch=' + encodeURIComponent(it.batch || ''))).json();
      for (const op of (d.operations || [])) await del(op.id);
    }
    return JSON.stringify({n: mine.length, batches: mine.map(x => x.batch || ''), rows,
                           ops2, detBatch, holdQty, closeOk, otherLeft,
                           i1: !!i1, i2: !!i2, i3: !!i3});
  })()`);
  const bt = JSON.parse(batRun);
  check('批次(v50.47): 同品种两个批次 → 主页 2 条独立记录(不是塞进同一条)',
        bt.n === 3 && bt.batches.indexOf('B20260915010101aa') >= 0 && bt.batches.indexOf('B20260915020202bb') >= 0,
        batRun);
  check('批次(v50.47): 主表每行带自己的批次号(data-b), 三行互不相同',
        bt.rows.length === 3 && new Set(bt.rows.map(r => r.b)).size === 3, JSON.stringify(bt.rows));
  check('批次(v50.47): 标的下方补合约小字(同品种多行时区分用)',
        bt.rows.every(r => /e2ebat2609/.test(r.sub)), JSON.stringify(bt.rows.map(r => r.sub)));
  check('批次(v50.47): 点某批次行 → 详情只加载该批次的操作(1 条开仓)',
        bt.detBatch === 'B20260915020202bb' && bt.ops2 === 1, 'batch=' + bt.detBatch + ' ops=' + bt.ops2);
  check('批次(v50.47): 详情页新建平仓落在本批次, 不动另一批次的持仓',
        bt.closeOk && bt.otherLeft === 2, 'closeOk=' + bt.closeOk + ' otherLeft=' + bt.otherLeft);

  // ===== v50.49: 主页面「新建开仓」必须新建一条(不能并进之前那条记录) =====
  const nbRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const U = 'e2eb49';
    const clean = async () => {
      const g = await (await fetch('/api/trades/groups?mode=futures')).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying) !== U) continue;
        const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) {
          await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({id: op.id})});
        }
      }
    };
    const rows = async () => {
      const g = await (await fetch('/api/trades/groups?mode=futures')).json();
      return (g.groups || []).filter(x => x.underlying === U).map(x => x.batch || '');
    };
    await clean();
    // 必须在期货模式下操作
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(800);
    // 主页面「新建开仓」填一条
    const newOpen = async (price, date, con) => {
      q('btnNewOpen').click();
      await w(400);
      q('tmUnderlying').value = U;
      q('tmContract').value = con;
      q('tmOpenPrice').value = String(price);
      q('tmQty').value = '2';
      q('tmPremium').value = '1000';
      q('tmOpenDate').value = date;
      q('tmDirection').value = 'buy';
      const ok = await TradeUI.submitModal();
      await w(700);
      q('tradeModalBg').classList.add('hidden');
      await TradeUI.refresh();
      await w(600);
      return ok;
    };
    const ok1 = await newOpen(3500, '2026-09-01', U + '2609');
    const n1 = await rows();
    // 点开这条记录(模拟「之前有这个品种的记录」且详情还开着) → this.detail 残留
    const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')].find(x => x.dataset.u === U);
    if (tr) tr.click();
    await w(700);
    const detB1 = (TradeUI.detail || {}).batch || '';
    const detSet = !!TradeUI.detail;
    // 再次从主页面新建同品种 → 必须是新的一条
    const ok2 = await newOpen(3550, '2026-09-02', U + '2610');
    const n2 = await rows();
    // 详情页里「新建开仓」= 加仓, 仍应并进当前这条(条数不变)
    q('tdNewOpen').click();
    await w(400);
    q('tmContract').value = U + '2611';
    q('tmOpenPrice').value = '3560';
    q('tmQty').value = '1';
    q('tmPremium').value = '500';
    q('tmOpenDate').value = '2026-09-03';
    const ok3 = await TradeUI.submitModal();
    await w(700);
    q('tradeModalBg').classList.add('hidden');
    await TradeUI.refresh();
    await w(600);
    const n3 = await rows();
    // 真实按钮「➖ 新建平仓」(v50.50): 之前 preset 漏了 fromDetail → 平仓被分到全新批次
    //   → 后端在那一批查不到开仓 → 明明有 3 手却报「平仓数量(1)超过剩余可平(0)」
    q('tdNewClose').click();
    await w(400);
    const closeBatch = q('tradeModalBg').dataset.batch || '';
    q('tmCloseDate').value = '2026-09-04';
    q('tmCloseQty').value = '1';
    q('tmClosePrice').value = '3600';
    q('tmPnl').value = '100';
    const okClsSave = await TradeUI.submitModal();
    const closeErr = q('tmError').textContent || '';
    await w(600);
    q('tradeModalBg').classList.add('hidden');
    await TradeUI.refresh();
    await w(600);
    const n4 = await rows();   // 平仓不该多出一条主页记录
    await clean();
    return JSON.stringify({ok1: !!ok1, ok2: !!ok2, ok3: !!ok3, n1, n2, n3, detB1, detSet,
                           okClsSave: !!okClsSave, closeErr, closeBatch, n4});
  })()`);
  const nb = JSON.parse(nbRun);
  check('主页新建开仓(v50.49): 已有该品种记录时再新建 → 主表多出 1 条(不是并进去)',
        nb.ok1 && nb.ok2 && nb.n1.length === 1 && nb.n2.length === 2, nbRun);
  check('主页新建开仓(v50.49): 新记录用全新批次号, 旧那条原封不动(详情残留的批次没有被继承)',
        nb.n2.length === 2 && new Set(nb.n2).size === 2
        && nb.n2.filter(b => b === nb.detB1).length === 1,
        'detail=' + nb.detB1 + ' rows=' + JSON.stringify(nb.n2));
  check('详情页新建开仓(v50.49): 属于加仓, 仍并进当前这条(主表条数不变)',
        nb.ok3 && nb.n3.length === 2, nbRun);
  check('详情页新建平仓(v50.50): 能正常存下(不再报「超过剩余可平(0)」)',
        nb.okClsSave && !nb.closeErr, 'ok=' + nb.okClsSave + ' err=' + nb.closeErr);
  check('详情页新建平仓(v50.50): 落进当前这条记录的批次, 且不新增主页记录',
        nb.closeBatch === nb.detB1 && nb.n4.length === 2,
        'closeBatch=' + nb.closeBatch + ' detail=' + nb.detB1 + ' rows=' + JSON.stringify(nb.n4));

  // ===== v50.50: 期货模式监控池不能串到期权模式 =====
  const poolRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    const orig = window.prompt;
    window.prompt = () => 'e2epool01、e2epool02';
    document.getElementById('btnNewPool').click();
    await w(1000);
    window.prompt = orig;
    const f = await (await fetch('/api/trades/pool?mode=futures')).json();
    const o = await (await fetch('/api/trades/pool?mode=options')).json();
    const inFut = (f.snapshots || []).some(x => (x.contracts || []).includes('e2epool01'));
    const inOpt = (o.snapshots || []).some(x => (x.contracts || []).includes('e2epool01'));
    // 页面上的期货监控池卡片也要能看到这个品种
    const shown = document.getElementById('poolArea').textContent.indexOf('e2epool01') >= 0;
    const dbg = document.getElementById('poolArea').textContent.replace(/\s+/g, ' ').slice(0, 120);
    for (const s of [...(f.snapshots || []), ...(o.snapshots || [])]) {
      if ((s.contracts || []).includes('e2epool01')) {
        await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: s.id})});
      }
    }
    return JSON.stringify({inFut, inOpt, shown, dbg});
  })()`);
  const pr = JSON.parse(poolRun);
  check('期货监控池(v50.50): 新建后落在期货模式(之前全进期权模式, 表现为「加不上」)',
        pr.inFut && !pr.inOpt, poolRun);
  check('期货监控池(v50.50): 页面上能立刻看到刚加的品种', pr.shown, poolRun);

  // ===== v50.51: 详情页内容必须按批次完全独立 =====
  const metaRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const U = 'e2emeta01', B1 = 'B20260910000001aa', B2 = 'B20260916000002bb';
    const clean = async () => {
      const g = await (await fetch('/api/trades/groups?mode=futures')).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying) !== U) continue;
        const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    await clean();
    // 批次1: 开 5 手 + 平 2 手(+2000); 批次2: 只开 5 手(从没平过)
    await post({underlying: U, contract: 'sr2611', op_type: 'open', direction: 'buy',
                open_date: '2026-09-10', open_price: 5400, qty: 5, premium: 0, mode: 'futures', batch: B1});
    await post({underlying: U, contract: 'sr2611', op_type: 'close', direction: 'sell',
                close_date: '2026-09-12', close_qty: 2, close_price: 5600, pnl: 2000,
                mode: 'futures', batch: B1});
    await post({underlying: U, contract: 'sr2611', op_type: 'open', direction: 'buy',
                open_date: '2026-09-16', open_price: 5500, qty: 5, premium: 0, mode: 'futures', batch: B2});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    await TradeUI.loadDetail(U, B2);
    await w(700);
    const t2 = document.getElementById('tdMeta').textContent.replace(/\\s+/g, ' ').trim();
    const ops2 = (TradeUI.detail.operations || []).length;
    await TradeUI.loadDetail(U, B1);
    await w(700);
    const t1 = document.getElementById('tdMeta').textContent.replace(/\\s+/g, ' ').trim();
    const ops1 = (TradeUI.detail.operations || []).length;
    await clean();
    return JSON.stringify({t2, t1, ops1, ops2});
  })()`);
  const md = JSON.parse(metaRun);
  check('详情页(v50.51): 未平仓那条不显示别条记录的平仓盈亏与状态',
        /未平仓/.test(md.t2) && md.t2.indexOf('2,000') < 0 && md.t2.indexOf('部分平仓') < 0,
        md.t2);
  check('详情页(v50.51): 已部分平仓那条自己显示 +CN¥2,000',
        /部分平仓/.test(md.t1) && /2,000/.test(md.t1), md.t1);
  check('详情页(v50.51): 操作记录也只属于本批次(1 条 vs 2 条)',
        md.ops2 === 1 && md.ops1 === 2, 'ops1=' + md.ops1 + ' ops2=' + md.ops2);

  // ===== v50.52: 复盘笔记跟着「这条记录」走 =====
  const rvRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const U = 'e2erv01', B1 = 'B20260920000001aa', B2 = 'B20260920000002bb';
    const clean = async () => {
      const g = await (await fetch('/api/trades/groups?mode=futures')).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying) !== U) continue;
        const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + U
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
      const rv = await (await fetch('/api/trades/reviews?mode=futures&underlying=' + U)).json();
      for (const x of (rv.reviews || [])) {
        await fetch('/api/trades/review/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: x.id})});
      }
    };
    await clean();
    for (const b of [B1, B2]) {
      await post({underlying: U, contract: 'sr2611', op_type: 'open', direction: 'buy',
                  open_date: '2026-09-20', open_price: 5400, qty: 1, premium: 0, mode: 'futures', batch: b});
    }
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    // 批次1 里写一条复盘
    await TradeUI.loadDetail(U, B1);
    await w(700);
    TradeUI.openReviewModal();
    await w(300);
    document.getElementById('rvContent').value = '只属于批次1的复盘';
    await TradeUI.submitReview();
    await w(800);
    const list1 = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    // 切到批次2 → 不该看到批次1 的复盘
    await TradeUI.loadDetail(U, B2);
    await w(800);
    const list2 = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    const txt2 = document.getElementById('reviewList').textContent.replace(/\\s+/g, ' ').trim().slice(0, 30);
    // 再切回批次1 → 复盘还在
    await TradeUI.loadDetail(U, B1);
    await w(800);
    const list1b = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    await clean();
    return JSON.stringify({list1, list2, list1b, txt2});
  })()`);
  const rv = JSON.parse(rvRun);
  check('复盘(v50.52): 在这条记录里写的复盘, 本记录能看到',
        rv.list1.length === 1 && rv.list1[0] === '只属于批次1的复盘', JSON.stringify(rv.list1));
  check('复盘(v50.52): 同品种另一条记录的详情里看不到(不再按品种共享)',
        rv.list2.length === 0 && /暂无复盘笔记/.test(rv.txt2), rvRun);
  check('复盘(v50.52): 切回原记录复盘仍在(归属稳定)',
        rv.list1b.length === 1 && rv.list1b[0] === '只属于批次1的复盘', JSON.stringify(rv.list1b));

  // ===== v50.57: 期权也能写复盘(与期货隔离) / 弹窗价格按 tick 步进 / 备份带最近方案 =====
  const v57Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const delRv = async (mode, u) => {
      const rv = await (await fetch('/api/trades/reviews?mode=' + mode + '&underlying=' + u)).json();
      for (const x of (rv.reviews || [])) {
        await fetch('/api/trades/review/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: x.id})});
      }
    };
    const wipe = async (mode, u) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying) !== u) continue;
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying=' + u
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
      await delRv(mode, u);
    };

    // ---------- 1) 期权复盘: 能写, 且不串到期货 ----------
    const OU = 'e2eopt57', OB = 'B20260921000001cc';
    await wipe('options', OU);
    await wipe('futures', OU);
    await post({underlying: OU, contract: 'lc2611-C-144000', op_type: 'open', direction: 'buy',
                open_date: '2026-09-21', open_price: 9800, qty: 1, premium: 9800,
                mode: 'options', batch: OB});
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await w(1200);
    await TradeUI.loadDetail(OU, OB);
    await w(900);
    const optBlockShown = (() => {
      const box = document.getElementById('reviewList');
      return !!(box && box.offsetParent !== null);
    })();
    TradeUI.openReviewModal();
    await w(300);
    document.getElementById('rvContent').value = '期权这条的复盘';
    await TradeUI.submitReview();
    await w(900);
    const optList = [...document.querySelectorAll('#reviewList .review-item .rv-c')].map(x => x.textContent);
    const optApi = (await (await fetch('/api/trades/reviews?mode=options&underlying=' + OU
      + '&batch=' + OB)).json()).reviews || [];
    const futSame = (await (await fetch('/api/trades/reviews?mode=futures&underlying=' + OU
      + '&batch=' + OB)).json()).reviews || [];
    const optMode = optApi.length ? optApi[0].mode : '';

    // ---------- 2) 弹窗价格按品种 tick 步进(期货) ----------
    const FB = 'B20260921000002dd';
    await wipe('futures', 'lc');
    await post({underlying: 'lc', contract: 'lc2611', op_type: 'open', direction: 'buy',
                open_date: '2026-09-21', open_price: 78600, qty: 1, premium: 0,
                init_stop: 75200, init_target: 89400, mode: 'futures', batch: FB});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(1000);
    await TradeUI.loadDetail('lc', FB);
    await w(900);
    document.getElementById('tdNewOpen').click();
    await w(500);
    const stepLc = document.getElementById('tmOpenPrice').step;
    const hintLc = document.getElementById('tmTickHint').textContent;
    document.getElementById('tradeModalBg').classList.add('hidden');
    await w(200);
    // 换成螺纹钢(tick=1) → step 应跟着变
    TradeUI.openEditModal('open', {underlying: 'rb', batch: 'B20260921000003ee'});
    await w(400);
    const stepRb = document.getElementById('tmOpenPrice').step;
    document.getElementById('tradeModalBg').classList.add('hidden');
    await w(200);

    // ---------- 3) 最近方案: 快照 → 清空 → 恢复 ----------
    const p1 = [{name: '测试方案A', code: 'cu', dir: 'long', entry: 72150}];
    const p2 = [{name: '期权方案B', code: 'lc', entry: 9800}];
    localStorage.setItem('oc_futures_plans', JSON.stringify(p1));
    localStorage.setItem('oc_options_plans', JSON.stringify(p2));
    const snap = plansSnapshot();
    localStorage.setItem('oc_futures_plans', '[]');
    localStorage.setItem('oc_options_plans', '[]');
    const restored = plansRestore(snap);
    const backF = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]');
    const backO = JSON.parse(localStorage.getItem('oc_options_plans') || '[]');
    localStorage.removeItem('oc_futures_plans');
    localStorage.removeItem('oc_options_plans');

    const lcTick = (CONTRACTS.find(x => String(x.code).toLowerCase() === 'lc') || {}).tick;
    const rbTick = (CONTRACTS.find(x => String(x.code).toLowerCase() === 'rb') || {}).tick;
    await wipe('options', OU);
    await wipe('futures', 'lc');
    return JSON.stringify({optBlockShown, optList, futSameLen: futSame.length, optMode,
                           stepLc, hintLc, stepRb, lcTick, rbTick,
                           snapF: snap.futures.length, snapO: snap.options.length,
                           restored, backF: backF[0] ? backF[0].name : '', backO: backO[0] ? backO[0].name : ''});
  })()`);
  const v57 = JSON.parse(v57Run);
  check('复盘(v50.57): 期权详情页显示复盘模块', v57.optBlockShown === true, v57Run);
  check('复盘(v50.57): 期权记录能写复盘并读回',
        v57.optList.length === 1 && v57.optList[0] === '期权这条的复盘', JSON.stringify(v57.optList));
  check('复盘(v50.57): 期权复盘 mode=options, 同标的期货查不到(隔离)',
        v57.optMode === 'options' && v57.futSameLen === 0, 'mode=' + v57.optMode + ' 期货侧=' + v57.futSameLen);
  check('价格步进(v50.57): 期货弹窗开仓价 step = 品种最小变动价位',
        String(v57.stepLc) === String(v57.lcTick) && /最小变动价位/.test(v57.hintLc || ''),
        'lc step=' + v57.stepLc + '(tick=' + v57.lcTick + ') hint=' + v57.hintLc);
  check('价格步进(v50.57): 换品种后 step 跟着变(不是写死的)',
        String(v57.stepRb) === String(v57.rbTick) && String(v57.stepLc) !== String(v57.stepRb),
        'rb step=' + v57.stepRb + '(tick=' + v57.rbTick + ')');
  check('最近方案(v50.57): 快照含两个模式, 恢复后原样写回',
        v57.snapF === 1 && v57.snapO === 1 && v57.restored === 2
        && v57.backF === '测试方案A' && v57.backO === '期权方案B', v57Run);

  // ===== v50.58: 最近方案支持删除 =====
  const dpRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const mk = n => ({code: 'cu', contract: '沪铜', dir: 'long', entry: 70000 + n,
                      stop: 69000 + n, target: 72000 + n, riskPct: '1', marginRate: '16', eqWan: '90'});
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click();
    await w(500);
    localStorage.setItem('oc_futures_plans', JSON.stringify([mk(1), mk(2)]));
    loadPlans(); renderPlans();
    await w(300);
    const before = document.querySelectorAll('#planList .plans-item').length;
    const delCount = document.querySelectorAll('#planList .plans-item .del').length;
    // 点第二条的 ✕ → 只删方案, 不能触发调出(输入框不该被填)
    const orig = window.confirm;
    window.confirm = () => true;
    document.getElementById('entry').value = '88888';
    const items = document.querySelectorAll('#planList .plans-item');
    items[1].querySelector('.del').click();
    await w(500);
    window.confirm = orig;
    const after = document.querySelectorAll('#planList .plans-item').length;
    const stored = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]');
    const entryUntouched = document.getElementById('entry').value === '88888';
    // 取消删除 → 数量不变
    const keep = JSON.parse(localStorage.getItem('oc_futures_plans') || '[]');
    window.confirm = () => false;
    document.querySelector('#planList .plans-item .del').click();
    await w(400);
    window.confirm = orig;
    const afterCancel = document.querySelectorAll('#planList .plans-item').length;
    // 清理
    localStorage.removeItem('oc_futures_plans');
    document.getElementById('entry').value = '';
    loadPlans(); renderPlans();
    return JSON.stringify({before, delCount, after, storedLen: stored.length,
                           storedEntry: stored[0] ? stored[0].entry : null,
                           entryUntouched, afterCancel, keepLen: keep.length});
  })()`);
  const dp = JSON.parse(dpRun);
  check('方案删除(v50.58): 每条方案卡片都带删除按钮', dp.before === 2 && dp.delCount === 2, dpRun);
  check('方案删除(v50.58): 点 ✕ 删掉对应那条并写回本地存储',
        dp.after === 1 && dp.storedLen === 1 && Number(dp.storedEntry) === 70001,
        '剩 ' + dp.storedLen + ' 条, 保留 entry=' + dp.storedEntry);
  check('方案删除(v50.58): 点 ✕ 不会被当成「调出」(输入框不变)', dp.entryUntouched === true, dpRun);
  check('方案删除(v50.58): 确认框点取消则不删', dp.afterCancel === 1 && dp.keepLen === 1,
        'afterCancel=' + dp.afterCancel);

  // ===== v50.59: 计算器三模式 / 期权双买测算+加入记录 / 交易记录第三套 =====
  const v59Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const wipe = async (mode, u) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        if (String(it.underlying) !== u) continue;
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying=' + u
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    const U = 'e2edual59';
    await wipe('dual', U);
    localStorage.removeItem('oc_dual_plans');

    // 1) 计算器切到「期权双买」: 字段容器正确切换
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click();
    await w(500);
    const modeBtns = [...document.querySelectorAll('.mode')].map(b => b.dataset.mode);
    document.querySelector('.mode[data-mode="dual"]').click();
    await w(400);
    const dualShown = !document.getElementById('dualFields').classList.contains('hidden');
    const optHidden = document.getElementById('optionsFields').classList.contains('hidden');
    const futHidden = document.getElementById('futuresFields').classList.contains('hidden');

    // 2) 填参数 → 测算
    const eq = document.getElementById('equity'); eq.value = '90'; eq.dispatchEvent(new Event('input'));
    const inp = document.getElementById('cSearchD');
    inp.value = 'lc'; inp.dispatchEvent(new Event('input'));
    inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await w(700);
    document.getElementById('dualMonth').value = '2611';
    document.getElementById('callStrike').value = '144000';
    document.getElementById('callDelta').value = '0.3';
    document.getElementById('callPremium').value = '9800';
    document.getElementById('putStrike').value = '120000';
    document.getElementById('putDelta').value = '-0.3';
    document.getElementById('putPremium').value = '8500';
    onInput();
    await w(1000);
    const shown = !document.getElementById('resultD').classList.contains('hidden');
    const qC = document.getElementById('rQtyCD').textContent.trim();
    const qP = document.getElementById('rQtyPD').textContent.trim();
    const premTxt = ((document.getElementById('rTgtSumD') || {}).textContent || '').trim();
    const noDupPremium = document.getElementById('rPremiumD') === null;   // v50.64: 与止盈目标价位重复, 已删
    const pxCell = document.querySelector('#rTargetsD tbody tr:nth-child(1) td:nth-child(2) .dt-px');
    const pxStyle = pxCell ? getComputedStyle(pxCell) : null;
    const pxBig = pxStyle ? (parseFloat(pxStyle.fontSize) >= 18 && parseInt(pxStyle.fontWeight, 10) >= 700) : false;
    const pxColored = pxStyle ? (pxStyle.color !== getComputedStyle(document.body).color) : false;
    const deltaTxt = document.getElementById('rDeltaD').textContent.trim();
    const tgtRows = document.querySelectorAll('#rTargetsD tbody tr').length;
    const tgt2 = (document.querySelector('#rTargetsD tbody tr:nth-child(1) td:nth-child(2)') || {}).textContent || '';

    // 3) 保存方案(双买独立存储)
    saveCurrentPlan();
    await w(400);
    const plansD = JSON.parse(localStorage.getItem('oc_dual_plans') || '[]');
    const plansO = JSON.parse(localStorage.getItem('oc_options_plans') || '[]');

    // 4) 一键加入记录 → 两条腿同批次 + 直接跳转到交易记录(v50.60: 不再弹成功框)
    const origAlert = window.alert; let alertTxt = '';
    window.alert = m => { alertTxt += String(m) + ';'; };
    await addDualToTradeRecord();
    await w(1400);
    window.alert = origAlert;
    const activeTab = (document.querySelector('#mainTabs .maintab.active') || {}).dataset || {};
    const jumped = document.querySelector('.trades-layout').dataset.tm === 'dual'
      && activeTab.tab === 'tradesDual';
    const g = await (await fetch('/api/trades/groups?mode=dual')).json();
    const grp = (g.groups || []).find(x => String(x.underlying) === U || String(x.underlying) === 'lc');
    let legs = [], futGroups = 0, optGroups = 0;
    if (grp) {
      const d = await (await fetch('/api/trades/detail?mode=dual&underlying=' + grp.underlying
        + '&batch=' + encodeURIComponent(grp.batch || ''))).json();
      legs = (d.holdings || []).map(h => h.contract);
    }
    futGroups = ((await (await fetch('/api/trades/groups?mode=futures')).json()).groups || [])
      .filter(x => String(x.underlying) === 'lc').length;
    optGroups = ((await (await fetch('/api/trades/groups?mode=options')).json()).groups || [])
      .filter(x => String(x.underlying) === 'lc').length;

    // 5) 交易记录第三套 tab
    document.querySelector('#mainTabs .maintab[data-tab="tradesDual"]').click();
    await w(1200);
    const layTm = document.querySelector('.trades-layout').dataset.tm;
    const mainTitle = (document.getElementById('mainTableTitle') || {}).textContent || '';
    const futColHidden = (() => {
      const el = document.querySelector('#tradesArea .fut-only');
      return el ? getComputedStyle(el).display === 'none' : true;
    })();

    const dualTargetTxt = (document.querySelector('#rTargetsD thead th:nth-child(2)') || {}).textContent || '';
    await wipe('dual', 'lc');
    localStorage.removeItem('oc_dual_plans');
    return JSON.stringify({modeBtns, dualShown, optHidden, futHidden, shown,
                           qC, qP, premTxt, deltaTxt, tgtRows, tgt2, dualTargetTxt,
                           noDupPremium, pxBig, pxColored,
                           plansD: plansD.length, plansO: plansO.length,
                           alertHas: /已加入/.test(alertTxt), jumped, legs,
                           futGroups, optGroups, layTm, mainTitle, futColHidden});
  })()`);
  const v59 = JSON.parse(v59Run);
  check('三模式(v50.59): 计算器模式按钮为 期货/期权买方/期权双买',
        JSON.stringify(v59.modeBtns) === JSON.stringify(['futures', 'options', 'dual']), v59Run);
  check('三模式(v50.59): 切到双买后只显示双买字段', v59.dualShown && v59.optHidden && v59.futHidden, v59Run);
  check('双买测算(v50.59): 结果卡显示两腿手数与权利金合计',
        v59.shown && v59.qC === '1' && v59.qP === '1' && /18,300/.test(v59.premTxt),
        'qC=' + v59.qC + ' qP=' + v59.qP + ' prem=' + v59.premTxt);
  check('双买UI(v50.64): 去掉与止盈表重复的「两腿权利金合计」条', v59.noDupPremium === true,
        'rPremiumD 仍存在=' + (v59.noDupPremium === false));
  check('双买UI(v50.64): 止盈目标价为大号粗体 + 醒目配色(不再与明细同字号)',
        v59.pxBig === true && v59.pxColored === true,
        '大号粗体=' + v59.pxBig + ' 配色=' + v59.pxColored);
  check('双买测算(v50.59): 显示净 delta 与「中性」标记',
        /0/.test(v59.deltaTxt) && /中性/.test(v59.deltaTxt), v59.deltaTxt);
  check('双买测算(v50.59): 输出 2/3/4/5 四档目标价位',
        v59.tgtRows === 4 && /36,600/.test(v59.tgt2), v59.tgt2);
  check('双买方案(v50.59): 存进独立的 oc_dual_plans, 不挤占期权买方',
        v59.plansD === 1 && v59.plansO === 0, 'dual=' + v59.plansD + ' opt=' + v59.plansO);
  check('双买加入记录(v50.60): 两条腿写进同一条记录 + 直接跳到交易记录(不弹成功框)',
        v59.legs.length === 2 && v59.jumped === true && v59.alertHas === false,
        JSON.stringify({legs: v59.legs, jumped: v59.jumped, alert: v59.alertHas}));
  check('双买目标价口径(v50.60): 表头按「元/吨」等价格口径给出', /元\//.test(v59.dualTargetTxt),
        v59.dualTargetTxt);
  check('双买记录隔离(v50.59): 不出现在期货/期权买方列表',
        v59.futGroups === 0 && v59.optGroups === 0, 'fut=' + v59.futGroups + ' opt=' + v59.optGroups);
  check('交易记录第三套(v50.59): tab 切到 dual + 标题 + 隐藏期货专属列',
        v59.layTm === 'dual' && v59.mainTitle === '期权双买' && v59.futColHidden === true,
        'tm=' + v59.layTm + ' title=' + v59.mainTitle + ' futHidden=' + v59.futColHidden);

  // ===== v50.60: 模式框一行 / 期权价格联动 / 结果按模式隔离 / 双买标的带年月 / 双买无监控池 =====
  const v60Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const tab = t => document.querySelector('#mainTabs .maintab[data-tab="' + t + '"]').click();
    const shown = id => !q(id).classList.contains('hidden');
    const post = (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
    const choose = (inputId, code) => {
      const el = q(inputId);
      el.value = code; el.dispatchEvent(new Event('input'));
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
    };

    // ---- A) 模式选择框: 一行 3 个 ----
    tab('calc'); await w(450);
    const modes = [...document.querySelectorAll('.modes > .mode')];
    const modeCols = getComputedStyle(document.querySelector('.modes')).gridTemplateColumns.split(' ').length;
    const modeTops = modes.map(m => Math.round(m.getBoundingClientRect().top));
    const modeOneRow = (new Set(modeTops).size === 1) && modeCols === 3;
    const modeH = Math.round(modes[0].getBoundingClientRect().height);

    // ---- B) 期权价格 ⇄ 每手权利金 双向联动 ----
    document.querySelector('.mode[data-mode="dual"]').click(); await w(400);
    choose('cSearchD', 'lc'); await w(700);
    const lcRow = CONTRACTS.find(x => String(x.code).toLowerCase() === 'lc') || {};
    const lcMult = lcRow.opt_mult || lcRow.mult || 1;
    const cp = q('callPrice'), cpr = q('callPremium');
    cp.value = '2500'; cp.dispatchEvent(new Event('input')); await w(300);
    const syncedPrem = cpr.value;
    cpr.value = '4000'; cpr.dispatchEvent(new Event('input')); await w(300);
    const syncedPrice = cp.value;
    const unitTxt = (q('callPriceUnit') || {}).textContent || '';

    // ---- C) 双买算出来 ----
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    await w(1000);
    const dualQtyC = q('rQtyCD').textContent.trim();
    const dualShownNow = shown('resultD');

    // ---- D) 切期货算一笔 → 双买结果不能残留在期货结果下面 ----
    document.querySelector('.mode[data-mode="futures"]').click(); await w(400);
    choose('cSearch', 'rb'); await w(800);
    q('equity').value = '50'; q('equity').dispatchEvent(new Event('input'));
    q('entry').value = '3200'; q('stop').value = '3100'; q('target').value = '3450';
    onInput(); await w(1000);
    const futLots = q('rLotsF').textContent.trim();
    const afterFut = {f: shown('resultF'), o: shown('resultO'), d: shown('resultD')};

    // 切到期权买方 → 期货/双买结果都必须清掉
    document.querySelector('.mode[data-mode="options"]').click(); await w(600);
    const afterOpt = {f: shown('resultF'), d: shown('resultD')};

    // 切回期货 → 恢复它自己的结果(手数一致)
    document.querySelector('.mode[data-mode="futures"]').click(); await w(600);
    const backFut = {f: shown('resultF'), d: shown('resultD'), lots: q('rLotsF').textContent.trim()};

    // 切回双买 → 恢复双买结果
    document.querySelector('.mode[data-mode="dual"]').click(); await w(600);
    const backDual = {d: shown('resultD'), f: shown('resultF'), qC: q('rQtyCD').textContent.trim()};

    // ---- E) 交易记录-期权双买: 标的带年月 + 不显示监控池 ----
    const B = 'B20261007000060dd';
    for (const leg of [['C','lc2611-C-144000',9800], ['P','lc2611-P-120000',8500]]) {
      await post('/api/trades/upsert', {mode:'dual', underlying:'lc', contract: leg[1], batch: B,
        op_type:'open', direction:'buy', call_put: leg[0], open_date:'2026-10-07',
        open_price: leg[2], qty: 1, premium: leg[2]});
    }
    tab('tradesDual'); await w(1400);
    const firstCell = (document.querySelector('#tradesTable tbody tr td') || {}).textContent || '';
    const poolDisp = (q('poolCard') || {style:{}}).style.display;
    await TradeUI.loadDetail('lc', B); await w(900);
    const tdTitle = (q('tdTitle') || {}).textContent || '';
    TradeUI.closeDetail(); await w(300);
    const dd = await (await fetch('/api/trades/detail?mode=dual&underlying=lc&batch=' + encodeURIComponent(B))).json();
    for (const op of (dd.operations || [])) {
      await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
    }
    return JSON.stringify({modeCols, modeOneRow, modeH, lcMult, syncedPrem, syncedPrice, unitTxt,
                           dualQtyC, dualShownNow, futLots, afterFut, afterOpt, backFut, backDual,
                           firstCell, poolDisp, tdTitle});
  })()`);
  const v60 = JSON.parse(v60Run);
  check('模式选择框(v50.60): 三个模式排成一行(3 列)', v60.modeOneRow === true,
        'cols=' + v60.modeCols + ' h=' + v60.modeH);
  check('双买价格联动(v50.60): 改动期权价格或每手权利金, 另一个自动换算',
        parseFloat(v60.syncedPrem) === 2500 * v60.lcMult && parseFloat(v60.syncedPrice) === 4000 / v60.lcMult,
        'mult=' + v60.lcMult + ' 2500→' + v60.syncedPrem + ' 4000→' + v60.syncedPrice);
  check('双买价格联动(v50.60): 价格单位随品种显示「元/吨」', /元\//.test(v60.unitTxt), v60.unitTxt);
  check('结果隔离(v50.60): 期货模式算完后不再显示双买结果',
        v60.afterFut.f === true && v60.afterFut.d === false && v60.afterFut.o === false,
        JSON.stringify(v60.afterFut));
  check('结果隔离(v50.60): 切到期权买方, 期货与双买结果都清空',
        v60.afterOpt.f === false && v60.afterOpt.d === false, JSON.stringify(v60.afterOpt));
  check('结果恢复(v50.60): 切回期货模式, 恢复原来的手数结果',
        v60.backFut.f === true && v60.backFut.d === false
        && v60.backFut.lots === v60.futLots && parseFloat(v60.futLots) > 0,
        'lots=' + v60.futLots + '→' + v60.backFut.lots);
  check('结果恢复(v50.60): 切回双买模式, 恢复原来的双买结果',
        v60.backDual.d === true && v60.backDual.f === false && v60.backDual.qC === v60.dualQtyC,
        JSON.stringify({d: v60.backDual.d, qC: v60.backDual.qC}));
  check('双买标的带年月(v50.60): 主表开仓标的显示「lc 2611」', /lc\s*2611/.test(v60.firstCell),
        v60.firstCell.replace(/\s+/g, ' ').trim().slice(0, 60));
  check('双买标的带年月(v50.60): 详情页标题也带年月', /lc\s*2611/.test(v60.tdTitle), v60.tdTitle);
  check('双买无监控池(v50.60): 期权双买模式下监控池整块隐藏', v60.poolDisp === 'none', 'display=' + v60.poolDisp);

  // ===== v50.61: 双买 IV / IV 百分位 · 侧栏顺序 · 只留一个联系作者按钮 =====
  const v61Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const tab = t => document.querySelector('#mainTabs .maintab[data-tab="' + t + '"]').click();
    const post = (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});

    // A) 侧栏顺序: 计算器 / 期权买方 / 期权双买 / 期货 / 资金曲线
    const order = [...document.querySelectorAll('#mainTabs .maintab')].map(x => x.dataset.tab);
    // B) 联系作者只保留侧栏那一个按钮(左下角浮动按钮已删)
    const floatBtn = !!q('floatingContact');
    const sideContact = !!q('btnContact');

    // C) 双买表单有 IV / IV 百分位, 填完结果卡里显示
    tab('calc'); await w(450);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(450);
    const hasIv = !!q('dualIv') && !!q('dualIvPct');
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    const el = q('cSearchD'); el.value = 'lc'; el.dispatchEvent(new Event('input'));
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); await w(800);
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    q('dualIv').value = '32.5'; q('dualIv').dispatchEvent(new Event('input'));
    q('dualIvPct').value = '92'; q('dualIvPct').dispatchEvent(new Event('input'));
    await w(1200);
    const ivInCard = (q('rIvD') || {}).textContent || '';
    const ivPctInCard = (q('rIvPctD') || {}).textContent || '';

    // D) 一键加入记录 → 详情顶部 meta 显示 IV, 接口也带 iv/iv_pct
    await addDualToTradeRecord(); await w(1800);
    const meta = (q('tdMeta') || {}).textContent || '';
    const g = await (await fetch('/api/trades/groups?mode=dual')).json();
    const grp = (g.groups || []).find(x => String(x.underlying) === 'lc');
    let apiIv = null, apiIvPct = null, keepIv = null;
    if (grp) {
      const bq = 'mode=dual&underlying=lc&batch=' + encodeURIComponent(grp.batch || '');
      const dd = await (await fetch('/api/trades/detail?' + bq)).json();
      apiIv = dd.iv; apiIvPct = dd.iv_pct;
      // 编辑开仓(改手数, 不传 IV) → IV 必须保留
      const openOp = (dd.operations || []).filter(o => o.op_type === 'open')[0];
      if (openOp) {
        await post('/api/trades/upsert', {id: openOp.id, mode:'dual', underlying:'lc', contract: openOp.contract,
          batch: grp.batch, op_type:'open', direction:'buy', call_put: openOp.call_put,
          open_date: openOp.open_date, open_price: openOp.open_price, qty: 3, premium: openOp.premium});
        const dd2 = await (await fetch('/api/trades/detail?' + bq)).json();
        keepIv = dd2.iv;
      }
      const dd3 = await (await fetch('/api/trades/detail?' + bq)).json();
      for (const op of (dd3.operations || [])) {
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
      }
    }
    localStorage.removeItem('oc_dual_plans');
    return JSON.stringify({order, floatBtn, sideContact, hasIv, ivInCard, ivPctInCard,
                           metaHasIv: /IV/.test(meta), apiIv, apiIvPct, keepIv});
  })()`);
  const v61 = JSON.parse(v61Run);
  check('侧栏顺序(v50.61): 计算器 / 期权买方 / 期权双买 / 期货 / 资金曲线',
        JSON.stringify(v61.order) === JSON.stringify(['calc', 'trades', 'tradesDual', 'tradesFut', 'funds']),
        JSON.stringify(v61.order));
  check('联系作者(v50.61): 只保留一个按钮(左下角浮动按钮已删)',
        v61.floatBtn === false && v61.sideContact === true,
        'float=' + v61.floatBtn + ' side=' + v61.sideContact);
  check('双买 IV 输入(v50.61): 表单有 IV / IV 百分位, 结果卡同步显示',
        v61.hasIv === true && /32\.5/.test(v61.ivInCard) && /92/.test(v61.ivPctInCard),
        'iv=' + v61.ivInCard + ' pct=' + v61.ivPctInCard);
  check('双买 IV 记录(v50.61): 加入记录后详情顶部显示 IV 与百分位',
        v61.metaHasIv === true && v61.apiIv === 32.5 && v61.apiIvPct === 92,
        JSON.stringify({meta: v61.metaHasIv, iv: v61.apiIv, pct: v61.apiIvPct}));
  check('双买 IV 保真(v50.61): 编辑开仓(不传 IV)后 IV 不丢', v61.keepIv === 32.5, 'iv=' + v61.keepIv);

  // ===== v50.62: delta 自动负号/4 位小数 · 操作表去掉「目标」· 双买主表新建开仓(两腿) · 模式框文字放大 =====
  const v62Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const tab = t => document.querySelector('#mainTabs .maintab[data-tab="' + t + '"]').click();
    const money = v => Number(v || 0);

    // ---- A) 计算器: put delta 自动带负号 + delta 4 位小数 ----
    tab('calc'); await w(450);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(400);
    const pdl = q('putDelta');
    pdl.value = '0.3'; pdl.dispatchEvent(new Event('input')); await w(250);
    const putAutoNeg = pdl.value;
    pdl.dispatchEvent(new Event('blur')); await w(150);
    const putBlur = pdl.value;
    const cdl = q('callDelta');
    cdl.value = '0.3'; cdl.dispatchEvent(new Event('blur')); await w(150);
    const callBlur = cdl.value;

    // 填完整 → 结果卡净 delta 也应是 4 位小数
    const el = q('cSearchD'); el.value = 'lc'; el.dispatchEvent(new Event('input'));
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); await w(800);
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3000';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3000';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    await w(1000);
    const netDeltaTxt = q('rDeltaD').textContent.trim();

    // ---- B) 模式选择框: 文字放大但仍是一行 ----
    const modeFs = parseFloat(getComputedStyle(document.querySelector('.mode')).fontSize);
    const modeSmallFs = parseFloat(getComputedStyle(document.querySelector('.mode small')).fontSize);
    const modeTops = [...document.querySelectorAll('.modes > .mode')]
      .map(m => Math.round(m.getBoundingClientRect().top));

    // ---- C) 双买主表「新建开仓」= 一次两条腿 ----
    tab('tradesDual'); await w(1000);
    q('btnNewOpen').click(); await w(500);
    const dlg = q('dualModalBg');
    const dlgShown = !!dlg && !dlg.classList.contains('hidden');
    const hasLegFields = !!(q('dmCallPrice') && q('dmPutPrice') && q('dmCallQty') && q('dmPutQty')
                            && q('dmIv') && q('dmIvPct'));
    const noTargetInDlg = !q('dmTargetDelta');
    q('dmUnderlying').value = 'lc'; q('dmUnderlying').dispatchEvent(new Event('input'));
    q('dmMonth').value = '2611';
    q('dmCallStrike').value = '144000'; q('dmCallDelta').value = '0.3';
    q('dmPutStrike').value = '120000'; q('dmPutDelta').value = '0.3';
    q('dmPutDelta').dispatchEvent(new Event('input'));   // 故意填正数 → 应自动取负
    q('dmCallPrice').value = '9800'; q('dmCallPrice').dispatchEvent(new Event('input'));
    q('dmPutPrice').value = '8500'; q('dmPutPrice').dispatchEvent(new Event('input'));
    q('dmCallQty').value = '2'; q('dmCallQty').dispatchEvent(new Event('input'));
    q('dmPutQty').value = '3'; q('dmPutQty').dispatchEvent(new Event('input'));
    q('dmIv').value = '31.25'; q('dmIvPct').value = '88';
    await w(250);
    const hintTxt = q('dmHint').textContent.replace(/\\s+/g, ' ').trim();
    const dlgPutDelta = q('dmPutDelta').value;
    const savedOk = await TradeUI.submitDualModal();
    await w(2400);          // ⚠ 保存后会 refresh + loadDetail, 等不够时操作表还是空态行(旧断言会抛异常)
    const gd = await (await fetch('/api/trades/groups?mode=dual')).json();
    const grp = (gd.groups || []).find(x => String(x.underlying) === 'lc');
    let legs = [], ivIn = null, premSum = 0, opNames = [], deltaCell = '';
    if (grp) {
      const bq = 'mode=dual&underlying=lc&batch=' + encodeURIComponent(grp.batch || '');
      const dd = await (await fetch('/api/trades/detail?' + bq)).json();
      legs = (dd.operations || []).filter(o => o.op_type === 'open')
        .map(o => ({c: o.contract, q: o.qty, p: o.open_price, d: o.open_delta}));
      ivIn = dd.iv;
      premSum = (dd.holdings || []).reduce((s, h) => s + money(h.premium), 0);
      // 详情操作表: 表头不该再有「目标」, delta 显示 4 位小数
      const opsTbl = [...document.querySelectorAll('#tradeDetailPanel table.tbl')][1];
      opNames = [...opsTbl.tHead.rows[0].children].filter(c => c.offsetParent !== null)
        .map(c => c.textContent.trim());
      const row = opsTbl.tBodies[0].rows[0];
      // ⚠ 操作表还没渲染完时第一行可能是「暂无操作记录」的空态行(只有 1 个 td) → 不能直接 .trim()
      deltaCell = (row && row.children[3] && row.children[3].textContent || '').trim();
      for (const op of (dd.operations || [])) {
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
      }
    }
    return JSON.stringify({putAutoNeg, putBlur, callBlur, netDeltaTxt,
                           modeFs, modeSmallFs, modeTopOne: new Set(modeTops).size === 1,
                           dlgShown, hasLegFields, noTargetInDlg, hintTxt, dlgPutDelta,
                           savedOk, legs, ivIn, premSum, opNames, deltaCell});
  })()`);
  const v62 = JSON.parse(v62Run);
  check('delta 负号(v50.62): 计算器 put delta 填正数自动带上「-」',
        v62.putAutoNeg === '-0.3' && v62.putBlur === '-0.3000',
        'input=' + v62.putAutoNeg + ' blur=' + v62.putBlur);
  check('delta 小数位(v50.62): delta 统一保留 4 位小数',
        v62.callBlur === '0.3000' && /^-?\d+\.\d{4}/.test(v62.netDeltaTxt),
        'call=' + v62.callBlur + ' net=' + v62.netDeltaTxt);
  check('模式选择框(v50.62): 文字放大(主 15.5px / 副 11.5px)但仍一行',
        v62.modeFs >= 15 && v62.modeSmallFs >= 11 && v62.modeTopOne === true,
        'fs=' + v62.modeFs + '/' + v62.modeSmallFs + ' oneRow=' + v62.modeTopOne);
  check('操作表(v50.62): 详情里已去掉「目标」列',
        v62.opNames.indexOf('目标') < 0 && v62.opNames.indexOf('delta') >= 0,
        JSON.stringify(v62.opNames));
  check('操作表(v50.62): delta 列显示 4 位小数', /^-?\d+\.\d{4}$/.test(v62.deltaCell), v62.deltaCell);
  check('双买新建开仓(v50.62): 主表按钮打开两腿弹窗(含 IV / 手数, 无目标 delta)',
        v62.dlgShown === true && v62.hasLegFields === true && v62.noTargetInDlg === true,
        JSON.stringify({shown: v62.dlgShown, legs: v62.hasLegFields, noTarget: v62.noTargetInDlg}));
  check('双买新建开仓(v50.62): 弹窗提示按期权价格算权利金',
        /乘数/.test(v62.hintTxt) && /合计/.test(v62.hintTxt), v62.hintTxt.slice(0, 120));
  check('双买新建开仓(v50.62): put delta 填正数自动取负', v62.dlgPutDelta === '-0.3', v62.dlgPutDelta);
  check('双买新建开仓(v50.62): 一次写入两条腿(手数/权利金/IV 都对)',
        v62.savedOk === true && v62.legs.length === 2
        && v62.legs.some(l => l.c === 'lc2611-C-144000' && l.q === 2 && l.p === 9800)
        && v62.legs.some(l => l.c === 'lc2611-P-120000' && l.q === 3 && l.p === 8500 && l.d === -0.3)
        && v62.premSum === 2 * 9800 + 3 * 8500 && v62.ivIn === 31.25,
        JSON.stringify(v62.legs) + ' prem=' + v62.premSum + ' iv=' + v62.ivIn);

  // ===== v50.63: 双买详情「止盈价测算」(持仓汇总上方, 计算器来的 / 手动新建的都有) =====
  const v63Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const tab = t => document.querySelector('#mainTabs .maintab[data-tab="' + t + '"]').click();
    const box = () => q('tdDualTargets');
    const boxTxt = () => ((box() || {}).textContent || '').replace(/\\s+/g, ' ').trim();
    const boxRows = () => box() ? box().querySelectorAll('tbody tr').length : 0;
    const wipeDual = async () => {
      const g = await (await fetch('/api/trades/groups?mode=dual')).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=dual&underlying=' + it.underlying
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) {
          await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: op.id})});
        }
      }
    };

    // ---- A) 手动新建(两腿弹窗) → 详情出现止盈价测算, 且在「当前持仓」标题上方 ----
    tab('tradesDual'); await w(1000);
    q('btnNewOpen').click(); await w(400);
    q('dmUnderlying').value = 'lc'; q('dmUnderlying').dispatchEvent(new Event('input'));
    q('dmMonth').value = '2611';
    q('dmCallStrike').value = '144000'; q('dmCallDelta').value = '0.3';
    q('dmPutStrike').value = '120000'; q('dmPutDelta').value = '-0.3';
    q('dmCallPrice').value = '9800'; q('dmCallPrice').dispatchEvent(new Event('input'));
    q('dmPutPrice').value = '8500'; q('dmPutPrice').dispatchEvent(new Event('input'));
    q('dmCallQty').value = '2'; q('dmCallQty').dispatchEvent(new Event('input'));
    q('dmPutQty').value = '3'; q('dmPutQty').dispatchEvent(new Event('input'));
    await w(250);
    await TradeUI.submitDualModal();
    // ⚠ 固定等待 1900ms 在机器忙时不够 → 这条用例曾偶发假红两次;
    //   改成轮询「等到真出行为止」(最多 6s, 一出就往下走)
    for (let i = 0; i < 40 && boxRows() < 1; i++) await w(150);
    const mRows = boxRows();
    const mTxt = boxTxt();
    const mCall = box() && box().querySelector('tbody tr td:nth-child(2)')
      ? box().querySelector('tbody tr td:nth-child(2)').textContent.trim() : '';
    const aboveHoldings = (() => {
      if (!box() || !box().parentElement) return false;
      const h3 = [...box().parentElement.querySelectorAll('h3')].find(h => /当前持仓/.test(h.textContent));
      if (!h3) return false;
      return !!(box().compareDocumentPosition(h3) & Node.DOCUMENT_POSITION_FOLLOWING);
    })();
    // 期权买方模式下这块必须隐藏
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await w(1200);
    const hiddenInOptions = !box() || box().offsetParent === null;
    await wipeDual();

    // ---- B) 从计算器「加入记录」来的 → 同样有止盈价测算 ----
    tab('calc'); await w(500);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(400);
    const el = q('cSearchD'); el.value = 'lc'; el.dispatchEvent(new Event('input'));
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); await w(800);
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3000';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3000';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    q('dualIv').value = '32.5'; q('dualIv').dispatchEvent(new Event('input'));
    await w(1100);
    await addDualToTradeRecord();
    await w(2000);
    const cRows = boxRows();
    const cTxt = boxTxt();
    await wipeDual();
    localStorage.removeItem('oc_dual_plans');
    return JSON.stringify({mRows, mTxt: mTxt.slice(0, 160), mCall, aboveHoldings, hiddenInOptions,
                           cRows, cTxt: cTxt.slice(0, 160)});
  })()`);
  const v63 = JSON.parse(v63Run);
  check('止盈价测算(v50.63): 手动新建的双买记录, 详情里出现 4 档止盈目标价',
        v63.mRows === 4 && /2×/.test(v63.mTxt) && /Call 目标价/.test(v63.mTxt), v63.mTxt);
  check('止盈价测算(v50.63): 数值口径 = 2×总权利金 ÷ 该腿手数(2C/3P → Call 每手 45,100)',
        /45,100/.test(v63.mCall), v63.mCall);
  check('止盈价测算(v50.63): 位置在「当前持仓」标题上方', v63.aboveHoldings === true, String(v63.aboveHoldings));
  check('止盈价测算(v50.63): 期权买方模式不显示这一块', v63.hiddenInOptions === true, String(v63.hiddenInOptions));
  check('止盈价测算(v50.63): 从计算器「加入记录」来的记录同样有止盈价测算',
        v63.cRows === 4 && /2×/.test(v63.cTxt) && /36,600/.test(v63.cTxt), v63.cTxt);

  // ===== v50.65: 双买换品种 → 开仓参数与测算结果一起清空 =====
  const v65Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const shown = id => !q(id).classList.contains('hidden');
    const choose = (inputId, code) => {
      const el = q(inputId);
      el.value = code; el.dispatchEvent(new Event('input'));
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
    };
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(500);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(500);
    choose('cSearchD', 'lc'); await w(900);
    // 填满一套参数
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3000';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3000';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    q('dualIv').value = '32.5'; q('dualIvPct').value = '92';
    onInput(); await w(1300);
    const beforeShown = shown('resultD');
    const beforeQty = q('rQtyCD').textContent.trim();
    const hintLC = (q('callLegHint') || {}).textContent || '';
    // 同品种重选 → 不能清空(避免误伤)
    choose('cSearchD', 'lc'); await w(800);
    const sameKeep = {price: q('callPrice').value, strike: q('callStrike').value,
                      iv: q('dualIv').value, shown: shown('resultD')};
    // 换品种 → 开仓参数 + 结果一起清
    choose('cSearchD', 'rb'); await w(1500);
    const ids = ['dualMonth','callStrike','callDelta','callPrice','callPremium',
                 'putStrike','putDelta','putPrice','putPremium','dualIv','dualIvPct'];
    const leftOver = ids.filter(id => (q(id).value || '') !== '');
    const after = {d: shown('resultD'), empty: shown('empty')};
    const hintRB = (q('callLegHint') || {}).textContent || '';
    // 切走再切回双买 → 不能把清空前的旧结果翻出来
    document.querySelector('.mode[data-mode="futures"]').click(); await w(500);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(800);
    const backStale = {d: shown('resultD'), empty: shown('empty')};
    const caches = {D: !!lastCalcD, O: !!lastCalcO, F: !!lastCalcF};
    // ---- 期货同理: 手动清空价格后切走再切回, 不能翻出旧结果 ----
    document.querySelector('.mode[data-mode="futures"]').click(); await w(500);
    choose('cSearch', 'rb'); await w(800);
    q('equity').value = '50'; q('equity').dispatchEvent(new Event('input'));
    q('entry').value = '3200'; q('stop').value = '3100'; q('target').value = '3450';
    onInput(); await w(1200);
    const futBefore = shown('resultF');
    q('btnClearPrices').click(); await w(700);
    const futAfterClear = {f: shown('resultF'), empty: shown('empty')};
    document.querySelector('.mode[data-mode="options"]').click(); await w(500);
    document.querySelector('.mode[data-mode="futures"]').click(); await w(800);
    const futBack = {f: shown('resultF'), empty: shown('empty')};
    return JSON.stringify({beforeShown, beforeQty, hintLC, sameKeep, leftOver, after, hintRB,
                           backStale, caches, futBefore, futAfterClear, futBack});
  })()`);
  const v65 = JSON.parse(v65Run);
  check('双买换品种(v50.65): 先算出一条结果(前置条件)',
        v65.beforeShown === true && parseFloat(v65.beforeQty) > 0, v65Run.slice(0, 200));
  check('双买换品种(v50.65): 同品种重选不清空(不该误伤)',
        v65.sameKeep.price === '9800' && v65.sameKeep.strike === '144000'
        && v65.sameKeep.iv === '32.5' && v65.sameKeep.shown === true, JSON.stringify(v65.sameKeep));
  check('双买换品种(v50.65): 换了品种 → 合约年月/两腿4组参数/IV 全部清空',
        v65.leftOver.length === 0, '残留=' + JSON.stringify(v65.leftOver));
  check('双买换品种(v50.65): 测算结果同时清掉并回到空态',
        v65.after.d === false && v65.after.empty === true, JSON.stringify(v65.after));
  check('双买换品种(v50.65): 两腿提示里的乘数跟着新标的刷新',
        v65.hintLC.indexOf('乘数 1 ') >= 0 && v65.hintRB.indexOf('乘数 10 ') >= 0,
        'lc=' + v65.hintLC.slice(0, 26) + ' | rb=' + v65.hintRB.slice(0, 26));
  check('双买换品种(v50.65): 切走再切回双买, 不会翻出刚清掉的旧结果',
        v65.backStale.d === false && v65.backStale.empty === true, JSON.stringify(v65.backStale));
  check('双买换品种(v50.65): 该模式测算缓存已作废(不留旧值)',
        v65.caches.D === false, JSON.stringify(v65.caches));
  check('结果缓存(v50.65): 期货清空价格后切走再切回, 不再翻出旧结果(同一套修法)',
        v65.futBefore === true && v65.futAfterClear.f === false && v65.futAfterClear.empty === true
        && v65.futBack.f === false && v65.futBack.empty === true,
        JSON.stringify({before: v65.futBefore, cleared: v65.futAfterClear, back: v65.futBack}));

  // ===== v50.66: Call / Put 用颜色分开(计算器两腿卡片 + 结果手数卡 + 止盈表, 交易记录同样) =====
  const v66Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const tab = t => document.querySelector('#mainTabs .maintab[data-tab="' + t + '"]').click();
    const cs = el => getComputedStyle(el);
    const choose = (inputId, code) => { const el = q(inputId);
      el.value = code; el.dispatchEvent(new Event('input'));
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); };
    const post = (p) => fetch('/api/trades/upsert', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)});

    tab('calc'); await w(500);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(500);
    choose('cSearchD', 'lc'); await w(900);
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3000';
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3000';
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    onInput(); await w(1300);
    const badgeC = document.querySelector('#dualFields .leg-card.call .cp-badge');
    const badgeP = document.querySelector('#dualFields .leg-card.put .cp-badge');
    const form = {
      cards: !!document.querySelector('#dualFields .leg-card.call') && !!document.querySelector('#dualFields .leg-card.put'),
      callColor: badgeC ? cs(badgeC).color : '',
      putColor: badgeP ? cs(badgeP).color : '',
    };
    const nC = document.querySelector('.bignum.cp-call .n'), nP = document.querySelector('.bignum.cp-put .n');
    const hud = {callColor: nC ? cs(nC).color : '', putColor: nP ? cs(nP).color : ''};
    const thC = document.querySelector('#rTargetsD th.cp-call'), thP = document.querySelector('#rTargetsD th.cp-put');
    const tdC = document.querySelector('#rTargetsD td.cp-call'), tdP = document.querySelector('#rTargetsD td.cp-put');
    const tbl = {
      callColor: thC ? cs(thC).color : '', putColor: thP ? cs(thP).color : '',
      callBg: thC ? cs(thC).backgroundColor : '', putBg: thP ? cs(thP).backgroundColor : '',
      callTdBg: tdC ? cs(tdC).backgroundColor : '', putTdBg: tdP ? cs(tdP).backgroundColor : '',
    };
    const dualCss = document.querySelector('#rPriceD') ? q('rPriceD').innerHTML.indexOf('cp-call') >= 0 : false;

    // 交易记录-期权双买详情里的同一张表
    const B = 'B20261007000666cc';
    for (const leg of [['C','lc2611-C-144000',9800], ['P','lc2611-P-120000',8500]]) {
      await post({mode:'dual', underlying:'lc', contract: leg[1], batch: B,
        op_type:'open', direction:'buy', call_put: leg[0], open_date:'2026-10-07',
        open_price: leg[2], qty: 1, premium: leg[2]});
    }
    tab('tradesDual'); await w(1500);
    await TradeUI.loadDetail('lc', B); await w(1300);
    const rThC = document.querySelector('#tdDualTargets th.cp-call');
    const rThP = document.querySelector('#tdDualTargets th.cp-put');
    const rec = {hasCall: !!rThC, hasPut: !!rThP,
                 callColor: rThC ? cs(rThC).color : '', putColor: rThP ? cs(rThP).color : '',
                 callBg: rThC ? cs(rThC).backgroundColor : ''};
    TradeUI.closeDetail(); await w(400);
    const d = await (await fetch('/api/trades/detail?mode=dual&underlying=lc&batch=' + encodeURIComponent(B))).json();
    for (const op of (d.operations || [])) {
      await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: op.id})});
    }
    return JSON.stringify({form, hud, tbl, dualCss, rec});
  })()`);
  const v66 = JSON.parse(v66Run);
  check('Call/Put 配色(v50.66): 开仓参数拆成两张腿卡片, 颜色分得开',
        v66.form.cards === true && v66.form.callColor !== v66.form.putColor
        && v66.form.callColor !== '', JSON.stringify(v66.form));
  check('Call/Put 配色(v50.66): 结果的手数卡数字按腿上色(Call ≠ Put)',
        v66.hud.callColor !== v66.hud.putColor && v66.hud.callColor !== '', JSON.stringify(v66.hud));
  check('Call/Put 配色(v50.66): 止盈目标表两列表头带腿色底纹+文字色',
        v66.tbl.callColor !== v66.tbl.putColor
        && v66.tbl.callBg !== v66.tbl.putBg
        && v66.tbl.callBg !== 'rgba(0, 0, 0, 0)',
        JSON.stringify(v66.tbl));
  check('Call/Put 配色(v50.66): 止盈目标表两列正文也有浅底(分量腿)',
        v66.tbl.callTdBg !== v66.tbl.putTdBg && v66.tbl.callTdBg !== 'rgba(0, 0, 0, 0)',
        'call=' + v66.tbl.callTdBg + ' put=' + v66.tbl.putTdBg);
  check('Call/Put 配色(v50.66): 结果里「期权价格 / 每手权利金」两腿值也分色',
        v66.dualCss === true, String(v66.dualCss));
  check('Call/Put 配色(v50.66): 交易记录-双买详情的止盈表同样分色(同一套样式)',
        v66.rec.hasCall === true && v66.rec.hasPut === true
        && v66.rec.callColor !== v66.rec.putColor && v66.rec.callBg !== 'rgba(0, 0, 0, 0)',
        JSON.stringify(v66.rec));

  // ===== v50.67: 双买开仓价必须存「期权价格」, 不能乘合约乘数 =====
  //    ⚠ 必须用乘数 > 1 的品种才验得出来(lc 乘数=1, 价格与每手权利金恰好相等, 之前一直看不出来)
  const v67Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const choose = (inputId, code) => { const el = q(inputId);
      el.value = code; el.dispatchEvent(new Event('input'));
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); };
    const wipe = async () => {
      const g = await (await fetch('/api/trades/groups?mode=dual')).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=dual&underlying=' + it.underlying
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) {
          await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({id: op.id})});
        }
      }
    };
    const legsOf = async (u, b) => {
      const d = await (await fetch('/api/trades/detail?mode=dual&underlying=' + encodeURIComponent(u)
        + '&batch=' + encodeURIComponent(b || ''))).json();
      const out = {};
      for (const x of (d.holdings || [])) {
        out[String(x.call_put).toUpperCase()] = {px: x.open_price, q: x.qty,
                                                 premEach: (x.premium || 0) / (x.qty || 1)};
      }
      return out;
    };
    await wipe();
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(400);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(500);
    choose('cSearchD', 'sh'); await w(900);
    const picked = selCode.D;
    const mult = dualOptMult();
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2612';
    q('callStrike').value = '1880'; q('callDelta').value = '0.4000';
    q('callPrice').value = '42'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '1820'; q('putDelta').value = '-0.3920';
    q('putPrice').value = '39'; q('putPrice').dispatchEvent(new Event('input'));
    await w(300);
    const synced = {cp: q('callPremium').value, pp: q('putPremium').value};
    onInput(); await w(1300);
    // A) 计算器「加入记录」
    await addDualToTradeRecord(); await w(2200);
    const hA = await legsOf(TradeUI.detail.underlying, TradeUI.detail.batch);
    // B) 交易记录里的两腿新建弹窗
    TradeUI.closeDetail(); await w(300);
    q('btnNewOpen').click(); await w(500);
    q('dmUnderlying').value = 'sh'; q('dmUnderlying').dispatchEvent(new Event('input'));
    q('dmMonth').value = '2701';
    q('dmCallStrike').value = '1900'; q('dmCallDelta').value = '0.35';
    q('dmCallPrice').value = '50'; q('dmCallPrice').dispatchEvent(new Event('input'));
    q('dmPutStrike').value = '1800'; q('dmPutDelta').value = '-0.35';
    q('dmPutPrice').value = '45'; q('dmPutPrice').dispatchEvent(new Event('input'));
    q('dmCallQty').value = '1'; q('dmCallQty').dispatchEvent(new Event('input'));
    q('dmPutQty').value = '1'; q('dmPutQty').dispatchEvent(new Event('input'));
    await w(300);
    const okB = await TradeUI.submitDualModal(); await w(1900);
    const hB = await legsOf(TradeUI.detail.underlying, TradeUI.detail.batch);
    await wipe();
    return JSON.stringify({picked, mult, synced, hA, hB, okB});
  })()`);
  const v67 = JSON.parse(v67Run);
  check('双买价格口径(v50.67): 选到乘数>1 的品种(烧碱 SH, 乘数 30)',
        String(v67.picked).toLowerCase() === 'sh' && v67.mult === 30,
        'code=' + v67.picked + ' mult=' + v67.mult);
  check('双买价格口径(v50.67): 期权价格 42 → 每手权利金自动 1260(×30)',
        parseFloat(v67.synced.cp) === 42 * 30 && parseFloat(v67.synced.pp) === 39 * 30,
        JSON.stringify(v67.synced));
  check('双买价格口径(v50.67): 计算器加入记录 → 价格存期权价格 42 / 39(没乘乘数)',
        v67.hA && v67.hA.C && v67.hA.P
        && Math.abs(v67.hA.C.px - 42) < 0.01 && Math.abs(v67.hA.P.px - 39) < 0.01,
        JSON.stringify(v67.hA));
  check('双买价格口径(v50.67): 同一张表里「权利金」仍是每手 1260 / 1170(钱没变)',
        Math.abs(v67.hA.C.premEach - 1260) < 0.5 && Math.abs(v67.hA.P.premEach - 1170) < 0.5,
        JSON.stringify(v67.hA));
  check('双买价格口径(v50.67): 手动两腿弹窗走同一口径(价格 50 / 45, 不乘 30)',
        v67.hB && v67.hB.C && v67.hB.P
        && Math.abs(v67.hB.C.px - 50) < 0.01 && Math.abs(v67.hB.P.px - 45) < 0.01
        && Math.abs(v67.hB.C.premEach - 1500) < 0.5,
        JSON.stringify(v67.hB));

  // ===== v50.68: 保存方案不许四舍五入(delta 4 位小数必须原样留住) =====
  const v68Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const choose = (inputId, code) => { const el = q(inputId);
      el.value = code; el.dispatchEvent(new Event('input'));
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); };
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(400);
    // ---- A) 双买: 4 位小数 delta ----
    document.querySelector('.mode[data-mode="dual"]').click(); await w(500);
    choose('cSearchD', 'lc'); await w(900);
    q('equity').value = '90'; q('equity').dispatchEvent(new Event('input'));
    q('dualMonth').value = '2611';
    q('callStrike').value = '144000'; q('callDelta').value = '0.3921';
    q('callDelta').dispatchEvent(new Event('input'));
    q('callPrice').value = '9800'; q('callPrice').dispatchEvent(new Event('input'));
    q('putStrike').value = '120000'; q('putDelta').value = '-0.3127';
    q('putDelta').dispatchEvent(new Event('input'));
    q('putPrice').value = '8500'; q('putPrice').dispatchEvent(new Event('input'));
    await w(900);
    saveCurrentPlan(); await w(400);
    const pD = (JSON.parse(localStorage.getItem('oc_dual_plans') || '[]')[0]) || {};
    // 清空后调出 → 回填必须与保存时一字不差
    q('callDelta').value = ''; q('putDelta').value = ''; q('callStrike').value = '';
    recallPlan(JSON.parse(localStorage.getItem('oc_dual_plans') || '[]')[0]);
    await w(700);
    const backD = {c: q('callDelta').value, p: q('putDelta').value, s: q('callStrike').value,
                   ivBefore: q('dualIv').value};
    // ---- B) 期货: tick=0.005 的国债价 + 权益带小数 ----
    document.querySelector('.mode[data-mode="futures"]').click(); await w(400);
    choose('cSearch', 'TF'); await w(900);
    q('equity').value = '12.345'; q('equity').dispatchEvent(new Event('input'));
    q('entry').value = '105.335'; q('stop').value = '104.8'; q('target').value = '106.02';
    onInput(); await w(1100);
    saveCurrentPlan(); await w(400);
    const pf = (JSON.parse(localStorage.getItem('oc_futures_plans') || '[]')[0]) || {};
    q('entry').value = ''; q('equity').value = '';
    recallPlan(pf); await w(700);
    const backF = {e: q('entry').value, s: q('stop').value, t: q('target').value, eq: q('equity').value};
    // 收尾: 别把方案留给后面的用例
    localStorage.removeItem('oc_futures_plans');
    localStorage.removeItem('oc_dual_plans');
    localStorage.removeItem('oc_options_plans');
    loadPlans();
    renderPlans();
    return JSON.stringify({pD, backD, pf, backF});
  })()`);
  const v68 = JSON.parse(v68Run);
  check('方案不四舍五入(v50.68): 双买 4 位小数 delta 原样存进方案(0.3921 不变 0.39)',
        v68.pD.callDelta === '0.3921' && v68.pD.putDelta === '-0.3127',
        JSON.stringify({c: v68.pD.callDelta, p: v68.pD.putDelta}));
  check('方案不四舍五入(v50.68): 调出方案后输入框回填也是 0.3921(不再变 0.3900)',
        v68.backD.c === '0.3921' && v68.backD.p === '-0.3127' && v68.backD.s === '144000',
        JSON.stringify(v68.backD));
  check('方案不四舍五入(v50.68): 期货 tick=0.005 的价格不再被抹成 2 位(105.335 保住)',
        v68.pf.entry === '105.335' && v68.pf.stop === '104.8' && v68.pf.target === '106.02',
        JSON.stringify({e: v68.pf.entry, s: v68.pf.stop, t: v68.pf.target}));
  check('方案不四舍五入(v50.68): 权益带小数也原样(12.345 万, 不进位成 12.35)',
        v68.pf.eqWan === '12.345' && v68.backF.eq === '12.345' && v68.backF.e === '105.335',
        JSON.stringify({eq: v68.pf.eqWan, back: v68.backF}));

  // ===== v50.69-A: 期权买方「权利金」= 开仓价 × 乘数 × 数量, 自动算 =====
  const v69aRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    // ⚠ 必须 bubbles:true —— 权利金是挂在弹窗上的**事件委托**, 非冒泡的合成事件到不了那儿
    //   (真人敲键盘产生的是冒泡的原生 input 事件, 这里要模拟成一样的)
    const set = (id, v) => { const el = q(id); el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
    // A) 期权买方: 玻璃 fg 乘数 20, 报价 7 → 每手 140, 3 手 → 420
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(1000);
    q('btnNewOpen').click(); await w(400);
    set('tmUnderlying', 'fg');
    set('tmContract', 'fg2701-P-800');
    set('tmOpenPrice', '7');
    set('tmQty', '3');
    await w(300);
    const opt = {prem: q('tmPremium').value, hint: q('tmPremiumHint').textContent};
    // 手改后不被覆盖(只有再动价格/数量才重算)
    set('tmPremium', '999');
    await w(150);
    const manual = q('tmPremium').value;
    set('tmQty', '4'); await w(200);
    const recalc = q('tmPremium').value;
    q('tmCancel').click(); await w(300);
    // B) 期货模式不参与自动计算(该字段是保证金)
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(900);
    q('btnNewOpen').click(); await w(400);
    set('tmUnderlying', 'rb');
    set('tmOpenPrice', '3200');
    set('tmQty', '2');
    await w(300);
    const fut = {prem: q('tmPremium').value, hint: q('tmPremiumHint').textContent};
    q('tmCancel').click(); await w(300);
    // C) 鸡蛋 jd: 报价单位 元/500千克 → 乘数必须是 10(不是吨位 5)
    const jd = CONTRACTS.find(x => String(x.code).toLowerCase() === 'jd') || {};
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(800);
    q('btnNewOpen').click(); await w(400);
    set('tmUnderlying', 'jd');
    set('tmOpenPrice', '35');
    set('tmQty', '2');
    await w(300);
    const jdPrem = q('tmPremium').value;
    q('tmCancel').click(); await w(300);
    return JSON.stringify({opt, manual, recalc, fut, jdMult: jd.mult, jdUnit: jd.unit, jdPrem});
  })()`);
  const v69a = JSON.parse(v69aRun);
  check('期权买方权利金(v50.69): 玻璃报价 7 × 乘数 20 × 3 手 = 420, 自动填好',
        v69a.opt.prem === '420' && /乘数 20/.test(v69a.opt.hint), JSON.stringify(v69a.opt));
  check('期权买方权利金(v50.69): 提示写明「每手 ¥140 = 开仓价 7 × 乘数 20」',
        /每手 ¥140/.test(v69a.opt.hint) && /3 手合计 ¥420/.test(v69a.opt.hint), v69a.opt.hint);
  check('期权买方权利金(v50.69): 手改后不被覆盖, 动数量才重算(140×4=560)',
        v69a.manual === '999' && v69a.recalc === '560',
        '手改=' + v69a.manual + ' 重算=' + v69a.recalc);
  check('期权买方权利金(v50.69): 期货模式不自动算(该字段是保证金, 保持空)',
        v69a.fut.prem === '' && v69a.fut.hint === '', JSON.stringify(v69a.fut));
  check('鸡蛋乘数(v50.69): 报价单位 元/500千克 → 乘数 10(修掉按吨位 5 算少一半)',
        v69a.jdMult === 10 && v69a.jdUnit === '500千克/手' && v69a.jdPrem === '700',
        'mult=' + v69a.jdMult + ' unit=' + v69a.jdUnit + ' 35×2 手=' + v69a.jdPrem);

  // ===== v50.69-B: 资金曲线图表放大后有左右滑动滑块 =====
  const v69bRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const rec = (y, m, ie, ee) => fetch('/api/funds/records', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({strategy:'期权买方', year:y, month:m, initial_equity:ie, end_equity:ee, cash_flow:0})});
    // 灌 20 个月(2024-01 ~ 2025-08) → 放大后画布宽过容器, 该出现滑块
    for (let i = 1; i <= 20; i++){
      const y = 2024 + Math.floor((i-1)/12), m = ((i-1) % 12) + 1;
      await rec(y, m, 100000 + i*1000, 101000 + i*1000);
    }
    document.querySelector('#mainTabs .maintab[data-tab="funds"]').click(); await w(900);
    const seg = document.querySelector('#stratSeg button[data-strategy="期权买方"]');
    if (seg) seg.click();
    await w(900);
    document.querySelector('.zoombtn[data-zoom="monthly"]').click(); await w(1400);
    const bar = q('zoomPanBar'), sl = q('zoomPan'), sc = q('zoomScroll'), inner = q('zoomInner');
    const before = {hidden: bar.classList.contains('hidden'), max: parseInt(sl.max, 10),
                    inner: inner.offsetWidth, cw: sc.clientWidth, canvas: q('chartZoom').width};
    sl.value = String(Math.round(parseInt(sl.max, 10) * 0.6));
    sl.dispatchEvent(new Event('input')); await w(400);
    const scrolled = Math.round(sc.scrollLeft);
    sc.scrollLeft = 0; await w(300);
    const synced = parseInt(sl.value, 10);
    q('chartZoomClose').click(); await w(300);
    // 年视图只有 2 个点 → 不需要滑块, 应隐藏
    document.querySelector('.zoombtn[data-zoom="yearly"]').click(); await w(1200);
    const yBar = q('zoomPanBar');
    const yearHidden = yBar.classList.contains('hidden');
    q('chartZoomClose').click(); await w(300);
    for (let i = 1; i <= 20; i++){
      const y = 2024 + Math.floor((i-1)/12), m = ((i-1) % 12) + 1;
      await fetch('/api/funds/records', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({strategy:'期权买方', year:y, month:m, action:'delete'})});
    }
    return JSON.stringify({before, scrolled, synced, yearHidden});
  })()`);
  const v69b = JSON.parse(v69bRun);
  check('图表放大滑块(v50.69): 数据点多时出现滑块, 画布比容器宽',
        v69b.before.hidden === false && v69b.before.max > 0
        && v69b.before.inner > v69b.before.cw + 10,
        JSON.stringify(v69b.before));
  check('图表放大滑块(v50.69): 拖动滑块 → 图表横向滚动到 60% 位置',
        v69b.scrolled > 0 && Math.abs(v69b.scrolled - Math.round(v69b.before.max * 0.6)) <= 2,
        'scrolled=' + v69b.scrolled + ' max=' + v69b.before.max);
  check('图表放大滑块(v50.69): 手动把图表滚回最左, 滑块跟着回到 0',
        v69b.synced === 0, String(v69b.synced));
  check('图表放大滑块(v50.69): 点数少(年视图)时不需要滑块 → 隐藏', v69b.yearHidden === true,
        String(v69b.yearHidden));

  // ===== v50.73: 点图例 = 显示 / 隐藏线条与柱状图 =====
  const v73Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const rec = (y, m, ie, ee) => fetch('/api/funds/records', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({strategy:'期权买方', year:y, month:m, initial_equity:ie, end_equity:ee, cash_flow:0})});
    for (let i = 1; i <= 6; i++) await rec(2025, i, 100000 + i*1000, 101000 + i*1000);
    document.querySelector('#mainTabs .maintab[data-tab="funds"]').click(); await w(900);
    document.querySelector('#stratSeg button[data-strategy="期权买方"]').click(); await w(1200);

    const legM = q('chartMonthlyLegend');
    const items = [...legM.querySelectorAll('.lg[data-ds]')];
    const click = async (i)=>{ items[i].click(); await w(500); };
    const snap = ()=>({
      vis: [0,1,2].map(i => FundUI.chartMonthly.isDatasetVisible(i)),
      off: items.map(it => it.classList.contains('off')),
      y:  FundUI.chartMonthly.options.scales.y.display,
      y1: FundUI.chartMonthly.options.scales.y1.display,
    });

    const t0 = {n: items.length, ds: items.map(it=>it.dataset.ds),
                cursor: getComputedStyle(items[0]).cursor,
                swatch: items[0].querySelector('i').offsetWidth > 0};

    await click(2);                       // 隐藏折线
    const t1 = snap();
    await click(2);                       // 再点 → 显示回来
    const t2 = snap();
    await click(0); await click(1);       // 两根柱都隐藏
    const t3 = snap();
    await click(0); await click(1);       // 恢复

    // 放大图与缩略图共用同一份偏好
    await click(2);
    document.querySelector('.zoombtn[data-zoom="monthly"]').click(); await w(1500);
    const zItems = [...q('chartZoomLegend').querySelectorAll('.lg[data-ds]')];
    const t4 = {vis2: FundUI.chartZoom.isDatasetVisible(2),
                y1: FundUI.chartZoom.options.scales.y1.display,
                off: zItems[2].classList.contains('off'),
                swatch: zItems[0].querySelector('i').offsetWidth > 0};
    zItems[2].click(); await w(700);      // 在弹窗里点回来 → 缩略图同步
    const t5 = {zoomVis2: FundUI.chartZoom.isDatasetVisible(2),
                smallVis2: FundUI.chartMonthly.isDatasetVisible(2),
                smallOff: items[2].classList.contains('off')};
    q('chartZoomClose').click(); await w(300);

    // 换策略重渲染后仍保持"被点掉"
    await click(2);
    document.querySelector('#stratSeg button[data-strategy="威科夫"]').click(); await w(900);
    document.querySelector('#stratSeg button[data-strategy="期权买方"]').click(); await w(1200);
    const t6 = {vis2: FundUI.chartMonthly.isDatasetVisible(2),
                off: items[2].classList.contains('off'),
                y1: FundUI.chartMonthly.options.scales.y1.display};
    await click(2);                       // 收尾: 全部恢复

    for (let i = 1; i <= 6; i++){
      await fetch('/api/funds/records', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({strategy:'期权买方', year:2025, month:i, action:'delete'})});
    }
    return JSON.stringify({t0,t1,t2,t3,t4,t5,t6});
  })()`);
  const v73 = JSON.parse(v73Run);
  check('图例可点(v50.73): 3 项都带 data-ds, 鼠标手型, 色块可见',
        v73.t0.n === 3 && v73.t0.ds.join(',') === '0,1,2'
        && v73.t0.cursor === 'pointer' && v73.t0.swatch === true, JSON.stringify(v73.t0));
  check('图例可点(v50.73): 点折线图例 → 折线隐藏 + 图例置灰划线 + 右轴收起',
        v73.t1.vis.join(',') === 'true,true,false' && v73.t1.off.join(',') === 'false,false,true'
        && v73.t1.y === true && v73.t1.y1 === false, JSON.stringify(v73.t1));
  check('图例可点(v50.73): 再点一次 → 折线与右轴都回来',
        v73.t2.vis.join(',') === 'true,true,true' && v73.t2.off.join(',') === 'false,false,false'
        && v73.t2.y1 === true, JSON.stringify(v73.t2));
  check('图例可点(v50.73): 两根柱都点掉 → 左轴也收起(只留折线)',
        v73.t3.vis.join(',') === 'false,false,true' && v73.t3.y === false && v73.t3.y1 === true,
        JSON.stringify(v73.t3));
  check('图例可点(v50.73): 放大图沿用缩略图的选择(共用一份状态) + 弹窗色块可见',
        v73.t4.vis2 === false && v73.t4.y1 === false && v73.t4.off === true && v73.t4.swatch === true,
        JSON.stringify(v73.t4));
  check('图例可点(v50.73): 在弹窗里点回来 → 缩略图同步恢复',
        v73.t5.zoomVis2 === true && v73.t5.smallVis2 === true && v73.t5.smallOff === false,
        JSON.stringify(v73.t5));
  check('图例可点(v50.73): 换策略重渲染后仍保持"被点掉"',
        v73.t6.vis2 === false && v73.t6.off === true && v73.t6.y1 === false, JSON.stringify(v73.t6));
  check('图例可点(v50.73): 系列藏起来后不再画它的数值标签',
        /isDatasetVisible|meta\.hidden/.test(await evalJs(ws, `valueLabelPlugin.afterDatasetsDraw.toString()`)));

  // ===== v50.70: 手续费(交易所+0.01) / 净盈亏列 / 开平仓时间 / 平今 / 期货合约年月 =====
  const v70Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const post = (p) => fetch('/api/trades/upsert', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)}).then(r=>r.json());
    const wipe = async (mode) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying=' + it.underlying
          + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) {
          await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({id: op.id})});
        }
      }
    };
    await wipe('futures'); await wipe('options');
    // ---- A) 铜(万分之0.5, 平今万分之1): 开 1 手 70000, 同日平 1 手 71000 ----
    await post({mode:'futures', underlying:'e2efee70', contract:'cu2612', batch:'Bv70a',
      op_type:'open', direction:'buy', open_date:'2026-10-10', open_time:'10:00',
      open_price:70000, qty:1, premium:0});
    await post({mode:'futures', underlying:'e2efee70', contract:'cu2612', batch:'Bv70a',
      op_type:'close', direction:'sell', close_date:'2026-10-10', close_time:'14:00',
      close_qty:1, close_price:71000, pnl:5000});
    // ---- B) 棉花(4.3元/手, 平今0): 隔一个交易日平 → 平昨 4.31; 同日平 → 平今 0.01 ----
    await post({mode:'futures', underlying:'e2efee71', contract:'CF2701', batch:'Bv70b',
      op_type:'open', direction:'buy', open_date:'2026-10-09', open_time:'10:00',
      open_price:15000, qty:1, premium:0});
    await post({mode:'futures', underlying:'e2efee71', contract:'CF2701', batch:'Bv70b',
      op_type:'close', direction:'sell', close_date:'2026-10-10', close_time:'14:00',
      close_qty:1, close_price:15100, pnl:500});
    await post({mode:'futures', underlying:'e2efee72', contract:'CF2701', batch:'Bv70c',
      op_type:'open', direction:'buy', open_date:'2026-10-10', open_time:'09:00',
      open_price:15000, qty:1, premium:0});
    await post({mode:'futures', underlying:'e2efee72', contract:'CF2701', batch:'Bv70c',
      op_type:'close', direction:'sell', close_date:'2026-10-10', close_time:'14:00',
      close_qty:1, close_price:15100, pnl:500});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1200);
    const readMain = () => {
      const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
        .find(x => x.dataset.u === 'e2efee70');
      return tr ? tr.innerText.replace(/\\s+/g, ' ').trim() : '';
    };
    const mainOn = readMain();
    const heads = [...document.querySelectorAll('#tradesTable thead th')].map(t => t.textContent.trim());
    // 详情: 操作记录里的手续费
    await TradeUI.loadDetail('e2efee70', 'Bv70a'); await w(1200);
    const feeHeads = [...document.querySelectorAll('#tdOps')].length
      ? [...document.querySelectorAll('#tradeDetailPanel table.tbl')][1].tHead.rows[0] : null;
    const opRows = [...document.querySelectorAll('#tdOps tr')].map(tr => tr.innerText.replace(/\\s+/g, ' ').trim());
    const dA = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efee70&batch=Bv70a&fee=ex')).json();
    const dB = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efee71&batch=Bv70b&fee=ex')).json();
    const dC = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efee72&batch=Bv70c&fee=ex')).json();
    const dBOff = await (await fetch('/api/trades/detail?mode=futures&underlying=e2efee71&batch=Bv70b&fee=off')).json();
    TradeUI.closeDetail(); await w(300);
    // ---- v50.71: 手续费口径从 <select> 改成「两个选项平铺」的分段控件 ----
    const feeBtn = k => document.querySelector('#feeSeg .fee-opt[data-fee="' + k + '"]');
    const noSelect = document.getElementById('feeMode') === null;
    const feeOpts = [...document.querySelectorAll('#feeSeg .fee-opt')]
      .map(b => b.textContent.trim() + ':' + b.classList.contains('active'));
    // 与旁边的「隐藏统计」按钮在同一行且竖直居中对齐(margin box 陷阱的回归测试)
    const rSeg = document.querySelector('#feeSeg .seg').getBoundingClientRect();
    const rBtn = document.getElementById('btnToggleStats').getBoundingClientRect();
    const segAlign = Math.abs((rSeg.top + rSeg.height / 2) - (rBtn.top + rBtn.height / 2)) <= 1.5;
    const segRow = Math.abs(rSeg.top - rBtn.top) < rSeg.height;
    // 切成「不计算」→ 净盈亏 = 平仓盈亏
    feeBtn('off').click(); await w(1300);
    const mainOff = readMain();
    const stored = localStorage.getItem('oc-fee-mode');
    const offActive = feeBtn('off').classList.contains('active') && !feeBtn('ex').classList.contains('active');
    feeBtn('ex').click(); await w(1200);
    const mainBack = readMain();
    const onActive = feeBtn('ex').classList.contains('active') && feeBtn('ex').getAttribute('aria-pressed') === 'true';
    // ---- C) 期货计算器: 合约年月 → 加入记录后标的带年月 ----
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(400);
    document.querySelector('.mode[data-mode="futures"]').click(); await w(400);
    const el = q('cSearch'); el.value = 'rb'; el.dispatchEvent(new Event('input'));
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); await w(900);
    q('equity').value = '50'; q('equity').dispatchEvent(new Event('input'));
    q('futMonth').value = '2610'; q('futMonth').dispatchEvent(new Event('input'));
    q('entry').value = '3200'; q('stop').value = '3100'; q('target').value = '3450';
    onInput(); await w(1200);
    const cardTxt = q('rContractF').textContent;
    await addToTradeRecord(); await w(2200);
    const gsFut = (await (await fetch('/api/trades/groups?mode=futures')).json()).groups || [];
    const rbG = gsFut.find(x => String(x.underlying).toLowerCase() === 'rb2610');
    const dRb = rbG ? await (await fetch('/api/trades/detail?mode=futures&underlying=' + encodeURIComponent(rbG.underlying)
      + '&batch=' + encodeURIComponent(rbG.batch || '') + '&fee=ex')).json() : {operations: []};
    const rbOp = (dRb.operations || [])[0] || {};
    // ---- D) 时间字段: 新建开仓默认带当前时间并落库 ----
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(900);
    q('btnNewOpen').click(); await w(400);
    const timePrefilled = /^\\d{2}:\\d{2}$/.test(q('tmOpenTime').value);
    q('tmUnderlying').value = 'fg'; q('tmUnderlying').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmContract').value = 'fg2701-P-800'; q('tmContract').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmOpenPrice').value = '7'; q('tmOpenPrice').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmQty').value = '3'; q('tmQty').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmOpenTime').value = '21:30'; q('tmOpenTime').dispatchEvent(new Event('input', {bubbles:true}));
    await w(300);
    const premAuto = q('tmPremium').value;
    q('tmSave').click(); await w(2000);
    const gsOpt = (await (await fetch('/api/trades/groups?mode=options')).json()).groups || [];
    const fgG = gsOpt.find(x => String(x.underlying).toLowerCase() === 'fg');
    const dFg = fgG ? await (await fetch('/api/trades/detail?mode=options&underlying=' + encodeURIComponent(fgG.underlying)
      + '&batch=' + encodeURIComponent(fgG.batch || '') + '&fee=ex')).json() : {operations: []};
    const fgOp = (dFg.operations || [])[0] || {};
    await wipe('futures'); await wipe('options');
    return JSON.stringify({mainOn, heads, opRows, dB: dB.operations.map(o => o.fee),
                           dBtotal: dB.fee_total, dBnet: dB.net_pnl, dBoff: dBOff.fee_total,
                           dBtoday: dB.operations.map(o => o.fee_is_today),
                           dC: dC.operations.map(o => o.fee), dCtoday: dC.operations.map(o => o.fee_is_today),
                           mainOff, mainBack, stored, cardTxt, rbUnd: rbG && rbG.underlying,
                           rbContract: rbOp.contract, rbTime: rbOp.open_time, timePrefilled,
                           premAuto, fgTime: fgOp.open_time, fgFee: fgOp.fee,
                           aFeeTotal: dA.fee_total, aNet: dA.net_pnl,
                           noSelect, feeOpts, segAlign, segRow, offActive, onActive});
  })()`);
  const v70 = JSON.parse(v70Run);
  check('手续费(v50.70): 主表新增「净盈亏」列', v70.heads.indexOf('净盈亏') >= 0, JSON.stringify(v70.heads));
  check('手续费(v50.70): 铜 开1手+同日平1手 = 万分之0.5×70000×5 + 万分之1×71000×5 + 0.04',
        Math.abs(v70.aFeeTotal - (70000 * 5 * 0.5 / 10000 + 71000 * 5 * 1 / 10000 + 0.02)) < 0.02,
        'fee=' + v70.aFeeTotal);
  check('手续费(v50.70): 净盈亏 = 5000 − 手续费',
        Math.abs(v70.aNet - (5000 - v70.aFeeTotal)) < 0.02, 'net=' + v70.aNet);
  check('平今/平昨(v50.70): 棉花隔日平 = 平昨 4.31; 同日平 = 平今 0.01',
        v70.dBtoday[1] === false && Math.abs(v70.dB[1] - 4.31) < 1e-6
        && v70.dCtoday[1] === true && Math.abs(v70.dC[1] - 0.01) < 1e-6,
        '平昨=' + JSON.stringify(v70.dB) + ' 平今=' + JSON.stringify(v70.dC));
  check('手续费(v50.70): 操作记录表出现「手续费」列并显示两笔金额',
        v70.opRows.length === 2 && v70.opRows.every(r => r.indexOf('CN¥') >= 0),
        JSON.stringify(v70.opRows));
  check('手续费口径(v50.70): 切「不计算」→ 手续费 0, 净盈亏 = 平仓盈亏, 且写进 localStorage',
        v70.stored === 'off' && /CN¥5,000/.test(v70.mainOff) && !/4,946/.test(v70.mainOff),
        'stored=' + v70.stored + ' main=' + v70.mainOff);
  check('手续费口径(v50.70): 切回「交易所 + 0.01」→ 净盈亏重新变回扣费后的数',
        /CN¥4,946\.98/.test(v70.mainBack), v70.mainBack);
  check('期货合约年月(v50.70): 计算器结果卡显示带年月的合约',
        /螺纹钢 2610/.test(v70.cardTxt), v70.cardTxt);
  check('期货合约年月(v50.70): 加入记录后标的与合约都带年月(rb2610)',
        v70.rbUnd === 'rb2610' && v70.rbContract === 'rb2610',
        '标的=' + v70.rbUnd + ' 合约=' + v70.rbContract);
  check('期货合约年月(v50.70): 加入记录时自动带上开仓时间',
        /^\d{2}:\d{2}$/.test(v70.rbTime || ''), String(v70.rbTime));
  check('开平仓时间(v50.70): 新建开仓默认带当前时间, 存库保留手填的 21:30',
        v70.timePrefilled === true && v70.fgTime === '21:30', 'prefilled=' + v70.timePrefilled + ' t=' + v70.fgTime);
  check('期权手续费(v50.70): 玻璃期权 0.5元/手 × 3 手 + 0.03 = 1.53',
        Math.abs(v70.fgFee - 1.53) < 1e-6, String(v70.fgFee));
  // ---- v50.71: 手续费口径改成「选项平铺」的分段控件 ----
  check('手续费口径UI(v50.71): 不再用下拉框, 两个选项同时平铺可见',
        v70.noSelect === true && v70.feeOpts.length === 2
        && v70.feeOpts[0].indexOf('交易所 + 0.01') === 0 && v70.feeOpts[1].indexOf('不计算') === 0,
        JSON.stringify({noSelect: v70.noSelect, opts: v70.feeOpts}));
  check('手续费口径UI(v50.71): 与「隐藏统计」同一行且竖直居中(±1.5px)',
        v70.segAlign === true && v70.segRow === true,
        'align=' + v70.segAlign + ' row=' + v70.segRow);
  check('手续费口径UI(v50.71): 选中态跟着按钮走(点「不计算」后高亮转移)',
        v70.offActive === true && v70.onActive === true,
        'off=' + v70.offActive + ' on=' + v70.onActive);

  // ===== v50.53: 顶部统计卡片(口径 = 主表每条记录) =====
  const stRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const wipe = async (mode) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying='
          + encodeURIComponent(it.underlying) + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    // 清空两个模式, 让统计口径可预期
    await wipe('futures'); await wipe('options');
    // 4 笔已平仓: 3 胜 1 负 -> 累计 4125 / 胜率 75.0% / 盈亏比 7.07 / 平均 1031.25
    const cases = [['e2est01', 2105], ['e2est02', 1000], ['e2est03', 1700], ['e2est04', -680]];
    for (const [u, pnl] of cases) {
      await post({underlying: u, contract: u + '2611', op_type: 'open', direction: 'buy',
                  open_date: '2026-09-01', open_price: 100, qty: 1, premium: 0, mode: 'futures'});
      await post({underlying: u, contract: u + '2611', op_type: 'close', direction: 'sell',
                  close_date: '2026-09-02', close_qty: 1, close_price: 200, pnl: pnl, mode: 'futures'});
    }
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(1000);
    const read = () => [...document.querySelectorAll('#tradeStats .stat-card')].map(c => ({
      k: (c.querySelector('.sk') || {}).textContent || '',
      v: (c.querySelector('.sv') || {}).textContent || '',
      s: (c.querySelector('.ss') || {}).textContent || '',
      cls: (c.querySelector('.sv') || {}).className || ''}));
    const fut = read();
    // v50.86: 第 5 卡(夏普比率)的 tooltip 必须**在这里**抓 —— 后面还有别的造数会把卡片覆盖掉
    const shTip = ((document.querySelectorAll('#tradeStats .stat-card')[4] || {})
      .querySelector('.sv') || {}).getAttribute('title') || '';
    // v50.53: 视口够宽时 5 张卡必须排在同一行(用户要求"能一行就一行")
    const tops = [...document.querySelectorAll('#tradeStats .stat-card')]
      .map(c => Math.round(c.getBoundingClientRect().top));
    const oneRow = new Set(tops).size === 1;
    const gridCols = getComputedStyle(document.getElementById('tradeStats')).gridTemplateColumns.split(' ').length;
    // 「只展示未平仓」只该影响表格, 不该改变统计卡片
    const chk = document.getElementById('tradesOnlyOpen');
    chk.checked = true; chk.dispatchEvent(new Event('change'));
    await w(500);
    const afterFilter = read();
    const rowsAfterFilter = document.querySelectorAll('#tradesTable tbody tr.clickable').length;
    chk.checked = false; chk.dispatchEvent(new Event('change'));
    await w(500);
    // ---- v50.71: 净盈亏比(扣手续费) —— 再补 2 笔真实品种(铝 3元/手、螺纹 万分之0.2) ----
    await post({underlying:'al', contract:'al2611', op_type:'open', direction:'buy',
                open_date:'2026-09-01', open_price:20000, qty:2, premium:0, mode:'futures'});
    await post({underlying:'al', contract:'al2611', op_type:'close', direction:'sell',
                close_date:'2026-09-30', close_qty:2, close_price:20100, pnl:1000, mode:'futures'});
    await post({underlying:'rb', contract:'rb2611', op_type:'open', direction:'buy',
                open_date:'2026-09-01', open_price:3200, qty:10, premium:0, mode:'futures'});
    await post({underlying:'rb', contract:'rb2611', op_type:'close', direction:'sell',
                close_date:'2026-09-30', close_qty:10, close_price:3100, pnl:-1000, mode:'futures'});
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(600);
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1200);
    const nut = read();
    const nutCard = (nut || []).find(c => c.k === '盈亏比 / 净盈亏比') || {};
    const nutTip = (document.querySelectorAll('#tradeStats .stat-card')[3] || {})
      .querySelector('.sv').getAttribute('title') || '';
    const feeBtn2 = k => document.querySelector('#feeSeg .fee-opt[data-fee="' + k + '"]');
    feeBtn2('off').click(); await w(1400);
    const nutOff = ((read() || []).find(c => c.k === '盈亏比 / 净盈亏比') || {}).v || '';
    feeBtn2('ex').click(); await w(1400);
    const nutBack = ((read() || []).find(c => c.k === '盈亏比 / 净盈亏比') || {}).v || '';
    const nutTitle = (document.querySelectorAll('#tradeStats .stat-card')[3] || {})
      .querySelector('.sk') || {};
    // 切到期权模式 -> 期货那几笔不算进来
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await w(1000);
    const opt = read();
    const optMode = TradeUI.mode;
    await wipe('futures'); await wipe('options');
    return JSON.stringify({fut, afterFilter, rowsAfterFilter, opt, optMode, oneRow, gridCols,
                           nut, nutCard, nutTip, nutOff, nutBack, shTip,
                           nutLabel: nutTitle.textContent || ''});
  })()`);
  const stat = JSON.parse(stRun);
  const cardOf = (arr, k) => (arr || []).find(c => c.k === k) || {};
  check('统计卡(v50.53): 期货/期权两模式顶部各有 5 张卡',
        stat.fut.length === 5 && stat.opt.length === 5,
        'fut=' + stat.fut.length + ' opt=' + stat.opt.length);
  check('统计卡(v50.53): 累计盈亏 = 3胜1负的已实现盈亏合计 +CN¥4,125',
        cardOf(stat.fut, '累计盈亏').v === '+CN¥4,125' && /pos/.test(cardOf(stat.fut, '累计盈亏').cls),
        JSON.stringify(cardOf(stat.fut, '累计盈亏')));
  check('统计卡(v50.53): 胜率 75.0%(3 胜 / 1 负)',
        cardOf(stat.fut, '胜率').v === '75.0%' && cardOf(stat.fut, '胜率').s === '3 胜 / 1 负',
        JSON.stringify(cardOf(stat.fut, '胜率')));
  check('统计卡(v50.53): 已平仓 4 / 持仓中 0',
        cardOf(stat.fut, '已平仓').v === '4' && /持仓中: 0/.test(cardOf(stat.fut, '已平仓').s),
        JSON.stringify(cardOf(stat.fut, '已平仓')));
  check('统计卡(v50.53): 盈亏比 = 盈利4805 / 亏损680 = 7.07',
        cardOf(stat.fut, '盈亏比 / 净盈亏比').v === '7.07 / 7.07'
        && /盈利 4,805 \/ 亏损 680/.test(cardOf(stat.fut, '盈亏比 / 净盈亏比').s),
        JSON.stringify(cardOf(stat.fut, '盈亏比 / 净盈亏比')));
  // ---- v50.71: 「盈亏比 / 净盈亏比」一格两值, 净 = 扣掉手续费 ----
  check('净盈亏比(v50.71): 卡片标题改成「盈亏比 / 净盈亏比」',
        stat.nutLabel === '盈亏比 / 净盈亏比', stat.nutLabel);
  check('净盈亏比(v50.71): 同一格里两个比值 —— 毛 3.46 / 净 3.42(铝+螺纹的手续费把比率拉低)',
        stat.nutCard.v === '3.46 / 3.42', String(stat.nutCard.v));
  check('净盈亏比(v50.71): tooltip 写明毛/净两个口径与各自盈亏',
        /未扣手续费/.test(stat.nutTip) && /扣手续费后/.test(stat.nutTip)
        && /毛：盈利 5,805 \/ 亏损 1,680/.test(stat.nutTip)
        && /净：盈利 5,792\.96 \/ 亏损 1,692\.8/.test(stat.nutTip), stat.nutTip);
  check('净盈亏比(v50.71): 切「不计算」→ 两个比值相同(3.46 / 3.46)',
        stat.nutOff === '3.46 / 3.46', String(stat.nutOff));
  check('净盈亏比(v50.71): 切回「交易所 + 0.01」→ 净盈亏比重新变小(3.46 / 3.42)',
        stat.nutBack === '3.46 / 3.42', String(stat.nutBack));
  // ---- v50.86: 第 5 卡「平均盈亏」换成「夏普比率」 ----
  check('统计卡(v50.86): 第 5 卡换成「夏普比率」= 1031.25 ÷ 1228.76 = 0.84',
        cardOf(stat.fut, '夏普比率').v === '0.84'
        && cardOf(stat.fut, '夏普比率').s === '每笔 · 4 笔已平仓',
        JSON.stringify(cardOf(stat.fut, '夏普比率')));
  check('统计卡(v50.86): 夏普卡 tooltip 写明口径与均值·标准差·最大盈亏',
        /平均每笔 1,031\.25/.test(stat.shTip) && /标准差 1,228\.76/.test(stat.shTip)
        && /最大盈 2,105 \/ 亏 680/.test(stat.shTip) && /每笔口径 0\.84/.test(stat.shTip),
        stat.shTip);
  check('统计卡(v50.87): 只有 4 笔(不足 5) → 不年化, tooltip 说明原因',
        /年化口径：已平仓不足 5 笔/.test(stat.shTip)
        && cardOf(stat.fut, '夏普比率').s.indexOf('年化') < 0,
        cardOf(stat.fut, '夏普比率').s);
  check('统计卡(v50.86): 「平均盈亏」这张卡已经不存在了',
        cardOf(stat.fut, '平均盈亏').v === undefined, JSON.stringify(stat.fut.map(x => x.k)));
  check('统计卡(v50.53): 不受「只展示未平仓」影响(表格空了卡片不变)',
        stat.rowsAfterFilter === 0 && JSON.stringify(stat.afterFilter) === JSON.stringify(stat.fut),
        'rows=' + stat.rowsAfterFilter + ' after=' + JSON.stringify(stat.afterFilter));
  check('统计卡(v50.53): 视口够宽时 5 张卡排在同一行(不是硬分两行)',
        stat.oneRow && stat.gridCols >= 5,
        'oneRow=' + stat.oneRow + ' cols=' + stat.gridCols);
  check('统计卡(v50.53): 期权模式独立统计(期货那 4 笔不计入)',
        stat.optMode === 'options' && cardOf(stat.opt, '累计盈亏').v === '—'
        && cardOf(stat.opt, '已平仓').v === '0', JSON.stringify(stat.opt));

  // ===== v50.55: 统计卡片「显示/隐藏」按钮(状态持久化) =====
  const stgRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    const btn = document.getElementById('btnToggleStats');
    const box = document.getElementById('tradeStats');
    const chk = document.getElementById('tradesOnlyOpen');
    const br = btn.getBoundingClientRect(), cr = chk.getBoundingClientRect();
    const onRight = br.left >= cr.right - 1;          // 排在「只展示未平仓」右边
    const snap = () => ({
      hidden: box.classList.contains('hidden'),
      label: btn.textContent.trim(),
      stored: localStorage.getItem('oc-trade-stats'),
      boxH: Math.round(box.getBoundingClientRect().height),
    });
    try { localStorage.removeItem('oc-trade-stats'); } catch (e) {}
    TradeUI.applyStatsPref(); await w(200);
    const before = snap();                             // 没有记录时默认显示
    btn.click(); await w(350);
    const afterHide = snap();
    TradeUI.applyStatsPref(); await w(200);            // 模拟「下次打开」
    const reopen1 = snap();
    btn.click(); await w(350);
    const afterShow = snap();
    TradeUI.applyStatsPref(); await w(200);
    const reopen2 = snap();
    try { localStorage.removeItem('oc-trade-stats'); } catch (e) {}
    TradeUI.applyStatsPref();
    // v50.55: 「只展示未平仓」与「隐藏统计」按钮外观统一(同款高度/圆角) + 选中时高亮打勾
    const ooLbl = document.getElementById('onlyOpenLbl');
    const oo = document.getElementById('tradesOnlyOpen');
    const hOO = Math.round(ooLbl.getBoundingClientRect().height);
    const hTG = Math.round(btn.getBoundingClientRect().height);
    const ooRadius = getComputedStyle(ooLbl).borderRadius;
    const tgRadius = getComputedStyle(btn).borderRadius;
    oo.checked = true; oo.dispatchEvent(new Event('change'));
    await w(350);
    const onCls = ooLbl.classList.contains('on');
    const onRows = document.querySelectorAll('#tradesTable tbody tr.clickable').length;
    oo.checked = false; oo.dispatchEvent(new Event('change'));
    await w(350);
    const offCls = ooLbl.classList.contains('on');
    // v50.55: 工具栏三个按钮必须同一水平线(此前 label 带 label{margin:14px 0 6px} 被推低 4px)
    const tops = [ooLbl, btn, document.getElementById('btnNewOpen')]
      .map(el => Math.round(el.getBoundingClientRect().top));
    const wrapPT = parseInt(getComputedStyle(document.querySelector('#tradesArea .wrap')).paddingTop, 10);
    const hdrMB = parseInt(getComputedStyle(document.querySelector('#tradesArea').previousElementSibling).marginBottom, 10)
               || parseInt(getComputedStyle(document.querySelector('.wrap > header')).marginBottom, 10);
    const gapTop = Math.round(ooLbl.getBoundingClientRect().top)
                 - Math.round(document.querySelector('.wrap > header').getBoundingClientRect().bottom);
    return JSON.stringify({onRight, before, afterHide, reopen1, afterShow, reopen2,
                           hOO, hTG, ooRadius, tgRadius, onCls, offCls, onRows,
                           tops, wrapPT, hdrMB, gapTop});
  })()`);
  const stg = JSON.parse(stgRun);
  check('统计开关(v50.55): 按钮排在「只展示未平仓」右边', stg.onRight, stgRun);
  check('统计开关(v50.55): 没有记录时默认显示统计',
        stg.before.hidden === false && /隐藏统计/.test(stg.before.label),
        JSON.stringify(stg.before));
  check('统计开关(v50.55): 点一下隐藏(卡片不占高度) + 写入偏好 0',
        stg.afterHide.hidden === true && stg.afterHide.boxH === 0
        && stg.afterHide.stored === '0' && /显示统计/.test(stg.afterHide.label),
        JSON.stringify(stg.afterHide));
  check('统计开关(v50.55): 隐藏后「下次打开」仍是隐藏',
        stg.reopen1.hidden === true && /显示统计/.test(stg.reopen1.label),
        JSON.stringify(stg.reopen1));
  check('统计开关(v50.55): 再点显示 + 写入偏好 1',
        stg.afterShow.hidden === false && stg.afterShow.boxH > 0
        && stg.afterShow.stored === '1' && /隐藏统计/.test(stg.afterShow.label),
        JSON.stringify(stg.afterShow));
  check('统计开关(v50.55): 显示后「下次打开」仍是显示',
        stg.reopen2.hidden === false && /隐藏统计/.test(stg.reopen2.label),
        JSON.stringify(stg.reopen2));
  check('筛选开关(v50.55): 「只展示未平仓」外观与旁边按钮统一(高度/圆角一致)',
        Math.abs(stg.hOO - stg.hTG) <= 2 && stg.ooRadius === stg.tgRadius,
        'h=' + stg.hOO + '/' + stg.hTG + ' r=' + stg.ooRadius + '/' + stg.tgRadius);
  check('筛选开关(v50.55): 勾选时高亮(.on)且表格只剩未平仓',
        stg.onCls === true && stg.onRows === 0 && stg.offCls === false,
        JSON.stringify({onCls: stg.onCls, offCls: stg.offCls, onRows: stg.onRows}));
  check('工具栏对齐(v50.55): 三个按钮在同一水平线(顶部坐标一致)',
        new Set(stg.tops).size === 1, JSON.stringify(stg.tops));
  check('工具栏间距(v50.55): 与上方标题的间隙已收紧(内层 wrap 不再留 28px)',
        stg.wrapPT <= 6 && stg.gapTop <= 34,
        'wrapPT=' + stg.wrapPT + ' gapTop=' + stg.gapTop + ' hdrMB=' + stg.hdrMB);

  // ===== v50.72: 平仓盈亏按 开仓价/平仓价 自动算(不含手续费), 仍可手改 =====
  const pnl72Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const set = (id, v) => { const el = q(id); el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const wipe = async (mode) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying='
          + encodeURIComponent(it.underlying) + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    await wipe('futures'); await wipe('options');

    // ---- A) 期货空头: 开仓卖 3500, 平仓买 3450, 10 手 × 乘数 10 → 价跌赚 (3450-3500)×(-1)×10×10 = +5000
    await post({mode:'futures', underlying:'rb', contract:'rb2701', batch:'Brb72',
                op_type:'open', direction:'sell', open_date:'2026-09-01', open_time:'09:00',
                open_price:3500, qty:10, premium:0});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(900);
    await TradeUI.loadDetail('rb', 'Brb72'); await w(800);
    q('tdNewClose').click(); await w(400);
    const hintBefore = q('tmPnlHint').textContent;      // 只填了数量前: 提示里应有开仓均价
    const pnlBefore = q('tmPnl').value;                 // 还没填平仓价 → 不写值
    set('tmCloseQty', '10'); await w(150);
    set('tmClosePrice', '3450'); await w(250);
    const futShort = q('tmPnl').value;
    const futHint = q('tmPnlHint').textContent;
    // 手改 → 不动源输入就不会被覆盖
    set('tmPnl', '777'); await w(150);
    const manualKeep = q('tmPnl').value;
    // 再动平仓数量 → 重算回来
    set('tmCloseQty', '10'); await w(250);
    const afterTouch = q('tmPnl').value;
    // 改平仓价 → 跟着变: (3460-3500)×(-1)×10×10 = 4000
    set('tmClosePrice', '3460'); await w(250);
    const futShort2 = q('tmPnl').value;
    // 存下去(这一批 10 手全平) → holdings 变空, 供 D) 用
    set('tmClosePrice', '3450'); await w(200);
    const closeSaved = await TradeUI.submitModal(); await w(800);
    q('tradeModalBg').classList.add('hidden');

    // ---- B) 期货多头: 开仓买 3500, 平仓卖 3530, 2 手 × 乘数 10 → 价涨赚 = +600
    await post({mode:'futures', underlying:'hc', contract:'hc2701', batch:'Bhc72',
                op_type:'open', direction:'buy', open_date:'2026-09-01', open_time:'09:00',
                open_price:3500, qty:2, premium:0});
    await TradeUI.refresh(); await w(600);
    await TradeUI.loadDetail('hc', 'Bhc72'); await w(800);
    q('tdNewClose').click(); await w(400);
    set('tmCloseQty', '2');
    set('tmClosePrice', '3530'); await w(250);
    const futLong = q('tmPnl').value;
    // 亏损方向: 平仓价低于开仓价 → 负数, 且**提示里的符号**必须是负号(不能只写 ¥600)
    set('tmClosePrice', '3470'); await w(250);       // (3470-3500)×10×2 = -600
    const futLoss = q('tmPnl').value;
    const lossHint = q('tmPnlHint').textContent;
    q('tmCancel').click(); await w(200);

    // ---- C) 期权: 烧碱 SH 乘数 30, 开仓买 40 → 平 55, 2 手 → (55-40)×30×2 = +900
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(900);
    await post({mode:'options', underlying:'SH', contract:'SH2701C2000', batch:'Bsh72',
                op_type:'open', direction:'buy', call_put:'C', open_date:'2026-09-02',
                open_price:40, qty:2, premium:2400});
    await TradeUI.refresh(); await w(600);
    await TradeUI.loadDetail('SH', 'Bsh72'); await w(800);
    q('tdNewClose').click(); await w(400);
    set('tmCloseQty', '2');
    set('tmClosePrice', '55'); await w(250);
    const optPnl = q('tmPnl').value;
    const optHint = q('tmPnlHint').textContent;
    q('tmCancel').click(); await w(200);

    // ---- D) 编辑已有平仓记录: holdings 已空 → 从该合约的开仓记录取均价; 库里存的值不被覆盖
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(800);
    await TradeUI.loadDetail('rb', 'Brb72'); await w(800);
    const holdLeft = (TradeUI.detail.holdings || []).length;
    const closeOp = (TradeUI.detail.operations || []).filter(o => o.op_type === 'close').pop();
    document.querySelector('#tdOps [data-edit="' + closeOp.id + '"]').click(); await w(500);
    const editPnl = q('tmPnl').value;
    const editHint = q('tmPnlHint').textContent;
    set('tmClosePrice', '3400'); await w(250);          // (3400-3500)×(-1)×10×10 = +10000
    const editRe = q('tmPnl').value;
    q('tmCancel').click(); await w(200);

    await wipe('futures'); await wipe('options');
    return JSON.stringify({hintBefore, pnlBefore, futShort, futHint, manualKeep, afterTouch,
                           futShort2, closeSaved: !!closeSaved, futLong, futLoss, lossHint,
                           optPnl, optHint, holdLeft, editPnl, editHint, editRe});
  })()`);
  const pn72 = JSON.parse(pnl72Run);
  check('平仓盈亏自动算(v50.72): 只填数量时先提示开仓均价, 不乱填值',
        /开仓均价 3,500/.test(pn72.hintBefore) && /不含手续费/.test(pn72.hintBefore) && pn72.pnlBefore === '',
        JSON.stringify({hint: pn72.hintBefore, v: pn72.pnlBefore}));
  check('平仓盈亏自动算(v50.72): 期货空头 价跌赚 → (3450−3500)×(−1)×10×10 = 5000',
        pn72.futShort === '5000', String(pn72.futShort));
  check('平仓盈亏自动算(v50.72): 期货多头 价涨赚 → (3530−3500)×10×2 = 600',
        pn72.futLong === '600', String(pn72.futLong));
  check('平仓盈亏自动算(v50.72): 亏损是负数, 提示里也带负号 → -600 / −¥600',
        pn72.futLoss === '-600' && / = −¥600/.test(pn72.lossHint),
        JSON.stringify({v: pn72.futLoss, h: pn72.lossHint}));
  check('平仓盈亏自动算(v50.72): 期权用期权乘数(SH×30) → (55−40)×30×2 = 900',
        pn72.optPnl === '900' && /乘数 30/.test(pn72.optHint), JSON.stringify({v: pn72.optPnl, h: pn72.optHint}));
  check('平仓盈亏(v50.72): 提示里写明算式与「不含手续费」',
        /\(3,450 − 3,500\)/.test(pn72.futHint) && /× −1/.test(pn72.futHint) && /不含手续费/.test(pn72.futHint),
        pn72.futHint);
  check('平仓盈亏(v50.72): 手填的值不会被覆盖(不动源输入就保留)',
        pn72.manualKeep === '777', String(pn72.manualKeep));
  check('平仓盈亏(v50.72): 再动平仓数量/平仓价 就重算(777 被算回来)',
        pn72.afterTouch === '5000' && pn72.futShort2 === '4000',
        'afterTouch=' + pn72.afterTouch + ' 改价后=' + pn72.futShort2);
  check('平仓盈亏(v50.72): 编辑已有平仓记录时不覆盖库里存的值(提示里给算式供对照)',
        pn72.closeSaved && pn72.holdLeft === 0 && pn72.editPnl === '5000'
        && /\(3,450 − 3,500\)/.test(pn72.editHint) && /\+¥5,000/.test(pn72.editHint),
        JSON.stringify({saved: pn72.closeSaved, left: pn72.holdLeft, v: pn72.editPnl, h: pn72.editHint}));
  check('平仓盈亏(v50.72): 持仓已清空也能从开仓记录取到均价并重算 → 10000',
        pn72.editRe === '10000', String(pn72.editRe));

  // ===== v50.74: 新建平仓的「平今 / 平昨」手选(有些品种两档费率差很多) =====
  const v74Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const wipe = async (mode) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying='
          + encodeURIComponent(it.underlying) + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    const det = async (mode, u, b) => (await (await fetch('/api/trades/detail?mode=' + mode
      + '&underlying=' + encodeURIComponent(u) + '&batch=' + encodeURIComponent(b) + '&fee=ex')).json());
    const opBtns = () => [...document.querySelectorAll('#tmCloseToday .pick-opt')];
    const actCt = () => { const b = opBtns().find(x => x.classList.contains('active')); return b ? b.dataset.ct : null; };
    const clickCt = async v => { opBtns().find(x => x.dataset.ct === v).click(); await w(250); };

    await wipe('futures'); await wipe('options');

    // ---- A) 期货 棉花(平今 0 元/手、平昨 4.3 元/手): 同一天平 → 自动=平今
    await post({mode:'futures', underlying:'CF', contract:'CF2701', batch:'Bv74',
                op_type:'open', direction:'sell', open_date:'2026-10-12', open_time:'09:30',
                open_price:15000, qty:2, premium:0});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(900);
    await TradeUI.loadDetail('CF', 'Bv74'); await w(800);
    q('tdNewClose').click(); await w(500);
    const shownCt = q('tmCloseTodayWrap').style.display !== 'none';
    const labels = opBtns().map(b => b.textContent.trim());
    const defActive = actCt();
    const hintAuto = q('tmCloseTodayHint').textContent;
    q('tmCloseQty').value = '2'; q('tmCloseQty').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmClosePrice').value = '14900'; q('tmClosePrice').dispatchEvent(new Event('input', {bubbles:true}));
    q('tmCloseDate').value = '2026-10-12'; q('tmCloseTime').value = '14:00';
    await w(300);
    const savedAuto = await TradeUI.submitModal(); await w(900);
    q('tradeModalBg').classList.add('hidden');
    await TradeUI.loadDetail('CF', 'Bv74'); await w(700);
    let d = await det('futures', 'CF', 'Bv74');
    let cl = (d.operations || []).filter(o => o.op_type === 'close')[0] || {};
    const autoFee = cl.fee, autoCt = cl.close_today, autoToday = cl.fee_is_today, autoManual = cl.fee_today_manual;

    // ---- B) 编辑这笔平仓 → 预选「自动」+ 提示写出自动判定结果; 改成「平昨」→ 手续费变大
    document.querySelector('#tdOps [data-edit="' + cl.id + '"]').click(); await w(500);
    const editActive = actCt();
    const editHint = q('tmCloseTodayHint').textContent;
    await clickCt('0');
    const pickedActive = actCt();
    const pickedHint = q('tmCloseTodayHint').textContent;
    const savedY = await TradeUI.submitModal(); await w(900);
    q('tradeModalBg').classList.add('hidden');
    await TradeUI.loadDetail('CF', 'Bv74'); await w(700);
    d = await det('futures', 'CF', 'Bv74');
    cl = (d.operations || []).filter(o => o.op_type === 'close')[0] || {};
    const yFee = cl.fee, yCt = cl.close_today, yManual = cl.fee_today_manual, yToday = cl.fee_is_today;
    const opFeeTitle = (document.querySelector('#tdOps [data-edit="' + cl.id + '"]')
      .closest('tr').querySelector('.op-fee') || {}).title || '';

    // ---- C) 再打开 → 手选被记住(仍是平昨)
    document.querySelector('#tdOps [data-edit="' + cl.id + '"]').click(); await w(500);
    const reopenActive = actCt();
    q('tmCancel').click(); await w(200);

    // ---- D) 期权买方模式也有同一套控件(三个模式共用弹窗)
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(900);
    await post({mode:'options', underlying:'CF', contract:'CF2701-P-15000', batch:'Bv74o',
                op_type:'open', direction:'buy', call_put:'P', open_date:'2026-10-12',
                open_price:92, qty:3, premium:1380});
    await TradeUI.loadDetail('CF', 'Bv74o'); await w(800);
    q('tdNewClose').click(); await w(500);
    const optShown = q('tmCloseTodayWrap').style.display !== 'none';
    const optLabels = opBtns().map(b => b.textContent.trim());
    q('tmCancel').click(); await w(200);

    await wipe('futures'); await wipe('options');
    return JSON.stringify({shownCt, labels, defActive, hintAuto, savedAuto: !!savedAuto,
                           autoFee, autoCt, autoToday, autoManual,
                           editActive, editHint, pickedActive, pickedHint, savedY: !!savedY,
                           yFee, yCt, yManual, yToday, opFeeTitle, reopenActive,
                           optShown, optLabels});
  })()`);
  const v74 = JSON.parse(v74Run);
  check('平今/平昨(v50.74): 平仓弹窗有「自动 / 平今 / 平昨」三项, 默认自动',
        v74.shownCt === true && v74.labels.join(',') === '自动,平今,平昨' && v74.defActive === ''
        && /自动/.test(v74.hintAuto), JSON.stringify(v74.labels) + ' active=' + v74.defActive);
  check('平今/平昨(v50.74): 开仓弹窗/存盘正常, 自动档落库为 ""(空)',
        v74.savedAuto === true && v74.autoCt === '', String(v74.autoCt));
  check('平今/平昨(v50.74): 同一天平 → 自动判成平今, 棉花平今 0 元/手 → 只收 0.02',
        v74.autoToday === true && v74.autoManual === false && Math.abs(v74.autoFee - 0.02) < 1e-6,
        JSON.stringify({fee: v74.autoFee, today: v74.autoToday, manual: v74.autoManual}));
  check('平今/平昨(v50.74): 编辑平仓时预选「自动」并把自动判定结果写进提示',
        v74.editActive === '' && /自动判定：平今/.test(v74.editHint), v74.editHint);
  check('平今/平昨(v50.74): 点「平昨」→ 选中态转移 + 提示改成强制说明',
        v74.pickedActive === '0' && /强制按「平昨」/.test(v74.pickedHint), v74.pickedHint);
  check('平今/平昨(v50.74): 手选平昨后手续费变成 4.3×2 + 0.02 = 8.62, 且标记为手选',
        v74.savedY === true && v74.yCt === '0' && v74.yManual === true && v74.yToday === false
        && Math.abs(v74.yFee - 8.62) < 1e-6,
        JSON.stringify({fee: v74.yFee, ct: v74.yCt, manual: v74.yManual, today: v74.yToday}));
  check('平今/平昨(v50.74): 操作表手续费列 tooltip 标出「平昨 · 手选」',
        /平昨 · 手选/.test(v74.opFeeTitle), String(v74.opFeeTitle));
  check('平今/平昨(v50.74): 再次打开平仓弹窗时手选被记住(仍是平昨)',
        v74.reopenActive === '0', String(v74.reopenActive));
  check('平今/平昨(v50.74): 期权买方模式也有同一套三项控件',
        v74.optShown === true && v74.optLabels.join(',') === '自动,平今,平昨', JSON.stringify(v74.optLabels));

  // ===== v50.76: 统计区间(近一月/近三月/近一年/今年以来/全部) + 区间口径(开仓/平仓) =====
  // 口径: 区间收窄后统计卡与主表一起跟着变; 条数在统计卡上方与主表行数上都要对得上
  const r76Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const wipe = async (mode) => {
      const g = await (await fetch('/api/trades/groups?mode=' + mode)).json();
      for (const it of (g.groups || [])) {
        const d = await (await fetch('/api/trades/detail?mode=' + mode + '&underlying='
          + encodeURIComponent(it.underlying) + '&batch=' + encodeURIComponent(it.batch || ''))).json();
        for (const op of (d.operations || [])) await del(op.id);
      }
    };
    await wipe('futures');
    const pad = n => String(n).padStart(2, '0');
    const dayStr = off => { const d = new Date(); d.setHours(12,0,0,0); d.setDate(d.getDate() - off);
      return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()); };
    // ⚠ 参考实现(独立写法: 手动归一化月份 + 夹取日号) 与页面的 rangeFrom() 交叉验证
    const refFrom = rg => {
      if (rg === 'all') return '';
      const t = new Date(); t.setHours(12,0,0,0);
      if (rg === 'ytd') return t.getFullYear() + '-01-01';
      const back = { '1m':1, '3m':3, '1y':12 }[rg];
      let y = t.getFullYear(), m = t.getMonth() - back;
      y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
      const last = new Date(y, m + 1, 0).getDate();
      return y + '-' + pad(m+1) + '-' + pad(Math.min(t.getDate(), last));
    };
    // 种子: o=开仓距今(天), c=平仓距今(天, null=未平仓), pnl 各不相同 → 金额和能区分区间
    const S = [
      {u:'e2er5',   o: dayStr(5),     c: dayStr(5),     pnl:100},
      {u:'e2er40',  o: dayStr(40),    c: dayStr(40),    pnl:200},
      {u:'e2er200', o: dayStr(200),   c: dayStr(200),   pnl:300},
      {u:'e2er400', o: dayStr(400),   c: dayStr(400),   pnl:400},
      {u:'e2er800', o: dayStr(800),   c: dayStr(800),   pnl:500},
      // 正好落在「近一年」起算日当天(端点含在内) → 近一年要算它、今年以来不能算它
      {u:'e2er1y',  o: refFrom('1y'), c: refFrom('1y'), pnl:700},
      {u:'e2erx',   o: dayStr(500),   c: dayStr(3),     pnl:1000},   // 跨期: 老开仓 / 新平仓
      {u:'e2ery',   o: dayStr(3),     c: null,          pnl:0},      // 未平仓
    ];
    for (const s of S) {
      await post({underlying:s.u, contract:s.u+'2611', op_type:'open', direction:'buy',
                  open_date: s.o, open_price: 100, qty: 1, premium: 0, mode: 'futures'});
      if (s.c) await post({underlying:s.u, contract:s.u+'2611', op_type:'close', direction:'sell',
                  close_date: s.c, close_qty: 1, close_price: 200, pnl: s.pnl, mode: 'futures'});
    }
    const chk = document.getElementById('tradesOnlyOpen');
    if (chk.checked) { chk.checked = false; chk.dispatchEvent(new Event('change')); }
    document.getElementById('tradesSearch').value = '';
    TradeUI.mainQuery = '';
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(900);
    await TradeUI.refresh();
    await w(400);

    const read = () => {
      const cards = [...document.querySelectorAll('#tradeStats .stat-card')];
      const txt = el => (el && el.textContent) || '';
      const num = t => { const m = String(t).replace(/[^0-9.-]/g, ''); return m === '' ? null : parseFloat(m); };
      return {
        realized: num(txt(cards[0] && cards[0].querySelector('.sv'))),
        card3sub: txt(cards[2] && cards[2].querySelector('.ss')),
        note: txt(document.getElementById('rangeNote')),
        rows: document.querySelectorAll('#tradesTable tbody tr.clickable').length
      };
    };
    const setRange = async rg => { document.querySelector('#rangeSeg .fee-opt[data-range="'+rg+'"]').click(); await w(260); };
    const setBasis = async b => { document.querySelector('#basisSeg .fee-opt[data-basis="'+b+'"]').click(); await w(260); };
    const out = { ranges: [], basis: {}, meta: {} };

    // ⚠ 先清掉本机偏好再断言「默认值」: E2E 可能复用同一个 Chrome profile 重跑,
    //    上一轮留下的 oc-trade-range/-basis 会让「默认 = 全部 + 按开仓时间」假失败
    localStorage.removeItem('oc-trade-range');
    localStorage.removeItem('oc-trade-range-basis');
    TradeUI.applyRangePref();
    await w(200);

    out.meta.rangeOpts = [...document.querySelectorAll('#rangeSeg .fee-opt')].map(e => e.textContent.trim());
    out.meta.basisOpts = [...document.querySelectorAll('#basisSeg .fee-opt')].map(e => e.textContent.trim());
    out.meta.defaultRange = (document.querySelector('#rangeSeg .fee-opt.active') || {dataset:{}}).dataset.range || '';
    out.meta.defaultBasis = (document.querySelector('#basisSeg .fee-opt.active') || {dataset:{}}).dataset.basis || '';
    const rb = document.getElementById('rangeBar');
    out.meta.barOverflow = rb ? (rb.scrollWidth > rb.clientWidth + 1) : true;
    out.meta.barRows = rb ? new Set([...rb.children].map(c => Math.round(c.getBoundingClientRect().top))).size : -1;
    out.meta.cards = document.querySelectorAll('#tradeStats .stat-card').length;

    for (const rg of ['all','1m','3m','1y','ytd']) {
      await setRange(rg);
      const from = refFrom(rg);
      const exp = S.filter(s => !from || s.o >= from);
      out.ranges.push({
        rg, from, appFrom: TradeUI.rangeFrom(), got: read(),
        expN: exp.length,
        expRealized: exp.reduce((a, s) => a + s.pnl, 0)
      });
    }
    // 口径: 固定「近一月」, 两种口径必须给出不同结果
    await setRange('1m');
    const openBasis = read();
    await setBasis('close');
    const closeBasis = read();
    out.basis = { openBasis, closeBasis,
                  tipOpen: '按开仓时间时未平仓应计入',
                  noteClose: closeBasis.note,
                  hasUnclosedHint: /未平仓记录没有平仓时间/.test(closeBasis.note) };
    out.meta.lsRange = localStorage.getItem('oc-trade-range');
    out.meta.lsBasis = localStorage.getItem('oc-trade-range-basis');
    const snap = plansSnapshot();
    out.meta.snap = { tradeRange: snap.tradeRange, tradeRangeBasis: snap.tradeRangeBasis };
    // 模拟换机重开: 抹掉本机设置 → 从备份快照恢复 → 内存/选中态/localStorage 三处都要回来
    localStorage.removeItem('oc-trade-range');
    localStorage.removeItem('oc-trade-range-basis');
    TradeUI.range = 'all'; TradeUI.rangeBasis = 'open'; TradeUI.syncRangeSeg();
    const nPlans = plansRestore({ tradeRange: '3m', tradeRangeBasis: 'close' });
    await w(250);
    out.meta.restore = {
      n: nPlans, range: TradeUI.range, basis: TradeUI.rangeBasis,
      activeRange: (document.querySelector('#rangeSeg .fee-opt.active') || {dataset:{}}).dataset.range || '',
      activeBasis: (document.querySelector('#basisSeg .fee-opt.active') || {dataset:{}}).dataset.basis || '',
      lsRange: localStorage.getItem('oc-trade-range'), lsBasis: localStorage.getItem('oc-trade-range-basis')
    };
    // 三个模式共用一个区间设置
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await w(800);
    out.meta.otherMode = {
      range: TradeUI.range, basis: TradeUI.rangeBasis,
      activeRange: (document.querySelector('#rangeSeg .fee-opt.active') || {dataset:{}}).dataset.range || '',
      note: (document.getElementById('rangeNote') || {}).textContent || ''
    };
    // 统计卡 tooltip 写明区间
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click();
    await w(700);
    const c1s = document.querySelectorAll('#tradeStats .stat-card')[0];
    out.meta.cardTip = (c1s && c1s.querySelector('.sv') ? c1s.querySelector('.sv').title : '') || '';
    // v50.76: 「隐藏统计」必须把「统计区间」这一行一起收起(它属于同一块)
    const tgBtn = document.getElementById('btnToggleStats');
    const rbEl = document.getElementById('rangeBar');
    const stBox = document.getElementById('tradeStats');
    try { localStorage.removeItem('oc-trade-stats'); } catch (e) {}
    TradeUI.applyStatsPref(); await w(250);
    out.meta.hide = { shownFirst: rbEl.classList.contains('hidden') === false && stBox.classList.contains('hidden') === false };
    tgBtn.click(); await w(350);
    out.meta.hide.hiddenBoth = rbEl.classList.contains('hidden') === true && stBox.classList.contains('hidden') === true;
    out.meta.hide.rbarH = Math.round(rbEl.getBoundingClientRect().height);
    out.meta.hide.label = tgBtn.textContent.trim();
    TradeUI.applyStatsPref(); await w(200);            // 模拟「下次打开」
    out.meta.hide.reopenBoth = rbEl.classList.contains('hidden') === true && stBox.classList.contains('hidden') === true;
    tgBtn.click(); await w(350);
    out.meta.hide.backBoth = rbEl.classList.contains('hidden') === false && stBox.classList.contains('hidden') === false;
    out.meta.hide.labelBack = tgBtn.textContent.trim();
    try { localStorage.removeItem('oc-trade-stats'); } catch (e) {}
    TradeUI.applyStatsPref(); await w(200);
    return JSON.stringify(out);
  })()`);
  const r76 = JSON.parse(r76Run);
  check('统计区间(v50.76): 5 个区间项 + 2 个口径项(文案与需求一致)',
        r76.meta.rangeOpts.join(',') === '近一月,近三月,近一年,今年以来,全部'
        && r76.meta.basisOpts.join(',') === '按开仓时间,按平仓时间', JSON.stringify(r76.meta));
  check('统计区间(v50.76): 默认「全部 + 按开仓时间」(等价于原行为)',
        r76.meta.defaultRange === 'all' && r76.meta.defaultBasis === 'open', JSON.stringify(r76.meta));
  check('统计区间(v50.76): 工具条不出横向滚动且排布紧凑(≤2 行)',
        r76.meta.barOverflow === false && r76.meta.barRows <= 2,
        'overflow=' + r76.meta.barOverflow + ' rows=' + r76.meta.barRows);
  check('统计区间(v50.76): 新增工具条不影响 5 张统计卡', r76.meta.cards === 5, String(r76.meta.cards));
  for (const r of r76.ranges) {
    // 「全部」时说明写「全部记录（共 N 条）」, 其余区间写「区间内 N 条」
    const wantTxt = (r.rg === 'all') ? ('共 ' + r.expN + ' 条') : ('区间内 ' + r.expN + ' 条');
    check('统计区间(v50.76)【' + r.rg + '】条数一致: 说明文字 + 主表行数 = ' + r.expN,
          r.got.rows === r.expN && r.got.note.indexOf(wantTxt) >= 0,
          JSON.stringify({rows: r.got.rows, note: r.got.note, exp: r.expN, want: wantTxt}));
    check('统计区间(v50.76)【' + r.rg + '】累计盈亏只算区间内 = ' + r.expRealized,
          r.got.realized === r.expRealized, 'got=' + r.got.realized + ' exp=' + r.expRealized);
    check('统计区间(v50.76)【' + r.rg + '】起算日与独立实现一致',
          r.appFrom === r.from, 'app=' + r.appFrom + ' ref=' + r.from);
  }
  const r76_1y = r76.ranges.find(x => x.rg === '1y') || {};
  const r76_ytd = r76.ranges.find(x => x.rg === 'ytd') || {};
  check('统计区间(v50.76): 「近一年」与「今年以来」确实不同(跨年那笔只进近一年, 起算日各按各的)',
        r76_1y.expN > r76_ytd.expN && r76_1y.appFrom !== r76_ytd.appFrom,
        '1y=' + r76_1y.expN + '@' + r76_1y.appFrom + ' ytd=' + r76_ytd.expN + '@' + r76_ytd.appFrom);
  check('统计区间(v50.76): 按开仓时间【近一月】= 老开仓那笔不计入 / 未平仓计入(2 条, 累计 +100)',
        r76.basis.openBasis.rows === 2 && r76.basis.openBasis.realized === 100,
        JSON.stringify(r76.basis.openBasis));
  check('统计区间(v50.76): 按平仓时间【近一月】= 老开仓新平仓计入 / 未平仓排除(2 条, 累计 +1100)',
        r76.basis.closeBasis.rows === 2 && r76.basis.closeBasis.realized === 1100,
        JSON.stringify(r76.basis.closeBasis));
  check('统计区间(v50.76): 按平仓时间时说明里提示「未平仓不计入」',
        r76.basis.hasUnclosedHint === true, r76.basis.noteClose);
  check('统计区间(v50.76): 选择写入 localStorage 持久化',
        r76.meta.lsRange === '1m' && r76.meta.lsBasis === 'close',
        'range=' + r76.meta.lsRange + ' basis=' + r76.meta.lsBasis);
  check('统计区间(v50.76): 备份快照带上区间与口径',
        r76.meta.snap.tradeRange === '1m' && r76.meta.snap.tradeRangeBasis === 'close',
        JSON.stringify(r76.meta.snap));
  check('统计区间(v50.76): 导入备份能恢复区间与口径, 且不计入「方案组数」',
        r76.meta.restore.range === '3m' && r76.meta.restore.basis === 'close'
        && r76.meta.restore.activeRange === '3m' && r76.meta.restore.activeBasis === 'close'
        && r76.meta.restore.lsRange === '3m' && r76.meta.restore.n === 0,
        JSON.stringify(r76.meta.restore));
  check('统计区间(v50.76): 三个模式共用同一份区间设置(切到期权买方仍生效)',
        r76.meta.otherMode.range === '3m' && r76.meta.otherMode.activeRange === '3m'
        && r76.meta.otherMode.note.indexOf('区间内') >= 0, JSON.stringify(r76.meta.otherMode));
  check('统计区间(v50.76): 统计卡 tooltip 写明当前统计区间',
        /统计区间：/.test(r76.meta.cardTip), r76.meta.cardTip);
  check('统计区间(v50.76): 「隐藏统计」把「统计区间」那一行一起收起(不占高度)',
        r76.meta.hide.shownFirst === true && r76.meta.hide.hiddenBoth === true && r76.meta.hide.rbarH === 0,
        JSON.stringify(r76.meta.hide));
  check('统计区间(v50.76): 隐藏后「下次打开」两行仍一起隐藏, 再点一起回来(按钮文案跟着切)',
        r76.meta.hide.reopenBoth === true && r76.meta.hide.backBoth === true
        && /显示统计与区间/.test(r76.meta.hide.label) && /隐藏统计与区间/.test(r76.meta.hide.labelBack),
        JSON.stringify(r76.meta.hide));

  // 区间 + 搜索叠加(搜索只在区间内收窄), 顺带复位
  const r76bRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(700);
    document.querySelector('#rangeSeg .fee-opt[data-range="all"]').click(); await w(300);
    const all = document.querySelectorAll('#tradesTable tbody tr.clickable').length;
    const inp = document.getElementById('tradesSearch');
    inp.value = 'e2er5'; inp.dispatchEvent(new Event('input', {bubbles:true}));
    await w(300);
    const out = { all, filtered: document.querySelectorAll('#tradesTable tbody tr.clickable').length,
                  hint: (document.getElementById('tradesSearchHint')||{}).textContent || '',
                  rangeNote: (document.getElementById('rangeNote')||{}).textContent || '' };
    document.getElementById('tradesSearchClear').click(); await w(250);
    out.noteAfterClear = (document.getElementById('tradesSearchHint')||{}).textContent || '';
    return JSON.stringify(out);
  })()`);
  const r76b = JSON.parse(r76bRun);
  check('统计区间(v50.76): 区间与搜索叠加生效(全部 8 条 → 搜「e2er5」命中 1 条)',
        r76b.all === 8 && r76b.filtered === 1 && /筛选出 1 条/.test(r76b.hint), r76bRun);
  check('统计区间(v50.76): 改回「全部」时区间说明同步为全部记录',
        /全部记录/.test(r76b.rangeNote) && r76b.noteAfterClear === '', r76bRun);

  // ---- v50.77: 区间不为「全部」时, 「持仓中」必须仍按全量算(以前恒为 0) ----
  const r76AllGot = (r76.ranges.find(x => x.rg === 'all') || {}).got || {};
  check('统计区间(v50.77): 「全部」时第 3 卡小字沿用原样式(持仓中: N · 共 M 条)',
        /持仓中: 1 · 共 8 条/.test(r76AllGot.card3sub || ''), r76AllGot.card3sub);
  check('统计区间(v50.77): 「按平仓时间」下「持仓中」仍按全量算(不再恒为 0)',
        /持仓中 1（全部）/.test(r76.basis.closeBasis.card3sub || ''),
        r76.basis.closeBasis.card3sub);

  // ===== v50.77: 交易日历 / 导入失败明细 / 文案 =====
  const v77Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const out = {};
    // ① 交易日历: 周五 2026-10-09 21:30 开的仓 → 交易日是下周一 10-12;
    //    下周一 09:30 平 = 同一交易日 → 自动判「平今」(旧版会算成 10-10 周六 → 平昨)
    const o1 = await post({mode:'futures', underlying:'e2ecal', contract:'CF2701', batch:'Bcal77',
      op_type:'open', direction:'buy', open_date:'2026-10-09', open_time:'21:30',
      open_price:15000, qty:1, premium:0});
    const c1 = await post({mode:'futures', underlying:'e2ecal', contract:'CF2701', batch:'Bcal77',
      op_type:'close', direction:'sell', close_date:'2026-10-12', close_time:'09:30',
      close_qty:1, close_price:15100, pnl:500});
    const d1 = await (await fetch('/api/trades/detail?mode=futures&underlying=e2ecal&batch=Bcal77&fee=ex')).json();
    const cl1 = (d1.operations || []).find(x => x.op_type === 'close') || {};
    out.friNight = { feeIsToday: cl1.fee_is_today, fee: cl1.fee, ct: cl1.close_today };
    // 反例: 周五夜盘开、下周二(10-13)平 → 不同交易日 → 平昨
    const o2 = await post({mode:'futures', underlying:'e2ecal2', contract:'CF2701', batch:'Bcal77b',
      op_type:'open', direction:'buy', open_date:'2026-10-09', open_time:'21:30',
      open_price:15000, qty:1, premium:0});
    const c2 = await post({mode:'futures', underlying:'e2ecal2', contract:'CF2701', batch:'Bcal77b',
      op_type:'close', direction:'sell', close_date:'2026-10-13', close_time:'09:30',
      close_qty:1, close_price:15100, pnl:500});
    const d2 = await (await fetch('/api/trades/detail?mode=futures&underlying=e2ecal2&batch=Bcal77b&fee=ex')).json();
    const cl2 = (d2.operations || []).find(x => x.op_type === 'close') || {};
    out.tueClose = { feeIsToday: cl2.fee_is_today, fee: cl2.fee };
    // 清掉这两组
    for (const id of [o1.id, c1.id, o2.id, c2.id]) if (id) await del(id);
    // ② 导入失败明细: 一条合法 + 一条坏记录 → 接口要带回 failed / failed_samples
    const imp = await (await fetch('/api/funds/import', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({records: [], trades: [
        {underlying:'e2eimpok', contract:'e2eimpokC1', op_type:'open', direction:'buy',
         open_date:'2026-01-05', open_price:1, qty:1, premium:1, mode:'options'},
        {op_type:'open'}]})})).json();
    out.imp = { ok: imp.ok, imported: imp.imported, failed: imp.failed, samples: imp.failed_samples };
    // 清掉刚导进去的那条
    const g = await (await fetch('/api/trades/groups?mode=options')).json();
    for (const it of (g.groups || [])) {
      if (it.underlying !== 'e2eimpok') continue;
      const dd = await (await fetch('/api/trades/detail?mode=options&underlying=e2eimpok&batch='
        + encodeURIComponent(it.batch || ''))).json();
      for (const op of (dd.operations || [])) await del(op.id);
    }
    // ③ 「清除全部」按钮文案: 只清资金曲线, 不该写成「全部」
    const cb = document.getElementById('btnClearAll');
    out.clearBtn = cb ? cb.textContent.trim() : '';
    out.clearTip = cb ? cb.title : '';
    return JSON.stringify(out);
  })()`);
  const v77 = JSON.parse(v77Run);
  check('交易日历(v50.77): 周五夜盘(10-09 21:30)开 + 下周一(10-12)早盘平 → 自动判「平今」',
        v77.friNight.feeIsToday === true && Math.abs(v77.friNight.fee - 0.01) < 1e-6,
        JSON.stringify(v77.friNight));
  check('交易日历(v50.77): 换到下周二(10-13)平就变「平昨」(4.31)',
        v77.tueClose.feeIsToday === false && Math.abs(v77.tueClose.fee - 4.31) < 1e-6,
        JSON.stringify(v77.tueClose));
  check('导入失败明细(v50.77): 接口带回 failed 与明细(不再假装全成功)',
        v77.imp.ok === true && v77.imp.imported === 1 && v77.imp.failed === 1
        && Array.isArray(v77.imp.samples) && /交易记录 第 2 条/.test(v77.imp.samples[0] || ''),
        JSON.stringify(v77.imp));
  check('文案(v50.77): 清除按钮明说只清「资金曲线」',
        /资金曲线/.test(v77.clearBtn) && !/清除全部/.test(v77.clearBtn)
        && /交易记录/.test(v77.clearTip), v77.clearBtn + ' | ' + v77.clearTip);

  // ===== v50.78: 三档优化(平仓两档手续费 / 未平仓风险汇总 / 恢复前差异预览) =====
  const v78Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const out = {};
    // ---- ① 平仓弹窗直接显示两档手续费金额(棉花平今 0 / 平昨 4.3) ----
    // ⚠ 标的要用**真实品种**(CF) —— 弹窗是拿 标的/合约 去解析品种查费率表的,
    //   拿 e2e 自己的假名(如 e2efee78)会解析不出来, 会误显示"费率表未收录"
    const o = await post({mode:'futures', underlying:'CF', contract:'CF2701', batch:'Bfee78',
      op_type:'open', direction:'buy', open_date:'2026-09-01', open_time:'10:00',
      open_price:15000, qty:5, premium:0});
    // 顺带造一笔期权未平仓, 用来看期权口径的「占用权利金」
    const oo = await post({mode:'options', underlying:'sm', contract:'sm2701C6000', batch:'Bopt78',
      op_type:'open', direction:'buy', open_date:'2026-09-01', open_price:50, qty:4, premium:2000});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(900);
    await TradeUI.loadDetail('CF', 'Bfee78'); await w(700);
    // 走真实入口: 详情页的「新建平仓」
    const nb = [...document.querySelectorAll('#tradesArea button')]
      .find(b => /新建平仓/.test(b.textContent));
    if (nb) { nb.click(); await w(500); }
    const setV = (id, v) => { const el = document.getElementById(id);
      el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
    setV('tmClosePrice', '15100');
    setV('tmCloseQty', '2');
    await w(900);          // 等 debounce(150ms) + 请求
    const hintEl = document.getElementById('tmFeeHint');
    out.feeHint = hintEl ? hintEl.textContent : '';
    out.feeHtml = hintEl ? hintEl.innerHTML : '';
    out.feeShown = !!hintEl && !/hidden/.test(hintEl.parentElement.className);
    // 点「平今」→ 高亮应该挪到平今那一档(只换高亮, 不重新请求)
    const seg = document.querySelector('#tmCloseToday .pick-opt[data-ct="1"]');
    if (seg) { seg.click(); await w(400); }
    out.feeHtmlAfterPick = hintEl ? hintEl.innerHTML : '';
    out.pickedCt = TradeUI._closeToday;
    document.getElementById('tmCancel').click(); await w(400);
    // ---- ② 未平仓风险汇总(期货: 占用保证金 / 理论最大亏损) ----
    const rk = await (await fetch('/api/trades/open-risk?mode=futures')).json();
    out.risk = { positions: rk.positions, lots: rk.lots, occupied: rk.occupied, maxLoss: rk.max_loss,
                 partial: rk.max_loss_is_partial, noStop: rk.no_stop_lots };
    const rb = document.getElementById('openRisk');
    out.riskBar = { shown: rb && !rb.classList.contains('hidden'),
                    text: rb ? rb.textContent : '', title: rb ? rb.title : '' };
    // 期权买方模式换个口径(占用权利金)
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(1100);
    const rbO = document.getElementById('openRisk');
    const rkO = await (await fetch('/api/trades/open-risk?mode=options')).json();
    out.riskBarOpt = { text: rbO ? rbO.textContent : '', positions: rkO.positions,
                       occupied: rkO.occupied, maxLoss: rkO.max_loss };
    // ---- ③ 恢复备份前的差异预览接口 ----
    await fetch('/api/funds/backup', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    await w(300);
    const bl = await (await fetch('/api/funds/backups')).json();
    const nm = (bl.backups || [])[0] ? bl.backups[0].name : '';
    const dd = await (await fetch('/api/funds/backup-diff?name=' + encodeURIComponent(nm))).json();
    out.diff = { ok: dd.ok, nTables: (dd.tables || []).length,
                 hasTrade: (dd.tables || []).some(t => t.key === 'trade_records' && t.delta === 0),
                 lines: (dd.lines || []).join(' | ') };
    const bad = await (await fetch('/api/funds/backup-diff?name=' + encodeURIComponent('abc.db'))).json();
    out.diffBad = { ok: bad.ok, error: bad.error || '' };
    await del(o.id); await del(oo.id);
    return JSON.stringify(out);
  })()`);
  const v78 = JSON.parse(v78Run);
  check('平仓两档手续费(v50.78): 弹窗提示里同时给出平今与平昨金额',
        /平今\s*¥0\.02/.test(v78.feeHint.replace(/\s+/g, ' ')) && /平昨\s*¥8\.62/.test(v78.feeHint.replace(/\s+/g, ' ')),
        v78.feeHint);
  check('平仓两档手续费(v50.78): 自动档时提示「当前：自动」',
        /当前：自动/.test(v78.feeHint), v78.feeHint);
  check('平仓两档手续费(v50.78): 点「平今」后高亮挪到平今那一档',
        v78.pickedCt === '1' && /class="on">平今/.test(v78.feeHtmlAfterPick)
        && !/class="on">平昨/.test(v78.feeHtmlAfterPick), v78.feeHtmlAfterPick);
  check('风险汇总(v50.78): 接口给出未平仓笔数/手数/占用/最大亏损',
        v78.risk.positions >= 1 && v78.risk.lots >= 5 && v78.risk.occupied > 0,
        JSON.stringify(v78.risk));
  check('风险汇总(v50.78): 期货口径写「占用保证金」+「理论最大亏损」+「没设止损」提醒',
        v78.riskBar.shown && /未平仓/.test(v78.riskBar.text) && /占用保证金/.test(v78.riskBar.text)
        && /理论最大亏损/.test(v78.riskBar.text) && /没设初始止损/.test(v78.riskBar.text),
        v78.riskBar.text);
  check('风险汇总(v50.78): 悬停可看逐笔明细(标的/合约/占用/最大亏损)',
        /占用 CN¥/.test(v78.riskBar.title) && /最大亏损 CN¥/.test(v78.riskBar.title),
        v78.riskBar.title.slice(0, 160));
  check('风险汇总(v50.78): 期权买方换成「占用权利金」口径',
        /占用权利金/.test(v78.riskBarOpt.text) && v78.riskBarOpt.positions >= 1
        && v78.riskBarOpt.maxLoss > 0, JSON.stringify(v78.riskBarOpt));
  check('备份差异(v50.78): 接口返回四张表的备份/当前条数',
        v78.diff.ok === true && v78.diff.nTables === 4 && v78.diff.hasTrade === true,
        JSON.stringify(v78.diff));
  check('备份差异(v50.78): 差异行写明「备份 N 条 / 现在 N 条」',
        /交易记录：备份 \d+ 条 \/ 现在 \d+ 条/.test(v78.diff.lines), v78.diff.lines);
  check('备份差异(v50.78): 非法文件名返回 ok=false + 人话错误',
        v78.diffBad.ok === false && /不合法/.test(v78.diffBad.error), JSON.stringify(v78.diffBad));

  // ===== v50.79: 开仓手续费核算(弹窗金额 + 详情页合计) + 点合约代码复制 =====
  const v79Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const setV = (id, v) => { const el = document.getElementById(id);
      el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); };
    const out = {};
    // 棉花: 期货 4.3 元/手 + 0.01 申报费 = 4.31/手
    const o = await post({mode:'futures', underlying:'CF', contract:'CF2701', batch:'Bfee79',
      op_type:'open', direction:'buy', open_date:'2026-09-01', open_time:'10:00',
      open_price:15000, qty:3, premium:0, init_stop:14800});
    const c = await post({mode:'futures', underlying:'CF', contract:'CF2701', batch:'Bfee79',
      op_type:'close', direction:'sell', close_date:'2026-09-10', close_time:'10:00',
      close_qty:1, close_price:15200, pnl:1000});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(900);
    await TradeUI.loadDetail('CF', 'Bfee79'); await w(700);

    // ---- ① 详情页 meta: 手续费合计 / 净盈亏 ----
    const metaEl = document.getElementById('tdMeta');
    out.metaText = metaEl ? metaEl.textContent.replace(/\\s+/g, ' ') : '';
    out.metaHtml = metaEl ? metaEl.innerHTML : '';
    const dApi = await (await fetch('/api/trades/detail?underlying=CF&mode=futures&batch=Bfee79&fee=ex')).json();
    out.detailFee = { fee: dApi.fee_total, net: dApi.net_pnl, gross: dApi.total_pnl };

    // ---- ② 开仓弹窗也显示手续费金额(走真实入口: 详情页「新建开仓」) ----
    const ab = [...document.querySelectorAll('#tradesArea button')]
      .find(b => /新建开仓/.test(b.textContent));
    if (ab) { ab.click(); await w(500); }
    out.isOpenModal = TradeUI._closeModal === false;
    setV('tmContract', 'CF2701');
    setV('tmOpenPrice', '15000');
    setV('tmQty', '3');
    await w(900);
    const fh = document.getElementById('tmFeeHint');
    out.openFeeText = fh ? fh.textContent : '';
    out.openFeeHtml = fh ? fh.innerHTML : '';
    // ⚠ 提示必须真的**看得见**: 早先它写在 #tmCloseTodayWrap 里, 开仓弹窗把那个 label
    //   整个 display:none → 文本设进去了但用户看不到(E2E 只看 textContent 会漏)
    out.openFeeVisible = !!(fh && fh.offsetParent !== null);
    out.openFeeInHiddenWrap = !!(fh && fh.closest('#tmCloseTodayWrap'));
    // 「未填数量」时的引导语也要是开仓口径(不是"平今/平昨")
    setV('tmQty', '');
    await w(700);
    out.openFeeEmpty = fh ? fh.textContent : '';
    document.getElementById('tmCancel').click(); await w(400);
    // 平仓弹窗仍是两档(不能被开仓口径串了)
    const nb = [...document.querySelectorAll('#tradesArea button')]
      .find(b => /新建平仓/.test(b.textContent));
    if (nb) { nb.click(); await w(500); }
    out.isCloseModal = TradeUI._closeModal === true;
    setV('tmClosePrice', '15200');
    setV('tmCloseQty', '1');
    await w(900);
    out.closeFeeText = fh ? fh.textContent : '';
    out.closeFeeVisible = !!(fh && fh.offsetParent !== null);
    document.getElementById('tmCancel').click(); await w(400);

    // ---- ③ 点合约代码复制(持仓汇总 + 操作记录) ----
    let copied = null;
    out.clipStubOk = false;
    try {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: t => { copied = t; return Promise.resolve(); } }, configurable: true });
      out.clipStubOk = true;
    } catch (e) { out.clipStubErr = String(e); }
    const holdCell = document.querySelector('#tdHoldings td .copyc');
    const opCell = document.querySelector('#tdOps tr td .copyc');
    out.copyTargets = { holding: holdCell ? holdCell.dataset.copy : '',
                        op: opCell ? opCell.dataset.copy : '',
                        opTitle: opCell ? opCell.title : '',
                        opCursor: opCell ? getComputedStyle(opCell).cursor : '' };
    if (opCell) { opCell.click(); await w(300); }
    out.copiedOp = copied;
    const tEl = document.getElementById('ocToast');
    out.toast = tEl ? { on: tEl.classList.contains('on'), text: tEl.textContent } : null;
    copied = null;
    if (holdCell) { holdCell.click(); await w(300); }
    out.copiedHold = copied;
    // 复制不能误触「按合约筛选」下拉或行内编辑
    out.filterAfter = document.getElementById('tdContractFilter').value;

    await del(o.id); await del(c.id);
    return JSON.stringify(out);
  })()`);
  const v79 = JSON.parse(v79Run);
  const fmt2 = v => Math.abs(v).toLocaleString('en-US', {maximumFractionDigits: 2});
  check('开仓手续费(v50.79): 详情页 meta 显示「手续费合计」(含开仓那笔)',
        v79.detailFee.fee > 0 && v79.metaText.indexOf('手续费合计 CN¥' + fmt2(v79.detailFee.fee)) >= 0,
        v79.metaText + ' || fee=' + v79.detailFee.fee);
  check('开仓手续费(v50.79): 详情页 meta 的「净盈亏」= 平仓盈亏 − 手续费',
        v79.metaText.indexOf('净盈亏') >= 0
        && v79.metaText.indexOf(fmt2(v79.detailFee.net)) >= 0
        && Math.abs(v79.detailFee.net - (v79.detailFee.gross - v79.detailFee.fee)) < 1e-6,
        JSON.stringify(v79.detailFee));
  check('开仓手续费(v50.79): 开仓弹窗显示「开仓手续费 ¥12.93」(棉花 3 手 = 3×4.31)',
        v79.isOpenModal === true && /开仓手续费：¥12[.]93/.test(v79.openFeeText),
        v79.openFeeText);
  check('开仓手续费(v50.79): 开仓弹窗不出现「平今/平昨」字样(那是平仓口径)',
        !/[平]今|[平]昨/.test(v79.openFeeText), v79.openFeeText);
  check('开仓手续费(v50.79): 提示真的看得见(没被平今/平昨那格的 display:none 一起藏掉)',
        v79.openFeeVisible === true && v79.openFeeInHiddenWrap === false,
        'visible=' + v79.openFeeVisible + ' inHiddenWrap=' + v79.openFeeInHiddenWrap);
  check('开仓手续费(v50.79): 没填数量时给的是开仓引导语',
        /填了开仓数量/.test(v79.openFeeEmpty), v79.openFeeEmpty);
  check('平仓两档(v50.79): 平仓弹窗仍是平今/平昨两档(开仓口径没串味)',
        v79.isCloseModal === true && /平今/.test(v79.closeFeeText) && /平昨/.test(v79.closeFeeText),
        v79.closeFeeText);
  check('平仓两档(v50.79): 平仓时手续费提示同样可见',
        v79.closeFeeVisible === true, String(v79.closeFeeVisible));
  check('复制合约(v50.79): 持仓汇总 + 操作记录的合约都可点(带 data-copy 与手型光标)',
        /^cf2701$/i.test(v79.copyTargets.holding) && v79.copyTargets.holding === v79.copyTargets.op
        && /点击复制/.test(v79.copyTargets.opTitle) && v79.copyTargets.opCursor === 'pointer',
        JSON.stringify(v79.copyTargets));
  check('复制合约(v50.79): 点操作记录的合约 → 写入剪贴板的就是表里显示的合约代码',
        !!v79.copiedOp && v79.copiedOp === v79.copyTargets.op, String(v79.copiedOp));
  check('复制合约(v50.79): 复制后弹出轻提示「已复制：<合约>」',
        !!v79.toast && v79.toast.on === true && v79.toast.text === ('已复制：' + v79.copyTargets.op),
        JSON.stringify(v79.toast));
  check('复制合约(v50.79): 点持仓汇总的合约同样能复制',
        !!v79.copiedHold && v79.copiedHold === v79.copyTargets.holding, String(v79.copiedHold));
  check('复制合约(v50.79): 点合约不误触其它交互(合约筛选保持「全部合约」)',
        v79.filterAfter === '', v79.filterAfter);

  // ===== v50.80: 日期转义/校验 + 风险条保证金率口径 + 交易日历年度提醒 + 复制兜底 =====
  const v80Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const out = {};

    // ---- ① 日期: 写入侧拒绝构造过的串 + 显示侧转义 ----
    out.badDate = await post({mode:'futures', underlying:'xss80', contract:'xss802701',
      op_type:'open', direction:'buy', open_date:'<img src=x onerror=1>', open_price:100, qty:1});
    out.fmtDateEsc = TradeUI.fmtDate('<img src=x onerror=1>');
    out.fmtDateOk = TradeUI.fmtDate('2026-10-14');
    out.fmtDateEmpty = TradeUI.fmtDate('');
    const g0 = await (await fetch('/api/trades/groups?mode=futures')).json();
    out.noXssRec = !(g0.groups || []).some(x => x.underlying === 'xss80');

    // ---- ② 风险条: 有自定义保证金率时文案要照实说(不能写死「按 16% 估」) ----
    const o = await post({mode:'futures', underlying:'ap', contract:'ap2701', batch:'Bmr80',
      op_type:'open', direction:'buy', open_date:'2026-09-01', open_price:9000, qty:3,
      premium:0, init_stop:8800, calc_json:'{"margin_rate": 0.22}'});
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1100);
    const rk = await (await fetch('/api/trades/open-risk?mode=futures')).json();
    out.mr = { rates: rk.margin_rates, custom: rk.margin_rate_custom, occupied: rk.occupied };
    const rb = document.getElementById('openRisk');
    out.mrTip = rb ? rb.textContent.replace(/\\s+/g, ' ') : '';
    out.mrTitle = rb ? rb.title : '';
    await del(o.id);

    // ---- ③ 交易日历年度提醒横幅(打桩 data-info, 用完立刻还原) ----
    const oldFetch = window.fetchT;
    window.fetchT = () => Promise.resolve({ ok: true, json: () => Promise.resolve(
      { ok: true, risky: false, data_dir: 'X', holiday_missing: [2027, 2028] }) });
    try {
      sessionStorage.removeItem('calStaleClosed');
      await FundUI.checkDataSafety(); await w(300);
    } finally { window.fetchT = oldFetch; }
    const cb = document.getElementById('calStaleBanner');
    out.cal = { text: cb ? cb.textContent.replace(/\\s+/g, ' ') : '',
                shown: cb ? !cb.classList.contains('hidden') : false };
    document.getElementById('calStaleClose').click(); await w(200);
    out.calClosed = cb.classList.contains('hidden') && sessionStorage.getItem('calStaleClosed') === '1';
    sessionStorage.removeItem('calStaleClosed');
    cb.classList.add('hidden');
    const di = await (await fetch('/api/funds/data-info')).json();
    out.calReal = { missing: di.holiday_missing, years: di.holiday_years };

    // ---- ④ 复制兜底: execCommand 抛异常时也要把临时 textarea 收掉 ----
    const taCount = () => document.querySelectorAll('body > textarea').length;
    const oldExec = document.execCommand;
    let ret = null;
    try {
      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
      document.execCommand = () => { throw new Error('boom'); };
      out.copyBefore = taCount();
      ret = await TradeUI.copyText('abc');
    } finally {
      document.execCommand = oldExec;
    }
    out.copyFallback = { ret: ret, before: out.copyBefore, after: taCount() };
    return JSON.stringify(out);
  })()`);
  const v80 = JSON.parse(v80Run);
  check('日期(v50.80): 写入侧拒绝构造过的日期串(接口 ok=false + 人话)',
        v80.badDate.ok === false && /日期/.test(v80.badDate.error || ''), JSON.stringify(v80.badDate));
  check('日期(v50.80): 脏日期被拒后没留下记录', v80.noXssRec === true, String(v80.noXssRec));
  check('日期(v50.80): 显示侧对非日期串做转义(老库里的脏数据也挡得住)',
        /&lt;img/.test(v80.fmtDateEsc) && v80.fmtDateEsc.indexOf('<img') < 0, v80.fmtDateEsc);
  check('日期(v50.80): 正常日期原样、空值给「—」',
        v80.fmtDateOk === '2026-10-14' && v80.fmtDateEmpty === '—',
        v80.fmtDateOk + ' | ' + v80.fmtDateEmpty);
  check('风险条(v50.80): 有自定义保证金率时不再写死「按 16% 估」',
        /各笔记录里的保证金率/.test(v80.mrTip) && /22%/.test(v80.mrTip) && !/按 16% 估/.test(v80.mrTip),
        v80.mrTip);
  check('风险条(v50.80): 接口返回实际用到的保证金率 + 自定义标记',
        v80.mr.custom === true && (v80.mr.rates || []).indexOf(0.22) >= 0, JSON.stringify(v80.mr));
  check('风险条(v50.80): 逐笔明细 tooltip 也标出保证金率',
        /保证金率 22%/.test(v80.mrTitle), v80.mrTitle.slice(0, 200));
  check('日历提醒(v50.80): data-info 带 holiday_years / holiday_missing',
        Array.isArray(v80.calReal.missing) && (v80.calReal.years || []).indexOf(2026) >= 0
        && v80.calReal.missing.length === 0, JSON.stringify(v80.calReal));
  check('日历提醒(v50.80): 缺年份时显示横幅并写明年份与后果',
        v80.cal.shown === true && /2027 年/.test(v80.cal.text) && /2028 年/.test(v80.cal.text)
        && /平今/.test(v80.cal.text), v80.cal.text);
  check('日历提醒(v50.80): 「知道了」收起并本次会话不再提示', v80.calClosed === true, String(v80.calClosed));
  check('复制兜底(v50.80): execCommand 抛异常时返回 false 且不残留隐藏 textarea',
        v80.copyFallback.ret === false && v80.copyFallback.after === v80.copyFallback.before,
        JSON.stringify(v80.copyFallback));

  // ===== v50.81: 双买手数按 delta 中性优先 + 主表表头排序(三态) =====
  const v81Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const del = id => fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify({id})}).then(r=>r.json());
    const out = {};
    const set = (id, v, e) => { const el = document.getElementById(id);
      el.value = v; el.dispatchEvent(new Event(e || 'input', {bubbles:true})); };

    // ---- ① 双买: 玻璃 800 跨式(call 63 → 每手 1260, put 7.5 → 每手 150), 走真实入口 ----
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(600);
    document.querySelector('.mode[data-mode="dual"]').click(); await w(400);
    set('equity', '11');
    const sd = document.getElementById('cSearchD');
    sd.value = 'fg'; sd.dispatchEvent(new Event('input'));
    sd.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); await w(800);
    set('dualMonth', '2701', 'change');
    set('callStrike', '800'); set('callDelta', '0.8291');
    set('callPrice', '63');
    set('putStrike', '800'); set('putDelta', '-0.1973');
    set('putPrice', '7.5');
    set('riskAmountD', '3', 'change');
    await w(1400);
    const snap = () => ({
      qc: (document.getElementById('rQtyCD') || {}).textContent.trim(),
      qp: (document.getElementById('rQtyPD') || {}).textContent.trim(),
      delta: (document.getElementById('rDeltaD') || {}).textContent.trim(),
      hint: (document.getElementById('rQtyHintD') || {}).textContent.trim(),
    });
    out.p3 = snap();
    set('riskAmountD', '2', 'change'); await w(1200);
    out.p2 = snap();
    // 风险额度只有 0.5/1/1.5/2/3 → 想看「规模翻倍」得抬权益: 13 万 × 3% = 3900(2:8 只要 3720)
    set('riskAmountD', '3', 'change'); await w(1000);
    set('equity', '13'); await w(1300);
    out.p13 = snap();
    set('equity', '11'); await w(1000);

    // ---- ② 主表排序: 造 3 条(开仓时间不同 + 盈亏不同 + 一条未平仓) ----
    //   ⚠ 开仓行必须带 premium(库里 NOT NULL), 漏了会报 raw SQL 错误 → 造数静默失败
    const U = 'e2esort81';
    const seedRes = [];
    const mk = async (b, od, ot, cd, pnl) => {
      seedRes.push(await post({mode:'futures', underlying:U, contract:'rb2701', batch:b,
        op_type:'open', direction:'buy', open_date:od, open_time:ot, open_price:3200, qty:1,
        premium:0}));
      if (cd) await post({mode:'futures', underlying:U, contract:'rb2701', batch:b,
        op_type:'close', direction:'sell', close_date:cd, close_time:'14:00', close_qty:1,
        close_price:3300, pnl:pnl});
    };
    await mk('B81a', '2026-03-05', '10:00', '2026-03-20', 500);
    await mk('B81b', '2026-05-20', '10:00', '2026-06-01', -300);
    await mk('B81c', '2026-07-10', '10:00', null, 0);
    out.seedOk = seedRes.map(r => !!r.ok);
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1400);
    // 归零可能影响顺序的视图状态(别的用例可能留了搜索/只展示未平仓/统计区间)
    TradeUI.onlyOpen = false; TradeUI.mainQuery = '';
    const sb = document.getElementById('tradesSearch');
    if (sb) sb.value = '';
    const scb = document.getElementById('tradesSearchClear');
    if (scb) scb.hidden = true;
    TradeUI.range = 'all'; TradeUI.sortKey = ''; TradeUI.sortState = 0;
    TradeUI.syncSortHead(); TradeUI.showAll = true; TradeUI.renderMain(); await w(400);
    const order = () => [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
      .map(tr => tr.dataset.b || '').filter(b => b.indexOf('B81') === 0);
    const th = k => document.querySelector('#tradesTable thead th[data-sort="' + k + '"]');
    const dir = k => th(k).getAttribute('data-dir');
    const title = () => (document.getElementById('mainTableOrder') || {}).textContent || '';
    out.def = order();
    out.titleDef = title();
    th('open_date').click(); await w(300);
    out.odAsc = { order: order(), dir: dir('open_date') };
    th('open_date').click(); await w(300);
    out.odDesc = { order: order(), dir: dir('open_date') };
    th('open_date').click(); await w(300);
    out.back = { order: order(), dir: dir('open_date') };
    th('total_pnl').click(); await w(300);
    out.pnlAsc = { order: order(), dir: dir('total_pnl'), odDir: dir('open_date') };
    out.titleAsc = title();
    th('total_pnl').click(); await w(300);
    out.pnlDesc = { order: order() };
    out.titleDesc = title();
    th('close_status').click(); await w(300);
    out.stAsc = { order: order() };
    // 表头文本不能被污染(箭头是 CSS ::after, 不写进 textContent)
    out.headers = [...document.querySelectorAll('#tradesTable thead th')].map(t => t.textContent.trim());
    out.opSortable = !!document.querySelector('#tradesTable thead th:nth-child(8)').dataset.sort;
    // 恢复默认 + 清理造的数据
    TradeUI.sortKey = ''; TradeUI.sortState = 0; TradeUI.showAll = false;
    TradeUI.syncSortHead(); TradeUI.renderMain(); await w(300);
    out.restored = order();
    out.titleBack = title();
    for (const b of ['B81a', 'B81b', 'B81c']) {
      const d = await (await fetch('/api/trades/detail?underlying=' + encodeURIComponent(U)
        + '&mode=futures&batch=' + encodeURIComponent(b))).json();
      for (const op of (d.operations || [])) await del(op.id);
    }
    return JSON.stringify(out);
  })()`);
  const v81 = JSON.parse(v81Run);
  check('双买(v50.81): 造数成功(3 条开仓都写进去了)', v81.seedOk.join(',') === 'true,true,true',
        JSON.stringify(v81.seedOk));
  check('双买(v50.81): 玻璃 3% 预算 → Call 1 : Put 4(不是老算法的 1:6)',
        v81.p3.qc === '1' && v81.p3.qp === '4', v81.p3.qc + ':' + v81.p3.qp);
  check('双买(v50.81): 两腿净 delta 接近中性(差 <5%)',
        /0\.0399/.test(v81.p3.delta) && (function () {
          const m = v81.p3.delta.match(/两腿差 ([\d.]+)%/);
          return !m || parseFloat(m[1]) < 5;
        })(), v81.p3.delta);
  check('双买(v50.81): 2% 预算给同一组手数(装不下下一档)',
        v81.p2.qc === '1' && v81.p2.qp === '4', v81.p2.qc + ':' + v81.p2.qp);
  check('双买(v50.81): 预算加到 3900 → 2 : 8(规模翻倍且仍中性)',
        v81.p13.qc === '2' && v81.p13.qp === '8', v81.p13.qc + ':' + v81.p13.qp);
  check('双买(v50.81): 预算没用完时写明原因(还剩多少 + 下一档要多少)',
        /预算还剩/.test(v81.p3.hint) && /2 手 call \+ 8 手 put/.test(v81.p3.hint)
        && /超预算/.test(v81.p3.hint), v81.p3.hint);
  check('主表排序(v50.81): 默认顺序 = 开仓时间倒序(最新在顶)',
        v81.def.join(',') === 'B81c,B81b,B81a', JSON.stringify(v81.def));
  check('主表排序(v50.81): 点「开仓时间」→ 升序(最早在顶) + 表头标 asc',
        v81.odAsc.order.join(',') === 'B81a,B81b,B81c' && v81.odAsc.dir === 'asc',
        JSON.stringify(v81.odAsc));
  check('主表排序(v50.81): 再点 → 降序 + 表头标 desc',
        v81.odDesc.order.join(',') === 'B81c,B81b,B81a' && v81.odDesc.dir === 'desc',
        JSON.stringify(v81.odDesc));
  check('主表排序(v50.81): 第三次点 → 恢复默认顺序 + 标记清掉',
        v81.back.order.join(',') === 'B81c,B81b,B81a' && v81.back.dir === null,
        JSON.stringify(v81.back));
  check('主表排序(v50.81): 换列排序(平仓盈亏 升序 = -300 → 0 → +500) + 旧列标记清掉',
        v81.pnlAsc.order.join(',') === 'B81b,B81c,B81a' && v81.pnlAsc.dir === 'asc'
        && v81.pnlAsc.odDir === null, JSON.stringify(v81.pnlAsc));
  check('主表排序(v50.81): 平仓盈亏降序 = +500 → 0 → -300',
        v81.pnlDesc.order.join(',') === 'B81a,B81c,B81b', JSON.stringify(v81.pnlDesc));
  check('主表排序(v50.81): 「是否平仓」升序 = 未平仓 → 已平仓(同状态按开仓时间)',
        v81.stAsc.order.join(',') === 'B81c,B81a,B81b', JSON.stringify(v81.stAsc));
  check('主表排序(v50.81): 表头文字没被箭头污染(箭头走 CSS ::after)',
        v81.headers.indexOf('净盈亏') >= 0 && v81.headers.indexOf('开仓时间') >= 0
        && !v81.headers.some(h => /[↕↑↓]/.test(h)), JSON.stringify(v81.headers));
  check('主表排序(v50.81): 「操作」列不可排序', v81.opSortable === false, String(v81.opSortable));
  check('主表排序(v50.81): 恢复默认后顺序与初始一致', v81.restored.join(',') === 'B81c,B81b,B81a',
        JSON.stringify(v81.restored));
  check('主表排序(v50.81): 标题里的排序口径跟着变(不再是「按开仓时间倒序」)',
        /按开仓时间倒序/.test(v81.titleDef) && /按 平仓盈亏 升序/.test(v81.titleAsc)
        && /按 平仓盈亏 降序/.test(v81.titleDesc) && /按开仓时间倒序/.test(v81.titleBack),
        v81.titleAsc + ' | ' + v81.titleDesc + ' | ' + v81.titleBack);

  // ===== v50.82: 「开仓标的」列限宽 + 合约换行(多合约记录不再把表撑出横向滚动条) =====
  const wide82 = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const U = 'e2ewide82';
    const cs = ['lc2701-P-114000', 'lc2701-P-100000', 'lc2701-P-92000'];
    const seeds = [];
    for (const c of cs) {
      seeds.push(await post({mode:'futures', underlying: U, contract: c, batch: 'Bw82',
        op_type:'open', direction:'buy', open_date:'2026-10-10', open_time:'10:00',
        open_price: 8000, qty: 2, premium: 0}));
    }
    // 字号档位会影响列宽 → 固定成中号, 保证测量可复现
    document.body.classList.remove('fz-sm', 'fz-lg');
    document.body.classList.add('fz-md');
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1400);
    TradeUI.onlyOpen = false; TradeUI.mainQuery = ''; TradeUI.range = 'all';
    TradeUI.showAll = true; TradeUI.renderMain(); await w(400);
    return JSON.stringify({ seeded: seeds.filter(s => s.ok).length });
  })()`);
  const w82 = JSON.parse(wide82);
  check('表宽(v50.82): 3 个长合约名的记录造好了', w82.seeded === 3, wide82);

  // 窄窗口(≈半屏 1080)下量: 表格不许比容器宽(否则下面冒横向滚动条)
  await send(ws, 'Emulation.setDeviceMetricsOverride', {width: 1080, height: 1000, deviceScaleFactor: 1, mobile: false});
  await sleep(700);
  const narrow82 = await evalJs(ws, `(() => {
    const tb = document.getElementById('tradesTable');
    const wrap = tb.closest('.tblwrap');
    const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
      .find(x => x.dataset.u === 'e2ewide82');
    const td = tr ? tr.querySelector('td.col-name') : null;
    const sub = td ? td.querySelector('.u-sub') : null;
    const chip = sub ? sub.querySelector('i') : null;
    const ccs = chip ? getComputedStyle(chip) : null;
    return JSON.stringify({
      view: window.innerWidth, client: wrap.clientWidth, scroll: wrap.scrollWidth,
      tableW: tb.scrollWidth,
      nameW: td ? Math.round(td.getBoundingClientRect().width) : -1,
      subH: sub ? Math.round(sub.getBoundingClientRect().height) : -1,
      chipH: chip ? chip.offsetHeight : -1,
      // 换行判定: 每个合约是一个 inline-block, 数它们落在几行上(比 lineHeight 稳 —— 那个是 'normal')
      chipTops: sub ? [...sub.querySelectorAll('i')].map(i => i.offsetTop) : [],
      chips: sub ? sub.querySelectorAll('i').length : 0,
      chipWs: ccs ? ccs.whiteSpace : '', chipDisp: ccs ? ccs.display : '',
      sep: chip ? (getComputedStyle(chip, '::after').content || '') : '',
      subTxt: sub ? sub.textContent : '',
    });
  })()`);
  const n82 = JSON.parse(narrow82);
  await send(ws, 'Emulation.setDeviceMetricsOverride', {width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false});
  await sleep(400);
  check('表宽(v50.82): 半屏(1080)下整张表不再超出容器(没有横向滚动条)',
        n82.scroll <= n82.client + 1, JSON.stringify({view: n82.view, client: n82.client, scroll: n82.scroll}));
  check('表宽(v50.82): 「开仓标的」列被限宽(不再跟着合约串一起变宽)',
        n82.nameW > 0 && n82.nameW <= 260, 'nameW=' + n82.nameW);
  check('表宽(v50.82): 多合约换行显示(而不是挤成一行把表撑宽)',
        n82.chips === 3 && new Set(n82.chipTops).size >= 2 && n82.subH > n82.chipH + 2,
        JSON.stringify({chips: n82.chips, rows: new Set(n82.chipTops).size, subH: n82.subH, chipH: n82.chipH}));
  check('表宽(v50.82): 换行只发生在合约之间(单个合约号不折断)',
        n82.chipWs === 'nowrap' && n82.chipDisp === 'inline-block' && n82.sep.indexOf('/') >= 0,
        JSON.stringify({ws: n82.chipWs, disp: n82.chipDisp, sep: n82.sep}));
  check('表宽(v50.82): 三个合约号都完整显示(没有被截断/丢字)',
        ['lc2701-P-114000', 'lc2701-P-100000', 'lc2701-P-92000'].every(c => n82.subTxt.indexOf(c) >= 0),
        n82.subTxt);
  // 清理(顺手把 showAll 还原, 不影响后面用例)
  await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const d = await (await fetch('/api/trades/detail?underlying=e2ewide82&mode=futures&batch=Bw82')).json();
    for (const op of (d.operations || [])) {
      await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: op.id})});
    }
    TradeUI.showAll = false; TradeUI.renderMain(); await w(200);
    return 'ok';
  })()`);

  // ===== v50.83: 风险额度新增 4% / 5% + 期权买方「每手权利金 = 报价 × 乘数」 =====
  const v83Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const out = {opts: {}};
    // ① 三个模式的风险额度下拉都要有 4% / 5%(后端白名单 + 三处 select 一处都不能漏)
    ['riskAmount', 'riskAmountO', 'riskAmountD'].forEach(id => {
      out.opts[id] = [...q(id).options].map(o => o.value);
    });
    // ② 期权买方: 棕榈油 p 报价 72 × 乘数 10 = 每手 720; 权益 12 万 × 3% = 3600 → 5 手
    //    (这正是用户报的那组数: 以前把 72 当每手价格 → 算出 50 手)
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(300);
    setMode('options'); await w(300);
    pickContract(CONTRACTS.find(x => x.code.toLowerCase() === 'p'), 'O'); await w(300);
    q('equity').value = '12'; q('equity').dispatchEvent(new Event('input', {bubbles:true}));
    q('entryO').value = '72'; q('entryO').dispatchEvent(new Event('input', {bubbles:true}));
    q('riskAmountO').value = '3';
    onInput(); await w(900);
    out.unit = q('unitO').textContent;
    out.hint = q('entryOHint').textContent;
    out.hintVisible = q('entryOHint').offsetParent !== null;
    out.prem = q('rPremiumO').textContent;
    out.lots = q('rLotsO').textContent;
    out.funds = q('rFundsO').textContent;
    out.budget = q('rBudgetO').textContent;
    out.entryCell = q('rEntryO').textContent;
    out.premSub = q('rPremSubO').textContent;
    // ③ 换到新增的 5%: 预算 6000 → 8 手
    q('riskAmountO').value = '5'; onInput(); await w(900);
    out.budget5 = q('rBudgetO').textContent;
    out.lots5 = q('rLotsO').textContent;
    // ④ 双买(后端)也要收 5%
    const d5 = await (await fetch('/api/calc/dual', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({equity: 120000, code:'fg', month:'2701', risk_percent: 5,
        call_strike:800, call_delta:0.8291, call_premium:1260,
        put_strike:800, put_delta:-0.1973, put_premium:150})})).json();
    out.dual5 = {ok: d5.ok, pct: d5.risk_percent, budget: d5.budget, qc: d5.call && d5.call.qty, qp: d5.put && d5.put.qty};
    // ⑤ 期货(后端)收 4%
    const f4 = await (await fetch('/api/calc/futures', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({equity: 1000000, code:'rb', direction:'long', entry: 3200, stop: 3100, target: 3400,
        risk_percent: 4})})).json();
    out.fut4 = {ok: f4.ok, pct: f4.risk_percent, budget: f4.budget, lots: f4.max_lots};
    return JSON.stringify(out);
  })()`);
  const v83 = JSON.parse(v83Run);
  const RISK_ALL = '0.5,1,1.5,2,3,4,5';
  check('风险额度(v50.83): 期货/期权买方/双买三个下拉都含 4% 与 5%',
        v83.opts.riskAmount.join(',') === RISK_ALL && v83.opts.riskAmountO.join(',') === RISK_ALL
        && v83.opts.riskAmountD.join(',') === RISK_ALL, JSON.stringify(v83.opts));
  check('期权买方(v50.83): 开仓价单位跟着品种(棕榈油 = 元/吨) + 提示写出算式并可见',
        /元\/吨/.test(v83.unit) && v83.hintVisible === true
        && /乘数 10/.test(v83.hint) && /72 × 10/.test(v83.hint) && /720/.test(v83.hint),
        JSON.stringify({unit: v83.unit, hint: v83.hint, vis: v83.hintVisible}));
  check('期权买方(v50.83): 报价 72 × 乘数 10 = 每手 ¥720(不再是 72)',
        /720/.test(v83.prem) && !/ ¥ 72$/.test(v83.prem.trim()), v83.prem);
  check('期权买方(v50.83): 权益 12 万 × 3% = 3600 → 5 手(以前是 50 手)', /3,600/.test(v83.budget)
        && v83.lots === '5' && /3,600/.test(v83.funds), JSON.stringify({b: v83.budget, lots: v83.lots, f: v83.funds}));
  check('期权买方(v50.83): 结果里另列「期权价格(报价)72 元/吨」并写明每手 = 期权价格 × 乘数',
        /72 元\/吨/.test(v83.entryCell) && /期权价格 × 乘数 10/.test(v83.premSub),
        v83.entryCell + ' | ' + v83.premSub);
  check('风险额度(v50.83): 期权买方选 5% → 预算 6000 → 8 手',
        /6,000/.test(v83.budget5) && v83.lots5 === '8', v83.budget5 + ' / ' + v83.lots5);
  check('风险额度(v50.83): /api/calc/dual 收 5%(预算 120000x5% = 6000)',
        v83.dual5.ok === true && v83.dual5.pct === 5 && Math.abs(v83.dual5.budget - 6000) < 0.01,
        JSON.stringify(v83.dual5));
  check('风险额度(v50.83): /api/calc/futures 收 4%(预算 1000000x4% = 40000)',
        v83.fut4.ok === true && v83.fut4.pct === 4 && Math.abs(v83.fut4.budget - 40000) < 0.01,
        JSON.stringify(v83.fut4));

  // ===== v50.84: 期权买方 = 单腿(call/put) + 合约年月·行权价·delta + 止盈目标价 + 加入记录 =====
  const v84Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const q = id => document.getElementById(id);
    const out = {};
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click(); await w(300);
    setMode('options'); await w(300);
    pickContract(CONTRACTS.find(x => x.code.toLowerCase() === 'cu'), 'O'); await w(400);
    // ① 合约年月挪到「风险额度」那一行(不再在腿卡里)
    const row = q('optMonth').closest('.dual-row');
    out.layout = {sameRow: !!row && row.contains(q('riskAmountO')),
                  inLeg: !!q('optMonth').closest('#optLegCard')};
    // ② 做多 → CALL(红框) + 合约拼成 C
    q('optMonth').value = '2612'; q('optMonth').dispatchEvent(new Event('input',{bubbles:true}));
    q('optStrike').value = '80000'; q('optStrike').dispatchEvent(new Event('input',{bubbles:true}));
    q('optDelta').value = '0.32'; q('optDelta').dispatchEvent(new Event('input',{bubbles:true}));
    q('entryO').value = '55'; q('entryO').dispatchEvent(new Event('input',{bubbles:true}));
    q('equity').value = '12'; q('equity').dispatchEvent(new Event('input',{bubbles:true}));
    q('riskAmountO').value = '3'; q('riskAmountO').dispatchEvent(new Event('change',{bubbles:true}));
    onInput(); await w(900);
    out.long = {card: q('optLegCard').className, badge: q('optLegBadge').textContent,
                name: q('optLegName').textContent, border: getComputedStyle(q('optLegCard')).borderTopColor,
                premAuto: q('optPremium').value, hint: q('entryOHint').textContent,
                code: q('rCodeO').textContent, lots: q('rLotsO').textContent,
                delta: q('rDeltaO').textContent, funds: q('rFundsO').textContent,
                heads: [...document.querySelectorAll('#rTargetsO thead th')].map(th => th.textContent.trim()),
                rows: [...document.querySelectorAll('#rTargetsO tbody tr')].map(tr =>
                  [...tr.children].map(td => td.textContent.trim()))};
    // ③ 做空 → PUT(青框) + 合约变 P + delta 自动带负号
    document.querySelector('#dirSegO button[data-dir="short"]').click(); await w(900);
    out.short = {card: q('optLegCard').className, badge: q('optLegBadge').textContent,
                 name: q('optLegName').textContent, border: getComputedStyle(q('optLegCard')).borderTopColor,
                 deltaVal: q('optDelta').value, deltaCell: q('rDeltaO').textContent,
                 code: q('rCodeO').textContent,
                 hint: q('entryOHint').textContent,
                 firstTgt: (document.querySelector('#rTargetsO tbody tr td:nth-child(2)') || {}).textContent || ''};
    // v50.91: 浮盈列必须是「红涨」→ 与 --bad 的实测颜色一致(别只断言 class)
    const pc = document.querySelector('#rTargetsO tbody tr td.pos, #rTargetsO tbody tr td.neg');
    const probe = document.createElement('span');
    probe.style.color = 'var(--bad)'; document.body.appendChild(probe);
    out.red = getComputedStyle(probe).color;
    probe.style.color = 'var(--good)';
    out.green = getComputedStyle(probe).color;
    out.profitColor = pc ? getComputedStyle(pc).color : '';
    probe.remove();
    // ④ 加入记录 → 交易记录 · 期权买方 多一条(合约/方向/价格/手数/权利金都要对上)
    const g0 = await (await fetch('/api/trades/groups?mode=options')).json();
    const n0 = (g0.groups || []).length;
    q('btnAddOpt').click(); await w(1800);
    const g1 = await (await fetch('/api/trades/groups?mode=options')).json();
    // ⚠ v50.85: 标的带合约年月(cu2612), 不再只是 cu
    const mine = (g1.groups || []).find(x => String(x.underlying).indexOf('cu') === 0 && x.batch);
    let op0 = null;
    const extra = {};
    if (mine){
      const det = await (await fetch('/api/trades/detail?mode=options&underlying='
        + encodeURIComponent(mine.underlying) + '&batch='
        + encodeURIComponent(mine.batch))).json();
      op0 = (det.operations || [])[0] || null;
      extra.underlying = mine.underlying;
      extra.tpTargets = (det.tp_targets || []).length;
      extra.tpFirst = (det.tp_targets && det.tp_targets[0]) ? det.tp_targets[0].targets.map(t => t.price_unit) : null;
      extra.tpBox = (document.getElementById('tdTpTargets') || {}).textContent || '';
    }
    out.added = Object.assign({n0, n1: (g1.groups || []).length, found: !!mine, tab: '', op: op0}, extra);
    const act = document.querySelector('#mainTabs .maintab.active');
    out.added.tab = act ? act.dataset.tab : '';
    // 清理: 删掉这条记录, 免得影响后面的用例
    if (mine){
      const det = await (await fetch('/api/trades/detail?mode=options&underlying='
        + encodeURIComponent(mine.underlying) + '&batch='
        + encodeURIComponent(mine.batch))).json();
      for (const op of (det.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
      if (typeof TradeUI !== 'undefined') TradeUI.closeDetail();
    }
    const g2 = await (await fetch('/api/trades/groups?mode=options')).json();
    out.cleaned = (g2.groups || []).length === n0;
    return JSON.stringify(out);
  })()`);
  const v84 = JSON.parse(v84Run);
  check('期权买方(v50.84): 合约年月与「风险额度」同一行, 已从腿卡里挪出',
        v84.layout.sameRow === true && v84.layout.inLeg === false, JSON.stringify(v84.layout));
  check('期权买方(v50.84): 做多 → CALL 红框(腿卡/徽章/文案都跟着走)',
        /\bcall\b/.test(v84.long.card) && !/put/.test(v84.long.card)
        && v84.long.badge === 'CALL' && v84.long.name === '看涨腿', JSON.stringify(v84.long.card + '|' + v84.long.badge));
  check('期权买方(v50.84): 每手权利金自动 = 期权价格 55 × 乘数 5 = 275',
        v84.long.premAuto === '275', v84.long.premAuto);
  check('期权买方(v50.84): 做多合约代码 = cu2612C80000',
        v84.long.code === 'cu2612C80000', v84.long.code);
  check('期权买方(v50.84): 手数 3600÷275 = 13 / 占用 ¥3,575 / delta 4 位小数',
        v84.long.lots === '13' && /3,575/.test(v84.long.funds) && v84.long.delta === '0.3200',
        JSON.stringify({lots: v84.long.lots, funds: v84.long.funds, delta: v84.long.delta}));
  check('止盈表(v50.91): 计算器的「浮盈」列是红色（红涨绿跌，不是 good 的绿）',
        v84.profitColor && v84.profitColor === v84.red && v84.profitColor !== v84.green,
        JSON.stringify({profit: v84.profitColor, red: v84.red, green: v84.green}));
  check('期权买方(v50.90): 止盈目标价表 4 档 = 2×/3×/4×/6×(报价 110/165/220/330)',
        v84.long.rows.length === 4 && v84.long.rows[0][0] === '2×' && v84.long.rows[1][0] === '3×'
        && v84.long.rows[2][0] === '4×' && v84.long.rows[3][0] === '6×'
        && /110/.test(v84.long.rows[0][1]) && /165/.test(v84.long.rows[1][1])
        && /220/.test(v84.long.rows[2][1]) && /330/.test(v84.long.rows[3][1]),
        JSON.stringify(v84.long.rows));
  check('期权买方(v50.84): 止盈表列出 每手权利金 / 合计价值 / 浮盈(2× → 550 / 7,150 / +3,575)',
        /550/.test(v84.long.rows[0][2]) && /7,150/.test(v84.long.rows[0][3]) && /3,575/.test(v84.long.rows[0][4]),
        JSON.stringify(v84.long.rows[0]));
  check('期权买方(v50.84): 做空 → 腿卡切到 PUT(青框) 且与 CALL 的边框色不同',
        /\bput\b/.test(v84.short.card) && !/\bcall\b/.test(v84.short.card)
        && v84.short.badge === 'PUT' && v84.short.name === '看跌腿'
        && v84.short.border !== v84.long.border,
        JSON.stringify({card: v84.short.card, badge: v84.short.badge,
                        short: v84.short.border, long: v84.long.border}));
  check('期权买方(v50.84): 做空后 delta 自动带负号(-0.3200)',
        v84.short.deltaVal === '-0.32' && v84.short.deltaCell === '-0.3200',
        v84.short.deltaVal + ' / ' + v84.short.deltaCell);
  check('期权买方(v50.84): 做空合约代码变 cu2612P80000',
        v84.short.code === 'cu2612P80000', v84.short.code);
  check('期权买方(v50.84): 加入记录 → 期权买方多一条(合约/看跌/价格 55/13 手/权利金 3575)',
        v84.added.found === true && v84.added.n1 === v84.added.n0 + 1 && v84.added.op
        && v84.added.op.contract === 'cu2612P80000' && v84.added.op.call_put === 'P'
        && Number(v84.added.op.open_price) === 55 && Number(v84.added.op.qty) === 13
        && Math.abs(Number(v84.added.op.premium) - 3575) < 0.01,
        JSON.stringify(v84.added.op));
  check('期权买方(v50.84): 加入记录后自动跳到「交易记录 · 期权买方」',
        v84.added.tab === 'trades', v84.added.tab);
  check('期权买方(v50.84): 用例清理干净(记录数回到原值)', v84.cleaned === true, String(v84.cleaned));
  check('期权买方(v50.85): 加入记录的「标的」带上合约年月(cu2612, 不再只是 cu)',
        v84.added.underlying === 'cu2612', String(v84.added.underlying));
  check('期权买方(v50.90): 详情接口返回 tp_targets(报价目标 110/165/220/330)',
        v84.added.tpTargets === 1 && (v84.added.tpFirst || []).join(',') === '110,165,220,330',
        JSON.stringify(v84.added.tpFirst));
  check('期权买方(v50.85): 详情页真的画出「期权止盈目标价位」板块',
        /期权止盈目标价位/.test(v84.added.tpBox) && /2×/.test(v84.added.tpBox),
        (v84.added.tpBox || '').slice(0, 120));

  // ===== v50.85: 详情页止盈目标价跟「当前持仓」走(老合约平完 → 按新开合约的价格重算) =====
  const v85Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const U = 'p2701x85';
    const det = () => fetch('/api/trades/detail?mode=options&underlying=' + U + '&batch=B85a')
      .then(r=>r.json());
    const out = {};
    // ① 手动新建一条(报价 87.5, 乘数 10 → 每手 875, 3 手)
    await post({mode:'options', underlying:U, contract:'p2701P9000', batch:'B85a', op_type:'open',
      direction:'buy', call_put:'P', open_date:'2026-10-01', open_price:87.5, qty:3,
      premium:2625, open_delta:-0.22});
    const d1 = await det();
    out.one = (d1.tp_targets || []).map(g => [g.contract, g.qty, g.price, g.targets.map(t => t.price_unit)]);
    // ② 全部平掉 + 之后又开了一个新月份合约 → 目标价必须按**新合约**算
    await post({mode:'options', underlying:U, contract:'p2701P9000', batch:'B85a', op_type:'close',
      direction:'sell', close_date:'2026-10-10', close_qty:3, close_price:120, pnl:975});
    const dMid = await det();
    out.closed = (dMid.tp_targets || []).map(g => g.contract);
    await post({mode:'options', underlying:U, contract:'p2703P9500', batch:'B85a', op_type:'open',
      direction:'buy', call_put:'P', open_date:'2026-10-12', open_price:120, qty:2,
      premium:2400, open_delta:-0.30});
    const d2 = await det();
    out.two = (d2.tp_targets || []).map(g => [g.contract, g.qty, g.price, g.targets.map(t => t.price_unit)]);
    // ③ 在界面上打开这条记录 → 板块画出来且看得见
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(600);
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(500);
    await TradeUI.loadDetail(U, 'B85a'); await w(800);
    const box = document.getElementById('tdTpTargets');
    // v50.91: 详情页浮盈列也得是红
    const dcell = box.querySelector('tbody tr td.pos, tbody tr td.neg');
    const pb2 = document.createElement('span');
    pb2.style.color = 'var(--bad)'; document.body.appendChild(pb2);
    out.domRed = getComputedStyle(pb2).color;
    pb2.style.color = 'var(--good)'; out.domGreen = getComputedStyle(pb2).color;
    out.domProfitColor = dcell ? getComputedStyle(dcell).color : '';
    pb2.remove();
    out.dom = {visible: box.offsetParent !== null, display: getComputedStyle(box).display,
               txt: box.textContent.replace(/\\s+/g, ' ').trim().slice(0, 240),
               sep: box.querySelectorAll('tbody tr.tp-sep').length,
               rows: box.querySelectorAll('tbody tr').length};
    // ④ 切到期货 / 双买时这块不显示
    TradeUI.setMode('futures'); await w(500);
    out.inFutures = getComputedStyle(document.getElementById('tdTpTargets')).display;
    TradeUI.setMode('options'); await w(400);
    // 清理
    const dd = await det();
    for (const op of (dd.operations || [])){
      await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: op.id})});
    }
    TradeUI.closeDetail();
    return JSON.stringify(out);
  })()`);
  const v85 = JSON.parse(v85Run);
  check('期权买方(v50.90): 手动新建的记录详情按当前持仓算目标价(87.5 → 175/262.5/350/525)',
        v85.one.length === 1 && v85.one[0][0] === 'p2701P9000' && v85.one[0][1] === 3
        && v85.one[0][3].join(',') === '175,262.5,350,525', JSON.stringify(v85.one));
  check('期权买方(v50.85): 全部平掉(还没开新合约)时退回开仓记录，仍有目标价',
        v85.closed.join(',') === 'p2701P9000', JSON.stringify(v85.closed));
  check('期权买方(v50.90): 老合约平完 + 新开合约 → 目标价改成按新合约(120 → 240/360/480/720)',
        v85.two.length === 1 && v85.two[0][0] === 'p2703P9500' && v85.two[0][1] === 2
        && v85.two[0][3].join(',') === '240,360,480,720', JSON.stringify(v85.two));
  check('期权买方(v50.85): 详情页板块可见且写出合约/当前价/倍数(不是仅接口有数据)',
        v85.dom.visible === true && v85.dom.sep === 1
        && /p2703P9500/.test(v85.dom.txt) && /120/.test(v85.dom.txt) && /2×/.test(v85.dom.txt),
        JSON.stringify(v85.dom));
  check('止盈表(v50.91): 交易记录详情页的「浮盈」列同样是红色',
        v85.domProfitColor && v85.domProfitColor === v85.domRed
        && v85.domProfitColor !== v85.domGreen,
        JSON.stringify({profit: v85.domProfitColor, red: v85.domRed, green: v85.domGreen}));
  check('期权买方(v50.85): 期货模式下这块不显示(display:none)',
        v85.inFutures === 'none', v85.inFutures);

  // ===== v50.86: 期权买方主表「开仓标的」后面带方向(买 call = 多 / 买 put = 空) =====
  const v86Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const out = {};
    // 老写法(标的里已经写了 多/空) + 新写法(只有品种+年月)各造一条
    await post({mode:'options', underlying:'fu2612多', contract:'fu2612C5200', batch:'B86a',
      op_type:'open', direction:'buy', call_put:'C', open_date:'2026-10-08', open_price:88, qty:3, premium:2640});
    await post({mode:'options', underlying:'ao2701空', contract:'ao2701P2500', batch:'B86b',
      op_type:'open', direction:'buy', call_put:'P', open_date:'2026-10-09', open_price:60, qty:2, premium:1200});
    await post({mode:'options', underlying:'cu2701', contract:'cu2701C90000', batch:'B86c',
      op_type:'open', direction:'buy', call_put:'C', open_date:'2026-10-10', open_price:55, qty:1, premium:550});
    // 写法与合约**矛盾**的一条: 标的写「空」但合约是 call → 不许静默改写, 要提示核对
    await post({mode:'options', underlying:'si2611空', contract:'si2611C9500', batch:'B86d',
      op_type:'open', direction:'buy', call_put:'C', open_date:'2026-10-11', open_price:70, qty:1, premium:700});
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(900);
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(700);
    TradeUI.onlyOpen = false; TradeUI.mainQuery = ''; TradeUI.range = 'all';
    TradeUI.showAll = true; TradeUI.renderMain(); await w(500);
    const cellOf = u => {
      const tr = [...document.querySelectorAll('#tradesTable tbody tr.clickable')]
        .find(x => x.dataset.u === u);
      if (!tr) return null;
      const td = tr.querySelector('td.col-name');
      const b = td.querySelector('.u-dir');
      return {name: (td.querySelector('b') || {}).textContent || '',
              badge: b ? b.textContent : '', cls: b ? b.className : '',
              color: b ? getComputedStyle(b).color : '',
              tip: td.getAttribute('title') || '',
              sub: (td.querySelector('.u-sub') || {}).textContent || ''};
    };
    out.callOld = cellOf('fu2612多');     // 老写法 call → 名字剥掉「多」+ 徽章「多」
    out.putOld = cellOf('ao2701空');      // 老写法 put → 名字剥掉「空」+ 徽章「空」
    out.callNew = cellOf('cu2701');       // 新写法 → 名字原样 + 徽章「多」
    out.mismatch = cellOf('si2611空');    // 矛盾 → 名字保持原样 + 徽章按合约(多) + 悬停提示
    // 期货模式不该冒出这个徽章
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(700);
    TradeUI.setMode('futures'); await TradeUI.refresh(); await w(600);
    out.futBadges = document.querySelectorAll('#tradesTable td.col-name .u-dir').length;
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(400);
    // 清理
    const g = await (await fetch('/api/trades/groups?mode=options')).json();
    for (const x of (g.groups || [])){
      if (['fu2612多', 'ao2701空', 'cu2701', 'si2611空'].indexOf(x.underlying) < 0) continue;
      const d = await (await fetch('/api/trades/detail?mode=options&underlying='
        + encodeURIComponent(x.underlying) + '&batch=' + encodeURIComponent(x.batch || ''))).json();
      for (const op of (d.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
    }
    TradeUI.showAll = false;
    out.cleaned = ((await (await fetch('/api/trades/groups?mode=options')).json()).groups || [])
      .filter(x => ['fu2612多', 'ao2701空', 'cu2701', 'si2611空'].indexOf(x.underlying) >= 0).length === 0;
    return JSON.stringify(out);
  })()`);
  const v86 = JSON.parse(v86Run);
  check('期权买方(v50.86): 买 call 的记录 → 标的后面的徽章是「多」',
        v86.callNew && v86.callNew.badge === '多' && /long/.test(v86.callNew.cls)
        && v86.callNew.name === 'cu2701', JSON.stringify(v86.callNew));
  check('期权买方(v50.86): 买 put 的记录 → 徽章「空」(青色)',
        v86.putOld && v86.putOld.badge === '空' && /short/.test(v86.putOld.cls)
        && v86.putOld.badge !== v86.callNew.badge
        && v86.putOld.color !== v86.callNew.color,
        JSON.stringify(v86.putOld));
  check('期权买方(v50.86): 老记录标的里写着的「多/空」不再重复显示(剥掉后只剩徽章)',
        v86.callOld && v86.callOld.name === 'fu2612' && v86.callOld.badge === '多'
        && v86.putOld.name === 'ao2701',
        JSON.stringify({call: v86.callOld, put: v86.putOld}));
  check('期权买方(v50.86): 合约小字没被方向徽章挤掉',
        /fu2612C5200/.test(v86.callOld.sub), v86.callOld.sub);
  check('期权买方(v50.86): 写法与合约矛盾的记录不被静默改写 —— 名字保持「si2611空」, 徽章按合约给「多」, 悬停提示核对',
        v86.mismatch && v86.mismatch.name === 'si2611空' && v86.mismatch.badge === '多'
        && /请核对/.test(v86.mismatch.tip) && /si2611C9500/.test(v86.mismatch.tip),
        JSON.stringify(v86.mismatch));
  check('期权买方(v50.86): 写法一致的记录没有这个提示(title 为空)',
        v86.callOld && v86.callOld.tip === '' && v86.callNew.tip === '',
        JSON.stringify([v86.callOld && v86.callOld.tip, v86.callNew && v86.callNew.tip]));
  check('期权买方(v50.86): 期货模式不出现该徽章', v86.futBadges === 0, String(v86.futBadges));
  check('期权买方(v50.86): 用例清理干净', v86.cleaned === true, String(v86.cleaned));

  // ===== v50.87: 夏普卡补「年化」口径(≥5 笔且跨度 ≥30 天) =====
  const v87Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const day = n => new Date(Date.UTC(2026, 0, 5 + n)).toISOString().slice(0, 10);
    const cases = [['e2esh01', 1000, 0], ['e2esh02', 800, 12], ['e2esh03', 1200, 24],
                   ['e2esh04', -400, 36], ['e2esh05', 900, 48], ['e2esh06', 1500, 60]];
    for (const [u, pnl, off] of cases){
      await post({underlying:u, contract:u+'2611', op_type:'open', direction:'buy',
                  open_date:day(off), open_price:100, qty:1, premium:0, mode:'futures'});
      await post({underlying:u, contract:u+'2611', op_type:'close', direction:'sell',
                  close_date:day(off+5), close_qty:1, close_price:200, pnl:pnl, mode:'futures'});
    }
    document.querySelector('#mainTabs .maintab[data-tab="tradesFut"]').click(); await w(1200);
    TradeUI.setMode('futures'); await TradeUI.refresh(); await w(800);
    TradeUI.onlyOpen = false; TradeUI.mainQuery = ''; TradeUI.range = 'all';
    TradeUI.renderMain(); await w(500);
    const c5 = document.querySelectorAll('#tradeStats .stat-card')[4];
    const out = {val: (c5.querySelector('.sv') || {}).textContent || '',
                 sub: (c5.querySelector('.ss') || {}).textContent || '',
                 tip: (c5.querySelector('.sv') || {}).getAttribute('title') || ''};
    // 独立重算: 用页面自己的 groups 数据(不依赖 calcStats 的实现)算一遍每笔/年化
    const closed = (TradeUI.groups || []).filter(g => (g.total_pnl || 0) !== 0);
    const xs = closed.map(g => g.total_pnl);
    const n = xs.length, m = xs.reduce((a, b) => a + b, 0) / n;
    const sd = Math.sqrt(xs.reduce((a, b) => a + (b - m) * (b - m), 0) / (n - 1));
    const per = m / sd;
    const od = closed.map(g => g.open_date || '').filter(Boolean).sort();
    const cd = closed.map(g => g.last_close_date || '').filter(Boolean).sort();
    const days = (Date.parse(cd[cd.length - 1]) - Date.parse(od[0])) / 86400000;
    out.exp = {per: per.toFixed(2), ann: (per * Math.sqrt(n / (days / 365))).toFixed(2),
               n: n, days: Math.round(days)};
    // 清理
    const g = await (await fetch('/api/trades/groups?mode=futures&fee=ex')).json();
    for (const x of (g.groups || [])){
      if (String(x.underlying).indexOf('e2esh') !== 0) continue;
      const d = await (await fetch('/api/trades/detail?mode=futures&underlying=' + x.underlying
        + '&batch=' + encodeURIComponent(x.batch || ''))).json();
      for (const op of (d.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
    }
    out.cleaned = ((await (await fetch('/api/trades/groups?mode=futures&fee=ex')).json()).groups || [])
      .filter(x => String(x.underlying).indexOf('e2esh') === 0).length === 0;
    return JSON.stringify(out);
  })()`);
  const v87 = JSON.parse(v87Run);
  check('夏普年化(v50.87): 每笔口径与页面对得上(独立重算 ' + v87.exp.per + ')',
        v87.val === v87.exp.per, v87.val + ' vs ' + v87.exp.per);
  check('夏普年化(v50.87): ≥5 笔且跨度 ≥30 天时, 小字报出「年化」且数值 = 每笔 × √(每年笔数)',
        /年化/.test(v87.sub) && v87.sub.indexOf(v87.exp.ann) >= 0,
        JSON.stringify({sub: v87.sub, exp: v87.exp}));
  check('夏普年化(v50.87): tooltip 写清两个口径的算法与跨度',
        /年化口径/.test(v87.tip) && /跨度/.test(v87.tip) && /每年/.test(v87.tip),
        v87.tip.slice(0, 120));
  check('夏普年化(v50.87): 用例清理干净', v87.cleaned === true, String(v87.cleaned));

  // ===== v50.88: 交易记录工具栏「当前权益」+ 风险比例 =====
  const v88Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const out = {};
    // 干净的起点: 三个模式的权益都清掉(复用同一 profile 重跑时不会带上一轮的值)
    ['futures', 'options', 'dual'].forEach(m => localStorage.removeItem('oc-cur-equity-' + m));
    localStorage.removeItem('oc-cur-equity');
    await post({mode:'options', underlying:'e2eeq88', contract:'e2eeq88C9000', batch:'B88',
      op_type:'open', direction:'buy', call_put:'C', open_date:'2026-10-10',
      open_price:60, qty:2, premium:1200});
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(800);
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(700);
    const eqBar = document.getElementById('curEqBar');
    const eq = document.getElementById('curEquity');
    const bar = document.getElementById('openRisk');
    // ① 位置: 紧跟在「手续费」口径条右边
    out.afterFee = !!(eqBar && eqBar.previousElementSibling && eqBar.previousElementSibling.id === 'feeSeg');
    out.inToolbar = !!(eqBar && eqBar.closest('.trades-toolbar'));
    // ② 没填权益 → 风险比例显示 —
    eq.value = ''; eq.dispatchEvent(new Event('input', {bubbles:true})); await w(400);
    out.emptyTxt = bar.textContent;
    out.emptyTip = bar.getAttribute('title') || '';
    // ③ 填 12 万 → 风险比例 = 最大亏损 ÷ 120000
    const rk = await (await fetch('/api/trades/open-risk?mode=options&fee=ex')).json();
    eq.value = '12'; eq.dispatchEvent(new Event('input', {bubbles:true})); await w(500);
    out.rk = {maxLoss: rk.max_loss, partial: rk.max_loss_is_partial};
    out.ratioText = (bar.textContent.match(/风险比例\\s*(≥\\s*)?[\\d.]+%/) || [])[0] || '';
    out.ratioTip = bar.getAttribute('title') || '';
    out.exp = (rk.max_loss / 120000 * 100).toFixed(2) + '%';
    // ④ 分账户: 期权买方 12 万 / 期货 20 万 / 双买空着, 三个互不影响
    out.ls = localStorage.getItem('oc-cur-equity-options');
    TradeUI.setMode('futures'); await w(500);
    out.futEqBefore = eq.value;                     // 期货还没填过 → 空
    eq.value = '20'; eq.dispatchEvent(new Event('input', {bubbles:true})); await w(300);
    TradeUI.setMode('options'); await w(500);
    out.backOpt = eq.value;                         // 回期权买方 → 还是 12
    TradeUI.setMode('dual'); await w(500);
    out.dualEq = eq.value;                          // 双买独立 → 空
    TradeUI.setMode('futures'); await w(500);
    out.futEq = eq.value;                           // 期货 → 20
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(600);
    // ⑤ 进 .opcalc 备份(按模式存) + 恢复(不计入方案组数; 老格式字符串仍兼容)
    out.snapEq = plansSnapshot().curEquity;
    const nBack = plansRestore({futures:[], options:[], dual:[], curEquity:'88'});
    out.restoreCnt = nBack;
    out.restoredEq = eq.value;                      // 老格式 → 三个模式都写 88
    const nBack2 = plansRestore({futures:[], options:[], dual:[],
      curEquity:{futures:'30', options:'12', dual:'7'}});
    out.restoreCnt2 = nBack2;
    out.restoredEq2 = eq.value;                     // 新格式 → 只改本模式(期权买方 12)
    eq.value = '12'; eq.dispatchEvent(new Event('input', {bubbles:true}));
    // ⑥ 期货模式: 一笔有止损 + 一笔没设止损 → 比例前面要带「≥」(只是下限)
    await post({mode:'futures', underlying:'e2efq88a', contract:'e2efq88a2611', batch:'B88f',
      op_type:'open', direction:'buy', open_date:'2026-10-10', open_price:3200, qty:2,
      premium:0, init_stop:3100});
    await post({mode:'futures', underlying:'e2efq88b', contract:'e2efq88b2611', batch:'B88g',
      op_type:'open', direction:'buy', open_date:'2026-10-10', open_price:3400, qty:1, premium:0});
    TradeUI.setMode('futures'); await TradeUI.refresh(); await w(800);
    const fbar = document.getElementById('openRisk');
    out.futTxt = fbar.textContent;
    out.futTip = fbar.getAttribute('title') || '';
    // 清理
    const gf = await (await fetch('/api/trades/groups?mode=futures&fee=ex')).json();
    for (const x of (gf.groups || [])){
      if (String(x.underlying).indexOf('e2efq88') !== 0) continue;
      const df = await (await fetch('/api/trades/detail?mode=futures&underlying=' + x.underlying
        + '&batch=' + encodeURIComponent(x.batch || ''))).json();
      for (const op of (df.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
    }
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(300);
    // 清理(期权)
    const g = await (await fetch('/api/trades/groups?mode=options&fee=ex')).json();
    for (const x of (g.groups || [])){
      if (x.underlying !== 'e2eeq88') continue;
      const d = await (await fetch('/api/trades/detail?mode=options&underlying=e2eeq88&batch='
        + encodeURIComponent(x.batch || ''))).json();
      for (const op of (d.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
    }
    out.cleaned = ((await (await fetch('/api/trades/groups?mode=options&fee=ex')).json()).groups || [])
      .filter(x => x.underlying === 'e2eeq88').length === 0;
    return JSON.stringify(out);
  })()`);
  const v88 = JSON.parse(v88Run);
  check('当前权益(v50.88): 输入框在「手续费」口径条的右边(同一工具栏)',
        v88.afterFee === true && v88.inToolbar === true, JSON.stringify({after: v88.afterFee}));
  check('风险比例(v50.88): 没填权益时显示「—」并在悬停里提示先填',
        /风险比例\s*—/.test(v88.emptyTxt) && /先在工具栏「当前权益」填/.test(v88.emptyTip),
        v88.emptyTxt.slice(0, 80));
  check('风险比例(v50.88): 填 12 万 → 最大亏损 ÷ 120000（页面 = 独立算出的 ' + v88.exp + '）',
        v88.ratioText.indexOf(v88.exp) >= 0, v88.ratioText + ' vs ' + v88.exp);
  check('风险比例(v50.88): 悬停里给出算式（最大亏损 ÷ 当前权益 = x%）',
        /风险比例 = 最大亏损/.test(v88.ratioTip) && /÷ 当前权益/.test(v88.ratioTip),
        v88.ratioTip.slice(-90));
  check('当前权益(v50.89): 三个模式分账户 —— 期权买方 12 / 期货 20 互不影响, 双买默认空',
        v88.ls === '12' && v88.futEqBefore === '' && v88.backOpt === '12'
        && v88.dualEq === '' && v88.futEq === '20',
        JSON.stringify({ls: v88.ls, futBefore: v88.futEqBefore, backOpt: v88.backOpt,
                        dual: v88.dualEq, fut: v88.futEq}));
  check('当前权益(v50.89): 备份按模式存(futures/options/dual 三个键)',
        v88.snapEq && v88.snapEq.options === '12' && v88.snapEq.futures === '20'
        && v88.snapEq.dual === '',
        JSON.stringify(v88.snapEq));
  check('当前权益(v50.89): 恢复新格式(按模式) → 只改本模式; 老格式(字符串) → 三个都写',
        v88.restoreCnt === 0 && v88.restoredEq === '88'
        && v88.restoreCnt2 === 0 && v88.restoredEq2 === '12',
        JSON.stringify({n1: v88.restoreCnt, back1: v88.restoredEq,
                        n2: v88.restoreCnt2, back2: v88.restoredEq2}));
  check('风险比例(v50.88): 期货模式也有这一项, 且有手数没设止损时前面带「≥」（下限）',
        /风险比例\s*≥\s*[\d.]+%/.test(v88.futTxt) && /下限/.test(v88.futTip),
        v88.futTxt.slice(-60) + ' | ' + (v88.futTip || '').slice(-40));
  check('当前权益(v50.88): 用例清理干净', v88.cleaned === true, String(v88.cleaned));

  // ===== v50.92: 交易记录最下方「品种统计」(按开仓标的的品种归并, 期权不按合约) =====
  const v92Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const post = p => fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(p)}).then(r=>r.json());
    const out = {};
    // 同一品种两个月份/合约 → 必须合成一行; 另一品种; 还有一个只开仓没平的
    const mk = async (u, c, batch, pnl, closed) => {
      await post({mode:'options', underlying:u, contract:c, batch:batch, op_type:'open',
        direction:'buy', call_put:'P', open_date:'2026-10-01', open_price:80, qty:2, premium:1600});
      if (closed){
        await post({mode:'options', underlying:u, contract:c, batch:batch, op_type:'close',
          direction:'sell', close_date:'2026-10-05', close_qty:2, close_price:130, pnl:pnl});
      }
    };
    await mk('zzp2701', 'zzp2701P9000', 'B92a', 1000, true);
    await mk('zzp2703', 'zzp2703P9500', 'B92b', -400, true);
    await mk('zzf2612多', 'zzf2612C5200', 'B92c', 600, true);
    await mk('zzg2612多', 'zzg2612C5300', 'B92e', 2400, true);   // 最大 → 柱宽 100%
    await mk('zzc2701', 'zzc2701C9000', 'B92d', 0, false);
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(900);
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(900);
    TradeUI.onlyOpen = false; TradeUI.mainQuery = ''; TradeUI.range = 'all'; TradeUI.renderMain(); await w(500);
    const box = document.getElementById('prodStats');
    const rowOf = key => {
      const tr = [...box.querySelectorAll('tbody tr')].find(x => {
        const c = x.querySelector('.p-code'); return c && c.textContent.trim() === key;
      });
      return tr ? [...tr.children].map(td => td.textContent.trim()) : null;
    };
    out.belowPool = document.getElementById('poolCard').nextElementSibling.id === 'prodCard';
    out.heads = [...box.querySelectorAll('thead th')].map(x => x.textContent.trim());
    out.zzpCount = [...box.querySelectorAll('tbody .p-code')].filter(x => x.textContent.trim() === 'zzp').length;
    out.zzp = rowOf('zzp');
    out.zzf = rowOf('zzf');
    out.zzc = rowOf('zzc');
    out.tip = (box.querySelector('.tip') || {}).textContent || '';
    // v50.93: 排序 + 背景柱状图(宽度/颜色)
    const allTr = [...box.querySelectorAll('tbody tr')];
    out.keys = allTr.map(tr => (tr.querySelector('.p-code') || {}).textContent || '');
    out.pnls = allTr.map(tr => (tr.children[3] || {}).textContent || '');
    out.bars = allTr.map(tr => ({
      cls: tr.className,
      w: (tr.getAttribute('style') || '').replace(/\s/g, ''),
      bg: getComputedStyle(tr).backgroundImage.slice(0, 220)}));
    // 文字没被柱子盖住: 单元格还是 6 个(柱子不是额外图层), 且名称格子颜色仍是正常正文色
    out.tdCount = allTr[0] ? allTr[0].children.length : 0;
    const pn = allTr[0] ? allTr[0].querySelector('.p-name') : null;
    const ps = document.createElement('span'); ps.style.color = 'var(--text)';
    document.body.appendChild(ps); out.textColor = getComputedStyle(ps).color; ps.remove();
    out.nameColor = pn ? getComputedStyle(pn).color : '';
    // 盈亏必须是红(红涨绿跌)
    const trp = [...box.querySelectorAll('tbody tr')].find(x => {
      const c = x.querySelector('.p-code'); return c && c.textContent.trim() === 'zzp';
    });
    const pcell = trp ? trp.children[3] : null;
    const pr = document.createElement('span'); pr.style.color = 'var(--bad)';
    document.body.appendChild(pr); out.red = getComputedStyle(pr).color; pr.remove();
    out.pnlColor = pcell ? getComputedStyle(pcell).color : '';
    out.pnlCls = pcell ? pcell.className : '';
    // 期货模式: 不该出现这些期权品种
    TradeUI.setMode('futures'); await TradeUI.refresh(); await w(700);
    const fbox = document.getElementById('prodStats');
    out.futHasZzp = !!(fbox.querySelector('.p-code') && [...fbox.querySelectorAll('.p-code')]
      .some(x => x.textContent.trim() === 'zzp'));
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(400);
    // 清理
    const g = await (await fetch('/api/trades/groups?mode=options&fee=ex')).json();
    for (const x of (g.groups || [])){
      if (String(x.underlying).indexOf('zz') !== 0) continue;
      const d = await (await fetch('/api/trades/detail?mode=options&underlying='
        + encodeURIComponent(x.underlying) + '&batch=' + encodeURIComponent(x.batch || ''))).json();
      for (const op of (d.operations || [])){
        await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: op.id})});
      }
    }
    out.cleaned = ((await (await fetch('/api/trades/groups?mode=options&fee=ex')).json()).groups || [])
      .filter(x => String(x.underlying).indexOf('zz') === 0).length === 0;
    return JSON.stringify(out);
  })()`);
  const v92 = JSON.parse(v92Run);
  check('品种统计(v50.92): 位置在监控池下面 · 表头 = 品种/记录数/已平仓/盈亏/胜率/盈亏比',
        v92.belowPool === true
        && v92.heads.join(',') === '品种,记录数,已平仓,盈亏,胜率,盈亏比',
        JSON.stringify({below: v92.belowPool, heads: v92.heads}));
  check('品种统计(v50.92): 期权按开仓标的归并 —— zzp2701 + zzp2703 合成一行(不是按合约)',
        v92.zzpCount === 1 && v92.zzp && v92.zzp[1] === '2', JSON.stringify(v92.zzp));
  check('品种统计(v50.92): 盈亏/胜率/盈亏比 = +600 / 50.0% / 2.50（+1000 与 −400 两笔）',
        v92.zzp && v92.zzp[3] === '+CN¥600' && v92.zzp[4] === '50.0%' && v92.zzp[5] === '2.50',
        JSON.stringify(v92.zzp));
  check('品种统计(v50.92): 只有盈利时盈亏比显示 ∞ · 胜率 100.0%',
        v92.zzf && v92.zzf[4] === '100.0%' && v92.zzf[5] === '∞', JSON.stringify(v92.zzf));
  check('品种统计(v50.92): 只开仓没平的品种也占一行(已平仓/盈亏/胜率都显示 —)',
        v92.zzc && v92.zzc[2] === '—' && v92.zzc[3] === '—' && v92.zzc[4] === '—',
        JSON.stringify(v92.zzc));
  check('品种统计(v50.92): 盈亏是红(红涨绿跌) · 底部有合计说明',
        v92.pnlColor === v92.red && /pos/.test(v92.pnlCls) && /盈亏合计/.test(v92.tip),
        JSON.stringify({color: v92.pnlColor, red: v92.red, tip: (v92.tip || '').slice(0, 60)}));
  check('品种统计(v50.92): 切到期货模式不混入期权品种',
        v92.futHasZzp === false, String(v92.futHasZzp));
  check('品种统计(v50.93): 按盈亏从多到少排（2400 → 600 → 600 → 0，同额按记录数）',
        v92.keys.join(',') === 'zzg,zzp,zzf,zzc',
        JSON.stringify({keys: v92.keys, pnls: v92.pnls}));
  check('品种统计(v50.93): 行背景柱状图宽度 = |盈亏| ÷ 最大 |盈亏|（2400→100% / 600→25% / 0→无柱）',
        v92.bars[0].w === '--w:100%' && v92.bars[1].w === '--w:25%'
        && v92.bars[2].w === '--w:25%' && v92.bars[3].w === ''
        && /100%/.test(v92.bars[0].bg) && /25%/.test(v92.bars[1].bg),
        JSON.stringify(v92.bars.map(x => [x.w, x.bg.slice(-30)])));
  check('品种统计(v50.93): 盈利行红柱 / 亏损行绿柱（柱子是行背景，不是覆盖层）',
        /bar-pos/.test(v92.bars[0].cls) && /rgba\(255, 107, 107, 0\.16\)/.test(v92.bars[0].bg),
        JSON.stringify(v92.bars[0]));
  check('品种统计(v50.93): 柱子不挡数据 —— 仍是 6 列、品种名颜色 = 正常正文色',
        v92.tdCount === 6 && v92.nameColor === v92.textColor,
        JSON.stringify({td: v92.tdCount, name: v92.nameColor, text: v92.textColor}));
  check('品种统计(v50.92): 用例清理干净', v92.cleaned === true, String(v92.cleaned));
  // ===== v50.94: 监控池品种用 \ 分隔也能拆开(三个模式同一个弹窗) =====
  const v94Run = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    const out = {};
    const listPool = async () => (await (await fetch('/api/trades/pool?mode=options')).json()).snapshots || [];
    // ⚠ 反斜杠在「模板字符串 + 字符串字面量」里会被解析两次(\f 会变成换页符) → 用码点拼, 别写转义
    const BS = String.fromCharCode(92);
    // 清空期权模式的监控池, 便于断言
    for (const it of await listPool()){
      await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: it.id})});
    }
    // ① 接口层: 一个元素里带反斜杠 → 后端也要拆
    await fetch('/api/trades/pool/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({contracts: ['si' + BS + 'lc' + BS + 'fu'], mode: 'options'})});
    out.api = (await listPool())[0].contracts;
    for (const it of await listPool()){
      await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: it.id})});
    }
    // ② 界面层: 输入框里敲 si\lc|fu/ao → 四个 chip
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click(); await w(700);
    TradeUI.setMode('options'); await TradeUI.refresh(); await w(600);
    const realPrompt = window.prompt;
    window.prompt = () => 'si' + BS + 'lc|fu/ao';
    document.getElementById('btnNewPool').click();
    for (let i = 0; i < 30 && !document.querySelector('#poolArea .pool-chip'); i++) await w(150);
    await w(300);
    window.prompt = realPrompt;
    out.ui = [...document.querySelectorAll('#poolArea .pool-chip')].map(x => x.textContent.trim());
    out.stored = (await listPool())[0].contracts;
    // 清理
    for (const it of await listPool()){
      await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: it.id})});
    }
    out.cleaned = (await listPool()).length === 0;
    return JSON.stringify(out);
  })()`);
  const v94 = JSON.parse(v94Run);
  check('监控池分隔符(v50.94): 接口收到「反斜杠分隔」的一个元素 → 落库拆成 3 个品种',
        (v94.api || []).join(',') === 'si,lc,fu', JSON.stringify(v94.api));
  check('监控池分隔符(v50.94): 输入框敲「反斜杠 + 竖线 + 斜杠」混合分隔 → 界面 4 个 chip 且落库 4 条',
        (v94.ui || []).join(',') === 'si,lc,fu,ao'
        && (v94.stored || []).join(',') === 'si,lc,fu,ao',
        JSON.stringify({ui: v94.ui, stored: v94.stored}));
  check('监控池分隔符(v50.94): 用例清理干净', v94.cleaned === true, String(v94.cleaned));


  // ===== v50.56: 资金曲线「累计提现」字号与同行一致 + 跟随字号设置 =====
  const wdRun = await evalJs(ws, `(async () => {
    const w = ms => new Promise(r => setTimeout(r, ms));
    document.querySelector('#mainTabs .maintab[data-tab="funds"]').click();
    await w(1000);
    const fs = el => el ? getComputedStyle(el).fontSize : 'none';
    const chip = document.getElementById('wdAbe');
    const lbl = document.querySelector('#withdrawBox .w-lbl');
    const label = document.querySelector('.funds-bar .funds-label');
    const smBtn = document.getElementById('btnClearAll');
    const read = () => ({chip: fs(chip), lbl: fs(lbl), label: fs(label), sm: fs(smBtn)});
    const md = read();
    document.querySelector('#fontSeg button[data-fz="lg"]').click();
    await w(450);
    const lg = read();
    document.querySelector('#fontSeg button[data-fz="sm"]').click();
    await w(450);
    const smm = read();
    document.querySelector('#fontSeg button[data-fz="md"]').click();
    await w(350);
    return JSON.stringify({md, lg, sm: smm});
  })()`);
  const wd = JSON.parse(wdRun);
  check('提现字号(v50.56): 「累计提现」与同行按钮字号一致(不再写死 11.5px)',
        wd.md.chip === wd.md.sm && wd.md.lbl === wd.md.sm && wd.md.label === wd.md.sm,
        JSON.stringify(wd.md));
  check('提现字号(v50.56): 跟随字号设置缩放(小 < 中 < 大)',
        parseFloat(wd.sm.chip) < parseFloat(wd.md.chip) && parseFloat(wd.md.chip) < parseFloat(wd.lg.chip),
        'sm=' + wd.sm.chip + ' md=' + wd.md.chip + ' lg=' + wd.lg.chip);

  // ===== v50.41: 侧栏文案 / 测算结果两列 / 检查更新 =====
  const v541 = await evalJs(ws, `(() => {
    const out = {};
    // 1) 侧栏交易记录小字不能带冒号
    out.sideSmall = [...document.querySelectorAll('#mainTabs .maintab small')].map(e => e.textContent.trim());
    out.sideHasColon = [...document.querySelectorAll('#mainTabs .maintab small')]
      .some(e => /^[：:]/.test(e.textContent.trim()));
    // 2) 测算结果: 两列网格 + 无遗留 drow
    // ⚠ 不能用 gridTemplateColumns 字符串分词数判断 — 浏览器会原样返回
    //   "repeat(2, minmax(0px, 1fr))"(3 个空格分隔 token), 看起来像 3 列
    //   改成看声明列数 + 强行排布后每行几个格子
    document.querySelector('#mainTabs .maintab[data-tab="calc"]').click();
    const df = document.getElementById('rDetailF');
    out.fGrid = !!df && df.classList.contains('grid2');
    out.fDrow = df ? df.querySelectorAll('.drow').length : -1;
    out.fCells = df ? df.querySelectorAll('.dcell').length : -1;
    // 用 computed grid-template-columns 的「声明列数」(repeat(n,...) 取 n)
    // ⚠ 正则写在模板字符串里要写双反斜杠, 否则 \\d 会被吃成 d
    const _gtc = df ? getComputedStyle(df).gridTemplateColumns : '';
    const _rep = _gtc.match(/repeat\\(\\s*(\\d+)/);
    out.fCols = _rep ? +_rep[1] : _gtc.split(' ').filter(x => x).length;
    // 每行格子数: 按 rect.top 分组(同 top = 同一行).
    // ⚠ 不能用 offsetTop: 此时若面板不可见, 所有 offset 都是 0 → 会假判成 8 个一行.
    //   改成临时挂一个离屏但参与布局的容器量一次(量完立刻移除, 不动真实 DOM 状态)
    if (df) {
      const host = document.createElement('div');
      host.style.cssText = 'position:absolute;left:-99999px;top:0;width:900px';
      const probe = df.cloneNode(true);
      host.appendChild(probe);
      document.body.appendChild(host);
      const tops = [...probe.querySelectorAll('.dcell')].map(c => Math.round(c.getBoundingClientRect().top));
      out.fPerRow = tops.filter(t => t === tops[0]).length;
      out.fRows = new Set(tops).size;
      host.remove();
    } else { out.fPerRow = -1; out.fRows = -1; }
    // 每个格子都是「标签在上、数值在下」的块级布局
    const c0 = df ? df.querySelector('.dcell') : null;
    out.fCellStack = !!c0 && getComputedStyle(c0.querySelector('.k')).display === 'block'
      && getComputedStyle(c0.querySelector('.v')).display === 'block';
    // 标签必须留在 DOM 里(改成 title 提示也不能丢掉可见标签)
    out.fLabels = df ? [...df.querySelectorAll('.dcell .k')].map(e => e.textContent.trim()) : [];
    // 3) 期权测算卡也走两列
    const do_ = document.getElementById('rDetailO');
    out.oGrid = !!do_ && do_.classList.contains('grid2');
    out.oDrow = do_ ? do_.querySelectorAll('.drow').length : -1;
    // 4) 侧栏「⟳ 检查更新」按钮 + 红点元素存在
    out.updBtn = !!document.getElementById('btnUpdate');
    out.updDot = !!document.getElementById('updateDot');
    out.updBg = !!document.getElementById('updateBg');
    out.updBtnTitle = (document.getElementById('btnUpdate') || {}).title || '';
    // v50.41.1: 检查更新图标必须与导出/导入不重复 —— 曾一度都用 ⬆ 撞车
    out.iconUpd = (document.getElementById('btnUpdate') || {}).textContent.trim();
    out.iconExp = (document.getElementById('btnExport') || {}).textContent.trim();
    out.iconImp = (document.getElementById('btnImport') || {}).textContent.trim();
    out.modalTitle = (document.querySelector('#updateBg h3') || {}).textContent || '';
    out.updActions = ['updDownload','updBackup','updOpenDir','updClose']
      .filter(id => !document.getElementById(id)).length;   // 缺几个
    return out;
  })()`);

  check('侧栏: 五个 tab 小字已去掉冒号',
        v541.sideSmall.length === 5 && !v541.sideHasColon, JSON.stringify(v541.sideSmall));
  check('侧栏: 小字内容为 期货 · 期权 / 期权买方 / 期权双买 / 期货模式 / 多策略',
        JSON.stringify(v541.sideSmall) === JSON.stringify(['期货 · 期权','期权买方','期权双买','期货模式','多策略']),
        JSON.stringify(v541.sideSmall));
  check('测算结果: 期货明细改为两列网格',
        v541.fGrid && v541.fCols === 2 && v541.fPerRow === 2,
        'grid2=' + v541.fGrid + ' cols=' + v541.fCols + ' perRow=' + v541.fPerRow);
  check('测算结果: 8 项全部保留为 dcell 且无遗留 drow',
        v541.fCells === 8 && v541.fDrow === 0, 'cells=' + v541.fCells + ' drow=' + v541.fDrow);
  check('测算结果: 每格「标签在上 · 数值在下」', v541.fCellStack);
  check('测算结果: 原有标签文字未丢失',
        v541.fLabels.indexOf('开仓标的') >= 0 && v541.fLabels.indexOf('每手风险金额') >= 0
        && v541.fLabels.indexOf('最大占用保证金') >= 0, JSON.stringify(v541.fLabels));
  check('测算结果: 期权明细也改为两列', v541.oGrid && v541.oDrow === 0, 'drow=' + v541.oDrow);
  check('侧栏: 存在「⟳ 检查更新」按钮', v541.updBtn, v541.updBtnTitle);
  check('侧栏: 检查更新图标不与导出/导入重复',
        v541.iconUpd && v541.iconUpd !== v541.iconExp && v541.iconUpd !== v541.iconImp
        && v541.iconUpd === '⟳',
        'upd=' + v541.iconUpd + ' exp=' + v541.iconExp + ' imp=' + v541.iconImp);
  check('更新弹窗: 标题为「⟳ 检查更新」', v541.modalTitle.indexOf('⟳ 检查更新') >= 0, v541.modalTitle);
  check('侧栏: 更新提示红点元素存在且默认隐藏', v541.updDot);
  check('更新弹窗: DOM 完整(下载/备份/打开目录/关闭 齐全)', v541.updBg && v541.updActions === 0,
        'missing=' + v541.updActions);

  // 红点逻辑: 模拟有更新 → 亮; 无更新 → 灭
  const dot = await evalJs(ws, `(() => {
    const restore = UpdUI.info;
    UpdUI.info = {ok: true, has_update: true, latest_name: 'v99.99'};
    UpdUI.paintDot();
    const on = !document.getElementById('updateDot').classList.contains('hidden');
    const tOn = document.getElementById('btnUpdate').title;
    UpdUI.info = {ok: true, has_update: false};
    UpdUI.paintDot();
    const off = document.getElementById('updateDot').classList.contains('hidden');
    const tOff = document.getElementById('btnUpdate').title;
    // 检查失败(网络不通)时不能亮红点误报
    UpdUI.info = {ok: false, has_update: false, error: 'x'};
    UpdUI.paintDot();
    const errOff = document.getElementById('updateDot').classList.contains('hidden');
    UpdUI.info = restore;
    UpdUI.paintDot();
    return {on, off, errOff, tOn, tOff};
  })()`);
  check('更新红点: 有新版本时亮起', dot.on, JSON.stringify(dot.tOn));
  check('更新红点: 无新版本时消失', dot.off, JSON.stringify(dot.tOff));
  check('更新红点: 检查失败时不误亮(不打扰)', dot.errOff);
  check('更新按钮 title: 有更新时提示版本号', /v99\.99/.test(dot.tOn || ''), String(dot.tOn));
  check('更新按钮 title: 无更新时为「检查更新」', /检查更新/.test(dot.tOff || ''), String(dot.tOff));

  // 不自动弹窗: 页面加载后更新弹窗必须仍是关闭状态
  check('更新: 启动静默检查不自动弹窗',
        await evalJs(ws, `document.getElementById('updateBg').classList.contains('hidden')`));

  // 接口契约: /api/update/check 在测试环境(可能断网)也必须返回结构化结果, 不能 500
  // ⚠ 版本号不写死: 与 /api/version 交叉校验两处一致 —— 否则每次升版本都得改断言
  const updApi = await evalJs(ws, `(async () => {
    const r = await fetch('/api/update/check');
    const d = await r.json();
    const v = await (await fetch('/api/version')).json();
    return {status: r.status, ok: d.ok, cur: d.current, ver: v.version,
            curName: d.current_name, keys: ['ok','current','current_name','latest','latest_name','has_update','notes','frozen']
                  .filter(k => (k in d)).length,
            err: d.error || ''};
  })()`);
  check('接口: /api/update/check 返回 200 且键位齐全',
        updApi.status === 200 && updApi.keys === 8, JSON.stringify(updApi).slice(0, 160));
  check('接口: /api/update/check 带回当前版本号(与 /api/version 一致)',
        updApi.cur === updApi.ver && updApi.cur > 0,
        'check=' + updApi.cur + ' version=' + updApi.ver);
  check('接口: /api/update/check 带回 current_name(vXX.YY 格式)',
        /^v\d+\.\d+$/.test(updApi.curName || ''), 'name=' + updApi.curName);
  check('接口: 断网时 ok=false 且带人话错误(不抛 500)',
        updApi.ok === true || (updApi.ok === false && updApi.err.length > 0),
        updApi.ok ? 'online' : updApi.err);

  const updBk = await evalJs(ws, `(async () => {
    const r = await fetch('/api/update/backups');
    const d = await r.json();
    const v = await (await fetch('/api/version')).json();
    return {status: r.status, ok: d.ok, isArr: Array.isArray(d.backups),
            vname: d.version_name, ver: v.version, frozen: d.frozen};
  })()`);
  check('接口: /api/update/backups 返回 200 + 列表 + 版本名(与 /api/version 同源)',
        updBk.status === 200 && updBk.ok && updBk.isArr
        && /^v\d+\.\d+$/.test(updBk.vname || '') && updBk.vname === 'v' + String(updBk.ver).slice(0, -2) + '.' + String(updBk.ver).slice(-2),
        JSON.stringify(updBk));
  // ⚠ 这条别写死 false: 跑源码服务时 frozen=false, 跑打包 exe 时必须是 true(本身就是 exe)。
  //   对 exe 实例跑同一套 E2E 时用 OC_E2E_FROZEN=1 声明期望值。
  const expectFrozen = process.env.OC_E2E_FROZEN === '1';
  check('接口: frozen 与运行形态一致 (不谎报可替换 exe)',
        updBk.frozen === expectFrozen, 'got=' + updBk.frozen + ' expect=' + expectFrozen);

  const updProg = await evalJs(ws, `(async () => {
    const r = await fetch('/api/update/progress');
    const d = await r.json();
    return {status: r.status, ok: d.ok, hasDl: typeof d.downloaded === 'number',
            hasTotal: typeof d.total === 'number', hasRunning: typeof d.running === 'boolean'};
  })()`);
  check('接口: /api/update/progress 返回 200 + 进度字段齐全',
        updProg.status === 200 && updProg.ok && updProg.hasDl && updProg.hasTotal && updProg.hasRunning,
        JSON.stringify(updProg));

  // 下载按钮文案在轮询中会变成「⬇ x/10.6 MB」→ 至少确认轮询逻辑存在(不真的下载 10MB)
  check('更新: 下载按钮默认文案为「⬇ 下载最新版」',
        (await evalJs(ws, `(document.getElementById('updDownload').textContent||'').trim()`)) === '⬇ 下载最新版');
  check('更新: 下载走轮询显示进度(源码含 /api/update/progress)',
        /api\/update\/progress/.test(await evalJs(ws, `UpdUI.download.toString()`)));

  const failed = results.filter(r => !r.ok);
  console.log('\n==== 结果: ' + (results.length - failed.length) + '/' + results.length + ' 通过 ====');
  ws.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch(e => { console.error('E2E FATAL:', e); process.exit(2); });
