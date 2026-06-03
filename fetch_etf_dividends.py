"""
fetch_etf_dividends.py
用 yfinance 抓取 ETF 配息資料，存成 data/etf_dividend_data.csv
執行方式：python fetch_etf_dividends.py
"""
import sys, time, pandas as pd
import yfinance as yf
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HOT_ETFS = [
    "0050","0051","0052","0053","0055","0056","0057","006201","006203","006204",
    "006208","00660","00690","00692","00701","00702","00712","00713","00714",
    "00717","00728","00730","00731","00733","00735","00736","00739","00770",
    "00771","00830","00850","00851","00858","00878","00881","00882","00888",
    "00891","00892","00894","00896","00900","00901","00904","00905","00907",
    "00908","00909","00911","00912","00913","00915","00916","00917","00918",
    "00919","00920","00921","00922","00923","00926","00927","00928","00929",
    "00930","00932","00934","00935","00936","00938","00939","00940","00943",
    "00944","00946","00947","00951","00952","00956","00960","00961","00962",
    "00963","00964",
]

def fetch_etf_dividend(stock_id: str) -> pd.DataFrame:
    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(stock_id + suffix)
            div = tk.dividends
            if div is None or div.empty:
                continue
            df = div.reset_index()
            df.columns = ["ex_dividend_date", "CashDividend"]
            df["stock_id"] = stock_id
            df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"]).dt.tz_localize(None)
            cutoff = pd.Timestamp(datetime.now().year - 2, 1, 1)
            df = df[df["ex_dividend_date"] >= cutoff]
            if not df.empty:
                return df
        except Exception as e:
            print(f"  ERR {suffix}: {e}")
    return pd.DataFrame()

def main():
    all_dfs = []
    total = len(HOT_ETFS)
    for i, sid in enumerate(HOT_ETFS, 1):
        print(f"[{i:02d}/{total}] {sid}...", end=" ", flush=True)
        df = fetch_etf_dividend(sid)
        if df.empty:
            print("NO DATA")
        else:
            print(f"OK {len(df)}")
            all_dfs.append(df)
        time.sleep(0.5)

    if not all_dfs:
        print("ALL FAILED")
        return

    df_all = pd.concat(all_dfs, ignore_index=True)
    out = "data/etf_dividend_data.csv"
    df_all.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nDone! {len(df_all)} rows, {df_all['stock_id'].nunique()} ETFs -> {out}")

if __name__ == "__main__":
    main()
