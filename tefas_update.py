"""
Fetch TEFAS fund prices for the portfolio workbook.

Setup (once):   pip install tefas-crawler pandas
Normally called by Update-Portfolio.ps1, which passes the fund codes it reads
from the workbook. Manual run:

    python tefas_update.py --funds FFH TCA KZL GOL KTT CKS KIS

Writes two files next to this script (or into --outdir):
  tefas_latest.csv   one row per fund: latest price, previous price, daily change
  tefas_history.csv  every price ever fetched (appended, de-duplicated)

If nothing could be fetched the existing files are left untouched and the
exit code is 1, so the workbook keeps the last known prices.
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

LOOKBACK_DAYS = 10   # wide enough to span weekends and Turkish holidays
RETRIES = 3


def to_number(x):
    """Accept 41.30, '41.30', and Turkish-formatted '1.234,5678'."""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def fetch_fund(crawler, code, pause):
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    end = date.today().isoformat()
    for attempt in range(1, RETRIES + 1):
        try:
            df = crawler.fetch(start=start, end=end, name=code,
                               columns=["code", "date", "price"])
            if df is not None and not df.empty:
                df = df.copy()
                df["code"] = code
                df["date"] = pd.to_datetime(df["date"]).dt.date
                df["price"] = df["price"].map(to_number)
                return df.dropna(subset=["price"])
            print(f"{code}: empty result (attempt {attempt})", file=sys.stderr)
        except Exception as exc:  # network, bot protection, API change...
            print(f"{code}: {exc} (attempt {attempt})", file=sys.stderr)
        time.sleep(pause * attempt)
    return pd.DataFrame(columns=["code", "date", "price"])


def latest_table(history):
    rows = []
    for code, g in history.groupby("code"):
        g = g.sort_values("date")
        last = g.iloc[-1]
        prev = g.iloc[-2] if len(g) > 1 else None
        rows.append({
            "code": code,
            "date": last["date"],
            "price_try": last["price"],
            "prev_date": prev["date"] if prev is not None else "",
            "prev_price_try": prev["price"] if prev is not None else "",
            "daily_change": (last["price"] / prev["price"] - 1) if prev is not None else "",
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--funds", nargs="+", required=True)
    ap.add_argument("--outdir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--pause", type=float, default=8.0,
                    help="seconds between requests (TEFAS limits ~6/min)")
    args = ap.parse_args()
    out = Path(args.outdir)

    from tefas import Crawler  # imported late so --help works without it
    crawler = Crawler()

    frames, failed = [], []
    requested = list(dict.fromkeys(c.strip().upper() for c in args.funds))
    for i, code in enumerate(requested):
        if i:
            time.sleep(args.pause)
        df = fetch_fund(crawler, code, args.pause)
        (frames if not df.empty else failed).append(df if not df.empty else code)

    if failed:
        print("FAILED: " + ", ".join(failed), file=sys.stderr)
    if not frames:
        print("Nothing fetched; files left untouched.", file=sys.stderr)
        return 1

    new = pd.concat(frames, ignore_index=True)
    hist_path = out / "tefas_history.csv"
    if hist_path.exists():
        old = pd.read_csv(hist_path)
        old["date"] = pd.to_datetime(old["date"]).dt.date
        new = pd.concat([old, new], ignore_index=True)
    history = (new.drop_duplicates(subset=["code", "date"], keep="last")
                  .sort_values(["code", "date"]))
    history.to_csv(hist_path, index=False)

    # Built from the full history, so a fund that failed today still shows its
    # last known price instead of disappearing from the workbook.
    latest = latest_table(history[history["code"].isin(requested)])
    latest.to_csv(out / "tefas_latest.csv", index=False)
    print(f"OK: {len(latest)} funds fetched" + (f", {len(failed)} failed" if failed else ""))
    return 2 if failed else 0   # 2 = partial success


if __name__ == "__main__":
    sys.exit(main())
