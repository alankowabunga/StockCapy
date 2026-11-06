# 吉祥物出現的畫面

所有放了吉祥物的畫面截圖。由 `tools/capture_mascot_screens.js` 產生（用法在檔案開頭註解），
改了畫面或換了圖之後重跑即可更新。圖片來源與用途清單見 `assets/README.md`。
網頁是 3 個主分頁（今天／我的股票／提醒與資料），下方列出每張截的是哪個分頁與子頁。

## 真實網頁（空資料庫下實際跑出來的畫面）

| 檔案 | 畫面 | 吉祥物 |
|---|---|---|
| `01-login.png` | API token 登入頁 | 3D 場景大圖 |
| `02-today.png` | 今天首頁，還沒有持股 | thumbsup 貼圖（持股空狀態） |
| `03-targets-empty.png` | 我的股票 › 入手價，沒有標的；下方監控清單也是空的 | winner 貼圖、bull 貼圖 |
| `04-positions-empty.png` | 我的股票 › 持股；下方淨值走勢空狀態 | thumbsup 貼圖、紅箭頭 K 線 |
| `05-dividends-empty.png` | 我的股票 › 股利，沒有已領股利 | party 貼圖 |
| `06-notify-watchlist-empty.png` | 提醒與資料 › 通知設定，沒有提醒對象 | 吉祥物的臉 |
| `07-price-alert-triggered.png` | 提醒與資料 › 通知設定，「立即檢查」觸發提醒（範例資料填入） | party 小貼圖 |
| `08-import-success.png` | 提醒與資料 › 匯入，匯入成功（範例資料填入） | thumbsup 小貼圖 |
| `09-manual-positions-empty.png` | 提醒與資料 › 手動持倉，沒有資料 | thumbsup 貼圖 |
| `10-desktop-targets-empty.png` | 入手價頁的桌面版寬度 | winner 貼圖 |

每張的頂端與頁尾都有扁平小頭像。

## 示意圖（手工拼的 HTML，只用來看位置，不是真實 app）

| 檔案 | 畫面 | 吉祥物 |
|---|---|---|
| `11-phone-home-screen.png` | 手機主畫面上的 app icon | 3D 金框 icon |
| `12-browser-tab.png` | 瀏覽器分頁 | favicon（扁平頭像） |
| `13-telegram-bot.png` | Telegram bot 對話與頭像 | 比讚貼圖頭像 |
| `14-github-readme.png` | GitHub README 頂部（淺色與深色模式） | 3D 橫幅 |
