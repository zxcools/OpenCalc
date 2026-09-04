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

  const failed = results.filter(r => !r.ok);
  console.log('\n==== 结果: ' + (results.length - failed.length) + '/' + results.length + ' 通过 ====');
  ws.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch(e => { console.error('E2E FATAL:', e); process.exit(2); });
