# 吉祥物圖庫

StockCapy 的卡皮巴拉圖。**原圖**是 `*-source.jpg`（白底），其餘都是由
`tools/generate_mascot_icons.py` 與 `tools/generate_mascot_art.py` 裁切、去背、縮放出來的，
不要手改；換原圖後重跑腳本即可。新增圖片時請同步更新這張表。

> 這些圖是 AI 生成的，正式上架（App Store / Google Play）前請先確認來源工具的商用授權。

## 原圖

| 檔案 | 內容 | 產出 |
|---|---|---|
| `app-icon-source.jpg` | 3D 水豚加金框的 app icon | 手機 icon（`icons/`） |
| `mascot-banner-source.jpg` | 橫式 3D 場景：全身、金幣堆、K 線、紅箭頭 | README 橫幅、登入頁、空狀態裁切 |
| `mascot-flat-source.jpg` | 扁平卡通頭像（咬鈔票、紅領結） | 小圖示、頭像 |
| `mascot-party-source.jpg` | 派對帽，抱著紅心與上升箭頭 | 貼圖 `party` |
| `mascot-thumbsup-source.jpg` | 比讚、眨眼、咬鈔票、頭頂金幣 | 貼圖 `thumbsup` |
| `mascot-bull-source.jpg` | 公牛帽加金色墨鏡，抱著金牛 | 貼圖 `bull` |
| `mascot-winner-source.jpg` | 「必勝 UP! 旺」頭巾，寶箱與紅箭頭 | 貼圖 `winner` |

## 衍生圖與用途

| 圖 | 用在哪 |
|---|---|
| `capy-banner.png` | README 最上方（右側淡出成透明，深淺色模式都能看） |
| `capy-logo.png` | 文件引用的金框 icon（與 `icon-512.png` 相同） |
| `capy-mark.png` | 文件用的小頭像（扁平，透明底） |
| `capy-avatar.png` | Telegram bot 頭像（到 BotFather 上傳，圓形裁切） |
| `stickers/party.png` | README「智慧提醒」；網頁：股利頁沒有已領股利資料、價格提醒或智慧提醒「立即檢查」有新觸發時 |
| `stickers/thumbsup.png` | README「第一次使用」；網頁：沒有持倉／手動持倉、匯入 CSV 成功時 |
| `stickers/bull.png` | README「判斷入手價」；網頁：監控清單是空的 |
| `stickers/winner.png` | README「快速開始」；網頁：入手價頁沒有標的 |

網頁用的圖在 `src/portfolio/web/static/`：

| 圖 | 用在哪 |
|---|---|
| `icons/icon-192.png`、`icon-512.png`、`icon-maskable-512.png`、`apple-touch-icon.png` | PWA／加到主畫面的 icon |
| `favicon.ico`、`icons/favicon-48.png` | 瀏覽器分頁 |
| `icons/capy-mark.png` | 網頁左上角與頁尾的小頭像 |
| `art/login.webp` | API token 登入頁 |
| `art/face.webp` | 智慧提醒：沒有任何股票會收到提醒 |
| `art/chart.webp` | 淨值走勢、歷史收盤價、觸發紀錄的空狀態 |
| `art/sticker-*.webp` | 同上表的四張貼圖（透明底，2 倍解析度） |

## 使用原則

- 成功類的結果框（匯入完成、到價觸發）會在右側放一張小貼圖（`cheerHtml()`），沒有觸發或寫入 0 列時不放。
- 失敗或錯誤訊息（「取不到…」「HTTP 409」）不放吉祥物，笑嘻嘻的圖會誤導。
- 同一個畫面不要重複放同一張；空狀態各分頁分配了不同的圖（股利頁有兩個空狀態，只有第一個放圖）。
- 小尺寸（favicon、左上角、頁尾）用扁平頭像，3D 圖縮小後細節會糊。
