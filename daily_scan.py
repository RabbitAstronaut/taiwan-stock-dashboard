# -*- coding: utf-8 -*-
"""
daily_scan.py — 「利多不漲・大戶高位出貨」每日盤後排毒掃描腳本（v2）
══════════════════════════════════════════════════════════════════
【用途】
本腳本與 Streamlit 前端完全解耦，獨立執行，建議排程：
    - GitHub Actions：每週一至週五 台灣時間 17:00（UTC 09:00）
    - 或 Windows 工作排程器 / Linux cron：同上時間

【掃描邊界（嚴禁盲目爬全市場）】
動態讀取 data/watch_list.json：
    {
      "my_army":        ["5289","2379","8299","3491","3037"],
      "sector_leaders": ["2330","2454","2317","2357","2603","2610",
                         "2002","1101","2891","2345","2308","3017"]
    }
    - my_army        ：自己的戰備個股，套用「利多不漲」三項觸發判定
    - sector_leaders ：12檔大金剛龍頭股，額外統計站上月線(SMA20)/
                       季線(SMA60)的家數比例，作為大盤多空風向標

【利多不漲・大戶出貨 觸發條件（三項同時成立）】
    1. news_score > 15　（當日多頭新聞關鍵字熱度偏高，市場處於炒作高位）
    2. bad_candle       （當日K線收黑，或上影線比例 ≥ 1.5%，價格無法續攻）
    3. foreign_net < 0  （當日外資三大法人為淨賣超，籌碼面實質出貨）
此判定套用於 my_army + sector_leaders 全部標的。

【輸出：data/triggered_alerts.json】
{
  "alerts": [ {"date":..,"stock_id":..,"name":..,"news_score":..,
               "shadow_pct":..,"foreign_net":..,"open":..,"close":..,
               "scanned_at":..}, ... ],
  "sector_breadth": {
      "date": "2026-06-15",
      "above_sma20": 4, "above_sma60": 6, "total": 12,
      "details": [{"stock_id":"2330","name":"台積電",
                    "above_sma20":true,"above_sma60":true}, ...]
  }
}
    - alerts：採「同日去重後追加」策略，今日若無觸發則不新增任何 alert
      （但 sector_breadth 每次執行都會更新為最新一筆，供前端風向判斷）

【異常處理】
所有對外請求（yfinance / cnyes / chips_data 讀取）皆包在 try-except 中，
單一個股或單一資料源失敗，不影響其他個股的掃描；最終即使全部失敗，
腳本仍會正常結束，絕不中斷排程。每檔掃描完畢主動 gc.collect()。
"""

import os
import json
import argparse
import logging
import gc
from datetime import datetime

import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 備援
    from backports.zoneinfo import ZoneInfo  # type: ignore

try:
    import requests
except ImportError:
    requests = None

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False


# ══════════════════════════════════════════════════════════════
# ▌ 設定
# ══════════════════════════════════════════════════════════════
DATA_DIR        = "data"
WATCH_LIST_JSON = os.path.join(DATA_DIR, "watch_list.json")
CHIPS_CSV       = os.path.join(DATA_DIR, "chips_data.csv")
STOCK_LIST_CSV  = os.path.join(DATA_DIR, "stock_list.csv")
ALERTS_JSON     = os.path.join(DATA_DIR, "triggered_alerts.json")

GITHUB_COMMIT_MSG = "auto: 利多不漲排毒掃描 {date}"

# 多頭炒作關鍵字（用於新聞熱度計分，純關鍵字計數，非情緒分析模型）
BULLISH_KEYWORDS = ["AI", "大漲", "目標價", "上修", "買超", "利多",
                     "強勢", "噴出", "法人", "推升", "創高", "熱潮"]

NEWS_SCORE_THRESHOLD  = 15   # 新聞熱度門檻
SHADOW_PCT_THRESHOLD  = 1.5  # 上影線比例門檻（%）

TZ_TW = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("daily_scan")


