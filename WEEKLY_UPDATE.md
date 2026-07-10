# 每週更新指令書（給 Claude Code）

> 目標:每週把 Best Buy(US/CA)+ Walmart(US)的 HP OmniBook 新評論抓下來、重算 dashboard、交給 Claire push。
> 一切自動化的入口是 `run_weekly.py`。**Claude Code 全程不 push,push 由 Claire 親自執行。**

---

## 一鍵指令(90% 情況只需要這個)

```powershell
cd "C:\Users\hp\Side Project\reddit-sentiment"
python run_weekly.py
```

這會依序做:增量抓取(BBY CA → BBY US → Walmart)→ 去識別化 → 統整 → 系列彙整 → 重建 manifest → 驗證 schema,然後印出給 Claire 的 git 指令。**增量抓取**只抓比上次新的評論(靠 `_private/state/` 記錄每檔的 last-seen id),所以每週很快。

跑完後 Claude Code 要做的事:
1. 執行 `git status --short`,**確認輸出裡沒有 `_private/` 或任何 `.env`**(有的話停下告知 Claire,不要 add)。
2. 把要更新的檔案交給 Claire 並附上建議指令(見最後一節),**不要自己 push**。

---

## 參數(視情況加)

| 情況 | 指令 |
|---|---|
| 只更新某一來源 | `python run_weekly.py --only walmart`(或 `bby` / `us` / `ca`) |
| 某個 scraper 被擋(印出 `[BLOCKED]`) | 加 `--headed` 開有畫面的瀏覽器再跑一次:`python run_weekly.py --only us --headed` |
| 順便掃有沒有新機型上架 | `python run_weekly.py --discover`(見下方「新增產品」) |
| 只重算不抓取(改了分析邏輯時) | `python run_weekly.py --skip-scrape` |

---

## 新增產品到追蹤清單

### A. 自動掃描新 SKU(Best Buy)
```powershell
python scraper/discover_skus.py --dry-run
```
印出 diff。想追蹤的新品,到 `config/products.json` 把該檔的 `"enabled": false` 改成 `true`,然後正常跑 `run_weekly.py`。

### B. 手動加一台(任何 retailer,例:新的 Walmart OmniBook 3)
在 `config/products.json` 的 `products` 陣列加一筆:
```json
{
  "product_key": "wm_<item_id>",
  "market": "US",
  "retailer": "walmart",
  "sku": "<item_id>",
  "series": "Three",
  "name": "HP OmniBook 3 14\" ...(官網完整名稱)",
  "model": "", "cpu_ram_ssd": "",
  "url": "https://www.walmart.com/ip/<item_id>",
  "enabled": true
}
```
- `product_key` 前綴:Walmart 用 `wm_`、BBY US 用 `us_`、BBY CA 用 `ca_`。
- `series` 目前有:`Ultra` `UltraFlip` `X` `XFlip` `FiveClamshell` `FiveConvertible`。**若是全新系列(如 OmniBook 3),用新代號**(例 `Three` = clamshell、`ThreeFlip` = 2-in-1),並同步在三處註冊:
  1. `analysis/aggregate_series.py` 的 `SERIES_KEYS` + `SERIES_TITLES`
  2. `docs/index.html` 的 `SERIES_LABEL` + `SERIES_ORDER` + `ABBREV` + `OVERVIEWS`
  3. `worker/gemini-proxy.js` 的 `SERIES_MAP`
  （改法直接照現有 `FiveClamshell` 那幾行複製。）
- 加完跑 `python run_weekly.py --only walmart`(或對應來源),它會抓新產品全部評論(state 沒紀錄=第一次抓全量)。

---

## 各 scraper 說明(排錯用)

| 檔案 | 來源 | 方法 | 常見狀況 |
|---|---|---|---|
| `scraper/scrape_ca.py` | bestbuy.ca | REST API,直接 JSON | 幾乎不會被擋 |
| `scraper/scrape_us.py` | bestbuy.com | Playwright 打 `/ugc/v2/reviews` | 被擋加 `--headed` |
| `scraper/scrape_walmart.py` | walmart.com | Playwright 解析 `__NEXT_DATA__` | 被擋加 `--headed`;若每頁都 `[NODATA]` 代表 Walmart 改了頁面結構,見下 |
| `scraper/_browser.py` | — | 共用 stealth 瀏覽器設定 | 被擋很兇時 `pip install playwright-stealth` 會自動生效 |
| `scraper/sanitize.py` | — | 去 author/PII、統一 schema、加 topics/product_name/competitors 欄 | — |

**Walmart 頁面改版自救**:若 `scrape_walmart.py` 全部 `[NODATA]`,Claude Code 直接開一個 item 的 reviews 頁存 HTML,檢查評論 JSON 的新路徑,更新 `deep_find_reviews()` 的判斷欄位(目前認 `reviewText` / `reviewSubmissionTime`)。這是唯一可能需要人工維護的點。

---

## 環境需求(第一次或換機才需要)
```powershell
pip install requests playwright
playwright install chromium
```

---

## 給 Claire 的 push 指令(Claude Code 跑完後貼給她)
```powershell
cd "C:\Users\hp\Side Project\reddit-sentiment"
if (Test-Path .git\index.lock) { del .git\index.lock }
git status --short
git add docs/ config/products.json
git commit -m "data: weekly refresh <日期>"
git push origin main
```
push 後 GitHub Pages 會自動重新部署,約 1-2 分鐘;若動到 `worker/` 才會觸發 Cloudflare 重部署。Claire 開網站 Ctrl+F5 驗收。

---

## 設定成真正「每週自動」(選配)
若要完全免手動,Claude Code 可幫 Claire 建一個 Windows 工作排程器任務,每週一早上跑 `python run_weekly.py`,並在完成後開啟一個提示視窗請 Claire 檢查後 push(抓取仍建議人工過目再 push,因為 Walmart/BBY 偶爾會改版)。要做再跟 Claude Code 說「幫我設 Windows 排程每週一跑 run_weekly」。
