# StockCapy 🐹

> A self-hosted US-stock tool: fair-value estimates, buy-below prices, rule-based price alerts
> pushed to Telegram/email, and TWD (NT$) return attribution. Docs are in Traditional Chinese.
> MIT licensed. **Not investment advice.**

**美股合理價、入手價與到價提醒。淡定等到價。**

自己架的美股持股工具。把券商交易紀錄匯進來（或手動輸入持股），它會：

- 算出每檔的**參考合理價與入手價**，以及現價離入手價還差多少
- 價格到了、或你設的技術指標規則被觸發時，**推播到 Telegram / Email**
- 把台幣損益拆成「股票漲跌」和「匯率變動」兩塊
- 列出股利（含 30% 預扣稅）、風險指標與每日淨值

有網頁（手機可加到主畫面，像一支 app）也有 CLI。資料存在你自己的機器上（SQLite），
對外只連行情來源（Yahoo）與你啟用的通知管道。

> **免責聲明**：本工具只輸出機械模型與你自設規則的結果，**不是投資建議**，數字可能有誤或過時，
> 決策與風險由你自行承擔。行情來自 [yfinance](https://github.com/ranaroussi/yfinance)（非官方 Yahoo 介面），
> 使用前請自行確認並遵守 Yahoo 的使用條款，僅供個人用途。

## 快速開始（Docker，約 5 分鐘）

需要先裝好 [Docker](https://docs.docker.com/get-docker/)（含 Docker Compose）。

**1. 下載並建立設定檔**

```bash
git clone https://github.com/alankowabunga/StockCapy.git stockcapy && cd stockcapy
cp .env.example .env
```

**2. 產生登入用的 token**（第一次會花幾分鐘建置映像）

```bash
docker compose run --rm stockcapy stockcapy token
```

它會印出一行 `PORTFOLIO_API_TOKEN=xxxx`。打開 `.env`，把原本空白的 `PORTFOLIO_API_TOKEN=` 那行換成它。
這串字就是登入網頁的密碼，請存進密碼管理器。

**3. 啟動**

```bash
docker compose up -d
```

**4. 開 <http://localhost:8765>**，貼上 token 登入。

資料存在 `./data/`（已在 `.gitignore`，不會進版控）。改了 `.env` 之後要再跑一次 `docker compose up -d` 才會生效。

## 第一次使用：四個步驟

1. **放進持股**：到「資料」分頁上傳券商匯出的 CSV（目前支援 Firstrade，見[怎麼拿 Firstrade 的資料](#怎麼拿-firstrade-的資料)），
   或在「手動持倉」輸入代號、股數、均價。想先看效果，可以上傳範例檔
   [`examples/sample_firstrade_history.csv`](examples/sample_firstrade_history.csv)（假資料）。
2. **看入手價**：「入手價」分頁列出每檔的參考合理價、入手價、現價還差幾 %。點一列可以看是哪些估值模型算出來的、為什麼。
3. **設提醒**：在「監控」分頁設定買進／賣出提醒價（或用 CLI `stockcapy watch auto` 依入手價一次設好所有持股）。
   「智慧提醒」不用設定，會自動看你的持股與監控清單。
4. **接上手機通知**：照[Telegram 推播](#telegram-推播)取得 bot token 和 chat id 填進 `.env`，然後：

   ```bash
   docker compose up -d                                       # 讓新設定生效
   docker compose exec stockcapy stockcapy notify-test        # 手機應該收到一則測試訊息
   ```

之後內建排程每 30 分鐘檢查一次，到價或規則觸發就推播。想放到雲端讓手機隨時連得到，
見 [docs/deploy-gce.md](docs/deploy-gce.md)（GCP 免費額度，月費 $0）。

## 不用 Docker：本機安裝

需要 Python 3.12 與 [uv](https://docs.astral.sh/uv/)。

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env
.venv/bin/stockcapy serve        # 開 http://127.0.0.1:8765
```

只在本機（`127.0.0.1`）使用時不需要 token。要讓手機或其他電腦連進來，見[手機存取與安全性](#手機存取與安全性)。

## 指令一覽

網頁做得到的事 CLI 都做得到，兩者共用同一個資料庫。下文的 `stockcapy <指令>`：

- **Docker**：`docker compose exec stockcapy stockcapy <指令>`
- **本機安裝**：`.venv/bin/stockcapy <指令>`（或先 `source .venv/bin/activate`）
- `sc` 是 `stockcapy` 的簡寫

| 指令 | 回答的問題 |
|---|---|
| **`stockcapy targets`** | **合理價、建議入手價、與我的均價對照、價格在 52 週區間的位置** |
| **`stockcapy watch auto`** | **用估值模型自動為每檔持倉設定入手提醒價** |
| **`stockcapy check`** | **比對現價與提醒價，到價就發通知（桌面 / Telegram / email）** |
| **`stockcapy signals check`** | **技術指標 + 均價規則觸發時，推播分層現況摘要與觸發依據（非投資建議）** |
| **`stockcapy signals backtest <SYM>`** | **用歷史資料回放規則，看觸發頻率是否合理（上線前先跑）** |
| `stockcapy watch add/list/remove` | 手動管理監控清單與提醒價位 |
| `stockcapy value <SYM>` | 單檔的完整估值模型明細與假設 |
| `stockcapy add-position` | 手動輸入持倉（沒有 CSV 或匯出檔不完整時） |
| `stockcapy import <csv>` | 匯入券商交易紀錄（可重複匯入，自動去重） |
| `stockcapy show` | 現價、我的平均成本、未實現損益、權重、產業曝險、**台幣損益歸因** |
| `stockcapy risk` | 年化波動度、最大回撤、Sharpe、與 SPY 相關性 |
| `stockcapy dividends` | 已領股利（含 30% 預扣稅）與未來一年配息預估 |
| `stockcapy snapshot` / `history` | 每日淨值快照與歷史曲線 |
| `stockcapy unclassified` | 檢查有哪些 CSV 列沒被正確分類 |
| **`stockcapy serve`** | **啟動網頁介面與 API（手機加到主畫面也是連這裡）** |
| `stockcapy token` | 產生 API token（手機與遠端連線必需） |
| `stockcapy notify-test` | 測試所有已啟用的通知管道 |
| **`stockcapy fx-sync`** | **補上每筆交易的成交日匯率（台幣報酬率用）** |
| `stockcapy assume set/default/reset/list` | 管理每檔的估值假設 |

## 網頁介面

```bash
stockcapy serve
```

開 http://127.0.0.1:8765（Docker 是 http://localhost:8765）。七個分頁：

- **入手價**：主表格，點一列展開估值模型明細（哪些採計、哪些不適用、為什麼）與價格位置 bar
- **持倉**：總覽卡片、持倉明細、產業曝險、淨值走勢
- **風險**、**股利**：與 CLI 相同的內容
- **監控**：管理提醒價位、一鍵「立即檢查」
- **智慧提醒**：目前觸發的規則、每檔門檻編輯、觸發歷史
- **資料**：CSV 拖放上傳、手動持倉新增/刪除、**估值假設就地編輯**

前端是單一 HTML 檔（`src/portfolio/web/static/index.html`），無 build step、無 npm、無外部 CDN。

## 手機存取與安全性

這套工具有兩個前端，共用同一個 API 與同一份資料：**CLI** 與 **網頁**。手機用瀏覽器開網頁後「加到主畫面」（PWA，iPhone 與 Android 皆可），就像一支 app。

### 先產生 API token

沒有 token 時，`serve` **只允許綁 `127.0.0.1`**；一旦你想綁其他位址（讓手機連進來），
它會拒絕啟動。這是刻意的 fail-closed——持倉、成本、帳戶淨值不能裸奔。

```bash
stockcapy token --write-env     # 產生並寫進 .env（Docker 的做法見「快速開始」第 2 步）
```

之後：

- **網頁**：第一次開會要你貼 token，存成 httpOnly cookie
- **手機**：同網頁，加到主畫面後開啟、第一次貼 token
- **API**：帶 `Authorization: Bearer <token>`

### 同網段使用（最簡單）

```bash
stockcapy serve --host 0.0.0.0
```

手機連同一個 Wi-Fi，開 `http://<電腦的區網 IP>:8765`。**電腦要開著、手機要在同一個網段。**
Docker 版預設就對區網開放 8765，不用另外設定。

### 部署到雲端（手機隨時隨地可用）

推薦照 [docs/deploy-gce.md](docs/deploy-gce.md) 放到 GCP always-free 的 e2-micro（月費 $0，已實際運行）。
另備有 [fly.toml](fly.toml)（Fly.io，東京節點），但未實測：

```bash
fly launch --no-deploy          # 產生 app 名稱，改 fly.toml 的 app =
fly volumes create portfolio_data --size 1 --region nrt
fly secrets set PORTFOLIO_API_TOKEN=<你的token> \
  PORTFOLIO_TELEGRAM_BOT_TOKEN=<bot token> \
  PORTFOLIO_TELEGRAM_CHAT_ID=<chat id>
fly deploy
```

SQLite 放在掛載的 volume（`/data`），重新部署不會掉資料。
容器裡 `PORTFOLIO_CHECK_INTERVAL_MINUTES=30` 會啟動 app 內建排程定時檢查價格——
雲端沒有 launchd，所以改由 app 自己排。

> Docker 映像已於 2026-11-06 實測：建置、無 token 時 API 回 401、帶 token 匯入範例 CSV、網頁與
> PWA manifest 都正常。Fly.io 的部署本身尚未實測。

## Telegram 推播

推到手機最簡單的方式。設定一次約 3 分鐘：

1. 跟 [@BotFather](https://t.me/BotFather) 說 `/newbot`，取得 bot token
2. 跟你的新 bot 說任意一句話
3. 開 `https://api.telegram.org/bot<TOKEN>/getUpdates`，抄 `result[0].message.chat.id`
4. 兩個值填進 `.env` 的 `PORTFOLIO_TELEGRAM_BOT_TOKEN` 與 `PORTFOLIO_TELEGRAM_CHAT_ID`
5. 驗證（Docker 先跑 `docker compose up -d` 讓設定生效）：

```bash
stockcapy notify-test
```

兩個值都要填才會啟用。通知會同時送到所有已啟用的管道，任一成功就算送達，
失敗的管道會列出來。訊息長短可以調，見[智慧提醒](#智慧提醒規則引擎--一句話摘要)的「訊息格式」。

### Email（第三條管道）

要推到 email 就填 `.env` 的 `PORTFOLIO_SMTP_*` 與 `PORTFOLIO_EMAIL_*`（見 [.env.example](.env.example)）：
`host + sender + recipient` 三個都填才會啟用，走標準庫 `smtplib`，密碼只放 `.env`、不進程式。
到價提醒與智慧提醒都會送到所有已啟用的管道（桌面 / Telegram / email）。

## 判斷入手價（主要用途）

```bash
stockcapy targets                              # 用預設假設看所有持倉
stockcapy targets MSFT NVDA --target-pe 28     # 指定標的與貼近現實的倍數
```

輸出兩張表。第一張是**入手價對照**：現價、你的均價、參考價、入手價、還差幾 %、依據。
第二張是**價格位置**：現在 P/E、52 週高低、區間位置、與 200 日均線的差距
——這張刻意與內在價值分開，它只講「這價格在它自己近期歷史的哪裡」。

每檔會自動採用它自己的假設（見上節），`--target-pe` 等旗標是**臨時覆寫**，不寫入資料庫。
第二張表的「假設來源」欄會標明這檔用的是 `歷史P/E` / `手動` / `預設` / `命令列`。

### 到價通知

```bash
stockcapy watch auto --target-pe 28 --margin-of-safety 0.15   # 自動設定所有持倉
stockcapy watch add TSM --buy-below 180 --sell-above 280       # 手動加一檔（不必持有）
stockcapy watch list                                           # 看目前距離
stockcapy check                                                # 到價就發桌面通知
```

`check` 同一檔同一天只通知一次（避免轟炸）；改了目標價會自動重新武裝。
`--resend` 可強制重送。

Docker 與雲端部署有內建排程（`PORTFOLIO_CHECK_INTERVAL_MINUTES`，預設 30 分鐘），不用另外設定。
**只有在 Mac 本機、不用 Docker 時**才需要用 launchd 排程（把 `/path/to/stockcapy` 換成你的 clone 位置）：

```bash
cat > ~/Library/LaunchAgents/com.stockcapy.check.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockcapy.check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/stockcapy/.venv/bin/stockcapy</string>
    <string>check</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardErrorPath</key><string>/tmp/stockcapy-check.err</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.stockcapy.check.plist
```

## 估值假設會被記住

`--target-pe` 這個假設主導整個結論，所以它現在**每檔分開儲存**，不必每次重打。

解析順序（後者覆蓋前者）：

1. **通用預設**（`stockcapy assume default`，內建 18）
2. **該檔自己的歷史 P/E 中位數** —— 用近 5 年每日收盤價 ÷ 當時「已公布」的年度稀釋 EPS 算出分布取中位數
3. **你的手動覆寫**（`stockcapy assume set`）

```bash
stockcapy assume list                      # 看每檔實際用了什麼、來源是哪裡
stockcapy assume set NVDA --target-pe 26   # 只改想改的欄位，其餘保留
stockcapy assume reset NVDA                # 回到歷史 P/E 或預設
stockcapy assume default --target-pe 20    # 改通用預設
```

**歷史 P/E 會被主動拒用**：若四分位距 > 中位數的 0.8 倍，代表 EPS 變動太劇烈、trailing P/E
已失去意義，就退回預設而不是拿一個假精確的數字用。實例：

| | 現在 P/E | 歷史中位數 | 四分位距 | 採用 |
|---|---|---|---|---|
| MSFT | 28.2 | 35.3 | 32.5–37.8（窄） | ✅ 歷史 35.3 |
| KO | 25.0 | 25.4 | 24.2–27.1（窄） | ✅ 歷史 25.4 |
| NVDA | 26.7 | 73.9 | 46.0–134.3（極寬） | ❌ 退回預設 18 |
| VTI | — | 無 EPS | — | ❌ 退回預設 |

歷史 P/E 結果快取 30 天（年度 EPS 一年只變幾次）。

**已知限制**：只有 4 個會計年度的年度 EPS（yfinance 上限），所以是年度顆粒度而非逐季；
且用 trailing EPS，對高成長股會系統性偏高——這正是 NVDA 被拒用的原因。

## 合理價評估怎麼看

`stockcapy value` / `targets` 同時跑四個模型，並**自動排除不適用的模型**：

- **Graham Number** — 只在 P/E ≤ 25 且淨值為正時採計（對輕資產成長股會系統性低估）
- **Target P/E** — 用你設定的倍數，結論等於你的假設
- **Gordon Growth (DDM)** — 只在殖利率 ≥ 2% 時採計
- **Analyst Consensus** — 賣方平均目標價，僅列作對照，**永不納入中位數**

```bash
stockcapy value KO --target-pe 20 --required-return 0.08 --margin-of-safety 0.25
```

只有一個模型適用時（成長股常見），輸出會明確警告「結論等同於你的假設，脆弱度高」。

**ETF 沒有 EPS 或淨值，四個模型全不適用。** 這時 `targets` 改用
**200 日均線**當入手錨點，並在「依據」欄標成 `200MA`。
那是趨勢均值回歸的參考，不是內在價值——兩者不會被混在一起。

**這些是機械模型輸出，不是投資建議，也不是「正確價格」。** 模型互相不同意時，
代表這檔股票的價值對假設極度敏感 —— 那件事本身就是最有用的資訊。

## 智慧提醒（規則引擎 + 一句話摘要）

到價提醒只看單一價位。**智慧提醒**多疊一層：定期抓還原股價、算技術指標，結合你的均價，
用你自己設定的規則判斷「現在偏低 / 偏高」，觸發時推播一句話現況摘要。

分層疊加（每一層可獨立驗證）：

```
到價提醒（既有，看單一價位）
      +
規則引擎：抓還原股價 → 算 RSI / 均線乖離 / 布林 / 均價偏離
      +
分層摘要：白話摘要 + 價格來源與日期 + 實際門檻 + 相較上次通知的條件變化
      +
通知管道：桌面 / Telegram / email
```

四條規則（門檻可在 `signals.py` 的 `SignalThresholds` 調）：

| 規則 | 預設門檻 | 依據 |
|---|---|---|
| **均價偏離** | 現價 ≤ 均價 −12% | 你的成本，只有持倉才有 |
| **均線乖離** | 現價 ≤ 60 日均線 −10% | 趨勢均值回歸 |
| **RSI 超賣** | RSI(14) < 30 | 動能 |
| **布林下軌** | 現價跌破 20 日 2σ 下軌 | 相對便宜 |

賣側對稱（高於均線 +10%、RSI > 70、突破上軌）也會觸發，標成「偏高」。

```bash
stockcapy signals check                 # 看持倉+監控清單目前觸發哪些規則，並推播
stockcapy signals check NVDA MSFT       # 只看指定標的
stockcapy signals backtest NVDA --years 3   # 上線前先回測：觸發頻率 + 後續報酬
stockcapy signals set NVDA --rsi-oversold 25   # 每檔門檻可調可存（* 改通用預設）
stockcapy signals list                  # 看每檔實際採用的門檻與來源
stockcapy signals history               # 過去實際觸發並通知過的紀錄
```

`full` 格式每檔先顯示摘要，再列出價格、資料日期及觸發規則的實際設定門檻。報價的「擷取時間」（UTC）
不等於成交時間；來源未提供報價時間時會明示。報價失敗改用調整後收盤價時，會標示「備援價格，非即時報價」。
指標另列歷史資料日期與調整後收盤價；歷史快取不會被標成今天的新資料。

**什麼時候會再通知**：條件組合有變（新增或不再觸發）就通知並標出差異；條件沒變時，只有價格比上次通知
又往同方向走 5% 以上（`PORTFOLIO_SIGNAL_RENOTIFY_MOVE_PERCENT`），或距上次通知滿 7 天
（`PORTFOLIO_SIGNAL_REMINDER_DAYS`）才再發一次，訊息會寫明原因。`--resend` 可強制重送。

**訊息格式**（`PORTFOLIO_MESSAGE_STYLE`）：

| 格式 | 內容 |
|---|---|
| `compact` | 一行：代號、現價與漲跌、主要觸發條件、持倉損益 |
| `plain`（預設） | 白話導言、📐 參考合理價、再通知原因、大盤背景 |
| `full` | 再加上觸發規則的門檻數字、報價擷取時間與指標資料日期 |

Telegram / Email 可分別用 `PORTFOLIO_TELEGRAM_MESSAGE_STYLE`、`PORTFOLIO_EMAIL_MESSAGE_STYLE` 覆寫；
macOS 桌面通知固定 `compact`。`PORTFOLIO_MESSAGE_SHOW_HOLDINGS=0` 會在所有格式隱藏你的均價與損益。
到價通知附上現價與目標價的百分比差距。Telegram 使用純文字並自動分段，避免長訊息或特殊符號導致送出失敗。
啟用選用 LLM 摘要時（只影響 `full` 格式的摘要句），數值、門檻與資料來源仍由固定格式保留。

**回測看兩件事**：`頻率`（觸發比例接近 100% 代表門檻太鬆、沒資訊量）與 `準確度`
（買點觸發後 20/60 日的勝率與報酬中位數，只算進場當日、用還原股價、絕不看未來）。
門檻每檔可持久化，解析順序：通用預設 → 全域覆寫 → 每檔覆寫（比照估值假設那套）。

三件刻意做的事：

1. **大盤背景**：同時看基準指數（預設 SPY）最近最多 5 個交易日的走勢。達到大幅同向變動門檻時，
   摘要列出實際比較期間、資料日期與漲跌幅，僅描述同向變動，不推論個股變動原因。
2. **除息/分割不會被誤判為大跌**：技術指標一律用**還原股價**（`auto_adjust`）；
   而「低於我的均價」用的是**即時原始報價 vs 你的原始成本**，兩者刻意分開算。
3. **不重複轟炸**：同樣的條件不會每天重發，價格再走 5% 或滿 7 天才提醒一次（見上方「什麼時候會再通知」）。

**這一層不給買賣建議。** 它只把「你設定的規則現在被觸發了、觸發的是哪幾條」講清楚，
單檔摘要帶「這只是規則觸發，非投資建議」；多檔通知在結尾統一保留一次。AI/摘要層不自己發明判斷邏輯
（見 [docs/decisions.md](docs/decisions.md) D12）。

**大盤靜音（可選）**：設 `PORTFOLIO_MUTE_MARKET_WIDE=1`，被大盤帶動的訊號仍會列出，
但不推播，避免大盤跌時每檔都響。行情歷史會快取在本機（`price_history` 表），
每檔每天只抓一次，盤中排程不再每 30 分重抓一年資料；抓取失敗時回退舊快取而非丟棄。

**摘要的第二層（可選，預設關）**：`PORTFOLIO_LLM_NARRATION=1` 可讓 LLM 把觸發的規則
講得更自然（需 `uv pip install -e ".[llm]"` 與 `ANTHROPIC_API_KEY`）。它只拿到「已觸發的
事實」、系統提示禁止它新增任何判斷、輸出一律補上免責，任何錯誤都自動回退確定性樣板。

**網頁有「智慧提醒」分頁**（手機加到主畫面同樣可用）：列出觸發、一句話摘要、門檻編輯、觸發歷史，
共用 `/api/signals*` 這組端點。

## 沒有 CSV 也能用：手動輸入持倉

```bash
stockcapy add-position VTI --shares 10.05 --avg-cost 300.00
stockcapy remove-position VTI
```

手動持倉會**覆蓋**同代號由 CSV 推導出的持倉，所以輸入的要是**該代號的完整股數與整體均價**。
完全不匯入 CSV、只用手動輸入也能跑 `targets` / `watch` / `check` / `show`。

## 怎麼拿 Firstrade 的資料

Firstrade 網站 → **My Accounts → History**（或 Statements / Gain & Loss）
→ 選日期區間 → 匯出 CSV。建議每次都拉「開戶至今」的完整區間，
重複的列會靠 row fingerprint 自動去重，不會重複計算。

```bash
stockcapy import ~/Downloads/FT_CSV_12345678.csv
stockcapy unclassified   # 確認沒有列被誤判成 OTHER
stockcapy show
```

> **欄位相容性**：解析器已對真實的**中文版匯出檔**驗證過
> （`日期,交易類別,數量,說明,代號,賬戶類別,價格,金額`），也支援英文版面
> （`Symbol,Quantity,Price,Action,...`）。欄名比對忽略大小寫與空白。
> 如果你的匯出檔欄位不同，`import` 會直接報出它看到的欄名。

Firstrade 中文匯出有三個陷阱，解析器都處理了：

1. **預扣稅沒有獨立列**，藏在股息列的說明文字裡（`NON-RES TAX WITHHELD $6.00000`）。
   「金額」欄是**股利總額**，不是實收淨額。
2. **股利再投資（DRIP）的交易類別是「其他」**、價格欄是 0，
   真實成交價在說明文字的 `REIN @ 400.0000` 裡。這些列會正確計入持股與成本。
3. **匯出區間不含早期買進時，成本基礎會不完整。**
   `import` 會從股息說明的「ON 10.05 SHS」反推應有股數並主動警告，
   叫你補匯入更早區間或用 `add-position` 手動補。

沒有真實檔案也能先試跑：

```bash
stockcapy import examples/sample_firstrade_history.csv && stockcapy show
```

## 成本基礎的算法

用**移動平均法**（moving average）：每次買進累加成本、賣出按當時均價釋放成本並結算已實現損益。
手續費與規費計入成本。

注意這和 Firstrade 報稅用的 **FIFO / 指定稅務批次（specific lot）不同**，
數字會有差異。本工具的用途是看投資組合狀態，**不是報稅依據**。

## 台幣報酬率與匯率損益拆解

美股用美元計價，但你的錢是台幣。這兩件事分開算才有意義。

```bash
stockcapy fx-sync    # 把每筆交易補上它「成交當天」的匯率
stockcapy show       # 最後一段就是台幣損益歸因
```

**關鍵在於用成交當天的匯率，不是今天的匯率。** 用今天的匯率去換算成本，
會把匯率損益混進股票損益裡，算出來的台幣報酬率是錯的。

輸出會把總損益拆成兩塊：

| | 含意 |
|---|---|
| **股票漲跌** | `(現值美元 − 成本美元) × 今日匯率`。股價本身賺賠多少 |
| **匯率變動** | `成本美元 × (今日匯率 − 成本均價匯率)`。已投入的錢因匯率變動賺賠多少 |

兩者**精確相加等於台幣總損益**（這條有專門的測試在守，跨多組情境驗證）。
所以你能分辨「這檔賺的錢有多少是股票漲、有多少只是台幣貶值」。

匯率來源同樣是 yfinance（`USDTWD=X`），歷史匯率快取在本機 `fx_rates` 表。
遇到週末或假日沒報價時，會回溯到前一個交易日——**絕不會用未來的匯率**
（那會無中生有地製造獲利，也有測試在守）。

匯入 CSV 時會自動補匯率，所以一般不需要手動跑 `fx-sync`。

> **匯率精度的預期**：Yahoo 給的是參考匯率，不是你複委託實際成交的匯率
> （券商有買賣價差），所以換算出來的台幣數字跟對帳單會有小幅差異。

## 每日自動快照

券商不會留長期歷史，自己累積才有長期曲線。每個交易日收盤後跑一次 `stockcapy snapshot`。
雲端部署可用 systemd timer（見 [docs/deploy-gce.md](docs/deploy-gce.md)）；Mac 本機用 launchd（替換 `/path/to/stockcapy`）：

```bash
cat > ~/Library/LaunchAgents/com.stockcapy.snapshot.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockcapy.snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/stockcapy/.venv/bin/stockcapy</string>
    <string>snapshot</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardErrorPath</key><string>/tmp/stockcapy-snapshot.err</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.stockcapy.snapshot.plist
```

（台灣時間早上 6:30 ≈ 美股收盤後。`snapshot` 的現金餘額券商無 API 可拿，
自動跑時記為 0，需要精確總值時手動跑一次 `stockcapy snapshot --cash-balance <金額>`。）

## 設定

所有設定都在 `.env`，完整清單與說明見 [.env.example](.env.example)。最常改的幾個：

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `PORTFOLIO_API_TOKEN` | （空） | 登入網頁用的 token；不設就只能在本機使用 |
| `PORTFOLIO_TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | （空） | Telegram 推播 |
| `PORTFOLIO_MESSAGE_STYLE` | `plain` | 通知格式：`compact` / `plain` / `full` |
| `PORTFOLIO_CHECK_INTERVAL_MINUTES` | Docker 30，本機 0 | 內建排程間隔；0 = 不自動檢查 |
| `PORTFOLIO_HOME_CURRENCY` | `TWD` | 本國貨幣 |
| `PORTFOLIO_NRA_WITHHOLDING` | `0.30` | 股利預扣稅率（台灣居民非美國人為 30%） |
| `PORTFOLIO_DATA_DIR` | `./data` | 資料庫位置 |

## 已知限制

- **行情來自 yfinance**（非官方 Yahoo 介面），免費但約延遲 15 分鐘，偶有抓取失敗或欄位缺漏；目前只支援這個來源。
- **成本基礎為移動平均法**，與券商報稅用的 FIFO 不同。
- **風險指標是「目前持倉」的回溯計算**，不是你歷史實際交易的績效（那需要每日快照累積）。
- **券商匯入目前只支援 Firstrade 的 CSV**；其他券商請用手動輸入持倉。現金餘額也需手動輸入。
- **ETF 的年配息用「近 12 個月實際配息」推算**（yfinance 不提供 ETF 的 `dividendRate`）。
- **匯率是 Yahoo 參考匯率**，與券商複委託實際成交匯率有價差。
- **桌面通知只支援 macOS**（用內建 `osascript`）；要推到手機請設定 Telegram 或 email。
- **智慧提醒是機械規則觸發**，門檻要自己用 `signals backtest` 回測校準；摘要只複述觸發的規則，不含買賣建議。
- **智慧提醒的網頁 UI 未經視覺驗證**。
- **不提供投資建議。** 這是資料工具，所有估值都是機械模型輸出，決策是你的。
- **歷史 P/E 只有 4 年年度顆粒度**，且用 trailing EPS，對高成長股偏高。
- **沒設 token 時只能綁 127.0.0.1**（fail-closed）；要對區網或公網開放必須先設 `PORTFOLIO_API_TOKEN`。
- **`/api` 回應一律 `no-store`**：開發期曾因瀏覽器快取把一次失敗的行情回應凍結在畫面上。
## 文件

| | |
|---|---|
| [docs/product.md](docs/product.md) | 產品設計：要解決什麼問題、六條核心設計原則、功能分類、明確不做的事 |
| [docs/decisions.md](docs/decisions.md) | 關鍵決策紀錄：為什麼這樣做、否決了什麼、踩過哪些坑 |
| [docs/progress.md](docs/progress.md) | 進度、待辦（含需要你提供資料的項目）、已知限制 |
| [docs/api.md](docs/api.md) | API 契約（網頁依賴它）與已知落差 |
| [docs/deploy-gce.md](docs/deploy-gce.md) | 部署到 GCE always-free e2-micro（月費 $0）的逐步指令與費用檢查清單 |

## 開發

### 分支策略

| 分支 | 角色 |
|---|---|
| **`develop`** | **預設分支**，日常開發都推這裡 |
| `main` | 發版分支，只在要發版時從 `develop` 合進來 |

所以 `develop` 是 GitHub 上的 default branch，PR 也以它為目標。改動先進 `develop`，
不要直接推 `main`。

### 架構

```
firstrade_csv.py   券商 CSV 解析（中文/英文版面、DRIP、內嵌預扣稅）
holdings.py        移動平均成本基礎、已實現損益
market.py          yfinance 封裝（報價/基本面/股利/歷史價/年度 EPS）
historical_pe.py   該檔自己的歷史 P/E 分布
valuation.py       四個估值模型 + 適用性閘門
assumptions.py     每檔假設的儲存與解析順序
targets.py         入手價（估值模型 or 200MA）
indicators.py      技術指標（RSI / 均線 / 布林），純函式、離線可測
signals.py         智慧提醒規則引擎（門檻、觸發條件、大盤同向判斷）
renotify.py        已通知過的訊號何時再發（條件改變 / 再走 N% / 滿 N 天）
advisor.py         通知文字與三種格式（只複述觸發的規則，不發明判斷；可選 LLM 後端）
signal_config.py   每檔智慧提醒門檻的儲存與解析（通用預設→全域→每檔）
backtest.py        回放規則：觸發頻率 + 買點後續報酬（勝率/中位數）
performance.py     報酬與風險指標
dividends.py       股利現金流（含 NRA 預扣）
diagnostics.py     成本基礎缺失偵測
alerts.py          價位提醒與 macOS 通知
currency.py        台幣換算與「股票 vs 匯率」損益歸因
fx_sync.py         歷史匯率取得、快取、回填到每筆交易
auth.py            API token 驗證與 fail-closed 綁定檢查
notifiers.py       通知管道（macOS 桌面 / Telegram / email）
scheduler.py       雲端用的背景價格檢查（到價提醒 + 智慧提醒）
service.py         CLI / Web 共用的商業邏輯
cli.py             終端介面
web/               FastAPI + 單檔網頁前端
```

### 測試

```bash
.venv/bin/python -m pytest -q
```

全部離線執行（行情、通知、Telegram/SMTP、LLM、指標與回測都用 stub 或合成序列），GitHub Actions 每次 push 自動跑。
`tests/conftest.py` 的 `make_settings()` 是唯一建構 `Settings` 的地方，新增設定欄位不會打爛各測試檔。