# ══════════════════════════════════════════════════════════════
# ▌ 1. 讀取戰備清單（watch_list.json）
# ══════════════════════════════════════════════════════════════
def load_watch_list():
    """
    讀取 data/watch_list.json，回傳四大板塊合併後的完整個股代號清單（去重）。
    同時回傳各板塊原始 dict，供龍頭風向統計使用。

    回傳：
        all_ids      : list[str]  — 全部標的代號（去重）
        sectors_dict : dict       — {板塊key: [stock_id,...]} 原始四板塊結構
    """
    defaults = {
        "ai_semi":      ["2330", "2454", "2317", "2357"],
        "ai_infra":     ["2345", "2308", "3017", "8299"],
        "next_gen":     ["3491", "2359", "1519", "3037"],
        "shipping_fin": ["2603", "2610", "2002", "1101", "2891"],
    }

    sectors_dict = {}
    if os.path.exists(WATCH_LIST_JSON):
        try:
            with open(WATCH_LIST_JSON, "r", encoding="utf-8") as f:
                wl = json.load(f)
            for key, default in defaults.items():
                raw = wl.get(key)
                sectors_dict[key] = [str(s).strip() for s in (raw if raw else default)]
            log.info(f"讀取 {WATCH_LIST_JSON}：" +
                     "、".join(f"{k}={len(v)}檔" for k, v in sectors_dict.items()))
        except Exception as e:
            log.warning(f"讀取 {WATCH_LIST_JSON} 失敗，使用預設值：{e}")
            sectors_dict = {k: list(v) for k, v in defaults.items()}
    else:
        log.warning(f"⚠️ {WATCH_LIST_JSON} 不存在，使用預設值")
        sectors_dict = {k: list(v) for k, v in defaults.items()}

    # 全部板塊合併去重（保留順序）
    seen = set()
    all_ids = []
    for ids in sectors_dict.values():
        for sid in ids:
            if sid not in seen:
                seen.add(sid)
                all_ids.append(sid)

    return all_ids, sectors_dict



# ══════════════════════════════════════════════════════════════
# ▌ 2. 個股 K 線（含 SMA20 / SMA60，今日最終定格）
# ══════════════════════════════════════════════════════════════
def _get_ticker_suffix(stock_id: str) -> str:
    """依 stock_list.csv 的 type 欄位判斷上市(.TW)或上櫃(.TWO)，預設 .TW。"""
    try:
        if os.path.exists(STOCK_LIST_CSV):
            df = pd.read_csv(STOCK_LIST_CSV, dtype=str)
            df["stock_id"] = df["stock_id"].astype(str).str.strip()
            row = df[df["stock_id"] == str(stock_id)]
            if not row.empty and str(row.iloc[0].get("type", "")).strip() == "tpex":
                return ".TWO"
    except Exception:
        pass
    return ".TW"


def fetch_kline_with_sma(stock_id: str, period: str = "4mo"):
    """
    抓取個股近4個月日K線，計算今日是否站上 SMA20 / SMA60，
    並回傳今日定格K線（open/close/high）。

    回傳 dict：
      {"date":, "open":, "close":, "high":,
       "above_sma20": bool|None, "above_sma60": bool|None}
    抓取失敗時回傳 None（呼叫端需妥善處理，不中斷流程）。
    """
    if not HAS_YF:
        return None
    try:
        suffix = _get_ticker_suffix(stock_id)
        hist = yf.Ticker(f"{stock_id}{suffix}").history(period=period, interval="1d")
        if hist.empty and suffix == ".TW":
            hist = yf.Ticker(f"{stock_id}.TWO").history(period=period, interval="1d")
        if hist.empty:
            return None

        closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        last_close = float(closes.iloc[-1])

        above_sma20 = None
        above_sma60 = None
        if len(closes) >= 20:
            above_sma20 = bool(last_close > closes.tail(20).mean())
        if len(closes) >= 60:
            above_sma60 = bool(last_close > closes.tail(60).mean())

        last = hist.iloc[-1]
        return {
            "date":  hist.index[-1].strftime("%Y-%m-%d"),
            "open":  float(last["Open"]),
            "close": last_close,
            "high":  float(last["High"]),
            "above_sma20": above_sma20,
            "above_sma60": above_sma60,
        }
    except Exception as e:
        log.warning(f"  [{stock_id}] K線抓取失敗：{e}")
        return None


