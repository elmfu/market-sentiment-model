# 每週更新 Runbook — BBY + Walmart

一頁式操作卡。全部在 PowerShell、專案根目錄執行。

```powershell
cd "C:\Users\hp\Side Project\reddit-sentiment"
```

---

## 0. 只有這次要做：清掉換行雜訊（一次性）

新增了 `.gitattributes`，讓 `git status` 不再出現 300 多個「假變更」。
第一次要手動正規化一次：

```powershell
git add .gitattributes
git commit -m "chore: normalize line endings (.gitattributes)"
git add --renormalize .
git commit -m "chore: renormalize line endings across repo"
```

之後 `git status --short` 就只會顯示**真正**有變的檔案，push 前的檢查才看得懂。

---

## 1. 跑更新（約 15–20 分鐘）

```powershell
python run_weekly.py
```

會依序跑：scrape（增量）→ sanitize → consolidate → aggregate → build manifest → validate。
**它永遠不會 push**，跑完會印出給你檢查的 git 指令。

### 變化型

| 情境 | 指令 |
|---|---|
| Walmart 被擋（log 出現 `[BLOCKED]`） | `python run_weekly.py --only walmart --headed` |
| 懷疑有新 SKU 上架 | `python run_weekly.py --discover` |
| 只重建 dashboard，不重抓 | `python run_weekly.py --skip-scrape` |
| 只補某一邊 | `python run_weekly.py --only bby` / `--only ca` / `--only us` |

---

## 2. 檢查（push 前必做）

```powershell
git status --short
```

確認：**沒有** `_private/` 或 `.env` 出現在清單裡。

然後請 Claude 核對筆數（貼 run_weekly 的輸出即可），或自己看：
- 每個來源累計筆數只增不減
- `docs/data/manifest.json` 的 `generated_at` 是今天

⚠️ 若任一來源「累計筆數 < 上週」→ **停下來問**，不要 push。

---

## 3. Push

```powershell
git add docs/ config/products.json
git commit -m "data: weekly refresh YYYY-MM-DD"
git push origin main
```

GitHub Pages 會自動重佈，約 1–2 分鐘後 https://elmfu.github.io/market-sentiment-model/ 生效。

驗證線上是否更新：開 https://elmfu.github.io/market-sentiment-model/data/manifest.json
看 `generated_at` 是否為今天（瀏覽器可能要 Ctrl+F5 強制重整）。

---

## 4.（選用）語意搜尋索引同步

只有在 Vectorize 已建好、worker 已部署後才需要：

```powershell
cd worker
node reindex.js --token 你的API_TOKEN
cd ..
```

⚠️ 一定要在 **push 之後**跑——索引是從 GitHub 上的資料建的，先 push 才抓得到新評論。

---

## 排程沒跑怎麼辦

症狀：`logs/` 沒有本週的檔案、dashboard 的 `generated_at` 落後好幾週。

檢查 Task Scheduler：
1. 開「工作排程器」→ 找到這個任務
2. 看「上次執行結果」——`0x0` 才是成功
3. 常見原因與對策：

| 原因 | 對策 |
|---|---|
| 排程時間電腦關機／睡眠 | 勾選「若錯過排定的啟動時間，盡快啟動工作」 |
| 電腦在電池模式 | 取消勾選「只有在使用 AC 電源時才啟動」 |
| 任務被停用 | 右鍵 → 啟用 |
| 路徑錯誤 | 「起始於」要填 `C:\Users\hp\Side Project\reddit-sentiment` |

**手動補跑就是第 1 步的 `python run_weekly.py`**——增量爬取會自動把落掉幾週的評論一次補齊，不需要特別處理。

---

## 設定備忘

- **Task Scheduler 程式**：`C:\Users\hp\Side Project\reddit-sentiment\run_weekly.bat`
- **參數**：留空（排程模式含自動 push）
- **起始於**：`C:\Users\hp\Side Project\reddit-sentiment`
- 互動式手動跑時用 `python run_weekly.py`（不 push，由你檢查後再 push）
