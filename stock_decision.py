"""
stock_decision.py — V7 統一決策資料結構
================================================================
把三個容易混淆的分數徹底分開：

  1. 王者品質分（king_score，40分制）—— 公司長期品質，不判斷今天買不買
  2. 研究優先分（research_priority_score，舊名 Rex Priority Score／
     舊 base_total）—— 只決定今天先研究誰，不是買進訊號
  3. 攻擊時機分（attack_score，attack_engine.py，100分制）—— 唯一有權
     產生試單／第一擊／加碼等實際行動建議的分數

Tab3／Tab4／Tab7 都只讀這裡組出來的 stock_decision，不各自重算排名。
"""

import os
import json
from datetime import datetime

import attack_engine

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# ── 統一狀態詞彙（見規格書第八節，不得混用）──────────────────────
RESEARCH_STATES = ["正常追蹤", "優先研究", "重新估值", "待驗證", "暫停研究"]
ATTACK_STAGES = ["防守", "攻擊準備", "第一擊", "確認進攻", "趨勢攻擊", "停止抄底"]
ACTIONS = ["不處理", "持有", "觀察", "試單", "第一擊", "加碼", "減碼", "退出"]
EVIDENCE_STATES = ["完整", "部分缺失", "已過期", "待驗證", "證據衝突"]

RESEARCH_PRIORITY_DISCLAIMER = (
    "研究優先分只決定今天先研究哪一檔，不代表買進順序。"
    "實際操作以攻擊時機分與硬性否決為準。"
)

# 研究優先分門檻（沿用舊「高度優先≥75／次要優先≥60」，改名不改數值）
_RESEARCH_PRIORITY_TIER1 = 75  # 優先研究
_RESEARCH_PRIORITY_TIER2 = 60  # 次序研究

_STAGE_TO_ACTION = {
    "停止抄底": "退出",
    "防守": "不處理",
    "攻擊準備": "觀察",
    "第一擊": "試單",
    "確認進攻": "加碼",
    "趨勢攻擊": "持有",
}

_STAGE_TO_POSITION_LIMIT = {
    "停止抄底": "0%（硬性否決，禁止進場）",
    "防守": "0%",
    "攻擊準備": "0%（觀察，尚未達門檻）",
    "第一擊": "20%～30%（第一筆）",
    "確認進攻": "30%～60%（分批加碼）",
    "趨勢攻擊": "60%～100%（維持既有部位）",
}

_STAGE_TO_NEXT_TRIGGER = {
    "停止抄底": "硬性否決解除且基本面重新確認",
    "防守": "四大分項合計達40分以上",
    "攻擊準備": "價格確認或籌碼改善分數提升，合計達55分",
    "第一擊": "後續分數持續提升至70分以上",
    "確認進攻": "分數維持85分以上、確認趨勢",
    "趨勢攻擊": "分數跌破85分或出現否決",
}


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_rex_scores_map():
    """回傳 ({stock_id: rex_score_dict}, calculated_at字串)"""
    d = _load_json(os.path.join(DATA_DIR, "rex_scores.json"), {})
    return {s["stock_id"]: s for s in d.get("scores", [])}, d.get("calculated_at")


def classify_research_state(rex_rec, attack_result, has_king_discount_event=False):
    """
    決定研究狀態，只能是 RESEARCH_STATES 其中之一。
    規則（第一版，之後可依 King Discount Monitor 實際事件擴充）：
      - 沒有王者評分資料 → 待驗證
      - King Discount Monitor 觸發重新估值事件 → 重新估值
      - research_priority_score >= 60（次序研究門檻以上）→ 優先研究
      - 有硬性否決且分數很低 → 暫停研究
      - 其餘 → 正常追蹤
    """
    if not rex_rec:
        return "待驗證"
    if has_king_discount_event:
        return "重新估值"
    base_total = rex_rec.get("base_total", 0) or 0
    if attack_result and attack_result.get("hard_veto") and base_total < _RESEARCH_PRIORITY_TIER2:
        return "暫停研究"
    if base_total >= _RESEARCH_PRIORITY_TIER2:
        return "優先研究"
    return "正常追蹤"


