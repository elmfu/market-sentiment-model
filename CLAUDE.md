# CLAUDE.md — HP OmniBook Review Intelligence

Rules Claude Code must follow in this repo at all times.

---

## 鐵則（絕不允許例外）

### 1. 禁止在無原始資料的情況下建立或更新 docs/data/

在執行任何 build / sanitize / consolidate / aggregate / build_manifest 之前，必須先確認：

```python
# 驗證原始資料存在且非空（已內建於 weekly_update.py 資料閘門）
assert any(_private/raw_data/**/*.json)
assert count_raw_records() > 0
```

若 `_private/raw_data/` 為空、或爬取步驟全數失敗（exit != 0）：
- **立即停止，不執行任何 build 步驟**
- 印出 `ABORT: 原始資料為空 — 禁止在無資料狀態下建 dashboard`
- 不得以舊版 sanitized JSON 或任何快取資料假裝本週有新資料

### 2. Build 前必須驗證筆數一致

`sanitize.py` 輸出的 `docs/data/{key}/reviews_sanitized.json` 筆數，必須 ≥ 上一次 commit 的筆數（只增不減，除非明確執行去重或資料修正）。

驗證方式：`analysis/validate_output.py` 已實作 schema 與筆數檢查，**weekly_update.py 裡 validate_output 步驟不能設為 `required=True` 以外的值**（除非 validate_output.py 本身有 bug 需要臨時繞過，且必須留下 log 說明原因）。

> 目前 `validate_output.py` 在 `required=False` — 若發現它一直在失敗，優先修它，不是繼續忽略它。

### 3. push 前必須列出資料來源與筆數

每次執行完 `python weekly_update.py --no-push` 或 `python run_weekly.py`，在告知 Claire push 之前，Claude Code 必須先印出以下資訊（從 log 或 git diff 彙整）：

```
資料來源    本次新增   累計筆數
──────────  ─────────  ────────
BBY CA       +N         X
BBY US       +N         X
Walmart US   +N         X（若被擋：N=0，提示需 --headed）
──────────  ─────────  ────────
合計         +N         X

git status 無 _private/ ✓
staged 檔案: docs/ config/products.json
```

若任何一來源「累計筆數 < 上週」，**停下來詢問 Claire，不要直接 push**。

---

## 常規規則

### git push 永遠由 Claire 親自執行

Claude Code **不得**執行 `git push`，無論帶什麼參數（包含 `--force`）。
每次需要 push 時，輸出給 Claire 的 git 指令：

```powershell
git status --short
# 確認沒有 _private/ 或 .env 出現在 staged 清單
git add docs/ config/products.json
git commit -m "data: weekly refresh YYYY-MM-DD"
git push origin main
```

### 原始評論資料永不 commit

`GIT_ADD_PATHS` 只能包含 `docs/` 和 `config/products.json`。  
以下路徑受 `.gitignore` 保護，Claude Code 不得嘗試 `git add` 它們：

| 路徑 | 原因 |
|------|------|
| `_private/` | 含有 PII（author / nickname / userId） |
| `_private/raw_data/` | 原始爬取 JSON，有姓名欄位 |
| `_private/state/` | 增量游標，本地狀態 |
| `_private/llm_input/` | 全文評論，非公開 |
| `.claude/` | Claude Code 本地設定 |
| `.env` | 金鑰 |

### 每週更新入口

```powershell
# 標準週更（自動爬取 + build，不 push）
python run_weekly.py

# 或用排程器入口（功能相同，多了 log 檔與更嚴格的閘門）
python weekly_update.py --no-push
```

Task Scheduler 設定（使用 `run_weekly.bat`）：
- **程式**：`C:\Users\hp\Side Project\reddit-sentiment\run_weekly.bat`
- **參數**：`--no-push`
- **起始於**：`C:\Users\hp\Side Project\reddit-sentiment`

### Walmart 被擋時

Walmart 爬蟲在 headless 模式下常被擋（exit 0 但印 `[BLOCKED]`）。  
被擋時不中止整條管線（`required=False`），但 Claude Code 需在回報中標示：

```
Walmart US: [BLOCKED] 13/14 products — 請用 --headed 手動補跑：
python run_weekly.py --only walmart --headed
```

### 新系列或新 retailer 上架時

在 `config/products.json` 加新 `series` 值，必須同步更新三個地方（否則 dashboard 不顯示）：

1. `analysis/aggregate_series.py` → `SERIES_KEYS` + `SERIES_TITLES`
2. `docs/index.html` → `SERIES_LABEL` + `SERIES_ORDER` + `ABBREV` + `OVERVIEWS`
3. `worker/gemini-proxy.js` → `SERIES_MAP`

---

## 編碼規定（Windows ARM64 注意事項）

此機器為 Windows ARM64，Python 預設編碼 `cp1252`。
凡是讀取 `config/products.json`（含中文/特殊字元）或寫 log 時，**必須明確指定 `encoding="utf-8"`**：

```python
# 正確
Path("config/products.json").read_text(encoding="utf-8")
open(log_file, "a", encoding="utf-8")

# 錯誤（會在 ARM64 Windows 上崩潰）
Path("config/products.json").read_text()
```

若需要輸出 Unicode 到 console：

```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```
