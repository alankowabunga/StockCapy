# API 契約

網頁前端（`src/portfolio/web/static/index.html`）依賴這份契約，改動時要同步調整。

Base URL 由使用者設定。除 `/api/session` 外，所有端點都需要 `Authorization: Bearer <token>`（或瀏覽器的 httpOnly cookie）。

錯誤格式一律 `{"detail": "..."}`。401 = token 不對或未帶。

快取標頭：`/api/*` 回 `no-store`；`/` 與 `/index.html` 回 `no-cache`（見 decisions.md D8）。

## 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/session` | **公開**。`{authentication_required, authenticated}` |
| POST | `/api/login` | body `{token}`，成功設 cookie |
| POST | `/api/logout` | 清除 cookie |
| GET | `/api/portfolio` | 總覽 + 持倉 + 產業曝險 + **台幣歸因**。無持倉回 409 |
| GET | `/api/targets` | 入手價清單（含 `estimates` 明細與價格位置） |
| GET | `/api/risk` | 風險指標，資料不足回 `null`；無持倉回 409 |
| GET | `/api/dividends` | 已領 + 未來一年預估 |
| GET | `/api/watchlist` | 監控清單與距離 |
| POST | `/api/watchlist` | body `{symbol, buy_below?, sell_above?}`。兩者皆空回 400；只給一個時另一個保留原值 |
| DELETE | `/api/watchlist/{symbol}` | |
| POST | `/api/check` | body `{resend}`。回 `{triggered, notified, failed_channels}` |
| GET | `/api/signals` | 智慧提醒：回 `{signals[], stale[]}`。**2026-10-13 起由陣列改為物件**——`signals: []` 單獨存在無法分辨「市場平靜」與「行情壞掉」 |
| POST | `/api/signals/check` | body `{resend}`。重評估並推播新觸發，回 `{signals, fresh, notified, failed_channels, stale}` |
| GET | `/api/signals/thresholds` | `{default, symbols[]}`，含每檔 `source`（manual/default） |
| PUT | `/api/signals/thresholds/{symbol}` | 只送要改的門檻欄位。全空回 400。`*` 改通用預設 |
| DELETE | `/api/signals/thresholds/{symbol}` | 清除該檔門檻覆寫 |
| GET | `/api/signals/history` | 觸發歷史（最新在前），可帶 `?symbol=&limit=` |
| GET | `/api/assumptions` | `{default, symbols[]}`，含 `source` 與 `historical_median_pe` |
| PUT | `/api/assumptions/{symbol}` | 只送要改的欄位。全空回 400 |
| DELETE | `/api/assumptions/{symbol}` | 還原成歷史 P/E 或預設 |
| GET | `/api/positions/manual` | |
| POST | `/api/positions/manual` | body `{symbol, quantity, average_cost, note}` |
| DELETE | `/api/positions/manual/{symbol}` | |
| POST | `/api/import` | multipart，欄位名 `file`。無法解析回 400 |
| GET | `/api/history` | 每日淨值快照 |
| GET | `/api/prices/{symbol}` | 該檔快取的歷史收盤 + 60 日均線，可帶 `?days=`（預設 180）。沒有快取回 `points: []` |

## 可為 null 的欄位

這些欄位在資料不足時回 `null`，前端必須顯示 `—` 而非 `NaN` / `undefined`：

- `targets[]`：`average_cost`、`discount_to_average_cost_percent`、`reference_price`、`buy_below`、
  `gap_to_buy_below_percent`、`trailing_pe`、`week52_low`、`week52_high`、`range_percentile`、
  `premium_to_two_hundred_day_percent`、`historical_median_pe`
- `positions[]`：`average_cost_rate`、`cost_basis_home`、`stock_gain_home`、`currency_gain_home`、`total_gain_home`
- `home_currency_summary`：`available: false` 時只有 `home_currency`、`home_currency_symbol`、`rate_now`
- `/api/risk`：整個回應可為 `null`
- `/api/prices/{symbol}`：`points[].moving_average` 在 60 日窗口還沒填滿前為 `null`
- `signals[]`：`average_cost`、`discount_to_average_cost_percent`、`rsi`、`sma_short`、`sma_long`、
  `bias_to_sma_short_percent`、`bollinger_lower`、`bollinger_upper`、`benchmark_symbol`、`benchmark_change_percent`（歷史不足時為 `null`）

## 列舉值

- `targets[].basis`：`估值模型` | `200日均線` | `資料不足` | `無`
- `targets[].assumption_source`：`historical` | `manual` | `default`
- `watchlist[].state`：`buy_triggered` | `sell_triggered` | `watching`
- `check.triggered[].direction`：`BUY` | `SELL`
- `signals[].direction`：`BUY`（偏低）| `SELL`（偏高）
- `signals[].conditions[].code`：`BELOW_AVERAGE_COST` | `BELOW_SHORT_TREND` | `OVERSOLD_RSI` | `BELOW_LOWER_BAND` | `ABOVE_SHORT_TREND` | `OVERBOUGHT_RSI` | `ABOVE_UPPER_BAND`

## 已知的契約落差

1. `/api/risk` 在**沒持倉**時回 409（不是 `null`）。`null` 只代表「有持倉但歷史價格不足」。
2. `positions[].sector` 不會是 `null`，缺值時是字串 `"Unknown"`。
3. `week52_low` / `week52_high` 用 `or None` 序列化，所以真正的 `0` 會被當成缺值。對股票不構成實務問題。
4. （已移除：原為 Expo app 未接台幣欄位，app 已於 D25 移除。）
5. 網頁已接「智慧提醒」分頁，但 UI 未經視覺驗證。
6. `signals/thresholds[].source` 為 `manual` | `default`；門檻欄位任一為 `null` 代表沿用下一層（每檔→全域→內建預設）。
7. `/api/prices/{symbol}` **只讀 DB 快取**，不會連線抓行情：畫圖不該觸發網路。`days` 的視窗從「最新一筆已存日期」往回算（不是今天），快取晚一兩天也畫得出完整區間。行動端尚未接。


## 行情資料過期（2026-10-13 起）

收盤序列的最後日期超過 5 天時，該標的**不產生訊號**，改列入 `stale`：

```json
{"symbol": "MSFT", "as_of": "2026-09-18", "age_days": 25,
 "describe_age": "資料停在 2026-09-18（25 天前）"}
```

即時報價正常也救不了——布林、RSI、均線全部來自收盤序列。詳見 decisions.md D21。

**破壞性變更**：`GET /api/signals` 從 `Signal[]` 改為 `{signals, stale}`。網頁已同步更新；任何第三方消費端需一併調整。

## 智慧提醒通知對象（2026-11-06 起，D29）

- `GET /api/signals/subscriptions` → `[{symbol, sources, enabled}]`。`sources` 為 `持股` / `監控` / `手動加入`。
- `PUT /api/signals/subscriptions/{symbol}`，body `{"enabled": true|false}` → 回傳該檔最新狀態；
  關掉一檔只在 `手動加入` 時會讓它從清單消失（`sources: []`）。代號不合法回 400。
- `GET /api/signals` 與 `POST /api/signals/check` 的每筆 signal 多一個 `muted`：`true` 代表照樣評估、但不推播。
