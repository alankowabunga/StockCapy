# 估值引擎改進設計：讓通用規則也適用成長股

## 緣起

一則 NVDA 智慧提醒出現「參考合理價 $129.24、入手價 $103.39」，而現價約 $181.60。使用者質疑這個合理價明顯偏低、不合常理。追查後確認：**這不是 NVDA 特例，而是通用估值規則套在高成長股上時的系統性結果**。本文整理現況、對照文獻與業界實務，提出分階段改進設計。

結論先講：現有「多模型 + 適用性閘門 + 取中位數」的架構方向正確，接近業界精神；缺的是**處理成長股的那幾個模型**，以及**提醒路徑把信心度資訊丟掉**。要補的不是特例邏輯，而是讓通用規則的工具箱更完整。

---

## 一、現況：它本來就是一套通用規則

每檔股票跑同樣 4 個模型，差異化靠「適用性閘門」自動發生（見 `valuation.py`、`product.md` 原則 2）：

| 模型 | 採計條件 | 代表性質 | 程式位置 |
|---|---|---|---|
| Graham Number | 本益比 ≤ 25 且淨值 > 0 | 價值股 | `valuation.py:86` |
| Target P/E | EPS > 0（幾乎永遠適用） | 多數獲利公司 | `valuation.py:133` |
| Gordon Growth (DDM) | 殖利率 ≥ 2% | 穩定配息股 | `valuation.py:147` |
| 分析師目標價 | 永不納入，只對照 | — | `valuation.py:167` |

合理價 = 採計模型的**中位數**（`valuation.py:57`）；入手價 = 合理價 ×（1 − 安全邊際），預設安全邊際 20%（`valuation.py:21`、`targets.py:58`）。

NVDA 的結果是這套規則跑出來的：高本益比 → Graham 出局；不配息 → DDM 出局 → **只剩 Target P/E 一個模型**。而 Target P/E = EPS × 倍數，倍數在提醒路徑固定用 18 → $129.24，再打 8 折 → 入手價 $103.39。

---

## 二、文獻與業界怎麼估同樣這些數據

學界把估值分兩大家族，業界標準流程是**以 DCF 為錨，再用倍數法與反向 DCF 交叉驗證，最後要求安全邊際**。

| 方法 | 分類 | 適用情境 | 現有程式 |
|---|---|---|---|
| DCF（現金流折現） | 絕對估值 | 業界估 fair value 的核心（如 Morningstar） | ❌ 無 |
| Target / 同業 P/E | 相對估值 | 最主流，88% 分析師使用 | ✅ 有 |
| 前瞻 P/E、PEG | 相對（成長調整） | 高成長、盈餘快速變動 | ❌ 無 |
| EV/Sales | 相對 | 未獲利或薄利公司 | ❌ 無 |
| Gordon Growth (DDM) | 絕對 | 成熟配息股 | ✅ 有 |
| Graham Number | 公式法 | 深度價值篩選 | ✅ 有 |

**倍數要設多少**——業界四種做法，現有只做了一種：

| 做法 | 內容 | 現有程式 |
|---|---|---|
| 同業中位數 | 可比公司 P/E 中位數 | ❌ |
| 自身歷史均值 | 個股過去 P/E 中位數 | ✅（但提醒路徑未用，見問題 2） |
| Justified P/E | 基本面推導：P/E =（配息率）/(r − g)，成長越高倍數越高 | ❌ |
| 成長調整（PEG / 前瞻） | 高成長正當化高倍數 | ❌ |

**安全邊際**——文獻與業界不是固定值，而是隨不確定性浮動：

| 來源 | 做法 |
|---|---|
| Graham / 傳統價值投資 | 約 20~30%，不確定性高拉到 25~50% |
| Morningstar（業界標竿） | 依不確定性評級：低 → 跌到合理價 95% 才算買點；極高 → 要跌到 50% |
| 現有程式 | 固定 20%，不分公司 |

---

## 三、現有實作的四個具體問題

### 問題 1：成長股工具箱缺口（最根本）
高成長、不配息、高本益比的股票會把 Graham 與 DDM 都排除，只剩 Target P/E。此時合理價 = EPS × 單一倍數，等於用「成熟價值股的尺」量成長股。文獻明確指出這類股票不該只用 trailing P/E，應改用前瞻 P/E、PEG 或 EV/Sales，且倍數與成長率的時間基準不可混用（`valuation.py:105-123` 註解已點到同一風險）。

