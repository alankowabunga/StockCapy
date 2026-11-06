# 台股支援：計畫與架構設計

> 狀態：**暫停中**（2026-11-06）。本文件給下一位接手的人（或 agent）：已確認的決策、架構設計、做到哪、怎麼接著做。
> 做完一個階段就更新本文件的「進度」，全部完成後把決策搬進 [decisions.md](decisions.md)（預留 D30–D32），本文件可刪除。

## 目標

StockCapy 原本只處理美股。目標是讓台股（上市 `.TW`、上櫃 `.TWO`）也能：

1. 被監控、收到到價提醒與智慧提醒、看入手價（第 1 階段）
2. 算進持股、總資產、股利（第 2 階段）
3. 從台股券商匯入交易紀錄，匯入區塊分「美股券商」「台股券商」兩區（第 3 階段）

**資料來源已驗證**（2026-11-06 實際查詢 yfinance）：`2330.TW`、`6488.TWO` 有台幣價格、EPS、配息、淨值、
分析師目標價與 4 年年度 EPS；`0050.TW` 是 ETF（無 EPS，會走 200 日均線）；`^TWII` 可當大盤基準。

## 已確認的決策（使用者 2026-11-06 確認）

| # | 題目 | 決定 |
|---|---|---|
| 1 | 台股代號輸入 | 打 `2330` 自動補成 `.TW` 或 `.TWO`（先試 `.TW`，查得到價格就用；否則試 `.TWO`；都沒有就報錯） |
| 2 | 總資產顯示 | 有台股時以**台幣**為主，另列「美股小計（USD）」「台股小計（TWD）」；**內部快照仍存美元**，歷史曲線不斷 |
| 3 | 台股股利 | 不扣美股 30% 預扣稅；單筆 ≥ NT$20,000 扣二代健保補充保費 2.11%；**不估所得稅**，畫面註明 |
| 4 | 台股券商格式 | 使用者尚未指定券商 → 第 3 階段先做「通用格式」與分區版面；個別券商等使用者提供匯出範例 |
| — | 執行順序 | 第 1 → 2 → 3 階段，各自測試、commit |
| — | 部署 | **每次部署到 VM 前都要再問使用者**（全域規則：確認設計不等於授權部署） |

全域規則提醒：第 1 階段改了核心判斷（資料新鮮度、大盤比較），第 2 階段動到金額計算，都已經過上表確認；
**實作中若要改變上表任何一項，先停下來提差異給使用者確認。**

## 架構設計

### 核心原則：市場由代號後綴決定

- 新模組 [`src/portfolio/markets.py`](../src/portfolio/markets.py)：`Market`（code、label、currency、close_utc）、
  `US_MARKET`、`TW_MARKET`、`market_for_symbol()`、`is_taiwan_symbol()`、`currency_for_symbol()`。
- `.TW` / `.TWO` 結尾 = 台股（TWD），其他一律視為美股（USD）。
- **不在資料庫存幣別**：幣別永遠從代號推得，所以第 1、2 階段都不需要改資料表結構。

### 第 1 階段：監控與提醒

| 項目 | 設計 | 位置 |
|---|---|---|
| 資料新鮮度 | `is_close_series_current(last_close_day, now_utc, market)`；收盤時間依市場：美股 21:00 UTC、台股 05:30 UTC，再加 3 小時發布緩衝 | `market_calendar.py` |
| 大盤基準 | 美股用 `PORTFOLIO_BENCHMARK_SYMBOL`（SPY）；台股用 `PORTFOLIO_TW_BENCHMARK_SYMBOL`（預設 `^TWII`）。一次掃描對每個出現的市場各算一次 `market_context`，每檔用自己市場的 | `service.benchmark_symbol_for` / `market_context` / `_resolve_tuning_for`、`config.Settings.tw_benchmark_symbol` |
| 金額顯示 | `currency.format_money(amount, currency)` → `$181.60` / `NT$2,180.00`；`currency.labeled_money()` 讓美股維持既有的 `USD $` 寫法 | `currency.py`；呼叫端 `advisor.py`、`renotify.py`、`alerts.py`、`cli.py`、`report.py`、網頁 `money()` |
| 代號輸入 | `markets.resolve_symbol_input(raw, has_price)`；`service.resolve_symbol(client, raw)` 包一層用報價判斷存在 | 寫入點：CLI `add-position`、`watch add`、`signals on/off`；API `POST /api/watchlist`、`POST /api/positions/manual`、`PUT /api/signals/subscriptions/{symbol}` |
| 錯誤 | `markets.InvalidSymbolError`（`signal_subscriptions` 轉出口相容）；CLI 印紅字結束、API 回 400 | |

