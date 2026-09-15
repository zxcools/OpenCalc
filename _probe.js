// 实测: 点「显示全部」前后, 表格容器高度 / 内滚动 / 页面高度 的变化
const BASE = process.env.OC_E2E_BASE || 'http://127.0.0.1:8897';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let msgId = 0; const pending = new Map();
function send(ws, method, params = {}) {
  return new Promise((resolve) => { const id = ++msgId; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
}
async function evalJs(ws, expr) {
  const m = await send(ws, 'Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  const rr = m.result || {};
  if (rr.exceptionDetails) throw new Error('JS: ' + JSON.stringify(rr.exceptionDetails.exception || rr.exceptionDetails.text));
  return (rr.result || {}).value;
}
const PROBE = `JSON.stringify((() => {
  const t = document.getElementById('tradesTable');
  const w = t.closest('.tblwrap');
  const card = t.closest('.card');
  const main = t.closest('.trades-main') || card;
  const cs = getComputedStyle(w);
  return {
    rows: t.querySelectorAll('tbody tr').length,
    vis: [...t.querySelectorAll('tbody tr')].filter(r => r.offsetParent !== null).length,
    wrapH: Math.round(w.getBoundingClientRect().height),
    wrapClient: w.clientHeight, wrapScroll: w.scrollHeight,
    wrapOy: cs.overflowY, wrapMaxH: cs.maxHeight,
    cardH: Math.round(card.getBoundingClientRect().height),
    mainScroll: main.scrollHeight,
    bodyScroll: document.documentElement.scrollHeight,
    winH: window.innerHeight,
    pageScrollable: document.documentElement.scrollHeight > window.innerHeight
  };
})())`;
async function main() {
  let wsUrl = null;
  for (let i = 0; i < 30 && !wsUrl; i++) {
    try { const l = await (await fetch('http://127.0.0.1:9222/json/list')).json(); const p = l.find(t => t.type === 'page'); if (p) wsUrl = p.webSocketDebuggerUrl; } catch (e) {}
    if (!wsUrl) await sleep(500);
  }
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  await send(ws, 'Page.enable');
  await send(ws, 'Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await send(ws, 'Page.navigate', { url: BASE + '/' });
  await sleep(2600);
  await evalJs(ws, `(async () => {
    for (let i = 1; i <= 12; i++) {
      await fetch('/api/trades/upsert', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({underlying:'e2em'+String(i).padStart(2,'0'), contract:'e2em'+i+'C1000',
        op_type:'open', direction:'buy', open_date:'2026-08-'+String(i).padStart(2,'0'), call_put:'C',
        open_price:100, qty:1, premium:100})});
    }
    document.querySelector('#mainTabs .maintab[data-tab="trades"]').click();
  })()`);
  await sleep(1500);
  console.log('展开前:', await evalJs(ws, PROBE));
  await evalJs(ws, `document.getElementById('btnShowAllTrades').click()`);
  await sleep(700);
  console.log('展开后:', await evalJs(ws, PROBE));
  // 清理
  await evalJs(ws, `(async () => {
    const g = await (await fetch('/api/trades/groups')).json();
    for (const it of (g.groups||[])) {
      if (String(it.underlying).startsWith('e2em')) {
        const d = await (await fetch('/api/trades/detail?underlying=' + it.underlying)).json();
        for (const op of (d.operations||[])) await fetch('/api/trades/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:op.id})});
      }
    }
  })()`);
  process.exit(0);
}
main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
