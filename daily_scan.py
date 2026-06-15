# -*- coding: utf-8 -*-
"""
daily_scan.py — 「利多不漲・大戶高位出貨」每日盤後排毒掃描腳本
══════════════════════════════════════════════════════════════════
【用途】
本腳本與 Streamlit 前端完全解耦，獨立執行，建議排程：
    - GitHub Actions：每週一至週五 台灣時間 17:00（UTC 09:00）
    - 或 Windows 工作排程器 / Linux cron：同上時間

【掃描邊界】
動態讀取戰備清單，掃描範圍優先順序：
    1. data/watch_list.json 的 "core_army" + "cyclical_stocks"（新格式，約10~20檔）
    2. 若上述檔案不存在，退回 data/watchlist.json 的 "reserve"（既有戰略儲備庫）
每檔個股需有 {"id": "2330", "name": "台積電"} 或純字串代號兩種格式皆可解析。

【觸發條件（三項同時成立才算「利多不漲」）】
    1. news_score > 15　（當日多頭新聞關鍵字熱度偏高，市場處於炒作高位）
    2. bad_candle       （當日K線收黑，或上影線比例 ≥ 1.5%，價格無法續攻）
    3. foreign_net < 0  （當日外資三大法人為淨賣超，籌碼面實質出貨）

【輸出】
若「今日」有任何個股觸發 → 將觸發明細以 JSON 格式追加寫入
data/triggered_alerts.json（重跑同一天會先移除舊紀錄再寫入，不會重複累積）。
若今日無任何觸發 → 不寫入、不修改檔案，保持歷史紀錄乾淨。

【異常處理】
所有對外請求（yfinance / cnyes / chips_data 讀取）皆包在 try-except 中，
單一個股或單一資料源失敗，不影響其他個股的掃描；最終即使全部失敗，
腳本仍會正常結束（視為「今日無觸發」），絕不中斷排程。
"""

import os
import sys
import json
import argparse
import logging
import gc
from datetime import datetime
from pathlib import Path

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
WATCH_LIST_NEW  = os.path.join(DATA_DIR, "watch_list.json")     # 新格式：core_army + cyclical_stocks
WATCH_LIST_OLD  = os.path.join(DATA_DIR, "watchlist.json")      # 既有格式：reserve
CHIPS_CSV       = os.path.join(DATA_DIR, "chips_data.csv")
STOCK_LIST_CSV  = os.path.join(DATA_DIR, "stock_list.csv")
ALERTS_JSON     = os.path.join(DATA_DIR, "triggered_alerts.json")

GITHUB_REPO     = "RabbitAstronaut/taiwan-stock-dashboard"
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
# ▌ 1. 讀取戰備清單
# ══════════════════════════════════════════════════════════════
def _normalize_entry(item):
    """把清單項目統一轉成 (stock_id, name) tuple，相容 dict 與純字串兩種格式。"""
    if isinstance(item, dict):
        sid = str(item.get("id") or item.get("stock_id") or "").strip()
        name = str(item.get("name") or sid).strip()
        return sid, name
    sid = str(item).strip()
    return sid, sid


def load_watch_list():
    """
    讀取戰備清單，回傳 [(stock_id, name), ...]，已去重。

    優先讀 data/watch_list.json 的 core_army + cyclical_stocks（新格式）；
    若該檔不存在或解析失敗，退回 data/watchlist.json 的 reserve（舊格式）。
    任何讀取失敗都會被捕捉，最終回傳空清單而不中斷腳本。
    """
    entries = []

    # ── 1) 新格式：watch_list.json
    try:
        if os.path.exists(WATCH_LIST_NEW):
            with open(WATCH_LIST_NEW, "r", encoding="utf-8") as f:
                wl = json.load(f)
            for key in ("core_army", "cyclical_stocks"):
                for item in wl.get(key, []):
                    entries.append(_normalize_entry(item))
            if entries:
                log.info(f"從 {WATCH_LIST_NEW} 讀取 core_army+cyclical_stocks，共 {len(entries)} 檔")
    except Exception as e:
        log.warning(f"讀取 {WATCH_LIST_NEW} 失敗：{e}")

    # ── 2) 備援：watchlist.json 的 reserve
    if not entries:
        try:
            if os.path.exists(WATCH_LIST_OLD):
                with open(WATCH_LIST_OLD, "r", encoding="utf-8") as f:
                    wl = json.load(f)
                for item in wl.get("reserve", []):
                    entries.append(_normalize_entry(item))
                if entries:
                    log.info(f"從 {WATCH_LIST_OLD} 讀取 reserve，共 {len(entries)} 檔")
        except Exception as e:
            log.warning(f"讀取 {WATCH_LIST_OLD} 失敗：{e}")

    # 去重（保留第一次出現的順序）
    seen = set()
    unique = []
    for sid, name in entries:
        if sid and sid not in seen:
            seen.add(sid)
            unique.append((sid, name))

    if not unique:
        log.warning("⚠️ 戰備清單為空，本次掃描將不會產生任何紀錄")

    return unique


