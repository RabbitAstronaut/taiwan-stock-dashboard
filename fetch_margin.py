"""
fetch_margin.py
全市場融資餘額抓取腳本（來源：台灣證交所信用交易統計彙總）
每日 daily_scan.py 排程後執行，或手動執行產生初始資料。
"""
import requests, json, os, re
from datetime import datetime

DATA_DIR   = "data"
OUTPUT_PATH = os.path.join(DATA_DIR, "margin_summary.json")

def fetch_and_save():
    url = ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
           "?response=json&selectType=MS")
    try:
        r = requests.get(url, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"})
        print(f"HTTP {r.status_code}, 長度={len(r.text)}")
        j = r.json()
        print(f"stat={j.get('stat')}, date={j.get('date')}")

        raw_date = j.get("date", "")
        date_str = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    if len(raw_date) == 8 else raw_date)

        for tbl in j.get("tables", []):
            for row in tbl.get("data", []):
                if "融資金額" in str(row[0]):
                    print(f"融資金額列：{row}")
                    raw_val = str(row[-1]).replace(",", "").strip()
                    balance_qian = float(raw_val)
                    balance_yi   = round(balance_qian / 100_000, 0)
                    result = {
                        "balance_yi":  balance_yi,
                        "balance_raw": balance_qian,
                        "date":        date_str,
                    }
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    print(f"✅ 已寫入 {OUTPUT_PATH}")
                    print(f"   融資餘額：{balance_yi:,.0f} 億元（{date_str}）")
                    return True

        print("❌ 找不到融資金額列")
        return False

    except Exception as e:
        print(f"❌ 抓取失敗：{e}")
        return False

if __name__ == "__main__":
    fetch_and_save()
