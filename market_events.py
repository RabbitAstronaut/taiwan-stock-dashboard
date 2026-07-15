"""
market_events.py — V7 攻擊引擎：盤中價格行為 / 布林通道擴張 / 期貨曝險 / 證據衝突
================================================================================
把「盤中V形反彈」拆成三種不可混為一談的證據，分開計算再交叉比對：

  1. 盤中價格行為（本檔 calculate_intraday_recovery_metrics /
     classify_intraday_reversal）
  2. 日線布林通道擴張（本檔 calculate_bollinger_extended /
     classify_bollinger_event，沿用 app.py 既有 calc_indicators() 算出的
     收盤價布林，本模組不重算布林本身，只做斜率/寬度等衍生指標）
  3. 籌碼與期貨（本檔 normalize_index_futures_exposure，大中小台不可直接
     加總口數，換算成契約金額後才能合計）

最後用 evaluate_market_evidence_conflict() 交叉比對，允許輸出「證據衝突」，
不強迫給多方或空方結論。

任何具體日期的數字都是呼叫端傳入的即時資料，本模組不寫死任何日期或價位。
"""

import os
import json
from datetime import date, datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PIVOT_HISTORY_PATH = os.path.join(DATA_DIR, "market_pivot_history.json")
PRICE_EVENTS_PATH = os.path.join(DATA_DIR, "market_price_events.json")

# 台指期貨契約乘數（新台幣／點）。微台目前系統無資料來源，保留欄位供未來接入。
FUTURES_MULTIPLIER = {"large": 200, "small": 50, "micro": 10}


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _json_safe_default(obj):
    """numpy/pandas純量防呆轉換，避免忘記轉型時整包寫入失敗"""
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_json_safe_default)
    os.replace(tmp, path)
    return True


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ══════════════════════════════════════════════════════════════
# ▌ 一、盤中價格收復指標
# ══════════════════════════════════════════════════════════════

def calculate_intraday_recovery_metrics(previous_close, open_price, high_price, low_price, close_price):
    """
    回傳 dict，任何無效輸入都回傳 valid=False，不會拋例外中斷呼叫端。
    """
    result = {
        "max_intraday_drawdown_pct": None, "close_return_pct": None,
        "recovery_ratio": None, "close_location_value": None,
        "lower_shadow_ratio": None, "valid": False,
    }
    try:
        pc = float(previous_close); o = float(open_price)
        h = float(high_price); l = float(low_price); c = float(close_price)
    except (TypeError, ValueError):
        return result
    if pc <= 0 or h <= 0 or l <= 0 or c <= 0:
        return result
    if h < l:
        h, l = l, h  # 防呆：高低顛倒時互換

    result["max_intraday_drawdown_pct"] = round((l - pc) / pc * 100, 2)
    result["close_return_pct"] = round((c - pc) / pc * 100, 2)

    if pc > l:
        result["recovery_ratio"] = round((c - l) / (pc - l) * 100, 1)  # 可能>100（收盤高於前收）
    else:
        result["recovery_ratio"] = None  # 當日最低未跌破前收，沒有「跌幅收復」的概念

    if h == l:
        result["close_location_value"] = None
        result["lower_shadow_ratio"] = None
    else:
        result["close_location_value"] = round((c - l) / (h - l), 3)
        result["lower_shadow_ratio"] = round((min(o, c) - l) / (h - l), 3)

    result["valid"] = True
    return result


def classify_intraday_reversal(metrics):
    """回傳：無明顯反轉／弱勢收復／中度承接／強烈承接／盤中完全反轉／資料不足"""
    if not metrics or not metrics.get("valid"):
        return "資料不足"
    recovery = metrics.get("recovery_ratio")
    clv = metrics.get("close_location_value")
    close_ret = metrics.get("close_return_pct")

    if close_ret is not None and close_ret >= 0:
        return "盤中完全反轉"
    if recovery is None:
        return "無明顯反轉"
    if recovery >= 100:
        return "盤中完全反轉"
    if recovery >= 60 and (clv is None or clv >= 0.2):
        return "強烈承接"
    if recovery >= 40 and (clv is None or clv >= 0.2):
        return "中度承接"
    if recovery >= 20:
        return "弱勢收復"
    return "無明顯反轉"


