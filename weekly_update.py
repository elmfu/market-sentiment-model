"""
weekly_update.py — 每週自動更新管線（純確定性，不呼叫任何 LLM）
================================================================
流程: discover → scrape(incremental) → validate → build → git commit/push
安全閘門:
  - 爬取後若「新增筆數 = 0」且未帶 --allow-empty → 中止，不 build 不 push
  - build 前驗證原始資料檔存在且非空 → 防止用虛構/舊資料重建 dashboard
  - push 前列出 staged 檔案寫入 log

用法:
  python weekly_update.py               # 完整流程（含 push）
  python weekly_update.py --no-push     # 跑完但不 push（首次測試用這個）
  python weekly_update.py --allow-empty # 沒新資料也繼續 build（除錯用）
  python weekly_update.py --dry-run     # 只印出將執行的步驟

排程 (Task Scheduler):
  程式:   "<repo>\\.venv\\Scripts\\python.exe"
  參數:   weekly_update.py
  起始於: <repo 根目錄>
"""

import argparse
import glob
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows console 可能預設 cp1252，強制 UTF-8 輸出避免 log 字元錯誤
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─── 設定（Claude Code：請依實際 repo 調整此區塊）──────────────────────────
REPO_ROOT = Path(__file__).parent.resolve()

# 依序執行的爬取步驟。required=False 的步驟失敗只記 warning，不中止整條管線。
# 若某個 scraper 被擋（exit != 0），加 --headed 再跑一次。
PIPELINE_STEPS = [
    # BBY CA — 公開 REST API，幾乎不會被擋
    {"name": "scrape_bby_ca",
     "cmd":  [sys.executable, "scraper/scrape_ca.py", "--incremental"],
     "required": True},
    # BBY US — Playwright；被擋時加 --headed 參數手動跑
    {"name": "scrape_bby_us",
     "cmd":  [sys.executable, "scraper/scrape_us.py", "--incremental", "--resume"],
     "required": False},
    # Walmart US — Playwright；無資料時失敗不中止
    {"name": "scrape_walmart",
     "cmd":  [sys.executable, "scraper/scrape_walmart.py", "--incremental"],
     "required": False},
]

# 爬取原始資料所在路徑（計算「本次新增筆數」與驗證非空用）
# 這些目錄在 .gitignore 裡（_private/），永不 commit
RAW_DATA_GLOBS = [
    "_private/raw_data/bby/**/*.json",      # BBY CA + US raw pages
    "_private/raw_data/walmart/**/*.json",  # Walmart（目錄不存在時 glob 傳回空，無害）
]

# Build 步驟：sanitize → consolidate → aggregate → manifest → validate
# 全部在資料閘門（驗證有新增筆數）之後執行
BUILD_STEPS = [
    {"name": "sanitize",
     "cmd":  [sys.executable, "scraper/sanitize.py"],
     "required": True},
    {"name": "consolidate",
     "cmd":  [sys.executable, "analysis/consolidate.py"],
     "required": True},
    {"name": "aggregate_series",
     "cmd":  [sys.executable, "analysis/aggregate_series.py"],
     "required": True},
    {"name": "build_manifest",
     "cmd":  [sys.executable, "analysis/build_manifest.py"],
     "required": True},
    {"name": "validate_output",
     "cmd":  [sys.executable, "analysis/validate_output.py"],
     "required": True},
]

# git 只允許 add 這些路徑（原始評論與 state 永不進公開 repo）
GIT_ADD_PATHS = ["docs/", "config/products.json"]
GIT_BRANCH = "main"
# ──────────────────────────────────────────────────────────────────────────

LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"weekly_{datetime.now():%Y%m%d_%H%M%S}.log"


def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(step: dict, dry_run: bool) -> bool:
    log(f"── STEP: {step['name']}  →  {' '.join(map(str, step['cmd']))}")
    if dry_run:
        return True
    try:
        r = subprocess.run(step["cmd"], cwd=REPO_ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
        for stream, tag in ((r.stdout, "OUT"), (r.stderr, "ERR")):
            if stream:
                for ln in stream.strip().splitlines()[-40:]:  # 只留尾段避免 log 爆量
                    log(f"   [{tag}] {ln}")
        if r.returncode != 0:
            log(f"   FAILED (exit {r.returncode})")
            return False
        return True
    except subprocess.TimeoutExpired:
        log("   FAILED (timeout 3h)")
        return False
    except FileNotFoundError as e:
        log(f"   FAILED (not found: {e})")
        return False


def count_raw_records() -> int:
    """統計原始爬取資料總筆數（list 長度加總；壞檔跳過並記 log）"""
    total = 0
    for pattern in RAW_DATA_GLOBS:
        for fp in glob.glob(str(REPO_ROOT / pattern), recursive=True):
            p = Path(fp)
            if p.name.startswith(".") or "dashboard" in p.parts or "context" in p.parts:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    total += len(data)
            except Exception as e:
                log(f"   [WARN] unreadable raw file {p.name}: {e}")
    return total


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--allow-empty", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log(f"=== WEEKLY UPDATE START === repo={REPO_ROOT}")

    # 0. 前置檢查
    if not (REPO_ROOT / ".git").exists():
        log("ABORT: 不在 git repo 根目錄。Task Scheduler 的『起始於』必須設成 repo 根目錄。")
        sys.exit(2)

    baseline = count_raw_records()
    log(f"Baseline raw records: {baseline:,}")

    # 1. 發現 + 爬取
    for step in PIPELINE_STEPS:
        ok = run(step, args.dry_run)
        if not ok and step["required"]:
            log("ABORT: 必要爬取步驟失敗，停止（不 build、不 push）。")
            sys.exit(1)

    # 2. 資料驗證閘門
    if not args.dry_run:
        after = count_raw_records()
        delta = after - baseline
        log(f"Raw records after scrape: {after:,}  (new: +{delta:,})")
        if after == 0:
            log("ABORT: 原始資料為空 — 禁止在無資料狀態下建 dashboard。")
            sys.exit(1)
        if delta <= 0 and not args.allow_empty:
            log("STOP: 本次無新增資料。dashboard 已是最新，跳過 build 與 push。"
                "（若要強制重建請加 --allow-empty）")
            sys.exit(0)

    # 3. Build
    for step in BUILD_STEPS:
        ok = run(step, args.dry_run)
        if not ok and step["required"]:
            log("ABORT: build 失敗，停止（不 push）。")
            sys.exit(1)

    # 4. Git 發佈
    if args.dry_run:
        log(f"[DRY RUN] would git add {GIT_ADD_PATHS} && commit && push")
        log("=== DONE (dry run) ===")
        return

    for p in GIT_ADD_PATHS:
        if (REPO_ROOT / p).exists():
            git("add", p)
    staged = git("diff", "--staged", "--stat").stdout.strip()
    if not staged:
        log("Nothing staged — no changes to publish. DONE.")
        return
    log("Staged changes:")
    for ln in staged.splitlines():
        log(f"   {ln}")

    msg = f"data: weekly refresh {datetime.now():%Y-%m-%d}"
    r = git("commit", "-m", msg)
    log(r.stdout.strip() or r.stderr.strip())

    if args.no_push:
        log("--no-push: commit 已建立但未推送。確認無誤後手動 git push。")
    else:
        r = git("push", "origin", GIT_BRANCH)
        log((r.stdout + r.stderr).strip())
        if r.returncode != 0:
            log("PUSH FAILED — commit 保留在本機，請檢查認證/網路後手動 push。")
            sys.exit(1)

    log("=== WEEKLY UPDATE DONE ===")


if __name__ == "__main__":
    main()