# ══════════════════════════════════════════════════════════════
# ▌ 3. 法人籌碼（當日外資淨買賣超）
# ══════════════════════════════════════════════════════════════
def load_chips_for_targets(stock_ids):
    """
    一次性讀取 chips_data.csv，只保留目標股票的資料，回傳
    {stock_id: {"date": 最新日期, "foreign_net": 外資淨額(張)}}。
    相容新舊格式（有無 buy 欄位皆可），net 單位自動換算為「張」。
    """
    result = {}
    try:
        if not os.path.exists(CHIPS_CSV):
            log.warning(f"找不到 {CHIPS_CSV}，跳過法人籌碼比對")
            return result

        df = pd.read_csv(CHIPS_CSV, dtype={"stock_id": str})
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df = df[df["stock_id"].isin(stock_ids)]
        if df.empty:
            return result

        _keep = [c for c in ["date", "stock_id", "name", "net"] if c in df.columns]
        df = df[_keep].copy()
        df["net"] = pd.to_numeric(df.get("net"), errors="coerce")
        df = df.dropna(subset=["name", "net"])
        # 股單位轉張（與前端一致：絕對值最大>50000視為股單位）
        if not df.empty and df["net"].abs().max() > 50000:
            df["net"] = df["net"] / 1000
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

        foreign = df[df["name"].astype(str).str.contains("Foreign_Investor", na=False)]
        for sid, g in foreign.groupby("stock_id"):
            g = g.dropna(subset=["date"]).sort_values("date")
            if g.empty:
                continue
            last = g.iloc[-1]
            result[sid] = {
                "date": last["date"].strftime("%Y-%m-%d"),
                "foreign_net": float(last["net"]),
            }
        del df, foreign
        gc.collect()
    except Exception as e:
        log.warning(f"讀取籌碼資料失敗：{e}")
    return result


# ══════════════════════════════════════════════════════════════
# ▌ 4. 新聞熱度（多頭關鍵字計數）
# ══════════════════════════════════════════════════════════════
def get_news_heat_score(stock_name: str) -> int:
    """
    爬取鉅亨網新聞搜尋頁，以多頭關鍵字出現次數做為「新聞熱度分數」。
    這是簡化版的文字探勘（關鍵字計數），用於捕捉「市場炒作熱度」的
    相對高低，並非嚴謹的情緒分析模型。失敗時回傳 0，不中斷流程。
    """
    if requests is None:
        return 0
    score = 0
    try:
        url = f"https://www.cnyes.com/search/news?q={stock_name}"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8,
        )
        if r.status_code == 200:
            text = r.text
            for kw in BULLISH_KEYWORDS:
                score += text.count(kw)
    except Exception as e:
        log.warning(f"  [{stock_name}] 新聞熱度抓取失敗：{e}")
    return score


# ══════════════════════════════════════════════════════════════
# ▌ 5. 單檔個股「利多不漲」掃描
# ══════════════════════════════════════════════════════════════
def scan_one_stock(stock_id: str, name: str, chips_map: dict, kline: dict):
    """
    對單一個股執行「利多不漲」三項條件判定。
    kline 為 fetch_kline_with_sma() 的回傳結果（可為 None）。
    回傳 dict（含 trigger 布林值與所有觀察 facts），任一資料缺失
    僅會讓對應欄位維持預設值（0 / False），不會讓整支腳本中斷。
    """
    result = {
        "stock_id": stock_id, "name": name,
        "news_score": 0, "bad_candle": False, "shadow_pct": 0.0,
        "foreign_net": 0.0, "open": None, "close": None,
        "trigger": False,
    }

    # 1) 新聞熱度
    result["news_score"] = get_news_heat_score(name)

    # 2) K線結構：收黑 或 長上影線
    if kline:
        _open, _close, _high = kline["open"], kline["close"], kline["high"]
        result["open"], result["close"] = _open, _close
        is_red = _close < _open
        shadow_pct = (_high - max(_close, _open)) / _close * 100 if _close > 0 else 0
        result["shadow_pct"] = round(shadow_pct, 2)
        result["bad_candle"] = bool(is_red or shadow_pct >= SHADOW_PCT_THRESHOLD)

    # 3) 法人籌碼：外資當日淨賣超
    chip = chips_map.get(stock_id)
    if chip:
        result["foreign_net"] = chip["foreign_net"]

    # 三項條件同時成立 → 觸發
    result["trigger"] = (
        result["news_score"] > NEWS_SCORE_THRESHOLD
        and result["bad_candle"]
        and result["foreign_net"] < 0
    )
    return result


