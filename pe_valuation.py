"""
pe_valuation.py — V7 個股本益比估值模組
================================================================
用 FinMind 的 TaiwanStockPER 資料集（涵蓋上市＋上櫃，回溯至2005年，
date/stock_id/dividend_yield/PER/PBR）一次拿歷史序列，不用像原本
設想的「每天存一筆、等三個月才有用」，第一次呼叫就有完整歷史可用。

提供：
  - 目前PE（最新一筆）
  - 歷史百分位（今天的PE在過去N年分布中的位置）
  - PE均值／前值
  - PEG（本益成長比，用EPS年增率）
  - 類股PE比較（沿用kg_companies.json的Topic分類）
  - 簡易評等（星等/狀態）

證據等級：FinMind轉載自公開資訊觀測站/交易所官方數字，A級。
歷史百分位/PEG/類股PE屬系統衍生計算，C級。
"""

import os
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PE_CACHE_PATH = os.path.join(DATA_DIR, "pe_history_cache.json")

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
PE_HISTORY_YEARS = 3          # 抓幾年歷史來算百分位（3年約750個交易日，樣本足夠）
PE_CACHE_TTL_HOURS = 20       # 一天抓一次即可，避免重複打API
MIN_PERCENTILE_SAMPLE = 60    # 少於這個天數，百分位視為不可靠
REALTIME_PRICE_CACHE_TTL_MIN = 5  # 即時股價快取分鐘數，避免同一次瀏覽重複打yfinance

RATING_LABELS = {
    5: "★★★★★ 顯著低估",
    4: "★★★★☆ 偏低",
    3: "★★★☆☆ 中性",
    2: "★★☆☆☆ 偏貴",
    1: "★☆☆☆☆ 顯著昂貴",
    0: "— 資料不足，無法評等",
}


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for _delay in (0, 0.2, 0.5, 1.0):
        if _delay:
            time.sleep(_delay)
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            continue
    return False


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ══════════════════════════════════════════════════════════════
# ▌ 歷史PE序列抓取（FinMind TaiwanStockPER，一次拿完整歷史）
# ══════════════════════════════════════════════════════════════

