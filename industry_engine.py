"""
industry_engine.py — V7 攻擊引擎：自動產業情報層
============================================================
目的：Tab2 打開就有結論，不需要 Rex 自己查資料填表單。

資料責任分工：
  1. 官方機構與公司公告提供原始證據（目前：rex_scores.json 的營收/EPS/
     毛利率，皆來自 FinMind 官方財報數字；prices/*.csv 為 TWSE/OTC
     日K，皆為既有真實資料源）。
  2. 本模組負責計算數值、趨勢與分類（calculate_industry_metrics /
     classify_industry_state）。
  3. AI（若接上 Gemini）只負責摘要、比較、提出反證，不覆蓋數字。
  4. Rex 只負責覆核與最終決策（見 Tab2「進階人工覆核」）。

重要邊界（見第九節「禁止產業資料冒充個股基本面」）：
  本模組產出的一切都停留在「Topic 層級」，寫入 industry_state.json /
  industry_metrics.json / industry_evidence.json。
  【不會】呼叫 attack_engine.register_evidence() 寫入任何個股的
  'fundamental' 證據 —— 個股基本面分數只能由 Tab10 真正的公司層級
  財報證據決定。需要產業背景時，呼叫 get_stock_industry_context()
  現查現算，不落地成個股證據，避免產業分數冒充公司分數。
"""

import os
import time
import json
import glob
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_DIR = os.path.join(DATA_DIR, "prices")

INDUSTRY_METRICS_PATH = os.path.join(DATA_DIR, "industry_metrics.json")
INDUSTRY_STATE_PATH = os.path.join(DATA_DIR, "industry_state.json")
INDUSTRY_EVIDENCE_PATH = os.path.join(DATA_DIR, "industry_evidence.json")
INDUSTRY_STATE_HISTORY_PATH = os.path.join(DATA_DIR, "industry_state_history.json")
LEGACY_TREND_PATH = os.path.join(DATA_DIR, "industry_trend.json")  # 舊版人工填寫資料，保留可讀
CSP_CAPEX_PATH = os.path.join(DATA_DIR, "csp_capex.json")
CAPEX_GUIDANCE_PATH = os.path.join(DATA_DIR, "capex_guidance.json")  # 台積電CAPEX等季度人工登錄

# ── 美國CSP CAPEX：SEC EDGAR官方XBRL API，免金鑰，A級證據 ──────────
# CIK為公開資訊（SEC官網可查），非機密。API文件：
# https://www.sec.gov/edgar/sec-api-documentation
CSP_COMPANIES = {
    "MSFT": {"name": "Microsoft", "cik": "0000789019"},
    "GOOGL": {"name": "Alphabet", "cik": "0001652044"},
    "AMZN": {"name": "Amazon", "cik": "0001018724"},
    "META": {"name": "Meta", "cik": "0001326801"},
}
CSP_CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment",
                  "PaymentsForCapitalImprovements", "PaymentsToAcquireProductiveAssets"]
CSP_CAPEX_CACHE_DAYS = 7  # 10-Q每季才更新一次，不需要每天打SEC API

# ── FinMind月營收補充：戰略儲備庫以外的公司，用這個補基本面樣本 ──
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
FINMIND_REVENUE_CACHE_PATH = os.path.join(DATA_DIR, "finmind_revenue_cache.json")
FINMIND_REVENUE_CACHE_DAYS = 20  # 月營收一個月才更新一次，不需要每天打API
MAX_FINMIND_FETCH_PER_TOPIC = 25  # 每次重新整理，每個Topic最多補抓幾家新公司（避免拖慢頁面/觸發限流）

TOPIC_IDS = ["AI_DATACENTER", "SEMI", "CONN_SPACE", "POWER_INFRA",
             "WATER_ENV", "ROBOT_AUTOMATION", "EDGE_AI_DEVICE", "SMART_MOBILITY"]

TOPIC_LABELS = {
    "AI_DATACENTER": "AI 與資料中心", "SEMI": "半導體與先進封裝",
    "CONN_SPACE": "次世代通訊與太空", "POWER_INFRA": "電力基礎建設",
    "WATER_ENV": "水資源與環境工程", "ROBOT_AUTOMATION": "機器人與智慧製造",
    "EDGE_AI_DEVICE": "邊緣AI與終端裝置", "SMART_MOBILITY": "智慧移動與車用電子",
}

ALLOWED_STATES = ["需求加速", "需求成長", "成長減速", "估值修正", "景氣反轉", "證據衝突", "證據不足"]

