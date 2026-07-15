"""
direction_strategy.py — V7 台股方向型ETF策略：正2／反1／現金
================================================================
只算「市場該偏多、偏空、還是不確定」，完全不碰個股基本面、
不碰王者品質分。資料來源全部沿用 attack_engine.py 已經登記在
"market" 底下的證據（Tab1/market_events.py寫入的），不重新抓資料、
不重新定義另一套市場判斷邏輯。

輸出固定只能是五種狀態之一：
  正2 / 正2模擬觀察 / 現金觀察 / 反1模擬觀察 / 反1
證據衝突、資料不足或方向不明時，一律「現金觀察」，不強迫二選一。
"""

import os
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# ▌ 市場方向分數（-100～+100）
# ══════════════════════════════════════════════════════════════

DIRECTION_WEIGHTS = {
    "trend_structure": 30,    # 趨勢結構：布林事件狀態＋下軌是否擴張
    "price_structure": 25,    # 價格結構：盤中反轉狀態＋跌幅收復比例
    "market_breadth": 15,     # 市場廣度：尚未接入結構化證據，預設中性0分
    "foreign_chips": 15,      # 外資籌碼：期貨部位增減方向
    "valuation_risk": 15,     # 估值與風險偏好：估值風險釋放比例
}

_TREND_STRUCTURE_MAP = {
    "沿下軌加速下跌": -30, "下軌開始擴張": -18, "收盤跌破但尚未擴張": -8,
    "盤中刺穿後收回": 5, "超賣後回收": 18, "通道內正常波動": 0, "資料不足": 0,
}

_PRICE_STRUCTURE_MAP = {
    "盤中完全反轉": 25, "強烈承接": 18, "中度承接": 10, "弱勢收復": 3,
    "無明顯反轉": 0, "資料不足": 0,
}

_CHIPS_POSTURE_MAP = {
    "新增淨空": -1.0, "空單回補或轉多": 1.0, "與前日持平": 0.0, "無前日資料可比較": 0.0,
}

DIRECTION_STATE_BANDS = [
    (45, 100, "正2候選"),
    (20, 44, "偏多觀察"),
    (-19, 19, "中性或證據衝突"),
    (-44, -20, "偏空觀察"),
    (-100, -45, "反1候選"),
]

# 正式進場門檻
DIRECTION_SCORE_THRESHOLD = 45
CONFIDENCE_THRESHOLD = 60

ALLOWED_STRATEGY_STATES = ["正2", "正2模擬觀察", "現金觀察", "反1模擬觀察", "反1"]


def _get_market_evidence(attack_engine_module, category):
    """取market subject某個category底下最新一筆有效證據的value，取不到回傳{}"""
    try:
        evs = attack_engine_module.get_valid_evidence("market", category=category)
        if not evs:
            return {}
        return evs[-1].get("value", {}) or {}
    except Exception:
        return {}


def calculate_market_direction_score(attack_engine_module):
    """
    回傳 dict：{"total": -100~100, "breakdown": {...}, "band": 分類文字}
    五個分項權重固定：趨勢結構30/價格結構25/市場廣度15/外資籌碼15/估值風險15。
    任何一個分項沒有證據時，該分項算0分（中性），不會讓總分無法計算。
    """
    price_val = _get_market_evidence(attack_engine_module, "price")
    chips_val = _get_market_evidence(attack_engine_module, "chips")
    valuation_val = _get_market_evidence(attack_engine_module, "valuation")

    # 1. 趨勢結構（30）：布林事件狀態
    bollinger_state = price_val.get("bollinger_event_state", "資料不足")
    trend_raw = _TREND_STRUCTURE_MAP.get(bollinger_state, 0)
    trend_score = trend_raw  # 已經是-30~+30範圍，不需再乘權重

    # 2. 價格結構（25）：盤中反轉狀態
    reversal_state = price_val.get("intraday_reversal_state", "資料不足")
    price_score = _PRICE_STRUCTURE_MAP.get(reversal_state, 0)
    # 若收盤本身是負的，且沒有明顯承接，價格結構應偏負
    close_return = price_val.get("close_return_pct")
    if close_return is not None and close_return < 0 and reversal_state in ("無明顯反轉", "資料不足"):
        price_score = min(price_score, -8)

    # 3. 市場廣度（15）：目前沒有結構化的市場廣度證據可用，中性0分，誠實標示
    breadth_score = 0
    breadth_available = False

    # 4. 外資籌碼（15）：期貨部位增減方向
    posture = chips_val.get("posture")
    chips_score = round(_CHIPS_POSTURE_MAP.get(posture, 0.0) * DIRECTION_WEIGHTS["foreign_chips"], 1)

    # 5. 估值與風險偏好（15）：估值風險釋放比例，0.5為中性
    val_ratio = valuation_val.get("score_ratio")
    if val_ratio is not None:
        valuation_score = round((val_ratio - 0.5) * 2 * DIRECTION_WEIGHTS["valuation_risk"], 1)
    else:
        valuation_score = 0

    total = trend_score + price_score + breadth_score + chips_score + valuation_score
    total = max(-100, min(100, total))

    band = next((label for lo, hi, label in DIRECTION_STATE_BANDS if lo <= total <= hi), "中性或證據衝突")

    return {
        "total": round(total, 1),
        "band": band,
        "breakdown": {
            "trend_structure": trend_score, "price_structure": price_score,
            "market_breadth": breadth_score, "foreign_chips": chips_score,
            "valuation_risk": valuation_score,
        },
        "breadth_available": breadth_available,
        "raw_evidence": {
            "bollinger_event_state": bollinger_state, "intraday_reversal_state": reversal_state,
            "chips_posture": posture, "valuation_score_ratio": val_ratio,
        },
    }


