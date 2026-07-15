"""
attack_engine.py — 台股量化系統 V7 攻擊引擎（第一階段：核心計算層）
============================================================
提供：
  - 證據登記（evidence_registry.json）
  - 攻擊分數計算：基本面完整度40 / 估值風險釋放25 / 價格確認20 / 籌碼改善15
  - 硬性否決判斷（觸發時分數上限 49，禁止進入第一擊以上階段）
  - 資料有效期限（TTL）判斷，過期證據自動排除計算
  - 歷史分數保存與回溯（7日/30日變化）
  - 看多／看空論點與反證登記、簡化版證據衝突偵測
  - 人工覆核紀錄

設計原則：
  - 本模組不 import streamlit，可獨立被 app.py 匯入，也方便單元測試。
  - 各 Tab（1/2/10/11 等）之後只需呼叫 register_evidence() 寫入證據，
    不需要各自重算攻擊分數 —— 分數一律由本模組的
    calculate_attack_score() / calculate_market_attack_state() /
    calculate_stock_attack_state() 統一計算，Tab3/Tab4/Tab7 只讀結果。
  - 目前尚未接上 Tab1/Tab2/Tab10/Tab11 的真實證據來源（屬於第二階段之後
    的工作），因此在證據登記之前，calculate_attack_score() 對任何
    subject 都會回傳「資料不足 / 0分 / 防守」，這是預期中的誠實狀態，
    不是 bug。
"""

import json
import os
import time
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

EVIDENCE_REGISTRY_PATH = os.path.join(DATA_DIR, "evidence_registry.json")
ATTACK_SCORES_PATH     = os.path.join(DATA_DIR, "attack_scores.json")
ATTACK_HISTORY_PATH    = os.path.join(DATA_DIR, "attack_score_history.json")
COUNTEREVIDENCE_PATH   = os.path.join(DATA_DIR, "argument_counterevidence.json")
MANUAL_REVIEWS_PATH    = os.path.join(DATA_DIR, "manual_reviews.json")

EVIDENCE_GRADES = ("A", "B", "C", "D")  # A:官方確認 B:公司證據 C:系統衍生指標 D:AI推論(待驗證)

ATTACK_STAGE_BANDS = [
    (0, 39, "防守"),
    (40, 54, "攻擊準備"),
    (55, 69, "第一擊"),
    (70, 84, "確認進攻"),
    (85, 100, "趨勢攻擊"),
]

HARD_VETO_CAP = 49  # 硬性否決成立時，攻擊分數上限

WEIGHTS = {
    "fundamental": 40,   # 基本面完整度
    "valuation":   25,   # 估值風險釋放
    "price":       20,   # 價格確認
    "chips":       15,   # 籌碼改善
}

GRADE_WEIGHT = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}


# ──────────────────────────────────────────────────────────
# ▌ 基礎 IO（原子寫入，避免中斷造成檔案損毀）
# ──────────────────────────────────────────────────────────

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _json_safe_default(obj):
    """
    防呆轉換：呼叫端有時會不小心把 numpy/pandas 純量（如 numpy.bool_、
    numpy.float64）放進 value dict，這些不是原生 json 型別。與其讓整次
    register_evidence 寫入失敗，這裡統一轉成 Python 原生型別再序列化。
    """
    if hasattr(obj, "item"):  # numpy 純量 (bool_, float64, int64 ...) 都有 .item()
        return obj.item()
    return str(obj)


