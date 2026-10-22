#!/usr/bin/env python3
"""
Weekly refresh — one command to update all reviews, rebuild the dashboard,
and stage everything for a single git push.

    python run_weekly.py                # full incremental refresh (all retailers)
    python run_weekly.py --discover     # also re-scan for NEW SKUs first
    python run_weekly.py --only bby      # bby | walmart | ca | us
    python run_weekly.py --headed       # visible browser if a scraper is blocked
    python run_weekly.py --skip-scrape  # only re-consolidate + rebuild (no fetch)

Pipeline order:
  [discover] → scrape (incremental) → sanitize → consolidate
  → aggregate_series → build_manifest → validate

It NEVER pushes. After a clean run it prints the exact git commands to review
and push yourself. Raw scrapes + state stay in _private/ (gitignored).
"""
import argparse, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent

def _pick_python() -> str:
    """Prefer the project venv over whatever interpreter launched this script.

    Running `python run_weekly.py` with system Python used to propagate that
    interpreter to every child process via sys.executable — and system Python
    has no requests/playwright, so all three scrapers died with
    ModuleNotFoundError while the build happily continued on stale raw data
    (2026-10-22). Resolve the venv explicitly instead.
    """
    for cand in (ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
                 ROOT / ".venv" / "bin" / "python"):          # POSIX
        if cand.exists():
            return str(cand)
    return sys.executable

PY = _pick_python()
if Path(PY) != Path(sys.executable):
    print(f"ℹ  Using project venv interpreter: {PY}")

def _preflight():
    """Fail fast and loud if the chosen interpreter can't import the scrapers'
    dependencies — better than three ModuleNotFoundError tracebacks that the
    pipeline then shrugs off as 'optional'."""
    probe = "import requests, playwright"
    r = subprocess.run([PY, "-c", probe], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"\n{'='*66}")
        print("ABORT: 爬蟲相依套件缺失 — 禁止在無法抓取的狀態下建 dashboard")
        print(f"{'='*66}")
        print(f"  Interpreter : {PY}")
        print(f"  Missing     : {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'requests / playwright'}")
        print("\n  修復方式：")
        print(f'    & "{ROOT}\\.venv\\Scripts\\python.exe" -m pip install -r requirements.txt')
        print(f'    & "{ROOT}\\.venv\\Scripts\\python.exe" -m playwright install chromium')
        print("\n  或直接用 venv 的 python 執行：")
        print(f'    & "{ROOT}\\.venv\\Scripts\\python.exe" run_weekly.py')
        sys.exit(1)

def run(desc, *cmd, cwd=ROOT, optional=False):
    print(f"\n{'='*66}\n▶ {desc}\n{'='*66}")
    r = subprocess.run([PY, *cmd], cwd=str(cwd))
    if r.returncode != 0:
        if optional:
            print(f"  ⚠ {desc} exited {r.returncode} — continuing (optional step)")
        else:
            print(f"\n✗ {desc} failed (exit {r.returncode}). Fix and re-run.")
            sys.exit(r.returncode)
    return r.returncode

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="re-scan retailers for NEW SKUs first")
    ap.add_argument("--only", choices=["bby", "walmart", "ca", "us"], help="limit scraping to one source")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-scrape", action="store_true", help="rebuild only, no fetching")
    args = ap.parse_args()
    t0 = time.time()
    only = args.only
    headed = ["--headed"] if args.headed else []

    if args.discover and not args.skip_scrape:
        # SKU discovery is dry-run first — new products land disabled for review
        run("Discover new SKUs (Best Buy)", "scraper/discover_skus.py", "--dry-run", *headed, optional=True)
        print("\n  ℹ  If discovery listed NEW SKUs you want, enable them in config/products.json,")
        print("     then re-run without --discover. Continuing with existing enabled products…\n")

    scrape_results = {}   # name -> exit code, for the 鐵則 #1 gate below

    if not args.skip_scrape:
        _preflight()
        if only in (None, "bby", "ca"):
            scrape_results["BBY CA"] = run("Scrape Best Buy CA (incremental)", "scraper/scrape_ca.py", "--incremental", optional=True)
        if only in (None, "bby", "us"):
            # NOTE: do NOT pass --resume here. Weekly runs are incremental; --resume
            # skips any SKU whose state has completed=True, which every US SKU gets
            # after its first successful scrape — that silently froze US at +0 for
            # weeks (2026-07-05 → 2026-08-24). --resume is for manually resuming an
            # interrupted FIRST full scrape only.
            scrape_results["BBY US"] = run("Scrape Best Buy US (incremental)", "scraper/scrape_us.py", "--incremental", *headed, optional=True)
        if only in (None, "walmart"):
            scrape_results["Walmart US"] = run("Scrape Walmart US (incremental)", "scraper/scrape_walmart.py", "--incremental", *headed, optional=True)

        # ── 鐵則 #1 gate ──────────────────────────────────────────────────────
        # If EVERY attempted scraper failed, there is no new raw data this run.
        # Building anyway silently republishes the previous week's numbers under
        # today's date — exactly what happened on 2026-10-22. Abort instead.
        if scrape_results and all(rc != 0 for rc in scrape_results.values()):
            print(f"\n{'='*66}")
            print("ABORT: 原始資料為空 — 禁止在無資料狀態下建 dashboard")
            print(f"{'='*66}")
            for name, rc in scrape_results.items():
                print(f"  {name:<12} exit {rc}")
            print("\n  所有爬取步驟皆失敗，本次沒有新的原始資料。")
            print("  已跳過 sanitize / consolidate / aggregate / manifest，docs/ 未被修改。")
            print("  修好爬蟲後重跑；若只是要用既有資料重建，明確加上 --skip-scrape。")
            sys.exit(1)
        if any(rc != 0 for rc in scrape_results.values()):
            failed = [n for n, rc in scrape_results.items() if rc != 0]
            print(f"\n  ⚠ 部分來源失敗：{', '.join(failed)} — 繼續 build，但這些來源本次為 +0")

    run("Sanitize raw → docs/data (PII-stripped)", "scraper/sanitize.py")
    run("Consolidate per-product dashboards", "analysis/consolidate.py")
    run("Aggregate series + all overviews", "analysis/aggregate_series.py")
    run("Rebuild manifest", "analysis/build_manifest.py")
    run("Validate output schema", "analysis/validate_output.py")

    mins = (time.time() - t0) / 60
    print(f"\n{'='*66}\n✓ Weekly refresh done in {mins:.1f} min.")
    print(f"{'='*66}\nNext — review & push yourself:\n")
    print('  git status --short')
    print('  # confirm NO _private/ or .env lines appear')
    print('  git add docs/ config/products.json')
    print('  git commit -m "data: weekly refresh %s"' % time.strftime("%Y-%m-%d"))
    print('  git push origin main\n')
    print("GitHub Pages redeploys automatically after push.\n")

if __name__ == "__main__":
    main()