def fetch_pe_history(stock_id, years=PE_HISTORY_YEARS, force=False):
    """
    回傳 pandas DataFrame(date, PER, PBR, dividend_yield) 或 None。
    磁碟快取20小時，避免同一天重複打API消耗FinMind額度。
    """
    cache = _load_json(PE_CACHE_PATH, {})
    cached = cache.get(stock_id)
    if not force and cached and cached.get("fetched_at"):
        try:
            age_h = (datetime.now() - datetime.strptime(
                cached["fetched_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            if age_h < PE_CACHE_TTL_HOURS and cached.get("data"):
                df = pd.DataFrame(cached["data"])
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date")
        except Exception:
            pass

    try:
        import requests
        fm_token = os.environ.get("FINMIND_TOKEN", "")
        headers = {"Authorization": f"Bearer {fm_token}"} if fm_token else {}
        start_date = (datetime.now() - timedelta(days=int(years * 365.25))).strftime("%Y-%m-%d")
        resp = requests.get(FINMIND_API, params={
            "dataset": "TaiwanStockPER", "data_id": stock_id, "start_date": start_date,
        }, headers=headers, timeout=15)
        data = resp.json()
        rows = data.get("data") if data.get("status") == 200 else None
        if not rows:
            cache[stock_id] = {"fetched_at": _now(), "data": [], "status": data.get("msg", "無資料")}
            _save_json(PE_CACHE_PATH, cache)
            return None

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df["PER"] = pd.to_numeric(df["PER"], errors="coerce")
        df = df.dropna(subset=["PER"]).sort_values("date")

        cache[stock_id] = {
            "fetched_at": _now(), "status": "已更新",
            "data": df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_dict("records"),
        }
        _save_json(PE_CACHE_PATH, cache)
        return df
    except Exception as e:
        cache[stock_id] = {"fetched_at": _now(), "data": [], "status": f"取得失敗（{type(e).__name__}）"}
        _save_json(PE_CACHE_PATH, cache)
        return None


# ══════════════════════════════════════════════════════════════
# ▌ 目前PE／百分位／均值／前值
# ══════════════════════════════════════════════════════════════

def calculate_pe_percentile(stock_id):
    """
    回傳 dict：{"official_pe", "official_pe_date", "percentile", "mean", "previous",
                "sample_size", "data_as_of", "status"}

    【重要】official_pe是FinMind TaiwanStockPER的數字，這個資料集是收盤後才
    結算的，不是即時盤中報價——如果你現在是盤中看盤、股價已經跟前一天收盤
    差很多，這個PE會有落差，直接拿來判斷可能會被過時的數字誤導。
    要看「以現在股價計算」的版本，改用 get_realtime_adjusted_pe()。
    樣本數不足 MIN_PERCENTILE_SAMPLE 天時，percentile回傳None、status說明原因。
    """
    df = fetch_pe_history(stock_id)
    if df is None or df.empty:
        return {
            "official_pe": None, "official_pe_date": None, "percentile": None,
            "mean": None, "previous": None,
            "sample_size": 0, "data_as_of": None, "status": "查無本益比歷史資料",
        }

    per_series = df["PER"].astype(float)
    current_pe = float(per_series.iloc[-1])
    previous_pe = float(per_series.iloc[-2]) if len(per_series) >= 2 else None
    mean_pe = round(float(per_series.mean()), 2)
    data_as_of = df["date"].iloc[-1].strftime("%Y-%m-%d")
    sample_size = len(per_series)

    if sample_size < MIN_PERCENTILE_SAMPLE:
        percentile = None
        status = f"樣本數僅{sample_size}天（少於{MIN_PERCENTILE_SAMPLE}天），百分位不可靠，僅供參考"
    else:
        percentile = round(float((per_series < current_pe).sum() / sample_size * 100), 1)
        status = "已計算"

    return {
        "official_pe": round(current_pe, 2), "official_pe_date": data_as_of,
        "percentile": percentile,
        "mean": mean_pe, "previous": round(previous_pe, 2) if previous_pe is not None else None,
        "sample_size": sample_size, "data_as_of": data_as_of, "status": status,
    }


def get_realtime_price(stock_id):
    """
    抓即時（或最新可得）股價，快取5分鐘。用yfinance，跟系統其他地方
    （Tab1/leveraged_etf.py）的做法一致，不另建資料來源。
    回傳 (price, fetched_at) 或 (None, None)。
    """
    cache = _load_json(PE_CACHE_PATH, {})
    cache_key = f"_realtime_price_{stock_id}"
    cached = cache.get(cache_key)
    if cached and cached.get("fetched_at"):
        try:
            age_min = (datetime.now() - datetime.strptime(
                cached["fetched_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if age_min < REALTIME_PRICE_CACHE_TTL_MIN:
                return cached.get("price"), cached["fetched_at"]
        except Exception:
            pass

    try:
        import yfinance as yf
        sid = str(stock_id).strip()
        for suffix in [".TW", ".TWO"]:
            try:
                hist = yf.Ticker(sid + suffix).history(period="5d")
                if hist is not None and not hist.empty:
                    price = round(float(hist["Close"].iloc[-1]), 2)
                    fetched_at = _now()
                    cache[cache_key] = {"price": price, "fetched_at": fetched_at}
                    _save_json(PE_CACHE_PATH, cache)
                    return price, fetched_at
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def get_realtime_adjusted_pe(stock_id):
    """
    【解決盤中判讀落差】FinMind的PER是收盤後才結算，如果現在是盤中、
    股價已經跟前一天收盤有落差，直接用official_pe判斷可能會被過時數字
    誤導（可能導致不該出場時出場、或該注意時沒注意到）。

    做法：EPS不會在盤中變動，用「昨天PE反推EPS」，再用「現在的即時股價」
    重新算一次PE：
        implied_eps = 昨天收盤價 / 昨天PE
        即時估算PE = 現在股價 / implied_eps
                  = 昨天PE × (現在股價 / 昨天收盤價)

    回傳 dict：{"realtime_pe", "realtime_price", "price_change_pct",
                "based_on_date", "note"}
    """
    pe_info = calculate_pe_percentile(stock_id)
    official_pe = pe_info.get("official_pe")
    official_date = pe_info.get("official_pe_date")
    if official_pe is None:
        return {"realtime_pe": None, "realtime_price": None, "price_change_pct": None,
                "based_on_date": None, "note": "沒有官方PE可以反推，無法計算即時估算值"}

    df = fetch_pe_history(stock_id)
    if df is None or df.empty:
        return {"realtime_pe": None, "realtime_price": None, "price_change_pct": None,
                "based_on_date": None, "note": "沒有歷史價格資料可以反推"}

    # TaiwanStockPER沒有附收盤價欄位，另外用yfinance抓當天收盤價當基準
    try:
        import yfinance as yf
        sid = str(stock_id).strip()
        base_close = None
        for suffix in [".TW", ".TWO"]:
            try:
                hist = yf.Ticker(sid + suffix).history(
                    start=official_date, end=(datetime.strptime(official_date, "%Y-%m-%d")
                                               + timedelta(days=3)).strftime("%Y-%m-%d")
                )
                if hist is not None and not hist.empty:
                    base_close = float(hist["Close"].iloc[0])
                    break
            except Exception:
                continue
        if base_close is None:
            return {"realtime_pe": None, "realtime_price": None, "price_change_pct": None,
                    "based_on_date": official_date, "note": f"抓不到{official_date}的收盤價，無法反推"}
    except Exception as e:
        return {"realtime_pe": None, "realtime_price": None, "price_change_pct": None,
                "based_on_date": official_date, "note": f"反推時發生問題（{e}）"}

    realtime_price, price_fetched_at = get_realtime_price(stock_id)
    if realtime_price is None:
        return {"realtime_pe": None, "realtime_price": None, "price_change_pct": None,
                "based_on_date": official_date, "note": "抓不到即時股價，無法反推"}

    price_change_pct = round((realtime_price - base_close) / base_close * 100, 2) if base_close else None
    realtime_pe = round(official_pe * (realtime_price / base_close), 2) if base_close else None

    return {
        "realtime_pe": realtime_pe, "realtime_price": realtime_price,
        "price_fetched_at": price_fetched_at,
        "price_change_pct": price_change_pct, "based_on_date": official_date,
        "note": (f"以{official_date}官方PE({official_pe})反推EPS，"
                 f"再用即時股價({realtime_price})重算，股價較{official_date}收盤"
                 f"{'上漲' if price_change_pct and price_change_pct > 0 else '下跌' if price_change_pct and price_change_pct < 0 else '持平'}"
                 f"{abs(price_change_pct) if price_change_pct is not None else 0}%"),
    }


# ══════════════════════════════════════════════════════════════
# ▌ PEG（本益成長比）
# ══════════════════════════════════════════════════════════════

def calculate_peg(stock_id, current_pe=None):
    """
    PEG = PE / EPS年增率(%)。用rex_scores.json既有的eps_yoy_val，
    沒有該股票資料時回傳None，不憑空估算成長率。
    """
    try:
        rex_data = _load_json(os.path.join(DATA_DIR, "rex_scores.json"), {})
        rex_map = {s["stock_id"]: s for s in rex_data.get("scores", [])}
        rec = rex_map.get(str(stock_id))
        if not rec:
            return {"peg": None, "eps_yoy": None, "status": "此股票不在戰略儲備庫評分名單，無EPS成長率資料"}
        eps_yoy = rec.get("eps_yoy_val")
        if eps_yoy is None or eps_yoy <= 0:
            return {"peg": None, "eps_yoy": eps_yoy, "status": "EPS年增率為負或無資料，PEG無意義（不計算）"}
        if current_pe is None:
            pe_info = calculate_pe_percentile(stock_id)
            current_pe = pe_info.get("official_pe")
        if current_pe is None:
            return {"peg": None, "eps_yoy": eps_yoy, "status": "無本益比資料"}
        peg = round(current_pe / eps_yoy, 2)
        return {"peg": peg, "eps_yoy": eps_yoy, "status": "已計算"}
    except Exception as e:
        return {"peg": None, "eps_yoy": None, "status": f"計算時發生問題（{e}）"}


# ══════════════════════════════════════════════════════════════
# ▌ 類股PE比較（沿用kg_companies.json的Topic分類）
# ══════════════════════════════════════════════════════════════

def get_stock_topics(stock_id):
    """讀kg_companies.json，回傳該股票所屬的Topic清單"""
    try:
        d = _load_json(os.path.join(DATA_DIR, "kg_companies.json"), {})
        topics = set()
        for row in d.get("companies", []):
            if len(row) >= 3 and str(row[2]).strip() == str(stock_id):
                topics.add(row[1])
        return sorted(topics)
    except Exception:
        return []


def calculate_industry_pe(stock_id, max_peers=15):
    """
    找同Topic的其他公司，抓他們的目前PE算平均（只對已有戰略儲備庫評分或
    已快取過PE的公司，避免無限制打API）。回傳 {"industry_pe_mean", "peer_count", "topics"}
    """
    topics = get_stock_topics(stock_id)
    if not topics:
        return {"industry_pe_mean": None, "peer_count": 0, "topics": [],
                "status": "此股票沒有Topic分類資料"}

    try:
        d = _load_json(os.path.join(DATA_DIR, "kg_companies.json"), {})
        peer_ids = set()
        for row in d.get("companies", []):
            if len(row) >= 3 and row[1] in topics and str(row[2]).strip() != str(stock_id):
                peer_ids.add(str(row[2]).strip())
    except Exception:
        peer_ids = set()

    pe_cache = _load_json(PE_CACHE_PATH, {})
    peer_pes = []
    checked = 0
    for pid in peer_ids:
        if checked >= max_peers:
            break
        cached = pe_cache.get(pid)
        if not cached or not cached.get("data"):
            continue  # 只用已經快取過的，不為了算類股平均而額外打一堆API
        checked += 1
        try:
            last_row = cached["data"][-1]
            pe = float(last_row.get("PER"))
            if 0 < pe < 500:
                peer_pes.append(pe)
        except Exception:
            continue

    if not peer_pes:
        return {"industry_pe_mean": None, "peer_count": 0, "topics": topics,
                "status": "同類股公司目前都還沒有PE快取資料，需要先在Tab10查過那些公司"}

    return {
        "industry_pe_mean": round(float(np.mean(peer_pes)), 2),
        "peer_count": len(peer_pes), "topics": topics, "status": "已計算",
    }


# ══════════════════════════════════════════════════════════════
# ▌ 簡易評等
# ══════════════════════════════════════════════════════════════

def classify_valuation_rating(percentile, peg=None):
    """
    純粹依歷史百分位評星，PEG當輔助資訊（不納入星等，避免過度複雜化）。
    百分位None（樣本不足）時回傳0星「資料不足」。
    """
    if percentile is None:
        return 0, RATING_LABELS[0]
    if percentile <= 20:
        return 5, RATING_LABELS[5]
    if percentile <= 40:
        return 4, RATING_LABELS[4]
    if percentile <= 60:
        return 3, RATING_LABELS[3]
    if percentile <= 80:
        return 2, RATING_LABELS[2]
    return 1, RATING_LABELS[1]


# ══════════════════════════════════════════════════════════════
# ▌ 整合：組一份完整估值摘要
# ══════════════════════════════════════════════════════════════

def build_valuation_summary(stock_id):
    """一次算完目前PE/百分位/均值/前值/PEG/類股PE/評等，供UI直接使用"""
    pe_info = calculate_pe_percentile(stock_id)
    realtime_info = get_realtime_adjusted_pe(stock_id)
    peg_info = calculate_peg(stock_id, current_pe=pe_info.get("official_pe"))
    industry_info = calculate_industry_pe(stock_id)

    # 【重要】評等用「即時反推PE」而不是前一天的官方PE，因為評等是給
    # 「現在」要不要行動的判斷用的，用昨天的數字評等，遇到今天股價已經
    # 大漲大跌時會誤導判斷。即時PE算不出來時（比如收盤後查詢、股價沒變化）
    # 才退回用官方PE評等。
    _realtime_pe = realtime_info.get("realtime_pe")
    _pe_for_rating = _realtime_pe if _realtime_pe is not None else pe_info.get("official_pe")
    _percentile_for_rating = pe_info.get("percentile")
    if _realtime_pe is not None and pe_info.get("percentile") is not None:
        # 用即時PE重新對歷史分布排百分位（而不是直接套用昨天算好的百分位）
        try:
            df = fetch_pe_history(stock_id)
            if df is not None and not df.empty:
                per_series = df["PER"].astype(float)
                if len(per_series) >= MIN_PERCENTILE_SAMPLE:
                    _percentile_for_rating = round(
                        float((per_series < _realtime_pe).sum() / len(per_series) * 100), 1
                    )
        except Exception:
            pass

    stars, rating_label = classify_valuation_rating(_percentile_for_rating)

    return {
        "stock_id": stock_id,
        "official_pe": pe_info["official_pe"], "official_pe_date": pe_info["official_pe_date"],
        "realtime_pe": realtime_info.get("realtime_pe"),
        "realtime_price": realtime_info.get("realtime_price"),
        "price_change_pct": realtime_info.get("price_change_pct"),
        "realtime_note": realtime_info.get("note"),
        "percentile": pe_info["percentile"],
        "realtime_percentile": _percentile_for_rating,
        "percentile_status": pe_info["status"],
        "pe_mean": pe_info["mean"],
        "pe_previous": pe_info["previous"],
        "sample_size": pe_info["sample_size"],
        "peg": peg_info["peg"],
        "eps_yoy": peg_info["eps_yoy"],
        "peg_status": peg_info["status"],
        "industry_pe_mean": industry_info["industry_pe_mean"],
        "industry_peer_count": industry_info["peer_count"],
        "industry_status": industry_info["status"],
        "rating_stars": stars,
        "rating_label": rating_label,
        "data_as_of": pe_info["data_as_of"],
        "calculated_at": _now(),
    }
