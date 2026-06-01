"""
fetch_etf_dividends.py
一次性抓取熱門 ETF 配息資料，存成 data/etf_dividend_data.csv
執行方式：python fetch_etf_dividends.py
"""
import os, time, requests, pandas as pd
from datetime import datetime, timedelta

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")

HOT_ETFS = [
    "0050","0056","006208",
    "00713","00878","00900","00907","00919","00929","00939",
    "00940","00941","00943","00944","00945","00946","00947",
    "00948","00949","00950","00951","00952","00953","00954",
    "00955","00956","00957","00958","00959","00960",
]

def fetch_etf_dividend(stock_id: str) -> pd.DataFrame:
    url   = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    params = {
        "dataset":    "TaiwanStockDividend",
        "data_id":    stock_id,
        "start_date": start,
        "token":      FINMIND_TOKEN,
    }
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = 65 * (attempt + 1)
                print(f"  ⏳ 頻率上限，等待 {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"  ❌ HTTP {r.status_code}")
                return pd.DataFrame()
            data = r.json().get("data", [])
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            df["stock_id"] = str(stock_id)
            return df
        except Exception as e:
            print(f"  ❌ 例外：{e}")
            time.sleep(5)
    return pd.DataFrame()

def main():
    all_dfs = []
    total = len(HOT_ETFS)
    for i, sid in enumerate(HOT_ETFS, 1):
        print(f"[{i:02d}/{total}] 抓取 {sid}...", end=" ")
        df = fetch_etf_dividend(sid)
        if df.empty:
            print("無資料")
        else:
            print(f"✅ {len(df)} 筆")
            all_dfs.append(df)
        time.sleep(1.5)  # 避免觸發頻率上限

    if not all_dfs:
        print("❌ 所有 ETF 均無資料，請確認 FINMIND_TOKEN 是否設定正確")
        return

    df_all = pd.concat(all_dfs, ignore_index=True)
    out = "data/etf_dividend_data.csv"
    df_all.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n✅ 完成！共 {len(df_all)} 筆，存至 {out}")
    print(f"   ETF 數：{df_all['stock_id'].nunique()} 檔")

if __name__ == "__main__":
    main()
