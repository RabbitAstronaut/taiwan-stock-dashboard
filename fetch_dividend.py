"""
fetch_dividend.py
抓取未來 21 天除權息資料，存入 data/dividend_data.json

策略：
1. TWTBAU1（TWSE OpenAPI）→ 取得未來停止過戶名單（有未來除息日）
2. FinMind TaiwanStockDividend（免費版）→ 查各股最近一次現金股利
3. 合併後存入 JSON，殖利率用「最近一次股利 / 現價」估算

執行：python fetch_dividend.py
"""

import requests, json, os, re, time
from datetime import date, timedelta

LOOKAHEAD_DAYS = 21
OUTPUT_PATH    = os.path.join("data", "dividend_data.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0",
    "Referer":    "https://www.twse.com.tw/",
}
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def roc7_to_iso(s):
    """民國年7碼 '1150713' → '2026-07-13'"""
    s = str(s).strip()
    if re.match(r'^\d{7}$', s):
        y = int(s[:3]) + 1911
        return f"{y}-{s[3:5]}-{s[5:7]}"
    return None


def fetch_upcoming_stocks():
    """
    TWTBAU1 → 未來21天除息股票清單
    回傳：[{"stock_id":..., "name":..., "ex_date":..., "market":...}, ...]
    """
    url   = "https://openapi.twse.com.tw/v1/exchangeReport/TWTBAU1"
    today = date.today()
    end   = today + timedelta(days=LOOKAHEAD_DAYS)
    rows  = []
    seen  = set()

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        print(f"  TWTBAU1: {len(data)} 筆停止過戶記錄")

        for row in data:
            reason = row.get("Reason", "")
            if not any(k in reason for k in ["除息", "除權息", "分配收益"]):
                continue

            # StartDate 前一個交易日 ≈ 除息日，這裡直接用 StartDate 估算
            ex_date_str = roc7_to_iso(row.get("StartDate", ""))
            if not ex_date_str:
                continue
            try:
                ex_date = date.fromisoformat(ex_date_str)
            except Exception:
                continue

            if ex_date < today or ex_date > end:
                continue

            sid = str(row.get("Code", "")).strip()
            if not sid or (sid, ex_date_str) in seen:
                continue
            seen.add((sid, ex_date_str))

            rows.append({
                "stock_id": sid,
                "name":     str(row.get("Name", "")).strip(),
                "ex_date":  ex_date_str,
                "market":   "上市",
            })

    except Exception as e:
        print(f"  TWTBAU1 錯誤: {e}")

    return rows


def fetch_last_cash_dividend(stock_id):
    """
    FinMind TaiwanStockDividend → 該股最近一次現金股利
    回傳：(cash_div, stock_div) 或 (None, None)
    """
    # 查近2年資料，取最新一筆
    start = (date.today() - timedelta(days=730)).strftime("%Y-%m-%d")
    try:
        r = requests.get(FINMIND_URL, params={
            "dataset":   "TaiwanStockDividend",
            "data_id":   stock_id,
            "start_date": start,
        }, timeout=12)
        data = r.json().get("data", [])
        if not data:
            return None, None

        # 取有現金除息日的最新一筆
        cash_rows = [
            d for d in data
            if d.get("CashExDividendTradingDate") and
               float(d.get("CashEarningsDistribution", 0) or 0) > 0
        ]
        if not cash_rows:
            return None, None

        latest = sorted(cash_rows, key=lambda x: x["CashExDividendTradingDate"], reverse=True)[0]
        cash  = float(latest.get("CashEarningsDistribution", 0) or 0)
        stock = float(latest.get("StockEarningsDistribution", 0) or 0)
        return cash, stock

    except Exception as e:
        return None, None


def main():
    today = date.today()
    print(f"🔄 更新除權息資料：{today} ~ {today + timedelta(days=LOOKAHEAD_DAYS)}")

    # Step 1：取得未來21天除息名單
    print("\n[Step 1] TWTBAU1 未來除息名單...")
    upcoming = fetch_upcoming_stocks()
    print(f"  找到 {len(upcoming)} 檔即將除息")

    if not upcoming:
        print("  ⚠️ 無資料，結束")
        return

    # Step 2：逐檔查 FinMind 最近一次現金股利
    print(f"\n[Step 2] FinMind 查詢各股最近股利（共 {len(upcoming)} 檔）...")
    rows = []
    for i, item in enumerate(upcoming):
        sid = item["stock_id"]
        cash_div, stock_div = fetch_last_cash_dividend(sid)
        rows.append({
            "stock_id":  sid,
            "name":      item["name"],
            "ex_date":   item["ex_date"],
            "cash_div":  cash_div,
            "stock_div": stock_div,
            "market":    item["market"],
        })
        status = f"現金:{cash_div:.2f}" if cash_div else "無股利資料"
        print(f"  [{i+1}/{len(upcoming)}] {sid} {item['name']}: {status}")
        time.sleep(0.3)  # FinMind 免費版限速

    # Step 3：存檔
    os.makedirs("data", exist_ok=True)
    has_div = sum(1 for r in rows if r["cash_div"])
    output  = {
        "updated_at": today.strftime("%Y-%m-%d"),
        "count":      len(rows),
        "data":       rows,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！共 {len(rows)} 檔，有股利資料 {has_div} 檔，存入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
