# 進度與待辦

最後更新：2026-11-06

## 現況

可用。Python 測試全過，全部離線執行（行情、通知、匯率、技術指標、回測與 LLM 皆以 stub 或合成序列替代）。

已用真實的 Firstrade 中文匯出檔驗證過完整流程。

## 已完成

### 資料進入
- [x] Firstrade CSV 解析（中文與英文欄名、模糊比對）
- [x] 重複匯入自動去重（row fingerprint）
- [x] DRIP 股利再投資（類別為「其他」、價格藏在說明文字的 `REIN @`）
- [x] 內嵌預扣稅解析（`NON-RES TAX WITHHELD $X` / `NON-QUALIFIED DIVIDEND $X`）
- [x] 成本基礎缺失偵測（從股息說明的「ON N SHS」反推應有股數）
- [x] 手動輸入持倉（覆蓋同代號由 CSV 推導的持倉）

### 估值與入手價
- [x] 四個估值模型 + 適用性閘門
- [x] 成長股模型 PEG、DCF（現金流折現）＋閘門
- [x] 合理價不確定性等級＋浮動安全邊際（使用者明確設定時不加寬）
- [x] 智慧提醒揭露採計模型、不確定性、實際安全邊際、倍數來源；提醒與完整視圖共用同一套假設解析
- [x] ETF 退回 200 日均線並標示依據
- [x] 區分「資料不足」與「無法估值」
- [x] 價格位置（現在 P/E、52 週高低與區間位置、vs 200MA）
- [x] 每檔假設持久化，歷史 P/E 自動推導 + 穩定性閘門 + 30 天快取

### 提醒
- [x] 監控清單（買點 / 賣點）
- [x] 從估值自動推導入手提醒價
- [x] 同日去重、改價自動重新武裝
- [x] macOS 桌面通知（osascript）
- [x] Telegram 管道（**2026-09-17 已在雲端 VM 實測送達**）
- [x] Email 管道（smtplib，程式完成，SMTP 以 stub 測；未實際送達測試——需要 SMTP 設定）
- [x] 雲端用的背景排程器（一次失敗不會讓迴圈死掉；到價提醒 + 智慧提醒都會跑）

### 智慧提醒（技術指標 + 均價規則）
- [x] 技術指標：RSI(14, Wilder)、60/200 日均線、布林(20, 2σ)，純函式離線可測（`indicators.py`）
- [x] 規則引擎：均價偏離 / 均線乖離 / RSI 超賣超買 / 布林上下軌，門檻在 `SignalThresholds`（`signals.py`）
- [x] 假訊號防呆：對照基準指數（SPY）近一週走勢，標注「可能大盤同向」
- [x] 除息/分割：指標用還原股價，均價偏離用原始報價，兩者分開（decisions.md D13）
- [x] 一句話摘要：確定性樣板，複述觸發的規則、帶「非投資建議」，可換 LLM 後端（`advisor.py`，D12）
- [x] 同日去重 + 條件變動重新武裝（`signal_alerts` 表）
- [x] 回測：觸發頻率 + 買點後續報酬（20/60 日勝率與中位數，不看未來）（`backtest.py`）
- [x] 每檔門檻可持久化（`signal_config.py`，比照 assumptions 解析順序）
- [x] 大盤同向可靜音（`PORTFOLIO_MUTE_MARKET_WIDE`）
- [x] 行情歷史快取（`price_history` + `price_fetch_log`，每檔每天一抓，失敗回退舊快取）
- [x] 觸發歷史記錄（`signal_events`）
- [x] 可選 LLM 第二層摘要（`advisor.LLMNarrator`，依賴注入、預設關、失敗回退樣板）
- [x] 通知卡片改版（2026-10-14）：開頭一段白話導言（今日漲跌、52 週位置、vs 200 日均線、持倉損益），
      細節分區塊並加 emoji
- [x] 通知帶 📐 參考合理價與入手價（2026-10-14）：只用通用預設＋該檔手動覆寫、不跑歷史 P/E 推導，
      避免每 30 分鐘的檢查變重，所以數字可能跟「入手價」頁略有差異；算不出來就略過、不擋通知
- [x] CLI `signals check/backtest/set/reset/list/history`
- [x] API：`GET/POST /api/signals`、`/api/signals/thresholds`(GET/PUT/DELETE)、`/api/signals/history`
- [x] 網頁「智慧提醒」分頁（node --check 過，未經視覺驗證）

### 幣別
- [x] 每筆交易標記成交日匯率（`fx_rate` 欄位 + `fx_rates` 快取表）
- [x] 台幣損益歸因：股票漲跌 vs 匯率變動，精確相加
- [x] NT$ 符號（幣別符號集中在 `currency.py`，後端供給前端）

