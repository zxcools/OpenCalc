// OpenCalc v49 UI E2E: 填参数→计算→阶梯→保存方案→刷新→调出方案 (13 项断言)
// 运行前置:
//   1. 软件服务运行在 8765 (启动 dist/OpenCalc.exe 或 python main.py)
//   2. Chrome headless 调试端口 9222:
//      "C:\...\chrome.exe" --headless=new --disable-gpu --no-first-run \
//        --remote-debugging-port=9222 --user-data-dir=<临时目录> about:blank
//   3. node oc_cdp_test.js   (退出码 0=全过; 1=有断言失败; 2=脚本错误)
const BASE = 'http://127.0.0.1:8765';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getWsUrl() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch('http://127.0.0.1:9222/json/list')).json();
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
  const ws = new WebSocket(await getWsUrl());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m); pending.delete(m.id); }
  };

  await send(ws, 'Page.enable');
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
  check('监控池快照展示品种', /si/.test(poolText || '') && /cf/.test(poolText || '') && /2026-09-09/.test(poolText || ''), poolText.replace(/\s+/g,' ').slice(0,120));
  // 侧边栏导入导出按钮存在
  const sideBtns = await evalJs(ws, `JSON.stringify({ex: !!document.getElementById('btnExport'), im: !!document.getElementById('btnImport'), tabs: document.querySelectorAll('#mainTabs .maintab').length})`);
  const sb = JSON.parse(sideBtns);
  check('侧边栏导出/导入按钮 + 3个tab', sb.ex === true && sb.im === true && sb.tabs === 3, sideBtns);
  // 箭头方向: 导出 ⬆(出去) / 导入 ⬇(进来)
  const arrowDir = await evalJs(ws, `JSON.stringify({ex: document.getElementById('btnExport').textContent.trim(), im: document.getElementById('btnImport').textContent.trim()})`);
  const ad = JSON.parse(arrowDir);
  check('箭头方向 导出⬆ / 导入⬇', ad.ex === '⬆' && ad.im === '⬇', arrowDir);

  // ---- 场景G: UI 结构打磨 (v50.1) ----
  // 先关掉场景 F 留下的详情面板, 保证干净的 has-detail 检测
  await evalJs(ws, `document.querySelector('#tdClose')?.click()`);
  await sleep(400);
  // 1. 主页面 toolbar 只保留「新建开仓」(无「新建平仓」)
  const mainToolbar = await evalJs(ws, `JSON.stringify({newOpen: !!document.getElementById('btnNewOpen'), newClose: !!document.getElementById('btnNewClose'), onlyOpen: !!document.getElementById('tradesOnlyOpen'), showAll: !!document.getElementById('btnShowAll')})`);
  const mt = JSON.parse(mainToolbar);
  check('主页面 toolbar 含「只展示未平仓」+「新建开仓」+「显示全部」', mt.onlyOpen && mt.newOpen && mt.showAll, mainToolbar);
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
  // 5. 侧边栏 side-extras 含 4 个图标按钮(导出/导入/数据位置/联系作者)
  const sideBtnsCount = await evalJs(ws, `document.querySelectorAll('.side-extras .side-btn').length`);
  check('侧边栏底部 4 个图标按钮(导出/导入/数据位置/联系作者)', sideBtnsCount === 4, 'count=' + sideBtnsCount);
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

  const failed = results.filter(r => !r.ok);
  console.log('\n==== 结果: ' + (results.length - failed.length) + '/' + results.length + ' 通过 ====');
  ws.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch(e => { console.error('E2E FATAL:', e); process.exit(2); });