def classify_research_tier_label(base_total):
    """優先研究／次序研究／—（沿用舊75/60門檻，只改名不改數值）"""
    if base_total is None:
        return "—"
    if base_total >= _RESEARCH_PRIORITY_TIER1:
        return "優先研究"
    if base_total >= _RESEARCH_PRIORITY_TIER2:
        return "次序研究"
    return "—"


def evidence_completeness_label(attack_result):
    """對應 EVIDENCE_STATES"""
    if not attack_result:
        return "待驗證"
    if attack_result.get("hard_veto") and any(
        r.get("category") == "fundamental" for r in attack_result.get("veto_reasons", [])
    ):
        pass  # 否決不等於證據衝突，維持下方一般判斷
    if not attack_result.get("data_sufficient"):
        return "部分缺失"
    return "完整"


def build_stock_decision(stock_id, king_discount_event=None):
    """
    組出單一 stock_decision 物件，供 Tab3/Tab4/Tab7 共同讀取。
    king_discount_event: 若該股票有 King Discount Monitor 觸發的重新估值事件，
                          傳入該事件 dict（見 evaluate_king_discount()），否則 None。
    """
    rex_map, rex_calculated_at = load_rex_scores_map()
    rex_rec = rex_map.get(str(stock_id), {})

    attack_result = attack_engine.calculate_stock_attack_state(str(stock_id))

    hard_veto = bool(attack_result.get("hard_veto"))
    if hard_veto:
        attack_stage = "停止抄底"
    else:
        attack_stage = attack_result.get("stage", "防守")

    recommended_action = _STAGE_TO_ACTION.get(attack_stage, "不處理")
    research_state = classify_research_state(
        rex_rec, attack_result, has_king_discount_event=bool(king_discount_event)
    )

    base_total = rex_rec.get("base_total")

    return {
        "ticker": str(stock_id),
        "name": rex_rec.get("name", str(stock_id)),
        "king_score": rex_rec.get("king_total"),
        "quality_tier": rex_rec.get("stock_class"),
        "price_opportunity_score": rex_rec.get("attack_total"),  # 舊「攻擊分40」正式更名
        "research_priority_score": base_total,
        "research_priority_tier": classify_research_tier_label(base_total),
        "research_state": research_state,
        "research_reasons": (
            [king_discount_event.get("reason")] if king_discount_event else []
        ),
        "attack_score": attack_result.get("total_score", 0),
        "attack_breakdown": attack_result.get("breakdown", {}),
        "attack_stage": attack_stage,
        "evidence_completeness": evidence_completeness_label(attack_result),
        "hard_veto": hard_veto,
        "veto_reasons": attack_result.get("veto_reasons", []),
        "recommended_action": recommended_action,
        "position_limit": _STAGE_TO_POSITION_LIMIT.get(attack_stage, "0%"),
        "next_trigger": _STAGE_TO_NEXT_TRIGGER.get(attack_stage, "—"),
        "invalid_conditions": [
            f"{r['category']}：{r['reason']}" for r in attack_result.get("veto_reasons", [])
        ],
        "data_as_of": {
            "rex_scores": rex_calculated_at or "—",
            "attack_engine": attack_result.get("calculated_at", "—"),
        },
    }


def build_stock_decisions(stock_ids, king_discount_events=None):
    """批次組多檔股票的 stock_decision，king_discount_events: {stock_id: event_dict}"""
    king_discount_events = king_discount_events or {}
    return [
        build_stock_decision(sid, king_discount_events.get(str(sid)))
        for sid in stock_ids
    ]


def get_oldest_data_date(decision):
    """回傳該decision裡最舊的資料日期字串，供畫面顯示「資料缺口」用"""
    dates = [v for v in decision.get("data_as_of", {}).values() if v and v != "—"]
    if not dates:
        return "—"
    try:
        parsed = [datetime.strptime(d[:10], "%Y-%m-%d") for d in dates]
        return min(parsed).strftime("%Y-%m-%d")
    except Exception:
        return min(dates)