**相容性要求：只有美股的使用者，所有輸出必須和改動前逐字相同**（測試要守住這條）。

### 第 2 階段：台股持股（尚未開始）

| 項目 | 設計 |
|---|---|
| 持股幣別 | 由代號推得；手動輸入台股時，均價就填台幣（UI 提示文字要寫清楚） |
| 加總 | 每檔先算原幣市值；台股用當下 USDTWD 換成美元做內部加總（權重、產業曝險、風險指標、每日快照都用這個）。顯示：有台股時主卡片是台幣合計，再列兩個市場小計 |
| 每日快照 | `account_snapshots` 繼續存美元，與既有歷史一致 |
| 匯率損益歸因 | 只對美股做「股票漲跌＋匯率變動」拆解；台股成本本來就是台幣（`total_cost_home = total_cost`），只有股票漲跌 |
| 股利 | 依市場：美股維持 `PORTFOLIO_NRA_WITHHOLDING`；台股單筆 ≥ 20,000 扣 2.11%，其餘不扣；註明「所得稅依個人申報方式，未計入」 |
| 風險指標 | 用各檔原幣日報酬率（不含匯率波動），在畫面或文件註明 |
| 回退風險 | 已輸入台股持股後退回舊版，舊程式會把台股當美元算 → 退回前先刪台股持股（寫進 decisions） |

要動的檔案（預估）：`holdings.py`、`performance.py`、`service.load_performance` / `load_positions`、`dividends.py`、
`currency.py`、`report.py`、`web/serializers.py`、網頁「持倉」「股利」分頁。

### 第 3 階段：匯入分區＋通用格式（尚未開始，見下方「接手注意」）

| 項目 | 設計 |
|---|---|
| 格式註冊表 | 新模組 `importers.py`：每個格式有 key、market（US/TW）、中文名稱、parser、匯出說明。先註冊 `firstrade`（US，既有）、`generic-us`、`generic-tw` |
| 通用格式欄位 | `date/日期, symbol/代號, action/動作, quantity/股數, price/價格, fee/手續費, tax/稅, note/備註`；動作接受 BUY/SELL/DIVIDEND 與 買進/賣出/股利/配息；日期 `YYYY-MM-DD` 或 `YYYY/MM/DD` |
| 欄位對應 | fee → commission；SELL 的 tax（證交稅）→ fee；DIVIDEND 的 tax → withheld_amount；DIVIDEND 的 price = 股利總額，quantity 可留空（實作時對照 `holdings.py` 確認成本與賣出所得的算法再定案） |
| 台股代號 | 通用格式要求完整代號（`2330.TW`），**純數字代號直接報錯**，不在匯入時猜上市或上櫃 |
| 去重 | 沿用既有 row fingerprint，同一檔重匯不會重複 |
| 範本 | `src/portfolio/web/static/templates/generic-us.csv`、`generic-tw.csv`（合成範例資料），靜態檔公開下載 |
| 介面 | API `POST /api/import?format=<key>`（預設 firstrade）、`GET /api/import/formats`；CLI `stockcapy import <csv> --format <key>`；網頁「資料」分頁分「美股券商」「台股券商」兩區，各有格式下拉、匯出說明、範本下載、上傳區 |
| 先選格式再上傳 | 不做自動判斷格式，避免誤讀 |

## 進度（暫停當下，2026-11-06）

### 已 commit（本機 `develop`，**尚未 push、尚未部署**）

- `0b70766` 智慧提醒可自選通知對象（D29）

### 第 1 階段：完成約一半，**未 commit**（主工作目錄）

已完成：

