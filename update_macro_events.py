"""
update_macro_events.py
GitHub Actions 每月定時執行：自動抓取未來一個月內重大總經事件，
清洗去重後覆寫 data/macro_events.json，並 commit 回倉庫。

執行方式：python update_macro_events.py
"""

import json
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ_TW = ZoneInfo("Asia/Taipei")

# ── 台灣時區今日與未來 35 天區間
today = datetime.now(TZ_TW).date()
end_date = today + timedelta(days=35)

# ── 固定已知的核心重大事件（手動維護表，永久不過期）
# 格式：{"date": "YYYY-MM-DD", "event": "事件名稱", "country": "🇺🇸/🇹🇼", "level": 3}
FIXED_EVENTS = [
    # 聯準會 FOMC（2026年）
    {"date": "2026-06-18", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-07-30", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-09-17", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-11-05", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    {"date": "2026-12-10", "event": "聯準會 FOMC 利率決議", "country": "🇺🇸", "level": 3},
    # 台積電法說會（季報後約 3-4 週）
    {"date": "2026-07-17", "event": "台積電 Q2 法說會", "country": "🇹🇼", "level": 3},
    {"date": "2026-10-16", "event": "台積電 Q3 法說會", "country": "🇹🇼", "level": 3},
]

def fetch_finmind_economic_events():
    """
    嘗試從 FinMind 抓取美國總經事件（USEconomicIndex）。
    失敗時靜默返回空列表，不影響主流程。
    """
    rows = []
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "USEconomicIndex",
            "start_date": str(today),
            "end_date":   str(end_date),
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return rows
        data = r.json().get("data", [])

        # 只保留三星級重大事件關鍵字
        keywords = ["CPI", "PCE", "NFP", "Nonfarm", "GDP", "FOMC", "Fed", "PPI", "Retail"]
        for item in data:
            name = item.get("name", "") or item.get("event", "")
            if any(kw.lower() in name.lower() for kw in keywords):
                rows.append({
                    "date":    item.get("date", "")[:10],
                    "event":   name,
                    "country": "🇺🇸",
                    "level":   3,
                })
    except Exception as e:
        print(f"[FinMind] 抓取失敗（靜默）：{e}")
    return rows


def fetch_us_cpi_dates():
    """
    使用美國勞工統計局發布時程（固定每月第二或第三週三）來推算 CPI 日期。
    BLS 發布時程固定，這個方法永久有效。
    """
    rows = []
    # 往後推 2 個月，找每月第三個週三
    for offset_month in range(0, 3):
        month = (today.month + offset_month - 1) % 12 + 1
        year  = today.year + (today.month + offset_month - 1) // 12
        # 找當月第一天
        first_day = today.replace(year=year, month=month, day=1)
        # 找第一個週三（weekday=2）
        days_to_wed = (2 - first_day.weekday()) % 7
        first_wed = first_day + timedelta(days=days_to_wed)
        # 第三個週三 = 第一個週三 + 14 天
        third_wed = first_wed + timedelta(days=14)
        if today <= third_wed <= end_date:
            rows.append({
                "date":    str(third_wed),
                "event":   f"美國 {year}/{month:02d} CPI 通膨數據",
                "country": "🇺🇸",
                "level":   3,
            })
    return rows


def merge_and_deduplicate(all_events):
    """
    合併所有來源事件，去重（同日期同關鍵字），
    只保留今日（含）以後的未來事件，並按日期排序。
    """
    seen = set()
    result = []
    for ev in sorted(all_events, key=lambda x: x["date"]):
        # 過濾過期事件
        if ev["date"] < str(today):
            continue
        # 去重 key = 日期 + 事件名稱前 10 字
        key = ev["date"] + ev["event"][:10]
        if key in seen:
            continue
        seen.add(key)
        result.append(ev)
    return result


def main():
    print(f"[macro] 開始更新，今日：{today}，截止：{end_date}")

    # 收集所有來源
    all_events = []
    all_events += FIXED_EVENTS
    all_events += fetch_finmind_economic_events()
    all_events += fetch_us_cpi_dates()

    # 清洗去重
    events = merge_and_deduplicate(all_events)
    print(f"[macro] 清洗後共 {len(events)} 筆事件")
    for ev in events:
        print(f"  {ev['date']} {ev['country']} {ev['event']}")

    # 覆寫 JSON
    output = {
        "updated_at": str(today),
        "events": events,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/macro_events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[macro] 已寫入 data/macro_events.json")


if __name__ == "__main__":
    main()
