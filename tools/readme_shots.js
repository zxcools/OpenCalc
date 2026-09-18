// README 功能截图生成器(第二版): 滚动 + 整屏截图, 坐标绝对可靠
// 前置: OC_DATA_DIR=<临时库> python main.py  (服务 8899) + Chrome CDP(9231)
// 用法(在项目根目录运行):
//   python tools/readme_shots_seed.py            # 先灌演示数据
//   OC_E2E_BASE=http://127.0.0.1:8899 OC_CDP_PORT=9231 node tools/readme_shots.js
//   python tools/readme_shots_trim.py            # 裁切 + 缩放
const BASE = process.env.OC_E2E_BASE || 'http://127.0.0.1:8899';
const CDP_PORT = process.env.OC_CDP_PORT || '9231';
const OUT = 'docs/screenshots';
const fs = require('fs');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));

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
let msgId = 0; const pending = new Map();
function send(ws, method, params = {}) {
  return new Promise(res => { const id = ++msgId; pending.set(id, { res }); ws.send(JSON.stringify({ id, method, params })); });
}
async function ev(ws, expr) {
  const m = await send(ws, 'Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  const rr = m.result || {};
  if (rr.exceptionDetails) throw new Error('JS: ' + JSON.stringify(rr.exceptionDetails.exception || rr.exceptionDetails.text));
  return (rr.result || {}).value;
}

async function setViewport(ws, h) {
  await send(ws, 'Emulation.setDeviceMetricsOverride',
    { width: 1560, height: h, deviceScaleFactor: 2, mobile: false });
}

const meta = [];

// 整屏截图(从窗口顶部开始): 视口高度先按内容自适应, 再截
async function snap(ws, name, { maxH = 1400, y = 0, right = 1560, left = 0, top = 0 } = {}) {
  // 视口高度会反过来影响内容高度(vh 布局) → 迭代两次收敛
  let h = 1100;
  for (let i = 0; i < 3; i++) {
    await setViewport(ws, h);
    await sleep(450);
    const docH = await ev(ws, `document.documentElement.scrollHeight`);
    const next = Math.min(Math.max(docH, 760), maxH);
    if (next === h) break;
    h = next;
  }
  await setViewport(ws, h);
  await sleep(500);
  const info = await ev(ws, `(async () => {
    window.scrollTo(0, ${y});
    await new Promise(r => setTimeout(r, 350));
    return JSON.stringify({ sy: window.scrollY, docH: document.documentElement.scrollHeight });
  })()`);
  const { sy, docH } = JSON.parse(info);
  await sleep(600);
  const r = await send(ws, 'Page.captureScreenshot', { format: 'png' });
  if (!r.result || !r.result.data) { console.log('ERR shot ' + name); return; }
  fs.mkdirSync(OUT, { recursive: true });
  const p = path.join(OUT, name);
  fs.writeFileSync(p, Buffer.from(r.result.data, 'base64'));
  meta.push({ file: name, right, h, sy, docH, left, top });
  console.log('shot ' + name + '  scrollY=' + sy + '/' + docH + '  vh=' + h + '  left=' + left + '  ' +
    Math.round(fs.statSync(p).size / 1024) + 'KB');
}

async function tab(ws, t) {
  await ev(ws, `document.querySelector('#mainTabs .maintab[data-tab="${t}"]').click()`);
  await sleep(2400);
}

async function main() {
  const ws = new WebSocket(await getWsUrl());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id).res(m); pending.delete(m.id); } };
  await send(ws, 'Page.enable');
  await send(ws, 'Runtime.enable');
  await setViewport(ws, 1000);
  await send(ws, 'Page.navigate', { url: BASE + '/' });
  await sleep(2800);
  await ev(ws, `(() => { localStorage.setItem('oc-theme','dark'); document.body.classList.remove('light');
    document.body.classList.remove('fz-sm','fz-lg'); })()`);
  await ev(ws, `window.scrollTo(0,0)`);
  await sleep(400);

  // ================= 1) 开仓计算器 =================
  await tab(ws, 'calc');
  await ev(ws, `
    (() => {
      const eq = document.getElementById('equity');
      eq.value = '90'; eq.dispatchEvent(new Event('input'));
      const cu = CONTRACTS.find(x => String(x.code).toLowerCase() === 'cu');
      pickContract(cu, 'F');
      document.getElementById('entry').value = '72150';
      document.getElementById('stop').value = '70800';
      document.getElementById('target').value = '75000';
      document.getElementById('marginRate').value = '16';
      document.getElementById('riskAmount').value = '1';
      onInput();
      window.scrollTo(0,0);
    })()
  `);
  await sleep(2000);
  await ev(ws, `window.scrollTo(0,0)`);
  await snap(ws, 'calc.png', { maxH: 1300 });

  // ================= 2) 交易记录 · 期货 =================
  await tab(ws, 'tradesFut');
  await snap(ws, 'trades-futures.png', { maxH: 1180 });

  // 详情(沪铜: 已平仓 + 测算卡 + 复盘)
  await ev(ws, `(async () => {
    const cu = TradeUI.groups.find(g => g.underlying === 'cu');
    await TradeUI.loadDetail(cu.underlying, cu.batch);
    window.scrollTo(0, 0);
  })()`);
  await sleep(2200);
  await ev(ws, `window.scrollTo(0,0)`);
  // 详情是 fixed 浮层(不挤压主表) → 单独框住浮层, 细节看得清
  const sideLeft = await ev(ws, `(() => {
    const el = document.querySelector('.trades-side');
    return el ? Math.max(0, Math.round(el.getBoundingClientRect().left) + 6) : 0;
  })()`);
  await snap(ws, 'trade-detail.png', { maxH: 1420, left: sideLeft, top: 58 });

  // ================= 3) 交易记录 · 期权 =================
  await tab(ws, 'trades');
  await snap(ws, 'trades-options.png', { maxH: 1120 });

  // ================= 4) 资金曲线 =================
  await tab(ws, 'funds');
  // 只截到「年度汇总」(下方图表在窄栏里标签会挤, 单独放大截)
  await snap(ws, 'funds.png', { maxH: 846 });
  await ev(ws, `document.querySelector('.zoombtn[data-zoom="monthly"]').click()`);
  await sleep(1800);
  // 放大浮层: 取内部卡片边界(遮罩是全屏的)
  const zbox = await ev(ws, `(() => {
    let el = document.getElementById('chartZoomBg');
    if (!el) return '';
    const inner = el.firstElementChild;
    const t = inner && inner.getBoundingClientRect().width < window.innerWidth - 40 ? inner : el;
    const r = t.getBoundingClientRect();
    return JSON.stringify({l: Math.max(0, Math.round(r.left) - 6), t: Math.max(0, Math.round(r.top) - 6)});
  })()`);
  const zb = zbox ? JSON.parse(zbox) : { l: 0, t: 0 };
  await snap(ws, 'funds-chart.png', { maxH: 850, left: zb.l, top: zb.t });

  fs.writeFileSync('_tmp_shot_meta.json', JSON.stringify(meta, null, 1));
  console.log('DONE');
  process.exit(0);
}
main().catch(e => { console.error('ERR', e.message); process.exit(1); });