# ══════════════════════════════════════════════════════════════
# ▌ 6. 寫入 triggered_alerts.json
# ══════════════════════════════════════════════════════════════
def save_results(today_str: str, hits: list, sector_breadth: dict):
    """
    更新 data/triggered_alerts.json：
      - alerts        ：移除今日舊紀錄後，附加今日觸發結果（若有）
      - sector_breadth：直接覆寫為本次最新統計（每次執行都更新，
                        供前端隨時顯示最新龍頭股風向）
    """
    data = {"alerts": [], "sector_breadth": {}}
    if os.path.exists(ALERTS_JSON):
        try:
            with open(ALERTS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("alerts", [])
            data.setdefault("sector_breadth", {})
        except Exception as e:
            log.warning(f"讀取既有 {ALERTS_JSON} 失敗，將建立新檔：{e}")
            data = {"alerts": [], "sector_breadth": {}}

    # 移除今日舊紀錄（同日重跑去重）
    data["alerts"] = [a for a in data["alerts"] if a.get("date") != today_str]

    now_iso = datetime.now(TZ_TW).isoformat()
    for h in hits:
        data["alerts"].append({
            "date": today_str,
            "stock_id": h["stock_id"],
            "name": h["name"],
            "news_score": h["news_score"],
            "shadow_pct": h["shadow_pct"],
            "foreign_net": h["foreign_net"],
            "open": h["open"],
            "close": h["close"],
            "scanned_at": now_iso,
        })

    # 龍頭股風向：每次執行都覆寫為最新一筆
    data["sector_breadth"] = sector_breadth

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALERTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"✅ 已寫入 {ALERTS_JSON}（今日 {len(hits)} 檔觸發，"
             f"龍頭風向 {sector_breadth.get('above_sma20','-')}/"
             f"{sector_breadth.get('total','-')} 站月線）")