# ══════════════════════════════════════════════════════════════
# ▌ 二、布林通道擴張指標（沿用既有收盤價布林，不重算布林本身）
# ══════════════════════════════════════════════════════════════

def calculate_bollinger_extended(bb_mid_today, lb1_today, lb1_prev, lb1_3d_ago,
                                  ub1_today, low_price_today, close_price_today,
                                  close_below_streak_prev=0):
    """
    輸入皆為既有 calc_indicators() 算出的收盤價布林值（BB_MID / UB2(=upper1) / LB2(=lower1)）。
    本函式只負責算「擴張」相關的衍生指標，不重新定義布林本身。

    close_below_streak_prev: 呼叫端傳入「昨天為止」收盤已連續跌破下軌幾天，
    本函式據此推算「今天為止」的連續天數，並回傳更新後的值。
    """
    out = {
        "bandwidth": None, "lower_band_slope_1d": None, "lower_band_slope_3d": None,
        "bandwidth_change_pct": None, "intraday_lower_band_breach_pct": None,
        "close_reclaimed_lower_band": None, "close_below_lower_band": None,
        "close_below_streak": close_below_streak_prev, "is_expanding": False, "valid": False,
    }
    try:
        bb_mid = float(bb_mid_today); lb1 = float(lb1_today); ub1 = float(ub1_today)
        low_p = float(low_price_today); close_p = float(close_price_today)
    except (TypeError, ValueError):
        return out
    if bb_mid == 0:
        return out

    bandwidth = (ub1 - lb1) / bb_mid
    out["bandwidth"] = round(bandwidth, 4)

    if lb1_prev is not None:
        out["lower_band_slope_1d"] = round(lb1 - float(lb1_prev), 2)
    if lb1_3d_ago is not None:
        out["lower_band_slope_3d"] = round(lb1 - float(lb1_3d_ago), 2)

    out["intraday_lower_band_breach_pct"] = round((low_p - lb1) / lb1 * 100, 2) if lb1 else None
    out["close_reclaimed_lower_band"] = bool(close_p >= lb1)
    out["close_below_lower_band"] = bool(close_p < lb1)

    out["close_below_streak"] = (close_below_streak_prev + 1) if out["close_below_lower_band"] else 0

    out["valid"] = True
    return out


def update_bandwidth_change(bandwidth_today, bandwidth_prev):
    if bandwidth_today is None or not bandwidth_prev:
        return None
    return round((bandwidth_today - bandwidth_prev) / bandwidth_prev * 100, 2)


def is_lower_band_expanding(slope_3d, bandwidth_change_pct, close_below_streak):
    """
    綜合判斷「下軌是否開始向下擴張」：不能只看下軌比昨天低。
    至少同時滿足：三日斜率為負、布林寬度明顯增加、且已連續收盤跌破下軌。
    """
    if slope_3d is None or bandwidth_change_pct is None:
        return False
    return slope_3d < 0 and bandwidth_change_pct > 3.0 and close_below_streak >= 2


def classify_bollinger_event(low_price, close_price, lower_band_1, expanding,
                              close_below_streak, was_below_recently):
    """回傳：通道內正常波動／盤中刺穿後收回／收盤跌破但尚未擴張／下軌開始擴張／沿下軌加速下跌／超賣後回收／資料不足"""
    if low_price is None or close_price is None or lower_band_1 is None:
        return "資料不足"

    if close_price >= lower_band_1:
        if low_price < lower_band_1:
            return "盤中刺穿後收回"
        if was_below_recently and close_below_streak == 0:
            return "超賣後回收"
        return "通道內正常波動"
    else:
        if expanding:
            return "沿下軌加速下跌" if close_below_streak >= 2 else "下軌開始擴張"
        return "收盤跌破但尚未擴張"


# ══════════════════════════════════════════════════════════════
# ▌ 三、期貨曝險換算與結算日降權
# ══════════════════════════════════════════════════════════════