def _save_json(path, data):
    """
    【Windows耐用性修正】os.replace() 在Windows上如果目的檔案暫時被別的程式
    鎖住（防毒軟體掃描、OneDrive同步、另一個還開著的Streamlit進程），會直接
    拋出PermissionError讓整頁崩潰。改成重試幾次、每次間隔加長，大部分暫時性
    鎖定都能在幾百毫秒內自然解除，這樣就不會整頁掛掉。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_json_safe_default)
    _last_err = None
    for _attempt, _delay in enumerate((0, 0.2, 0.5, 1.0)):
        if _delay:
            time.sleep(_delay)
        try:
            os.replace(tmp, path)
            return True
        except PermissionError as e:
            _last_err = e
            continue
    # 重試多次仍失敗，放棄替換但不讓整頁崩潰，暫存檔案留著供除錯
    return False


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────
# ▌ 證據登記 evidence_registry.json
# ──────────────────────────────────────────────────────────

def load_evidence_registry():
    return _load_json(EVIDENCE_REGISTRY_PATH, {})


def save_evidence_registry(registry):
    return _save_json(EVIDENCE_REGISTRY_PATH, registry)


def register_evidence(subject_key, evidence_id, *, category, value, source,
                       date, grade="D", ttl_days=30, verified=False, note=""):
    """
    登記一筆證據。

    subject_key : 'market' 或個股代號（例如 '2330'）
    evidence_id : 此證據的唯一 id（同一 subject 下不可重複，重複會覆寫）
    category    : 'fundamental' | 'valuation' | 'price' | 'chips'
                  | 'industry' | 'supply_chain'（Tab2/Tab11 之後擴充用）
    value       : dict，至少建議包含：
                    score_ratio (0~1，本證據對該分類的貢獻比例)
                    direction   ('up' / 'down'，供證據衝突偵測使用)
                    veto        (True 時觸發硬性否決)
                    veto_reason (否決原因，veto=True 時必填)
    grade       : A/B/C/D，見 EVIDENCE_GRADES；AI推論或新聞一律先標 D
    ttl_days    : 有效天數，超過即視為過期，計分時自動排除
    verified    : 是否已由官方/公司資料確認（True 才可視為正式關聯/證據）
    """
    if grade not in EVIDENCE_GRADES:
        raise ValueError(f"invalid grade: {grade}")
    registry = load_evidence_registry()
    bucket = registry.setdefault(subject_key, {})
    expiry = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=ttl_days)).strftime("%Y-%m-%d")
    bucket[evidence_id] = {
        "category": category,
        "value": value,
        "source": source,
        "date": date,
        "grade": grade,
        "expiry": expiry,
        "verified": verified,
        "note": note,
        "registered_at": _now_str(),
    }
    save_evidence_registry(registry)
    return bucket[evidence_id]


def is_evidence_expired(evidence, as_of=None):
    as_of = as_of or datetime.now().date()
    expiry = evidence.get("expiry")
    if not expiry:
        return False
    try:
        return datetime.strptime(expiry, "%Y-%m-%d").date() < as_of
    except Exception:
        return False


def get_valid_evidence(subject_key, category=None):
    """回傳指定 subject 尚未過期的證據清單（list of dict，含 id）"""
    registry = load_evidence_registry()
    bucket = registry.get(subject_key, {})
    out = []
    for eid, ev in bucket.items():
        if category and ev.get("category") != category:
            continue
        if is_evidence_expired(ev):
            continue
        out.append({**ev, "id": eid})
    return out


# ──────────────────────────────────────────────────────────
# ▌ 硬性否決
# ──────────────────────────────────────────────────────────

def evaluate_hard_veto(evidence_list):
    """
    evidence_list: 來自 get_valid_evidence() 的證據清單
    規則（第一階段先建立機制，Phase 4 接上 Tab1/Tab2/Tab10 真實資料時
    再擴充實際否決條件，例如跌破第二布林下軌且收盤確認、財報硬性造假
    疑慮、產業景氣反轉證據等 —— 但都是透過同一個 value['veto']=True
    的介面送入，不需要修改本函式）。
    回傳 (triggered: bool, reasons: list[dict])
    """
    reasons = []
    for ev in evidence_list:
        val = ev.get("value")
        if not isinstance(val, dict):
            continue
        if val.get("veto") is True:
            reasons.append({
                "evidence_id": ev.get("id"),
                "category": ev.get("category"),
                "reason": val.get("veto_reason", "未說明否決原因"),
                "date": ev.get("date"),
                "source": ev.get("source"),
            })
    return (len(reasons) > 0, reasons)


# ──────────────────────────────────────────────────────────
# ▌ 攻擊分數計算
# ──────────────────────────────────────────────────────────

def _score_from_evidence(evidence_list, category, max_score):
    """
    每筆證據依 grade 給權重（A:100% B:75% C:50% D:25%），乘上該證據的
    score_ratio（0~1，未提供時先給 0.5 中性分並等待人工覆核），加總後
    以 max_score 為上限。回傳 (score, evidence_id_list)。
    """
    cat_evidence = [e for e in evidence_list if e.get("category") == category]
    if not cat_evidence:
        return 0.0, []
    total_ratio = 0.0
    used = []
    for ev in cat_evidence:
        val = ev.get("value") if isinstance(ev.get("value"), dict) else {}
        ratio = val.get("score_ratio")
        if ratio is None:
            ratio = 0.5
        gw = GRADE_WEIGHT.get(ev.get("grade", "D"), 0.25)
        total_ratio += ratio * gw
        used.append(ev.get("id"))
    total_ratio = min(total_ratio, 1.0)
    return round(total_ratio * max_score, 2), used


def classify_attack_stage(total_score, veto_triggered):
    if veto_triggered:
        total_score = min(total_score, HARD_VETO_CAP)
    for lo, hi, name in ATTACK_STAGE_BANDS:
        if lo <= total_score <= hi:
            return name
    return "防守"


def calculate_attack_score(subject_key):
    """
    計算單一 subject（'market' 或個股代號）的攻擊分數。
    回傳 dict，符合 attack_scores.json 的欄位規格，可直接被
    Tab3/Tab4/Tab7 讀取顯示。
    """
    evidence_list = get_valid_evidence(subject_key)
    veto_triggered, veto_reasons = evaluate_hard_veto(evidence_list)

    fund_score, fund_ev = _score_from_evidence(evidence_list, "fundamental", WEIGHTS["fundamental"])
    val_score, val_ev = _score_from_evidence(evidence_list, "valuation", WEIGHTS["valuation"])
    price_score, price_ev = _score_from_evidence(evidence_list, "price", WEIGHTS["price"])
    chips_score, chips_ev = _score_from_evidence(evidence_list, "chips", WEIGHTS["chips"])

    total = fund_score + val_score + price_score + chips_score
    if veto_triggered:
        total = min(total, HARD_VETO_CAP)

    stage = classify_attack_stage(total, veto_triggered)
    has_any_evidence = len(evidence_list) > 0

    return {
        "subject": subject_key,
        "total_score": round(total, 2),
        "stage": stage,
        "hard_veto": veto_triggered,
        "veto_reasons": veto_reasons,
        "data_sufficient": has_any_evidence,
        "breakdown": {
            "fundamental": {"score": fund_score, "max": WEIGHTS["fundamental"], "evidence": fund_ev},
            "valuation":   {"score": val_score,  "max": WEIGHTS["valuation"],  "evidence": val_ev},
            "price":       {"score": price_score, "max": WEIGHTS["price"],     "evidence": price_ev},
            "chips":       {"score": chips_score, "max": WEIGHTS["chips"],     "evidence": chips_ev},
        },
        "calculated_at": _now_str(),
    }


def calculate_market_attack_state():
    return calculate_attack_score("market")


def calculate_stock_attack_state(stock_id):
    return calculate_attack_score(stock_id)


# ──────────────────────────────────────────────────────────
# ▌ 分數保存 attack_scores.json ＋ 歷史 attack_score_history.json
# ──────────────────────────────────────────────────────────

def load_attack_scores():
    return _load_json(ATTACK_SCORES_PATH, {"market": {}, "stocks": {}})


def load_score_history():
    return _load_json(ATTACK_HISTORY_PATH, {})


def append_score_history(subject_key, result):
    history = load_score_history()
    bucket = history.setdefault(subject_key, [])
    today = datetime.now().strftime("%Y-%m-%d")
    bucket = [h for h in bucket if h.get("date") != today]  # 同日只留最新一筆
    bucket.append({
        "date": today,
        "total_score": result["total_score"],
        "stage": result["stage"],
        "hard_veto": result["hard_veto"],
    })
    bucket = sorted(bucket, key=lambda x: x["date"])[-180:]  # 保留近180天
    history[subject_key] = bucket
    _save_json(ATTACK_HISTORY_PATH, history)
    return bucket


def save_attack_score(subject_key, result):
    scores = load_attack_scores()
    if subject_key == "market":
        scores["market"] = result
    else:
        scores.setdefault("stocks", {})[subject_key] = result
    scores["updated_at"] = _now_str()
    _save_json(ATTACK_SCORES_PATH, scores)
    append_score_history(subject_key, result)
    return scores


def refresh_attack_score(subject_key):
    """計算並保存（含歷史），供 Tab7/排程呼叫"""
    result = calculate_attack_score(subject_key)
    save_attack_score(subject_key, result)
    return result


def get_score_change(subject_key, days):
    """回傳距今 N 天前 vs 最新分數的差值；資料不足回傳 None"""
    bucket = load_score_history().get(subject_key, [])
    if len(bucket) < 2:
        return None
    latest = bucket[-1]["total_score"]
    cutoff = datetime.now().date() - timedelta(days=days)
    past_candidates = [h for h in bucket if datetime.strptime(h["date"], "%Y-%m-%d").date() <= cutoff]
    if not past_candidates:
        return None
    past = past_candidates[-1]["total_score"]
    return round(latest - past, 2)


# ──────────────────────────────────────────────────────────
# ▌ 看多／看空論點與反證 argument_counterevidence.json
# ──────────────────────────────────────────────────────────

def load_counterevidence():
    return _load_json(COUNTEREVIDENCE_PATH, {})


def add_argument(subject_key, *, side, statement, evidence_id=None, expiry=None):
    """side: 'bull'（看多） 或 'bear'（看空）"""
    if side not in ("bull", "bear"):
        raise ValueError("side must be 'bull' or 'bear'")
    data = load_counterevidence()
    bucket = data.setdefault(subject_key, {"bull": [], "bear": []})
    bucket.setdefault(side, []).append({
        "statement": statement,
        "evidence_id": evidence_id,
        "expiry": expiry,
        "added_at": _now_str(),
    })
    _save_json(COUNTEREVIDENCE_PATH, data)
    return bucket


def detect_evidence_conflict(subject_key):
    """
    簡化版證據衝突偵測（第一階段規則，先處理「基本面 vs 價格」方向不一致）：
      - 基本面偏多 + 價格轉弱 → 衝突
      - 基本面轉弱 + 價格反彈 → 衝突
    回傳 (conflict: bool, message: str|None)
    """
    evidence_list = get_valid_evidence(subject_key)
    fund_dir, price_dir = [], []
    for ev in evidence_list:
        val = ev.get("value") if isinstance(ev.get("value"), dict) else {}
        direction = val.get("direction")
        if ev.get("category") == "fundamental" and direction:
            fund_dir.append(direction)
        if ev.get("category") == "price" and direction:
            price_dir.append(direction)
    if fund_dir and price_dir:
        if "up" in fund_dir and "down" in price_dir:
            return True, "基本面仍強，但價格轉弱"
        if "down" in fund_dir and "up" in price_dir:
            return True, "基本面轉弱，但股價反彈"
    return False, None


# ──────────────────────────────────────────────────────────
# ▌ 人工覆核 manual_reviews.json
# ──────────────────────────────────────────────────────────

def load_manual_reviews():
    return _load_json(MANUAL_REVIEWS_PATH, [])


def add_manual_review(*, subject_key, field, before, after, reason, reviewer="Rex"):
    reviews = load_manual_reviews()
    reviews.append({
        "subject": subject_key,
        "field": field,
        "before": before,
        "after": after,
        "reason": reason,
        "reviewer": reviewer,
        "reviewed_at": _now_str(),
    })
    _save_json(MANUAL_REVIEWS_PATH, reviews)
    return reviews