# ── 分類門檻，集中設定，之後要調整只改這裡 ──────────────────────
THRESHOLDS = {
    "min_fundamental_sample": 3,   # 基本面樣本數低於此值 → 證據不足
    "min_price_sample": 3,         # 價格樣本數低於此值 → 無法計算價格廣度
    "accel_rev_median": 15.0,      # 需求加速：營收年增中位數 >= 15%
    "accel_pos_ratio": 0.6,        # 需求加速：正成長公司比例 >= 60%
    "growth_rev_median": 0.0,      # 需求成長：營收年增中位數 > 0%
    "growth_pos_ratio": 0.5,       # 需求成長：正成長公司比例 >= 50%
    "reversal_rev_median": 0.0,    # 景氣反轉：營收年增中位數 < 0%
    "reversal_pos_ratio": 0.4,     # 景氣反轉：正成長公司比例 < 40%
    "price_fall_60d": -10.0,       # 價格明顯修正：60日中位數跌幅 <= -10%
    "price_rise_60d": 5.0,         # 價格明顯走強：60日中位數漲幅 >= 5%
    "data_stale_days": 10,         # 價格資料超過幾天沒更新視為過期
}


# ══════════════════════════════════════════════════════════════
# ▌ 基礎 IO
# ══════════════════════════════════════════════════════════════

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


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════
# ▌ 公司 ↔ Topic 對應（沿用既有 kg_companies.json，不重複建立）
# ══════════════════════════════════════════════════════════════

def get_stock_topic_maps():
    """回傳 (stock_to_topics: {sid:set(topic)}, topic_to_stocks: {topic:[{stock_id,name}]})"""
    path = os.path.join(DATA_DIR, "kg_companies.json")
    stock_to_topics, topic_to_stocks = {}, {t: [] for t in TOPIC_IDS}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        seen = set()
        for row in d.get("companies", []):
            if len(row) < 4:
                continue
            topic_id, sid, name = row[1], str(row[2]).strip(), row[3]
            if not sid or topic_id not in TOPIC_IDS:
                continue
            stock_to_topics.setdefault(sid, set()).add(topic_id)
            key = (topic_id, sid)
            if key not in seen:
                seen.add(key)
                topic_to_stocks[topic_id].append({"stock_id": sid, "name": name})
    except Exception:
        pass
    return stock_to_topics, topic_to_stocks


# ══════════════════════════════════════════════════════════════
# ▌ 既有真實資料源讀取（rex_scores.json / prices/*.csv）
# ══════════════════════════════════════════════════════════════

def load_rex_scores_map():
    """回傳 {stock_id: rex_score_dict}，來源 data/rex_scores.json（既有FinMind財報計算結果）"""
    d = _load_json(os.path.join(DATA_DIR, "rex_scores.json"), {})
    return {s["stock_id"]: s for s in d.get("scores", [])}


def _load_revenue_cache():
    return _load_json(FINMIND_REVENUE_CACHE_PATH, {})


def _save_revenue_cache(data):
    _save_json(FINMIND_REVENUE_CACHE_PATH, data)


def _revenue_cache_is_fresh(rec):
    if not rec or not rec.get("fetched_at"):
        return False
    try:
        age = (datetime.now() - datetime.strptime(rec["fetched_at"], "%Y-%m-%d %H:%M:%S")).days
        return age < FINMIND_REVENUE_CACHE_DAYS
    except Exception:
        return False


def fetch_monthly_revenue_yoy(stock_id, force=False):
    """
    補充來源：對不在戰略儲備庫評分名單的公司，直接向FinMind抓月營收，
    自己算年增率（FinMind TaiwanStockMonthRevenue是官方轉載自公開資訊觀測站
    的月營收數字，A級證據）。有磁碟快取，同一檔股票20天內不會重複打API。
    """
    cache = _load_revenue_cache()
    rec = cache.get(stock_id)
    if not force and _revenue_cache_is_fresh(rec):
        return rec.get("revenue_yoy")

    try:
        import requests
        start = (datetime.now() - timedelta(days=450)).strftime("%Y-%m-%d")
        _fm_token = os.environ.get("FINMIND_TOKEN", "")
        _headers = {"Authorization": f"Bearer {_fm_token}"} if _fm_token else {}
        r = requests.get(FINMIND_API, params={
            "dataset": "TaiwanStockMonthRevenue", "data_id": stock_id, "start_date": start
        }, headers=_headers, timeout=10)
        d = r.json()
        rows = d.get("data") if d.get("status") == 200 else None
        if not rows:
            cache[stock_id] = {"revenue_yoy": None, "fetched_at": _now(), "status": "無資料"}
            _save_revenue_cache(cache)
            return None
        rows = sorted(rows, key=lambda x: (x.get("revenue_year", 0), x.get("revenue_month", 0)))
        if len(rows) < 13:
            cache[stock_id] = {"revenue_yoy": None, "fetched_at": _now(), "status": "資料不足13個月"}
            _save_revenue_cache(cache)
            return None
        latest = rows[-1]
        same_month_ly = next((x for x in rows if x.get("revenue_year") == latest.get("revenue_year", 0) - 1
                               and x.get("revenue_month") == latest.get("revenue_month")), None)
        if not same_month_ly or not same_month_ly.get("revenue"):
            cache[stock_id] = {"revenue_yoy": None, "fetched_at": _now(), "status": "無去年同期資料"}
            _save_revenue_cache(cache)
            return None
        yoy = round((latest["revenue"] - same_month_ly["revenue"]) / same_month_ly["revenue"] * 100, 2)
        cache[stock_id] = {
            "revenue_yoy": yoy, "fetched_at": _now(), "status": "已更新",
            "revenue_month": f"{latest.get('revenue_year')}-{latest.get('revenue_month'):02d}",
        }
        _save_revenue_cache(cache)
        return yoy
    except Exception as e:
        cache[stock_id] = {"revenue_yoy": None, "fetched_at": _now(), "status": f"取得失敗（{type(e).__name__}）"}
        _save_revenue_cache(cache)
        return None