def normalize_index_futures_exposure(large_net_lots=None, small_net_lots=None, micro_net_lots=None,
                                      index_price=None, prev_large_net_lots=None,
                                      prev_small_net_lots=None, prev_micro_net_lots=None):
    """
    大台、小台、微台不可直接加總口數，先換算契約金額（新台幣）再合計，
    合計金額再換算回「大台等值口數」方便閱讀。缺料的契約自動略過，不擋整體計算。
    """
    m = FUTURES_MULTIPLIER

    def _value(lots, mult):
        if lots is None or index_price is None:
            return None
        return lots * mult * index_price

    large_value = _value(large_net_lots, m["large"])
    small_value = _value(small_net_lots, m["small"])
    micro_value = _value(micro_net_lots, m["micro"])
    values = [v for v in (large_value, small_value, micro_value) if v is not None]
    total_value = sum(values) if values else None
    large_equiv_lots = (round(total_value / (m["large"] * index_price), 1)
                         if total_value is not None and index_price else None)

    def _delta(now, prev):
        if now is None or prev is None:
            return None
        return now - prev

    large_change = _delta(large_net_lots, prev_large_net_lots)
    small_change = _delta(small_net_lots, prev_small_net_lots)
    micro_change = _delta(micro_net_lots, prev_micro_net_lots)

    if large_change is None:
        posture = "無前日資料可比較"
    elif large_change < 0:
        posture = "新增淨空"
    elif large_change > 0:
        posture = "空單回補或轉多"
    else:
        posture = "與前日持平"

    return {
        "large_net_lots": large_net_lots, "small_net_lots": small_net_lots, "micro_net_lots": micro_net_lots,
        "large_value_ntd": large_value, "small_value_ntd": small_value, "micro_value_ntd": micro_value,
        "total_value_ntd": total_value, "large_equivalent_lots": large_equiv_lots,
        "large_change_lots": large_change, "small_change_lots": small_change, "micro_change_lots": micro_change,
        "posture": posture,
        "micro_available": micro_net_lots is not None,
    }


def get_third_wednesday(year, month):
    for day in range(15, 22):  # 第三個星期三必定落在15~21號之間
        d = date(year, month, day)
        if d.weekday() == 2:
            return d
    return None  # 理論上不會發生


def is_near_futures_settlement(as_of=None):
    """回傳 (settlement_date, distance_days)；distance_days為距離下一個結算日的交易日曆天數（未扣週末假日精算）"""
    as_of = as_of or date.today()
    settle = get_third_wednesday(as_of.year, as_of.month)
    if settle is None or as_of > settle:
        ny, nm = (as_of.year, as_of.month + 1) if as_of.month < 12 else (as_of.year + 1, 1)
        settle = get_third_wednesday(ny, nm)
    distance = (settle - as_of).days
    return settle, distance


def get_futures_signal_weight(distance_days):
    """一般交易日權重100%；結算日前一交易日至結算日權重60%（50~70%區間取中）"""
    if distance_days is None:
        return 1.0, False
    if 0 <= distance_days <= 1:
        return 0.6, True
    return 1.0, False


# ══════════════════════════════════════════════════════════════
# ▌ 四、價格確認暫定分數（20分拆解）
# ══════════════════════════════════════════════════════════════