- [x] `markets.py`（市場判斷、`resolve_symbol_input`、`InvalidSymbolError`）
- [x] `market_calendar.py` 依市場判斷新鮮度
- [x] 大盤基準依市場（`config.tw_benchmark_symbol`、`service.benchmark_symbol_for`）
- [x] `currency.format_money` / `labeled_money`；`advisor.py`、`renotify.py`、`alerts.py` 改用
- [x] 代號解析接到 CLI 與 API 的寫入點（見上表）
- [x] `tests/test_api.py` 一處預期值更新（API 多回傳 `symbol`）；全部 492 個測試通過

未完成：

- [ ] `cli.py` 還有 6 行寫死 `$`（交易列表、snapshot、history、watch auto 輸出）
- [ ] `report.py` 還有 19 處寫死 `$`（估值明細、股利表、入手價表的 `_money`）；美股照舊、台股改 `NT$`
- [ ] 網頁 `money(value)`（`index.html` 約 40 處）改成依代號或幣別顯示；有代號的列傳入代號，總計類留到第 2 階段
- [ ] 新測試：`markets`（後綴判斷、代號解析成功／失敗／不連網時報錯）、`market_calendar` 台股收盤時間、
      台股訊號用 `^TWII` 當基準、台股金額顯示 `NT$`、**美股輸出逐字不變**
- [ ] `.env.example` 補 `PORTFOLIO_TW_BENCHMARK_SYMBOL`；README（指令一覽、監控、智慧提醒處說明台股代號）
- [ ] decisions.md 寫 **D30**（市場由後綴決定、不存幣別、依市場的收盤與基準、代號自動補後綴）
- [ ] 用暫存資料夾＋測試 token 開網頁實測（做法見下方「驗證方式」），commit

### 第 2 階段：未開始

### 第 3 階段：只開了頭

- 曾交給一個背景 agent，已停止。它的 worktree 在 `.claude/worktrees/agent-a0743cb7e3eee7163`，
  只改了 `firstrade_csv.py` 兩行，**沒有 commit**。

## 接手注意

1. **第 3 階段請從最新的 `develop` 重開**，不要沿用上面那個 worktree：它是從公開 repo 的第一個 commit（`eecddfc`）開出來的，
   缺少之後的 README 更新與 D29。確認沒有要留的東西後，用 `git worktree remove` 清掉它。
2. **`.claude/worktrees/` 要加進 `.gitignore`**，目前在主目錄顯示為未追蹤，避免誤 commit。
3. **這是公開 repo**：測試與範例只用合成數字；帳號、IP、真實持股只能放 gitignored 的 `docs/private/`。commit 身分是 GitHub noreply 地址（repo 層級已設定）。
4. **決策編號**：D29 已用；預留 D30（第 1 階段）、D31（第 2 階段）、D32（第 3 階段）。
5. **寫程式規範**：method 約 20 行內、參數 ≤ 4、命名完整、註解只寫「為什麼」且 ≤ 3 行、新功能必附單元測試。
6. **部署**：VM 從公開 repo 的 `main` 拉程式，指令在本機的 `docs/private/deploy-gce.md`。部署前要先問使用者、先備份資料庫。

## 驗證方式

- 測試：`.venv/bin/python -m pytest -q`（全部離線）。
- 網頁實測**不要用本機真實資料與 token**：在 `.claude/launch.json` 暫時加一個 configuration，用 `env` 帶入
  `PORTFOLIO_DATA_DIR=<暫存資料夾>`、`PORTFOLIO_API_TOKEN=<臨時測試 token>`、把 `PORTFOLIO_TELEGRAM_*` 設為空字串、
  `PORTFOLIO_DESKTOP_NOTIFICATIONS=0`，換一個 port 啟動；先用 CLI 對暫存資料夾匯入 `examples/` 的範例檔。
  驗完把 launch.json 還原。
- 台股建議實測：監控清單加 `2330`（應存成 `2330.TW`）、加 `6488`（應存成 `6488.TWO`）、通知對象加 `0050`；
  智慧提醒卡片金額顯示 `NT$`、大盤背景顯示 `^TWII`。
