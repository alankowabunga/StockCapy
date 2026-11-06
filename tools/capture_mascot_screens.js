/*
 * Captures every screen that shows the mascot into docs/screens/ (real web pages plus a few mock-ups).
 *
 * Needs Node with Playwright (`npm i -g playwright`) and two running servers on a throwaway data dir:
 *   PORTFOLIO_DATA_DIR=/tmp/capy-data uvicorn portfolio.web.api:create_app --factory --port 8765
 *   PORTFOLIO_DATA_DIR=/tmp/capy-data PORTFOLIO_API_TOKEN=secret uvicorn portfolio.web.api:create_app --factory --port 8766
 *   node tools/capture_mascot_screens.js
 * Real pages: the web app as it renders (empty database, so the empty states show). Success boxes are filled
 * in by calling the page's own render functions with sample data. Mock-ups (home screen, browser tab, Telegram,
 * README) are hand-built HTML around the real image files, so they show placement, not an actual app.
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const os = require('os');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'docs', 'screens');
const BASE = process.env.CAPY_BASE || 'http://127.0.0.1:8765';
const BASE_AUTH = process.env.CAPY_BASE_AUTH || 'http://127.0.0.1:8766';
const file = (...parts) => 'file://' + path.join(ROOT, ...parts);
const STATIC = path.join('src', 'portfolio', 'web', 'static');

async function web(browser, name, url, { width = 430, height = 800, fullPage = true, after } = {}) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
  page.on('pageerror', (error) => console.log('pageerror', name, error.message));
  await page.goto(url);
  await page.waitForTimeout(1500);
  if (after) await after(page);
  await page.screenshot({ path: path.join(OUT, name), fullPage });
  console.log('wrote', name);
  await page.close();
}

async function mock(browser, name, html, width, height) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
  // setContent() pages cannot load file:// images, so write the mock-up to a real file first.
  const temp = path.join(os.tmpdir(), 'capy-mock-' + name + '.html');
  fs.writeFileSync(temp, '<!doctype html><meta charset="utf-8">' + html);
  await page.goto('file://' + temp);
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, name) });
  console.log('wrote', name);
  await page.close();
}

const FONT = "font-family:-apple-system,'Segoe UI','Noto Sans CJK TC','PingFang TC',sans-serif";

function homeScreenHtml() {
  const placeholders = ['相機', '地圖', '日曆', '天氣', '時鐘', '筆記', '設定']
    .map((label, i) => `<div class="app"><div class="tile" style="background:hsl(${210 + i * 18} 22% ${46 + (i % 3) * 6}%)"></div><span>${label}</span></div>`)
    .join('');
  return `<body style="margin:0;${FONT}"><style>
    body{width:430px;height:860px;background:linear-gradient(160deg,#6a5bd6,#2a6fdb 55%,#12b5c9);overflow:hidden}
    .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:28px 12px;padding:96px 26px 0}
    .app{display:flex;flex-direction:column;align-items:center;gap:7px;color:#fff;font-size:12px}
    .tile,.icon{width:72px;height:72px;border-radius:17px}
    .icon{background:url(${file(STATIC, 'icons', 'icon-192.png')}) center/cover;box-shadow:0 6px 14px rgba(0,0,0,.3)}
    .tile{opacity:.85}
    .dock{position:absolute;left:18px;right:18px;bottom:22px;height:92px;border-radius:30px;background:rgba(255,255,255,.28);backdrop-filter:blur(10px)}
    .time{position:absolute;top:28px;left:0;right:0;text-align:center;color:#fff;font-weight:600}
  </style><div class="time">9:41</div><div class="grid">
    <div class="app"><div class="icon"></div><span>StockCapy</span></div>${placeholders}</div><div class="dock"></div></body>`;
}

function browserTabHtml() {
  return `<body style="margin:0;${FONT};background:#dfe3e8;width:760px;height:150px">
  <style>.tab{position:absolute;top:14px;height:38px;border-radius:10px 10px 0 0;padding:0 14px;display:flex;align-items:center;gap:8px;font-size:13px}
  .bar{position:absolute;left:0;right:0;top:52px;height:98px;background:#fff;border-bottom:1px solid #ccd}
  .url{position:absolute;left:24px;right:24px;top:62px;height:30px;border-radius:15px;background:#eef1f5;font-size:13px;display:flex;align-items:center;padding-left:16px;color:#556}
  img{width:16px;height:16px}</style>
  <div class="tab" style="left:12px;background:#fff;z-index:2"><img src="${file(STATIC, 'icons', 'favicon-48.png')}"><span>StockCapy · 入手價</span></div>
  <div class="tab" style="left:206px;background:#d0d5dc;color:#667"><span>新分頁</span></div>
  <div class="bar"></div><div class="url">localhost:8765/#targets</div></body>`;
}

function telegramHtml() {
  return `<body style="margin:0;${FONT};width:430px;height:420px;background:#8fb3d9">
  <style>.head{height:64px;background:#fff;display:flex;align-items:center;gap:12px;padding:0 14px;box-shadow:0 1px 4px rgba(0,0,0,.15)}
  .av{width:44px;height:44px;border-radius:50%;background:url(${file('assets', 'capy-avatar.png')}) center/cover}
  .name{font-weight:600}.sub{font-size:12px;color:#8a94a0}
  .bubble{margin:22px 14px 0 66px;position:relative;background:#fff;border-radius:14px 14px 14px 4px;padding:10px 12px;font-size:14px;line-height:1.5;max-width:300px}
  .mini{position:absolute;left:-52px;bottom:0;width:40px;height:40px;border-radius:50%;background:url(${file('assets', 'capy-avatar.png')}) center/cover}
  .t{font-size:11px;color:#8a94a0;text-align:right}</style>
  <div class="head"><div class="av"></div><div><div class="name">StockCapy Bot</div><div class="sub">bot</div></div></div>
  <div class="bubble"><div class="mini"></div><b>🔔 TSM 跌到入手價</b><br>目標 $180.00 / 現價 $178.50<br><span style="color:#2a6fdb">淡定等到價，到了再動手。</span><div class="t">09:32</div></div></body>`;
}

function readmeHtml() {
  const panel = (dark) => `<div style="flex:1;padding:26px 30px;background:${dark ? '#0d1117' : '#fff'};color:${dark ? '#e6edf3' : '#1f2328'};border:1px solid ${dark ? '#30363d' : '#d0d7de'};border-radius:8px">
    <div style="text-align:center"><img src="${file('assets', 'capy-banner.png')}" width="380"></div>
    <h1 style="text-align:center;margin:12px 0 4px;font-size:28px;border-bottom:1px solid ${dark ? '#30363d' : '#d8dee4'};padding-bottom:10px">StockCapy</h1>
    <p style="font-size:13px;color:${dark ? '#9198a1' : '#59636e'}">${dark ? '深色模式' : '淺色模式'} · 橫幅右側淡出成透明，兩種底色都自然</p></div>`;
  return `<body style="margin:0;${FONT};background:#f6f8fa;width:960px;height:430px;display:flex;gap:20px;padding:20px;box-sizing:border-box">${panel(false)}${panel(true)}</body>`;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  await web(browser, '01-login.png', BASE_AUTH + '/', { height: 700 });
  await web(browser, '02-today.png', BASE + '/#today', { height: 640 });
  await web(browser, '03-targets-empty.png', BASE + '/#stocks/targets', { height: 560 });
  await web(browser, '04-positions-empty.png', BASE + '/#stocks/positions', { height: 700 });
  await web(browser, '05-dividends-empty.png', BASE + '/#stocks/dividends', { height: 640 });
  await web(browser, '06-notify-watchlist-empty.png', BASE + '/#manage/notify', { height: 700 });
  await web(browser, '07-price-alert-triggered.png', BASE + '/#manage/notify', {
    height: 640,
    fullPage: false,
    after: async (page) => {
      await page.evaluate(() => {
        document.getElementById('check-result').innerHTML = checkResultHtml({
          triggered: [
            { direction: 'BUY', headline: 'TSM 跌到入手價', symbol: 'TSM', target_price: 180, last_price: 178.5 },
            { direction: 'SELL', headline: 'NVDA 漲到出場價', symbol: 'NVDA', target_price: 140, last_price: 141 },
          ],
          notified: true,
        });
      });
      await page.locator('#check-result').scrollIntoViewIfNeeded();
    },
  });
  await web(browser, '08-import-success.png', BASE + '/#manage/import', {
    height: 640,
    fullPage: false,
    after: async (page) => {
      await page.evaluate(() => {
        var host = document.querySelector('[data-import-result]');
        host.hidden = false;
        host.innerHTML = importResultHtml({ parsed: 42, inserted: 42, activity_breakdown: { Buy: 31, Dividend: 9, Sell: 2 }, warnings: [] });
      });
      await page.locator('[data-import-result]').scrollIntoViewIfNeeded();
    },
  });
  await web(browser, '09-manual-positions-empty.png', BASE + '/#manage/manual', { height: 700 });
  await web(browser, '10-desktop-targets-empty.png', BASE + '/#stocks/targets', { width: 1100, height: 560, fullPage: false });
  await mock(browser, '11-phone-home-screen.png', homeScreenHtml(), 430, 860);
  await mock(browser, '12-browser-tab.png', browserTabHtml(), 760, 150);
  await mock(browser, '13-telegram-bot.png', telegramHtml(), 430, 420);
  await mock(browser, '14-github-readme.png', readmeHtml(), 960, 430);
  await browser.close();
})();
