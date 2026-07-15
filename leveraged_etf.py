"""
leveraged_etf.py — V7 槓桿ETF觀察與模擬（第一階段）
================================================================
只做使用者建議的「第一版畫面」範圍：
  1. 四檔正2 ETF即時觀察卡
  2. 四檔橫向比較
  3. 區間一次投入模擬（含手續費/證交稅/滑價）
  4. 淨值曲線＋回撤曲線（兩張圖分開，不用subplot）

明確不做（留給第二階段）：
  - 正2/反1/現金方向策略（另一份規格書的擴充）
  - 分批/自訂/訊號進場模擬
  - 紙上交易模擬倉
  - 攻擊引擎 leveraged_etf_entry_score
  - Tab1/Tab4/Tab7 串接

槓桿ETF不使用王者品質分/營收/EPS/產業護城河，全部改用獨立的
流動性/波動/回撤指標，這裡的函式完全不碰 rex_scores.json。
"""

import os
import time
import json
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_DIR = os.path.join(DATA_DIR, "prices")
CACHE_PATH = os.path.join(DATA_DIR, "leveraged_etf_price_cache.json")

# 目前只做四檔正2（反1商品是另一份規格的第二階段擴充，這裡先不加）
LEVERAGED_ETF_TICKERS = {
    "00631L": {"name": "元大台灣50正2", "benchmark": "台灣50指數", "leverage": 2, "direction": "long"},
    "00685L": {"name": "群益臺灣加權正2", "benchmark": "臺灣加權指數", "leverage": 2, "direction": "long"},
    "00663L": {"name": "國泰臺灣加權正2", "benchmark": "臺灣加權指數", "leverage": 2, "direction": "long"},
    "00675L": {"name": "富邦臺灣加權正2", "benchmark": "臺灣加權指數", "leverage": 2, "direction": "long"},
    # 反1商品：注意是「單日反向1倍」，不是反向2倍，畫面與計算都不得寫成反2
    "00632R": {"name": "元大台灣50反1", "benchmark": "台灣50指數", "leverage": -1, "direction": "short"},
    "00664R": {"name": "國泰臺灣加權反1", "benchmark": "臺灣加權指數", "leverage": -1, "direction": "short"},
    "00676R": {"name": "富邦臺灣加權反1", "benchmark": "臺灣加權指數", "leverage": -1, "direction": "short"},
    "00686R": {"name": "群益臺灣加權反1", "benchmark": "臺灣加權指數", "leverage": -1, "direction": "short"},
}

# 正2／反1配對關係（同一追蹤標的的多空對）
LONG_SHORT_PAIRS = {
    "00631L": "00632R",
    "00663L": "00664R",
    "00675L": "00676R",
    "00685L": "00686R",
}

LONG_TICKERS = [t for t, i in LEVERAGED_ETF_TICKERS.items() if i["direction"] == "long"]
SHORT_TICKERS = [t for t, i in LEVERAGED_ETF_TICKERS.items() if i["direction"] == "short"]

# 交易成本預設值，集中在這裡設定，不要散落在畫面程式碼裡
DEFAULT_FEE_RATE = 0.001425      # 券商手續費率（未折扣）
DEFAULT_FEE_DISCOUNT = 0.6       # 手續費折數（六折）
DEFAULT_TAX_RATE = 0.001         # ETF證交稅率（0.1%，非股票型ETF通用稅率）
DEFAULT_MIN_FEE = 20             # 單筆最低手續費（元）
DEFAULT_SLIPPAGE_PCT = 0.001     # 滑價估計（0.1%）

PRICE_CACHE_TTL_HOURS = 4


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    """
    【Windows耐用性修正】os.replace() 在Windows上如果目的檔案暫時被別的程式
    鎖住（防毒軟體掃描、OneDrive同步、另一個還開著的Streamlit進程），會直接
    拋出PermissionError讓整頁崩潰。改成重試幾次、每次間隔加長。
    """
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
# ▌ 價格資料載入：本機CSV優先，沒有才用yfinance即時抓（有磁碟快取）
# ══════════════════════════════════════════════════════════════