def calculate_price_confirmation_score(*, recovery_ratio=None, close_location_value=None,
                                        pierced_and_reclaimed=False, closed_back_above_lb1=False,
                                        next_day_held_lb1=False,
                                        days_without_new_low=0, formed_higher_low=False,
                                        reclaimed_ma5=False, reclaimed_ma10=False,
                                        ma_turning_up=False, relative_strength_positive=False,
                                        breadth_improving=False, new_low_ratio_falling=False,
                                        leaders_not_breaking_low=False, leaders_reclaimed_short_ma=False):
    """
    價格確認滿分20分，拆成5個子項；未滿三日不破低一律標記 is_provisional=True，
    表示這是「暫定分數」，不可單獨用來觸發第一擊以上階段。
    """
    s1 = 0
    if recovery_ratio is not None:
        if recovery_ratio >= 40:
            s1 += 1
        if recovery_ratio >= 60:
            s1 += 1
        if (close_location_value is not None and close_location_value >= 0.6
                and recovery_ratio >= 40):
            s1 += 1
    s1 = min(s1, 3)

    s2 = int(bool(pierced_and_reclaimed)) + int(bool(closed_back_above_lb1)) + int(bool(next_day_held_lb1))
    s2 = min(s2, 3)

    s3 = 0
    if days_without_new_low >= 1:
        s3 += 1
    if days_without_new_low >= 2:
        s3 += 1
    if days_without_new_low >= 3:
        s3 += 2
    if formed_higher_low:
        s3 += 1
    s3 = min(s3, 5)

    s4 = (int(bool(reclaimed_ma5)) + int(bool(reclaimed_ma10))
          + int(bool(ma_turning_up)) + int(bool(relative_strength_positive)))
    s4 = min(s4, 5)

    s5 = (int(bool(breadth_improving)) + int(bool(new_low_ratio_falling))
          + int(bool(leaders_not_breaking_low)) + int(bool(leaders_reclaimed_short_ma)))
    s5 = min(s5, 4)

    total = min(s1 + s2 + s3 + s4 + s5, 20)
    is_provisional = days_without_new_low < 3

    return {
        "total": total, "is_provisional": is_provisional,
        "detail": {
            "intraday_acceptance": s1, "back_inside_band": s2, "low_confirmation": s3,
            "trend_repair": s4, "breadth_leaders": s5,
        },
    }


# ══════════════════════════════════════════════════════════════
# ▌ 五、關鍵低點事件追蹤（market_pivot_event，持久化）
# ══════════════════════════════════════════════════════════════

def load_pivot_history():
    return _load_json(PIVOT_HISTORY_PATH, [])


def update_pivot_events(event_date, intraday_low, close_price, previous_close,
                         recovery_metrics, bollinger_state, futures_posture):
    """
    每個交易日呼叫一次：
      1. 檢查既有未失效事件是否被跌破（失效）或連續三日不破低（升級為「低點確認」）
      2. 若今日出現「跌幅收復>=60%」的價格行為，建立新的「恐慌低點候選」事件
    """
    history = load_pivot_history()
    updated = []
    for ev in history:
        if ev.get("event_date") == event_date:
            updated.append(ev)  # 同一天不重複處理
            continue
        if ev.get("invalidated"):
            updated.append(ev)
            continue
        if intraday_low is not None and intraday_low < ev["intraday_low"]:
            ev["invalidated"] = True
            ev["invalidated_date"] = event_date
            ev["confirmation_status"] = "invalidated"
            ev["event_type"] = "低點失效"
        else:
            ev["days_without_new_low"] = ev.get("days_without_new_low", 0) + 1
            if intraday_low is not None and ev.get("_higher_low_low") is not None and intraday_low > ev["_higher_low_low"]:
                ev["formed_higher_low"] = True
            if ev["days_without_new_low"] >= 3 and ev.get("confirmation_status") != "confirmed":
                ev["confirmation_status"] = "confirmed"
                ev["event_type"] = "低點確認"
        updated.append(ev)

    recovery_ratio = (recovery_metrics or {}).get("recovery_ratio")
    if recovery_ratio is not None and recovery_ratio >= 60:
        already_today = any(e["event_date"] == event_date and not e.get("invalidated") for e in updated)
        if not already_today:
            updated.append({
                "event_date": event_date,
                "event_type": ("長下影線候選" if (recovery_metrics or {}).get("lower_shadow_ratio", 0) >= 0.5
                               else "恐慌低點候選"),
                "intraday_low": intraday_low, "close_price": close_price, "previous_close": previous_close,
                "recovery_ratio": recovery_ratio,
                "lower_shadow_ratio": (recovery_metrics or {}).get("lower_shadow_ratio"),
                "bollinger_state": bollinger_state, "foreign_futures_state": futures_posture,
                "confirmation_status": "pending", "days_without_new_low": 0,
                "formed_higher_low": False, "_higher_low_low": intraday_low,
                "invalidated": False, "invalidated_date": None,
            })

    _save_json(PIVOT_HISTORY_PATH, updated)
    return updated


def get_active_pivot_event():
    """回傳最新一筆尚未失效的事件（供 Tab1/Tab7/Tab4顯示），沒有則回傳 None"""
    history = load_pivot_history()
    active = [e for e in history if not e.get("invalidated")]
    if not active:
        return None
    return sorted(active, key=lambda e: e["event_date"])[-1]