# ══════════════════════════════════════════════════════════════
# ▌ 7. Git 推送（可選）
# ══════════════════════════════════════════════════════════════
def git_push(commit_msg: str):
    """用 subprocess 執行 git add/commit/push，任何步驟失敗都不會中斷腳本。"""
    import subprocess
    try:
        subprocess.run(["git", "add", ALERTS_JSON], check=True)
        result = subprocess.run(["git", "commit", "-m", commit_msg],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            log.info(f"git commit 無變更或失敗（可忽略）：{result.stdout.strip()} {result.stderr.strip()}")
            return
        subprocess.run(["git", "push"], check=True)
        log.info("🚀 已推送至 GitHub")
    except Exception as e:
        log.warning(f"git push 失敗（不影響本次掃描結果）：{e}")


# ══════════════════════════════════════════════════════════════
# ▌ 主程式
# ══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="利多不漲・大戶高位出貨 每日盤後排毒掃描（v2）")
    parser.add_argument("--no-push", action="store_true", help="不執行 git push")
    args = parser.parse_args()

    today_str = datetime.now(TZ_TW).strftime("%Y-%m-%d")
    log.info("=" * 60)
    log.info(f"利多不漲排毒掃描 v2 — {today_str}")
    log.info("=" * 60)

    all_ids, sectors_dict = load_watch_list()
    if not all_ids:
        log.info("watch_list.json 無有效標的，結束本次掃描（不寫入任何檔案）")
        return

    log.info(f"總掃描標的：{len(all_ids)} 檔")
    for _k, _v in sectors_dict.items():
        log.info(f"  [{_k}] {len(_v)} 檔：{', '.join(_v)}")

    # 取得個股中文名稱（從 stock_list.csv，找不到就用代號本身）
    name_map = {}
    try:
        if os.path.exists(STOCK_LIST_CSV):
            _sl = pd.read_csv(STOCK_LIST_CSV, dtype=str)
            _sl["stock_id"] = _sl["stock_id"].astype(str).str.strip()
            for _, row in _sl.drop_duplicates("stock_id").iterrows():
                name_map[row["stock_id"]] = row.get("stock_name", row["stock_id"])
    except Exception as e:
        log.warning(f"讀取 stock_list.csv 名稱對照失敗：{e}")

    chips_map = load_chips_for_targets(all_ids)

    # ── 逐檔抓K線（含SMA20/60），同時供「利多不漲」與「龍頭風向」共用
    kline_map = {}
    for sid in all_ids:
        kline_map[sid] = fetch_kline_with_sma(sid)
        gc.collect()  # 每檔抓完立即回收，降低長時間執行的記憶體佔用

    # ── 1) 全部標的跑「利多不漲」三項判定
    hits = []
    for sid in all_ids:
        name = name_map.get(sid, sid)
        try:
            r = scan_one_stock(sid, name, chips_map, kline_map.get(sid))
            tag = "🔴觸發" if r["trigger"] else "⚪正常"
            log.info(f"  [{sid}] {name}｜新聞熱度{r['news_score']}｜"
                     f"上影{r['shadow_pct']:.1f}%｜外資{r['foreign_net']:+.0f}張｜{tag}")
            if r["trigger"]:
                hits.append(r)
        except Exception as e:
            log.warning(f"  [{sid}] {name} 掃描失敗（跳過）：{e}")
        finally:
            gc.collect()

    # ── 2) 四大板塊龍頭動態風向：分板塊統計站上 SMA20 / SMA60 家數比例
    breadth_by_sector = {}
    above20_total, above60_total, valid_total = 0, 0, 0
    for sector_key, sector_ids in sectors_dict.items():
        s_above20, s_above60, s_valid, s_details = 0, 0, 0, []
        for sid in sector_ids:
            k = kline_map.get(sid)
            if not k:
                continue
            a20, a60 = k.get("above_sma20"), k.get("above_sma60")
            if a20 is None and a60 is None:
                continue
            s_valid += 1
            if a20: s_above20 += 1
            if a60: s_above60 += 1
            s_details.append({"stock_id": sid, "name": name_map.get(sid, sid),
                               "above_sma20": bool(a20), "above_sma60": bool(a60)})
        breadth_by_sector[sector_key] = {
            "above_sma20": s_above20, "above_sma60": s_above60,
            "total": s_valid, "details": s_details,
        }
        above20_total += s_above20
        above60_total += s_above60
        valid_total   += s_valid
        log.info(f"  [{sector_key}] 站月線 {s_above20}/{s_valid}  站季線 {s_above60}/{s_valid}")

    sector_breadth = {
        "date": today_str,
        "above_sma20": above20_total,
        "above_sma60": above60_total,
        "total": valid_total,
        "by_sector": breadth_by_sector,
    }
    log.info(f"龍頭總風向：站月線 {above20_total}/{valid_total}  站季線 {above60_total}/{valid_total}")

    # ── 3) 寫入結果（alerts 視情況追加；sector_breadth 每次覆寫）
    if hits:
        log.info(f"🚨 今日共 {len(hits)} 檔觸發「利多不漲」警報")
    else:
        log.info("✅ 今日無任何標的觸發「利多不漲」")

    save_results(today_str, hits, sector_breadth)
    if not args.no_push:
        git_push(GITHUB_COMMIT_MSG.format(date=today_str))

    log.info("=" * 60)
    log.info("掃描完成")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