# ══════════════════════════════════════════════════════════════
# ▌ 2. 個股 K 線（today's final candle）
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


def fetch_today_candle(stock_id: str):
    """
    抓取個股「今日」最終定格K線。
    回傳 dict {"open":..,"close":..,"high":..,"date":..} 或 None（失敗時）。
    """
    if not HAS_YF:
        return None
    try:
        suffix = _get_ticker_suffix(stock_id)
        hist = yf.Ticker(f"{stock_id}{suffix}").history(period="5d", interval="1d")
        if hist.empty and suffix == ".TW":
            hist = yf.Ticker(f"{stock_id}.TWO").history(period="5d", interval="1d")
        if hist.empty:
            return None
        last = hist.iloc[-1]
        return {
            "date":  hist.index[-1].strftime("%Y-%m-%d"),
            "open":  float(last["Open"]),
            "close": float(last["Close"]),
            "high":  float(last["High"]),
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
# ▌ 5. 單檔個股掃描
# ══════════════════════════════════════════════════════════════
def scan_one_stock(stock_id: str, name: str, chips_map: dict):
    """
    對單一個股執行「利多不漲」三項條件判定。
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

    # 2) K線結構
    candle = fetch_today_candle(stock_id)
    if candle:
        _open, _close, _high = candle["open"], candle["close"], candle["high"]
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
# ▌ 6. 寫入 triggered_alerts.json（僅在有觸發時寫入）
# ══════════════════════════════════════════════════════════════
def append_alerts(today_str: str, hits: list):
    """
    將今日觸發紀錄寫入 data/triggered_alerts.json。
    若檔案已存在，先移除「今日」的舊紀錄（避免重跑造成重複），
    再附加最新結果，其餘歷史紀錄保留。
    """
    data = {"alerts": []}
    if os.path.exists(ALERTS_JSON):
        try:
            with open(ALERTS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "alerts" not in data:
                data["alerts"] = []
        except Exception as e:
            log.warning(f"讀取既有 {ALERTS_JSON} 失敗，將建立新檔：{e}")
            data = {"alerts": []}

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

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALERTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"✅ 已寫入 {ALERTS_JSON}（今日 {len(hits)} 檔觸發，總紀錄 {len(data['alerts'])} 筆）")


# ══════════════════════════════════════════════════════════════
# ▌ 7. Git 推送（可選）
# ══════════════════════════════════════════════════════════════
def git_push(commit_msg: str):
    """用 subprocess 執行 git add/commit/push，任何步驟失敗都不會中斷腳本。"""
    import subprocess
    try:
        subprocess.run(["git", "add", ALERTS_JSON], check=True)
        # 沒有變更時 commit 會失敗，屬正常情況
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
    parser = argparse.ArgumentParser(description="利多不漲・大戶高位出貨 每日盤後排毒掃描")
    parser.add_argument("--no-push", action="store_true", help="不執行 git push")
    args = parser.parse_args()

    today_str = datetime.now(TZ_TW).strftime("%Y-%m-%d")
    log.info("=" * 60)
    log.info(f"利多不漲排毒掃描 — {today_str}")
    log.info("=" * 60)

    targets = load_watch_list()
    if not targets:
        log.info("戰備清單為空，結束本次掃描（不寫入任何檔案）")
        return

    stock_ids = [sid for sid, _ in targets]
    log.info(f"掃描標的（{len(targets)}檔）：{', '.join(f'{n}({s})' for s, n in targets)}")

    chips_map = load_chips_for_targets(stock_ids)

    hits = []
    for sid, name in targets:
        try:
            r = scan_one_stock(sid, name, chips_map)
            tag = "🔴觸發" if r["trigger"] else "⚪正常"
            log.info(f"  [{sid}] {name}｜新聞熱度{r['news_score']}｜"
                     f"上影{r['shadow_pct']:.1f}%｜外資{r['foreign_net']:+.0f}張｜{tag}")
            if r["trigger"]:
                hits.append(r)
        except Exception as e:
            # 單檔失敗不影響其他個股
            log.warning(f"  [{sid}] {name} 掃描失敗（跳過）：{e}")
        finally:
            gc.collect()  # 每檔掃描後主動回收，降低長時間執行的記憶體佔用

    if hits:
        log.info(f"🚨 今日共 {len(hits)} 檔觸發「利多不漲」警報")
        append_alerts(today_str, hits)
        if not args.no_push:
            git_push(GITHUB_COMMIT_MSG.format(date=today_str))
    else:
        log.info("✅ 今日無任何標的觸發，不寫入 triggered_alerts.json")

    log.info("=" * 60)
    log.info("掃描完成")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
