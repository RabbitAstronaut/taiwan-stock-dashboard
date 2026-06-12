"""
update_macro_events.py
GitHub Actions 每月 1 號執行：自動抓取未來 180 天內重大總經與財報事件，
清洗去重後覆寫 data/macro_events.json，並 commit 回倉庫。
"""

import json
import os
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

TZ_TW    = ZoneInfo("Asia/Taipei")
today    = datetime.now(TZ_TW).date()
end_date = today + timedelta(days=180)

# ── 固定核心事件（FOMC + 台積電法說 2026~2027）
FIXED_EVENTS = [
    # 2026 FOMC
    {"date": "2026-06-18", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2026-07-30", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2026-09-17", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2026-11-05", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2026-12-10", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    # 2026 台積電法說
    {"date": "2026-07-17", "event": "台積電 Q2 法說會",       "country": "🇹🇼", "level": 3},
    {"date": "2026-10-16", "event": "台積電 Q3 法說會",       "country": "🇹🇼", "level": 3},
    # 2027 FOMC
    {"date": "2027-01-29", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-03-18", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-05-06", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-06-17", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-07-29", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-09-16", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-10-28", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    {"date": "2027-12-09", "event": "聯準會 FOMC 利率決議",  "country": "🇺🇸", "level": 3},
    # 2027 台積電法說
    {"date": "2027-01-16", "event": "台積電 Q4 法說會",       "country": "🇹🇼", "level": 3},
    {"date": "2027-04-15", "event": "台積電 Q1 法說會",       "country": "🇹🇼", "level": 3},
    {"date": "2027-07-15", "event": "台積電 Q2 法說會",       "country": "🇹🇼", "level": 3},
    {"date": "2027-10-15", "event": "台積電 Q3 法說會",       "country": "🇹🇼", "level": 3},
]

# ── 重要美股財報觀察清單
US_EARNINGS_WATCHLIST = {
    "NVDA":  "Nvidia 財報",
    "AMD":   "AMD 財報",
    "AVGO":  "博通 Broadcom 財報",
    "QCOM":  "高通 Qualcomm 財報",
    "AAPL":  "Apple 財報",
    "MSFT":  "Microsoft 財報",
    "META":  "Meta 財報",
    "GOOGL": "Google 財報",
    "AMZN":  "Amazon 財報",
    "TSM":   "台積電 ADR 財報",
    "AMAT":  "應用材料 AMAT 財報",
    "ASML":  "ASML 財報",
    "ARM":   "ARM Holdings 財報",
}

# ── 台灣重要法說觀察清單（公司代號：名稱）
TW_EARNINGS_WATCHLIST = {
    "2454": "聯發科法說會",
    "2330": "台積電法說會",
    "2317": "鴻海法說會",
    "2308": "台達電法說會",
    "3034": "聯詠法說會",
    "2379": "瑞昱法說會",
    "6770": "力積電法說會",
    "2303": "聯電法說會",
}


def fetch_us_earnings(watchlist: dict) -> list:
    """
    從 Yahoo Finance 爬取美股財報日期。
    只抓未來 180 天內的財報，失敗時靜默跳過。
    """
    rows = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for ticker, name in watchlist.items():
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            earnings = (data.get("quoteSummary", {})
                           .get("result", [{}])[0]
                           .get("calendarEvents", {})
                           .get("earnings", {}))
            # earningsDate 是一個 list，取第一個
            dates = earnings.get("earningsDate", [])
            for d in dates[:2]:  # 最多取 2 個日期（有時有區間）
                raw = d.get("raw")
                if raw:
                    ev_date = date.fromtimestamp(raw)
                    if today <= ev_date <= end_date:
                        rows.append({
                            "date":    str(ev_date),
                            "event":   name,
                            "country": "🇺🇸",
                            "level":   3,
                        })
                        break  # 只取第一個有效日期
        except Exception as e:
            print(f"[earnings] {ticker} 抓取失敗（靜默）：{e}")
    return rows


def fetch_tw_earnings(watchlist: dict) -> list:
    """
    從 Yahoo Finance 爬取台股法說日期（ADR 或 .TW 後綴）。
    失敗時靜默跳過。
    """
    rows = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for code, name in watchlist.items():
        ticker = f"{code}.TW"
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            earnings = (data.get("quoteSummary", {})
                           .get("result", [{}])[0]
                           .get("calendarEvents", {})
                           .get("earnings", {}))
            dates = earnings.get("earningsDate", [])
            for d in dates[:2]:
                raw = d.get("raw")
                if raw:
                    ev_date = date.fromtimestamp(raw)
                    if today <= ev_date <= end_date:
                        rows.append({
                            "date":    str(ev_date),
                            "event":   name,
                            "country": "🇹🇼",
                            "level":   3,
                        })
                        break
        except Exception as e:
            print(f"[earnings] {ticker} 抓取失敗（靜默）：{e}")
    return rows


def fetch_us_cpi_dates() -> list:
    """推算未來 6 個月的美國 CPI 發布日（每月第三週三）。"""
    rows = []
    for offset in range(0, 7):
        month = (today.month + offset - 1) % 12 + 1
        year  = today.year + (today.month + offset - 1) // 12
        first_day   = date(year, month, 1)
        days_to_wed = (2 - first_day.weekday()) % 7
        third_wed   = first_day + timedelta(days=days_to_wed + 14)
        if today <= third_wed <= end_date:
            rows.append({
                "date":    str(third_wed),
                "event":   f"美國 {year}/{month:02d} CPI 通膨數據",
                "country": "🇺🇸",
                "level":   3,
            })
    return rows


def merge_and_deduplicate(all_events: list) -> list:
    """合併去重，只保留今日起 180 天內的未來事件，按日期升序。"""
    seen   = set()
    result = []
    for ev in sorted(all_events, key=lambda x: x["date"]):
        if ev["date"] < str(today) or ev["date"] > str(end_date):
            continue
        key = ev["date"] + ev["event"][:10]
        if key in seen:
            continue
        seen.add(key)
        result.append(ev)
    return result


def main():
    print(f"[macro] 今日：{today}，截止：{end_date}")

    all_events = []
    all_events += FIXED_EVENTS

    print("[macro] 抓取美股財報日期...")
    us_earnings = fetch_us_earnings(US_EARNINGS_WATCHLIST)
    print(f"[macro] 美股財報：{len(us_earnings)} 筆")
    all_events += us_earnings

    print("[macro] 抓取台股法說日期...")
    tw_earnings = fetch_tw_earnings(TW_EARNINGS_WATCHLIST)
    print(f"[macro] 台股法說：{len(tw_earnings)} 筆")
    all_events += tw_earnings

    all_events += fetch_us_cpi_dates()

    events = merge_and_deduplicate(all_events)
    print(f"[macro] 清洗後共 {len(events)} 筆")
    for ev in events:
        print(f"  {ev['date']} {ev['country']} {ev['event']}")

    os.makedirs("data", exist_ok=True)
    # 嘗試從 FinMind 抓最新 CPI 年增率
    latest_cpi = None
    try:
        r = requests.get("https://api.finmindtrade.com/api/v4/data",
                         params={"dataset":"USEconomicIndex","data_id":"CPIAUCSL",
                                 "start_date": str(today - timedelta(days=60))},
                         timeout=10)
        if r.status_code == 200:
            rows = r.json().get("data", [])
            if rows:
                latest_cpi = round(float(rows[-1].get("value", 0)), 1)
                print(f"[macro] CPI 年增率：{latest_cpi}%")
    except Exception as e:
        print(f"[macro] CPI 抓取失敗（靜默）：{e}")

    with open("data/macro_events.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": str(today), "events": events,
                   "latest_cpi": latest_cpi}, f, ensure_ascii=False, indent=2)
    print("[macro] 已寫入 data/macro_events.json")


if __name__ == "__main__":
    main()
