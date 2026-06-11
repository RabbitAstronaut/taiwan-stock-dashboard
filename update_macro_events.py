"""
update_macro_events.py
GitHub Actions 每月 1 號執行：自動抓取未來 180 天內重大總經事件，
清洗去重後覆寫 data/macro_events.json，並 commit 回倉庫。
"""

import json
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ_TW = ZoneInfo("Asia/Taipei")

today    = datetime.now(TZ_TW).date()
end_date = today + timedelta(days=180)  # 往後 6 個月

# ── 固定已知核心事件（含 2026~2027 全年 FOMC + 台積電法說）
FIXED_EVENTS = [
    # 2026 FOMC
    {"date": "2026-06-18", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-07-30", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-09-17", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-11-05", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-12-10", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    # 2026 台積電法說
    {"date": "2026-07-17", "event": "台積電 Q2 法說會",     "country": "🇹🇼", "level": 3},
    {"date": "2026-10-16", "event": "台積電 Q3 法說會",     "country": "🇹🇼", "level": 3},
    # 2027 FOMC（預估，每年 1/3/5/6/7/9/10/11/12 月）
    {"date": "2027-01-29", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-03-18", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-05-06", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-06-17", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-07-29", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-09-16", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-10-28", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2027-12-09", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    # 2027 台積電法說（預估）
    {"date": "2027-01-16", "event": "台積電 Q4 法說會",     "country": "🇹🇼", "level": 3},
    {"date": "2027-04-15", "event": "台積電 Q1 法說會",     "country": "🇹🇼", "level": 3},
    {"date": "2027-07-15", "event": "台積電 Q2 法說會",     "country": "🇹🇼", "level": 3},
    {"date": "2027-10-15", "event": "台積電 Q3 法說會",     "country": "🇹🇼", "level": 3},
]


def fetch_us_cpi_dates():
    """
    推算未來 6 個月的美國 CPI 發布日（每月第三週三）。
    BLS 固定時程，永久有效。
    """
    rows = []
    for offset in range(0, 7):
        month = (today.month + offset - 1) % 12 + 1
        year  = today.year + (today.month + offset - 1) // 12
        from datetime import date, timedelta as td
        first_day   = date(year, month, 1)
        days_to_wed = (2 - first_day.weekday()) % 7
        third_wed   = first_day + td(days=days_to_wed + 14)
        if today <= third_wed <= end_date:
            rows.append({
                "date":    str(third_wed),
                "event":   f"美國 {year}/{month:02d} CPI 通膨數據",
                "country": "🇺🇸",
                "level":   3,
            })
    return rows


def merge_and_deduplicate(all_events):
    """合併去重，只保留今日起 180 天內的未來事件。"""
    seen   = set()
    result = []
    for ev in sorted(all_events, key=lambda x: x["date"]):
        if ev["date"] < str(today):
            continue
        if ev["date"] > str(end_date):
            continue
        key = ev["date"] + ev["event"][:10]
        if key in seen:
            continue
        seen.add(key)
        result.append(ev)
    return result


def main():
    print(f"[macro] 今日：{today}，截止：{end_date}")
    all_events  = FIXED_EVENTS + fetch_us_cpi_dates()
    events      = merge_and_deduplicate(all_events)
    print(f"[macro] 清洗後共 {len(events)} 筆")
    for ev in events:
        print(f"  {ev['date']} {ev['country']} {ev['event']}")

    os.makedirs("data", exist_ok=True)
    with open("data/macro_events.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": str(today), "events": events}, f, ensure_ascii=False, indent=2)
    print("[macro] 已寫入 data/macro_events.json")


if __name__ == "__main__":
    main()
