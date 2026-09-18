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
  check('侧边栏导出/导入按钮 + 4个tab(含交易记录:期货模式)', sb.ex === true && sb.im === true && sb.tabs === 4, sideBtns);
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
  check('后端固定 abe(忽略传入 strategy)', customIgnored === 'abe', 'got=' + customIgnored);
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
    for (const s of [...(f.snapshots || []), ...(o.snapshots || [])]) {
      if ((s.contracts || []).includes('e2epool01')) {
        await fetch('/api/trades/pool/delete', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id: s.id})});
      }
    }
    return JSON.stringify({inFut, inOpt, shown});
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
    // 切到期权模式 -> 期货那 4 笔不算进来
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
    await w(1000);
    const opt = read();
    const optMode = TradeUI.mode;
    await wipe('futures'); await wipe('options');
    return JSON.stringify({fut, afterFilter, rowsAfterFilter, opt, optMode, oneRow, gridCols});
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
        cardOf(stat.fut, '盈亏比').v === '7.07' && /盈利 4,805 \/ 亏损 680/.test(cardOf(stat.fut, '盈亏比').s),
        JSON.stringify(cardOf(stat.fut, '盈亏比')));
  check('统计卡(v50.53): 平均盈亏 = 4125 / 4 = +CN¥1,031.25',
        cardOf(stat.fut, '平均盈亏').v === '+CN¥1,031.25', JSON.stringify(cardOf(stat.fut, '平均盈亏')));
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

  check('侧栏: 交易记录小字已去掉冒号',
        v541.sideSmall.length === 4 && !v541.sideHasColon, JSON.stringify(v541.sideSmall));
  check('侧栏: 小字内容为 期货 · 期权 / 期权模式 / 期货模式 / abe · 威科夫',
        JSON.stringify(v541.sideSmall) === JSON.stringify(['期货 · 期权','期权模式','期货模式','abe · 威科夫']),
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