### 台股（D30–D32）
- [x] 代號由 Yahoo 後綴判斷市場，純數字自動補 `.TW`／`.TWO`；收盤時間、大盤基準（`^TWII`）、幣別（NT$）依市場
- [x] 持股以美元內部加總、畫面以台幣為主，並列美股／台股小計；取不到匯率就不給數字（503）
- [x] 台股股利：單筆滿 NT$20,000 扣 2.11%，不估所得稅；合計列依幣別分開
- [x] 匯入：格式註冊表、`generic-us`／`generic-tw`、範本下載、全有或全無的錯誤列號

### 介面與部署
- [x] CLI（`stockcapy` / `sc`，17 主指令 + 8 子指令）
- [x] 網頁（6 分頁，含 CSV 拖放上傳、就地編輯假設）
- [x] ~~Expo Android app~~ **已移除（2026-11-06，D25）**：手機改用網頁的 PWA 外殼
- [x] API token 認證（fail-closed）
- [x] iPhone 用 PWA 外殼（2026-10-27，D24）：可加到主畫面、獨立視窗開啟；無 service worker，故無離線與推播
- [x] Dockerfile + `compose.yaml` + fly.toml（Docker 映像 2026-11-06 實測通過；Fly.io 部署未實測）
- [x] 公開分享準備（2026-11-06）：MIT LICENSE、GitHub Actions 跑 pytest、README 快速開始與免責聲明

## 待辦（需要使用者提供資料）

- [x] ~~**匯出區間之前買進的持倉缺口**~~ **已修（2026-09-18）**：買進紀錄早於 CSV 匯出區間時，從股息文字
      「CASH DIV ON N SHS」反推實際股數，以「股數已知、成本未知」納入持倉——市值、權重、產業曝險立即正確，
      需要成本的數字（報酬率、未實現損益、台幣歸因）標為未知並說明原因。補上買入價（匯出更長區間或 `sc add-position`）才會有報酬率。
- [ ] **NVDA 的 target P/E**。歷史 P/E 中位數 73.8（四分位距 46–134）被穩定性閘門正確擋掉，
      目前退回通用預設 18.0。可用的透明基準只有「目前 trailing P/E 26.8」或「forward P/E 13.7」，
      兩者都不是使用者自己的判斷。因 Target P/E 模型乘的是 forward EPS（15.61），填 18 得 $281、
      填 26.8 得 $418，模型對 NVDA 本質脆弱 —— 需使用者自己決定，不代填（見設計原則第 4 條）。
- [x] ~~**Telegram bot token 與 chat id**~~ **已完成（2026-09-17）**：填在雲端 VM 的 `~/stockcapy/.env`，
      `notify-test` 與真實 `signals check` 都已送達手機。
- [ ] **現金餘額**。Firstrade 無 API，`sc snapshot --cash-balance <金額>` 手動給

## 待辦（需要使用者決定）

- [ ] **幣別 toggle**。`show` / 持倉分頁兩種幣別都算好了，加 toggle 很快；但要全站一致得把匯率接到 `targets` 與 `dividends`
- [ ] **股利的真實台幣收入**。股息列也有入帳當天的匯率，所以「實際領到多少台幣」是真實數字而非估值。評估價值高於 toggle
- [ ] **集中度警示**。單一產業或單檔權重過高時，數字只靜靜躺在表格裡，沒有主動提醒
- [x] ~~launchd 定時任務~~ **不再需要**：雲端已由 `PORTFOLIO_CHECK_INTERVAL_MINUTES=30` 接手排程。
      本機若仍想跑，README 有 plist 範本。
- [x] ~~雲端尚未部署~~ **已部署（2026-09-17）**：GCE `e2-micro` / us-central1-a，
      以 `<VM_IP>.sslip.io` 對外（Caddy + Let's Encrypt，8765 不對外）。
      systemd 開機自啟、app 內建排程每 30 分鐘檢查。步驟與踩過的坑見 [deploy-gce.md](deploy-gce.md)。
      `fly.toml` 留著當備選。

## 待辦（工程）

- [ ] **智慧提醒的網頁分頁未經視覺驗證**（HTML node --check 過）。
- [x] ~~**`sc snapshot --cash-balance` 會被每日 timer 蓋回 0**~~ **已修（2026-09-17）**：改為 `float | None`，
      省略時沿用該日已存的現金餘額（`repository.load_cash_balance_on` 回傳 `None` 以區分「當日尚無快照」
      與「真的沒現金」）。5 個回歸測試，關鍵那個已驗證在舊行為下會紅。
- [ ] **行情抓取在這台 VM 上會間歇性失敗**（2026-10-13 發現）。**失敗後不會自我修復**的部分
      已修（D23：快取閘門改看內容），**為什麼會失敗**仍未查明，疑似 yfinance 對 e2-micro 的
      共用 IP 限流。失敗期間仍等於沒有訊號可用（但現在看得見，見 D21）。
      可考慮：重試退避、連續失敗 N 次就通知、或換資料來源。