### 問題 2：提醒路徑與完整視圖數字不一致
提醒走 `_valuation_hint`（`service.py:505`），為了讓提醒週期輕量，**刻意跳過 5 年歷史 P/E 推導，永遠用預設倍數 18 + 手動 override**。但完整入手價視圖走 `resolve_assumptions`（`assumptions.py:181`），歷史 P/E 穩定時會改用個股歷史中位數。結果：**同一檔股票，提醒與完整視圖可能給出不同的合理價**，而使用者無從得知看到的是哪一個。

### 問題 3：提醒把「信心度」資訊丟掉
系統其實有脆弱度機制：`EntryVerdict.is_fragile` 在採計模型數 < 2 時，把建議降級為「證據不足，不給建議」（`entry_verdict.py:68-80`），`product.md` 原則也寫明「只剩單一模型時要警告脆弱度高」。但 `_valuation_hint` 只取出 `reference_price / buy_below / basis`，**丟掉了 `model_count` 與 verdict**（`service.py:526`）。因此提醒上那句「參考合理價 $129.24、入手價 $103.39」看起來很篤定，卻完全沒揭露它只靠一個模型——違反了產品原則。

### 問題 4：安全邊際固定 20%
不隨股票不確定性調整，與 Morningstar 等業界做法不同。對波動大的成長股，固定 20% 可能不足。

---

## 四、改進設計（分階段）

遵守既有產品原則：只給基準不給建議、不適用就說不適用、分析師目標價永不納入中位數。

### 階段 1：先止血——提醒揭露信心度（低成本、高價值）
對應問題 2、3。不新增模型，只讓提醒誠實。

- `_valuation_hint` 一併回傳 `model_count`（與 verdict 的 `is_fragile`），讓 `advisor.py` 的 `_valuation_block` 在單一模型時加一句信心度註記，例如：「此合理價僅由 1 個模型（Target P/E）推得，對倍數假設高度敏感」。
- 決定提醒與完整視圖的倍數來源要不要統一；若維持輕量版，就在提醒標示「提醒使用預設倍數，完整視圖可能不同」。
- 影響檔案：`service.py`、`advisor.py`、`signals.py`（`Signal` 增欄位）、對應測試。

### 階段 2：補成長股模型（解決根本問題 1）
在既有「模型 + 適用性閘門」框架內新增模型，不改架構：

| 新增模型 | 適用性閘門建議 | 說明 |
|---|---|---|
| 前瞻 P/E | 有 forwardEps 且 > 0 | 用預估 EPS × 倍數，取代對成長股失真的 trailing |
| PEG | 有盈餘且有成長率估計 | 用 PEG=1（或可設）反推合理倍數，讓高成長正當化高倍數 |
| EV/Sales | 未獲利或毛利率低 | 盈餘為負時的備援，需搭配對未來毛利的假設 |

閘門設計要與既有一致：不適用就排除、不污染中位數（`product.md` 原則 2、`decisions.md` D3）。

### 階段 3：倍數選擇升級（對應問題「倍數設多少」）
- 讓 Target P/E 的倍數能取「同業中位數」或「自身歷史中位數」，而非固定 18；提醒路徑也統一吃得到。
- 評估加入 Justified P/E 作為基本面對照。

### 階段 4：安全邊際浮動（對應問題 4）
- 以可量化的不確定性（例如歷史 P/E 四分位距 `historical_pe.py:30`、模型分歧度 `valuation.py:78`）映射到安全邊際，分歧越大邊際越寬，向 Morningstar 做法靠攏。
- 仍維持「只給基準」：浮動的是邊際寬度，不是買賣建議。

### 階段 5（選配）：DCF
業界核心方法，但 yfinance 的現金流資料品質與假設負擔較高，列為長期選項，優先度最低。

---

## 五、建議優先順序

| 優先 | 階段 | 理由 |
|---|---|---|
| 1 | 階段 1（揭露信心度） | 成本最低，直接消除「篤定的誤導」，立刻改善使用者體驗 |
| 2 | 階段 2（成長股模型） | 解決根本問題，讓通用規則真正通用 |
| 3 | 階段 3（倍數來源） | 讓合理價更貼近市場現實 |
| 4 | 階段 4（浮動邊際） | 錦上添花，向業界標竿對齊 |
| 5 | 階段 5（DCF） | 效益高但成本與資料風險也高，最後再評估 |

---

## 六、驗收標準（實作前定稿）

每條都要有對應的自動化測試或可重現的檢查；全部達成才算完成。

