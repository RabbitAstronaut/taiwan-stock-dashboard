"""
generate_munger_sentiment.py  ── 連續性情緒分析引擎
7日滑動視窗 + Gemini 2.5 Flash 分析蒙格12項崩盤信號
執行：
  PowerShell: $env:GEMINI_API_KEY="你的key"; python generate_munger_sentiment.py
  CMD:        set GEMINI_API_KEY=你的key && python generate_munger_sentiment.py
"""
import os, json, re, time, sys
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
from google import genai

# ── 路徑設定
DATA_DIR     = Path("data")
HISTORY_FILE = DATA_DIR / "news_history.json"
OUT_FILE     = DATA_DIR / "munger_sentiment.json"
os.makedirs(DATA_DIR, exist_ok=True)

# ── Gemini 設定
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_KEY:
    print("❌ 未設定 GEMINI_API_KEY")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_KEY)

# ── 蒙格12項崩盤信號
MUNGER_SIGNALS = [
    "1. 散戶瘋狂開戶/入市",
    "2. 媒體/社群充斥股市暴富故事",
    "3. 非金融業人士大談選股",
    "4. 新股(IPO)超額認購屢創新高",
    "5. 槓桿融資餘額急速攀升",
    "6. 市場本益比遠高於歷史均值",
    "7. 央行/政策明顯轉向收緊",
    "8. 殖利率倒掛或信用利差擴大",
    "9. 地緣政治/黑天鵝事件頻傳",
    "10. 成交量萎縮但指數仍高",
    "11. 主力/外資連續大幅減碼",
    "12. 散戶情緒指標達極端樂觀",
]

# ══════════════════════════════════════════════
# 步驟一：抓取今日 RSS 新聞標題
# ══════════════════════════════════════════════
RSS_FEEDS = [
    "https://tw.stock.yahoo.com/rss",
    "https://feeds.feedburner.com/cnyes-news",
    "https://www.moneydj.com/KMDJ/RSSWidget/newsfeedlist.aspx?category=2",
]

def fetch_today_news() -> list[str]:
    titles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                t = entry.get("title", "").strip()
                if t and len(t) > 5:
                    titles.append(t)
            print(f"  ✅ {url[:50]} → {len(feed.entries)} 篇")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️ RSS 失敗：{url[:50]} → {e}")

    # 去重
    titles = list(dict.fromkeys(titles))
    print(f"📰 今日新聞標題：{len(titles)} 則")
    return titles

# ══════════════════════════════════════════════
# 步驟二：更新7日歷史記錄
# ══════════════════════════════════════════════
def update_news_history(today_titles: list[str]) -> dict:
    today_str = datetime.now().strftime("%Y-%m-%d")
    cutoff    = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # 讀取現有歷史
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}

    # 剔除7天前舊資料
    history = {k: v for k, v in history.items() if k >= cutoff}

    # 寫入今日資料
    history[today_str] = today_titles

    # 存回
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"📅 歷史記錄：{sorted(history.keys())}")
    return history

# ══════════════════════════════════════════════
# 步驟三：組裝7日新聞 Prompt
# ══════════════════════════════════════════════
def build_prompt(history: dict) -> str:
    # 按日期排序組裝
    news_block = ""
    for date_str in sorted(history.keys()):
        titles = history[date_str]
        news_block += f"\n【{date_str}】\n"
        news_block += "\n".join(f"- {t}" for t in titles[:15])  # 每天最多15則
        news_block += "\n"

    signals_str = "\n".join(MUNGER_SIGNALS)
    days = len(history)

    return (
        f"你是一位行為財務學專家，專精查理·蒙格的市場崩盤信號分析。\n\n"
        f"以下是『過去連續 {days} 天』的台股財經新聞標題：\n"
        f"{news_block}\n"
        f"請觀察這段時間的情緒演變與持續性。\n\n"
        f"蒙格大崩盤12項信號：\n{signals_str}\n\n"
        f"評估規則：必須是『連續多日發酵』的現象才算成立，單日出現不計。\n\n"
        f"強制只輸出純 JSON，直接從 {{ 開始：\n"
        f'{{\n'
        f'  "triggered_indexes": [觸發的信號編號陣列，如 [1, 4, 11]],\n'
        f'  "danger_score": 0到100的危險分數,\n'
        f'  "trend_analysis": "過去{days}天情緒演變的總結（2-3句話）",\n'
        f'  "key_signals": ["最顯著的2-3項信號簡述"],\n'
        f'  "analysis_days": {days},\n'
        f'  "generated_at": "{datetime.now().strftime("%Y-%m-%d %H:%M")}"\n'
        f'}}'
    )

# ══════════════════════════════════════════════
# 步驟四：呼叫 Gemini
# ══════════════════════════════════════════════
def call_gemini(prompt: str) -> dict | None:
    for attempt in range(3):
        try:
            print(f"🤖 呼叫 Gemini 2.5 Flash（第 {attempt+1} 次）...")
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = resp.text.strip()
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text).strip()
            result = json.loads(text)
            print("✅ 解析成功！")
            return result
        except json.JSONDecodeError:
            print(f"⚠️ JSON 解析失敗：{text[:200]}")
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ 失敗（{attempt+1}）：{e}")
            time.sleep(5 * (attempt + 1))
    return None

# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════
def main():
    print("=" * 55)
    print("🧠 generate_munger_sentiment.py  7日滑動視窗版")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # 步驟一：抓今日新聞
    today_titles = fetch_today_news()
    if not today_titles:
        print("⚠️ 今日無新聞，使用佔位資料")
        today_titles = ["今日無法抓取新聞資料"]

    # 步驟二：更新7日歷史
    history = update_news_history(today_titles)

    # 步驟三：呼叫 Gemini
    prompt = build_prompt(history)
    result = call_gemini(prompt)

    if result:
        OUT_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n✅ 結果存至 {OUT_FILE}")
        print(f"   危險分數：{result.get('danger_score', 0)}/100")
        print(f"   觸發信號：{result.get('triggered_indexes', [])}")
        print(f"   趨勢：{result.get('trend_analysis', '')[:80]}")
    else:
        print("\n❌ Gemini 失敗，保留舊有 JSON")
        if not OUT_FILE.exists():
            fallback = {
                "triggered_indexes": [],
                "danger_score": 0,
                "trend_analysis": "API 失敗，無法分析",
                "key_signals": [],
                "analysis_days": len(history),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "is_fallback": True
            }
            OUT_FILE.write_text(
                json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    print("=" * 55)

if __name__ == "__main__":
    main()