- [x] ~~**長時間運行的伺服器報價會凍結**~~ **已修（2026-11-06，待部署到 VM）**：`MarketDataClient`
      把每檔的 yfinance `Ticker` 永久快取，而 yfinance 會把 `fast_info` 的現價／前收存在物件上不再更新，
      所以服務不重啟，報價、今日漲跌、52 週位置、200 日均線、匯率、基本面就一直停在第一次抓的值。
      實例：11/06 通知寫 NVDA $224.52，實際收盤 $181.60（指標區塊的收盤價是對的，兩者自相矛盾）。
      改為每檔 Ticker 5 分鐘過期（`TICKER_CACHE_TTL_SECONDS`），`tests/test_market_client_cache.py` 3 個測試。
- [x] **同樣條件每天重複通知** **已修（2026-11-06，D26）**：改為條件改變、再走 5%、或滿 7 天才重發。
- [x] **訊息格式只有一種** **已完成（2026-11-06，D27）**：compact / plain / full，可分管道設定、可隱藏持倉。
- [x] **公開分享前的個資清理（2026-11-06，D28）**：原始筆記移到 gitignored 的 `docs/private/`，文件改佔位符、測試改合成數字。
- [x] ~~**建立公開 repo**~~ **已完成（2026-11-06）**：github.com/alankowabunga/StockCapy；VM 改從公開 repo 部署。
- [x] **台股支援三階段完成（2026-11-06，D30–D32）**：監控／提醒／入手價、持股與總資產（台幣為主、美元內部加總）、股利（2.11% 補充保費）、匯入分美股／台股兩區與通用格式。細節與接手說明見 [taiwan-stocks-plan.md](taiwan-stocks-plan.md)。
- [ ] **台股券商專屬匯入格式**：等使用者提供去識別化的匯出範例（目前用 `generic-tw` 通用格式）。
- [ ] **台股端到端實測**：用一檔真實台股代號、真資料在私人環境跑一遍（需連 yfinance；不進公開 repo）。
- [x] **智慧提醒可自選通知對象（2026-11-06，D29）**：預設全收，`signals on/off` 與網頁「通知對象」逐檔關閉或額外加入。
- [ ] **通知的「報價時間」寫死為「來源未提供」**。可改顯示 yfinance 的 `regularMarketTime`，
      報價若再過期，從通知本身就看得出來。
- [ ] **回測不評估賣方訊號**：`ForwardReturnStats` 只量測 BUY 進場點，故 RSI 超買 / 突破上軌的門檻
      無實證支撐（見 D19 已知落差 2）。
- [ ] **布林通道寬度不可調**。`indicators.BOLLINGER_STDEV = 2.0` 寫死，`SignalThresholds` 沒有對應欄位。
      D19 校準後 VTI 剩下的觸發幾乎全來自布林下軌，而 D19 已證實 VTI 的訊號低於隨機進場基線 ——
      也就是這部分雜訊目前無法用門檻降低。要嘛把 σ 納入 SignalThresholds，要嘛讓使用者決定是否
      把 VTI 留在監控清單。
- [ ] **沒有總覽分頁**。七個分頁是 入手價/持倉/風險/股利/監控/智慧提醒/資料。報酬歸因圖因此放在
      持倉分頁既有的 TWD 歸因面板內；若要獨立總覽頁需改 `TABS` / `LOADERS` / nav。
- [ ] **Email / LLM 未實際送達或呼叫**。程式與 stub 測試完成；email 需填 `.env` 跑 `sc notify-test`，
      LLM 需 `.[llm]` extra + `ANTHROPIC_API_KEY` 才會真的呼叫（否則自動用樣板）。Telegram 已於 2026-09-17 實測送達。
- [ ] **網頁沒有前端單元測試**（見 decisions.md D9，已知例外）
- [x] ~~**Docker build 未實測**~~ **已實測（2026-11-06）**：建置、401 擋未授權、帶 token 匯入範例 CSV、網頁與 manifest 皆正常

## 已知限制

| 項目 | 說明 |
|---|---|
| 行情源 | yfinance 非官方介面，會暫時失敗；免費源延遲約 15 分鐘；伺服器內報價最多再舊 5 分鐘（Ticker 快取期限） |
| 成本基礎 | 移動平均法，與券商報稅用的 FIFO 不同，**不是報稅依據** |
| 風險指標 | 「目前持倉」回溯計算，不是歷史實際交易績效（需累積每日快照） |
| 歷史 P/E | 僅 4 個會計年度、年度顆粒度，且用 trailing EPS，對高成長股偏高 |
| 匯率 | Yahoo 參考匯率，非複委託實際成交匯率（券商有買賣價差） |
| ETF 配息 | yfinance 不提供 ETF 的 `dividendRate`，用近 12 個月實配推算 |
| 桌面通知 | 僅 macOS。要推到手機需設定 Telegram 或 email |
| 智慧提醒 | 機械規則觸發，門檻要自己回測校準；摘要為確定性樣板、不含買賣建議；日線顆粒度，非盤中即時 |
| 網頁安全 | 只綁 127.0.0.1 時無認證；對外必須設 token（fail-closed 會強制） |

## 不打算做

- 投資建議（見 product.md 原則 1）
- 交易執行
- 報稅用的 FIFO 稅務批次
- 即時報價
