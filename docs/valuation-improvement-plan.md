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
| D1 | 不確定性由三個項目計點決定（單一模型 2 點、模型分歧大 1 點、盈餘劇變 1 點），點數對應「低／中／高／極高」，四級都必須可達 |
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

## 七、實作結果（2026-11-06）

全部標準達成。實作過程中對原設計有三處修正，已併入上面的標準或決策紀錄：

| 修正 | 原因 |
|---|---|
| D1 由「旗標數」改為「計點」（單一模型 2 點） | 「單一模型」與「模型分歧」互斥（分歧要 ≥ 2 個模型），等權重的旗標最多只會亮 2 個，「極高」永遠到不了。單一模型本來就是最弱的情況，權重加倍 |
| 階段 2 的 Forward P/E 模型不另立 | Target P/E 是 trailing 基準，拿去乘預估 EPS 會把成長算兩次（`valuation.py` 註解已警告）；用同一個倍數乘預估 EPS 又和 Target P/E 算出同一個數。改由 PEG 承擔「前瞻 + 成長調整」 |
| 階段 3 的「同業中位數」「Justified P/E」不做 | 理由見 `decisions.md` D30：yfinance 無穩定同業清單且無法離線驗證；Justified P/E 實際算出 17～19 倍，與預設 18 倍重疊、沒有獨立資訊 |

### 標準對應的測試

| # | 驗證位置 |
|---|---|
| A1 | `test_advisor.py`：`test_a_lone_model_is_called_out…`、`test_several_models_are_listed…` |
| A2 | `test_advisor.py`：`test_uncertainty_and_the_margin_actually_used_are_shown` |
| A3 | `test_advisor.py`：`test_the_multiple_and_where_it_came_from_are_shown`、`test_a_manual_multiple_says_so` |
| A4 / C1 | `test_alert_valuation.py`：提醒與完整視圖同一個合理價、歷史取不到退回預設、失敗不丟提醒、30 天內不重抓 |
| A5 | `test_advisor.py`：`test_reference_without_context_adds_nothing_extra`、`test_no_reference_price_means…` |
| B1 / B2 | `test_valuation.py`：PEG 公式、封頂、閘門（參數化 6 種不適用情形） |
| B3 | `test_valuation.py`：`test_value_stock_without_new_inputs_is_untouched…` |
| B4 | `test_valuation.py`：`test_growth_stock_is_no_longer_a_single_assumption` |
| B5 | `test_valuation.py`、`test_api.py`：新模型自動列在估值表 |
| C2 / D5 | `test_api.py`：`test_targets_expose_the_multiple_and_the_uncertainty…` |
| C3 | `decisions.md` D30 |
| D1 | `test_uncertainty.py`：四級皆可達 |
| D2 | `test_uncertainty.py`、`test_assumptions.py`、`test_api.py`：明確設定的邊際不被加寬 |
| D3 | `test_targets.py`（buy_below 與 verdict 同用有效邊際）、`test_report_valuation.py`、`test_alert_valuation.py` |
| D4 | `test_uncertainty.py`、`test_targets.py`：低等級與舊行為一致 |
| E1 / E2 / E3 | `test_valuation.py`：DCF 閉式解與逐年獨立計算、閘門（參數化 7 種）、既有股票不變 |
| G1 | 因「有意的行為變更」修改的既有測試：`test_valuation.py` 成長股彙總（現為 2 個模型）、`test_target_pe_options.py` 三個（MSFT 預期成長 31%，PEG 加入，單一模型情境改用低成長標的）、`test_signal_service.py` 價格快取計數（提醒現在也會讀五年歷史，改為只計訊號窗口） |
| G3 | `store.py` 無任何改動 |

另外做了突變驗證（故意弄壞實作，測試必須變紅）：關掉加寬、讓提醒忽略歷史 P/E、拿掉 PEG 成長閘門、拿掉 DCF 產業閘門，四種都有測試失敗。

### 已知限制

- 沙盒連不到 Yahoo，所以**沒有用實際 NVDA 資料驗證**。改用你那則提醒反推的數字（EPS 7.18、現價 181.60）加上預估 EPS 與自由現金流的合理範圍掃過：合理價落在 113～220，沒有荒謬值；DCF 與每股自由現金流嚴格成正比，是最敏感的模型。請你實際跑一次 `stockcapy targets` 對照。
- PEG 目標 1.0、成長上限 20%、終值成長 3% 是程式內預設，尚未逐檔持久化。

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