# ══════════════════════════════════════════════════════════════
# ▌ 訊號可信度（0～100）
# ══════════════════════════════════════════════════════════════

def calculate_direction_confidence_score(attack_engine_module, market_events_module):
    """
    回傳 dict：{"total": 0~100, "breakdown": {...}, "missing_checks": [尚未接入的檢查項]}
    起始基準分50，依已接入的證據加減分；未接入的檢查項清楚列出，不假裝有算。
    """
    price_val = _get_market_evidence(attack_engine_module, "price")
    conflict_val = _get_market_evidence(attack_engine_module, "conflict")
    chips_val = _get_market_evidence(attack_engine_module, "chips")

    score = 50.0
    breakdown = {}

    # 1. 是否為單日暫定訊號
    is_provisional = price_val.get("is_provisional")
    if is_provisional is True:
        breakdown["單日暫定訊號扣分"] = -20
        score -= 20
    elif is_provisional is False:
        breakdown["已過三日確認加分"] = 10
        score += 10

    # 2. 關鍵低點/高點是否確認
    try:
        active_event = market_events_module.get_active_pivot_event()
        if active_event and active_event.get("confirmation_status") == "confirmed":
            breakdown["關鍵低點已確認加分"] = 15
            score += 15
        elif active_event and active_event.get("confirmation_status") == "pending":
            breakdown["關鍵低點尚未確認扣分"] = -10
            score -= 10
    except Exception:
        pass

    # 3. 布林通道是否真正擴張
    if price_val.get("lower_band_expanding") is True:
        breakdown["布林確實擴張加分"] = 10
        score += 10

    # 4. 市場廣度是否配合 —— 尚未接入結構化證據
    # 5. 外資現貨與期貨是否同向 —— 目前只有期貨，沒有現貨市場級即時數字
    posture = chips_val.get("posture")
    if posture in ("新增淨空", "空單回補或轉多"):
        breakdown["外資期貨方向明確加分"] = 5
        score += 5

    # 6. 是否接近期貨結算
    if chips_val.get("near_settlement"):
        breakdown["接近期貨結算扣分"] = -15
        score -= 15

    # 7. 資料是否完整
    try:
        market_result = attack_engine_module.calculate_market_attack_state()
        if market_result.get("data_sufficient"):
            breakdown["市場證據齊全加分"] = 10
            score += 10
    except Exception:
        pass

    # 8. 是否存在證據衝突
    if conflict_val.get("state") == "證據衝突":
        breakdown["證據衝突扣分"] = -25
        score -= 25

    score = max(0, min(100, score))

    missing_checks = [
        "市場廣度是否配合（尚未接入）",
        "領先股是否同步（尚未接入）",
        "成交量是否確認（尚未接入）",
    ]

    return {"total": round(score, 1), "breakdown": breakdown, "missing_checks": missing_checks}


# ══════════════════════════════════════════════════════════════
# ▌ 五態輸出（唯一合法輸出集合）
# ══════════════════════════════════════════════════════════════

def classify_direction_strategy(direction_result, confidence_result, hard_veto=False, evidence_conflict=False):
    """
    回傳 dict：{"state": 五態之一, "reason": 說明文字}
    證據衝突、硬性否決或資料不足時一律「現金觀察」，不強迫二選一。
    """
    total = direction_result["total"]
    confidence = confidence_result["total"]

    if hard_veto:
        return {"state": "現金觀察", "reason": "市場層級硬性否決已觸發，禁止方向性進場"}
    if evidence_conflict:
        return {"state": "現金觀察", "reason": "市場證據衝突，暫不建議方向性進場"}

    if total >= DIRECTION_SCORE_THRESHOLD:
        if confidence >= CONFIDENCE_THRESHOLD:
            return {"state": "正2", "reason": f"方向分數{total}分達正2門檻，可信度{confidence}分已確認"}
        return {"state": "正2模擬觀察",
                "reason": f"方向分數{total}分達正2候選區間，但可信度僅{confidence}分（需≥{CONFIDENCE_THRESHOLD}），先模擬觀察"}
    if total >= 20:
        return {"state": "正2模擬觀察", "reason": f"方向分數{total}分偏多，尚未達正式進場門檻({DIRECTION_SCORE_THRESHOLD})"}

    if total <= -DIRECTION_SCORE_THRESHOLD:
        if confidence >= CONFIDENCE_THRESHOLD:
            return {"state": "反1", "reason": f"方向分數{total}分達反1門檻，可信度{confidence}分已確認"}
        return {"state": "反1模擬觀察",
                "reason": f"方向分數{total}分達反1候選區間，但可信度僅{confidence}分（需≥{CONFIDENCE_THRESHOLD}），先模擬觀察"}
    if total <= -20:
        return {"state": "反1模擬觀察", "reason": f"方向分數{total}分偏空，尚未達正式進場門檻(-{DIRECTION_SCORE_THRESHOLD})"}

    return {"state": "現金觀察", "reason": f"方向分數{total}分落在中性區間(-19~+19)，方向不明確"}