# ══════════════════════════════════════════════════════════════
# ▌ 六、市場證據衝突引擎
# ══════════════════════════════════════════════════════════════

def evaluate_market_evidence_conflict(*, intraday_reversal_state=None, recovery_ratio=None,
                                       bollinger_event_state=None, lower_band_expanding=False,
                                       foreign_futures_change=None, foreign_cash_flow=None,
                                       market_breadth=None, volume_state=None,
                                       settlement_day_flag=False, fundamental_veto=False):
    """
    交叉比對價格／布林／籌碼三類獨立證據，允許輸出「證據衝突」，不強迫多空。
    回傳 {"state": "證據衝突"|"無明顯衝突", "conflicts": [文字說明,...]}
    """
    conflicts = []

    strong_accept = (intraday_reversal_state in ("強烈承接", "盤中完全反轉")
                      or (recovery_ratio is not None and recovery_ratio >= 60))
    futures_bearish = foreign_futures_change is not None and foreign_futures_change < 0
    cash_bearish = foreign_cash_flow is None or foreign_cash_flow < 0
    if strong_accept and futures_bearish and cash_bearish:
        conflicts.append("價格出現低檔承接，但外資避險未退。")

    pierced_not_expanding = (bollinger_event_state in ("盤中刺穿後收回", "收盤跌破但尚未擴張")
                              and not lower_band_expanding)
    if pierced_not_expanding:
        conflicts.append("盤中波動衝擊尚未轉化為日線空頭擴張。")

    if fundamental_veto and intraday_reversal_state in ("強烈承接", "盤中完全反轉"):
        conflicts.append("價格反彈不能解除基本面否決。")

    state = "證據衝突" if conflicts else "無明顯衝突"
    return {"state": state, "conflicts": conflicts, "settlement_day_flag": settlement_day_flag}


# ══════════════════════════════════════════════════════════════
# ▌ 七、每日事件保存（market_price_events.json，供回溯）
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# ▌ 大盤本益比自動估算（TWSE OpenAPI，個股本益比中位數，非官方單一數字）
#   TWSE沒有穩定公開的「單一大盤本益比」JSON API，但OpenAPI有
#   BWIBBU_ALL（全上市股票每日本益比/殖利率/股價淨值比），可以自己
#   算中位數當估計值。清楚標示這是「估計」不是「官方公布數字」，
#   Rex如果查到TWSE月報的正式數字，一樣可以手動覆蓋。
# ══════════════════════════════════════════════════════════════

MARKET_PE_PROXY_PATH = os.path.join(DATA_DIR, "market_pe_proxy.json")
MARKET_PE_PROXY_CACHE_HOURS = 20  # 一天抓一次即可（該API為前一交易日資料）