### 全域
| # | 標準 |
|---|---|
| G1 | 既有測試維持通過；若因「有意的行為變更」而修改，須在提交說明逐條列出原因 |
| G2 | 新增測試涵蓋下列每一條標準，全部測試通過 |
| G3 | 不改資料庫 schema（不需要遷移） |
| G4 | 產品原則不破：分析師目標價仍不進中位數；提醒文字不出現買賣建議用語 |
| G5 | 文件同步：`product.md` 模型表、`decisions.md` 新決策、`api.md` 新欄位 |

### 階段 1：提醒揭露信心度
| # | 標準 |
|---|---|
| A1 | 提醒在合理價下方列出「採計模型名稱與數量」；僅 1 個模型時有明確警示字樣 |
| A2 | 提醒顯示不確定性等級，以及入手價實際用的安全邊際 |
| A3 | 提醒顯示 Target P/E 倍數與來源（歷史／預設／手動） |
| A4 | 提醒與完整視圖用同一套假設解析；歷史 P/E 取不到時退回預設，且不中斷提醒 |
| A5 | 沒有合理價時，提醒內容與現在完全相同（不多一行） |

### 階段 2：成長股模型
| # | 標準 |
|---|---|
| B1 | PEG 模型公式：預估 EPS × PEG 目標 × 成長率%；成長率超過上限以上限計 |
| B2 | PEG 閘門：預期成長 < 15%、預估 EPS ≤ 0、缺資料時不採計，且不進中位數 |
| B3 | 沒有新欄位資料的既有股票，結果與改動前一致 |
| B4 | 「高本益比、不配息、高成長」的股票採計模型數 ≥ 2，合理價高於只有 Target P/E 18 倍時的結果 |
| B5 | 新模型自動出現在 CLI 與 Web 的估值表，不需改 UI |

### 階段 3：倍數來源一致
| # | 標準 |
|---|---|
| C1 | 同 A4（提醒與完整視圖一致） |
| C2 | API 回傳 Target P/E 倍數數值與來源 |
| C3 | 文件記錄未採用的做法（同業中位數、Justified P/E）及原因 |

### 階段 4：浮動安全邊際
| # | 標準 |
|---|---|
| D1 | 不確定性由三個旗標決定（單一模型、模型分歧大、盈餘劇變），旗標數對應「低／中／高／極高」 |
| D2 | 有效安全邊際 = max(使用者設定, 等級下限)；使用者明確設定安全邊際（全域／個股／命令列）時不自動加寬 |
| D3 | 入手價、判斷層級（verdict）、CLI、Web、提醒一律使用同一個有效邊際 |
| D4 | 等級為「低」時，結果與舊行為完全一致 |
| D5 | API 回傳不確定性等級、原因、有效安全邊際 |

### 階段 5：DCF
| # | 標準 |
|---|---|
| E1 | 公式可手算驗證：10 年成長線性淡出至終值成長，以 required_return 折現，除以流通股數 |
| E2 | 閘門：自由現金流 ≤ 0、缺股數、金融／不動產業、折現率 ≤ 終值成長時不採計 |
| E3 | 缺自由現金流欄位的既有股票，結果與改動前一致 |

---

## 參考來源

- Morningstar — Fair Value Estimate 與不確定性評級 / 安全邊際：<https://www.morningstar.com/investing-terms/fair-value-estimate>、<https://www.morningstar.com/stocks/how-determine-whether-stock-is-cheap-expensive-or-fairly-valued>
- Morningstar Equity Research Methodology Guide：<https://freefincal.com/wp-content/uploads/2018/05/Morningstar_Equity_Research_Methodology_Guide_Final.pdf>
- Morgan Stanley — Counterpoint Global Insights: Valuation Multiples：<https://www.morganstanley.com/im/publication/insights/articles/article_valuationmultiples.pdf>
- CFA Institute — Market-Based Valuation: Price and Enterprise Value Multiples：<https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/market-based-valuation-price-enterprise-value-multiples>
- Damodaran — Earnings Multiples (NYU Stern, Ch.18)：<https://pages.stern.nyu.edu/~adamodar/pdfiles/valn2ed/ch18.pdf>
- PEG Ratio：<https://en.wikipedia.org/wiki/PEG_ratio>
- Comparable Company Analysis：<https://www.streetofwalls.com/finance-training-courses/investment-banking-technical-training/comparable-company-analysis/>
- Stock Rover — Valuation Metrics Explained：<https://www.stockrover.com/metrics/valuation/>