def load_price_history(ticker, period_days=730):
    """
    回傳 DataFrame(date index, Close/High/Low/Open/Volume) 或 None。
    優先讀 data/prices/{ticker}.csv（跟主系統其他個股共用路徑，
    daily_update.yml排程未來會把這四檔也納入每日更新）；
    本機沒有資料時，退回yfinance即時抓取並存磁碟快取（4小時），
    避免每次都重打API，也讓這個模組在還沒排程更新前就能先用。
    """
    local_path = os.path.join(PRICES_DIR, f"{ticker}.csv")
    if os.path.exists(local_path):
        try:
            df = pd.read_csv(local_path)
            if "date" in df.columns and len(df) >= 20:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.dropna(subset=["date"]).set_index("date").sort_index()
                latest = df.index[-1]
                if (pd.Timestamp.now() - latest).days <= 5:
                    return df
        except Exception:
            pass

    # 本機CSV沒有或太舊 → 退回yfinance，磁碟快取4小時
    cache = _load_json(CACHE_PATH, {})
    cached_entry = cache.get(ticker)
    if cached_entry and cached_entry.get("fetched_at"):
        try:
            age_h = (datetime.now() - datetime.strptime(
                cached_entry["fetched_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            if age_h < PRICE_CACHE_TTL_HOURS:
                df = pd.DataFrame(cached_entry["data"])
                df["date"] = pd.to_datetime(df["date"])
                return df.set_index("date").sort_index()
        except Exception:
            pass

    try:
        import yfinance as yf
        hist = yf.Ticker(f"{ticker}.TW").history(period=f"{period_days}d")
        if hist is None or hist.empty:
            hist = yf.Ticker(f"{ticker}.TWO").history(period=f"{period_days}d")
        if hist is None or hist.empty:
            return None
        hist = hist.reset_index()
        hist = hist.rename(columns={"Date": "date"})
        hist["date"] = pd.to_datetime(hist["date"]).dt.tz_localize(None)
        cache[ticker] = {
            "fetched_at": _now(),
            "data": hist[["date", "Open", "High", "Low", "Close", "Volume"]].assign(
                date=hist["date"].dt.strftime("%Y-%m-%d")
            ).to_dict("records"),
        }
        _save_json(CACHE_PATH, cache)
        return hist.set_index("date").sort_index()
    except Exception:
        return None


def get_data_freshness(df):
    """回傳 (最新資料日期字串, 是否過期>5天)"""
    if df is None or df.empty:
        return None, True
    latest = df.index[-1]
    is_stale = (pd.Timestamp.now() - latest).days > 5
    return latest.strftime("%Y-%m-%d"), is_stale


# ══════════════════════════════════════════════════════════════
# ▌ 交易成本計算
# ══════════════════════════════════════════════════════════════

def calculate_trading_cost(amount, side="buy", fee_rate=DEFAULT_FEE_RATE,
                            fee_discount=DEFAULT_FEE_DISCOUNT, tax_rate=DEFAULT_TAX_RATE,
                            min_fee=DEFAULT_MIN_FEE, slippage_pct=DEFAULT_SLIPPAGE_PCT):
    """
    回傳 {"fee": 手續費, "tax": 證交稅, "slippage": 滑價成本, "total_cost": 合計}
    買進只收手續費+滑價，賣出才收證交稅。
    """
    fee = max(amount * fee_rate * fee_discount, min_fee)
    tax = amount * tax_rate if side == "sell" else 0.0
    slippage = amount * slippage_pct
    return {
        "fee": round(fee, 0), "tax": round(tax, 0), "slippage": round(slippage, 0),
        "total_cost": round(fee + tax + slippage, 0),
    }


# ══════════════════════════════════════════════════════════════
# ▌ 最大回撤
# ══════════════════════════════════════════════════════════════

def calculate_max_drawdown(series):
    """
    輸入淨值或價格的pandas Series（index為日期）。
    回傳 {"max_drawdown_pct": float, "max_drawdown_date": str, "peak_date": str}
    """
    if series is None or len(series) < 2:
        return {"max_drawdown_pct": None, "max_drawdown_date": None, "peak_date": None}
    running_max = series.cummax()
    drawdown = (series - running_max) / running_max
    trough_idx = drawdown.idxmin()
    dd_pct = float(drawdown.min()) * 100
    peak_idx = series[:trough_idx].idxmax() if trough_idx in series.index else series.idxmax()
    return {
        "max_drawdown_pct": round(dd_pct, 2),
        "max_drawdown_date": trough_idx.strftime("%Y-%m-%d") if hasattr(trough_idx, "strftime") else str(trough_idx),
        "peak_date": peak_idx.strftime("%Y-%m-%d") if hasattr(peak_idx, "strftime") else str(peak_idx),
    }


# ══════════════════════════════════════════════════════════════
# ▌ 一次投入模擬（Phase 1 唯一支援的買進方式）
# ══════════════════════════════════════════════════════════════

def simulate_lump_sum(ticker, start_date, end_date, initial_amount,
                       fee_rate=DEFAULT_FEE_RATE, fee_discount=DEFAULT_FEE_DISCOUNT,
                       tax_rate=DEFAULT_TAX_RATE, min_fee=DEFAULT_MIN_FEE,
                       slippage_pct=DEFAULT_SLIPPAGE_PCT):
    """
    起始日一次買進，計算到結束日的績效。使用ETF實際歷史價格，
    不使用「大盤區間報酬×2」這種捷徑。
    回傳 dict，找不到資料或日期範圍內無交易日時 "error" 有值。
    """
    df = load_price_history(ticker)
    if df is None or df.empty:
        return {"error": f"{ticker} 沒有可用的價格資料"}

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    window = df[(df.index >= start_ts) & (df.index <= end_ts)]
    if window.empty:
        return {"error": f"{start_date}～{end_date} 區間內沒有交易日資料"}

    # 【依Rex指示新增】模擬區間如果橫跨分割／反分割事件，起訖兩端的價格
    # 基準不同（分割前後不能直接比），算出來的股數/報酬會是假的。
    # 先偵測區間內有沒有單日變動超過50%的斷崖，有就直接擋掉，不產生錯誤數字。
    window_ret = window["Close"].astype(float).pct_change().dropna()
    _split_in_window = window_ret[window_ret.abs() > 0.5]
    if not _split_in_window.empty:
        _split_dates_str = "、".join(
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            for d in _split_in_window.index
        )
        return {
            "error": f"模擬區間內偵測到疑似分割／反分割事件（{_split_dates_str}），"
                     "起訖兩端價格基準不同，無法直接計算報酬率。請將模擬區間限制在"
                     "分割前或分割後的單一區間內，或先確認實際分割比例後再處理。"
        }

    entry_date = window.index[0]
    entry_price = float(window["Close"].iloc[0])
    exit_date = window.index[-1]
    exit_price = float(window["Close"].iloc[-1])

    cost = calculate_trading_cost(initial_amount, side="buy", fee_rate=fee_rate,
                                   fee_discount=fee_discount, tax_rate=tax_rate,
                                   min_fee=min_fee, slippage_pct=slippage_pct)
    net_invest = initial_amount - cost["fee"] - cost["slippage"]
    shares = net_invest / entry_price if entry_price else 0

    final_value_gross = shares * exit_price
    sell_cost = calculate_trading_cost(final_value_gross, side="sell", fee_rate=fee_rate,
                                        fee_discount=fee_discount, tax_rate=tax_rate,
                                        min_fee=min_fee, slippage_pct=slippage_pct)
    final_value_net = final_value_gross - sell_cost["fee"] - sell_cost["tax"] - sell_cost["slippage"]

    total_pnl = final_value_net - initial_amount
    total_return_pct = (total_pnl / initial_amount * 100) if initial_amount else 0

    days_held = (exit_date - entry_date).days
    years_held = max(days_held / 365.25, 1 / 365.25)
    annualized_return_pct = (
        ((final_value_net / initial_amount) ** (1 / years_held) - 1) * 100
        if initial_amount > 0 and final_value_net > 0 else None
    )

    nav_series = window["Close"] / entry_price * net_invest
    dd = calculate_max_drawdown(nav_series)

    daily_ret = window["Close"].pct_change().dropna()
    annual_vol_pct = float(daily_ret.std() * np.sqrt(252) * 100) if len(daily_ret) > 1 else None
    sharpe = (
        round((annualized_return_pct / annual_vol_pct), 2)
        if annualized_return_pct is not None and annual_vol_pct not in (None, 0) else None
    )

    total_trading_cost = cost["total_cost"] + sell_cost["total_cost"]

    return {
        "ticker": ticker, "name": LEVERAGED_ETF_TICKERS.get(ticker, {}).get("name", ticker),
        "start_date": entry_date.strftime("%Y-%m-%d"), "end_date": exit_date.strftime("%Y-%m-%d"),
        "trading_days": len(window),
        "initial_investment": round(initial_amount, 0),
        "cumulative_investment": round(initial_amount, 0),  # 一次投入=累計投入
        "shares": round(shares, 2),
        "avg_cost": round(entry_price, 3),
        "entry_price": round(entry_price, 3), "exit_price": round(exit_price, 3),
        "final_market_value": round(final_value_gross, 0),
        "unrealized_pnl": round(final_value_gross - initial_amount, 0),
        "realized_pnl": 0,
        "total_pnl": round(total_pnl, 0),
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return_pct": round(annualized_return_pct, 2) if annualized_return_pct is not None else None,
        "max_drawdown_pct": dd["max_drawdown_pct"], "max_drawdown_date": dd["max_drawdown_date"],
        "annual_volatility_pct": round(annual_vol_pct, 2) if annual_vol_pct is not None else None,
        "sharpe_ratio": sharpe,
        "fee_total": round(cost["fee"] + sell_cost["fee"], 0),
        "tax_total": round(sell_cost["tax"], 0),
        "slippage_total": round(cost["slippage"] + sell_cost["slippage"], 0),
        "total_trading_cost": round(total_trading_cost, 0),
        "estimated_market_exposure": round(
            final_value_gross * LEVERAGED_ETF_TICKERS.get(ticker, {}).get("leverage", 2), 0
        ),
        "nav_series": nav_series,  # 供畫淨值曲線用，不放進JSON輸出
    }


# ══════════════════════════════════════════════════════════════
# ▌ 四檔即時觀察卡 ＋ 橫向比較
# ══════════════════════════════════════════════════════════════

def _pct_return(series, days):
    if series is None or len(series) <= days:
        return None
    base = float(series.iloc[-(days + 1)])
    last = float(series.iloc[-1])
    return round((last - base) / base * 100, 2) if base else None


def detect_price_anomaly(close_series, window=20, threshold_pct=50):
    """
    抓最近window天內是否有單日變動超過threshold_pct%的離群值。
    槓桿ETF（2倍）就算大盤單日跌停(-10%)，理論上也頂多約-20%，
    單日變動超過50%基本上代表發生了分割／反分割事件（發行公司為了
    維持價格在合理區間會這樣做，是正式公告的事件，不是隨機髒資料），
    或是yfinance沒有正確套用分割調整。
    回傳 (has_anomaly: bool, anomaly_dates: list[str])
    """
    if close_series is None or len(close_series) < 2:
        return False, []
    recent = close_series.tail(window + 1)
    daily_ret = recent.pct_change().dropna()
    anomalies = daily_ret[daily_ret.abs() > (threshold_pct / 100)]
    if anomalies.empty:
        return False, []
    dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in anomalies.index]
    return True, dates


def get_clean_price_series(close_series, lookback_days=90):
    """
    【依Rex指示】偵測到分割斷崖時，不是整組停用指標，而是只取
    「最近一次分割之後」的乾淨價格序列當計算基礎——以分割後的現值
    為判斷值，分割前的價格不能拿來跟分割後的價格直接算報酬率/回撤，
    否則會出現-95%這種假訊號。
    回傳 (clean_series, split_detected: bool, split_date: str|None)
    """
    if close_series is None or len(close_series) < 2:
        return close_series, False, None
    lookback = close_series.tail(lookback_days + 1)
    daily_ret = lookback.pct_change().dropna()
    anomalies = daily_ret[daily_ret.abs() > 0.5]
    if anomalies.empty:
        return close_series, False, None
    last_anomaly_date = anomalies.index[-1]
    clean = close_series[close_series.index > last_anomaly_date]
    split_date_str = (last_anomaly_date.strftime("%Y-%m-%d")
                       if hasattr(last_anomaly_date, "strftime") else str(last_anomaly_date))
    return clean, True, split_date_str


def get_etf_snapshot(ticker):
    """單檔即時觀察卡資料。買賣價差/折溢價目前無資料來源，誠實顯示「資料尚未接入」"""
    info = LEVERAGED_ETF_TICKERS.get(ticker, {})
    df = load_price_history(ticker)
    if df is None or df.empty:
        return {
            "ticker": ticker, "name": info.get("name", ticker), "data_available": False,
            "note": "價格資料尚未接入",
        }

    close_raw = df["Close"].astype(float)

    # 【依Rex指示修正】偵測到分割斷崖時，不整組停用，改成只用分割後的
    # 乾淨資料序列（以分割後現值為判斷基礎）算後續指標。分割前的天數
    # 不夠計算20/60日等指標時，該指標會是None（誠實顯示不足，不是假裝正常）。
    close, split_detected, split_date = get_clean_price_series(close_raw)
    split_note = None
    if split_detected:
        split_note = (f"偵測到 {split_date} 疑似發生分割／反分割事件（單日價格變動超過50%），"
                       "已自動改用分割後的價格資料計算指標，分割前資料不納入計算。")

    vol = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(dtype=float)
    if split_detected:
        vol = vol[vol.index.isin(close.index)]  # 成交量也只取分割後的部分，避免股數基準不一致

    latest_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else latest_close
    daily_return_pct = round((latest_close - prev_close) / prev_close * 100, 2) if prev_close else None

    latest_vol = float(vol.iloc[-1]) if len(vol) else None
    turnover = latest_vol * latest_close if latest_vol is not None else None
    avg_vol_20 = float(vol.tail(20).mean()) if len(vol) >= 20 else None
    avg_turnover_20 = float((vol.tail(20) * close.tail(20)).mean()) if len(vol) >= 20 else None

    ret_5d = _pct_return(close, 5)
    ret_20d = _pct_return(close, 20)
    ret_60d = _pct_return(close, 60)

    daily_ret_20 = close.tail(21).pct_change().dropna()
    vol_20d = round(float(daily_ret_20.std() * np.sqrt(252) * 100), 2) if len(daily_ret_20) > 1 else None

    dd_20 = calculate_max_drawdown(close.tail(20)) if len(close) >= 2 else {"max_drawdown_pct": None}
    high_60 = float(close.tail(60).max()) if len(close) >= 1 else None
    dist_from_high_pct = round((latest_close - high_60) / high_60 * 100, 2) if high_60 else None

    date_str, is_stale = get_data_freshness(df)

    return {
        "ticker": ticker, "name": info.get("name", ticker), "data_available": True,
        "data_anomaly": split_detected, "split_date": split_date, "note": split_note,
        "latest_price": latest_close, "daily_return_pct": daily_return_pct,
        "volume": latest_vol, "turnover": turnover,
        "bid": None, "ask": None, "bid_ask_spread_pct": None,  # 資料尚未接入
        "premium_discount_pct": None,  # 資料尚未接入
        "avg_volume_20d": avg_vol_20, "avg_turnover_20d": avg_turnover_20,
        "volatility_20d_pct": vol_20d, "max_drawdown_20d_pct": dd_20["max_drawdown_pct"],
        "dist_from_high_pct": dist_from_high_pct,
        "return_5d_pct": ret_5d, "return_20d_pct": ret_20d, "return_60d_pct": ret_60d,
        "data_as_of": date_str, "is_stale": is_stale,
        "post_split_days_available": len(close) if split_detected else None,
    }


def compare_etfs(tickers=None):
    """
    橫向比較，回傳 list[dict]，供表格顯示。
    tickers=None 時預設只比較四檔正2（向後相容原本Tab8行為）；
    傳入 LONG_TICKERS/SHORT_TICKERS 可分別取得正2/反1清單。
    """
    if tickers is None:
        tickers = LONG_TICKERS
    rows = []
    for ticker in tickers:
        snap = get_etf_snapshot(ticker)
        rows.append(snap)
    return rows


def get_market_state_note(attack_engine_module=None):
    """
    查市場層級證據衝突（沿用 attack_engine 的market conflict證據），
    決定顯示「僅限模擬觀察」還是可以進一步討論。第一階段只做這個
    最簡單的二態判斷，完整的市場方向分數/可信度分數留給第二階段。
    """
    if attack_engine_module is None:
        return {"state": "未知", "strategy": "僅限模擬觀察", "reason": "尚未串接攻擊引擎市場證據"}
    try:
        conflict_evs = attack_engine_module.get_valid_evidence("market", category="conflict")
        ce = next((e for e in conflict_evs if e["id"] == "evidence_conflict"), None)
        if ce and ce.get("value", {}).get("state") == "證據衝突":
            conflicts = ce["value"].get("conflicts", [])
            return {
                "state": "證據衝突", "strategy": "僅限模擬觀察",
                "reason": "；".join(conflicts) if conflicts else "市場證據衝突，暫不建議正式進場",
            }
        market_result = attack_engine_module.calculate_market_attack_state()
        if market_result.get("hard_veto"):
            return {"state": "硬性否決", "strategy": "禁止進場", "reason": "市場層級硬性否決已觸發"}
        if market_result.get("stage") in ("防守", "攻擊準備"):
            return {"state": "防守／攻擊準備", "strategy": "僅限模擬觀察",
                    "reason": f"市場攻擊分數{market_result.get('total_score',0):.0f}分，未達進場門檻"}
        return {"state": market_result.get("stage", "—"), "strategy": "可考慮小額試單",
                "reason": "市場層級分數已達門檻，仍需個別確認槓桿ETF流動性條件"}
    except Exception as e:
        return {"state": "未知", "strategy": "僅限模擬觀察", "reason": f"查詢市場狀態時發生問題（{e}）"}