def fetch_market_pe_proxy(force=False):
    """
    回傳 {"pe_median": float|None, "sample": int, "fetched_at": str, "status": str}
    抓不到就回傳 pe_median=None，呼叫端要自己處理「抓不到就讓Rex手動輸入」。
    """
    cached = _load_json(MARKET_PE_PROXY_PATH, {})
    if not force and cached.get("fetched_at"):
        try:
            age_h = (datetime.now() - datetime.strptime(cached["fetched_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            if age_h < MARKET_PE_PROXY_CACHE_HOURS:
                return cached
        except Exception:
            pass

    try:
        import urllib.request
        url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        req = urllib.request.Request(url, headers={"User-Agent": "TaiwanStockDashboard-V7 (personal research use)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        pe_vals = []
        for row in data:
            try:
                pe = float(row.get("PEratio", "") or 0)
                if 0 < pe < 150:  # 排除虧損股(本益比為負/0)與明顯異常極端值
                    pe_vals.append(pe)
            except (TypeError, ValueError):
                continue

        if not pe_vals:
            result = {"pe_median": None, "sample": 0, "fetched_at": _now(), "status": "無有效資料"}
        else:
            pe_vals.sort()
            n = len(pe_vals)
            median = pe_vals[n // 2] if n % 2 else (pe_vals[n // 2 - 1] + pe_vals[n // 2]) / 2
            result = {"pe_median": round(median, 2), "sample": n, "fetched_at": _now(), "status": "已更新"}
        _save_json(MARKET_PE_PROXY_PATH, result)
        return result
    except Exception as e:
        result = {"pe_median": None, "sample": 0, "fetched_at": _now(), "status": f"取得失敗（{type(e).__name__}）"}
        _save_json(MARKET_PE_PROXY_PATH, result)
        return result


def fetch_market_pe_from_wantgoo():
    """
    直接爬取玩股網「加權指數本益比」頁面的即時本益比數字。
    這個數字在頁面原始HTML就有（伺服器端渲染，不需要執行JS），
    比靠AI猜測可靠很多，優先用這個方法，失敗才退回Gemini搜尋。
    B級證據（非TWSE官方直接發布，是財經網站計算後公開呈現的數字）。
    """
    try:
        import requests
        import re as _re
        url = "https://www.wantgoo.com/index/0000/price-to-earning-river"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.wantgoo.com/",
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"pe": None, "status": f"HTTP {resp.status_code}（可能有反爬蟲機制擋掉自動化請求）",
                    "source": url}
        html = resp.text

        # 抓「本益比」緊接著的數字（頁面上緣的即時概況區塊），
        # 排除掉「本益比河流圖」這種後面接的是文字不是數字的連結文案
        m = _re.search(r"本益比[^\d]{0,20}(\d{1,3}\.\d{1,2})", html)
        if not m:
            return {"pe": None, "status": "頁面能連上但抓不到數字，可能頁面結構已變更", "source": url}

        pe = float(m.group(1))
        if not (5 < pe < 100):  # 合理性檢查
            return {"pe": None, "status": f"抓到異常數值({pe})，已捨棄", "source": url}

        return {"pe": pe, "status": "已取得", "source": url, "fetched_at": _now()}
    except Exception as e:
        return {"pe": None, "status": f"失敗（{type(e).__name__}：{e}）", "source": None}


def fetch_pe_via_gemini_search(gemini_api_key):
    """
    用Gemini的Google Search grounding功能，搜尋當前TWSE公布的加權指數本益比。
    這是D級證據（AI搜尋提取），不是官方直接發布數字，回傳值務必讓Rex快速核對
    一次再使用——但比每天手動查詢快很多。找不到金鑰或解析失敗都不會拋例外，
    回傳status說明原因，呼叫端自己決定要不要繼續往下用。
    """
    if not gemini_api_key:
        return {"pe": None, "status": "未設定GEMINI_API_KEY"}
    try:
        import requests
        import re as _re
        prompt = (
            "請搜尋台股「加權指數本益比」（大盤本益比，不是個股本益比）目前的數值。"
            "可以查「玩股網 加權指數 本益比河流圖」「財報狗 台股本益比」"
            "「CMoney 大盤本益比」這類財經網站，它們通常會有即時的加權指數本益比數字。"
            "只回答一個數字（到小數點第一位，例如 30.8），不要有任何其他文字說明或單位。"
            "如果找不到明確、有來源依據的數字，請回答「無法確定」。"
        )
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            f"?key={gemini_api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
            },
            timeout=30,
        )
        data = resp.json()
        text = (data.get("candidates", [{}])[0].get("content", {})
                .get("parts", [{}])[0].get("text", "")).strip()
        if not text or "無法確定" in text:
            return {"pe": None, "status": "AI無法確定明確數字", "raw_text": text}
        m = _re.search(r"(\d+\.?\d*)", text)
        if m:
            pe = float(m.group(1))
            if 5 < pe < 100:  # 合理性檢查，排除AI亂回答的離譜數字
                return {"pe": pe, "status": "已取得", "raw_text": text}
        return {"pe": None, "status": "AI回覆無法解析出有效數字", "raw_text": text}
    except Exception as e:
        return {"pe": None, "status": f"失敗（{type(e).__name__}）"}


def save_daily_market_event(event_date, payload):
    all_events = _load_json(PRICE_EVENTS_PATH, {})
    all_events[event_date] = payload
    _save_json(PRICE_EVENTS_PATH, all_events)
    return all_events


def load_daily_market_event(event_date):
    return _load_json(PRICE_EVENTS_PATH, {}).get(event_date)
