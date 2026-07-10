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
PY = sys.executable

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

    if not args.skip_scrape:
        if only in (None, "bby", "ca"):
            run("Scrape Best Buy CA (incremental)", "scraper/scrape_ca.py", "--incremental", optional=True)
        if only in (None, "bby", "us"):
            run("Scrape Best Buy US (incremental)", "scraper/scrape_us.py", "--incremental", "--resume", *headed, optional=True)
        if only in (None, "walmart"):
            run("Scrape Walmart US (incremental)", "scraper/scrape_walmart.py", "--incremental", *headed, optional=True)

    run("Sanitize raw → docs/data (PII-stripped)", "scraper/sanitize.py")
    run("Consolidate per-product dashboards", "analysis/consolidate.py")
    run("Aggregate series + all overviews", "analysis/aggregate_series.py")
    run("Rebuild manifest", "analysis/build_manifest.py")
    run("Validate output schema", "analysis/validate_output.py", optional=True)

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