def get_direction_strategy_state(attack_engine_module, market_events_module):
    """
    一次算完：方向分數、可信度、硬性否決、證據衝突、最終五態輸出。
    這是Tab8/Tab4/Tab7應該呼叫的唯一入口，不要各自重算。
    """
    direction_result = calculate_market_direction_score(attack_engine_module)
    confidence_result = calculate_direction_confidence_score(attack_engine_module, market_events_module)

    try:
        market_result = attack_engine_module.calculate_market_attack_state()
        hard_veto = bool(market_result.get("hard_veto"))
    except Exception:
        hard_veto = False

    conflict_val = _get_market_evidence(attack_engine_module, "conflict")
    evidence_conflict = conflict_val.get("state") == "證據衝突"

    strategy = classify_direction_strategy(direction_result, confidence_result, hard_veto, evidence_conflict)

    return {
        "market_direction_score": direction_result["total"],
        "direction_band": direction_result["band"],
        "direction_breakdown": direction_result["breakdown"],
        "direction_confidence_score": confidence_result["total"],
        "confidence_breakdown": confidence_result["breakdown"],
        "confidence_missing_checks": confidence_result["missing_checks"],
        "hard_veto": hard_veto,
        "evidence_conflict": evidence_conflict,
        "conflict_detail": conflict_val.get("conflicts", []),
        "strategy_state": strategy["state"],
        "strategy_reason": strategy["reason"],
        "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ══════════════════════════════════════════════════════════════
# ▌ 方向曝險換算（正2與反1不是相同槓桿，不能用本金直接比較）
# ══════════════════════════════════════════════════════════════

def effective_directional_exposure(market_value, direction):
    """
    direction: "long"（正2）或 "short"（反1）
    正2：market_value × 2　　反1：market_value × -1
    """
    if direction == "long":
        return market_value * 2
    if direction == "short":
        return market_value * -1
    raise ValueError(f"invalid direction: {direction}")


def compare_symmetric_exposure(long_amount, short_amount):
    """
    案例五：正2投入10萬元 vs 反1投入10萬元——本金相同不代表風險曝險相同。
    回傳兩者的實際方向曝險，並警告如果本金相同、曝險並不對稱。
    """
    long_exposure = effective_directional_exposure(long_amount, "long")
    short_exposure = effective_directional_exposure(short_amount, "short")
    same_principal = abs(long_amount - short_amount) < 1e-6
    return {
        "long_principal": long_amount, "short_principal": short_amount,
        "long_exposure": long_exposure, "short_exposure": short_exposure,
        "is_symmetric_exposure": abs(abs(long_exposure) - abs(short_exposure)) < 1e-6,
        "warning": ("本金相同不代表方向曝險相同：正2的方向曝險是反1的2倍，"
                    "要比較兩個策略，應該用相同的『方向曝險』而不是相同本金。"
                    if same_principal else None),
    }


def aggregate_exposure(positions):
    """
    案例六：同時持有正2與反1，不能只顯示淨額為零。
    positions: list of {"ticker":..., "direction": "long"/"short", "market_value": float}
    回傳 {"long_notional":..., "short_notional":..., "net_exposure":..., "gross_exposure":...}
    """
    long_notional = sum(
        effective_directional_exposure(p["market_value"], "long")
        for p in positions if p["direction"] == "long"
    )
    short_notional = sum(
        effective_directional_exposure(p["market_value"], "short")
        for p in positions if p["direction"] == "short"
    )
    net_exposure = long_notional + short_notional  # short已經是負值
    gross_exposure = abs(long_notional) + abs(short_notional)
    return {
        "long_notional_exposure": round(long_notional, 0),
        "short_notional_exposure": round(short_notional, 0),
        "net_exposure": round(net_exposure, 0),
        "gross_exposure": round(gross_exposure, 0),
        "note": ("同時持有正2與反1時，淨曝險可能接近0，但雙邊都在承擔真實市場風險與交易成本，"
                 "不能誤認為沒有風險，請同時參考總曝險(gross_exposure)。"),
    }