def _parse_gm_direction(gm_trend_str):
    """粗略解析毛利率趨勢文字（如「近季提升(29.4%)」）判斷方向"""
    if not gm_trend_str or not isinstance(gm_trend_str, str):
        return None
    if any(k in gm_trend_str for k in ("提升", "改善", "上升")):
        return "up"
    if any(k in gm_trend_str for k in ("下滑", "惡化", "下降")):
        return "down"
    return "flat"


def load_price_df(stock_id):
    path = os.path.join(PRICES_DIR, f"{stock_id}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if "date" not in df.columns or "Close" not in df.columns or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "Close"]).sort_values("date")
        return df if not df.empty else None
    except Exception:
        return None


def calc_stock_price_metrics(stock_id):
    """
    單一個股的價格指標：20/60/120日報酬率、距高點跌幅、是否站上20MA、
    是否創新低、資料日期（供判斷是否過期）。回傳 None 代表沒有可用資料。
    """
    df = load_price_df(stock_id)
    if df is None or len(df) < 25:
        return None
    close = df["Close"].astype(float)
    last_date = df["date"].iloc[-1]
    last_close = float(close.iloc[-1])

    def _ret(n):
        if len(close) <= n:
            return None
        base = float(close.iloc[-(n + 1)])
        return round((last_close - base) / base * 100, 2) if base else None

    ret20, ret60, ret120 = _ret(20), _ret(60), _ret(120)
    high_window = close.tail(min(len(close), 250))
    high_max = float(high_window.max())
    dist_from_high = round((last_close - high_max) / high_max * 100, 2) if high_max else None
    ma20 = float(close.tail(20).mean())
    above_ma20 = last_close >= ma20
    low_window = close.tail(min(len(close), 60))
    is_new_low_60 = last_close <= float(low_window.min()) * 1.001

    return {
        "stock_id": stock_id, "last_date": last_date.strftime("%Y-%m-%d"),
        "last_close": last_close, "ret20": ret20, "ret60": ret60, "ret120": ret120,
        "dist_from_high": dist_from_high, "above_ma20": above_ma20,
        "is_new_low_60": is_new_low_60,
    }


def get_twii_ret(days):
    """大盤同期報酬率，用於相對強弱比較；抓不到回傳 None（不擋整體流程）"""
    try:
        import yfinance as _yf_ie
        hist = _yf_ie.Ticker("^TWII").history(period="260d")
        if hist is None or hist.empty or len(hist) <= days:
            return None
        close = hist["Close"].astype(float)
        base = float(close.iloc[-(days + 1)])
        last = float(close.iloc[-1])
        return round((last - base) / base * 100, 2) if base else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# ▌ 美國CSP CAPEX（SEC EDGAR官方XBRL API，A級證據，自動抓取）
# ══════════════════════════════════════════════════════════════

def _fetch_sec_concept(cik, tag):
    import urllib.request
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    # SEC要求User-Agent必須含識別資訊與聯絡方式，否則會被擋（非金鑰驗證，是禮貌規範）
    req = urllib.request.Request(url, headers={
        "User-Agent": "TaiwanStockDashboard-V7 (personal research use) contact: rex-research@example.com",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        import gzip
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def fetch_csp_capex(force=False):
    """
    抓 Microsoft/Alphabet/Amazon/Meta 最新10-Q/10-K申報的CAPEX
    （us-gaap:PaymentsToAcquirePropertyPlantAndEquipment等標準標籤）。
    官方SEC申報數字，A級證據。快取7天（10-Q每季才更新一次）。
    任何一家抓取失敗都不影響其他家，也不影響Tab2其他區塊。
    """
    cached = _load_json(CSP_CAPEX_PATH, {})
    if not force and cached.get("fetched_at"):
        try:
            age_days = (datetime.now() - datetime.strptime(cached["fetched_at"], "%Y-%m-%d %H:%M:%S")).days
            if age_days < CSP_CAPEX_CACHE_DAYS:
                return cached
        except Exception:
            pass

    result = {"companies": {}, "fetched_at": _now()}
    for ticker, info in CSP_COMPANIES.items():
        entry = {"name": info["name"], "status": "取得失敗", "quarters": [], "tag_used": None}
        for tag in CSP_CAPEX_TAGS:
            try:
                data = _fetch_sec_concept(info["cik"], tag)
                usd = data.get("units", {}).get("USD", [])
                quarterly = {}
                for item in usd:
                    if item.get("form") not in ("10-Q", "10-K"):
                        continue
                    if not item.get("end") or item.get("val") is None:
                        continue
                    quarterly[item["end"]] = item  # 同期間取最後一筆(修正版覆蓋原始版)
                sorted_q = sorted(quarterly.values(), key=lambda x: x["end"])
                if sorted_q:
                    entry["quarters"] = [{"end": q["end"], "val": q["val"], "form": q["form"]}
                                          for q in sorted_q[-6:]]
                    entry["status"] = "已更新"
                    entry["tag_used"] = tag
                    break
            except Exception as e:
                entry["status"] = f"取得失敗（{type(e).__name__}）"
                continue
        result["companies"][ticker] = entry
    _save_json(CSP_CAPEX_PATH, result)
    return result


def summarize_csp_capex():
    """回傳 Tab2 顯示用的摘要：{lines: [文字,...], success: bool, fetched_at, grade}"""
    try:
        data = fetch_csp_capex()
    except Exception as e:
        return {"lines": [f"抓取失敗：{e}"], "success": False, "fetched_at": None, "grade": None}

    lines, any_success = [], False
    for ticker, entry in data.get("companies", {}).items():
        qs = entry.get("quarters", [])
        if len(qs) < 2:
            lines.append(f"{entry['name']}：{entry.get('status', '尚無資料')}")
            continue
        any_success = True
        latest, prev = qs[-1], qs[-2]
        chg = (round((latest["val"] - prev["val"]) / prev["val"] * 100, 1)
               if prev["val"] else None)
        direction = "上修" if (chg is not None and chg > 5) else ("下修" if (chg is not None and chg < -5) else "持平")
        lines.append(
            f"{entry['name']}：最新申報季度CAPEX約 ${latest['val']/1e9:.1f}B（截至{latest['end']}，{latest['form']}），"
            f"較上季{direction}" + (f"（{chg:+.1f}%）" if chg is not None else "")
        )
    return {"lines": lines, "success": any_success, "fetched_at": data.get("fetched_at"), "grade": "A"}


# ══════════════════════════════════════════════════════════════
# ▌ 台積電CAPEX／官方出口統計：季度人工登錄（非每日必填，非自動抓取）
#   原因：台積電CAPEX公布在法說會逐字稿/簡報PDF，無結構化官方API；
#   經濟部/財政部出口統計格式複雜多變，自動解析容易出錯給假數字。
#   與其冒風險自動解析出錯誤數字，不如誠實請Rex每季登錄一次正式數字，
#   並附官方來源連結，登錄後即為B級證據（官方公告，人工轉錄）。
# ══════════════════════════════════════════════════════════════

def load_capex_guidance():
    return _load_json(CAPEX_GUIDANCE_PATH, {})


def set_capex_guidance(key, *, label, value_text, source_url, quarter, direction,
                        reviewer="Rex"):
    """
    key: 'tsmc_capex' 或 'export_stats' 等自訂識別碼
    direction: '上修'|'下修'|'維持'|'—'
    """
    data = load_capex_guidance()
    data[key] = {
        "label": label, "value_text": value_text, "source_url": source_url,
        "quarter": quarter, "direction": direction, "reviewer": reviewer,
        "updated_at": _now(),
    }
    _save_json(CAPEX_GUIDANCE_PATH, data)
    return data[key]


def get_capex_guidance_text(key, default_label):
    data = load_capex_guidance()
    rec = data.get(key)
    if not rec:
        return f"尚未接入（{default_label}無結構化官方API，需人工每季登錄一次，見Tab2「進階人工覆核」）"
    return f"{rec['value_text']}（{rec['quarter']}，{rec['direction']}，來源：{rec['source_url']}，登錄於{rec['updated_at'][:10]}）"


# ══════════════════════════════════════════════════════════════
# ▌ 核心：Topic 指標計算
# ══════════════════════════════════════════════════════════════

def calculate_industry_metrics(topic_id, twii_ret60=None):
    """
    整合 rex_scores（基本面，官方FinMind財報數字）+ prices（價格廣度）
    計算單一 Topic 的完整指標字典。不需任何人工輸入。
    """
    _, topic_to_stocks = get_stock_topic_maps()
    companies = topic_to_stocks.get(topic_id, [])
    total_count = len(companies)
    rex_map = load_rex_scores_map()

    # ── 基本面樣本：優先用戰略儲備庫既有評分（完整：營收/EPS/毛利率）
    fund_rows = []
    for c in companies:
        rs = rex_map.get(c["stock_id"])
        if not rs:
            continue
        fund_rows.append({
            "stock_id": c["stock_id"], "name": c["name"],
            "revenue_yoy": rs.get("revenue_yoy_val"),
            "eps_yoy": rs.get("eps_yoy_val"),
            "gm_direction": _parse_gm_direction(rs.get("gm_trend")),
            "king_total": rs.get("king_total"),
            "attack_total": rs.get("attack_total"),
            "source": "戰略儲備庫評分",
        })

    # ── 補充：不在戰略儲備庫的公司，向FinMind補抓月營收年增率（只補營收，
    #   沒有EPS/毛利率）。有磁碟快取，每次最多補抓 MAX_FINMIND_FETCH_PER_TOPIC
    #   家「還沒有快取或快取過期」的新公司，避免單次載入打太多API。
    covered_ids = {r["stock_id"] for r in fund_rows}
    _budget = MAX_FINMIND_FETCH_PER_TOPIC
    for c in companies:
        if c["stock_id"] in covered_ids:
            continue
        _cache_rec = _load_revenue_cache().get(c["stock_id"])
        _fresh = _revenue_cache_is_fresh(_cache_rec)
        if not _fresh and _budget <= 0:
            continue  # 這次額度用完，資料仍缺，下次重新整理或快取過期後再補
        _yoy = fetch_monthly_revenue_yoy(c["stock_id"])
        if not _fresh:
            _budget -= 1
        if _yoy is not None:
            fund_rows.append({
                "stock_id": c["stock_id"], "name": c["name"],
                "revenue_yoy": _yoy, "eps_yoy": None, "gm_direction": None,
                "king_total": None, "attack_total": None,
                "source": "FinMind補充(僅營收)",
            })

    rev_vals = [r["revenue_yoy"] for r in fund_rows if isinstance(r["revenue_yoy"], (int, float))]
    eps_vals = [r["eps_yoy"] for r in fund_rows if isinstance(r["eps_yoy"], (int, float))]
    gm_dirs = [r["gm_direction"] for r in fund_rows if r["gm_direction"]]

    fundamental_sample = len(fund_rows)
    revenue_yoy_median = round(float(np.median(rev_vals)), 2) if rev_vals else None
    revenue_yoy_positive_ratio = round(sum(1 for v in rev_vals if v > 0) / len(rev_vals), 2) if rev_vals else None
    revenue_yoy_above10_ratio = round(sum(1 for v in rev_vals if v > 10) / len(rev_vals), 2) if rev_vals else None
    eps_yoy_median = round(float(np.median(eps_vals)), 2) if eps_vals else None
    eps_yoy_positive_ratio = round(sum(1 for v in eps_vals if v > 0) / len(eps_vals), 2) if eps_vals else None
    gm_improve_ratio = round(gm_dirs.count("up") / len(gm_dirs), 2) if gm_dirs else None

    # ── 價格廣度樣本（涵蓋Topic內所有有價格資料的公司，樣本比基本面更廣）
    price_rows = []
    for c in companies:
        pm = calc_stock_price_metrics(c["stock_id"])
        if pm:
            pm["name"] = c["name"]
            price_rows.append(pm)

    price_sample = len(price_rows)
    ret20_vals = [r["ret20"] for r in price_rows if r["ret20"] is not None]
    ret60_vals = [r["ret60"] for r in price_rows if r["ret60"] is not None]
    ret120_vals = [r["ret120"] for r in price_rows if r["ret120"] is not None]
    dist_vals = [r["dist_from_high"] for r in price_rows if r["dist_from_high"] is not None]

    price_median_ret20 = round(float(np.median(ret20_vals)), 2) if ret20_vals else None
    price_median_ret60 = round(float(np.median(ret60_vals)), 2) if ret60_vals else None
    price_median_ret120 = round(float(np.median(ret120_vals)), 2) if ret120_vals else None
    dist_from_high_median = round(float(np.median(dist_vals)), 2) if dist_vals else None
    above_ma20_ratio = round(sum(1 for r in price_rows if r["above_ma20"]) / price_sample, 2) if price_sample else None
    new_low_ratio = round(sum(1 for r in price_rows if r["is_new_low_60"]) / price_sample, 2) if price_sample else None
    up20_ratio = round(sum(1 for v in ret20_vals if v > 0) / len(ret20_vals), 2) if ret20_vals else None

    if twii_ret60 is None:
        twii_ret60 = get_twii_ret(60)
    relative_strength_60d = (round(price_median_ret60 - twii_ret60, 2)
                              if price_median_ret60 is not None and twii_ret60 is not None else None)

    # 資料新鮮度
    latest_price_date = max((r["last_date"] for r in price_rows), default=None)
    is_price_stale = True
    if latest_price_date:
        try:
            days_old = (datetime.now().date() - datetime.strptime(latest_price_date, "%Y-%m-%d").date()).days
            is_price_stale = days_old > THRESHOLDS["data_stale_days"]
        except Exception:
            pass

    return {
        "topic_id": topic_id, "total_count": total_count,
        "fundamental_sample": fundamental_sample,
        "revenue_yoy_median": revenue_yoy_median,
        "revenue_yoy_positive_ratio": revenue_yoy_positive_ratio,
        "revenue_yoy_above10_ratio": revenue_yoy_above10_ratio,
        "eps_yoy_median": eps_yoy_median,
        "eps_yoy_positive_ratio": eps_yoy_positive_ratio,
        "gm_improve_ratio": gm_improve_ratio,
        "price_sample": price_sample,
        "price_median_ret20": price_median_ret20,
        "price_median_ret60": price_median_ret60,
        "price_median_ret120": price_median_ret120,
        "dist_from_high_median": dist_from_high_median,
        "above_ma20_ratio": above_ma20_ratio,
        "new_low_ratio_60d": new_low_ratio,
        "up20_ratio": up20_ratio,
        "twii_ret60": twii_ret60,
        "relative_strength_60d": relative_strength_60d,
        "latest_price_date": latest_price_date,
        "is_price_stale": is_price_stale,
        "fund_rows": fund_rows,
        "price_rows": price_rows,
        "all_companies": companies,
        # CSP CAPEX：SEC EDGAR官方XBRL API自動抓取（A級證據，快取7天）
        "capex_csp": summarize_csp_capex(),
        # 台積電CAPEX／官方出口統計：無結構化官方API，改為季度人工登錄（B級證據）
        "capex_tsmc": get_capex_guidance_text("tsmc_capex", "台積電CAPEX"),
        "export_stats": get_capex_guidance_text("export_stats", "官方出口統計"),
        "calculated_at": _now(),
    }


# ══════════════════════════════════════════════════════════════
# ▌ 產業狀態自動分類
# ══════════════════════════════════════════════════════════════

def classify_industry_state(metrics):
    """
    回傳 (state, reasons: list[str], confidence: 'high'|'medium'|'low')
    規則集中在這裡，不散落在畫面程式碼裡。
    """
    T = THRESHOLDS
    fund_ok = metrics["fundamental_sample"] >= T["min_fundamental_sample"]
    price_ok = metrics["price_sample"] >= T["min_price_sample"]
    reasons = []

    if not fund_ok and not price_ok:
        return "證據不足", ["基本面與價格樣本數皆不足，無法判斷"], "low"

    rev_med = metrics["revenue_yoy_median"]
    rev_pos = metrics["revenue_yoy_positive_ratio"]
    eps_med = metrics["eps_yoy_median"]
    price_ret60 = metrics["price_median_ret60"]

    if not fund_ok:
        reasons.append(f"僅有價格資料（樣本{metrics['price_sample']}檔），無財報/官方展望佐證")
        return "證據不足", reasons, "low"

    fundamentals_strong = (rev_med is not None and rev_med >= T["accel_rev_median"]
                            and rev_pos is not None and rev_pos >= T["accel_pos_ratio"])
    fundamentals_growing = (rev_med is not None and rev_med > T["growth_rev_median"]
                             and rev_pos is not None and rev_pos >= T["growth_pos_ratio"])
    fundamentals_weak = (rev_med is not None and rev_med < T["reversal_rev_median"]
                          and rev_pos is not None and rev_pos < T["reversal_pos_ratio"])

    price_falling = price_ok and price_ret60 is not None and price_ret60 <= T["price_fall_60d"]
    price_rising = price_ok and price_ret60 is not None and price_ret60 >= T["price_rise_60d"]

    if fundamentals_weak and eps_med is not None and eps_med < 0:
        reasons.append(f"營收年增中位數 {rev_med}%（正成長比例僅{rev_pos*100:.0f}%），EPS年增中位數 {eps_med}% 同步轉弱")
        return "景氣反轉", reasons, "medium"

    if fundamentals_strong and price_falling:
        reasons.append(f"營收年增中位數 {rev_med}%（強），但60日股價中位數 {price_ret60}%（弱）——說法與價格不一致")
        return "證據衝突", reasons, "medium"

    if fundamentals_weak and price_rising:
        reasons.append(f"營收轉弱（中位數{rev_med}%），但60日股價中位數 {price_ret60}%（漲）——說法與價格不一致")
        return "證據衝突", reasons, "medium"

    if not fundamentals_weak and price_falling:
        reasons.append(f"60日股價中位數 {price_ret60}%（明顯修正），但營收年增中位數 {rev_med}% 尚未確認惡化")
        return "估值修正", reasons, "medium"

    if fundamentals_strong:
        reasons.append(f"營收年增中位數 {rev_med}%，正成長比例 {rev_pos*100:.0f}%，符合需求加速門檻")
        return "需求加速", reasons, "high" if metrics["fundamental_sample"] >= 5 else "medium"

    if fundamentals_growing:
        reasons.append(f"營收年增中位數 {rev_med}%（正成長），但未達加速門檻（{T['accel_rev_median']}%）")
        return "需求成長", reasons, "medium"

    reasons.append(f"營收年增中位數 {rev_med}%，正成長比例 {rev_pos*100 if rev_pos is not None else 0:.0f}%，成長動能較弱")
    return "成長減速", reasons, "medium"


def calculate_industry_evidence_quality(metrics):
    """回傳0~100的證據完整度分數"""
    total = metrics["total_count"] or 1
    fund_cov = min(1.0, metrics["fundamental_sample"] / total)
    price_cov = min(1.0, metrics["price_sample"] / total)
    freshness_penalty = 0.3 if metrics.get("is_price_stale") else 0.0
    quality = max(0.0, 0.6 * fund_cov + 0.4 * price_cov - freshness_penalty)
    return round(quality * 100)


def get_industry_counterevidence(metrics, state):
    """回傳可能推翻目前判斷的條件與資料缺口清單"""
    gaps = []
    if metrics["fundamental_sample"] < metrics["total_count"]:
        gaps.append(f"僅 {metrics['fundamental_sample']}/{metrics['total_count']} 家公司有財報數字可查（其餘不在戰略儲備庫評分名單）")
    if metrics.get("is_price_stale"):
        gaps.append(f"價格資料最後更新 {metrics.get('latest_price_date','—')}，可能已過期")
    gaps.append("台積電／CSP最新CAPEX展望尚未接入，無法確認需求端最新變化")
    gaps.append("經濟部/財政部官方出口統計尚未接入")

    flip_conditions = []
    if state in ("需求加速", "需求成長"):
        flip_conditions.append("若下一期營收年增中位數轉負或正成長比例跌破50%，判斷可能轉為成長減速")
    elif state == "估值修正":
        flip_conditions.append("若營收/EPS開始同步惡化，判斷可能升級為景氣反轉")
        flip_conditions.append("若股價止穩且營收持續正成長，判斷可能轉回需求成長")
    elif state == "景氣反轉":
        flip_conditions.append("若營收年增回正且正成長比例回升，判斷可能轉為成長減速或估值修正")
    elif state == "證據衝突":
        flip_conditions.append("等下一期財報公布，確認基本面與價格哪一方修正")
    return {"data_gaps": gaps, "flip_conditions": flip_conditions}


def build_industry_summary(topic_id, metrics, state, reasons):
    """自動回答規格書要求的7個問題，供區塊二使用"""
    is_price_or_fund = "基本面尚未確認惡化，較可能是價格/估值修正" if state == "估值修正" else (
        "基本面已出現轉弱訊號" if state == "景氣反轉" else "—")
    counter = get_industry_counterevidence(metrics, state)

    top_support = []
    if metrics["revenue_yoy_median"] is not None:
        top_support.append(f"營收年增中位數 {metrics['revenue_yoy_median']}%（樣本{metrics['fundamental_sample']}檔）")
    if metrics["revenue_yoy_positive_ratio"] is not None:
        top_support.append(f"正成長公司比例 {metrics['revenue_yoy_positive_ratio']*100:.0f}%")
    if metrics["price_median_ret60"] is not None:
        top_support.append(f"60日股價中位數報酬 {metrics['price_median_ret60']}%")
    top_support = top_support[:3]

    top_against = counter["data_gaps"][:3]

    return {
        "topic_id": topic_id, "state": state,
        "why": "；".join(reasons) if reasons else "樣本不足，無法給出具體理由",
        "price_or_fundamental": is_price_or_fund,
        "top_support_evidence": top_support,
        "top_counter_evidence": top_against,
        "data_gaps": counter["data_gaps"],
        "next_trigger": counter["flip_conditions"],
    }


# ══════════════════════════════════════════════════════════════
# ▌ 整體流程：計算 → 分類 → 存檔 → 歷史
# ══════════════════════════════════════════════════════════════

def _load_legacy_manual_override(topic_id):
    """讀取舊版 industry_trend.json（人工填寫），僅供「進階人工覆核」顯示/遷移用，不參與自動分類"""
    legacy = _load_json(LEGACY_TREND_PATH, {})
    return legacy.get(topic_id)


def refresh_industry_state(topic_id, twii_ret60=None):
    metrics = calculate_industry_metrics(topic_id, twii_ret60=twii_ret60)
    state, reasons, confidence = classify_industry_state(metrics)
    quality = calculate_industry_evidence_quality(metrics)
    summary = build_industry_summary(topic_id, metrics, state, reasons)

    # 套用人工覆核（若有）：只影響顯示狀態，不覆蓋原始自動計算結果
    all_state = _load_json(INDUSTRY_STATE_PATH, {})
    prev = all_state.get(topic_id, {})
    override = prev.get("manual_override")
    display_state = override["state"] if override and override.get("active") else state

    record = {
        "topic_id": topic_id, "auto_state": state, "display_state": display_state,
        "confidence": confidence, "reasons": reasons,
        "evidence_quality": quality, "manual_override": override,
        "prev_state": prev.get("display_state"),
        "updated_at": _now(),
    }
    all_state[topic_id] = record
    _save_json(INDUSTRY_STATE_PATH, all_state)

    all_metrics = _load_json(INDUSTRY_METRICS_PATH, {})
    all_metrics[topic_id] = metrics
    _save_json(INDUSTRY_METRICS_PATH, all_metrics)

    all_evidence = _load_json(INDUSTRY_EVIDENCE_PATH, {})
    all_evidence[topic_id] = {
        "sources": [
            {"title": "個股月營收/EPS年增率（既有戰略儲備庫評分）", "grade": "A",
             "source": "FinMind官方財報（data/rex_scores.json）",
             "date": _today(), "sample": metrics["fundamental_sample"]},
            {"title": "個股價格趨勢（20/60/120日）", "grade": "A",
             "source": "TWSE/OTC日K（data/prices/*.csv）",
             "date": metrics.get("latest_price_date") or "—", "sample": metrics["price_sample"]},
            {"title": "產業狀態分類（依上述A級數字計算之衍生結果）", "grade": "C",
             "source": "classify_industry_state()", "date": _today(), "sample": None},
        ],
        "summary": summary,
        "updated_at": _now(),
    }
    _save_json(INDUSTRY_EVIDENCE_PATH, all_evidence)

    history = _load_json(INDUSTRY_STATE_HISTORY_PATH, {})
    bucket = history.setdefault(topic_id, [])
    today_s = _today()
    bucket = [h for h in bucket if h.get("date") != today_s]
    bucket.append({"date": today_s, "state": display_state, "evidence_quality": quality})
    history[topic_id] = sorted(bucket, key=lambda x: x["date"])[-365:]
    _save_json(INDUSTRY_STATE_HISTORY_PATH, history)

    return {"metrics": metrics, "state_record": record, "summary": summary}


def refresh_all_industries():
    twii_ret60 = get_twii_ret(60)  # 只抓一次大盤資料，8個Topic共用
    return {t: refresh_industry_state(t, twii_ret60=twii_ret60) for t in TOPIC_IDS}


def load_industry_state():
    return _load_json(INDUSTRY_STATE_PATH, {})


def load_industry_metrics():
    return _load_json(INDUSTRY_METRICS_PATH, {})


def load_industry_evidence():
    return _load_json(INDUSTRY_EVIDENCE_PATH, {})


def load_industry_state_history():
    return _load_json(INDUSTRY_STATE_HISTORY_PATH, {})


def set_manual_override(topic_id, *, state, reason, reviewer="Rex"):
    """
    進階人工覆核：只調整顯示狀態，不覆蓋 auto_state 原始計算結果。
    完整異動記錄（覆核前/後/原因/時間/人）另存到 attack_engine 的
    manual_reviews.json（呼叫端負責，這裡只更新 industry_state.json）。
    """
    if state not in ALLOWED_STATES:
        raise ValueError(f"invalid state: {state}")
    all_state = _load_json(INDUSTRY_STATE_PATH, {})
    rec = all_state.get(topic_id, {})
    before = rec.get("display_state")
    rec["manual_override"] = {
        "active": True, "state": state, "reason": reason,
        "reviewer": reviewer, "reviewed_at": _now(), "before": before,
    }
    rec["display_state"] = state
    all_state[topic_id] = rec
    _save_json(INDUSTRY_STATE_PATH, all_state)
    return rec


def clear_manual_override(topic_id):
    all_state = _load_json(INDUSTRY_STATE_PATH, {})
    rec = all_state.get(topic_id, {})
    if rec.get("manual_override"):
        rec["manual_override"]["active"] = False
    rec["display_state"] = rec.get("auto_state", rec.get("display_state"))
    all_state[topic_id] = rec
    _save_json(INDUSTRY_STATE_PATH, all_state)
    return rec


# ══════════════════════════════════════════════════════════════
# ▌ 供 Tab3/Tab7 查詢：個股的產業背景（唯讀，不寫入個股證據）
# ══════════════════════════════════════════════════════════════

def get_stock_industry_context(stock_id):
    """
    回傳個股所屬 Topic 的產業背景摘要（industry_context），
    純粹供顯示／參考使用，【不會】疊加進攻擊引擎的個股基本面40分。
    """
    stock_to_topics, _ = get_stock_topic_maps()
    topics = sorted(stock_to_topics.get(stock_id, []))
    if not topics:
        return {"topics": [], "industry_context_score": None, "industry_state": [],
                "industry_evidence_quality": None, "industry_risks": [], "industry_catalysts": []}

    state_data = load_industry_state()
    states, qualities, risks, catalysts = [], [], [], []
    ratio_map = {"需求加速": 1.0, "需求成長": 0.75, "成長減速": 0.5,
                 "估值修正": 0.5, "景氣反轉": 0.15, "證據衝突": 0.4, "證據不足": 0.3}
    for t in topics:
        rec = state_data.get(t)
        if not rec:
            continue
        st_ = rec.get("display_state", "證據不足")
        states.append({"topic": t, "label": TOPIC_LABELS.get(t, t), "state": st_,
                        "confidence": rec.get("confidence")})
        qualities.append(rec.get("evidence_quality", 0))
        if st_ in ("景氣反轉", "證據衝突"):
            risks.append(f"{TOPIC_LABELS.get(t,t)}：{st_}")
        if st_ in ("需求加速", "需求成長"):
            catalysts.append(f"{TOPIC_LABELS.get(t,t)}：{st_}")

    context_score = (round(sum(ratio_map.get(s["state"], 0.3) for s in states) / len(states) * 100)
                      if states else None)
    evidence_quality = round(sum(qualities) / len(qualities)) if qualities else None

    return {
        "topics": topics, "industry_context_score": context_score,
        "industry_state": states, "industry_evidence_quality": evidence_quality,
        "industry_risks": risks, "industry_catalysts": catalysts,
    }
