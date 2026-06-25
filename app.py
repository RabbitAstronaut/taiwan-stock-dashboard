"""
app.py  ── 台股全週期量化交易系統 V6
═══════════════════════════════════════════════════════════════
架構：Serverless CSV 託管
  資料來源：GitHub raw CSV（由 update_data.py / GitHub Actions 更新）
  部署平台：Streamlit Cloud（固定網址，讀 GitHub CSV）

三大分頁：
  Tab 1 ── 選股掃描儀（階層式篩選＋評分系統）
  Tab 2 ── 持股監控（防守警示＋籌碼純度＋基本面）
  Tab 3 ── 大盤預警（期貨引擎＋蒙格行為學＋AI診斷）
═══════════════════════════════════════════════════════════════
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timedelta
import time, warnings, json, os
from zoneinfo import ZoneInfo
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# ▌ 頁面基礎設定
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="台股量化系統 V6",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── GitHub raw URL 前綴（★ 請修改為你的帳號/repo）
GITHUB_RAW   = "https://raw.githubusercontent.com/RabbitAstronaut/taiwan-stock-dashboard/main/data"
GITHUB_REPO  = "RabbitAstronaut/taiwan-stock-dashboard"
# Streamlit Cloud 用 st.secrets，本機用 os.environ
try:
    GITHUB_TOKEN = st.secrets["GH_TOKEN"]
except Exception:
    GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")

def load_watchlist_from_github():
    """從 GitHub 讀取監控清單與 ETF 持倉"""
    try:
        url = f"{GITHUB_RAW}/watchlist.json"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("manual", []), data.get("scan", []), data.get("etf_shares", {})
    except Exception:
        pass
    return [], [], {}

def save_watchlist_to_github(manual_list, scan_list, etf_shares=None, reserve=None):
    """用 GitHub API 把監控清單與 ETF 持倉存到 data/watchlist.json"""
    if not GITHUB_TOKEN:
        return False
    import base64, json as _json
    payload = _json.dumps({"manual": manual_list, "scan": scan_list, "etf_shares": etf_shares or {}, "reserve": reserve or []}, ensure_ascii=False, indent=2)
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/watchlist.json"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    # 先取得現有 sha（更新需要）
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    body = {"message": "update watchlist", "content": base64.b64encode(payload.encode()).decode()}
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(api_url, headers=headers, json=body, timeout=15)
        return r.status_code in (200, 201)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# ▌ 利多不漲排毒｜四大行業板塊戰備庫（watch_list.json）讀寫
# ══════════════════════════════════════════════════════════════
# JSON 結構：
# {
#   "ai_semi":       ["2330","2454","2317","2357"],  # 🟢 AI與半導體核心
#   "ai_infra":      ["2345","2308","3017","8299"],  # 🔵 AI剛需基礎建設
#   "next_gen":      ["3491","2359","1519","3037"],  # 🟡 次世代戰略兵器
#   "shipping_fin":  ["2603","2610","2002","1101","2891"]  # 🔴 航運傳產金融
# }

# 四大板塊預設值（首次載入或 JSON 不存在時使用）
WATCH_LIST_DEFAULTS = {
    "ai_semi":      ["2330", "2454", "2317", "2357"],
    "ai_infra":     ["2345", "2308", "3017", "8299"],
    "next_gen":     ["3491", "2359", "1519", "3037"],
    "shipping_fin": ["2603", "2610", "2002", "1101", "2891"],
}
WATCH_LIST_SECTORS = {
    "ai_semi":      "🟢 AI 與半導體核心",
    "ai_infra":     "🔵 AI 剛需基礎建設",
    "next_gen":     "🟡 次世代戰略兵器",
    "shipping_fin": "🔴 航運傳產與大型金融",
}
WATCH_LIST_MAX_PER_ROW = 10  # 每行最多10檔（合計上限40檔）

# ══════════════════════════════════════════════════════════════
# ▌ 台股法定摩擦成本常數（剛性鎖定，拒絕估算誤差）
# ══════════════════════════════════════════════════════════════
FEE_RATE = 0.001425   # 法定手續費率 0.1425%
DISCOUNT = 0.6        # 券商手續費 6 折（可自行微調）
TAX_RATE = 0.003      # 常規股票證交稅率 0.3%（ETF 為 0.001，此處用股票標準值）
FEE_MIN  = 20         # 手續費未滿 20 元剛性計 20 元

PORTFOLIO_PATH = os.path.join("data", "portfolio.json")
TRADES_PATH    = os.path.join("data", "trades.json")
ACCOUNT_PATH   = os.path.join("data", "account.json")


def _calc_fee(price: float, qty: int) -> float:
    """計算單邊手續費（買入或賣出），未滿20元以20元計"""
    return max(FEE_MIN, price * qty * FEE_RATE * DISCOUNT)


def calc_buy_cost(buy_price: float, qty: int) -> float:
    """買入總成本 = 買入金額 + 買入手續費"""
    return buy_price * qty + _calc_fee(buy_price, qty)


def calc_net_inflow(sell_price: float, qty: int) -> float:
    """賣出實收金額 = 賣出金額 - 賣出手續費 - 證交稅"""
    return sell_price * qty - _calc_fee(sell_price, qty) - sell_price * qty * TAX_RATE


def calc_net_profit(buy_price: float, sell_price: float, qty: int) -> tuple:
    """
    計算純損益與投資報酬率（扣除完整摩擦成本後）。
    回傳 (net_profit, roi_pct)
    """
    cost   = calc_buy_cost(buy_price, qty)
    inflow = calc_net_inflow(sell_price, qty)
    profit = inflow - cost
    roi    = (profit / cost * 100) if cost > 0 else 0.0
    return profit, roi


# ══════════════════════════════════════════════════════════════
# ▌ 帳務三層 JSON 讀寫（portfolio / trades / account）
# ══════════════════════════════════════════════════════════════

@st.cache_data(ttl=30, show_spinner=False)
def load_portfolio() -> dict:
    """
    讀取 data/portfolio.json（當前持倉）。
    格式：{"代號": {"buy_price": float, "qty": int,
                    "stop_loss": float, "stop_profit": float,
                    "buy_date": "YYYY-MM-DD"}}
    """
    import json as _json
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def save_portfolio(portfolio: dict) -> bool:
    """覆寫 data/portfolio.json，成功回傳 True"""
    import json as _json
    try:
        os.makedirs("data", exist_ok=True)
        with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
            _json.dump(portfolio, f, ensure_ascii=False, indent=2)
        load_portfolio.clear()
        return True
    except Exception:
        return False


@st.cache_data(ttl=30, show_spinner=False)
def load_trades() -> list:
    """
    讀取 data/trades.json（歷史交易明細）。
    格式：[{"date":..,"action":"買入"/"賣出","stock_id":..,"price":..,"qty":..
             "fee":..,"tax":..,"amount":..,"realized_pnl":..,"roi_pct":..}, ...]
    """
    import json as _json
    if os.path.exists(TRADES_PATH):
        try:
            with open(TRADES_PATH, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return []


def save_trades(trades: list) -> bool:
    """覆寫 data/trades.json，成功回傳 True"""
    import json as _json
    try:
        os.makedirs("data", exist_ok=True)
        with open(TRADES_PATH, "w", encoding="utf-8") as f:
            _json.dump(trades, f, ensure_ascii=False, indent=2)
        load_trades.clear()
        return True
    except Exception:
        return False


@st.cache_data(ttl=30, show_spinner=False)
def load_account() -> dict:
    """
    讀取 data/account.json（帳戶層級：初始資金、現金、累計已實現損益）。
    格式：{"initial_capital": float, "cash": float, "realized_pnl": float}
    """
    import json as _json
    if os.path.exists(ACCOUNT_PATH):
        try:
            with open(ACCOUNT_PATH, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return {"initial_capital": 0.0, "cash": 0.0, "realized_pnl": 0.0}


def save_account(account: dict) -> bool:
    """覆寫 data/account.json，成功回傳 True"""
    import json as _json
    try:
        os.makedirs("data", exist_ok=True)
        with open(ACCOUNT_PATH, "w", encoding="utf-8") as f:
            _json.dump(account, f, ensure_ascii=False, indent=2)
        load_account.clear()
        return True
    except Exception:
        return False


@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_name_map() -> dict:
    """
    從 stock_list.csv 建立 {stock_id: stock_name} 對照表。
    快取1小時，供帳務系統顯示股票名稱使用。
    """
    try:
        df, ok = load_csv("stock_list.csv")
        if ok and not df.empty and "stock_id" in df.columns:
            df["stock_id"] = df["stock_id"].astype(str).str.strip()
            name_col = next((c for c in ["stock_name","name"] if c in df.columns), None)
            if name_col:
                return dict(zip(df["stock_id"], df[name_col]))
    except Exception:
        pass
    return {}


@st.cache_data(ttl=300, show_spinner=False)
def get_chips_facts_map() -> dict:
    """
    從 chips_data.csv 和 margin.csv 建立個股籌碼事實對照表。

    回傳格式：
    {
      "stock_id": {
        "foreign_net": 外資當日買超張數（正=買超，負=賣超）,
        "margin_chg_pct": 融資增減比例（%），
        "margin_today": 今日融資餘額,
        "margin_yesterday": 昨日融資餘額,
      }
    }
    讀取失敗的個股回傳 None，不影響其他個股。
    """
    result = {}

    # ── 1. 外資買超（chips_data.csv）
    try:
        df_c, ok_c = load_csv("chips_data.csv")
        if ok_c and not df_c.empty:
            df_c["stock_id"] = df_c["stock_id"].astype(str).str.strip()
            df_c["net"] = pd.to_numeric(df_c.get("net"), errors="coerce")
            df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
            # 只取外資（Foreign_Investor）
            df_foreign = df_c[df_c["name"].astype(str).str.contains("Foreign_Investor", na=False)]
            # 單位轉換：股→張
            if not df_foreign.empty and df_foreign["net"].abs().max() > 50000:
                df_foreign = df_foreign.copy()
                df_foreign["net"] = df_foreign["net"] / 1000
            # 每支股票取最新一筆
            for sid, g in df_foreign.groupby("stock_id"):
                g = g.dropna(subset=["date"]).sort_values("date")
                if not g.empty:
                    if sid not in result:
                        result[sid] = {}
                    result[sid]["foreign_net"] = round(float(g.iloc[-1]["net"]), 0)
        del df_c
        import gc; gc.collect()
    except Exception as e:
        pass

    # ── 2. 融資增減（margin.csv）
    try:
        df_m, ok_m = load_csv("margin.csv")
        if ok_m and not df_m.empty:
            df_m["stock_id"] = df_m["stock_id"].astype(str).str.strip()
            _today_col  = "MarginPurchaseTodayBalance"
            _yest_col   = "MarginPurchaseYesterdayBalance"
            if _today_col in df_m.columns and _yest_col in df_m.columns:
                df_m[_today_col] = pd.to_numeric(df_m[_today_col], errors="coerce")
                df_m[_yest_col]  = pd.to_numeric(df_m[_yest_col],  errors="coerce")
                df_m = df_m.dropna(subset=[_today_col, _yest_col])
                # 每支股票取最新一筆
                df_m_latest = df_m.sort_values("date") if "date" in df_m.columns else df_m
                df_m_latest = df_m_latest.drop_duplicates("stock_id", keep="last")
                for _, row in df_m_latest.iterrows():
                    sid  = str(row["stock_id"])
                    t    = float(row[_today_col])
                    y    = float(row[_yest_col])
                    pct  = round((t - y) / y * 100, 2) if y != 0 else 0.0
                    if sid not in result:
                        result[sid] = {}
                    result[sid]["margin_today"]     = t
                    result[sid]["margin_yesterday"]  = y
                    result[sid]["margin_chg_pct"]    = pct
        del df_m
        import gc; gc.collect()
    except Exception:
        pass

    return result


# ══════════════════════════════════════════════════════════════
# ▌ 異常變盤因果律自適應決策函數（全域共用，Tab3 + Tab4 同步調用）
# ══════════════════════════════════════════════════════════════
def check_anomaly_variant(
    stock_id: str,
    strategy_type: str,
    current_price: float,
    ma20: float,
    foreign_buy,      # 外資買賣超張數（可能為 None）
    margin_change,    # 融資增減百分比（可能為 None）
) -> dict:
    """
    異常變盤因果律核心演算法（100% 動態運算，拒絕寫死任何股票代號或假數據）。

    參數：
        stock_id      ：股票代號（僅用於回傳訊息顯示，不做任何 if stock_id == 判斷）
        strategy_type ：'LONG' 或 'SHORT'，個股的長短線戰略標籤
        current_price ：當前現價
        ma20          ：20日移動平均線（月線）
        foreign_buy   ：外資當日買賣超張數（正=買超，負=賣超），無資料則傳 None
        margin_change ：融資當日增減百分比，無資料則傳 None

    回傳：
        {
          "triggered": bool,          # 是否觸發任何異常變盤訊號
          "level": str,               # "AS_RETREAT"（以退為進令）/ "DIAMOND_BUY"（鑽石級布局令）/ None
          "market_level": str,        # "HIGH_RISK" / "BOTTOM_SAFE" / "NORMAL"
          "bias_20": float,           # 月線乖離率(%)
          "message": str,             # 完整繁中提示文字
        }

    判定邏輯：
        bias_20 = (現價 - 20MA) / 20MA × 100
        bias_20 >= +10.0  → market_level = "HIGH_RISK"   （高位階重災區）
        bias_20 <= -10.0  → market_level = "BOTTOM_SAFE" （底部隔離避風港）
        其餘區間          → market_level = "NORMAL"      （橫盤監控區）

        【以退為進令】strategy_type=="SHORT" 且 market_level=="HIGH_RISK"
                     且 foreign_buy<0 且 margin_change>=2.0
        【鑽石級布局令】strategy_type=="LONG" 且 market_level=="BOTTOM_SAFE"
                     且 margin_change<=-1.5
    """
    result = {
        "triggered": False, "level": None, "market_level": "NORMAL",
        "bias_20": 0.0, "message": "",
    }

    # 防呆：20MA 無效時無法計算乖離率
    if ma20 is None or ma20 <= 0 or current_price is None:
        return result

    bias_20 = (current_price - ma20) / ma20 * 100
    result["bias_20"] = bias_20

    # ── 動態位階自適應分流（拒絕寫死任何股票代號判斷）
    if bias_20 >= 10.0:
        market_level = "HIGH_RISK"
    elif bias_20 <= -10.0:
        market_level = "BOTTOM_SAFE"
    else:
        market_level = "NORMAL"
    result["market_level"] = market_level

    # ══════════════════════════════════════
    # 🚨 以退為進令：SHORT + HIGH_RISK + 外資賣超 + 融資逆勢大增
    # ══════════════════════════════════════
    if (strategy_type == "SHORT" and market_level == "HIGH_RISK" and
        foreign_buy is not None and foreign_buy < 0 and
        margin_change is not None and margin_change >= 2.0):

        result["triggered"] = True
        result["level"] = "AS_RETREAT"
        result["message"] = (
            f"🚨 **【異常變盤警告｜以退為進】{stock_id}**\n\n"
            f"變盤因果律 Facts：月線正乖離 **{bias_20:+.1f}%**（高位階重災區），"
            f"外資冷血大賣 **{foreign_buy:,.0f} 張**，散戶融資卻逆勢大增 **{margin_change:+.2f}%**！\n\n"
            f"最高風控令：此處拉高純屬誘敵接刀煙霧彈，系統硬核封印買入權限，"
            f"請執行『以退為進』清倉平倉，出貨後完全移除不追蹤！"
        )

    # ══════════════════════════════════════
    # 💎 鑽石級布局令：LONG + BOTTOM_SAFE + 融資大減
    # ══════════════════════════════════════
    elif (strategy_type == "LONG" and market_level == "BOTTOM_SAFE" and
          margin_change is not None and margin_change <= -1.5):

        result["triggered"] = True
        result["level"] = "DIAMOND_BUY"
        result["message"] = (
            f"💎 **【異常變盤指引｜底部布局】{stock_id}**\n\n"
            f"變盤因果律 Facts：月線負乖離 **{bias_20:+.1f}%**（底部隔離避風港），"
            f"散戶融資正大幅割肉斷頭 **{margin_change:+.2f}%**！"
            f"系統全面屏蔽短線破位雜訊。\n\n"
            f"最高風控令：此處即為鑽石級底部加碼點，請動用場外7成現金儲備分批優雅吸籌，"
            f"持股雷打不動死鎖至年底，出清後必須持續追蹤！"
        )

    return result


# ══════════════════════════════════════════════════════════════
# ▌ Rex Priority Score 排序引擎
# ──────────────────────────────────────────────────────────────
# 設計說明：
#   本模組是「注意力分配工具」，不是買進訊號。
#   回答的問題是：「今天應該把研究時間花在哪5檔？」
#
#   三層架構：
#     王者分數(40)  = 公司基本面品質
#     攻擊分數(40)  = 技術面買點位置
#     市場環境分(20) = 整體環境允不允許出手
#
#   降級旗標：即使總分高，觸發旗標時會標記警告
# ══════════════════════════════════════════════════════════════

def _rex_king_score(stock_id: str) -> dict:
    """
    王者分數（40分）：這家公司值不值得等？
    來源：financial_data.csv（Revenue/EPS/GrossMargin）
          shareholder_data.csv（大戶持股趨勢）
          由使用者手動標記的產業風口（strategy_tag 或預設中性）
    """
    result = {
        "total": 0,
        "revenue_yoy_score": 0, "revenue_yoy_val": None,
        "eps_yoy_score": 0,     "eps_yoy_val": None,
        "gm_score": 0,          "gm_trend": "—",
        "sector_score": 2,      "sector_tag": "未標記",
        "holder_score": 0,      "holder_trend": "—",
    }
    try:
        sid = str(stock_id).strip()

        # ── 讀取財報資料（使用 get_financials，自動處理 int64 stock_id）
        df_fin, ok_fin = get_financials(sid)

        if ok_fin and not df_fin.empty and "type" in df_fin.columns:

            def _get_type_series(df, type_kw):
                """取得特定 type 的季度數值序列（依日期排序）"""
                _sub = df[df["type"].astype(str).str.contains(type_kw, case=False, na=False)].copy()
                if "date" in _sub.columns:
                    _sub = _sub.sort_values("date")
                return _sub["value"].dropna().astype(float).tolist() if "value" in _sub.columns else []

            # ── 營收 YoY（type = Revenue，比較最近兩季同期）
            rev_vals = _get_type_series(df_fin, "Revenue")
            if len(rev_vals) >= 2:
                latest, prev = rev_vals[-1], rev_vals[-2]
                if prev != 0:
                    yoy = (latest - prev) / abs(prev) * 100
                    result["revenue_yoy_val"] = round(yoy, 1)
                    if yoy >= 30:   result["revenue_yoy_score"] = 10
                    elif yoy >= 15: result["revenue_yoy_score"] = 7
                    elif yoy >= 0:  result["revenue_yoy_score"] = 4
                    else:           result["revenue_yoy_score"] = 0

            # ── 稅後淨利 YoY（type = IncomeAfterTaxes，替代 EPS）
            eat_vals = _get_type_series(df_fin, "IncomeAfterTaxes")
            if len(eat_vals) >= 2:
                e_lat, e_prev = eat_vals[-1], eat_vals[-2]
                if e_prev != 0:
                    e_yoy = (e_lat - e_prev) / abs(e_prev) * 100
                    result["eps_yoy_val"] = round(e_yoy, 1)
                    if e_yoy >= 30:   result["eps_yoy_score"] = 10
                    elif e_yoy >= 15: result["eps_yoy_score"] = 7
                    elif e_yoy >= 0:  result["eps_yoy_score"] = 4
                    else:             result["eps_yoy_score"] = 0

            # ── 毛利率趨勢（GrossProfit / Revenue，動態計算比例後比較趨勢）
            gp_vals  = _get_type_series(df_fin, "GrossProfit")
            rev_vals2 = _get_type_series(df_fin, "Revenue")
            n = min(len(gp_vals), len(rev_vals2))
            if n >= 3:
                # 計算近3季毛利率
                gm_rates = []
                for _i in range(-3, 0):
                    _r = rev_vals2[_i]
                    gm_rates.append(gp_vals[_i] / _r * 100 if _r else None)
                gm_rates = [g for g in gm_rates if g is not None]
                if len(gm_rates) >= 3:
                    g0, g1, g2 = gm_rates[0], gm_rates[1], gm_rates[2]
                    if g2 > g1 > g0:
                        result["gm_score"], result["gm_trend"] = 10, f"連續提升({g2:.1f}%) ✅"
                    elif g2 > g1:
                        result["gm_score"], result["gm_trend"] = 7, f"近季提升({g2:.1f}%)"
                    elif abs(g2 - g1) < 1:
                        result["gm_score"], result["gm_trend"] = 5, f"持平({g2:.1f}%)"
                    elif g2 < g1 and g1 >= g0:
                        result["gm_score"], result["gm_trend"] = 2, f"單季下滑({g2:.1f}%) ⚠️"
                    else:
                        result["gm_score"], result["gm_trend"] = 0, f"連續下滑({g2:.1f}%) ❌"

        # ── 大戶持股趨勢（shareholder_data.csv）
        # 400張（400,000股）以上層級加總 percent，比較最近兩個日期的變化
        try:
            df_sh, ok_sh = load_csv("shareholder_data.csv")
            if ok_sh and not df_sh.empty:
                df_sh["stock_id"] = df_sh["stock_id"].astype(str).str.strip()
                sh_s = df_sh[df_sh["stock_id"] == sid].copy()

                # 400張以上的層級
                _big_levels = {
                    "400,001-600,000", "600,001-800,000",
                    "800,001-1,000,000", "more than 1,000,001"
                }
                sh_big = sh_s[sh_s["HoldingSharesLevel"].isin(_big_levels)].copy()

                if not sh_big.empty and "date" in sh_big.columns and "percent" in sh_big.columns:
                    sh_big["percent"] = pd.to_numeric(sh_big["percent"], errors="coerce")
                    sh_big["date"]    = pd.to_datetime(sh_big["date"], errors="coerce")

                    # 各日期加總大戶比例
                    sh_agg = sh_big.groupby("date")["percent"].sum().sort_index()

                    if len(sh_agg) >= 2:
                        r_lat  = float(sh_agg.iloc[-1])
                        r_prev = float(sh_agg.iloc[-2])
                        diff   = r_lat - r_prev
                        if diff > 0.5:
                            result["holder_score"] = 5
                            result["holder_trend"] = f"持續上升({r_lat:.1f}%) ✅"
                        elif diff >= 0:
                            result["holder_score"] = 3
                            result["holder_trend"] = f"持平({r_lat:.1f}%)"
                        else:
                            result["holder_score"] = 0
                            result["holder_trend"] = f"下滑({r_lat:.1f}%) ⚠️"
        except Exception:
            pass

    except Exception:
        pass

    # ── 產業風口強度：讀取使用者在 Tab4 手動標記的產業景氣燈號
    _sector_score_map = {
        "🌱 起步": 4, "🚀 加速": 5, "⚡ 高峰": 2,
        "🌙 衰退": 0, "❓ 未標記": 2,
    }
    _stock_sector_map = {
        "2330": "AI算力",    "2454": "AI算力",
        "2383": "高速傳輸",  "2345": "高速傳輸",
        "3017": "散熱電源",  "2308": "散熱電源",
        "1519": "電力基建",
        "3491": "低軌衛星",
        "3037": "半導體供應鏈", "8299": "半導體供應鏈",
        "6285": "網通",
    }
    try:
        _sid_sector   = _stock_sector_map.get(str(stock_id), "其他電子")
        _sector_tags  = st.session_state.get("sector_tags", {})
        _sector_label = _sector_tags.get(_sid_sector, "❓ 未標記")
        result["sector_score"] = _sector_score_map.get(_sector_label, 2)
        result["sector_tag"]   = f"{_sid_sector}｜{_sector_label}"
    except Exception:
        pass

    result["total"] = (
        result["revenue_yoy_score"] + result["eps_yoy_score"] +
        result["gm_score"] + result["sector_score"] + result["holder_score"]
    )
    return result


def _rex_attack_score(stock_id: str, chips_map: dict) -> dict:
    """
    攻擊分數（40分）：現在的進場位置是否具備優勢？
    來源：prices/*.csv（支撐位置/MA結構/MOM動能）
          chips_data.csv + margin.csv（籌碼沉澱）
    """
    result = {
        "total": 0,
        "support_score": 0, "support_detail": "—",
        "ma_score": 0,      "ma_detail": "—",
        "mom_score": 0,     "mom_detail": "—",
        "chips_score": 0,   "chips_detail": "—",
        "downgrade_flag": None,  # 降級旗標
        "bias_20": None,
    }
    try:
        df_p, ok_p = load_price_csv(stock_id)
        if not ok_p or df_p.empty or len(df_p) < 65:
            return result

        df_p = df_p.copy()
        closes = pd.to_numeric(df_p["Close"], errors="coerce").dropna()
        if len(closes) < 65:
            return result

        price    = float(closes.iloc[-1])
        sma20    = float(closes.tail(20).mean())
        sma60    = float(closes.tail(60).mean())
        bias_20  = (price - sma20) / sma20 * 100 if sma20 > 0 else 0.0
        result["bias_20"] = round(bias_20, 1)

        # ── SMA斜率（用近10日vs近20日均線方向判定）
        sma20_now  = float(closes.tail(20).mean())
        sma20_prev = float(closes.iloc[-21:-1].mean()) if len(closes) >= 21 else sma20_now
        sma60_now  = float(closes.tail(60).mean())
        sma60_prev = float(closes.iloc[-61:-1].mean()) if len(closes) >= 61 else sma60_now
        sma20_up   = sma20_now > sma20_prev
        sma60_up   = sma60_now > sma60_prev

        # ── 支撐位置（10分）
        if bias_20 <= -10 and price >= sma60:
            result["support_score"]  = 10
            result["support_detail"] = f"深度回測月線({bias_20:+.1f}%)且守季線 ✅"
        elif bias_20 <= -5 and price >= sma60:
            result["support_score"]  = 8
            result["support_detail"] = f"回測月線({bias_20:+.1f}%)且季線健在"
        elif bias_20 <= -2 and price >= sma20:
            result["support_score"]  = 6
            result["support_detail"] = f"輕微回落至月線附近({bias_20:+.1f}%)"
        elif -2 < bias_20 <= 5:
            result["support_score"]  = 3
            result["support_detail"] = f"月線上方不遠({bias_20:+.1f}%)"
        elif bias_20 > 5:
            result["support_score"]  = 1
            result["support_detail"] = f"正乖離({bias_20:+.1f}%)，追高風險偏高"
        if price < sma60:
            result["support_score"]  = max(0, result["support_score"] - 3)
            result["support_detail"] += "｜跌破季線 ⚠️"

        # ── MA結構（10分）+ 降級旗標
        if price >= sma20 >= sma60 and sma20_up:
            result["ma_score"]  = 10
            result["ma_detail"] = "月線>季線 且月線上彎 ✅"
        elif price >= sma20 >= sma60 and not sma20_up:
            result["ma_score"]  = 7
            result["ma_detail"] = "月線>季線 但月線走平"
        elif sma20 >= sma60 and price < sma20:
            result["ma_score"]  = 5
            result["ma_detail"] = "月線仍>季線，股價回測月線"
        elif sma20 < sma60 and sma60_up:
            result["ma_score"]  = 4
            result["ma_detail"] = "月線跌破季線 但季線仍向上（觀察中）⚠️"
        elif sma20 < sma60 and not sma60_up:
            result["ma_score"]  = 0
            result["ma_detail"] = "月線跌破季線 且季線下彎 ❌"
            result["downgrade_flag"] = "⛔ 趨勢破壞"

        # ── MOM 動能（10分）
        if len(closes) >= 61:
            mom_20 = (price - float(closes.iloc[-21])) / float(closes.iloc[-21]) * 100
            mom_60 = (price - float(closes.iloc[-61])) / float(closes.iloc[-61]) * 100
            if mom_20 > 0 and mom_60 > 0:
                result["mom_score"]  = 10
                result["mom_detail"] = f"短中期動能皆正(20D:{mom_20:+.1f}% 60D:{mom_60:+.1f}%) ✅"
            elif mom_20 > 0 and mom_60 <= 0:
                result["mom_score"]  = 7
                result["mom_detail"] = f"短線反彈 中期仍弱(20D:{mom_20:+.1f}% 60D:{mom_60:+.1f}%)"
            elif mom_20 <= 0 and mom_60 > 0:
                result["mom_score"]  = 5
                result["mom_detail"] = f"短線回落 中期仍強(20D:{mom_20:+.1f}% 60D:{mom_60:+.1f}%) 洗盤機會"
            else:
                result["mom_score"]  = 2
                result["mom_detail"] = f"短中期動能皆負(20D:{mom_20:+.1f}% 60D:{mom_60:+.1f}%)，尚未止跌"

        # ── 籌碼沉澱（10分）：外資(4) + 融資方向(6)
        chip = chips_map.get(str(stock_id), {})
        fgn  = chip.get("foreign_net", None)
        mgp  = chip.get("margin_chg_pct", None)

        fgn_score = 4 if (fgn is not None and fgn > 500)  else \
                    2 if (fgn is not None and fgn > 0)     else 0
        mg_score  = 6 if (mgp is not None and mgp <= -2.0) else \
                    4 if (mgp is not None and mgp <= 0)    else \
                    1 if (mgp is not None and mgp <= 2)    else 0

        result["chips_score"] = fgn_score + mg_score
        fgn_txt = f"外資{fgn:+,.0f}張" if fgn is not None else "外資—"
        mg_txt  = f"融資{mgp:+.2f}%" if mgp is not None else "融資—"
        result["chips_detail"] = f"{fgn_txt}｜{mg_txt}"

        # 籌碼惡化旗標
        if fgn is not None and fgn < -500 and mgp is not None and mgp > 2:
            result["downgrade_flag"] = result.get("downgrade_flag") or "🚨 籌碼惡化"

    except Exception:
        pass

    result["total"] = (
        result["support_score"] + result["ma_score"] +
        result["mom_score"] + result["chips_score"]
    )
    return result


def _rex_market_score(market_signal: str) -> int:
    """
    市場環境分（20分）：整體環境允不允許出手？
    承接 Tab4 市場溫度計的輸出燈號。
    """
    if "🟢" in str(market_signal): return 20
    if "🟡" in str(market_signal): return 10
    return 0


@st.cache_data(ttl=1800, show_spinner=False)
def calc_rex_priority_scores(reserve_ids: tuple, market_signal: str = "🟡") -> list:
    """
    對戰略儲備庫全部標的計算 Rex Priority Score。
    回傳按總分降序排列的清單。
    快取30分鐘，避免每次刷新都重算。

    Args:
        reserve_ids:   儲備庫股票代號的 tuple（用於快取key）
        market_signal: 市場溫度計燈號（🟢/🟡/🔴）
    """
    import gc as _gc
    chips_map = get_chips_facts_map()
    mkt_score = _rex_market_score(market_signal)
    results   = []

    for sid in reserve_ids:
        try:
            king   = _rex_king_score(sid)
            attack = _rex_attack_score(sid, chips_map)
            total  = king["total"] + attack["total"] + mkt_score

            # 降級旗標判定
            flag = attack.get("downgrade_flag")
            if king["revenue_yoy_val"] is not None and king["eps_yoy_val"] is not None:
                if king["revenue_yoy_val"] < 0 and king["eps_yoy_val"] < 0:
                    flag = flag or "⚠️ 基本面衰退"

            results.append({
                "stock_id":     sid,
                "total":        total,
                "king_total":   king["total"],
                "attack_total": attack["total"],
                "mkt_score":    mkt_score,
                "flag":         flag,
                # 王者分數明細
                "revenue_yoy_score": king["revenue_yoy_score"],
                "revenue_yoy_val":   king["revenue_yoy_val"],
                "eps_yoy_score":     king["eps_yoy_score"],
                "eps_yoy_val":       king["eps_yoy_val"],
                "gm_score":          king["gm_score"],
                "gm_trend":          king["gm_trend"],
                "sector_score":      king["sector_score"],
                "holder_score":      king["holder_score"],
                "holder_trend":      king["holder_trend"],
                # 攻擊分數明細
                "support_score":  attack["support_score"],
                "support_detail": attack["support_detail"],
                "ma_score":       attack["ma_score"],
                "ma_detail":      attack["ma_detail"],
                "mom_score":      attack["mom_score"],
                "mom_detail":     attack["mom_detail"],
                "chips_score":    attack["chips_score"],
                "chips_detail":   attack["chips_detail"],
                "bias_20":        attack["bias_20"],
            })
        except Exception:
            pass
        finally:
            _gc.collect()

    results.sort(key=lambda x: (-x["total"], x["stock_id"]))
    return results


@st.cache_data(ttl=60, show_spinner=False)
def load_watch_list():
    """
    讀取 data/watch_list.json，回傳四大板塊 dict。
    讀取順序：本地檔案 → GitHub raw 備援 → 預設值。
    確保任何情況下都能回傳合法結構，不報錯。
    """
    import json as _json, os as _os

    def _normalize(wl_raw):
        """統一格式，填補缺失板塊為預設值"""
        result = {}
        for key, default in WATCH_LIST_DEFAULTS.items():
            val = wl_raw.get(key) or wl_raw.get("sector_leaders", [])
            result[key] = [str(s).strip() for s in (val if val else default)]
        return result

    # 1) 本地檔案
    _local = _os.path.join("data", "watch_list.json")
    if _os.path.exists(_local):
        try:
            with open(_local, "r", encoding="utf-8") as f:
                return _normalize(_json.load(f))
        except Exception:
            pass

    # 2) GitHub raw 備援
    try:
        r = requests.get(f"{GITHUB_RAW}/watch_list.json", timeout=5)
        if r.status_code == 200:
            return _normalize(r.json())
    except Exception:
        pass

    # 3) 預設值（首次部署或 JSON 尚未建立）
    return dict(WATCH_LIST_DEFAULTS)


def save_watch_list_to_github(sectors: dict) -> bool:
    """
    將四大板塊名單【實時覆寫】回 data/watch_list.json。

    同時寫入本地副本（供 daily_scan.py 讀取），並透過
    GitHub Contents API 推送至遠端（供 Streamlit Cloud 讀取）。
    寫入成功後立即清除快取，確保下次讀取拿到最新版本。

    sectors 格式：{"ai_semi":[...],"ai_infra":[...],"next_gen":[...],"shipping_fin":[...]}
    成功回傳 True，失敗回傳 False（含無 token 的情況）。
    """
    import json as _json, os as _os, base64

    payload_str = _json.dumps(sectors, ensure_ascii=False, indent=2)

    # 1) 寫入本地檔案（供 daily_scan.py 直接讀取，不依賴 GitHub API）
    try:
        _os.makedirs("data", exist_ok=True)
        with open(_os.path.join("data", "watch_list.json"), "w", encoding="utf-8") as f:
            f.write(payload_str)
    except Exception:
        pass

    # 2) 推送至 GitHub（供 Streamlit Cloud 備援讀取）
    if not GITHUB_TOKEN:
        load_watch_list.clear()
        return True  # 本地已寫入即視為成功（無 token 時只能寫本地）

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/watch_list.json"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    body = {"message": "update watch_list（四大行業龍頭板塊）",
            "content": base64.b64encode(payload_str.encode()).decode()}
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(api_url, headers=headers, json=body, timeout=15)
        ok = r.status_code in (200, 201)
        load_watch_list.clear()
        return ok
    except Exception:
        load_watch_list.clear()
        return False


# ══════════════════════════════════════════════════════════════
# ▌ 風控卡閘核心函式（實時/回溯雙模隔離）
# ══════════════════════════════════════════════════════════════
def check_gatekeeper(sid, bias_ma20, rsi5, ema5, sma20,
                     close_now, high60, high250, vol_now, vma20,
                     is_holding=False, is_backtest=False):
    """
    完全體風控卡閘，具備時空隔離機制。
    回傳 dict: {level, msg, color}
    level: "purple"=停利 / "green_core"=精兵特赦 / "green_expand"=健康擴展
           "green_safe"=安全 / "yellow"=突破放寬 / "red"=攔截
    """
    result = {}

    if not is_backtest:
        # ── 實時模式：與戰略儲備庫聯動
        reserve_ids = set(r["id"] for r in st.session_state.get("reserve_list", []))
        is_core = sid in reserve_ids
    else:
        # ── 回溯模式：完全依賴歷史硬數據，禁用 session_state
        is_core = False

    # 判定突破 Facts（回溯模式用歷史當日數據）
    is_weekly_break = close_now >= high250 * 0.98 if high250 > 0 else False
    is_box_break    = close_now >= high60          if high60  > 0 else False
    is_volume_spike = vma20 > 0 and vol_now >= vma20 * 2.0

    # ── 優先級1：極端超買停利（bias>25 or RSI>85）
    if bias_ma20 > 25 or rsi5 > 85:
        if is_holding:
            result = {"level": "purple",
                      "msg": f"🎯 系統移動停利提示：月乖離 {bias_ma20:.1f}%（RSI5:{rsi5:.1f}），市場情緒極端非理性超買，強烈建議主動減碼 50% 或全數落袋！"}
        else:
            result = {"level": "red",
                      "msg": f"❌ 風控卡閘攔截：嚴重過載（乖離 {bias_ma20:.1f}%，RSI5:{rsi5:.1f}），嚴禁追高！"}

    # ── 優先級2：戰略儲備庫精兵特赦（20% 寬限）
    elif is_core:
        if bias_ma20 <= 20.0:
            result = {"level": "green_core",
                      "msg": f"👑 風控卡閘【儲備精兵特赦】：月乖離 {bias_ma20:.1f}%，本股列管於戰略儲備庫，20% 安全特赦圈內屬健康動能擴張。{'安心波段留倉至 Q3！' if is_holding else '准許手動建倉/加碼，波段留倉至 Q3！'}"}
        else:
            result = {"level": "red",
                      "msg": f"❌ 儲備精兵警示：{sid} 月乖離已達 {bias_ma20:.1f}%，超越 20% 歷史極限，請靜待拉回。"}

    # ── 優先級3：週線大突破特赦（250日，20% 寬限）
    elif is_weekly_break and is_volume_spike:
        if bias_ma20 <= 20.0:
            result = {"level": "green_core",
                      "msg": f"👑 風控卡閘【週線大突破特赦】：月乖離 {bias_ma20:.1f}%，系統偵測到一整年大型橫盤底部歷史性突破！20% 內屬健康動能溢價，{'安心續抱！' if is_holding else '准許建立主攻部位！'}"}
        else:
            result = {"level": "red",
                      "msg": f"❌ 週線突破但月乖離 {bias_ma20:.1f}% 超越 20% 極限，請靜待拉回。"}

    # ── 優先級4：日線箱體突破特赦（60日，12% 寬限）
    elif is_box_break and is_volume_spike and 5 < bias_ma20 <= 12:
        result = {"level": "yellow",
                  "msg": f"🟢 風控卡閘通過（強勢起漲點）：月乖離 {bias_ma20:.1f}%，帶量突破 60 日大箱體，{'安心續抱！' if is_holding else '准許手動建立第一筆主攻部位！'}"}

    # ── 優先級5：健康擴展期（5%~25%，已持股）
    elif 5 < bias_ma20 <= 25 and is_holding:
        result = {"level": "green_expand",
                  "msg": f"💡 高檔動能觀察：月乖離 {bias_ma20:.1f}%（RSI5:{rsi5:.1f}），多頭主升段正常動能擴張，籌碼極度安全，安心續抱。回踩 EMA5 ({ema5:.1f}) 或月線 ({sma20:.1f}) 是加碼甜甜點！"}

    # ── 優先級6：常規過熱攔截（5%+ 無特赦資格）
    elif bias_ma20 > 5:
        result = {"level": "red",
                  "msg": f"❌ 風控卡閘攔截：常規個股月乖離 {bias_ma20:.1f}%（RSI5:{rsi5:.1f}），無爆量突破事實，嚴禁追高！安全買點：EMA5 {ema5:.1f} 或月線 {sma20:.1f}。"}

    # ── 優先級7：安全區（≤5% 守月線）
    elif close_now > sma20:
        result = {"level": "green_safe",
                  "msg": f"🟢 風控卡閘通過：月乖離 {bias_ma20:.1f}%，安全右側拉回換手區。{'持股者安心續抱，拉回 EMA5 即加碼機會。' if is_holding else '可手動分批建倉。'}"}

    else:
        result = {"level": "green_safe",
                  "msg": f"🟢 股價處於月線之下，技術面仍在修復中，可持續觀察。"}

    return result

# ══════════════════════════════════════════════════════════════
# ▌ 潛伏期法人暗中鎖碼掃描函式（中信金模型）
# ══════════════════════════════════════════════════════════════
def scan_short_term_momentum(sid):
    """
    捕獲『三大法人合力點火』與『融資退場+信用軋空』的短線火箭演算法
    回傳 dict: {trigger, score, msg, facts}
    """
    result = {"trigger": False, "score": 0, "msg": "", "facts": {}}
    try:
        sid = str(sid).strip()

        # ── 籌碼資料
        df_c, ok_c = get_chips(sid)
        if not ok_c or df_c.empty:
            return result

        df_c["stock_id"] = df_c["stock_id"].astype(str).str.strip()
        df_c["date"]     = pd.to_datetime(df_c["date"], errors="coerce")
        df_c = df_c[df_c["stock_id"] == sid].sort_values("date")

        name_col = next((c for c in ["name","institutional_investors"]
                         if c in df_c.columns), None)

        # ── Fact1：三大法人近3日合計淨買超（物理分別加總，避免重複計算）
        inst_net3 = 0.0
        if name_col and "net" in df_c.columns:
            df_c["net"] = pd.to_numeric(df_c["net"], errors="coerce").fillna(0)
            # 分別抓外資/投信/自營，再各自按日加總後相加
            _daily_nets = []
            for _kw in ["Foreign_Investor", "Investment_Trust", "Dealer"]:
                _sub = df_c[df_c[name_col].astype(str).str.contains(_kw, na=False)].copy()
                if not _sub.empty:
                    _daily = _sub.groupby("date")["net"].sum().sort_index()
                    _daily_nets.append(_daily)
            if _daily_nets:
                import functools
                _combined = functools.reduce(lambda a, b: a.add(b, fill_value=0), _daily_nets)
                if len(_combined) >= 3:
                    inst_net3 = float(_combined.iloc[-3:].sum())
                    # 記錄近3日明細（日期+張數）
                    _last3 = _combined.iloc[-3:]
                    inst_net3_detail = [
                        (str(_last3.index[i])[:10], round(float(_last3.iloc[i])))
                        for i in range(len(_last3))
                    ]
                else:
                    inst_net3_detail = []
            else:
                inst_net3_detail = []
        # 單位自適應：絕對值 > 50000 推測為股，除以1000轉張；否則已是張
        if abs(inst_net3) > 50000:
            inst_net3 /= 1000
        is_institutional_swarm = inst_net3 > 0

        # ── Fact2：融資餘額近3日是否減少（散戶退場）
        margin_bal_now  = 0.0
        margin_bal_3d   = 0.0
        short_bal_now   = 0.0
        margin_source   = df_c[df_c["source"].astype(str) == "margin"]                           if "source" in df_c.columns else pd.DataFrame()
        if not margin_source.empty:
            mg_col = next((c for c in margin_source.columns
                           if "MarginPurchaseTodayBalance" in c), None)
            sh_col = next((c for c in margin_source.columns
                           if "ShortSale" in c and "Balance" in c), None)
            if mg_col:
                mg_vals = pd.to_numeric(margin_source[mg_col], errors="coerce").dropna()
                if len(mg_vals) >= 3:
                    margin_bal_now = float(mg_vals.iloc[-1])
                    margin_bal_3d  = float(mg_vals.iloc[-3])
                if len(mg_vals) >= 5:
                    margin_bal_5d  = float(mg_vals.iloc[-5])
                else:
                    margin_bal_5d  = 0.0
                # 近3日融資餘額明細（日期+餘額）
                _mg_with_date = margin_source[["date", mg_col]].copy() if "date" in margin_source.columns else margin_source[[mg_col]].copy()
                _mg_with_date[mg_col] = pd.to_numeric(_mg_with_date[mg_col], errors="coerce")
                _mg_with_date = _mg_with_date.dropna().tail(5)
                margin_detail = [
                    (str(row["date"])[:10] if "date" in _mg_with_date.columns else "",
                     int(row[mg_col]))
                    for _, row in _mg_with_date.iterrows()
                ]
            else:
                margin_bal_5d  = 0.0
                margin_detail  = []
            if sh_col:
                sh_vals = pd.to_numeric(margin_source[sh_col], errors="coerce").dropna()
                if len(sh_vals) >= 1:
                    short_bal_now = float(sh_vals.iloc[-1])

        is_margin_decreasing = margin_bal_3d > 0 and margin_bal_now < margin_bal_3d

        # 資券比
        margin_short_ratio = (short_bal_now / margin_bal_now * 100)                              if margin_bal_now > 0 and short_bal_now > 0 else 0.0
        is_squeeze_potential = margin_short_ratio >= 25.0

        # ── 評分
        score = sum([is_institutional_swarm, is_squeeze_potential, is_margin_decreasing])

        result["facts"] = {
            "inst_net3":          round(inst_net3, 0),
            "inst_net3_detail":   inst_net3_detail if 'inst_net3_detail' in dir() else [],
            "margin_bal_now":     round(margin_bal_now, 0),
            "margin_detail":      margin_detail if 'margin_detail' in dir() else [],
            "margin_change_3d":   round((margin_bal_now - margin_bal_3d) / margin_bal_3d * 100, 1)
                                  if margin_bal_3d > 0 else 0.0,
            "margin_change_5d":   round((margin_bal_now - margin_bal_5d) / margin_bal_5d * 100, 1)
                                  if 'margin_bal_5d' in dir() and margin_bal_5d > 0 else 0.0,
            "margin_change_pct":  round((margin_bal_now - margin_bal_3d) / margin_bal_3d * 100, 1)
                                  if margin_bal_3d > 0 else 0.0,
            "margin_short_ratio": round(margin_short_ratio, 1),
            "is_swarm":           is_institutional_swarm,
            "is_squeeze":         is_squeeze_potential,
            "is_margin_exit":     is_margin_decreasing,
            "score":              score,
        }

        if is_institutional_swarm and (is_squeeze_potential or is_margin_decreasing):
            _squeeze_txt = f"資券比 {margin_short_ratio:.1f}%（軋空基因）、" if is_squeeze_potential else ""
            _margin_txt  = f"融資近3日退場" if is_margin_decreasing else ""
            result.update({
                "trigger": True,
                "score":   score,
                "msg": (
                    f"⚡ 短線火箭標的！三大法人近3日合計買超 {inst_net3:+,.0f} 張，"
                    f"{_squeeze_txt}{_margin_txt}。"
                    f"法人點火＋散戶退場，建議啟動 3-5 天短線閃擊！"
                )
            })
        else:
            result["score"] = score

    except Exception as _e:
        result["msg"] = f"掃描失敗：{_e}"

    return result

def scan_accumulation_phase(sid):
    """
    潛伏期法人暗中鎖碼雷達 V3
    完全使用 .iloc 位置索引 + str.strip() 強制清洗
    """
    result = {"alert": False, "type": "常規盤整", "msg": "", "facts": {}}
    try:
        sid = str(sid).strip()

        # ── K線（High/Low 箱體震幅）
        df_k, ok_k = load_price_csv(sid)
        if not ok_k or df_k.empty or len(df_k) < 22:
            return result
        df_k = add_indicators(df_k)
        r20   = df_k.iloc[-20:].copy()
        high20 = float(pd.to_numeric(r20["High"],  errors="coerce").max()) if "High" in r20.columns else float(pd.to_numeric(r20["Close"], errors="coerce").max())
        low20  = float(pd.to_numeric(r20["Low"],   errors="coerce").min()) if "Low"  in r20.columns else float(pd.to_numeric(r20["Close"], errors="coerce").min())
        box_amp   = (high20 - low20) / low20 * 100 if low20 > 0 else 0.0
        is_in_box = box_amp <= 15.0

        # ── 籌碼（投信，.iloc 位置回溯）
        df_c, ok_c = get_chips(sid)
        inst_5d = 0.0; inst_15d = 0.0; inst_streak = 0
        if ok_c and not df_c.empty:
            df_c["stock_id"] = df_c["stock_id"].astype(str).str.strip()
            name_col = next((c for c in ["name","institutional_investors"] if c in df_c.columns), None)
            if name_col and "net" in df_c.columns:
                trust = df_c[
                    (df_c["stock_id"] == sid) &
                    df_c[name_col].astype(str).str.contains("Investment_Trust", na=False)
                ].copy()
                trust["net"] = pd.to_numeric(trust["net"], errors="coerce").fillna(0)
                trust = trust.sort_values("date")
                # 按日加總後用 .iloc
                daily = trust.groupby("date")["net"].sum().reset_index().sort_values("date")
                daily["net"] = pd.to_numeric(daily["net"], errors="coerce").fillna(0)
                n = len(daily)
                # debug
                if sid == '2330':
                    st.session_state['_dbg_daily'] = daily.tail(5).to_dict('records')
                if n >= 5:
                    inst_5d  = float(daily["net"].iloc[max(0,n-5):].sum())
                if n >= 1:
                    inst_15d = float(daily["net"].iloc[max(0,n-15):].sum())
                # 單位自適應：絕對值 > 50000 推測為股，除以1000轉張
                if abs(inst_5d) > 50000:
                    inst_5d  /= 1000
                    inst_15d /= 1000
                # 連續買超天數（位置倒數，超過 8 天空洞視為中斷）
                inst_streak_start = ""
                for i in range(n-1, max(n-21,-1), -1):
                    if daily["net"].iloc[i] > 0:
                        # 檢查與前一筆的日期間隔
                        if i < n-1:
                            days_gap = (daily["date"].iloc[i+1] - daily["date"].iloc[i]).days
                            if days_gap > 8:  # 超過 8 天空洞（排除週末後約 5 個交易日）
                                break
                        inst_streak += 1
                        inst_streak_start = str(daily["date"].iloc[i])[:10]
                    else:
                        break
        inst_buying = inst_5d > 0 and inst_streak >= 3

        # ── 大戶持股（100% 對齊 Tab3：HoldingSharesLevel + percent）
        df_sh, ok_sh = get_shareholder(sid)
        big_pct = 0.0
        if ok_sh and not df_sh.empty:
            df_sh["stock_id"] = df_sh["stock_id"].astype(str).str.strip()
            sub = df_sh[df_sh["stock_id"] == sid].copy()
            if not sub.empty and "HoldingSharesLevel" in sub.columns and "percent" in sub.columns:
                sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
                sub["percent"] = pd.to_numeric(sub["percent"], errors="coerce").fillna(0)
                latest_date = sub["date"].max()
                sub_l = sub[sub["date"] == latest_date]
                # 排除合計行
                sub_l = sub_l[~sub_l["HoldingSharesLevel"].astype(str).str.contains("total|差異", na=False)]
                # 千張以上大戶
                big_kw = ["400,001","600,001","800,001","1,000,001","more than"]
                is_big = sub_l["HoldingSharesLevel"].astype(str).str.contains("|".join(big_kw), na=False)
                big_pct = float(sub_l[is_big]["percent"].sum())
        is_locked = big_pct >= 55.0

        conds = sum([is_in_box, inst_buying, is_locked])
        result["facts"] = {
            "box_amp":     round(box_amp, 1),
            "inst_5d":     int(inst_5d),
            "inst_15d":    int(inst_15d),
            "inst_streak": inst_streak,
            "inst_streak_start": inst_streak_start if 'inst_streak_start' in dir() else "",
            "big_pct":     round(big_pct, 1),
            "is_in_box":   is_in_box,
            "inst_buying": inst_buying,
            "is_locked":   is_locked,
            "conds":       conds,
        }

        if conds == 3:
            result.update({"alert": True, "type": "👑【潛伏期大戶暗中鎖碼】",
                "msg": (f"橫盤震幅僅 {box_amp:.1f}%，投信連買 {inst_streak} 天"
                        f"（近5日{inst_5d:+,.0f}張/15日{inst_15d:+,.0f}張），"
                        f"大戶持股 {big_pct:.1f}% 鎖倉！黃金第3階段，強烈狙擊！")})
        elif conds == 2:
            result.update({"type": "🟡 部分條件成立",
                "msg": f"箱體 {box_amp:.1f}%｜大戶 {big_pct:.1f}%｜投信連買 {inst_streak}天（近5日{inst_5d:+,.0f}張）"})

    except Exception as _e:
        result["msg"] = f"掃描失敗：{_e}"

    return result

# ══════════════════════════════════════════════════════════════
# ▌ 戰略儲備庫籌碼飽和自動除名機制
# ══════════════════════════════════════════════════════════════
def refresh_reserve_metabolism():
    """
    每次進入 Tab4 時執行：審查儲備庫個股籌碼健康度，
    自動剔除「投信飽和 / 大戶撤退 / 跌破季線」的失效精兵。
    回傳：(removed_list, kept_list)
    """
    reserve = st.session_state.get("reserve_list", [])
    if not reserve:
        return [], []

    kept    = []
    removed = []

    for item in reserve:
        sid  = item["id"]
        name = item.get("name", sid)
        try:
            # K線
            df_k, ok_k = load_price_csv(sid)
            if not ok_k or df_k.empty or len(df_k) < 62:
                kept.append(item)   # 無資料保留，不誤殺
                continue

            df_k  = add_indicators(df_k)
            close = float(df_k["Close"].iloc[-1])
            sma60 = float(df_k["SMA60"].iloc[-1]) if "SMA60" in df_k.columns else float("nan")

            # 事實3：跌破季線
            structure_broken = not np.isnan(sma60) and close < sma60

            # 大戶持股趨勢
            df_sh, ok_sh = get_shareholder(sid)
            large_retreat = False
            if ok_sh and not df_sh.empty and "holdingSharesPercent" in df_sh.columns:
                df_sh["holdingSharesPercent"] = pd.to_numeric(
                    df_sh["holdingSharesPercent"], errors="coerce")
                df_sh = df_sh.sort_values("date")
                if len(df_sh) >= 4:
                    pts = df_sh["holdingSharesPercent"].dropna().tail(4).tolist()
                    # 事實2：連續2週下滑（大戶撤退）
                    large_retreat = (pts[-1] < pts[-2]) and (pts[-2] < pts[-3])

            # 投信持股飽和（籌碼比例）
            df_c, ok_c = get_chips(sid)
            inst_saturated = False
            if ok_c and not df_c.empty:
                df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
                name_col = next((c for c in ["name","institutional_investors"]
                                 if c in df_c.columns), None)
                if name_col:
                    trust = df_c[df_c[name_col].astype(str).str.contains(
                        "Investment_Trust", na=False)]
                    if "net" in trust.columns and len(trust) >= 15:
                        trust["net"] = pd.to_numeric(trust["net"], errors="coerce").fillna(0)
                        # 15日累積投信賣超（負數=開始出貨）
                        inst_net15 = float(trust["net"].tail(15).sum())
                        # 事實1：投信轉為賣超（持倉已飽和出場）
                        inst_saturated = inst_net15 < -500  # 累積賣超>500張

            # 除名判定
            reason = None
            if structure_broken:
                reason = f"跌破季線（現價 {close:.1f} < SMA60 {sma60:.1f}）"
            elif large_retreat:
                reason = "千張大戶連續兩週減碼，籌碼鬆動"
            elif inst_saturated:
                reason = f"投信近15日累積賣超轉負，籌碼飽和出場"

            if reason:
                item["_remove_reason"] = reason
                removed.append(item)
            else:
                kept.append(item)

        except Exception as _e:
            kept.append(item)  # 出錯保留，不誤殺

    return removed, kept

# ══════════════════════════════════════════════════════════════
# ▌ VIX 恐慌指數即時抓取
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# ▌ 全市場融資水位（讀取 daily_scan.py 排程抓取的證交所彙總 JSON）
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner=False)
def get_total_margin_balance() -> dict:
    """
    讀取 daily_scan.py 每日排程抓取的全市場融資餘額彙總 JSON
    (data/margin_summary.json)，來源為證交所信用交易統計彙總表。
    回傳：{"balance": 億元, "date": 資料日期} 或 None
    """
    try:
        import os as _os, json as _j
        _path = _os.path.join("data", "margin_summary.json")
        if not _os.path.exists(_path):
            return None
        with open(_path, "r", encoding="utf-8") as _f:
            _d = _j.load(_f)
        return {
            "balance": float(_d.get("balance_yi", 0)),
            "date":    _d.get("date", "—"),
        }
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_vix():
    """
    從 Yahoo Finance 抓取 VIX 恐慌指數（^VIX）
    < 15：平靜；15~20：偏高警戒；20~30：市場緊張；> 30：極度恐慌
    """
    try:
        import yfinance as _yf
        t = _yf.Ticker("^VIX")
        hist = t.history(period="1d", interval="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except:
        pass
    return None


# ▌ 美國 PPI（生產者物價指數）年增率 — FRED 免金鑰 CSV 端點
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=86400, show_spinner=False)
def get_us_ppi():
    """
    從 FRED（美國聖路易聯邦準備銀行）取得 PPIACO（生產者物價指數）年增率。
    使用免金鑰的公開 CSV 端點，並加上 units=pc1 參數，直接取得 FRED
    官方計算的「Percent Change from Year Ago」，避免自行用月份位移計算
    時因資料缺月而對錯期、算出失真數值。每日快取一次（PPI為月頻資料）。

    回傳：PPI 年增率（%），失敗或數值超出合理範圍時回傳備援值 6.5。
    """
    try:
        import requests as _req, io as _io
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PPIACO&units=pc1"
        r = _req.get(url, timeout=10)
        if r.status_code == 200:
            df = pd.read_csv(_io.StringIO(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["value"]).sort_values("date")
            if not df.empty:
                latest = round(float(df["value"].iloc[-1]), 1)
                # 合理範圍檢查：PPI年增率正常落在 -10% ~ +20% 之間，
                # 超出此範圍視為資料異常，改用備援值
                if -10 <= latest <= 20:
                    return latest
    except:
        pass
    # 備援固定值：2026年5月美國 PPI 年增率約 6.5%（嚴重衝破安全線）
    return 6.5


# ══════════════════════════════════════════════════════════════
# ▌ 美股動態流動性與信用天網 — 6欄指標（Tab5 第三行）
# 100% 直接抓取 yfinance / FRED，零基本檔維護成本
# ══════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def get_fred_series_latest(series_id: str, fallback: float = None):
    """
    從 FRED 免金鑰 CSV 端點取得任一序列的最新一筆數值。
    通用函式，供 Fed淨流動性／TED利差／高收益債利差共用。
    網路防呆：超時或失敗時回傳 fallback 備援值，絕不讓系統崩潰。
    """
    try:
        import requests as _req, io as _io
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        r = _req.get(url, timeout=8)
        if r.status_code == 200:
            df = pd.read_csv(_io.StringIO(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["value"])
            if not df.empty:
                return float(df["value"].iloc[-1])
    except Exception:
        pass
    return fallback


@st.cache_data(ttl=3600, show_spinner=False)
def get_fed_net_liquidity():
    """
    Fed 淨流動性（兆美元）＝ Fed總資產(WALCL) − 財政部TGA帳戶(WTREGEN) − 隔夜逆回購(RRPONTSYD)
    此為市場資金面最關鍵的「真實流動性」指標，數值越高代表市場資金越寬鬆。
    三個FRED序列任一抓取失敗則回傳 None（前端顯示「資料載入中」，不顯示假數據）。
    """
    try:
        walcl = get_fred_series_latest("WALCL")       # Fed總資產（百萬美元）
        tga   = get_fred_series_latest("WTREGEN")     # 財政部一般帳戶（十億美元）
        rrp   = get_fred_series_latest("RRPONTSYD")   # 隔夜逆回購（十億美元）
        if walcl is None or tga is None or rrp is None:
            return None
        # 統一換算為「兆美元」：WALCL是百�萬美元，TGA/RRP是十億美元
        net_liq_trillion = (walcl / 1e6) - (tga / 1e3) - (rrp / 1e3)
        return net_liq_trillion
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_ted_spread():
    """泰德利差（TED Spread），反映銀行間信用風險，數值越高代表信用緊縮壓力越大"""
    return get_fred_series_latest("TEDRATE", fallback=None)


@st.cache_data(ttl=3600, show_spinner=False)
def get_high_yield_spread():
    """ICE美銀高收益債信用利差（BAMLH0A0HYM2），反映企業違約風險溢酬"""
    return get_fred_series_latest("BAMLH0A0HYM2", fallback=None)


@st.cache_data(ttl=1800, show_spinner=False)
def get_index_bias20(ticker: str):
    """
    動態計算任一美股指數的現價與20日均線(月線)乖離率。
    用於那斯達克100(^NDX)與費城半導體(^SOX)的高位/底部位階判定。

    回傳：{"price": 現價, "ma20": 20MA, "bias_20": 乖離率%} 或 None（抓取失敗）
    網路防呆：yfinance 逾時或無資料時回傳 None，前端顯示「載入中」不中斷系統。
    """
    try:
        import yfinance as _yf2
        hist = _yf2.Ticker(ticker).history(period="40d")
        if hist is None or hist.empty or len(hist) < 20:
            return None
        closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if len(closes) < 20:
            return None
        price = float(closes.iloc[-1])
        ma20  = float(closes.tail(20).mean())
        bias  = ((price - ma20) / ma20 * 100) if ma20 > 0 else 0.0
        return {"price": price, "ma20": ma20, "bias_20": bias}
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def get_dxy_index():
    """美元指數（DXY）當前點數，反映美元相對一籃子貨幣的強弱"""
    try:
        import yfinance as _yf3
        hist = _yf3.Ticker("DX-Y.NYB").history(period="2d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_macro_indicators():
    """
    抓取第二行所需總經指標：
    - CPI 年增率（抓 Yahoo Finance 的 CPIAUCSL 或 FinMind，失敗用固定值）
    - 布倫特原油（BZ=F）+ 中東杜拜油（使用 BZ=F 近似）
    - 美國 10 年期公債殖利率（^TNX）
    - 大盤月乖離（從本地 K 線計算）
    - 航運指數 SCFI（爬取 Freightos / 或返回快取）
    回傳 dict
    """
    import yfinance as _yf
    result = {
        "cpi":       None,   # 美國核心 CPI 年增率 %
        "brent":     None,   # 布倫特油價 USD
        "dubai":     None,   # 中東杜拜油價 USD（用 BZ=F 近似）
        "tnx":       None,   # 美國 10 年期公債殖利率 %
        "bias":      None,   # 加權指數月乖離 %
        "scfi":      None,   # SCFI 上海貨運指數
        "bdi":       None,   # BDI 波羅的海乾散裝指數
    }

    # ── 布倫特原油（BZ=F）
    try:
        brent = _yf.Ticker("BZ=F").history(period="2d")
        if not brent.empty:
            result["brent"] = round(float(brent["Close"].iloc[-1]), 1)
    except: pass

    # ── 中東杜拜油（Yahoo 無直接代號，用 CL=F WTI 近似，差約 $2-3）
    try:
        cl = _yf.Ticker("CL=F").history(period="2d")
        if not cl.empty:
            result["dubai"] = round(float(cl["Close"].iloc[-1]) - 2.0, 1)
    except: pass

    # ── 美國 10 年期公債殖利率（^TNX，Yahoo 回傳值已是 % 單位，例如 4.45）
    try:
        tnx = _yf.Ticker("^TNX").history(period="2d")
        if not tnx.empty:
            _raw = float(tnx["Close"].iloc[-1])
            # Yahoo ^TNX 回傳值：若 < 2 表示是小數格式需 × 100，否則直接用
            result["tnx"] = round(_raw if _raw > 2 else _raw * 100, 2)
    except: pass

    # ── 大盤月乖離：從本地 price_basic.csv 取加權指數
    try:
        import pandas as _pd, os as _os
        _twii = _yf.Ticker("^TWII").history(period="40d")
        if not _twii.empty and len(_twii) >= 20:
            _close = _twii["Close"]
            _ma20  = float(_close.rolling(20).mean().iloc[-1])
            _last  = float(_close.iloc[-1])
            result["bias"] = round((_last - _ma20) / _ma20 * 100, 2)
    except: pass

    # ── BDI 波羅的海乾散裝指數（Yahoo 無直接代號，用 BDRY ETF 近似或備援固定值）
    try:
        bdi = _yf.Ticker("BDRY").history(period="2d")
        if not bdi.empty:
            result["bdi"] = int(bdi["Close"].iloc[-1] * 100)  # BDRY ETF 轉換係數
    except: pass

    # ── CPI：讀本地 macro_events.json 的 latest_cpi，無值則用固定備援
    try:
        import json as _json, os as _os
        _cpi_path = _os.path.join("data", "macro_events.json")
        if _os.path.exists(_cpi_path):
            with open(_cpi_path, "r", encoding="utf-8") as _f:
                _meta = _json.load(_f)
            _cpi_val = _meta.get("latest_cpi")
            if _cpi_val:
                result["cpi"] = _cpi_val
        # 備援固定值（每月 Actions 更新，無法抓時顯示最後已知值）
        if result["cpi"] is None:
            result["cpi"] = 4.2  # 美國 5月 CPI 4.2%（2026-06-11 公布）
        # 同時讀 cpi_month
        result["cpi_month"] = _meta.get("cpi_month", "")
    except: pass

    return result


# ▌ 全市場站上季線(SMA60)比例 ＋ 大盤距歷史高點百分比
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner=False)
def get_market_breadth_sma60(sample_size: int = 40):
    """
    計算台股監控池中，當前站上季線（SMA60）的個股家數比例。

    效能說明：
    Streamlit Cloud 記憶體有限，逐檔讀取 K 線 CSV 成本高，故大幅縮減
    採樣數（預設40檔，取監控池前段大型權值股具市場代表性）作為全市場
    多空結構的近似指標，結果快取1小時。讀取後立即只保留收盤價序列，
    不持有整份 DataFrame，降低記憶體佔用。

    回傳：站上季線家數比例（%），資料不足時回傳 None
    """
    try:
        df_sl, ok_sl = load_csv("stock_list.csv")
        if not ok_sl or df_sl.empty or "stock_id" not in df_sl.columns:
            return None
        ids = df_sl["stock_id"].dropna().astype(str).unique().tolist()
        ids = [s for s in ids if s.isdigit() and len(s) == 4]
        ids = ids[:sample_size]

        above, total = 0, 0
        for sid in ids:
            df_p, ok_p = load_csv(f"prices/{sid}.csv")
            if not ok_p or df_p.empty:
                continue
            close_col = next((c for c in df_p.columns if c.lower() == "close"), None)
            if not close_col:
                continue
            closes = pd.to_numeric(df_p[close_col], errors="coerce").dropna()
            if len(closes) < 60:
                continue
            sma60 = closes.tail(60).mean()
            if closes.iloc[-1] > sma60:
                above += 1
            total += 1
            del df_p, closes  # 立即釋放，避免逐檔累積記憶體

        if total == 0:
            return None
        return round(above / total * 100, 1)
    except:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_twii_high_proximity():
    """
    計算加權指數目前收盤價與最近一年內歷史最高點的距離百分比。
    回傳：距高點百分比（正值，0 = 創新高，5 = 距高點5%）
    資料不足時回傳 None
    """
    try:
        import yfinance as _yf
        hist = _yf.Ticker("^TWII").history(period="1y")
        if hist.empty:
            return None
        high = float(hist["Close"].max())
        last = float(hist["Close"].iloc[-1])
        if high <= 0:
            return None
        return round((high - last) / high * 100, 2)
    except:
        return None


# ▌ 「利多不漲」排毒雷達 ＋ 龍頭股風向 — 前端輕量讀取模組
# ══════════════════════════════════════════════════════════════
# 重大架構調整：原本在前端即時爬新聞、算K線、算籌碼的重邏輯，
# 已全部移至獨立後端腳本 daily_scan.py（每日盤後17:00執行一次），
# 結果寫入 data/triggered_alerts.json。
# 前端在這裡只做「讀取最新結果」的輕量操作，
# 不再對外發送任何爬蟲/yfinance請求，徹底解決前端記憶體與延遲問題。
@st.cache_data(ttl=300, show_spinner=False)
def _load_triggered_alerts_data():
    """讀取 data/triggered_alerts.json 完整內容（本地→GitHub raw備援）。失敗回傳空結構。"""
    import json as _json, os as _os
    data = None
    _local = _os.path.join("data", "triggered_alerts.json")
    if _os.path.exists(_local):
        try:
            with open(_local, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            data = None
    if data is None:
        try:
            _r = requests.get(f"{GITHUB_RAW}/triggered_alerts.json", timeout=5)
            if _r.status_code == 200:
                data = _r.json()
        except Exception:
            data = None
    if not data:
        return {"alerts": [], "sector_breadth": {}}
    data.setdefault("alerts", [])
    data.setdefault("sector_breadth", {})
    return data


def get_triggered_alerts_today():
    """
    回傳「今日」的利多不漲觸發紀錄清單（list[dict]）。
    每筆包含 stock_id, name, news_score, shadow_pct, foreign_net, open, close 等欄位。
    無觸發或檔案不存在時回傳空列表（不報錯）。
    """
    from datetime import datetime as _dt
    today_str = _dt.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    alerts = _load_triggered_alerts_data().get("alerts", [])
    return [a for a in alerts if a.get("date") == today_str]


def get_sector_breadth():
    """
    回傳 daily_scan.py 最新一次計算的「龍頭股風向」統計：
    {"date":..,"above_sma20":int,"above_sma60":int,"total":int,"details":[...]}
    無資料時回傳空 dict（前端需自行判斷顯示「—」）。
    """
    return _load_triggered_alerts_data().get("sector_breadth", {})


# ▌ CBOE Put/Call Ratio 即時抓取
# ══════════════════════════════════════════════════════════════
def get_cboe_pc_ratio():
    """
    CBOE 個股期權 Put/Call Ratio（^PCALL）
    常態 0.7~0.9；<0.65 散戶極度做多；>1.05 散戶極度恐慌
    """
    try:
        import requests as _req
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EPCALL"
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = _req.get(url, headers=hdrs, timeout=5)
        data = r.json()
        val = data["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1]
        return round(float(val), 3) if val else 0.80
    except:
        return 0.80  # 預設常態中立值

# ══════════════════════════════════════════════════════════════
# ▌ 總經事件日曆（動態倒數計時）
# ══════════════════════════════════════════════════════════════
def get_macro_countdown():
    """
    回傳 (days, event_name) 距離最近一場總經核彈的倒數天數。
    優先讀 data/macro_events.json，備援讀 GitHub raw，
    最終備援使用硬編碼事件表。
    """
    import json as _json, os as _os
    from datetime import datetime as _dt, date as _date
    tz_tw = ZoneInfo("Asia/Taipei")
    today = _dt.now(tz_tw).date()

    # 嘗試讀本地 JSON
    events = []
    _local = _os.path.join("data", "macro_events.json")
    if _os.path.exists(_local):
        try:
            with open(_local, "r", encoding="utf-8") as f:
                events = _json.load(f).get("events", [])
        except Exception:
            pass
    # 備援：讀 GitHub raw
    if not events:
        try:
            import requests as _req
            _r = _req.get(f"{GITHUB_RAW}/macro_events.json", timeout=5)
            if _r.status_code == 200:
                events = _r.json().get("events", [])
        except Exception:
            pass
    # 最終備援：硬編碼
    if not events:
        events = [
            {"date": "2026-06-18", "event": "聯準會 FOMC 利率決策"},
            {"date": "2026-07-17", "event": "台積電 Q2 法說會"},
        ]
    # 找最近的未來事件
    nearest_days = 999
    nearest_name = "無近期事件"
    for ev in events:
        try:
            ev_date = _date.fromisoformat(ev["date"])
            delta = (ev_date - today).days
            if 0 <= delta < nearest_days:
                nearest_days = delta
                nearest_name = ev.get("event", "未知事件")
        except Exception:
            continue
    return nearest_days, nearest_name

# ══════════════════════════════════════════════════════════════
# ▌ 全系統統一最高權限斷路器
# ══════════════════════════════════════════════════════════════
def get_system_risk_status():
    """
    三軌聯鎖：大台外資 × 小台散戶 × CBOE P/C × 總經事件倒數
    回傳 (status, info_dict)
    status: RED_ALERT / YELLOW_ALERT / SHORT_SQUEEZE / GREEN_NORMAL
    """
    tx_net     = get_tx_foreign_position()
    mtx_retail = get_mtx_retail_position()
    pc_ratio   = get_cboe_pc_ratio()
    days, event = get_macro_countdown()

    is_tw_red  = mtx_retail >= 12000 and tx_net <= -30000
    is_us_red  = pc_ratio <= 0.65
    is_event   = days <= 3

    if (is_tw_red and is_us_red) or (is_tw_red and is_event):
        status = "RED_ALERT"
    elif mtx_retail >= 8000 and tx_net <= -15000:
        status = "YELLOW_ALERT"
    elif mtx_retail <= -15000 and pc_ratio >= 1.05:
        status = "SHORT_SQUEEZE"
    else:
        status = "GREEN_NORMAL"

    return status, {
        "tx_net": tx_net, "mtx_retail": mtx_retail,
        "pc_ratio": pc_ratio, "days": days, "event": event,
        "is_tw_red": is_tw_red, "is_us_red": is_us_red, "is_event": is_event,
    }

def get_dual_alert():
    """相容舊版呼叫，回傳 (level, tx_net, mtx_retail)"""
    status, info = get_system_risk_status()
    level = {"RED_ALERT":"red","YELLOW_ALERT":"yellow",
             "SHORT_SQUEEZE":"safe","GREEN_NORMAL":"safe"}.get(status, "safe")
    return level, info["tx_net"], info["mtx_retail"]

# ══════════════════════════════════════════════════════════════
# ▌ 大台外資淨留倉計算（全域共用）
# ══════════════════════════════════════════════════════════════
def get_tx_foreign_position():
    """外資大台淨留倉（正=多 / 負=空）"""
    try:
        df_fut, ok_fut = get_futures()
        if not ok_fut or df_fut.empty: return 0
        nm = next((c for c in ["name","institutional_investors"] if c in df_fut.columns), None)
        lc = next((c for c in df_fut.columns if "long_open_interest_balance" in c and "amount" not in c), None)
        sc = next((c for c in df_fut.columns if "short_open_interest_balance" in c and "amount" not in c), None)
        if not nm or not lc or not sc: return 0
        inst = df_fut[df_fut["source"]=="institutional"] if "source" in df_fut.columns else df_fut
        tx   = inst[inst["contract"]=="TX"] if "contract" in inst.columns else pd.DataFrame()
        if tx.empty: return 0
        ld  = tx["date"].max()
        row = tx[(tx["date"]==ld) & tx[nm].astype(str).str.contains("外資", na=False)]
        if not row.empty:
            lv = int(float(row[lc].values[0]))
            sv = int(float(row[sc].values[0]))
            return lv - sv
    except: pass
    return 0

# ▌ 外資台指期「結轉（轉倉）」追蹤
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def get_tx_rollover_info():
    """
    追蹤外資台指期淨未平倉口數的『結轉（轉倉）』變化。

    資料源限制說明：
    futures_data 僅提供當沖近月合約之三大法人淨未平倉彙總，並無區分
    「近月合約」與「次月合約」的個別倉位，故無法直接取得真正的
    逐筆轉倉明細。本函式以「外資淨未平倉口數的日對日變化」以及
    「近5個交易日累計變化」作為次月結轉空單堆積之代理觀察指標：
      - daily_change：今日相較昨日的淨未平倉變化（負值＝增加空單/減少多單）
      - cum_rollover：近5日累計變化（持續為負＝空單結轉堆積中）

    回傳 dict：{"daily_change": int, "cum_rollover": int}
    資料不足時回傳 {"daily_change": 0, "cum_rollover": 0}
    """
    try:
        df_fut, ok_fut = get_futures()
        if not ok_fut or df_fut.empty:
            return {"daily_change": 0, "cum_rollover": 0}

        nm = next((c for c in ["name","institutional_investors"] if c in df_fut.columns), None)
        lc = next((c for c in df_fut.columns if "long_open_interest_balance" in c and "amount" not in c), None)
        sc = next((c for c in df_fut.columns if "short_open_interest_balance" in c and "amount" not in c), None)
        if not nm or not lc or not sc:
            return {"daily_change": 0, "cum_rollover": 0}

        inst = df_fut[df_fut["source"]=="institutional"] if "source" in df_fut.columns else df_fut
        tx   = inst[inst["contract"]=="TX"] if "contract" in inst.columns else pd.DataFrame()
        foreign = tx[tx[nm].astype(str).str.contains("外資", na=False)].copy()
        if foreign.empty:
            return {"daily_change": 0, "cum_rollover": 0}

        foreign["net"] = (pd.to_numeric(foreign[lc], errors="coerce")
                          - pd.to_numeric(foreign[sc], errors="coerce"))
        foreign["date"] = pd.to_datetime(foreign["date"], errors="coerce")
        daily = (foreign.dropna(subset=["date","net"])
                        .groupby("date")["net"].last()
                        .reset_index()
                        .sort_values("date"))
        if len(daily) < 2:
            return {"daily_change": 0, "cum_rollover": 0}

        daily["chg"] = daily["net"].diff()
        _last_chg = daily["chg"].iloc[-1]
        daily_change = int(_last_chg) if not pd.isna(_last_chg) else 0
        # 近5日累計變化（持續為負 = 外資空單持續結轉堆積）
        cum_rollover = int(daily["chg"].tail(5).sum(skipna=True))
        return {"daily_change": daily_change, "cum_rollover": cum_rollover}
    except:
        return {"daily_change": 0, "cum_rollover": 0}

def get_dual_alert():
    """
    大小台雙軌聯鎖警戒判定
    回傳 (level, tx_net, mtx_retail)
    level: 'red' / 'yellow' / 'safe'
    """
    tx_net     = get_tx_foreign_position()
    mtx_retail = get_mtx_retail_position()
    if mtx_retail >= 12000 and tx_net <= -30000:
        return "red", tx_net, mtx_retail
    elif mtx_retail >= 8000 and tx_net <= -15000:
        return "yellow", tx_net, mtx_retail
    else:
        return "safe", tx_net, mtx_retail

# ══════════════════════════════════════════════════════════════
# ▌ 小台散戶淨留倉計算（全域共用）
# ══════════════════════════════════════════════════════════════
def get_mtx_retail_position():
    """
    回傳小台散戶淨留倉口數（最新一天）
    正數=散戶做多（危險）/ 負數=散戶放空（軋空機會）
    """
    try:
        df_fut, ok_fut = get_futures()
        if not ok_fut or df_fut.empty:
            return 0
        nm = next((c for c in ["name","institutional_investors"] if c in df_fut.columns), None)
        lc = next((c for c in df_fut.columns if "long_open_interest_balance" in c and "amount" not in c), None)
        sc = next((c for c in df_fut.columns if "short_open_interest_balance" in c and "amount" not in c), None)
        if not nm or not lc or not sc:
            return 0
        inst = df_fut[df_fut["source"]=="institutional"] if "source" in df_fut.columns else df_fut
        mtx  = inst[inst["contract"]=="MTX"] if "contract" in inst.columns else pd.DataFrame()
        if mtx.empty:
            return 0
        ld = mtx["date"].max()
        day = mtx[mtx["date"]==ld].copy()
        total_inst = 0
        for kw in ["自營","投信","外資"]:
            row = day[day[nm].astype(str).str.contains(kw, na=False)]
            if not row.empty:
                try:
                    total_inst += int(float(row[lc].values[0])) - int(float(row[sc].values[0]))
                except:
                    pass
        # 散戶 = 法人反向
        return -total_inst
    except:
        return 0


def get_mtx_retail_position_with_delta():
    """
    回傳小台散戶淨留倉「今日／昨日」雙日數值，供逐日增減判定使用。
    正數=散戶做多（危險）/ 負數=散戶放空（軋空機會）

    回傳：{"today": 今日散戶淨部位, "yesterday": 昨日散戶淨部位, "delta": 今日-昨日}
          任一資料缺失時對應欄位回傳 None。
    """
    result = {"today": None, "yesterday": None, "delta": None}
    try:
        df_fut, ok_fut = get_futures()
        if not ok_fut or df_fut.empty:
            return result
        nm = next((c for c in ["name","institutional_investors"] if c in df_fut.columns), None)
        lc = next((c for c in df_fut.columns if "long_open_interest_balance" in c and "amount" not in c), None)
        sc = next((c for c in df_fut.columns if "short_open_interest_balance" in c and "amount" not in c), None)
        if not nm or not lc or not sc:
            return result
        inst = df_fut[df_fut["source"]=="institutional"] if "source" in df_fut.columns else df_fut
        mtx  = inst[inst["contract"]=="MTX"] if "contract" in inst.columns else pd.DataFrame()
        if mtx.empty:
            return result

        # 取最近兩個交易日期
        _dates = sorted(mtx["date"].dropna().unique())
        if not _dates:
            return result

        def _calc_retail_for_date(target_date):
            day = mtx[mtx["date"] == target_date].copy()
            total_inst = 0
            for kw in ["自營","投信","外資"]:
                row = day[day[nm].astype(str).str.contains(kw, na=False)]
                if not row.empty:
                    try:
                        total_inst += int(float(row[lc].values[0])) - int(float(row[sc].values[0]))
                    except Exception:
                        pass
            return -total_inst  # 散戶 = 法人反向

        result["today"] = _calc_retail_for_date(_dates[-1])
        if len(_dates) >= 2:
            result["yesterday"] = _calc_retail_for_date(_dates[-2])
            result["delta"] = result["today"] - result["yesterday"]
        return result
    except Exception:
        return result

# ══════════════════════════════════════════════════════════════
# ▌ CSS 主題
# ══════════════════════════════════════════════════════════════
# ── 登入驗證
def check_login():
    """簡單登入驗證，不分大小寫"""
    if st.session_state.get("authenticated"):
        return True
    try:
        auth = st.secrets["auth"]
        valid_user = auth["username"].lower()
        valid_pass = auth["password"].lower()
    except Exception:
        return True  # 沒設定 secrets 就不擋

    st.markdown("""
    <div style='max-width:400px;margin:120px auto;padding:40px;
    background:#0d1826;border:1px solid #1e3a5f;border-radius:16px;text-align:center;'>
    <div style='font-size:2rem;margin-bottom:8px;'>📊</div>
    <div style='color:#00d4ff;font-size:1.3rem;font-weight:700;margin-bottom:4px;'>
    台股全週期量化系統 V4</div>
    <div style='color:#7fb3d3;font-size:.82rem;margin-bottom:24px;'>請登入以繼續</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("login_form"):
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
            username = st.text_input("帳號", placeholder="Username")
            password = st.text_input("密碼", type="password", placeholder="Password")
            submitted = st.form_submit_button("🔐 登入", use_container_width=True)

            if submitted:
                if username.lower() == valid_user and password.lower() == valid_pass:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("帳號或密碼錯誤")
    return False

if not check_login():
    st.stop()

# ── 財報季提醒
def check_fin_season():
    from datetime import date
    today = date.today()
    m = today.month
    # 財報公布月份：3月(Q4)、5月(Q1)、8月(Q2)、11月(Q3)
    fin_months = {3: "Q4 年報", 5: "Q1 季報", 8: "Q2 季報", 11: "Q3 季報"}
    if m in fin_months:
        days_left = (date(today.year, m+1 if m < 12 else 1, 1) - today).days
        return fin_months[m], days_left
    # 提前 2 週提醒
    next_months = {2: (3,"Q4 年報"), 4: (5,"Q1 季報"), 7: (8,"Q2 季報"), 10: (11,"Q3 季報")}
    if m in next_months:
        nm, label = next_months[m]
        next_date = date(today.year, nm, 1)
        days_left = (next_date - today).days
        if days_left <= 14:
            return f"{label}（即將）", days_left
    return None, 0

_fin_label, _fin_days = check_fin_season()
if _fin_label:
    st.warning(
        f"📅 **財報季提醒：{_fin_label}** 資料將陸續公布，建議手動更新財報與 ETF 配息資料（還有約 {_fin_days} 天）\n\n"
        f"**① 更新財報：**\n```\npython reset_fin.py\npython update_data.py --full-market --only financials --paid\n```\n\n"
        f"**② 更新 ETF 配息：**\n```\npython fetch_etf_dividends.py\ngit add data\\etf_dividend_data.csv\ngit commit -m \"ETF配息更新\"\ngit push origin main\n```",
        icon="⚠️"
    )

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;700&display=swap');

/* ── 全域 */
html,body,[class*="css"]{font-family:"Noto Sans TC",sans-serif;}
.stApp{background:#060b14;}
.block-container{background:#060b14;padding-top:1.2rem;}

/* ── Date picker 深色主題（避免白底看不到字）*/
[data-baseweb="calendar"]{background:#0f2027!important;border:1px solid #1e3a5f!important;border-radius:8px!important;}
[data-baseweb="calendar"] *{color:#e8f4fd!important;}
[data-baseweb="calendar"] [aria-selected="true"]{background:#00d4ff!important;color:#060b14!important;}
[data-baseweb="calendar"] button:hover{background:#1e3a5f!important;}
[data-baseweb="datepicker"] input{background:#0f2027!important;color:#e8f4fd!important;border-color:#1e3a5f!important;}
/* 月曆主體 */
div[data-baseweb="calendar"] div{background:#0f2027!important;}
div[data-baseweb="calendar"] table{background:#0f2027!important;}
div[data-baseweb="calendar"] td{background:#0f2027!important;color:#e8f4fd!important;}
div[data-baseweb="calendar"] th{background:#0a1628!important;color:#7fb3d3!important;}
div[data-baseweb="calendar"] button{background:#0f2027!important;color:#e8f4fd!important;border:none!important;}
div[data-baseweb="calendar"] button:hover{background:#1e3a5f!important;}
/* 月份年份選擇列 */
div[data-baseweb="select"] div{background:#0f2027!important;color:#e8f4fd!important;}
div[data-baseweb="popover"]{background:#0f2027!important;border:1px solid #1e3a5f!important;}


[data-testid="stSidebar"]{background:#080e1a!important;}
[data-testid="stSidebar"]>div{background:#080e1a!important;}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label{color:#ddeeff!important;}

/* ── 卡片 */
.metric-card{
    background:linear-gradient(135deg,#0f2027,#162535);
    border:1px solid #1e3a5f;border-radius:10px;
    padding:13px 14px;text-align:center;transition:transform .2s;
}
.metric-card:hover{transform:translateY(-2px);}
.metric-label{color:#7fb3d3;font-size:.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px;}
.metric-value{color:#e8f4fd;font-size:1.15rem;font-weight:700;font-family:"JetBrains Mono",monospace;}
.metric-value.up{color:#ff5252;}.metric-value.down{color:#00e676;}

/* ── 信號燈 */
.sig-green{background:linear-gradient(135deg,#0a3d0a,#0f5c0f);border:1px solid #00e676;border-radius:8px;padding:12px;color:#00e676;font-weight:600;text-align:center;}
.sig-red  {background:linear-gradient(135deg,#3d0a0a,#5c0f0f);border:1px solid #ff5252;border-radius:8px;padding:12px;color:#ff5252;font-weight:600;text-align:center;animation:pulse 2s infinite;}
.sig-warn {background:linear-gradient(90deg,#2d1b00,#3d2500);border:1px solid #ffab40;border-left:4px solid #ffab40;border-radius:8px;padding:10px 14px;color:#ffab40;font-weight:600;margin:6px 0;}

/* ── 區塊標題 */
.sec-title{color:#00d4ff;font-size:.88rem;font-weight:600;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #1e3a5f;padding-bottom:7px;margin:14px 0 10px;}

/* ── Tab */
.stTabs [data-baseweb="tab-list"]{gap:4px;background:#0d1321;padding:4px;border-radius:10px;}
.stTabs [data-baseweb="tab"]{color:#7fb3d3;background:transparent;border-radius:8px;font-size:.84rem;padding:7px 16px;}
.stTabs [aria-selected="true"]{color:#00d4ff!important;background:linear-gradient(135deg,#0f2027,#162535)!important;border-bottom:2px solid #00d4ff!important;}

/* ── Expander */
div[data-testid="stExpander"]{background:#0d1826;border:1px solid rgba(255,255,255,.07);border-radius:8px;}
/* Expander header hover 去掉反白 */
div[data-testid="stExpander"] summary{background:transparent!important;background-color:transparent!important;}
div[data-testid="stExpander"] summary:hover{background:transparent!important;background-color:transparent!important;}
div[data-testid="stExpander"] summary:focus{background:transparent!important;outline:none!important;}
div[data-testid="stExpander"] details summary{background:transparent!important;}
div[data-testid="stExpander"] details summary:hover{background:transparent!important;}
[data-testid="stExpanderToggleIcon"]{color:#7fb3d3!important;}

/* ── 按鈕 */
.stButton>button{
    color:#ffffff!important;
    font-weight:600!important;
    font-size:0.88rem!important;
    background:linear-gradient(135deg,#162535,#1e3a5f)!important;
    border:1px solid #2a5080!important;
}
.stButton>button[kind="primary"]{
    background:linear-gradient(135deg,#0066cc,#0044aa)!important;
    border:1px solid #00d4ff!important;
    color:#ffffff!important;
}
.stButton>button:hover{
    border-color:#00d4ff!important;
    color:#ffffff!important;
}
section[data-testid="stSidebar"] .stButton>button{
    color:#ffffff!important;
    background:linear-gradient(135deg,#162535,#1e3a5f)!important;
    border:1px solid #2a5080!important;
    font-weight:600!important;
}
section[data-testid="stSidebar"] .stButton>button:hover{
    border-color:#00d4ff!important;
    color:#ffffff!important;
}
section[data-testid="stSidebar"] .stButton>button p{
    color:#ffffff!important;
}
section[data-testid="stSidebar"] button[data-testid="baseButton-secondary"]{
    color:#ffffff!important;
    background:linear-gradient(135deg,#162535,#1e3a5f)!important;
    border:1px solid #2a5080!important;
}
section[data-testid="stSidebar"] button[data-testid="baseButton-secondary"] p{
    color:#ffffff!important;
}
section[data-testid="stSidebar"] button[kind="secondary"],
section[data-testid="stSidebar"] button[kind="secondary"] p,
section[data-testid="stSidebar"] .stButton button,
section[data-testid="stSidebar"] .stButton button p,
section[data-testid="stSidebar"] .stButton button span{
    color:#e8f4fd!important;
    background:linear-gradient(135deg,#162535,#1e3a5f)!important;
}
/* AI題材個股按鈕 */
button[kind="secondary"] p, button[kind="secondary"] span{
    color:#e8f4fd!important;
}
/* 所有文字強制可見 */
p, span, label, div, h1, h2, h3, li {
    color:#ddeeff;
}
.stSelectbox label, .stMultiSelect label,
.stSlider label, .stRadio label,
.stNumberInput label, .stTextInput label {
    color:#b0cce0!important;
}
/* ══ DataFrame 黑底白字 ══ */
[data-testid="stDataFrame"] td,
[data-testid="stDataFrame"] th {
    color:#ffffff!important;
    background-color:#0a0f1a!important;
}
[data-testid="stDataFrame"] tr:hover td{
    background-color:#1e3a5f!important;
}
/* header */
[data-testid="stDataFrame"] th{
    background-color:#0d1826!important;
    color:#00d4ff!important;
    border-bottom:1px solid #1e3a5f!important;
}
/* Show/hide columns 浮動視窗 */
[data-testid="stDataFrameResizable"] [data-testid="stElementToolbar"]{
    background:#0d1826!important;
    border:1px solid #1e3a5f!important;
}
[data-testid="stElementToolbar"] button{
    color:#e8f4fd!important;
    background:#0d1826!important;
}
[data-testid="stElementToolbar"] button:hover{
    background:#1e3a5f!important;
    color:#00d4ff!important;
}
/* 欄位選擇浮動視窗 */
[data-testid="stDataFrameColumnConfigContainer"]{
    background:#0d1826!important;
    border:1px solid #1e3a5f!important;
    color:#e8f4fd!important;
}

/* ── 評分徽章 */
.badge-green{display:inline-block;background:rgba(0,230,118,.12);border:1px solid #00e676;color:#00e676;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}
.badge-red  {display:inline-block;background:rgba(255,82,82,.12);border:1px solid #ff5252;color:#ff5252;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}
.badge-gray {display:inline-block;background:rgba(84,110,122,.2);border:1px solid #546e7a;color:#7fb3d3;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}

/* ── Infobox */
.infobox{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:.78rem;color:#7fb3d3;line-height:1.6;}

@keyframes pulse{0%{box-shadow:0 0 8px rgba(255,82,82,.2);}50%{box-shadow:0 0 18px rgba(255,82,82,.5);}100%{box-shadow:0 0 8px rgba(255,82,82,.2);}}

/* ══ Selectbox / Dropdown ══ */
/* 選單本體：黑底白字，白框 */
[data-baseweb="select"]>div{
    background:#0a0f1a!important;
    color:#ffffff!important;
    border:1px solid #ffffff!important;
    border-radius:6px!important;
}
[data-baseweb="select"] span{color:#ffffff!important;}
[data-baseweb="select"] input{color:#ffffff!important;background:transparent!important;}
[data-baseweb="select"] svg{color:#ffffff!important;fill:#ffffff!important;}
/* 下拉清單：黑底白字 */
[data-baseweb="popover"]{background:#0a0f1a!important;border:1px solid #3a5a80!important;}
[data-baseweb="popover"] li{
    background:#0a0f1a!important;
    color:#ffffff!important;
}
/* hover：藍框高亮 */
[data-baseweb="popover"] li:hover{
    background:#1e3a5f!important;
    color:#ffffff!important;
    outline:1px solid #00d4ff!important;
}
/* 已選中項目：藍框，強制覆蓋所有反白 */
[data-baseweb="popover"] [aria-selected="true"],
[data-baseweb="popover"] [aria-selected="true"]:hover,
[data-baseweb="menu"] [aria-selected="true"],
[data-baseweb="option"][aria-selected="true"],
[role="option"][aria-selected="true"],
li[aria-selected="true"]{
    background:#0f2a45!important;
    color:#00d4ff!important;
    border-left:3px solid #00d4ff!important;
}
/* 強制去掉瀏覽器預設反白（藍底） */
[data-baseweb="popover"] *::selection{background:transparent!important;}
[data-baseweb="popover"] li *{-webkit-user-select:none;user-select:none;}
/* 所有 option 預設黑底 */
[data-baseweb="option"],
[role="option"],
[data-baseweb="menu"] li,
[data-baseweb="menu"] ul li{
    background:#0a0f1a!important;
    color:#ffffff!important;
}
[data-baseweb="menu"]{background:#0a0f1a!important;color:#ffffff!important;}
[data-baseweb="tag"]{background:#1e3a5f!important;border:1px solid #00d4ff!important;}
[data-baseweb="tag"] span{color:#ffffff!important;}
/* Radio ══ 也順便讓選單容器背景透明 */
/* ══ Radio ══ */
[data-testid="stRadio"] label p{color:#e8f4fd!important;}
[data-testid="stRadio"] label,
[data-testid="stRadio"] label:hover,
[data-testid="stRadio"] label:focus,
[data-testid="stRadio"] label:active,
[data-testid="stRadio"] label:focus-within{
    background:transparent!important;
    background-color:transparent!important;
}
[data-testid="stRadio"] label > div,
[data-testid="stRadio"] label > div:hover,
[data-testid="stRadio"] label > div:focus{
    background:transparent!important;
    background-color:transparent!important;
}
[data-testid="stRadio"] label > div > div,
[data-testid="stRadio"] label > div > div:hover{
    background:transparent!important;
    background-color:transparent!important;
}
/* 選中：底線+藍字，不用背景色 */
[data-testid="stRadio"] label:has(input[type=radio]:checked) p{
    color:#00d4ff!important;
    font-weight:600!important;
    text-decoration:underline;
    text-underline-offset:3px;
}
[data-testid="stRadio"] label:has(input[type=radio]:checked) span{color:#00d4ff!important;}
/* ══ Number / Text input ══ */
input[type="number"],input[type="text"],textarea{
    color:#e8f4fd!important;background:#0f1e30!important;border-color:#1e3a5f!important;}
/* ══ Slider ══ */
[data-testid="stSlider"] p{color:#c8dff0!important;}
/* ══ DataTable text ══ */
/* ══ Multiselect ══ */
[data-testid="stMultiSelect"] span{color:#e8f4fd!important;}
[data-testid="stMultiSelect"] input{color:#e8f4fd!important;}
/* ══ All labels ══ */
label{color:#c8dff0!important;}
label p{color:#c8dff0!important;}
/* ══ 隱藏 Streamlit 頂部白色 header/toolbar ══ */
[data-testid="stHeader"]{background:transparent!important;height:0!important;}
[data-testid="stToolbar"]{display:none!important;}
[data-testid="stDecoration"]{display:none!important;}
[data-testid="stStatusWidget"]{display:none!important;}
header[data-testid="stHeader"]{visibility:hidden!important;}
/* ══ Download button ══ */
[data-testid="stDownloadButton"] button{
    background:#0d1826!important;
    color:#e8f4fd!important;
    border:1px solid #1e3a5f!important;
}
[data-testid="stDownloadButton"] button:hover{
    background:#1e3a5f!important;
    color:#00d4ff!important;
    border-color:#00d4ff!important;
}
</style>
""", unsafe_allow_html=True)

# ── JS 強制移除 Radio 反白背景
st.markdown("""
<script>
function fixRadio() {
    document.querySelectorAll('[data-testid="stRadio"] label').forEach(label => {
        label.style.setProperty('background', 'transparent', 'important');
        label.style.setProperty('background-color', 'transparent', 'important');
        label.querySelectorAll('div').forEach(d => {
            d.style.setProperty('background', 'transparent', 'important');
            d.style.setProperty('background-color', 'transparent', 'important');
        });
    });
}
const observer = new MutationObserver(fixRadio);
observer.observe(document.body, { childList: true, subtree: true });
fixRadio();
setInterval(fixRadio, 500);
</script>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# ▌ 繪圖通用參數
# ══════════════════════════════════════════════════════════════
PLOT_BG   = "rgba(10,14,26,0)"
GRID_COL  = "#1e3a5f"
TEXT_COL  = "#7fb3d3"

def base_layout(title="", height=400, **kw):
    return dict(
        title      = dict(text=title, font=dict(color="#e8f4fd", size=13)),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PLOT_BG,
        font       = dict(color=TEXT_COL, family="JetBrains Mono,Noto Sans TC", size=11),
        height=height, margin=dict(l=46, r=18, t=44, b=34),
        xaxis=dict(gridcolor=GRID_COL, linecolor=GRID_COL, showgrid=True),
        yaxis=dict(gridcolor=GRID_COL, linecolor=GRID_COL, showgrid=True),
        legend=dict(bgcolor="rgba(10,14,26,.85)", bordercolor=GRID_COL, borderwidth=1),
        **kw,
    )

def mcard(col, label, val, cls=""):
    col.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>{label}</div>"
        f"<div class='metric-value {cls}'>{val}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

def badge(ok: bool, text: str) -> str:
    cls = "badge-green" if ok else "badge-red"
    icon = "✅" if ok else "❌"
    return f"<span class='{cls}'>{icon} {text}</span>"

# ══════════════════════════════════════════════════════════════
# ▌ 資料讀取（帶快取）
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def load_csv(filename: str) -> tuple[pd.DataFrame, bool]:
    """從 GitHub raw CSV 讀取，失敗時嘗試本地 data/ 目錄"""
    import os
    # 優先本地 CSV（本機開發 / 同倉庫部署）
    local = os.path.join("data", filename)
    if os.path.exists(local):
        try:
            df = pd.read_csv(local, dtype=str)
            return df, True
        except:
            pass
    # 備援：GitHub raw
    url = f"{GITHUB_RAW}/{filename}"
    try:
        df = pd.read_csv(url, dtype=str)
        return df, True
    except:
        return pd.DataFrame(), False

@st.cache_data(ttl=3600, show_spinner=False)
def load_json_meta() -> dict:
    import os, json
    local = os.path.join("data", "last_update.json")
    if os.path.exists(local):
        try:
            return json.loads(open(local).read())
        except:
            pass
    try:
        r = requests.get(f"{GITHUB_RAW}/last_update.json", timeout=10)
        return r.json()
    except:
        return {}

@st.cache_data(ttl=60, show_spinner=False)
def load_price_csv(stock_id: str) -> tuple[pd.DataFrame, bool]:
    """讀取個股 K 線 CSV：優先本地→GitHub→yfinance 即時抓取"""
    import os

    def _parse(df):
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).set_index("date").sort_index()
        for c in ["Open","High","Low","Close","Volume"]:
            matches = [x for x in df.columns if x.lower() == c.lower()]
            if matches:
                df[c] = pd.to_numeric(df[matches[0]], errors="coerce")
        df = df.dropna(subset=["Close"])
        return df

    # 優先：本地 CSV（本機開發用）
    local = os.path.join("data", "prices", f"{stock_id}.csv")
    if os.path.exists(local):
        try:
            df = _parse(pd.read_csv(local))
            if len(df) >= 10:
                # 檢查是否太舊（超過 5 天沒更新），若太舊繼續往下 fallback
                latest = df.index[-1]
                if (pd.Timestamp.now() - latest).days <= 5:
                    return df, True
        except:
            pass

    # 備援：GitHub raw URL（Streamlit Cloud 用）
    url = f"{GITHUB_RAW}/prices/{stock_id}.csv"
    try:
        df = _parse(pd.read_csv(url))
        if len(df) >= 10:
            latest = df.index[-1]
            if (pd.Timestamp.now() - latest).days <= 5:
                return df, True
    except:
        pass

    # 最終備援：yfinance 即時抓取（新加入股票或 CSV 過舊時使用）
    # 結果存入 session_state 快取，避免重複抓取
    cache_key = f"_yf_price_{stock_id}"
    if cache_key in st.session_state:
        cached = st.session_state[cache_key]
        if cached is not None and len(cached) >= 10:
            return cached, True

    try:
        import yfinance as _yf
        # 判斷上市(.TW)或上櫃(.TWO)：先讀 stock_list.csv
        suffix = ".TW"
        try:
            _sl_df, _sl_ok = load_csv("stock_list.csv")
            if _sl_ok and not _sl_df.empty:
                _sl_df["stock_id"] = _sl_df["stock_id"].astype(str)
                _row = _sl_df[_sl_df["stock_id"] == str(stock_id)]
                if not _row.empty and _row.iloc[0].get("type") == "tpex":
                    suffix = ".TWO"
        except:
            pass

        ticker = f"{stock_id}{suffix}"
        hist = _yf.Ticker(ticker).history(period="365d")
        if hist.empty and suffix == ".TW":
            # 嘗試 .TWO
            hist = _yf.Ticker(f"{stock_id}.TWO").history(period="365d")
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index.tz_localize(None) if hist.index.tz is not None else hist.index)
            for c in ["Open","High","Low","Close","Volume"]:
                if c in hist.columns:
                    hist[c] = pd.to_numeric(hist[c], errors="coerce")
            hist = hist.dropna(subset=["Close"])
            if len(hist) >= 10:
                st.session_state[cache_key] = hist
                return hist, True
    except:
        pass

    st.session_state[cache_key] = None
    return pd.DataFrame(), False

# ── 衍生載入函式
def get_stock_info():
    df, ok = load_csv("stock_info.csv")
    if ok and not df.empty:
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["stock_name"] = df["stock_name"].astype(str).str.strip()
        # 優先取有中文名稱的（stock_name != stock_id），再取第一筆
        has_name = df[df["stock_name"] != df["stock_id"]]
        no_name  = df[df["stock_name"] == df["stock_id"]]
        combined = pd.concat([has_name, no_name]).drop_duplicates("stock_id")
        return combined[["stock_id","stock_name"]].reset_index(drop=True), True
    return pd.DataFrame(), False


def fetch_live_price(sid):
    """用 yfinance 抓取個股即時（延遲15分鐘）資料"""
    try:
        import yfinance as yf
        ticker = sid + ".TW"
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if df.empty:
            ticker = sid + ".TWO"  # 上櫃
            df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if df.empty:
            return None
        latest = df.iloc[-1]
        return {
            "close":  round(float(latest["Close"]), 2),
            "high":   round(float(latest["High"]),  2),
            "low":    round(float(latest["Low"]),   2),
            "volume": int(latest["Volume"]),
            "time":   df.index[-1].strftime("%H:%M"),
        }
    except Exception:
        return None

def is_trading_time():
    """判斷是否在台灣交易時段 09:00~13:30"""
    try:
        tz_tw = ZoneInfo("Asia/Taipei")
        now_tw = datetime.now(tz_tw)
        t = now_tw.time()
        from datetime import time as dtime
        return dtime(9, 0) <= t <= dtime(13, 30) and now_tw.weekday() < 5
    except Exception:
        return False

def should_auto_refresh():
    """判斷是否需要自動刷新（固定時間點模式）
    時間表：9:18、9:20、每整10分鐘（9:30/9:40...13:20）
    """
    if not is_trading_time():
        return False
    try:
        tz_tw = ZoneInfo("Asia/Taipei")
        now_tw = datetime.now(tz_tw)
        t = now_tw.time()
        from datetime import time as dtime
        h, m = t.hour, t.minute

        # 固定更新時間點
        update_times = {(9,18), (9,20)}
        for hh in range(9, 14):
            for mm in [0, 10, 20, 30, 40, 50]:
                if (hh, mm) >= (9, 30) and (hh, mm) <= (13, 20):
                    update_times.add((hh, mm))

        # 判斷目前分鐘是否為更新時間點（在該分鐘的前30秒內觸發）
        if (h, m) not in update_times:
            return False
        if t.second > 30:
            return False

        # 避免同一分鐘重複觸發
        last = st.session_state.get("last_auto_refresh")
        if last:
            tz_tw2 = ZoneInfo("Asia/Taipei")
            last_tw = last.astimezone(tz_tw2) if hasattr(last, 'astimezone') else last
            if last_tw.hour == h and last_tw.minute == m:
                return False
        return True
    except Exception:
        return False

def refresh_all_live_prices():
    """抓取所有監控標的即時報價"""
    import time as _time
    all_wl = st.session_state.get("watchlist", []) + st.session_state.get("watchlist_scan", [])
    seen = set()
    for w in all_wl:
        sid = w["id"]
        if sid in seen:
            continue
        seen.add(sid)
        data = fetch_live_price(sid)
        if data:
            st.session_state.live_prices[sid] = data
        _time.sleep(0.3)  # 避免 yfinance 限速
    st.session_state.last_auto_refresh = datetime.now(ZoneInfo("Asia/Taipei"))

def df_to_html(df, height=380, font_size=".82rem"):
    """把 DataFrame 渲染成黑底白字的 HTML 表格"""
    TD = f"padding:6px 10px;border-bottom:1px solid #1a2a3a;white-space:nowrap;color:#e8f4fd;font-size:{font_size};"
    TH = f"padding:6px 10px;background:#0d1826;color:#00d4ff;font-size:{font_size};white-space:nowrap;border-bottom:2px solid #1e3a5f;"
    rows_html = ""
    for i, (_, row) in enumerate(df.iterrows()):
        bg = "#060b14" if i % 2 == 0 else "#080e18"
        cells = ""
        for col in df.columns:
            val = row[col]
            s = TD + f"background:{bg};"
            if val == "✅":
                cells += f"<td style='{s}text-align:center;font-size:1.1rem;'>✅</td>"
            elif val == "❌":
                cells += f"<td style='{s}text-align:center;font-size:1.1rem;color:#ff5252;'>❌</td>"
            elif val is None or (isinstance(val, float) and pd.isna(val)):
                cells += f"<td style='{s}color:#546e7a;text-align:center;'>—</td>"
            else:
                cells += f"<td style='{s}'>{val}</td>"
        rows_html += f"<tr>{cells}</tr>"
    headers = "".join(f"<th style='{TH}'>{c}</th>" for c in df.columns)
    return (
        f"<div style='overflow-x:auto;overflow-y:auto;max-height:{height}px;"
        f"border:1px solid #1e3a5f;border-radius:8px;background:#060b14;'>"
        f"<table style='width:100%;border-collapse:collapse;'>"
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        f"</table></div>"
    )

# ── Render 中繼站 URL（台灣即時籌碼）
RELAY_URL = "https://taiwan-chips-relay.onrender.com"

def _ping_relay():
    """背景 ping Render 防止睡眠（每10分鐘）"""
    import threading, time
    def _keep_alive():
        while True:
            try:
                import requests as _req
                _req.get(f"{RELAY_URL}/health", timeout=5, verify=False)
            except:
                pass
            time.sleep(600)  # 每10分鐘ping一次
    t = threading.Thread(target=_keep_alive, daemon=True)
    t.start()

_ping_relay()

CF_KV_URL = "https://taiwan-stock-kv.rex64-lee.workers.dev"

@st.cache_data(ttl=300)
def _load_relay_chips():
    """從 Cloudflare KV 讀取即時籌碼（快取5分鐘）"""
    try:
        import requests as _req
        r = _req.get(f"{CF_KV_URL}/get?key=chips_data", timeout=10)
        st.session_state["relay_debug"] = f"status={r.status_code} len={len(r.content)}"
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            if not df.empty:
                if "buy" in df.columns:
                    st.session_state["relay_debug"] = "舊格式，等今日更新"
                    return pd.DataFrame()
                df["stock_id"] = df["stock_id"].astype(str).str.strip()
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df["net"] = pd.to_numeric(df["net"], errors="coerce")
                df["net"] = df["net"] / 1000
                st.session_state["relay_debug"] += f" rows={len(df)} max_date={df['date'].max()}"
                return df
    except Exception as e:
        st.session_state["relay_debug"] = f"ERROR: {e}"
    return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def _get_chips_merged_all():
    """
    取得「全市場合併後」的籌碼+融資資料（GitHub歷史 + Render即時 + margin.csv）。
    這是 get_chips() 內部最耗資源的合併/去重步驟，加上5分鐘快取後，
    無論呼叫 get_chips(stock_id) 多少次（例如掃描整個儲備庫），
    這個合併運算只會在快取過期後執行一次，大幅降低記憶體與CPU開銷。
    """
    # 讀 GitHub CSV 歷史資料
    df_csv, ok_csv = load_csv("chips_data.csv")
    if ok_csv and not df_csv.empty:
        df_csv["stock_id"] = df_csv["stock_id"].astype(str).str.strip()
        if "date" in df_csv.columns:
            df_csv["date"] = pd.to_datetime(df_csv["date"], errors="coerce")
        # 統一只保留需要的欄位（相容新舊格式）
        _keep = [c for c in ["date","stock_id","name","net","source"] if c in df_csv.columns]
        df_csv = df_csv[_keep].copy()
        if "net" in df_csv.columns:
            df_csv["net"] = pd.to_numeric(df_csv["net"], errors="coerce")
        if "source" not in df_csv.columns:
            df_csv["source"] = "institutional"
        df_csv = df_csv.dropna(subset=["name","net"])
    else:
        df_csv = pd.DataFrame()

    # 歷史 CSV 的 net 是股單位，需除以 1000 轉張
    if not df_csv.empty and "net" in df_csv.columns:
        if df_csv["net"].abs().max() > 50000:  # 判斷是股單位
            df_csv["net"] = df_csv["net"] / 1000

    # 讀 Render 即時資料（今天）
    df_relay = _load_relay_chips()

    # 合併：Render 今天 + GitHub 歷史（按 date+stock_id+name 去重，保留最新）
    if not df_relay.empty and not df_csv.empty:
        # 過濾 net=0 的無意義資料（避免影響連買計算）
        df_relay_clean = df_relay[df_relay["net"].abs() > 0] if "net" in df_relay.columns else df_relay
        df = pd.concat([df_csv, df_relay_clean], ignore_index=True)
        # 去重：同一天同一股票同一法人，保留最後一筆（Render 的資料優先）
        df = df.drop_duplicates(subset=["date","stock_id","name"], keep="last")
    elif not df_relay.empty:
        df = df_relay.copy()
    elif not df_csv.empty:
        df = df_csv.copy()
    else:
        return pd.DataFrame()

    # ── 整合融資資料（margin.csv）
    df_margin, ok_margin = load_csv("margin.csv")
    if ok_margin and not df_margin.empty:
        df_margin["stock_id"] = df_margin["stock_id"].astype(str).str.strip()
        if "date" in df_margin.columns:
            df_margin["date"] = pd.to_datetime(df_margin["date"], errors="coerce")
        df_margin["source"] = "margin"
        df_margin["name"]   = "margin"
        df_margin["net"]    = pd.to_numeric(
            df_margin.get("MarginPurchaseTodayBalance", pd.Series()), errors="coerce"
        )
        _mg_keep = [c for c in ["date","stock_id","name","net","source",
                                 "MarginPurchaseTodayBalance","MarginPurchaseYesterdayBalance",
                                 "ShortSaleTodayBalance","ShortSaleYesterdayBalance"]
                    if c in df_margin.columns]
        df_margin = df_margin[_mg_keep].dropna(subset=["net"])
        df = pd.concat([df, df_margin], ignore_index=True)

    return df.sort_values("date") if "date" in df.columns else df


def get_chips(stock_id=None):
    """從快取的全市場合併結果中，依股票代號篩選（輕量操作）"""
    df = _get_chips_merged_all()
    if df.empty:
        return pd.DataFrame(), False
    if stock_id:
        df = df[df["stock_id"] == str(stock_id).strip()]
    return df, True

def get_financials(stock_id=None):
    df, ok = load_csv("financial_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    # ★ stock_id 強制轉字串（CSV 讀進來可能是 int64）
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    if stock_id:
        df = df[df["stock_id"] == str(stock_id).strip()]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.sort_values("date") if "date" in df.columns else df, True

def get_price_basic(stock_id=None):
    """讀取 price_basic.csv（yfinance，含毛利率/PE/EPS）"""
    df, ok = load_csv("price_basic.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    if stock_id:
        df = df[df["stock_id"] == str(stock_id).strip()]
    return df, True

@st.cache_data(ttl=300)
def _load_relay_futures():
    """從 Cloudflare KV 讀取即時期貨資料（快取5分鐘）"""
    try:
        import requests as _req
        r = _req.get(f"{CF_KV_URL}/get?key=futures_data", timeout=10)
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                # 只接受最近 7 天內的資料
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
                if df["date"].max() < cutoff:
                    return pd.DataFrame()
                # 只轉換數值欄位，保留文字欄位（contract、institutional_investors）
                num_cols = ["long_deal_volume","long_deal_amount","short_deal_volume","short_deal_amount",
                            "long_open_interest_balance_volume","long_open_interest_balance_amount",
                            "short_open_interest_balance_volume","short_open_interest_balance_amount"]
                for c in num_cols:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                return df
    except:
        pass
    return pd.DataFrame()

def get_futures():
    # 優先讀 Render 即時資料
    df_relay = _load_relay_futures()
    if not df_relay.empty:
        return df_relay.sort_values("date") if "date" in df_relay.columns else df_relay, True

    # fallback：讀 GitHub CSV
    df, ok = load_csv("futures_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in df.select_dtypes("object").columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(df[c])
    return df.sort_values("date") if "date" in df.columns else df, True

def get_shareholder(stock_id=None):
    df, ok = load_csv("shareholder_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    if stock_id:
        df = df[df["stock_id"] == str(stock_id).strip()]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.sort_values("date") if "date" in df.columns else df, True

# ══════════════════════════════════════════════════════════════
# ▌ 技術指標計算
# ══════════════════════════════════════════════════════════════
def calc_indicators(df: pd.DataFrame, ma_s=5, ma_m=20, ma_l=60) -> pd.DataFrame:
    df = df.copy()
    c  = df["Close"].astype(float)
    v  = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.zeros(len(df)), index=df.index)

    # 保留原有 MA 供其他地方使用
    df[f"MA{ma_s}"]  = c.rolling(ma_s).mean()
    df[f"MA{ma_m}"]  = c.rolling(ma_m).mean()
    df[f"MA{ma_l}"]  = c.rolling(ma_l).mean()
    df[f"VMA{ma_s}"] = v.rolling(ma_s).mean()

    # EMA5、SMA60
    df["EMA5"]  = c.ewm(span=5, adjust=False).mean()
    df["SMA60"] = c.rolling(60).mean()

    # 布林通道（20MA 中軌，2σ 和 4σ）
    bb_mid   = c.rolling(20).mean()
    bb_std   = c.rolling(20).std()
    df["BB_MID"] = bb_mid
    df["UB2"]    = bb_mid + 2 * bb_std
    df["LB2"]    = bb_mid - 2 * bb_std
    df["UB4"]    = bb_mid + 4 * bb_std
    df["LB4"]    = bb_mid - 4 * bb_std

    # RSI(5) 和 RSI(20)
    for period, col in [(5, "RSI5"), (20, "RSI20")]:
        delta = c.diff()
        gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        df[col] = (100 - 100 / (1 + rs)).round(2)

    # VWAP (當日近似)
    tp  = (df["High"].astype(float) + df["Low"].astype(float) + c) / 3
    df["VWAP"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
    return df

def add_indicators(df, ws=5, wm=20, wl=60):
    return calc_indicators(df, ws, wm, wl)

# ══════════════════════════════════════════════════════════════
# ▌ Session State 初始化
# ══════════════════════════════════════════════════════════════
# ── 自動更新狀態初始化
if "last_auto_refresh" not in st.session_state:
    st.session_state.last_auto_refresh = None
if "live_prices" not in st.session_state:
    st.session_state.live_prices = {}  # {sid: {close, high, low, volume, time}}

# 每天自動清除舊的即時報價快取（避免昨日漲跌殘留）
_today_str = datetime.now().strftime("%Y-%m-%d")
if st.session_state.get("live_prices_date") != _today_str:
    st.session_state.live_prices = {}
    st.session_state.live_prices_date = _today_str

if "wl_loaded" not in st.session_state:
    manual, scan, etf_sh = load_watchlist_from_github()
    st.session_state.watchlist      = manual  # 手動加入
    st.session_state.watchlist_scan = scan    # 掃描結果加入
    st.session_state.etf_shares     = etf_sh  # ETF 持倉
    st.session_state.wl_loaded      = True
    st.session_state.wl_debug = ("token=有" if GITHUB_TOKEN else "token=無") + f" manual={len(manual)} scan={len(scan)}"

# ══════════════════════════════════════════════════════════════
# ▌ SIDEBAR
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:10px 0 14px;">
        <div style="font-size:2rem;">📈</div>
        <div style="color:#00d4ff;font-size:.9rem;font-weight:700;letter-spacing:2px;">台股量化系統</div>
        <div style="color:#7fb3d3;font-size:.66rem;margin-top:3px;">QUANT TRADING SYSTEM V6</div>
    </div>""", unsafe_allow_html=True)

    # ── 資料更新時間
    meta = load_json_meta()
    df_relay_check = _load_relay_chips()
    if not df_relay_check.empty and "date" in df_relay_check.columns:
        latest = df_relay_check["date"].max()
        upd = str(latest)[:10] + "（Render）"
    else:
        upd = meta.get("updated_at", "尚未更新")
    # 期貨日期
    df_fut_check, _ = get_futures()
    if not df_fut_check.empty and "date" in df_fut_check.columns:
        fut_latest = str(df_fut_check["date"].max())[:10]
        upd += f" | 期貨:{fut_latest}"
    st.markdown(
        f"<div class='infobox'>📅 資料更新：<b style='color:#00d4ff;'>{upd}</b></div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── 監控標的管理
    st.markdown(
        "<div style='color:#ffab40;font-size:.74rem;letter-spacing:1px;"
        "text-transform:uppercase;margin-bottom:6px;'>🎯 監控標的</div>",
        unsafe_allow_html=True,
    )

    # 手動加入
    with st.expander("➕ 加入監控清單", expanded=False):
        add_id = st.text_input("股票代號", placeholder="例：2454",
                               label_visibility="collapsed", key="sb_add")
        if st.button("加入", width='stretch', key="sb_add_btn"):
            code = add_id.strip()
            if code.isdigit() and len(code) == 4:
                name = code
                df_si, ok_si = get_stock_info()
                if ok_si:
                    m = df_si[df_si["stock_id"] == code]
                    if not m.empty:
                        name = str(m["stock_name"].iloc[0])
                entry = {"id": code, "name": name}
                if not any(w["id"] == code for w in st.session_state.watchlist):
                    st.session_state.watchlist.append(entry)
                    save_watchlist_to_github(st.session_state.watchlist, st.session_state.watchlist_scan, {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0}, st.session_state.get("reserve_list", []))
                    st.success(f"已加入：{name}")
                else:
                    st.info("已在清單中")
            else:
                st.warning("請輸入 4 位數字代號")

        # 手動清單
        if st.session_state.watchlist:
            st.caption("📌 手動加入")
            rm_idx = None
            for i, w in enumerate(st.session_state.watchlist):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"<span style='color:#e8f4fd;font-size:.78rem;'>{w['id']} {w['name']}</span>", unsafe_allow_html=True)
                if c2.button("✕", key=f"rm_{i}", width='stretch'):
                    rm_idx = i
            if rm_idx is not None:
                st.session_state.watchlist.pop(rm_idx)
                save_watchlist_to_github(st.session_state.watchlist, st.session_state.watchlist_scan, {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0}, st.session_state.get("reserve_list", []))
                st.rerun()
        else:
            st.caption("📌 手動清單為空")
        st.caption("🔧 " + st.session_state.get("wl_debug", "載入中..."))

        st.markdown(
            "<div style='border-top:1px solid #1e3a5f;margin:8px 0 4px;'></div>",
            unsafe_allow_html=True
        )
        if st.button("🔄 恢復監控清單", key="restore_wl", use_container_width=True,
                     help="從 GitHub 重新讀取監控清單，遺失時使用"):
            manual, scan, etf_sh = load_watchlist_from_github()
            st.session_state.watchlist      = manual
            st.session_state.watchlist_scan = scan
            st.session_state.etf_shares     = etf_sh
            st.session_state.etf_confirmed_portfolio = {
                k: v for k, v in etf_sh.items() if v > 0
            }
            st.toast(f"✅ 已恢復！手動 {len(manual)} 檔，掃描 {len(scan)} 檔", icon="✅")
            st.rerun()

        # 掃描清單
        if st.session_state.watchlist_scan:
            st.caption("🔍 掃描加入")
            rm_idx2 = None
            for i, w in enumerate(st.session_state.watchlist_scan):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"<span style='color:#e8f4fd;font-size:.78rem;'>{w['id']} {w['name']}</span>", unsafe_allow_html=True)
                if c2.button("✕", key=f"rms_{i}", width='stretch'):
                    rm_idx2 = i
            if rm_idx2 is not None:
                st.session_state.watchlist_scan.pop(rm_idx2)
                save_watchlist_to_github(st.session_state.watchlist, st.session_state.watchlist_scan, {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0}, st.session_state.get("reserve_list", []))
                st.rerun()

    st.markdown("---")

    # ── 圖表設定
    st.markdown(
        "<div style='color:#7fb3d3;font-size:.74rem;letter-spacing:1px;"
        "text-transform:uppercase;margin-bottom:6px;'>⚙️ 圖表設定</div>",
        unsafe_allow_html=True,
    )
    MA_S    = st.slider("短均線",  3, 10, 5)
    MA_M    = st.slider("中均線", 10, 30, 20)
    MA_L    = st.slider("長均線", 40,120, 60)
    PERIOD  = st.select_slider("K 線週期", ["3mo","6mo","1y","2y"], value="1y")

    st.markdown("---")
    if st.button("🔄 清除快取（強制重整）", width='stretch'):
        st.cache_data.clear()
        st.rerun()

    st.markdown(
        f"<div style='background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;"
        f"padding:8px;text-align:center;margin-top:6px;'>"
        f"<div style='color:#00d4ff;font-size:.76rem;font-family:monospace;'>"
        f"{datetime.now().strftime('%Y/%m/%d %H:%M')}</div>"
        f"<div style='color:#2ecc71;font-size:.64rem;margin-top:2px;'>"
        f"● GitHub CSV → Streamlit Cloud</div></div>",
        unsafe_allow_html=True,
    )

# ══════════════════════════════════════════════════════════════
# ▌ HEADER
# ══════════════════════════════════════════════════════════════
st.markdown(f"""
<div class="metric-card" style="border-left:4px solid #00d4ff;
     background:linear-gradient(90deg,#0f2027,#203a43,#2c5364);
     padding:18px 26px;margin-bottom:14px;">
    <h1 style="color:#e8f4fd;font-size:1.4rem;font-weight:700;margin:0;letter-spacing:1px;">
        📊 台股全週期量化交易系統 V6
    </h1>
    <p style="color:#7fb3d3;margin:4px 0 0;font-size:.76rem;">
        架構：本機爬蟲 → GitHub CSV → Streamlit Cloud ｜
        資料更新：{upd} ｜
        監控清單：{len(st.session_state.watchlist)} 檔
    </p>
    <p style="color:#00d4ff;margin:6px 0 0;font-size:.82rem;letter-spacing:.25em;text-align:right;">
        Rex × Gemini × Claude
    </p>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# ▌ 三大分頁
# ══════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "🔭 新星池",            # Phase 2 改造（原選股掃描＋大數據雷達）
    "🏆 王者候選名單",      # Phase 3 升級（原戰略儲備庫）
    "📊 指揮中心",          # Phase 4 合併（原持股監控＋每日作戰）
    "🌡️ 市場溫度計",       # Phase 5 精簡（原大盤預警）
    "🎯 今日行動建議",      # Phase 6 新增
    "🌱 產業趨勢雷達",      # 新增（第六個核心模組）
    "💰 ETF退休計畫",       # Phase 7 保留（退休壓艙石）
    "🧪 策略回測實驗室",    # 暫時保留，Phase 後期評估退休
])

# ══════════════════════════════════════════════════════════════
# ▌ 全域類股龍頭監控天網（置頂 Expander，tabs 上方）
# ══════════════════════════════════════════════════════════════
# 設計說明：
#   - 此區塊位於所有 Tab 之上，無論切換到哪個 Tab 都看得到
#   - 四大行業行列 multiselect 支援全台股代號自由輸入（accept_new_options）
#   - 調整後點擊「💾 鎖定新風向」才會覆寫 watch_list.json（本地+GitHub）
#   - 覆寫成功後 daily_scan.py 下午17:00排程自動讀取最新名單
# ──────────────────────────────────────────────────────────────
# ▌ TAB 1：選股掃描儀（階層式篩選＋評分）
# ──────────────────────────────────────────────────────────────
# ── 系統簽名
st.markdown(
    "<div style='text-align:right;color:#546e7a;font-size:.78rem;"
    "letter-spacing:.2em;padding:4px 12px;'>Rex × Gemini × Claude</div>",
    unsafe_allow_html=True
)

with tab1:
    st.markdown("<div class='sec-title'>🔭 新星池 · 陌生高成長標的發現引擎</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>"
        "新星池的任務：從全市場發現你還不認識的高成長標的。"
        "發現後進入<b>30天強制觀察期</b>，觀察期結束後才可人工升級進入王者候選名單。"
        "<br><b>硬性規則：新星池的標的不給買進建議，不設停損價，只負責讓你認識它。</b>"
        "</div>",
        unsafe_allow_html=True
    )

    # ══════════════════════════════════════════════════════════════
    # ▌ 新星池觀察清單（朋友未滿區）
    # ══════════════════════════════════════════════════════════════
    with st.expander("🌟 新星池｜朋友未滿區", expanded=True):

        # ── session state 初始化
        # 每檔格式：{
        #   id, name, added_date,
        #   logs: [{date, note}],       ← 觀察紀錄歷史
        #   checks: {c1,c2,c3,c4,c5}   ← 升級前驗證條件
        # }
        if "nova_pool" not in st.session_state:
            st.session_state.nova_pool = []

        # ── 加入新星
        st.markdown("**➕ 加入新標的**")
        _np_c1, _np_c2, _np_c3 = st.columns([2, 3, 1])
        with _np_c1:
            _np_sid = st.text_input("股票代號", placeholder="如：3661", key="nova_sid")
        with _np_c2:
            _np_note = st.text_input("第一筆觀察原因（必填）",
                                      placeholder="如：AI散熱新客戶導入，法說會提到Q3放量",
                                      key="nova_note")
        with _np_c3:
            st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
            if st.button("加入", key="nova_add", use_container_width=True):
                if _np_sid.strip() and _np_note.strip():
                    if any(n["id"] == _np_sid.strip() for n in st.session_state.nova_pool):
                        st.warning(f"{_np_sid} 已在新星池中")
                    else:
                        _np_name = get_stock_name_map().get(_np_sid.strip(), _np_sid.strip())
                        _today_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
                        st.session_state.nova_pool.append({
                            "id":         _np_sid.strip(),
                            "name":       _np_name,
                            "added_date": _today_str,
                            "logs":       [{"date": _today_str, "note": _np_note.strip()}],
                            "checks":     {"c1": False, "c2": False, "c3": False,
                                           "c4": False, "c5": False},
                        })
                        st.success(f"✅ {_np_sid} {_np_name} 已加入新星池，30天觀察期開始")
                        st.rerun()
                else:
                    st.warning("請填寫股票代號與觀察原因")

        st.markdown("---")

        if not st.session_state.nova_pool:
            st.caption("新星池目前為空。使用下方掃描工具發現陌生標的後，手動加入。")
        else:
            _today_np = datetime.now(ZoneInfo("Asia/Taipei")).date()
            _remove_id, _upgrade_id = None, None

            for _np in st.session_state.nova_pool:
                try:
                    _added = datetime.strptime(_np["added_date"], "%Y-%m-%d").date()
                    _days  = (_today_np - _added).days
                except Exception:
                    _days = 0
                _remaining = max(0, 30 - _days)
                _prog      = min(100, int(_days / 30 * 100))
                _graduated = _days >= 30

                # 邊框顏色
                _border = "#00cc66" if _graduated else "#fbbf24"
                _status = "🎓 觀察期已滿" if _graduated else f"👁️ 觀察中"

                with st.container():
                    st.markdown(
                        f"<div style='border:1px solid {_border};"
                        f"border-left:4px solid {_border};"
                        f"border-radius:8px;padding:14px 16px;margin:10px 0;"
                        f"background:rgba(255,255,255,0.02);'>",
                        unsafe_allow_html=True
                    )

                    # ── 標題列
                    _h1, _h2 = st.columns([7, 3])
                    with _h1:
                        st.markdown(
                            f"<span style='font-size:1rem;font-weight:700;color:#e8f4fd;'>"
                            f"{_status}　{_np['id']} {_np['name']}</span>　"
                            f"<span style='color:#9fb8d4;font-size:.82rem;'>"
                            f"加入：{_np['added_date']}　"
                            f"{'已觀察 '+str(_days)+' 天' if not _graduated else '共觀察 '+str(_days)+' 天'}"
                            f"</span>",
                            unsafe_allow_html=True
                        )
                    with _h2:
                        _btn_cols = st.columns(2)
                        with _btn_cols[0]:
                            # 升級按鈕：畢業才可用
                            _checks = _np.get("checks", {})
                            _all_checked = all(_checks.get(f"c{i}", False) for i in range(1, 6))
                            if _graduated and _all_checked:
                                if st.button("⬆️ 升級王者", key=f"nova_up_{_np['id']}",
                                             use_container_width=True):
                                    _upgrade_id = _np["id"]
                            elif _graduated and not _all_checked:
                                st.button("⬆️ 升級王者", key=f"nova_up_{_np['id']}",
                                          disabled=True, use_container_width=True,
                                          help="請先完成所有驗證條件")
                            else:
                                st.button(f"還差 {_remaining} 天",
                                          key=f"nova_up_{_np['id']}",
                                          disabled=True, use_container_width=True)
                        with _btn_cols[1]:
                            if st.button("🗑️ 淘汰", key=f"nova_rm_{_np['id']}",
                                         use_container_width=True):
                                _remove_id = _np["id"]

                    # ── 進度條
                    if not _graduated:
                        st.markdown(
                            f"<div style='background:#1e3a5f;border-radius:4px;"
                            f"height:5px;margin:8px 0 12px;'>"
                            f"<div style='background:#fbbf24;width:{_prog}%;"
                            f"height:5px;border-radius:4px;'></div></div>",
                            unsafe_allow_html=True
                        )

                    # ── 觀察紀錄 + 新增紀錄
                    _log_col, _check_col = st.columns([6, 4])

                    with _log_col:
                        st.markdown("**📓 觀察紀錄**")
                        _logs = _np.get("logs", [])
                        for _log in reversed(_logs[-5:]):  # 最新5筆
                            st.markdown(
                                f"<div style='padding:4px 10px;margin:3px 0;"
                                f"border-left:2px solid #1e3a5f;"
                                f"font-size:.8rem;color:#9fb8d4;'>"
                                f"<span style='color:#7fb3d3;'>{_log['date']}</span>　"
                                f"{_log['note']}</div>",
                                unsafe_allow_html=True
                            )
                        # 追加紀錄
                        _new_log = st.text_input(
                            "追加觀察紀錄",
                            placeholder="今天的觀察...",
                            key=f"nova_log_{_np['id']}",
                            label_visibility="collapsed"
                        )
                        if st.button("➕ 追加", key=f"nova_log_add_{_np['id']}"):
                            if _new_log.strip():
                                _log_date = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
                                for _item in st.session_state.nova_pool:
                                    if _item["id"] == _np["id"]:
                                        if "logs" not in _item:
                                            _item["logs"] = []
                                        _item["logs"].append({
                                            "date": _log_date,
                                            "note": _new_log.strip()
                                        })
                                        break
                                st.rerun()

                    with _check_col:
                        # ── 升級前五大驗證條件
                        st.markdown("**✅ 升級驗證條件（滿足才可升級）**")
                        _check_labels = {
                            "c1": "了解公司主要產品與客戶",
                            "c2": "看過最近一季財報或法說",
                            "c3": "確認產業景氣方向向上",
                            "c4": "確認外資或投信持續買進",
                            "c5": "確認股價未在歷史高點區",
                        }
                        for _ck, _cl in _check_labels.items():
                            _cur = _np.get("checks", {}).get(_ck, False)
                            _new_val = st.checkbox(
                                _cl, value=_cur,
                                key=f"nova_ck_{_np['id']}_{_ck}"
                            )
                            if _new_val != _cur:
                                for _item in st.session_state.nova_pool:
                                    if _item["id"] == _np["id"]:
                                        if "checks" not in _item:
                                            _item["checks"] = {}
                                        _item["checks"][_ck] = _new_val
                                        break

                    st.markdown("</div>", unsafe_allow_html=True)

            # ── 執行升級
            if _upgrade_id:
                _np_obj = next((n for n in st.session_state.nova_pool if n["id"] == _upgrade_id), None)
                if _np_obj:
                    _exists_rsv = any(
                        r["id"] == _upgrade_id
                        for r in st.session_state.get("reserve_list", [])
                    )
                    if not _exists_rsv:
                        if "reserve_list" not in st.session_state:
                            st.session_state.reserve_list = []
                        _all_logs = _np_obj.get("logs", [])
                        _log_summary = "；".join(
                            l["note"] for l in _all_logs[-3:]
                        ) if _all_logs else ""
                        st.session_state.reserve_list.append({
                            "id":       _upgrade_id,
                            "name":     _np_obj["name"],
                            "note":     f"[新星池升級 {_np_obj['added_date']}~{datetime.now().strftime('%Y-%m-%d')}] {_log_summary}",
                            "added_at": datetime.now().strftime("%Y-%m-%d"),
                        })
                    st.session_state.nova_pool = [
                        n for n in st.session_state.nova_pool if n["id"] != _upgrade_id
                    ]
                    st.success(f"✅ {_upgrade_id} {_np_obj['name']} 已升級進入王者候選名單！")
                    st.rerun()

            # ── 執行淘汰
            if _remove_id:
                st.session_state.nova_pool = [
                    n for n in st.session_state.nova_pool if n["id"] != _remove_id
                ]
                st.rerun()

    st.markdown("---")
    st.markdown(
        "<div style='color:#7fb3d3;font-size:.82rem;margin-bottom:12px;'>"
        "⬇️ 使用下方掃描工具發現有潛力的陌生標的，再手動加入上方新星池觀察清單。"
        "</div>",
        unsafe_allow_html=True
    )

    # ── 原有選股掃描功能保留（發現工具）

    # ── 空頭避險 Toggle
    bear_mode = st.toggle("🐻 啟動空頭避險模式", key="bear_mode")

    if bear_mode:
        st.error(
            "🐻 **空頭作戰模式啟動！**\n\n"
            "**大盤處於高風險區，作戰守則：**\n"
            "① 持股控制在 3 成以下，現金為王\n"
            "② 停損紀律優先，跌破月線立刻執行\n"
            "③ 避開高本益比、高融資、低毛利的地雷股\n"
            "④ 空頭反彈不追，等量縮整理後再評估"
        )
        bear_tactic = st.radio(
            "請選擇空頭戰術：",
            ["🛡️ 尋找抗跌避風港（逆勢做多強勢股）",
             "🎯 狙擊破線弱勢股（順勢做空爛公司）"],
            horizontal=True, key="bear_tactic"
        )
        if bear_tactic.startswith("🛡️"):
            st.info(
                "💡 **說明**：空頭下的強勢股極為罕見，若能在此時符合條件，"
                "往往是資金避風港或具備強大基本面護城河。"
                "系統維持高標準濾網（EPS>10、毛利>30%、站上20MA）嚴格篩選。"
            )
            bear_short_mode = False  # 維持多頭篩選邏輯
        else:
            st.warning(
                "🎯 **做空模式**：系統將反轉篩選條件，"
                "尋找 EPS 衰退、法人賣超、跌破 20MA 的弱勢標的。"
                "請注意：做空風險極高，務必設定嚴格停損。"
            )
            bear_short_mode = True   # 反轉篩選邏輯
    else:
        bear_tactic    = None
        bear_short_mode = False

    # ── 說明
    if not bear_mode:
        st.markdown("""
    <div class='infobox'>
        <b style='color:#00d4ff;'>階層式篩選邏輯：</b><br>
        第一道：基本面護城河（EPS / P/E / 毛利率） → 建立核心母體<br>
        第二道：籌碼黃金交叉（法人/融資/技術面）→ 6項評分<br>
        第三道：財報趨勢驗證（月營收 YoY / 毛利率 QoQ / EPS YoY）→ 3項評分
    </div>
    """, unsafe_allow_html=True)

    # ── 掃描範圍設定
    with st.expander("🎯 掃描範圍設定", expanded=True):
        rng_type = st.radio(
            "掃描方式",
            ["📂 產業分類", "📊 產業板塊（動態）", "🔢 股號開頭", "🌏 全市場", "✏️ 自訂代號",
             "🌊 土洋認養雷達", "⚡ 黃金窒息量雷達", "💎 大戶硬漢雷達", "🎯 MTFA 狙擊名單",
             "🚀 短線火箭雷達"],
            horizontal=True, label_visibility="collapsed"
        )

        # 產業分類
        SECTOR_MAP = {
            "🔬 半導體｜IC設計":      ["2454","2379","3034","2303","2449","2388","3515","5347","4966","3443","6770","2344","2408","3653","6523","3661","6415","3035","2363","6533","3141","6643","3014","5274","4968","6269","3596","6789","2436","3494","2471","6510","3532","6147","8081","3209","6278","2406","6803","4919","3037","6230","5269","4961","3376","6214","3706","2397","3228","6442"],
            "⚡ 半導體｜晶圓代工封測": ["2330","2337","2325","3711","6274","2368","2351","6257","3016","2455","6271","2441","6239","3105","2329","3530","5483","6488","2383","3038","2475","3260","2340","2393","2409","3481","3691","6146","3057","4142"],
            "💻 AI伺服器｜雲端運算":   ["2382","2356","2353","2357","6669","3231","2301","2324","3017","2399","3533","6461","3583","6285","3023","2383","3189","5269","4938","3706","3062","2397","5354","2365","3044","3057","6230","3085","6442","6146","2332","3376","6257","2462","6510","3597","2406","6214","3228","2308"],
            "📱 消費電子｜手機零組件":  ["2317","2354","2498","3008","2439","3406","4958","2327","3036","2429","6278","2474","4961","2421","2393","6120","2308","6277","3376","6415","4906","3028","5371","2049","3017","2365","2364","3034","2332","6285","3059","6271","2340","3030","3023","2351","1590","3533","2460"],
            "🔋 電動車｜綠能儲能":     ["2308","6415","5483","6244","1590","1504","1514","1537","8210","1560","2207","2201","2204","1605","1603","1608","1609","1612","5009","1466","1710","1711","3211","6409","3593","3576","3548"],
            "🌐 網通｜5G基礎建設":     ["2412","4904","3045","2332","2345","3047","6456","4906","3518","6277","3062","6285","6227","3059","6409","3707","4960","6510","3596","2348"],
            "🏦 金融｜銀行保險券商":   ["2881","2882","2891","2886","2887","2884","2885","2892","2880","5880","2801","2820","2834","2838","2849","2850","2855","2856","2867","2883","2888","2889","2890","5876","5878"],
            "🧪 傳統產業｜石化鋼鐵":   ["6505","1301","1303","1326","1402","2002","1101","1102","2006","2007","2008","2009","2010","2012","2013","2014","2015"],
            "🏗️ 營建｜不動產":        ["5522","2528","2534","2511","2597","2515","5533","5536","5546","2543","2535","2536","2537","2538","2540","2542","2545","2546","2547","2548"],
            "💊 生技醫療":             ["4743","1789","4144","4147","6446","1760","4174","4162","4141","6547","4106","4108","4119","4121","4123","4126","4128","4130","4133","4148"],
            "🛒 零售百貨｜電商":       ["2912","2903","2915","5904","2910","2905","2908","2911","2914","2923","8044","5903"],
            "🏭 機械｜工具機":         ["2049","1590","1560","2059","2061","2062","2063","2064","2065","2201","2204","2207","2208","1580","1582","1583","1584","1585","1586","1589"],
        }

        # 股號開頭分組
        PREFIX_MAP = {
            "1開頭（傳產/食品/紡織）": "1",
            "2開頭（電子/金融/汽車）": "2",
            "3開頭（電子零組件）":     "3",
            "4開頭（生技/化工）":      "4",
            "5開頭（營建/其他）":      "5",
            "6開頭（新興電子）":       "6",
            "8開頭（其他電子）":       "8",
        }

        scan_pool_ids = []

        if rng_type == "📂 產業分類":
            selected_sector = st.selectbox(
                "選擇產業",
                list(SECTOR_MAP.keys()),
                label_visibility="collapsed"
            )
            scan_pool_ids = SECTOR_MAP[selected_sector]
            st.markdown(
                f"<div class='infobox'>掃描 <b style='color:#00d4ff;'>{selected_sector}</b>"
                f" ｜ 約 <b style='color:#e8f4fd;'>{len(scan_pool_ids)}</b> 檔</div>",
                unsafe_allow_html=True
            )

        elif rng_type == "📊 產業板塊（動態）":
            # 動態從 stock_info.csv 讀取產業別
            @st.cache_data(ttl=3600)
            def get_sector_options():
                df_si, ok = load_csv("stock_info.csv")
                if not ok or df_si.empty:
                    return {}, {}
                df_si["stock_id"] = df_si["stock_id"].astype(str).str.strip()
                df_si["stock_name"] = df_si["stock_name"].astype(str).str.strip()
                # 優先取有中文名稱的
                has_name = df_si[df_si["stock_name"] != df_si["stock_id"]].copy()
                no_name  = df_si[df_si["stock_name"] == df_si["stock_id"]].copy()
                df_clean = pd.concat([has_name, no_name]).drop_duplicates("stock_id")
                # 填補空白產業別
                if "industry_category" in df_clean.columns:
                    df_clean["industry_category"] = df_clean["industry_category"].fillna("其他/未分類")
                else:
                    df_clean["industry_category"] = "其他/未分類"
                sector_map = df_clean.groupby("industry_category")["stock_id"].apply(list).to_dict()
                return sector_map

            sector_map_dyn = get_sector_options()
            if not sector_map_dyn:
                st.warning("無法讀取產業別資料，請確認 stock_info.csv")
                scan_pool_ids = []
            else:
                sector_list = sorted(sector_map_dyn.keys())
                selected_sectors = st.multiselect(
                    "選擇產業板塊（可多選）",
                    sector_list,
                    placeholder="搜尋或選擇產業...",
                    label_visibility="collapsed"
                )
                if selected_sectors:
                    scan_pool_ids = []
                    for s in selected_sectors:
                        scan_pool_ids.extend(sector_map_dyn.get(s, []))
                    scan_pool_ids = list(dict.fromkeys(scan_pool_ids))  # 去重
                    st.markdown(
                        f"<div class='infobox'>已選 <b style='color:#00d4ff;'>{len(selected_sectors)}</b> 個板塊"
                        f" ｜ 共 <b style='color:#e8f4fd;'>{len(scan_pool_ids)}</b> 檔</div>",
                        unsafe_allow_html=True
                    )
                else:
                    scan_pool_ids = []
                    st.info("請選擇至少一個產業板塊")

        elif rng_type == "🔢 股號開頭":
            selected_prefix = st.selectbox(
                "選擇股號開頭",
                list(PREFIX_MAP.keys()),
                label_visibility="collapsed"
            )
            prefix_digit = PREFIX_MAP[selected_prefix]
            # 從財報CSV取得所有股號，過濾開頭
            scan_pool_ids = []  # 實際掃描時動態過濾
            st.markdown(
                f"<div class='infobox'>掃描 <b style='color:#00d4ff;'>{selected_prefix}</b>"
                f" 的所有股票（依財報CSV動態篩選）</div>",
                unsafe_allow_html=True
            )

        elif rng_type == "🌏 全市場":
            st.markdown(
                "<div class='infobox'>⚠️ 掃描所有有財報資料的股票，"
                "數量較多（約 300~800 檔），耗時較長</div>",
                unsafe_allow_html=True
            )
            scan_pool_ids = []  # 掃描時動態取全部

        elif rng_type == "🎯 MTFA 狙擊名單":
            # 從 dynamic_themes.json 讀取今日狙擊個股
            try:
                url_m = f"{GITHUB_RAW}/dynamic_themes.json"
                import requests as _rqm
                _rm = _rqm.get(url_m, timeout=8)
                if _rm.status_code == 200:
                    _tmj = _rm.json()
                    _top = _tmj.get("top15", [])
                    scan_pool_ids = [s.split()[0] for s in _top if s.split()]
                    _dt  = _tmj.get("trade_date", "")
                    _th  = "、".join(_tmj.get("themes", []))
                    st.markdown(
                        f"<div class='infobox'>🎯 <b style='color:#00d4ff;'>MTFA 狙擊名單</b>"
                        f"　｜　{_dt} AI萃取題材：<b style='color:#ffeb3b;'>{_th}</b>"
                        f"　｜　共 <b>{len(scan_pool_ids)}</b> 檔</div>",
                        unsafe_allow_html=True
                    )
                else:
                    scan_pool_ids = []
                    st.warning("MTFA 狙擊名單讀取失敗，請先執行 generate_dynamic_themes.py")
            except Exception as _em:
                scan_pool_ids = []
                st.warning(f"MTFA 狙擊名單讀取失敗：{_em}")

        elif rng_type == "🚀 短線火箭雷達":
            # 掃描全市場找法人點火+融資退場標的
            df_si_rk, ok_si_rk = get_stock_info()
            if ok_si_rk and not df_si_rk.empty:
                _all_sids_rk = df_si_rk["stock_id"].astype(str).str.strip().tolist()
                _scan_n = min(300, len(_all_sids_rk))
                _rocket_ids   = []
                _rocket_data  = []  # 儲存完整掃描結果
                _prog_rk = st.progress(0, text="🚀 短線火箭雷達掃描中...")
                df_si_name = df_si_rk.set_index("stock_id")["stock_name"].to_dict()                              if "stock_name" in df_si_rk.columns else {}
                for _ri, _rsid in enumerate(_all_sids_rk[:_scan_n]):
                    _prog_rk.progress((_ri+1)/_scan_n,
                        text=f"🚀 掃描 {_ri+1}/{_scan_n}：{_rsid}")
                    _rm = scan_short_term_momentum(_rsid)
                    if _rm.get("trigger"):
                        _rocket_ids.append(_rsid)
                        _rocket_data.append((_rsid, df_si_name.get(_rsid, _rsid), _rm))
                _prog_rk.empty()
                scan_pool_ids = _rocket_ids

                if _rocket_data:
                    st.markdown(
                        f"<div class='infobox'>🚀 短線火箭雷達掃出 "
                        f"<b style='color:#ff5252;'>{len(_rocket_data)}</b> 檔"
                        f"法人點火+融資退場標的，進入三道篩選</div>",
                        unsafe_allow_html=True
                    )
                    # 依 score 排序，顯示詳細色卡
                    _rocket_data.sort(key=lambda x: x[2].get("score", 0), reverse=True)
                    _rows_rk = []
                    for _rsid, _rname, _rm in _rocket_data:
                        _score = _rm.get("score", 0)
                        _facts = _rm.get("facts", {})
                        _msg   = _rm.get("msg", "")
                        # 分數顏色
                        if _score >= 4:
                            _col = "#ffee55"; _ico = "🚀"; _bg = "background:rgba(255,238,85,0.08);border-left:4px solid #ffee55;"
                        elif _score >= 2:
                            _col = "#ff9900"; _ico = "⚡"; _bg = "background:rgba(255,153,0,0.06);border-left:4px solid #ff9900;"
                        else:
                            _col = "#00d4ff"; _ico = "📡"; _bg = "background:rgba(0,212,255,0.04);border-left:4px solid #00d4ff;"
                        # 投信買超（台股紅買綠賣）
                        _inst = _facts.get("inst_net", 0)
                        _ic   = "#ff3333" if _inst >= 0 else "#00cc44"
                        _is   = f"+{int(_inst):,}" if _inst >= 0 else f"{int(_inst):,}"
                        _rows_rk.append(
                            f"<div style='font-size:.84rem;margin-bottom:8px;padding:6px 12px;"
                            f"border-radius:4px;{_bg}color:{_col};'>"
                            f"{_ico} <b>{_rsid} {_rname}</b>｜"
                            f"得分 {_score}｜"
                            f"法人淨買 <span style='color:{_ic};font-weight:700;'>{_is}張</span>｜"
                            f"融資比 {_facts.get('margin_ratio','—')}｜"
                            f"{_msg[:40]}"
                            f"</div>"
                        )
                    st.markdown(
                        "<div style='background:rgba(0,0,0,0.2);border:1px solid #1e3a5f;"
                        "border-radius:10px;padding:10px 4px;'>"
                        + "".join(_rows_rk) + "</div>",
                        unsafe_allow_html=True
                    )
                else:
                    st.info("🚀 今日無標的觸發短線火箭條件（法人未明顯點火或融資未退場）")
            else:
                scan_pool_ids = []

        elif rng_type in ["🌊 土洋認養雷達", "⚡ 黃金窒息量雷達", "💎 大戶硬漢雷達"]:
            # 從 session_state 取雷達結果
            radar_map = {
                "🌊 土洋認養雷達":    ("radar_r1", "土洋認養"),
                "⚡ 黃金窒息量雷達":  ("radar_r2", "黃金窒息量"),
                "💎 大戶硬漢雷達":    ("radar_r3", "大戶硬漢"),
            }
            rkey, rname = radar_map[rng_type]
            df_radar_src = st.session_state.get(rkey, pd.DataFrame())
            if df_radar_src.empty:
                st.warning(
                    f"{rname}雷達尚未執行！請依序："
                    f" 1) 前往 Tab5 大數據雷達"
                    f" 2) 點擊啟動大數據雷達掃描"
                    f" 3) 回到此頁選擇雷達清單"
                )
                scan_pool_ids = []
            else:
                scan_pool_ids = df_radar_src["代號"].astype(str).tolist()
                st.markdown(
                    f"<div class='infobox'>使用 <b style='color:#00d4ff;'>{rname}雷達</b> 結果"
                    f" ｜ 共 <b style='color:#e8f4fd;'>{len(scan_pool_ids)}</b> 檔</div>",
                    unsafe_allow_html=True
                )

        else:  # 自訂代號
            custom_input = st.text_area(
                "輸入股票代號（逗號或換行分隔）",
                placeholder="例：2330,2454,2308",
                height=80, label_visibility="collapsed"
            )
            if custom_input.strip():
                raw = custom_input.replace("\n", ",").replace("，", ",").replace(" ", "")
                scan_pool_ids = [c.strip() for c in raw.split(",") if c.strip().isdigit() and len(c.strip()) == 4]
                scan_pool_ids = [c.strip() for c in raw.split(",")
                                 if c.strip().isdigit() and len(c.strip())==4]
                st.markdown(
                    f"<div class='infobox'>已解析 <b style='color:#00e676;'>"
                    f"{len(scan_pool_ids)}</b> 檔</div>",
                    unsafe_allow_html=True
                )

    # ── 篩選條件設定
    # ── 篩選條件預設值（你的量化策略核心參數）
    FILTER_DEFAULTS = {
        "t1_eps": 10.0, "t1_pe": 40.0, "t1_gm": 30.0,
        "t1_mg": -3.0,  "t1_inst": 10.0, "t1_bias": 5.0, "t1_vol": 0.60,
        "t1_rev": 10.0, "t1_epsy": 20.0,
    }
    FILTER_KEYS = list(FILTER_DEFAULTS.keys())

    def apply_preset(preset_dict):
        for k, v in preset_dict.items():
            st.session_state[k] = v

    with st.expander("⚙️ 調整篩選條件", expanded=False):
        # ── 第一排：恢復預設 + 載入自訂1~3
        row1 = st.columns(4)
        with row1[0]:
            if st.button("↺ 恢復預設值", key="t1_reset", width='stretch'):
                apply_preset(FILTER_DEFAULTS)
                st.rerun()
        for slot in [1, 2, 3]:
            with row1[slot]:
                if st.button(f"📥 載入自訂{slot}", key=f"load_c{slot}", width='stretch'):
                    saved = st.session_state.get(f"t1_custom{slot}")
                    if saved:
                        apply_preset(saved)
                        st.rerun()
                    else:
                        st.toast(f"自訂{slot} 尚未儲存", icon="⚠️")
        # ── 第二排：儲存自訂1~3
        row2 = st.columns(4)
        for slot in [1, 2, 3]:
            with row2[slot]:
                if st.button(f"💾 儲存自訂{slot}", key=f"save_c{slot}", width='stretch'):
                    st.session_state[f"t1_custom{slot}"] = {k: st.session_state.get(k, FILTER_DEFAULTS[k]) for k in FILTER_KEYS}
                    st.toast(f"✅ 已儲存自訂{slot}", icon="✅")

        st.divider()
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            st.markdown("**第一道：基本面**")
            eps_min = st.number_input("近四季 EPS 合計 >", value=float(st.session_state.get("t1_eps", FILTER_DEFAULTS["t1_eps"])),  step=0.5, key="t1_eps")
            pe_max  = st.number_input("P/E <",            value=float(st.session_state.get("t1_pe",  FILTER_DEFAULTS["t1_pe"])),   step=1.0, key="t1_pe")
            gm_min  = st.number_input("最新季毛利率% >",  value=float(st.session_state.get("t1_gm",  FILTER_DEFAULTS["t1_gm"])),   step=1.0, key="t1_gm")
        with fc2:
            st.markdown("**第二道：籌碼技術**")
            mg_max       = st.number_input("融資5日變動% <",  value=float(st.session_state.get("t1_mg",   FILTER_DEFAULTS["t1_mg"])),   step=0.5,  key="t1_mg")
            inst_min_pct = st.number_input("法人買超比例% >", value=float(st.session_state.get("t1_inst", FILTER_DEFAULTS["t1_inst"])), step=1.0,  key="t1_inst")
            bias_max     = st.number_input("MA20乖離% <",     value=float(st.session_state.get("t1_bias", FILTER_DEFAULTS["t1_bias"])), step=0.5,  key="t1_bias")
            vol_max_r    = st.number_input("量比(5MA) <",     value=float(st.session_state.get("t1_vol",  FILTER_DEFAULTS["t1_vol"])),  step=0.05, key="t1_vol")
        with fc3:
            st.markdown("**第三道：財報趨勢**")
            rev_yoy_min  = st.number_input("月營收 YoY% >",  value=float(st.session_state.get("t1_rev",  FILTER_DEFAULTS["t1_rev"])),  step=1.0, key="t1_rev")
            eps_yoy_min  = st.number_input("EPS YoY% >",     value=float(st.session_state.get("t1_epsy", FILTER_DEFAULTS["t1_epsy"])), step=1.0, key="t1_epsy")

    if st.button("🚀 開始掃描", type="primary", width='stretch', key="t1_scan"):
        # ── 載入所有資料
        with st.spinner("載入資料中..."):
            df_si, ok_si = get_stock_info()
            df_fin, ok_fin = get_financials()
            df_chips, ok_chips = get_chips()

        if not ok_fin:
            st.error("❌ 財報 CSV 不足，請先執行 update_data.py --only financials 並推送 GitHub")
        elif df_fin.empty:
            st.error("❌ financial_data.csv 是空的，請重新執行 update_data.py --only financials --force")
        else:
            prog = st.progress(0)
            results = []

            # 欄位：date, stock_id, type, value, origin_name
            # origin_name = 中文名稱（基本每股盈餘、營業收入...）
            # type = 英文代碼（EPS, Revenue, GrossMargin...）
            name_col = "origin_name" if "origin_name" in df_fin.columns else                        "type"         if "type"         in df_fin.columns else                        df_fin.columns[2]



            # ── 依掃描範圍決定股票池
            all_fin_ids = df_fin["stock_id"].dropna().unique().tolist()

            if rng_type == "📂 產業分類":
                stock_ids = [s for s in scan_pool_ids if s in all_fin_ids]
            elif rng_type == "📊 產業板塊（動態）":
                stock_ids = [s for s in scan_pool_ids if s in all_fin_ids]
            elif rng_type == "🔢 股號開頭":
                stock_ids = [s for s in all_fin_ids if s.startswith(prefix_digit)]
            elif rng_type == "✏️ 自訂代號":
                stock_ids = [s for s in scan_pool_ids if s in all_fin_ids]
            elif rng_type in ["🌊 土洋認養雷達", "⚡ 黃金窒息量雷達", "💎 大戶硬漢雷達",
                              "🎯 MTFA 狙擊名單"]:
                # 直接用雷達/AI題材結果，不強制過濾 all_fin_ids（避免漏掉）
                stock_ids = scan_pool_ids if scan_pool_ids else all_fin_ids
            elif rng_type == "🚀 短線火箭雷達":
                # 短線火箭：只用雷達掃出的標的，無結果就停止不跑全市場
                stock_ids = scan_pool_ids
            else:  # 全市場
                stock_ids = all_fin_ids

            if not stock_ids:
                st.warning("⚠️ 此範圍在財報 CSV 中無對應資料，請先執行 update_data.py")
                st.stop()

            total = len(stock_ids)
            st.markdown(
                f"<div class='infobox'>掃描 <b style='color:#00d4ff;'>{total}</b> 檔</div>",
                unsafe_allow_html=True
            )
            for idx, sid in enumerate(stock_ids):
                prog.progress((idx + 1) / total)

                # ─── 第一道：基本面
                df_f = df_fin[df_fin["stock_id"].astype(str) == str(sid)].copy()
                if df_f.empty:
                    continue

                # EPS
                # EPS：origin_name 包含「每股盈餘」，type 為 EPS
                eps_rows = df_f[
                    df_f[name_col].str.contains("每股盈餘|BasicEPS|EPS", case=False, na=False) |
                    (df_f.get("type", pd.Series(dtype=str)).str.contains("EPS", case=False, na=False)
                     if "type" in df_f.columns else False)
                ]
                eps_vals = pd.to_numeric(eps_rows["value"], errors="coerce").dropna()
                # EPS：有幾筆加幾筆（最多4季），至少1筆就顯示
                if len(eps_vals) >= 1:
                    eps_ttm = eps_vals.tail(4).sum()
                else:
                    eps_ttm = np.nan

                # 毛利率
                # 毛利率：origin_name 包含「毛利率」，FinMind 值為小數（0.45=45%）
                gm_rows = df_f[
                    df_f[name_col].str.contains("毛利率|GrossMargin", case=False, na=False) |
                    (df_f.get("type", pd.Series(dtype=str)).str.contains("GrossMargin", case=False, na=False)
                     if "type" in df_f.columns else False)
                ]
                gm_vals   = pd.to_numeric(gm_rows["value"], errors="coerce").dropna()
                # 若值小於 2，代表是小數格式（0.45），轉成百分比
                if not gm_vals.empty and float(gm_vals.iloc[-1]) < 2:
                    gm_vals = gm_vals * 100
                gm_latest = float(gm_vals.iloc[-1]) if not gm_vals.empty else np.nan

                # 若 financial_data 沒有毛利率，從 price_basic.csv 補
                if np.isnan(gm_latest):
                    df_pb, ok_pb = get_price_basic(sid)
                    if ok_pb and not df_pb.empty and "gross_margin" in df_pb.columns:
                        gm_val = pd.to_numeric(df_pb["gross_margin"].iloc[0], errors="coerce")
                        if not pd.isna(gm_val):
                            gm_latest = float(gm_val)

                # P/E（從 K 線近期算，或用預設值 nan）
                pe_val = np.nan
                df_prc, ok_prc = load_price_csv(str(sid))
                if ok_prc and not df_prc.empty and not np.isnan(eps_ttm) and eps_ttm > 0:
                    last_close = float(df_prc["Close"].iloc[-1]) if "Close" in df_prc.columns else np.nan
                    if not np.isnan(last_close):
                        # 用年化EPS算P/E，避免只有1季時P/E虛高
                        n_eps_pe = min(len(eps_vals), 4)
                        eps_ann_pe = eps_ttm / n_eps_pe * 4
                        pe_val = last_close / eps_ann_pe if eps_ann_pe > 0 else np.nan

                # 第一道判斷
                if bear_short_mode:
                    # 做空模式：反轉條件，找EPS衰退/高PE/低毛利
                    if not np.isnan(eps_ttm):
                        n_eps = min(len(eps_vals), 4)
                        eps_annualized = eps_ttm / n_eps * 4
                        p1_eps = eps_annualized < eps_min
                    else:
                        p1_eps = False
                    p1_pe  = np.isnan(pe_val)    or pe_val    > pe_max
                    p1_gm  = np.isnan(gm_latest) or gm_latest < gm_min
                    pass1  = p1_eps or p1_pe or p1_gm
                else:
                    # 正常多頭模式
                    if not np.isnan(eps_ttm):
                        n_eps = min(len(eps_vals), 4)
                        eps_annualized = eps_ttm / n_eps * 4
                        p1_eps = eps_annualized > eps_min
                    else:
                        p1_eps = True
                    p1_pe  = np.isnan(pe_val)  or pe_val  < pe_max
                    p1_gm  = np.isnan(gm_latest) or gm_latest > gm_min
                    pass1  = p1_eps and p1_pe and p1_gm
                if not pass1:
                    continue

                # 做空模式：跌破MA20 作為額外確認
                if bear_short_mode:
                    if not np.isnan(float(lt_price.get("MA20", float("nan")))):
                        if float(lt_price["Close"]) >= float(lt_price["MA20"]):
                            continue  # 守月線的不做空

                # ─── 第二道：籌碼＋技術（6 項評分）
                df_c  = df_chips[df_chips["stock_id"].astype(str) == str(sid)]
                score2 = 0
                s2 = {}

                # ── a. 融資5日變動 < mg_max%
                mg_chg = np.nan
                if "source" in df_c.columns:
                    mg_rows = df_c[df_c["source"] == "margin"].copy()
                else:
                    mg_rows = pd.DataFrame()
                if not mg_rows.empty:
                    mg_col = next(
                        (c for c in mg_rows.columns
                         if "MarginPurchaseTodayBalance" in c), None
                    )
                    if mg_col:
                        bal = pd.to_numeric(mg_rows[mg_col], errors="coerce").dropna()
                        if len(bal) >= 2:
                            n = min(5, len(bal))
                            mg_chg = float(
                                (bal.iloc[-1] - bal.iloc[-n]) /
                                max(abs(float(bal.iloc[-n])), 1) * 100
                            )
                s2["融資5日變動"] = (not np.isnan(mg_chg)) and mg_chg < mg_max
                if s2["融資5日變動"]: score2 += 1

                # ── b. 法人5日淨買超 > inst_min_pct%（佔總成交量）
                inst_buy_pct = np.nan
                if "source" in df_c.columns:
                    inst_rows = df_c[df_c["source"] == "institutional"].copy()
                else:
                    inst_rows = df_c.copy()
                if not inst_rows.empty and "net" in inst_rows.columns:
                    inst_rows["date"] = pd.to_datetime(inst_rows["date"], errors="coerce")
                    inst_rows["net"]  = pd.to_numeric(inst_rows["net"], errors="coerce")
                    inst_rows["buy"]  = pd.to_numeric(inst_rows["buy"], errors="coerce").fillna(0)
                    inst_rows["sell"] = pd.to_numeric(inst_rows["sell"], errors="coerce").fillna(0)
                    # 近5個交易日的淨買超
                    daily = inst_rows.groupby("date").agg(
                        net=("net","sum"), buy=("buy","sum"), sell=("sell","sum")
                    ).sort_index()
                    net_5d  = float(daily["net"].iloc[-5:].sum())
                    total_v = float((daily["buy"].abs() + daily["sell"].abs()).sum())
                    inst_buy_pct = net_5d / max(total_v, 1) * 100 if total_v > 0 else 0
                s2["法人買超"] = (not np.isnan(inst_buy_pct)) and inst_buy_pct > inst_min_pct
                if s2["法人買超"]: score2 += 1

                # ── c. 大戶持股（400張以上）單週上升
                # 欄位：HoldingSharesLevel, percent, date
                # 大戶定義：400,001張以上 + more than 1,000,001
                df_sh, ok_sh = get_shareholder(sid)
                big_rising = False
                if ok_sh and not df_sh.empty:
                    lv_col  = "HoldingSharesLevel" if "HoldingSharesLevel" in df_sh.columns else                               next((c for c in df_sh.columns if "level" in c.lower()), None)
                    pct_col = "percent" if "percent" in df_sh.columns else                               next((c for c in df_sh.columns if "percent" in c.lower()), None)
                    if lv_col and pct_col:
                        df_sh = df_sh[~df_sh[lv_col].astype(str).str.contains("total|差異|調整", na=False)].copy()
                        df_sh[pct_col] = pd.to_numeric(df_sh[pct_col], errors="coerce")
                        # 大戶：400,001以上 或 more than
                        big_kw = ["400,001","600,001","800,001","1,000,001","more than"]
                        is_big = df_sh[lv_col].astype(str).str.contains(
                                     "|".join(big_kw), case=False, na=False)
                        big_grp = df_sh[is_big].groupby("date")[pct_col].sum()
                        big_grp = big_grp.sort_index().dropna()
                        if len(big_grp) >= 2:
                            big_rising = float(big_grp.iloc[-1]) > float(big_grp.iloc[-2])
                s2["大戶持股上升"] = big_rising
                if big_rising: score2 += 1

                # ── d. 收盤 >= MA20 且乖離 < bias_max%
                ma20_ok = False
                if ok_prc and not df_prc.empty and "Close" in df_prc.columns:
                    prc_c = df_prc["Close"].astype(float).dropna()
                    if len(prc_c) >= 20:
                        ma20   = float(prc_c.rolling(20).mean().iloc[-1])
                        last_p = float(prc_c.iloc[-1])
                        bias   = (last_p - ma20) / ma20 * 100
                        ma20_ok = (last_p >= ma20) and (0 <= bias < bias_max)
                s2["MA20乖離<5%"] = ma20_ok
                if ma20_ok: score2 += 1

                # ── e. 窒息量：成交量 < 5日均量 × vol_max_r
                vol_ok = False
                if ok_prc and not df_prc.empty and "Volume" in df_prc.columns:
                    vol    = df_prc["Volume"].astype(float).dropna()
                    if len(vol) >= 6:
                        vma5   = float(vol.iloc[-6:-1].mean())   # 前5日均量（不含今日）
                        last_v = float(vol.iloc[-1])
                        vol_ok = (vma5 > 0) and (last_v / vma5 < vol_max_r)
                s2["窒息量"] = vol_ok
                if vol_ok: score2 += 1

                # ── f. 近3日低點 >= 前10日低點（底部墊高不破低）
                hl_ok = False
                if ok_prc and not df_prc.empty and "Low" in df_prc.columns:
                    lo = df_prc["Low"].astype(float).dropna()
                    if len(lo) >= 13:
                        recent_low = float(lo.iloc[-3:].min())
                        prev_low   = float(lo.iloc[-13:-3].min())
                        hl_ok      = recent_low >= prev_low
                s2["低點墊高"] = hl_ok
                if hl_ok: score2 += 1

                # ─── 第三道：財報趨勢（3 項評分）
                score3 = 0
                s3 = {}

                # 近3個月月營收 YoY > rev_yoy_min
                rev_rows = df_f[
                    df_f[name_col].str.contains("營業收入|Revenue", case=False, na=False) |
                    (df_f.get("type", pd.Series(dtype=str)).str.contains("Revenue", case=False, na=False)
                     if "type" in df_f.columns else False)
                ]
                rev_ok = False
                if not rev_rows.empty and "value" in rev_rows.columns:
                    rev_vals = pd.to_numeric(rev_rows["value"], errors="coerce").dropna()
                    if len(rev_vals) >= 6:
                        yoy = (rev_vals.iloc[-3:].values - rev_vals.iloc[-6:-3].values) / \
                              (rev_vals.iloc[-6:-3].values.clip(1e-9)) * 100
                        rev_ok = bool(np.all(yoy > rev_yoy_min))
                s3["月營收YoY"] = rev_ok
                if rev_ok: score3 += 1

                # 近2季毛利率 QoQ 皆正成長
                gm_qoq_ok = False
                if len(gm_vals) >= 3:
                    qoq = gm_vals.diff().dropna()
                    gm_qoq_ok = bool(qoq.iloc[-2:].gt(0).all())
                s3["毛利率QoQ+"] = gm_qoq_ok
                if gm_qoq_ok: score3 += 1

                # 最新季 EPS YoY > eps_yoy_min
                eps_yoy_ok = False
                if len(eps_vals) >= 5:
                    last_q   = float(eps_vals.iloc[-1])
                    yoy_q    = float(eps_vals.iloc[-5]) if len(eps_vals) >= 5 else 0
                    if abs(yoy_q) > 1e-9:
                        eps_yoy_ok = (last_q - yoy_q) / abs(yoy_q) * 100 > eps_yoy_min
                s3["EPS YoY+"] = eps_yoy_ok
                if eps_yoy_ok: score3 += 1

                # 取得名稱
                sid_name = sid
                if ok_si:
                    m = df_si[df_si["stock_id"].astype(str).str.strip() == str(sid).strip()]
                    if not m.empty:
                        sid_name = str(m["stock_name"].iloc[0])

                results.append({
                    "代號":    sid,
                    "名稱":    sid_name,
                    "EPS_TTM": round(eps_ttm,  2) if not np.isnan(eps_ttm)  else None,
                    "P/E":     round(pe_val,   1) if not np.isnan(pe_val)   else None,
                    "毛利率%": round(gm_latest, 1) if not np.isnan(gm_latest) else None,
                    "法人買超%":    round(inst_buy_pct, 1) if not np.isnan(inst_buy_pct) else None,
                    "融資5日變動%": round(mg_chg, 1) if not np.isnan(mg_chg) else None,
                    "K值": None,  # KD 計算移至技術評分內
                    "MA20乖離%": round(
                        (float(df_prc["Close"].iloc[-1]) -
                         float(df_prc["Close"].rolling(20).mean().iloc[-1])) /
                        max(float(df_prc["Close"].rolling(20).mean().iloc[-1]), 1) * 100, 2
                    ) if ok_prc and not df_prc.empty and len(df_prc) >= 20 and "Close" in df_prc.columns else None,
                    "量比": round(
                        float(df_prc["Volume"].iloc[-1]) /
                        max(float(df_prc["Volume"].iloc[-6:-1].mean()), 1), 2
                    ) if ok_prc and not df_prc.empty and "Volume" in df_prc.columns and len(df_prc) >= 6 else None,
                    "籌碼得分": score2,
                    "財報得分": score3,
                    "總得分":   score2 + score3,
                    **{f"籌碼_{k}": v for k, v in s2.items()},
                    **{f"財報_{k}": v for k, v in s3.items()},
                })

            prog.empty()
            st.session_state["scan_results"] = results
            st.session_state["scan_done"]    = True
            st.success(f"✅ 掃描完成！基本面通過 {len(results)} 檔")


    if st.session_state.get("scan_done") and st.session_state.get("scan_results"):
        results = st.session_state["scan_results"]
        df_res  = pd.DataFrame(results)

        st.success(f"✅ 基本面母體：{len(df_res)} 檔（通過 EPS / P/E / 毛利率 篩選）")

        if df_res.empty:
            st.warning("無基本面通過標的，請調整篩選條件")
        else:
            # ══════════════════════════════
            # 表格一：籌碼與均線（6項各自獨立）
            # ══════════════════════════════
            st.markdown("<div class='sec-title'>📊 籌碼與均線評分（每項獨立）</div>",
                        unsafe_allow_html=True)
            st.markdown(
                "<div class='infobox'>以下為符合基本條件的所有標的。"
                "a~f 六項條件各自獨立，✅=符合 ❌=不符合，"
                "可依「籌碼得分」排序找出籌碼最健康的標的。</div>",
                unsafe_allow_html=True
            )
            cc1, cc2 = st.columns([2,1])
            min_chip = cc1.slider("顯示籌碼得分 ≥", 0, 6, 0, key="r_chip2")
            sort_chip = cc2.selectbox("排序", ["籌碼得分","EPS_TTM","毛利率%","代號"], key="r_csort")

            chip_cols = ["代號","名稱","EPS_TTM","P/E","毛利率%","融資5日變動%","法人買超%","MA20乖離%","量比"]
            label_map = {
                "籌碼_融資5日變動": "a.融資減少>3%",
                "籌碼_法人買超":    "b.法人買超>10%",
                "籌碼_大戶持股上升":"c.大戶持股↑",
                "籌碼_MA20乖離<5%": "d.站上MA20乖離<5%",
                "籌碼_窒息量":      "e.窒息量<60%",
                "籌碼_低點墊高":    "f.低點不破低",
                "籌碼得分":         "籌碼得分/6",
            }
            df_chip = df_res.copy()
            for old_col, new_label in label_map.items():
                if old_col in df_chip.columns:
                    df_chip[new_label] = df_chip[old_col].map(
                        lambda x: "✅" if x else "❌"
                    )
            df_chip["籌碼得分/6"] = df_chip["籌碼得分"].astype(str) + "/6"
            show_chip_cols = chip_cols + [v for v in label_map.values() if v in df_chip.columns]
            df_chip_show = df_chip[df_chip["籌碼得分"] >= min_chip].sort_values(
                sort_chip, ascending=False if sort_chip=="籌碼得分" else True, na_position="last"
            )
            st.markdown(
                df_to_html(df_chip_show[[c for c in show_chip_cols if c in df_chip_show.columns]], height=380),
                unsafe_allow_html=True
            )
            st.download_button("⬇️ 下載籌碼表 CSV",
                df_chip_show[[c for c in show_chip_cols if c in df_chip_show.columns]].to_csv(index=False, encoding='utf-8-sig'),
                file_name="chip_result.csv", mime="text/csv", key="dl_chip")

            # 加入監控按鈕（籌碼表格旁）
            if not df_chip_show.empty:
                add_from_chip = st.multiselect(
                    "從籌碼結果加入監控",
                    [f"{r['代號']} {r['名稱']}" for _,r in df_chip_show.iterrows()],
                    label_visibility="collapsed",
                    placeholder="選擇標的加入監控清單...",
                    key="chip_add_sel"
                )
                if st.button("⭐ 加入監控", key="chip_add_btn"):
                    added = 0
                    for item in add_from_chip:
                        code = item.split()[0]
                        name = " ".join(item.split()[1:])
                        if not any(w["id"]==code for w in st.session_state.watchlist_scan):
                            st.session_state.watchlist_scan.append({"id":code,"name":name})
                            added += 1
                    if added > 0:
                        save_watchlist_to_github(st.session_state.watchlist, st.session_state.watchlist_scan, {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0}, st.session_state.get("reserve_list", []))
                        st.toast(f"✅ 已加入 {added} 檔到監控清單")

            st.markdown("<br>", unsafe_allow_html=True)

            # ══════════════════════════════
            # 表格二：財報分析（3項各自獨立）
            # ══════════════════════════════
            st.markdown("<div class='sec-title'>📋 財報分析評分（每項獨立）</div>",
                        unsafe_allow_html=True)
            st.markdown(
                "<div class='infobox'>同樣母體，三項財報條件各自獨立，"
                "✅=符合 ❌=不符合，可依「財報得分」排序。</div>",
                unsafe_allow_html=True
            )
            fc1, fc2 = st.columns([2,1])
            min_fin = fc1.slider("顯示財報得分 ≥", 0, 3, 0, key="r_fin2")
            sort_fin = fc2.selectbox("排序", ["財報得分","EPS_TTM","毛利率%","代號"], key="r_fsort")

            fin_label_map = {
                "財報_月營收YoY": "①月營收YoY>10%",
                "財報_毛利率QoQ+":"②毛利率QoQ+",
                "財報_EPS YoY+":  "③EPS YoY>20%",
                "財報得分":        "財報得分/3",
            }
            df_fin2 = df_res.copy()
            for old_col, new_label in fin_label_map.items():
                if old_col in df_fin2.columns:
                    df_fin2[new_label] = df_fin2[old_col].map(
                        lambda x: "✅" if x else "❌"
                    )
            df_fin2["財報得分/3"] = df_fin2["財報得分"].astype(str) + "/3"
            show_fin_cols = chip_cols + [v for v in fin_label_map.values() if v in df_fin2.columns]
            df_fin_show = df_fin2[df_fin2["財報得分"] >= min_fin].sort_values(
                sort_fin, ascending=False if sort_fin=="財報得分" else True, na_position="last"
            )
            st.markdown(
                df_to_html(df_fin_show[[c for c in show_fin_cols if c in df_fin_show.columns]], height=380),
                unsafe_allow_html=True
            )
            st.download_button("⬇️ 下載財報表 CSV",
                df_fin_show[[c for c in show_fin_cols if c in df_fin_show.columns]].to_csv(index=False, encoding='utf-8-sig'),
                file_name="fin_result.csv", mime="text/csv", key="dl_fin")

            # 加入監控按鈕（財報表格旁）
            if not df_fin_show.empty:
                add_from_fin = st.multiselect(
                    "從財報結果加入監控",
                    [f"{r['代號']} {r['名稱']}" for _,r in df_fin_show.iterrows()],
                    label_visibility="collapsed",
                    placeholder="選擇標的加入監控清單...",
                    key="fin_add_sel"
                )
                if st.button("⭐ 加入監控", key="fin_add_btn"):
                    added = 0
                    for item in add_from_fin:
                        code = item.split()[0]
                        name = " ".join(item.split()[1:])
                        if not any(w["id"]==code for w in st.session_state.watchlist_scan):
                            st.session_state.watchlist_scan.append({"id":code,"name":name})
                            added += 1
                    if added > 0:
                        save_watchlist_to_github(st.session_state.watchlist, st.session_state.watchlist_scan, {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0}, st.session_state.get("reserve_list", []))
                        st.toast(f"✅ 已加入 {added} 檔到監控清單")

        # placeholder to keep old variable name for funnel chart below
        df_show = df_res  # keep for backward compat
        st.markdown(f"""
        <div class='infobox' style='display:none;'>
        </div>
        """, unsafe_allow_html=True)

        # ── 漏斗圖
        f1, f2 = st.columns([1, 3])
        with f1:
            fig_f = go.Figure(go.Funnel(
                y=["全市場", "基本面母體", "籌碼達標", "財報達標"],
                x=[1700, len(df_res),
                   len(df_res[df_res["籌碼得分"] >= min_chip]),
                   len(df_show)],
                textinfo="value+percent initial",
                marker=dict(color=["#1e3a5f","#00d4ff","#e040fb","#00e676"]),
                textfont=dict(color="#e8f4fd"),
                connector=dict(line=dict(color="#1e3a5f")),
            ))
            fig_f.update_layout(**base_layout("篩選漏斗", 280))
            st.plotly_chart(fig_f, width='stretch')

        with f2:
            # ── 結果表格
            display_cols = ["代號","名稱","EPS_TTM","P/E","毛利率%",
                            "法人買超%","融資5日變動%","籌碼得分","財報得分","總得分"]
            df_table = df_show[[c for c in display_cols if c in df_show.columns]].copy()
            st.markdown(
                df_to_html(df_table, height=360),
                unsafe_allow_html=True
            )
            # CSV 下載
            csv_data = df_table.to_csv(index=False, encoding='utf-8-sig')
            st.download_button(
                label="⬇️ 下載 CSV",
                data=csv_data,
                file_name="scan_result.csv",
                mime="text/csv",
                key="dl_csv"
            )

        # ── 個股評分卡 + 加入監控
        if not df_show.empty:
            st.markdown("<div class='sec-title'>📋 個股評分卡</div>",
                        unsafe_allow_html=True)
            for _, row in df_show[df_show["總得分"] > 0].sort_values("總得分", ascending=False).iterrows():
                with st.expander(
                    f"{row['代號']} {row['名稱']}  "
                    f"｜ 籌碼 {row['籌碼得分']}/6  財報 {row['財報得分']}/3  "
                    f"總分 {row['總得分']}/9",
                    expanded=False,
                ):
                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    with ec1:
                        st.markdown("**籌碼評分（6項）**")
                        for k in ["融資5日變動","法人買超","大戶持股上升",
                                  "MA20乖離<5%","窒息量","低點墊高"]:
                            ok_val = row.get(f"籌碼_{k}", False)
                            st.markdown(badge(bool(ok_val), k),
                                        unsafe_allow_html=True)
                    with ec2:
                        st.markdown("**財報評分（3項）**")
                        for k in ["月營收YoY","毛利率QoQ+","EPS YoY+"]:
                            ok_val = row.get(f"財報_{k}", False)
                            st.markdown(badge(bool(ok_val), k),
                                        unsafe_allow_html=True)
                    with ec3:
                        st.markdown("**快速操作**")
                        if st.button(
                            f"⭐ 加入監控",
                            key=f"add_{row['代號']}",
                            width='stretch',
                        ):
                            entry = {"id": row["代號"], "name": row["名稱"]}
                            if not any(
                                w["id"] == row["代號"]
                                for w in st.session_state.watchlist
                            ):
                                st.session_state.watchlist.append(entry)
                            # ★ 使用 toast 提示，不跳轉頁面
                            st.toast(f"✅ 已加入監控：{row['名稱']}")

    # ──────────────────────────────────────────────────────────────
    # ▌ TAB 2：持股監控
    # ──────────────────────────────────────────────────────────────
with tab3:
    st.markdown("<div class='sec-title'>📊 指揮中心 · 持倉管理</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>"
        "指揮中心是你每日唯一必須看的頁面。"
        "目標：<b>3分鐘內</b>確認持倉狀況，處理今天有沒有需要行動的事。"
        "<br>停損觸發 → 立刻處理。無警示 → 關掉繼續做自己的事。</div>",
        unsafe_allow_html=True
    )
    st.markdown("---")

    _acct    = load_account()
    _pf      = load_portfolio()
    _trd     = load_trades()
    _nm_map  = get_stock_name_map()  # {stock_id: stock_name}

    # ── 初始資金設定（若尚未設定，顯示輸入框）
    if _acct.get("initial_capital", 0) == 0:
        st.markdown("#### 💵 請先設定初始資金")
        _init_col1, _init_col2 = st.columns([2, 1])
        with _init_col1:
            _init_cap = st.number_input(
                "初始資金（元）", min_value=0, value=None,
                step=10000, format="%d", placeholder="請輸入初始資金（元）", key="init_cap_input"
            )
        with _init_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✅ 確認設定初始資金", key="set_init_cap"):
                _acct_new = {"initial_capital": float(_init_cap),
                             "cash": float(_init_cap), "realized_pnl": 0.0}
                save_account(_acct_new)
                st.success(f"初始資金 ${_init_cap:,} 已設定，現金帳戶同步啟動！")
                st.rerun()
    else:
        # ── 計算持倉即時市值
        _pos_market_value = 0.0
        _pos_cost_total   = 0.0
        _pf_rows          = []

        # 一次性讀取全市場籌碼事實（快取5分鐘，不重複IO）
        _chips_map = get_chips_facts_map()

        for _sid, _pos in _pf.items():
            _bp  = float(_pos.get("buy_price", 0))
            _qty = int(_pos.get("qty", 0))
            _sl  = float(_pos.get("stop_loss", 0))
            _sp  = float(_pos.get("stop_profit", 0))
            _bdt = _pos.get("buy_date", "—")
            if _qty <= 0 or _bp <= 0:
                continue
            try:
                _df_pf, _ok_pf = load_price_csv(_sid)
                _cp = float(_df_pf["Close"].iloc[-1]) if _ok_pf and not _df_pf.empty else _bp
                # 同步計算 20MA（供異常變盤因果律共用函數使用）
                if _ok_pf and not _df_pf.empty and len(_df_pf) >= 20:
                    _ma20_pf = float(pd.to_numeric(_df_pf["Close"], errors="coerce").dropna().tail(20).mean())
                else:
                    _ma20_pf = None
            except Exception:
                _cp = _bp
                _ma20_pf = None

            _cost   = calc_buy_cost(_bp, _qty)
            _inflow = calc_net_inflow(_cp, _qty)
            _profit, _roi = calc_net_profit(_bp, _cp, _qty)

            _pos_market_value += _inflow
            _pos_cost_total   += _cost

            # 讀取籌碼事實（融資增減 + 外資買超）
            _chip = _chips_map.get(_sid, {})
            _foreign_net = _chip.get("foreign_net",   None)
            _margin_chg  = _chip.get("margin_chg_pct", None)

            _pf_rows.append({
                "代號": _sid, "名稱": _nm_map.get(_sid, ""),
                "買入日期": _bdt, "現價": round(_cp, 2),
                "買入均價": round(_bp, 2), "持股數": _qty,
                "含費成本": round(_cost, 0), "扣稅實收": round(_inflow, 0),
                "未實現損益": round(_profit, 0), "ROI%": round(_roi, 2),
                "📉融資增減%": _margin_chg, "📡外資買超張": _foreign_net,
                "_sl": _sl, "_sp": _sp, "_cp": _cp, "_bp": _bp, "_ma20": _ma20_pf,
                "_strategy": _pos.get("strategy_type", "LONG"),  # 預設長線
            })
            try:
                del _df_pf
            except Exception:
                pass
            _gc.collect()

        # 帳戶統計
        _init   = _acct.get("initial_capital", 0.0)
        _cash   = _acct.get("cash", 0.0)
        _r_pnl  = _acct.get("realized_pnl", 0.0)
        _unreal = _pos_market_value - _pos_cost_total
        _total_assets = _cash + _pos_market_value
        _total_pnl    = _total_assets - _init
        _total_roi    = (_total_pnl / _init * 100) if _init > 0 else 0.0

        # ── 資金儀表板（5格）
        _a1, _a2, _a3, _a4, _a5 = st.columns(5)
        _a1.metric("💵 初始資金", f"${_init:,.0f}")
        _a2.metric("💰 可用現金", f"${_cash:,.0f}",
                   delta=f"{(_cash/_init*100-100):+.1f}%" if _init else None)
        _a3.metric("📈 持倉市值", f"${_pos_market_value:,.0f}",
                   delta=f"未實現 {_unreal:+,.0f}")
        _a4.metric("🏦 總資產", f"${_total_assets:,.0f}",
                   delta=f"{_total_pnl:+,.0f}")
        _a5.metric("📊 總投報率",
                   f"{_total_roi:+.2f}%",
                   delta=f"已實現 {_r_pnl:+,.0f}",
                   delta_color="normal")

        # ════════════════════════════════════════════════════
        # ▌ 停損/停利動態預警
        # ════════════════════════════════════════════════════
        # ════════════════════════════════════════════════════
        # ▌ 即時龍頭站月線掃描（供決策智庫使用）
        # ════════════════════════════════════════════════════
        @st.cache_data(ttl=1800, show_spinner=False)
        def _scan_leaders_sma20_live():
            """
            即時讀取 watch_list.json 四大板塊龍頭，動態計算站上 SMA20 比例。
            快取30分鐘，避免每次刷新都重跑17檔 load_price_csv。
            """
            import json as _j, os as _os
            _wl_path = _os.path.join("data", "watch_list.json")
            _wl = {}
            if _os.path.exists(_wl_path):
                try:
                    with open(_wl_path, "r", encoding="utf-8") as _f:
                        _wl = _j.load(_f)
                except Exception:
                    pass
            _seen, _all_ids = set(), []
            for _key in ("ai_semi", "ai_infra", "next_gen", "shipping_fin"):
                for _sid in _wl.get(_key, []):
                    if _sid not in _seen:
                        _seen.add(_sid)
                        _all_ids.append(_sid)
            _above, _total = 0, 0
            for _sid in _all_ids:
                try:
                    _df_l, _ok_l = load_price_csv(_sid)
                    if not _ok_l or _df_l.empty or len(_df_l) < 20:
                        continue
                    _closes = pd.to_numeric(_df_l["Close"], errors="coerce").dropna()
                    if len(_closes) < 20:
                        continue
                    _sma20 = float(_closes.tail(20).mean())
                    if float(_closes.iloc[-1]) >= _sma20:
                        _above += 1
                    _total += 1
                    del _df_l
                    import gc; gc.collect()
                except Exception:
                    continue
            return _above, _total

        _ldr_above, _ldr_total = _scan_leaders_sma20_live()
        _mkt_weak = (_ldr_above / _ldr_total < 0.5) if _ldr_total else False

        # ════════════════════════════════════════════════════
        # ▌ 持股監控 Expander 卡片（每持倉一張，長短雙軌）
        # ════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown(
            f"<div style='color:#7fb3d3;font-size:.82rem;margin-bottom:8px;'>"
            f"📊 市場權值龍頭站上月線：<b style='color:#e8f4fd;'>{_ldr_above} / {_ldr_total}</b> 檔"
            f"　｜　{'⚠️ 結構偏弱' if _mkt_weak else '✅ 結構健康'}"
            f"</div>",
            unsafe_allow_html=True
        )

        # ── 全局視角切換（影響下方所有持倉卡片的決策建議）
        _view_mode   = st.radio(
            "決策視角",
            ["🛡️ 長線防空洞視角", "⚡ 短線游擊視角"],
            horizontal=True,
            key="tab3_view_mode",
            help="長線視角：屏蔽短線雜訊，聚焦籌碼沉澱與底部布局｜短線視角：快狠準，觸及壓力/支撐即行動"
        )
        _is_long_view = "長線" in _view_mode

        st.markdown("#### 📋 持股監控卡片（含籌碼決策智庫）")

        if not _pf_rows:
            st.info("📭 目前無持倉，請在下方帳務登記櫃檯新增買入紀錄。")
        else:
            for _r in _pf_rows:
                _r_sid    = _r["代號"]
                _r_name   = _r["名稱"] or _r_sid
                _r_cp     = _r["_cp"]
                _r_bp     = _r["_bp"]
                _r_sl     = _r["_sl"]
                _r_sp     = _r.get("_sp", 0)
                _r_profit = _r["未實現損益"]
                _r_roi    = _r["ROI%"]
                _r_margin = _r.get("📉融資增減%")
                _r_fgn    = _r.get("📡外資買超張")
                _r_qty    = _r["持股數"]
                _r_ma20   = _r.get("_ma20")

                # 全局視角切換決定本次決策邏輯（不固定每支股票的策略）
                _strat_label = "🛡️ 長線防空洞視角" if _is_long_view else "⚡ 短線游擊視角"
                _pnl_color   = "#ff4444" if _r_profit > 0 else "#00cc66" if _r_profit < 0 else "#e8f4fd"

                # ── 呼叫全域共用的異常變盤因果律決策函數
                # 注意：strategy_type 用持倉實際儲存的策略標籤（_strategy），
                #      不是視角切換（_is_long_view 只影響下方決策智庫文字，
                #      異常變盤判定仍以持倉真實設定的長短線屬性為準）
                _r_anomaly = check_anomaly_variant(
                    stock_id=f"{_r_sid} {_r_name}",
                    strategy_type=_r.get("_strategy", "LONG"),
                    current_price=_r_cp,
                    ma20=_r_ma20,
                    foreign_buy=_r_fgn,
                    margin_change=_r_margin,
                )

                with st.expander(
                    f"{'📈' if _r_profit >= 0 else '📉'} {_r_sid} {_r_name}　｜　"
                    f"{_strat_label}　｜　"
                    f"{'獲利' if _r_profit >= 0 else '虧損'} {abs(_r_profit):,.0f} 元（{_r_roi:+.2f}%）",
                    expanded=True
                ):
                    # ── 異常變盤因果律警告（最上方優先顯示，覆蓋實體資產安全防禦）
                    if _r_anomaly["triggered"]:
                        if _r_anomaly["level"] == "AS_RETREAT":
                            st.error(_r_anomaly["message"])
                            if st.button("🗑️ 物理除名（出清後從帳本移除）",
                                         key=f"pf_anomaly_remove_{_r_sid}"):
                                _pf_after = load_portfolio()
                                _pf_after.pop(_r_sid, None)
                                save_portfolio(_pf_after)
                                st.success(f"已將 {_r_sid} {_r_name} 從持倉物理除名")
                                st.rerun()
                        elif _r_anomaly["level"] == "DIAMOND_BUY":
                            st.info(_r_anomaly["message"])

                    # ── 四格指標
                    _c1, _c2, _c3, _c4 = st.columns(4)
                    _c1.metric(
                        "現價 / 含費均價",
                        f"{_r_cp:,.2f}",
                        f"{_r_roi:+.2f}%",
                        delta_color="normal"
                    )
                    _mg_label = f"{_r_margin:+.2f}%" if _r_margin is not None else "—"
                    _mg_delta = "散戶退場✅" if (_r_margin or 0) < 0 else "散戶進場⚠️" if (_r_margin or 0) > 0 else "—"
                    _c2.metric("📉 融資增減", _mg_label, _mg_delta,
                               delta_color="inverse" if (_r_margin or 0) < 0 else "normal")
                    _fgn_label = f"{_r_fgn:+,.0f} 張" if _r_fgn is not None else "—"
                    _fgn_delta = "主力吸籌✅" if (_r_fgn or 0) > 0 else "主力調節⚠️" if (_r_fgn or 0) < 0 else "—"
                    _c3.metric("📡 外資動能", _fgn_label, _fgn_delta,
                               delta_color="normal" if (_r_fgn or 0) > 0 else "inverse")
                    _c4.metric(
                        "持股 / 均價",
                        f"{_r_qty:,} 股",
                        f"均價 {_r_bp:,.2f}"
                    )

                    # ══════════════════════════════
                    # 🛡️ LONG 長線防空洞決策軌
                    # ══════════════════════════════
                    if _is_long_view:
                        # 停損/停利空間計算
                        _profit_space = ((_r_sp - _r_bp) / _r_bp * 100) if _r_sp > 0 and _r_bp > 0 else None
                        _loss_space   = ((_r_sl - _r_bp) / _r_bp * 100) if _r_sl > 0 and _r_bp > 0 else None

                        # 短線破位等雜訊：長線全面屏蔽，只看籌碼和月線
                        _chips_healthy = (_r_margin is None or _r_margin <= 2) and (_r_fgn is None or _r_fgn >= -500)

                        # 情境A：底部洗盤（虧損+融資大減）
                        if _r_bp > _r_cp and (_r_sl <= 0 or _r_cp > _r_sl) and (_r_margin or 0) <= -2.0:
                            _sp_txt = f"｜停利目標 {_r_sp:.2f}（空間 +{_profit_space:.1f}%）" if _profit_space else ""
                            st.info(
                                f"💡 **{_r_sid} 長線防守 Facts：底部洗盤期確認**\n\n"
                                f"股價低於買入成本（虧損 {abs(_r_profit):,.0f} 元），"
                                f"但「**融資大幅割肉減肥 {abs(_r_margin):.2f}%**」！"
                                f"高位散戶浮額正在被清洗出去，底部特徵明確。"
                                f"0 槓桿純現貨雷打不動，此處為黃金支撐布局點{_sp_txt}，"
                                f"肉身扛過去，靜待大戶換股東風！"
                            )

                        # 情境B：外資護盤+獲利
                        elif _r_cp > _r_bp and (_r_fgn or 0) > 0:
                            _sp_txt = f"｜距停利 +{_profit_space:.1f}%" if _profit_space else ""
                            st.info(
                                f"🔥 **{_r_sid} 長線續抱信號：外資真金白銀護盤**\n\n"
                                f"股價已脫離買入成本區（獲利 {_r_profit:+,.0f} 元）！"
                                f"今日外資**大舉買超 {_r_fgn:,.0f} 張**，多頭骨架剛性{_sp_txt}。"
                                f"穩坐釣魚台優雅續抱，靜待撞擊前高天花板時的終極提款信號！"
                            )

                        # 情境C：市場結構偏弱+虧損
                        elif _mkt_weak and _r_profit < 0:
                            _sl_txt = f"停損防線 {_r_sl:.2f}" if _r_sl > 0 else "停損未設定（建議補設）"
                            st.info(
                                f"💡 **{_r_sid} 長線耐心等待：市場結構修復中**\n\n"
                                f"即時龍頭站月線 **{_ldr_above}/{_ldr_total}** 檔，結構偏弱。"
                                f"長線部位全面屏蔽短期雜訊，{_sl_txt}，"
                                f"現貨 0 槓桿肉身抗震，靜待結構修復！"
                            )

                        # 情境D：正常持倉
                        else:
                            _status = "獲利續抱中" if _r_profit >= 0 else "正常回撤中"
                            st.success(
                                f"✅ **{_r_sid} 長線防空洞｜{_status}**　"
                                f"籌碼結構健康，現貨部位**鎖進保險箱雷打不動**。"
                                f"龍頭站月線 {_ldr_above}/{_ldr_total}，"
                                f"靜待下一個籌碼分離訊號。"
                            )

                    # ══════════════════════════════
                    # ⚡ SHORT 短線游擊突擊隊決策軌
                    # ══════════════════════════════
                    else:
                        # 籌碼分離偵測（外資大賣 OR 融資大增）
                        _chips_sep = ((_r_fgn or 0) < -500) or ((_r_margin or 0) > 3.0)

                        # 停損觸發
                        if _r_sl > 0 and _r_cp <= _r_sl:
                            st.error(
                                f"🚨 **{_r_sid} 短線突擊隊強制令：停損觸發**\n\n"
                                f"現價 **{_r_cp:.2f}** 已跌破停損防線 **{_r_sl:.2f}**！"
                                f"虧損 {abs(_r_profit):,.0f} 元。"
                                f"**請立刻全數清空平倉，嚴禁抱成長期套牢！**"
                            )

                        # 停利觸發
                        elif _r_sp > 0 and _r_cp >= _r_sp:
                            st.warning(
                                f"🎯 **{_r_sid} 短線突擊隊提款令：停利觸發**\n\n"
                                f"現價 **{_r_cp:.2f}** 已達停利目標 **{_r_sp:.2f}**！"
                                f"獲利 {_r_profit:+,.0f} 元（{_r_roi:+.2f}%）。"
                                f"**請立刻全數平倉出場，落袋為安！**"
                            )

                        # 籌碼分離警告
                        elif _chips_sep:
                            st.error(
                                f"🚨 **{_r_sid} 短線｜高危籌碼分離警告（以退為進）**\n\n"
                                f"偵測到大戶與散戶軌道嚴重分離──"
                                f"外資 {(_r_fgn or 0):+,.0f} 張｜融資增減 {(_r_margin or 0):+.2f}%。"
                                f"短線流動性優勢已消失，**請執行以退為進清倉令**，"
                                f"落袋或認賠，出清後系統剔除追蹤名單，絕不留戀！"
                            )

                        # 觀望中
                        else:
                            _upper = _r_sp if _r_sp > 0 else round(_r_bp * 1.08, 2)
                            _lower = _r_sl if _r_sl > 0 else round(_r_bp * 0.95, 2)
                            st.info(
                                f"⚡ **{_r_sid} 短線游擊｜橫盤監控中**\n\n"
                                f"現價 {_r_cp:.2f}　{('獲利' if _r_profit >= 0 else '虧損')} "
                                f"{abs(_r_profit):,.0f} 元（{_r_roi:+.2f}%）\n\n"
                                f"🔴 上方壓力：**{_upper:.2f}**　"
                                f"🟢 下方防守：**{_lower:.2f}**\n\n"
                                f"籌碼：外資 {f'{_r_fgn:+,.0f}張' if _r_fgn is not None else '—'}"
                                f"｜融資 {f'{_r_margin:+.2f}%' if _r_margin is not None else '—'}。"
                                f"嚴禁將短線抱成長期套牢，隨時備好閃人防禦！"
                            )

        st.markdown("---")
        st.markdown("#### 📋 持倉明細彙總表（含籌碼）")
        if _pf_rows:
            _rows_html = ""
            for _r in _pf_rows:
                _pnl_color = "#ff4444" if _r["未實現損益"] > 0 else "#00cc66" if _r["未實現損益"] < 0 else "#e8f4fd"
                _strat = _r.get("_strategy", "LONG")
                _strat_badge = (
                    "<span style='background:#1a3a5c;color:#00d4ff;font-size:.7rem;"
                    "padding:2px 6px;border-radius:4px;'>🛡️ 長線</span>"
                    if _strat == "LONG" else
                    "<span style='background:#3a1a1a;color:#ff9800;font-size:.7rem;"
                    "padding:2px 6px;border-radius:4px;'>⚡ 短線</span>"
                )
                _mg = _r.get("📉融資增減%")
                if _mg is None:
                    _mg_str, _mg_color = "—", "#7fb3d3"
                else:
                    _mg_color = "#00cc66" if _mg <= -2 else "#ff4444" if _mg >= 2 else "#e8f4fd"
                    _mg_str   = f"{_mg:+.2f}%"
                _fgn = _r.get("📡外資買超張")
                if _fgn is None:
                    _fgn_str, _fgn_color = "—", "#7fb3d3"
                else:
                    _fgn_color = "#ff4444" if _fgn > 0 else "#00cc66" if _fgn < 0 else "#e8f4fd"
                    _fgn_str   = f"{_fgn:+,.0f}"

                _rows_html += (
                    f"<tr style='border-bottom:1px solid #1e3a5f;'>"
                    f"<td style='padding:8px;'>{_r['代號']}</td>"
                    f"<td style='padding:8px;color:#9fb8d4;'>{_r['名稱']}</td>"
                    f"<td style='padding:8px;'>{_strat_badge}</td>"
                    f"<td style='padding:8px;'>{_r['買入日期']}</td>"
                    f"<td style='padding:8px;text-align:right;'>{_r['現價']:,.2f}</td>"
                    f"<td style='padding:8px;text-align:right;'>{_r['買入均價']:,.4f}</td>"
                    f"<td style='padding:8px;text-align:right;'>{_r['持股數']:,}</td>"
                    f"<td style='padding:8px;text-align:right;'>{_r['含費成本']:,.0f}</td>"
                    f"<td style='padding:8px;text-align:right;color:{_pnl_color};font-weight:600;'>{_r['未實現損益']:+,.0f}</td>"
                    f"<td style='padding:8px;text-align:right;color:{_pnl_color};font-weight:600;'>{_r['ROI%']:+.2f}%</td>"
                    f"<td style='padding:8px;text-align:right;color:{_mg_color};font-weight:600;'>{_mg_str}</td>"
                    f"<td style='padding:8px;text-align:right;color:{_fgn_color};font-weight:600;'>{_fgn_str}</td>"
                    f"</tr>"
                )
            _headers = ["代號","名稱","策略","買入日期","現價","含費均價","持股數",
                        "含費成本","未實現損益","ROI%","📉融資增減%","📡外資買超張"]
            _head_html = "".join(
                f"<th style='padding:8px;text-align:{'left' if i<4 else 'right'};"
                f"border-bottom:2px solid #1e3a5f;color:#7fb3d3;font-size:.78rem;"
                f"white-space:nowrap;position:sticky;top:0;background:#0f2027;z-index:1;'>{h}</th>"
                for i, h in enumerate(_headers)
            )
            _pf_table_h = 40 + 12 * 34
            st.markdown(
                f"<div style='overflow-x:auto;overflow-y:auto;max-height:{_pf_table_h}px;"
                f"border:1px solid #1e3a5f;border-radius:6px;'>"
                f"<table style='width:100%;border-collapse:collapse;font-size:.83rem;color:#e8f4fd;'>"
                f"<thead><tr style='background:#0f2027;'>{_head_html}</tr></thead>"
                f"<tbody style='background:rgba(255,255,255,0.02);'>{_rows_html}</tbody>"
                f"</table></div>",
                unsafe_allow_html=True
            )
        else:
            st.caption("目前無持倉")        # ════════════════════════════════════════════════════
        # ════════════════════════════════════════════════════
        # ▌ 歷史交易紀錄（篩選 + 固定高度 + 匯出Excel）
        # ════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("#### 📜 歷史交易紀錄")
        if _trd:
            # ── 篩選控制列
            _f1, _f2, _f3, _f4 = st.columns([2, 2, 2, 1])
            _all_sids = sorted(set(t.get("stock_id","") for t in _trd if t.get("stock_id")))
            with _f1:
                _filter_sids = st.multiselect("篩選股票代號", options=_all_sids,
                                               default=[], key="trd_filter_sid",
                                               placeholder="全部")
            with _f2:
                _filter_date_start = st.date_input("起始日期", value=None,
                                                    key="trd_date_start")
            with _f3:
                _filter_date_end   = st.date_input("結束日期", value=None,
                                                    key="trd_date_end")
            with _f4:
                _filter_action = st.selectbox("動作", ["全部","買入","賣出"],
                                               key="trd_filter_action")

            # ── 套用篩選
            _trd_filtered = _trd[:]
            if _filter_sids:
                _trd_filtered = [t for t in _trd_filtered if t.get("stock_id") in _filter_sids]
            if _filter_date_start:
                _trd_filtered = [t for t in _trd_filtered if str(t.get("date","")) >= str(_filter_date_start)]
            if _filter_date_end:
                _trd_filtered = [t for t in _trd_filtered if str(t.get("date","")) <= str(_filter_date_end)]
            if _filter_action != "全部":
                _trd_filtered = [t for t in _trd_filtered if t.get("action") == _filter_action]

            # ── 建立 HTML 表格（固定高度，超過顯示捲軸）
            _col_map = {"date":"日期","action":"動作","stock_id":"代號","stock_name":"名稱",
                        "price":"成交價","qty":"股數","fee":"手續費",
                        "tax":"證交稅","amount":"成交金額","hold_cost":"持有成本",
                        "realized_pnl":"實現損益","roi_pct":"ROI%"}
            _df_trd_base = pd.DataFrame(_trd_filtered) if _trd_filtered else pd.DataFrame()
            _trd_keys    = [k for k in _col_map if not _df_trd_base.empty and k in _df_trd_base.columns]
            _trd_headers = [_col_map[k] for k in _trd_keys]

            _trd_rows_html = ""
            for _t in reversed(_trd_filtered):
                _action_color = "#ff4444" if _t.get("action") == "買入" else "#00cc66"
                _row = ""
                for _k in _trd_keys:
                    _v    = _t.get(_k)
                    _disp = "—" if _v is None else _v
                    _style = ""
                    if _k == "action":
                        _style = f"color:{_action_color};font-weight:600;"
                    elif _k in ("realized_pnl","roi_pct") and isinstance(_v, (int,float)):
                        _style = "color:#ff4444;font-weight:600;" if _v > 0 else "color:#00cc66;font-weight:600;" if _v < 0 else ""
                    if isinstance(_disp, float):
                        _disp = f"{_disp:+.2f}" if _k == "roi_pct" else f"{_disp:,.2f}" if _k == "price" else f"{_disp:,.0f}"
                    elif isinstance(_disp, int):
                        _disp = f"{_disp:,}"
                    _align = "left" if _k in ("date","action","stock_id") else "right"
                    _row += f"<td style='padding:7px 8px;text-align:{_align};white-space:nowrap;{_style}'>{_disp}</td>"
                _bg = "background:rgba(255,255,255,0.04);" if _trd_filtered.index(_t) % 2 == 0 else ""
                _trd_rows_html += f"<tr style='border-bottom:1px solid #1a2f44;{_bg}'>{_row}</tr>"

            _trd_head = "".join(
                f"<th style='padding:8px;text-align:{'left' if h in ('日期','動作','代號') else 'right'};"
                f"border-bottom:2px solid #1e3a5f;color:#7fb3d3;font-size:.78rem;"
                f"white-space:nowrap;position:sticky;top:0;background:#0f2027;z-index:1;'>{h}</th>"
                for h in _trd_headers
            )
            # 固定高度容器（約12列，每列32px = 384px + header 40px）
            _ROW_H = 32
            _HEADER_H = 40
            _VISIBLE_ROWS = 12
            _table_h = _HEADER_H + _VISIBLE_ROWS * _ROW_H
            st.markdown(
                f"<div style='overflow-y:auto;max-height:{_table_h}px;border:1px solid #1e3a5f;border-radius:6px;'>"
                f"<table style='width:100%;border-collapse:collapse;font-size:.83rem;color:#e8f4fd;'>"
                f"<thead><tr>{_trd_head}</tr></thead>"
                f"<tbody>{_trd_rows_html}</tbody>"
                f"</table></div>",
                unsafe_allow_html=True
            )
            st.caption(f"共 {len(_trd_filtered)} 筆（篩選後）")

            # ── 匯出 Excel
            try:
                import io as _io
                _df_export = pd.DataFrame(_trd_filtered).rename(columns=_col_map)
                _export_cols = [v for v in _col_map.values() if v in _df_export.columns]
                _df_export   = _df_export[_export_cols]
                _buf = _io.BytesIO()
                # 優先用 xlsxwriter，備援用 openpyxl
                try:
                    with pd.ExcelWriter(_buf, engine="xlsxwriter") as _writer:
                        _df_export.to_excel(_writer, sheet_name="交易紀錄", index=False)
                except Exception:
                    with pd.ExcelWriter(_buf, engine="openpyxl") as _writer:
                        _df_export.to_excel(_writer, sheet_name="交易紀錄", index=False)
                _buf.seek(0)
                st.download_button(
                    "📥 匯出 Excel",
                    data=_buf,
                    file_name=f"trades_{datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="export_trades_excel"
                )
            except Exception as _xe:
                st.caption(f"匯出失敗：{_xe}")

            # ── 統計
            _real_trades = [t for t in _trd if t.get("action") == "賣出"]
            if _real_trades:
                _sum_pnl = sum(t.get("realized_pnl", 0) or 0 for t in _real_trades)
                _avg_roi = sum(t.get("roi_pct", 0) or 0 for t in _real_trades) / len(_real_trades)
                from datetime import datetime as _dt2
                _this_month = _dt2.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m")
                _month_trades = [t for t in _real_trades if str(t.get("date","")).startswith(_this_month)]
                _month_pnl = sum(t.get("realized_pnl", 0) or 0 for t in _month_trades)
                st.caption(
                    f"累計已實現損益：**${_sum_pnl:,.0f}**　｜　"
                    f"平均ROI：**{_avg_roi:+.2f}%**　｜　共 {len(_real_trades)} 筆賣出"
                )
                _month_color = "#ff4444" if _month_pnl >= 0 else "#00cc66"
                st.markdown(
                    f"<div style='padding:8px 0;font-size:.9rem;'>"
                    f"📈 <b>本月（{_this_month}）累計已實現純利潤（已扣稅費）：</b>"
                    f"<b style='color:{_month_color};font-size:1.1rem;'>${_month_pnl:,.0f}</b>"
                    f"　（{len(_month_trades)} 筆賣出）"
                    f"</div>",
                    unsafe_allow_html=True
                )
        else:
            st.caption("尚無交易紀錄")

    # ════════════════════════════════════════════════════════
    # ▌ 帳務登記櫃檯（買入 / 賣出）
    # ════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("#### 📥 指揮官帳務登記櫃檯")

    _tab_buy, _tab_sell, _tab_manage = st.tabs(["🟢 買入登記", "🔴 賣出登記", "⚙️ 帳戶管理"])

    with _tab_buy:
        _buy_form_key = f"buy_form_{st.session_state.get('buy_count', 0)}"
        with st.form(_buy_form_key):
            st.markdown("**📌 新增買入紀錄**")
            # ── 戰略定位：長線防空洞 or 短線游擊隊（置頂，最重要的決策）
            _b_strategy = st.radio(
                "戰略定位",
                ["🛡️ 長線資產防空洞", "⚡ 短線游擊突擊隊"],
                horizontal=True,
                key="buy_strategy_radio",
                help="長線：忽略短期雜訊，停損空間寬；短線：快狠準，觸及停損/停利立即出場"
            )
            _b_strategy_val = "LONG" if "長線" in _b_strategy else "SHORT"

            _bc1, _bc2, _bc3 = st.columns(3)
            with _bc1:
                _b_sid  = st.text_input("股票代號", placeholder="如 2330")
                _b_date = st.date_input("買入日期",
                                         value=datetime.now(ZoneInfo("Asia/Taipei")).date())
                _b_bp   = st.number_input("買入均價", min_value=0.0, value=None, step=0.5, format="%.2f", placeholder="輸入買入均價")
            with _bc2:
                _b_qty  = st.number_input("買入股數", min_value=0, value=None, step=1000, placeholder="輸入股數（整數）")
                _b_sl   = st.number_input("自訂停損價", min_value=0.0, value=None, step=0.5, format="%.2f", placeholder="選填")
                _b_sp   = st.number_input("自訂停利價", min_value=0.0, value=None, step=0.5, format="%.2f", placeholder="選填")
            with _bc3:
                if (_b_bp or 0) > 0 and (_b_qty or 0) > 0:
                    _b_cost    = calc_buy_cost(_b_bp, _b_qty)
                    _b_fee     = _calc_fee(_b_bp, _b_qty)
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.info(
                        "📊 **預估摩擦成本**\n\n"
                        f"買入金額：${_b_bp*_b_qty:,.0f}\n\n"
                        f"手續費（6折）：${_b_fee:,.0f}\n\n"
                        f"**含費總成本：${_b_cost:,.0f}**"
                    )

            _b_submit = st.form_submit_button("💾 確認買入登記", type="primary")
            if _b_submit:
                if not _b_sid.strip() or (_b_bp or 0) <= 0 or (_b_qty or 0) <= 0:
                    st.error("請填寫完整：股票代號、買入均價、買入股數")
                else:
                    _sid_key = _b_sid.strip()
                    # ── 防呆1：驗證股票代號存在於 stock_list.csv
                    _valid_sid = False
                    try:
                        _sl_chk, _sl_ok_chk = load_csv("stock_list.csv")
                        if _sl_ok_chk and not _sl_chk.empty:
                            _sl_chk["stock_id"] = _sl_chk["stock_id"].astype(str).str.strip()
                            _matched = _sl_chk[_sl_chk["stock_id"] == _sid_key]
                            if not _matched.empty:
                                _valid_sid = True
                        else:
                            _valid_sid = True  # 無清單時放行
                    except Exception:
                        _valid_sid = True  # 無法驗證時放行

                    if not _valid_sid:
                        st.error(f"⚠️ 股票代號【{_sid_key}】查無此股，請確認代號是否正確（台股4位數字）")
                    else:
                        _b_cost_final = calc_buy_cost(_b_bp, int(_b_qty))
                        _acct_now = load_account()
                        # ── 防呆2：現金不足
                        if _acct_now.get("initial_capital", 0) > 0 and _acct_now.get("cash", 0) < _b_cost_final:
                            st.error(f"⚠️ 可用現金 ${_acct_now['cash']:,.0f} 不足以支付含費成本 ${_b_cost_final:,.0f}")
                        else:
                            # ── 更新持倉（WAC移動加權平均）
                            _pf_now = load_portfolio()
                        if _sid_key in _pf_now:
                            # ── 加碼：移動加權平均法（WAC），含手續費的真實成本
                            _old = _pf_now[_sid_key]
                            _old_qty = _old["qty"]
                            _old_avg = _old["buy_price"]
                            # 舊總成本 = 舊均價×舊股數 + 舊批次的手續費
                            # 注意：舊均價已是「含費均價」（在之前登記時就已含費攤入），
                            # 所以舊總成本直接用 舊均價×舊股數 即可（費用已攤入均價）
                            _old_total_cost = _old_avg * _old_qty
                            # 新批次總成本 = 新買入金額 + 新批次手續費（剛性含費成本）
                            _new_total_cost = calc_buy_cost(_b_bp, int(_b_qty))
                            _new_qty        = _old_qty + int(_b_qty)
                            # 更新後含費均價 = (舊含費總成本 + 新含費總成本) / 更新後總股數
                            _new_avg_price  = (_old_total_cost + _new_total_cost) / _new_qty
                            _pf_now[_sid_key]["buy_price"] = round(_new_avg_price, 4)
                            _pf_now[_sid_key]["qty"] = _new_qty
                            if _b_sl > 0: _pf_now[_sid_key]["stop_loss"] = _b_sl
                            if _b_sp > 0: _pf_now[_sid_key]["stop_profit"] = _b_sp
                            _pf_now[_sid_key]["strategy_type"] = _b_strategy_val
                        else:
                            # 首次買入：含費均價 = 含費總成本 / 股數
                            _first_total = calc_buy_cost(_b_bp, int(_b_qty))
                            _first_avg   = _first_total / int(_b_qty)
                            _pf_now[_sid_key] = {
                                "buy_price":     round(_first_avg, 4),  # 含費均價
                                "qty":           int(_b_qty),
                                "stop_loss":     _b_sl or 0,
                                "stop_profit":   _b_sp or 0,
                                "buy_date":      str(_b_date),
                                "strategy_type": _b_strategy_val,  # LONG or SHORT
                            }
                        save_portfolio(_pf_now)

                        # 更新現金
                        _acct_now["cash"] = _acct_now.get("cash", 0) - _b_cost_final
                        save_account(_acct_now)

                        # 寫入交易紀錄
                        _trd_now = load_trades()
                        _trd_now.append({
                            "date": str(_b_date), "action": "買入",
                            "stock_id": _sid_key,
                            "stock_name": get_stock_name_map().get(_sid_key, ""),
                            "price": _b_bp, "qty": int(_b_qty),
                            "fee": round(_calc_fee(_b_bp, _b_qty), 0),
                            "tax": 0, "amount": round(_b_bp * _b_qty, 0),
                            "realized_pnl": None, "roi_pct": None,
                        })
                        save_trades(_trd_now)
                        st.success(f"✅ 買入 {_sid_key} {_b_qty}股 @ {_b_bp}，含費成本 ${_b_cost_final:,.0f}，現金已扣除")
                        st.session_state["buy_count"] = st.session_state.get("buy_count", 0) + 1
                        st.rerun()

    with _tab_sell:
        _pf_sell = load_portfolio()
        if not _pf_sell:
            st.info("目前無持倉可賣出")
        else:
            _sell_form_key = f"sell_form_{st.session_state.get('sell_count', 0)}"
            with st.form(_sell_form_key):
                st.markdown("**📌 登記賣出紀錄**")
                _sc1, _sc2, _sc3 = st.columns(3)
                with _sc1:
                    _s_sid  = st.selectbox("選擇賣出標的", list(_pf_sell.keys()))
                    _s_date = st.date_input("賣出日期",
                                             value=datetime.now(ZoneInfo("Asia/Taipei")).date())
                with _sc2:
                    _s_max_qty = int(_pf_sell.get(_s_sid, {}).get("qty", 0))
                    _s_qty  = st.number_input(f"賣出股數（持有 {_s_max_qty} 股）",
                                               min_value=0, max_value=_s_max_qty, step=1000)
                    _s_price = st.number_input("賣出均價", min_value=0.0, value=None, step=0.5, format="%.2f", placeholder="輸入賣出均價")
                with _sc3:
                    if (_s_price or 0) > 0 and (_s_qty or 0) > 0:
                        _s_bp      = float(_pf_sell[_s_sid]["buy_price"])
                        _s_inflow  = calc_net_inflow(_s_price, _s_qty)
                        _s_profit, _s_roi = calc_net_profit(_s_bp, _s_price, _s_qty)
                        _s_fee     = _calc_fee(_s_price, _s_qty)
                        _s_tax     = _s_price * _s_qty * TAX_RATE
                        st.markdown("<br>", unsafe_allow_html=True)
                        _pnl_color = "#00cc66" if _s_profit >= 0 else "#ff4444"
                        st.markdown(
                            f"<div style='background:rgba(0,0,0,0.3);border-radius:8px;padding:10px;'>"
                            f"手續費：${_s_fee:,.0f}<br>"
                            f"證交稅：${_s_tax:,.0f}<br>"
                            f"實收金額：${_s_inflow:,.0f}<br>"
                            f"<b style='color:{_pnl_color};'>實現損益：${_s_profit:,.0f} ({_s_roi:+.2f}%)</b>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

                _s_submit = st.form_submit_button("💸 確認賣出登記", type="primary")
                if _s_submit:
                    if (_s_price or 0) <= 0 or (_s_qty or 0) <= 0:
                        st.error("請填寫賣出均價與股數")
                    elif _s_qty > _s_max_qty:
                        st.error(f"賣出股數 {_s_qty} 超過持有股數 {_s_max_qty}")
                    else:
                        # 持有含費均價（WAC，已含買入手續費攤入）
                        _s_avg_cost   = float(_pf_sell[_s_sid]["buy_price"])
                        # 這批賣出的持有成本 = 含費均價 × 賣出股數
                        _s_hold_cost  = _s_avg_cost * _s_qty
                        # 賣出摩擦成本
                        _s_fee_fin    = _calc_fee(_s_price, _s_qty)
                        _s_tax_fin    = _s_price * _s_qty * TAX_RATE
                        # 賣出實收
                        _s_inflow_fin = _s_price * _s_qty - _s_fee_fin - _s_tax_fin
                        # 純損益 = 賣出實收 - 持有含費成本（不重複計算買入手續費）
                        _s_profit_fin = _s_inflow_fin - _s_hold_cost
                        _s_roi_fin    = (_s_profit_fin / _s_hold_cost * 100) if _s_hold_cost > 0 else 0.0

                        # 更新持倉：股數減少，均價維持原值不變（WAC規則）
                        _pf_now2 = load_portfolio()
                        _remain  = _pf_now2[_s_sid]["qty"] - _s_qty
                        if _remain <= 0:
                            # 全部賣出 → pop 刪除
                            _pf_now2.pop(_s_sid, None)
                        else:
                            # 部分賣出 → 只更新股數，均價不變
                            _pf_now2[_s_sid]["qty"] = _remain
                        save_portfolio(_pf_now2)

                        # 更新現金與已實現損益
                        _acct_now2 = load_account()
                        _acct_now2["cash"] = _acct_now2.get("cash", 0) + _s_inflow_fin
                        _acct_now2["realized_pnl"] = _acct_now2.get("realized_pnl", 0) + _s_profit_fin
                        save_account(_acct_now2)

                        # 寫入交易紀錄（含持有成本欄位，供後續稽核）
                        _trd_now2 = load_trades()
                        _trd_now2.append({
                            "date": str(_s_date), "action": "賣出",
                            "stock_id": _s_sid,
                            "stock_name": get_stock_name_map().get(_s_sid, ""),
                            "price": _s_price, "qty": int(_s_qty),
                            "fee": round(_s_fee_fin, 0),
                            "tax": round(_s_tax_fin, 0),
                            "amount": round(_s_price * _s_qty, 0),
                            "hold_cost": round(_s_hold_cost, 0),
                            "realized_pnl": round(_s_profit_fin, 0),
                            "roi_pct": round(_s_roi_fin, 2),
                        })
                        save_trades(_trd_now2)
                        _emoji = "🎉" if _s_profit_fin >= 0 else "📉"
                        st.success(
                            f"{_emoji} 賣出 {_s_sid} {_s_qty}股 @ {_s_price}，"
                            f"實現損益 ${_s_profit_fin:,.0f}（{_s_roi_fin:+.2f}%），現金已回補"
                        )
                        st.session_state["sell_count"] = st.session_state.get("sell_count", 0) + 1
                        st.rerun()

    with _tab_manage:
        st.markdown("**⚙️ 帳戶管理**")
        _acct_mgr = load_account()
        _col_m1, _col_m2 = st.columns(2)
        with _col_m1:
            st.metric("初始資金", f"${_acct_mgr.get('initial_capital',0):,.0f}")
            st.metric("可用現金", f"${_acct_mgr.get('cash',0):,.0f}")
            st.metric("累計已實現損益", f"${_acct_mgr.get('realized_pnl',0):,.0f}")
        with _col_m2:
            # 重設初始資金
            _new_init = st.number_input(
                "修改初始資金", min_value=0, step=10000, format="%d",
                value=int(_acct_mgr.get("initial_capital", 0)) or None, placeholder="輸入新的初始資金", key="mgr_init"
            )
            if st.button("🔄 更新初始資金", key="mgr_init_btn"):
                _acct_mgr["initial_capital"] = float(_new_init)
                save_account(_acct_mgr)
                st.success("已更新初始資金")
                st.rerun()

        # 移除持股
        _pf_mgr = load_portfolio()
        if _pf_mgr:
            st.markdown("---")
            st.markdown("---")
            st.markdown("##### ✏️ 編輯持倉")
            _edit_c1, _edit_c2 = st.columns([2, 2])
            with _edit_c1:
                _edit_sid = st.selectbox("選擇要編輯的持股", list(_pf_mgr.keys()), key="pf_edit_sel")
            if _edit_sid and _edit_sid in _pf_mgr:
                _ep = _pf_mgr[_edit_sid]
                with st.form("edit_position_form"):
                    _ec1, _ec2, _ec3 = st.columns(3)
                    with _ec1:
                        _e_bp  = st.number_input("買入均價", value=float(_ep.get("buy_price", 0)), step=0.5, format="%.4f")
                        _e_qty = st.number_input("持股數", value=int(_ep.get("qty", 0)), step=1000)
                    with _ec2:
                        _e_sl  = st.number_input("停損價", value=float(_ep.get("stop_loss", 0)), step=0.5, format="%.2f")
                        _e_sp  = st.number_input("停利價", value=float(_ep.get("stop_profit", 0)), step=0.5, format="%.2f")
                    with _ec3:
                        _e_date = st.text_input("買入日期", value=_ep.get("buy_date", ""))
                    _e_submit = st.form_submit_button("💾 儲存修改", type="primary")
                    if _e_submit:
                        _pf_mgr[_edit_sid] = {
                            "buy_price": _e_bp, "qty": int(_e_qty),
                            "stop_loss": _e_sl, "stop_profit": _e_sp,
                            "buy_date": _e_date,
                        }
                        save_portfolio(_pf_mgr)
                        st.success(f"✅ {_edit_sid} 持倉已更新")
                        st.rerun()

            st.markdown("##### 🗑️ 移除持倉")
            _del_c1, _del_c2 = st.columns([2, 1])
            with _del_c1:
                _del_sid = st.selectbox("選擇要移除的持股", list(_pf_mgr.keys()), key="pf_del_mgr")
            with _del_c2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("🗑️ 確認移除", key="pf_del_btn_mgr"):
                    del _pf_mgr[_del_sid]
                    save_portfolio(_pf_mgr)
                    st.success(f"已移除 {_del_sid}")
                    st.rerun()

    st.markdown("---")

    # ════════════════════════════════════════════════════════
    # ▌ 以下：原有持股監控（即時防守＋籌碼＋基本面）
    # ════════════════════════════════════════════════════════
        # ▌ 以下：原有持股監控（即時防守＋籌碼＋基本面）
    # ════════════════════════════════════════════════════════
    st.markdown("<div class='sec-title'>🚨 持股監控 · 即時防守 ＋ 籌碼 ＋ 基本面</div>",
                unsafe_allow_html=True)

    # ── 即時更新控制列
    live_c1, live_c2, live_c3, live_c4 = st.columns([2, 2, 2, 2])
    with live_c1:
        trading = is_trading_time()
        if trading:
            st.markdown(
                "<span style='color:#00e676;font-weight:600;font-size:.85rem;'>"
                "🟢 交易時段（09:00~13:30）· 自動更新中</span>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                "<span style='color:#546e7a;font-size:.85rem;'>"
                "⚫ 非交易時段，自動更新暫停</span>",
                unsafe_allow_html=True
            )
    with live_c2:
        last_t = st.session_state.get("last_auto_refresh")
        if last_t:
            st.caption(f"最後更新：{last_t.strftime('%m/%d %H:%M:%S')}")
        else:
            st.caption("尚未更新")
    with live_c3:
        if trading:
            try:
                tz_tw = ZoneInfo("Asia/Taipei")
                now_tw = datetime.now(tz_tw)
                h, m = now_tw.hour, now_tw.minute
                update_times_sorted = sorted(
                    {(9,18),(9,20)} |
                    {(hh,mm) for hh in range(9,14) for mm in [0,10,20,30,40,50]
                     if (hh,mm) >= (9,30) and (hh,mm) <= (13,20)}
                )
                next_t = next(
                    ((hh,mm) for hh,mm in update_times_sorted if (hh,mm) > (h,m)),
                    None
                )
                if next_t:
                    st.caption(f"下次更新：{next_t[0]:02d}:{next_t[1]:02d}")
                else:
                    st.caption("今日更新已完成")
            except Exception:
                pass
    with live_c4:
        if st.button("🔄 立即更新", key="manual_refresh"):
            with st.spinner("更新中..."):
                refresh_all_live_prices()
            st.toast("✅ 即時資料已更新", icon="✅")
            st.rerun()

    # ── 自動更新觸發（交易時段內每20分鐘）
    if should_auto_refresh():
        refresh_all_live_prices()
        st.rerun()

    st.markdown("---")
    # ── 從 portfolio.json 讀取持倉，作為監控標的
    _pf_watch = load_portfolio()
    wl = [{"id": sid, "name": sid} for sid in _pf_watch.keys()] if _pf_watch else []

    if not wl:
        st.markdown("""
        <div style='background:#0f2027;border:2px dashed #1e3a5f;border-radius:12px;
             padding:50px;text-align:center;'>
            <div style='font-size:2rem;margin-bottom:10px;'>📋</div>
            <div style='color:#e8f4fd;font-size:.92rem;font-weight:600;'>持倉清單為空</div>
            <div style='color:#7fb3d3;font-size:.8rem;margin-top:8px;'>
                請先在上方「帳務登記櫃檯→買入登記」新增持倉
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # 選擇監控標的 - 從持倉讀取
        wl_manual = wl
        wl_scan   = []  # 持倉模式下不需要掃描清單

        # 搜尋框過濾
        search_kw = st.text_input("🔎 搜尋標的", placeholder="輸入代號或名稱...",
                                   key="t2_search", label_visibility="collapsed")

        # 建立分組選項（用 ── 標題行區隔）
        src_options = []
        manual_items = [f"📌 {w['id']} {w['name']}" for w in wl_manual]
        scan_items   = [f"🔍 {w['id']} {w['name']}" for w in wl_scan]

        if search_kw.strip():
            kw = search_kw.strip()
            manual_items = [o for o in manual_items if kw in o]
            scan_items   = [o for o in scan_items   if kw in o]

        if manual_items:
            src_options += manual_items

        if not src_options:
            st.warning("找不到符合的標的")
            st.stop()

        _sel_col, _btn_col = st.columns([5, 1])
        with _sel_col:
            selected = st.selectbox("選擇監控標的", src_options, key="t2_sel",
                                     format_func=lambda x: x)
        with _btn_col:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 即時報價", key="t2_refresh", use_container_width=True,
                         help="強制抓取最新即時報價，更新月乖離計算"):
                # 清除此股的 live_prices 快取，強制重新抓取
                _sid_cur = selected.lstrip("📌🔍 ").strip().split()[0] if selected else ""
                if _sid_cur:
                    st.session_state.live_prices.pop(_sid_cur, None)
                    live_data = fetch_live_price(_sid_cur)
                    if live_data:
                        st.session_state.live_prices[_sid_cur] = live_data
                        st.toast(f"✅ {_sid_cur} 已更新：{live_data['close']} 元（{live_data['time']}）", icon="✅")
                    else:
                        st.toast("⚠️ 即時報價抓取失敗（非交易時間或網路問題）", icon="⚠️")
                st.rerun()

        # 如果選到標題行，自動跳到下一個有效選項
        if selected.startswith("──"):
            idx = src_options.index(selected)
            for nxt in src_options[idx+1:]:
                if not nxt.startswith("──"):
                    selected = nxt
                    break

        selected_clean = selected.lstrip("📌🔍 ").strip()
        sid_watch  = selected_clean.split()[0]
        name_watch = " ".join(selected_clean.split()[1:])

        # 個股切換時清除推薦快取（避免殘留上一檔的推薦）
        if sid_watch != st.session_state.get("last_stock_watch"):
            st.session_state["current_recommendation"] = None
            st.session_state["last_stock_watch"] = sid_watch

        # ── 投信期貨熔斷 + 個股防守點設定
        fuse_c1, fuse_c2, fuse_c3 = st.columns([2, 2, 2])
        with fuse_c1:
            st.markdown("**🔴 投信期貨淨部位（口數）**")
            trust_fut_net = st.number_input(
                "投信大台期貨淨部位（正=多、負=空）",
                value=st.session_state.get("trust_fut_net", 0),
                step=100, key="trust_fut_net",
                label_visibility="collapsed"
            )
            if trust_fut_net < 0:
                st.error("🟥 觸發終極熔斷：投信轉空引發ETF贖回潮\n現股全撤清倉，ETF大舉減碼！")
            elif trust_fut_net == 0:
                st.caption("⚪ 尚未輸入或中性")
            else:
                st.success(f"🟢 投信淨多單 {trust_fut_net:+,} 口，籌碼偏多")

        with fuse_c2:
            st.markdown(f"**🎯 {name_watch} 個股防守點**")
            defense_key = f"defense_{sid_watch}"
            defense_pt = st.number_input(
                f"{name_watch} 防守點（元）",
                value=float(st.session_state.get(defense_key, 0.0)),
                step=1.0, key=defense_key,
                label_visibility="collapsed"
            )
            if defense_pt > 0:
                st.caption(f"防守點：**{defense_pt:.1f} 元**，跌破即啟動防守")

        with fuse_c3:
            st.markdown("**📊 大盤防守點**")
            mkt_mode = st.selectbox(
                "大盤防守模式",
                ["手動設定", "動態均線模式（布林中軌）", "動態ATR模式（高點-2ATR）"],
                key="mkt_defense_mode", label_visibility="collapsed"
            )

            # 載入大盤 K 線（^TWII）
            df_twii, ok_twii = load_price_csv("^TWII")
            if not ok_twii or df_twii.empty:
                df_twii, ok_twii = None, False

            if mkt_mode == "手動設定":
                mkt_defense = st.number_input(
                    "大盤防守點（點）",
                    value=float(st.session_state.get("mkt_defense_manual", 43815.0)),
                    step=100.0, key="mkt_defense_manual",
                    label_visibility="collapsed"
                )
            elif mkt_mode == "動態均線模式（布林中軌）":
                if ok_twii:
                    df_twii_ind = add_indicators(df_twii)
                    mkt_defense = float(df_twii_ind["BB_MID"].iloc[-1])
                    st.metric("布林中軌（自動）", f"{mkt_defense:,.0f}")
                else:
                    mkt_defense = 0.0
                    st.caption("無大盤K線資料")
            else:  # ATR模式
                if ok_twii:
                    c_twii = df_twii["Close"].astype(float)
                    atr14  = (df_twii["High"].astype(float) - df_twii["Low"].astype(float)).rolling(14).mean()
                    highest = c_twii.rolling(252).max().iloc[-1]
                    mkt_defense = float(highest - 2 * atr14.iloc[-1])
                    st.metric("最高點-2ATR（自動）", f"{mkt_defense:,.0f}")
                else:
                    mkt_defense = 0.0
                    st.caption("無大盤K線資料")

        # 熔斷總覽警示
        if trust_fut_net < 0:
            st.markdown(
                "<div style='background:#3d0a0a;border:2px solid #ff5252;border-radius:8px;"
                "padding:14px 20px;margin:8px 0;'>"
                "<b style='color:#ff5252;font-size:1rem;'>🟥 終極熔斷觸發</b><br>"
                "<span style='color:#ffcdd2;font-size:.88rem;'>"
                "投信大台期貨淨部位轉負，ETF贖回潮啟動。"
                "所有監控個股戰略紀律覆寫：<b>現股全撤清倉，ETF大舉減碼！</b>"
                "</span></div>",
                unsafe_allow_html=True
            )
        elif defense_pt > 0:
            # 個股防守點警示
            df_check, ok_check = load_price_csv(sid_watch)
            if ok_check and not df_check.empty:
                last_close = float(df_check["Close"].iloc[-1])
                if last_close < defense_pt:
                    st.warning(
                        f"⚠️ **{name_watch} 已跌破防守點！**　"
                        f"收盤 {last_close:.1f} < 防守點 {defense_pt:.1f}　啟動防守！"
                    )

        st.markdown("---")

        # 載入 K 線
        df_prc, ok_prc = load_price_csv(sid_watch)

        if not ok_prc or df_prc.empty:
            st.warning(
                f"⚠️ {sid_watch} 無 K 線資料，請執行 "
                "`python update_data.py --only prices` 後推送 GitHub"
            )
        else:
            df_ind = calc_indicators(df_prc, MA_S, MA_M, MA_L)
            lt     = df_ind.iloc[-1]
            pv     = df_ind.iloc[-2]
            chg    = (lt["Close"] - pv["Close"]) / pv["Close"] * 100
            # 若有即時資料，用即時資料覆蓋收盤價
            live = st.session_state.live_prices.get(sid_watch)
            if live:
                live_close = live["close"]
                live_chg   = (live_close - float(df_ind.iloc[-2]["Close"])) / float(df_ind.iloc[-2]["Close"]) * 100
                display_close = live_close
                display_chg   = live_chg
                live_tag = f" <span style='color:#ffeb3b;font-size:.7rem;'>⚡ {live['time']}</span>"
            else:
                display_close = lt["Close"]
                display_chg   = None  # 無即時資料，不顯示今日漲跌
                live_tag = " <span style='color:#546e7a;font-size:.7rem;'>📅 昨收</span>"

            if display_chg is None:
                chg_s = ""  # 無即時資料，不給顏色
            else:
                chg_s = "up" if display_chg >= 0 else "down"

            # 乖離率計算（統一用 display_close 即時價 對 MA20）
            _ma20_kpi = float(df_ind["MA20"].dropna().iloc[-1]) if "MA20" in df_ind.columns and not df_ind["MA20"].dropna().empty else float("nan")
            _ema5_kpi = float(df_ind["EMA5"].dropna().iloc[-1]) if "EMA5" in df_ind.columns and not df_ind["EMA5"].dropna().empty else float("nan")
            if not np.isnan(_ma20_kpi) and _ma20_kpi > 0:
                _bias_kpi = (display_close - _ma20_kpi) / _ma20_kpi * 100  # 用即時價
                _bias_str = f"{_bias_kpi:+.1f}%"
                _bias_status = "down" if _bias_kpi > 5 else "up" if _bias_kpi < -5 else ""
            else:
                _bias_kpi = float("nan")
                _bias_str, _bias_status = "—", ""

            # 安全低接點
            if not np.isnan(_ema5_kpi) and not np.isnan(_ma20_kpi):
                _safe_buy = f"EMA5 {_ema5_kpi:.1f} / 月線 {_ma20_kpi:.1f}"
            else:
                _safe_buy = "—"

            # ── KPI 列
            kpi_cols = st.columns(6)
            mcard(kpi_cols[0], "收盤價" + live_tag,
                  f"{display_close:.1f}", chg_s)
            if display_chg is not None:
                mcard(kpi_cols[1], "漲跌幅",
                      f"{'▲' if display_chg>=0 else '▼'}{abs(display_chg):.2f}%", chg_s)
            else:
                mcard(kpi_cols[1], "漲跌幅", "— 待更新", "")
            mcard(kpi_cols[2], "EMA5",  f"{lt.get('EMA5',  float('nan')):.1f}", "")
            mcard(kpi_cols[3], "SMA60", f"{lt.get('SMA60', float('nan')):.1f}", "")
            mcard(kpi_cols[4], "RSI5",  f"{lt.get('RSI5',  float('nan')):.1f}", "")
            mcard(kpi_cols[5], "RSI20", f"{lt.get('RSI20', float('nan')):.1f}", "")

            # 硬核化欄位：乖離率 + 安全低接點
            hc1, hc2 = st.columns(2)
            # 乖離率卡片顏色邏輯
            _bias_valid = _bias_str not in ["—"]
            if _bias_valid and _bias_kpi > 25:
                _bc, _bb, _bt = "156,39,176", "#ce93d8", "#ff80ab"  # 紫色：停利區
                _op_hint = "🟣 建議減碼/停利"
            elif _bias_valid and _bias_kpi > 5:
                _bc, _bb, _bt = "255,82,82", "#ff5252", "#ff5252"   # 紅色：過熱
                _op_hint = "⚠️ 追高危險"
            elif _bias_valid and _bias_kpi < 0:
                _bc, _bb, _bt = "0,230,118", "#00e676", "#00e676"   # 綠色：折價
                _op_hint = "🟢 可布局"
            else:
                _bc, _bb, _bt = "30,58,95", "#1e3a5f", "#e8f4fd"    # 藍：中性
                _op_hint = "⚪ 續抱"

            hc1.markdown(
                f"<div style='background:rgba({_bc},0.2);border:1px solid {_bb};"
                f"border-radius:8px;padding:8px 14px;'>"
                f"<div style='color:#7fb3d3;font-size:.72rem;'>📊 與月線乖離率</div>"
                f"<div style='color:{_bt};font-size:1.2rem;font-weight:700;'>{_bias_str}</div>"
                f"<div style='color:{_bt};font-size:.75rem;margin-top:2px;'>{_op_hint}</div>"
                f"</div>",
                unsafe_allow_html=True
            )
            hc2.markdown(
                f"<div style='background:rgba(30,58,95,0.2);border:1px solid #1e3a5f;"
                f"border-radius:8px;padding:8px 14px;'>"
                f"<div style='color:#7fb3d3;font-size:.72rem;'>🎯 下一個安全低接點</div>"
                f"<div style='color:#ffeb3b;font-size:.85rem;font-weight:600;'>{_safe_buy}</div></div>",
                unsafe_allow_html=True
            )
            st.markdown("<br>", unsafe_allow_html=True)

            # ══════════════════════════════════════════════
            # 子模組 1：即時防守
            # ══════════════════════════════════════════════
            st.markdown("<div class='sec-title'>🛡️ 即時防守警示</div>",
                        unsafe_allow_html=True)

            alerts = []
            if lt["Close"] < lt.get("EMA5", float("inf")):
                alerts.append(f"股價跌破 EMA5（{lt.get('EMA5',0):.1f}）")
            rsi5_now  = lt.get("RSI5",  50)
            rsi20_now = lt.get("RSI20", 50)
            if rsi5_now > 80 and rsi5_now < rsi20_now:
                alerts.append(f"RSI(5)={rsi5_now:.1f} 高檔回落且低於 RSI(20)")

            # ══════════════════════════════════════════════
            # ⚡ 風控一：高檔動能熄火預警器（EMA5 減速機制）
            # ══════════════════════════════════════════════
            is_momentum_killed = False
            if len(df_ind) >= 4:
                closes  = df_ind["Close"].astype(float)
                ema5s   = df_ind["EMA5"].astype(float)
                sma60   = float(df_ind["SMA60"].iloc[-1]) if "SMA60" in df_ind.columns else float("nan")
                close_n = float(closes.iloc[-1])

                if not np.isnan(sma60) and sma60 > 0:
                    # 條件1：高基期（現價 > SMA60 * 1.15）
                    high_base = close_n > sma60 * 1.15
                    # 條件2：連續3日收盤 < EMA5
                    below_ema5_3d = all(
                        float(closes.iloc[i]) < float(ema5s.iloc[i])
                        for i in [-1, -2, -3]
                    )
                    # 條件3：EMA5 連續2天下彎（斜率 < 0）
                    ema5_slope_down = (
                        float(ema5s.iloc[-1]) < float(ema5s.iloc[-2]) and
                        float(ema5s.iloc[-2]) < float(ema5s.iloc[-3])
                    )
                    is_momentum_killed = high_base and below_ema5_3d and ema5_slope_down

            if is_momentum_killed:
                st.warning(
                    "⚠️ **【高檔動能熄火】** 短線衝刺動能已實質熄火。"
                    "主力高檔有出貨嫌疑（受制於EMA5），"
                    "建議立即主動減碼 50%，嚴禁加碼攤平！"
                )
                alerts.append("高檔動能熄火：高基期 + EMA5連跌3日 + 斜率下彎")

            # ══════════════════════════════════════════════
            # 📊 風控三：量縮反彈真偽自動判定引擎
            # ══════════════════════════════════════════════
            is_volume_shrink_rally = False
            volume_shrink_verdict  = None
            if len(df_ind) >= 6:
                vol_now  = float(df_ind["Volume"].iloc[-1]) if "Volume" in df_ind.columns else 0
                vma5_now = float(df_ind["VMA5"].iloc[-1])   if "VMA5"   in df_ind.columns else 0
                pct_chg  = (float(df_ind["Close"].iloc[-1]) - float(df_ind["Close"].iloc[-2])) /                            float(df_ind["Close"].iloc[-2]) * 100 if float(df_ind["Close"].iloc[-2]) > 0 else 0
                sma20_now = float(df_ind["MA20"].iloc[-1]) if "MA20" in df_ind.columns else float("nan")

                # 觸發：量 <= VMA5*0.5 且漲幅 > 3%
                if vma5_now > 0 and vol_now <= vma5_now * 0.5 and pct_chg > 3:
                    is_volume_shrink_rally = True
                    # 取大戶持股
                    _bp_now = float(big_pct.iloc[-1]) if "big_pct" in dir() and len(big_pct) > 0 else 0
                    # 判定真偽
                    if not np.isnan(sma20_now) and close_n >= sma20_now and _bp_now >= 65:
                        volume_shrink_verdict = "clean"
                    else:
                        volume_shrink_verdict = "weak"

            if is_volume_shrink_rally:
                if volume_shrink_verdict == "clean":
                    st.success(
                        "🟢 **【籌碼極度乾淨】** 優質高檔鎖倉，主力惜售，"
                        "拉回即是黃金買點，波段續抱！"
                    )
                else:
                    st.error(
                        "🚨 **【弱勢無量回抽】** 此為無前景之弱勢縮量反彈！"
                        "主力大戶未認同追價，純屬短線套利解套波，"
                        "嚴禁手動追高介入！"
                    )
                    alerts.append("量縮漲：弱勢回抽訊號，主力未認同")

            # ── 大戶持股 + 融資交叉熔斷（股權死亡交叉熔斷器）
            big_divergence_msg = None

            # 取得融資餘額趨勢
            margin_rising = False
            df_c_watch, ok_c_watch = get_chips(sid_watch)
            if ok_c_watch and not df_c_watch.empty:
                mg_rows = df_c_watch[df_c_watch.get("source", pd.Series()) == "margin"].copy()                           if "source" in df_c_watch.columns else pd.DataFrame()
                mg_col = next((c for c in df_c_watch.columns if "MarginPurchaseTodayBalance" in c), None)
                if not mg_rows.empty and mg_col:
                    mg_rows["date"] = pd.to_datetime(mg_rows.get("date"), errors="coerce")
                    mg_series = mg_rows.groupby("date")[mg_col].last().sort_index().dropna()
                    mg_num = pd.to_numeric(mg_series, errors="coerce").dropna()
                    if len(mg_num) >= 2:
                        margin_rising = float(mg_num.iloc[-1]) > float(mg_num.iloc[-2])

            df_sh2, ok_sh2 = get_shareholder(sid_watch)
            if ok_sh2 and not df_sh2.empty:
                lv_col2  = "HoldingSharesLevel" if "HoldingSharesLevel" in df_sh2.columns else None
                pct_col2 = "percent" if "percent" in df_sh2.columns else None
                if lv_col2 and pct_col2:
                    df_sh2 = df_sh2[~df_sh2[lv_col2].astype(str).str.contains("total|差異|調整", na=False)].copy()
                    df_sh2[pct_col2] = pd.to_numeric(df_sh2[pct_col2], errors="coerce")
                    big_kw2 = ["400,001","600,001","800,001","1,000,001","more than"]
                    is_big2 = df_sh2[lv_col2].astype(str).str.contains("|".join(big_kw2), case=False, na=False)
                    big_grp2 = df_sh2[is_big2].groupby("date")[pct_col2].sum().sort_index().dropna()

                    if len(big_grp2) >= 3:
                        last3 = big_grp2.tail(3).tolist()
                        big_declining = (last3[-1] < last3[-2]) and (last3[-2] < last3[-3])
                        big_drop_pct  = last3[-2] - last3[-1] if big_declining else 0

                        if big_declining:
                            close_now2 = float(lt["Close"])
                            sma20_val  = float(lt.get("MA20", float("nan")))

                            if not np.isnan(sma20_val):
                                # 死亡交叉條件：大戶下滑>1.5% + 融資上升（業界標準閾值）
                                big_drop_significant = big_drop_pct > 1.5
                                cross_signal = big_declining and margin_rising and big_drop_significant
                                cross_tag = "（大戶↓>1.5%+融資↑ 死亡交叉）" if cross_signal else "（大戶↓）"

                                if close_now2 < sma20_val:
                                    # 情況A：真．大戶出貨 → 紅燈強制清倉
                                    msg = (f"🔴 【情況A：真．大戶出貨{cross_tag}】"
                                           f"大戶持股週降 {big_drop_pct:.1f}%（{last3[-1]:.1f}%），"
                                           f"現價（{close_now2:.1f}）跌破月線（{sma20_val:.1f}），"
                                           f"基本面出現重大逆風，強制紅色風控——建議清倉離場！")
                                    big_divergence_msg = ("danger", msg)
                                    alerts.append("真．大戶出貨+跌破月線，強制清倉警告")
                                else:
                                    # 情況B：良性換手/ETF被動鎖倉 → 綠燈覆寫
                                    if cross_signal:
                                        tag = f"大戶下滑 {big_drop_pct:.1f}% 但ETF/主力高檔承接，"
                                    else:
                                        tag = "大戶持股雖下滑，"
                                    big_divergence_msg = ("safe",
                                        f"🟢 【情況B：良性籌碼換手/ETF被動鎖倉】{tag}"
                                        f"現價（{close_now2:.1f}）仍守月線（{sma20_val:.1f}）之上，"
                                        f"有強大被動資金承接，覆寫綠燈，可波段留倉至Q3。")

            dc1, dc2 = st.columns([1, 3])
            with dc1:
                if alerts:
                    st.markdown(
                        f"<div class='sig-red'>🔴 高警戒<br>"
                        f"<small>觸發 {len(alerts)} 項</small></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<div class='sig-green'>🟢 持倉安全<br>"
                        "<small>無警示</small></div>",
                        unsafe_allow_html=True,
                    )
            with dc2:
                for a in alerts:
                    st.markdown(f"<div class='sig-warn'>⚠️ {a}</div>",
                                unsafe_allow_html=True)
                if not alerts:
                    st.success("✅ 所有指標正常")

            # 大戶持股多空背離校正結果
            if big_divergence_msg:
                kind, msg = big_divergence_msg
                if kind == "danger":
                    st.error(msg)
                else:
                    st.success(msg)

            # K線主圖 + RSI 副圖
            # ══════════════════════════════════════════════
            # 🚨 進場安全性卡閘 + 移動停利訊號
            # ══════════════════════════════════════════════
            _close_now = float(lt["Close"])
            _sma20_g   = float(lt.get("MA20",  float("nan")))
            _ema5_g    = float(lt.get("EMA5",  float("nan")))
            _rsi5_g    = float(lt.get("RSI5",  50))

            # 是否已持有此部位
            _hold_col, _core_col = st.columns([3, 2])
            with _hold_col:
                _is_holding = st.toggle(
                    f"📌 我已持有 {name_watch}（切換以顯示停利訊號）",
                    key=f"holding_{sid_watch}", value=False
                )
            with _core_col:
                _in_reserve = sid_watch in set(
                    r["id"] for r in st.session_state.get("reserve_list", [])
                )
                if _in_reserve:
                    st.markdown(
                        "<span style='color:#ce93d8;font-size:.8rem;'>👑 戰略儲備庫精兵｜20% 特赦啟用</span>",
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        "<span style='color:#546e7a;font-size:.8rem;'>⬜ 常規股｜5% 標準卡閘</span>",
                        unsafe_allow_html=True
                    )

            # 統一用 _ma20_kpi（與 KPI 大字相同的 MA20）確保一致性
            _sma20_g = _ma20_kpi  # 覆寫成與顯示一致的 MA20

            if not np.isnan(_sma20_g) and _sma20_g > 0:
                bias_ma20 = (display_close - _sma20_g) / _sma20_g * 100  # 統一用 display_close

                # ── 突破 Facts 計算
                _close_series = df_ind["Close"].astype(float)
                _high60  = float(_close_series.tail(60).max())  if len(df_ind) >= 60  else float(_close_series.max())
                _high250 = float(_close_series.tail(250).max()) if len(df_ind) >= 250 else float(_close_series.max())
                _vol_now = float(df_ind["Volume"].astype(float).iloc[-1]) if "Volume" in df_ind.columns else 0
                _vma20   = float(df_ind["Volume"].astype(float).rolling(20).mean().iloc[-1]) if "Volume" in df_ind.columns else 0

                # ── 呼叫統一風控卡閘函式
                _gate = check_gatekeeper(
                    sid=sid_watch, bias_ma20=bias_ma20, rsi5=_rsi5_g,
                    ema5=_ema5_g, sma20=_sma20_g,
                    close_now=display_close, high60=_high60, high250=_high250,
                    vol_now=_vol_now, vma20=_vma20,
                    is_holding=_is_holding, is_backtest=False
                )
                _lvl = _gate.get("level", "green_safe")
                _msg = _gate.get("msg", "")

                # ── 大盤聯鎖：使用 parse_futures_chips 解析正確的外資大台淨部位
                _df_fut_t3, _ok_fut_t3 = get_futures()
                _tx_net_t3 = 0
                if _ok_fut_t3 and not _df_fut_t3.empty:
                    try:
                        _nm_t3 = next((c for c in ["name","institutional_investors"]
                                       if c in _df_fut_t3.columns), None)
                        _lc_t3 = next((c for c in _df_fut_t3.columns
                                       if "long_open_interest_balance" in c and "amount" not in c), None)
                        _sc_t3 = next((c for c in _df_fut_t3.columns
                                       if "short_open_interest_balance" in c and "amount" not in c), None)
                        _inst_t3 = _df_fut_t3[_df_fut_t3["source"]=="institutional"]                                    if "source" in _df_fut_t3.columns else _df_fut_t3
                        _tx_t3  = _inst_t3[_inst_t3["contract"]=="TX"]                                   if "contract" in _inst_t3.columns else pd.DataFrame()
                        if not _tx_t3.empty and _lc_t3 and _sc_t3 and _nm_t3:
                            _ld_t3 = _tx_t3["date"].max()
                            _row_t3 = _tx_t3[(_tx_t3["date"]==_ld_t3) &
                                              _tx_t3[_nm_t3].astype(str).str.contains("外資", na=False)]
                            if not _row_t3.empty:
                                _tx_net_t3 = int(float(_row_t3[_lc_t3].values[0])) -                                              int(float(_row_t3[_sc_t3].values[0]))
                    except:
                        pass
                # ── V6 三軌聯鎖斷路器
                _risk_st_t3, _risk_i_t3 = get_system_risk_status()
                _danger_t3  = _risk_st_t3 == "RED_ALERT"
                _yellow_t3  = _risk_st_t3 == "YELLOW_ALERT"
                _squeeze_t3 = _risk_st_t3 == "SHORT_SQUEEZE"
                _tx_net_t3      = _risk_i_t3["tx_net"]
                _mtx_retail_t3  = _risk_i_t3["mtx_retail"]

                # 大盤危險時覆寫 SOP 訊息
                # ── 依風控狀態 + 乖離率輸出 SOP
                if _danger_t3:
                    if bias_ma20 < -5 or bias_ma20 <= 2:
                        # 套牢或平盤 → 斷尾求生
                        _t3_sop = (f"🛑 <b>【斷尾求生硬停損 SOP】</b>：大盤土石流將無差別拋售！"
                                   f"本股月乖離 {bias_ma20:.1f}%，已陷入弱勢，"
                                   f"<b>系統強制下達一票否決，請立刻尾盤無條件 100% 執行硬停損！</b>")
                        _t3c, _t3bg = "#e11d48", "rgba(244,63,94,0.12)"
                    else:
                        # 獲利中 → 縮緊停利
                        _t3_sop = (f"⚡ <b>【獲利落袋 SOP】</b>：大盤土石流風險！"
                                   f"本股月乖離 {bias_ma20:.1f}%，"
                                   f"<b>立刻縮緊停利至今日盤中最低點，一破立刻 100% 獲利了結！</b>")
                        _t3c, _t3bg = "#f43f5e", "rgba(244,63,94,0.08)"
                    st.markdown(
                        f"<div style='background:{_t3bg};border:2px solid {_t3c};"
                        f"border-radius:10px;padding:12px 16px;'>"
                        f"<span style='color:{_t3c};font-size:.9rem;'>{_t3_sop}</span></div>",
                        unsafe_allow_html=True
                    )
                elif _yellow_t3:
                    if bias_ma20 > 5:
                        _t3_sop = (f"⚠️ <b>【移動停利縮緊】</b>：大盤黃燈警戒！"
                                   f"本股月乖離 {bias_ma20:.1f}%（獲利中），"
                                   f"<b>立刻縮緊停利線至今日低點，鎖住浮盈！</b>")
                    else:
                        _t3_sop = (f"⚠️ <b>【防禦降載・減碼 SOP】</b>：大盤土石流蓄勢中！"
                                   f"月乖離 {bias_ma20:.1f}%，<b>建議尾盤無腦平倉 50% 部位，降載資金！</b>")
                    st.markdown(
                        f"<div style='background:rgba(251,191,36,0.08);border:1px solid #fbbf24;"
                        f"border-radius:10px;padding:12px 16px;'>"
                        f"<span style='color:#fbbf24;font-size:.9rem;'>{_t3_sop}</span></div>",
                        unsafe_allow_html=True
                    )
                elif _squeeze_t3:
                    st.success(f"🔥 **【軋空特赦・安心續抱】** 台美散戶同步恐慌放空，主力即將軋空！"
                               f"本股月乖離 {bias_ma20:.1f}%，遵循長線30週線或短線EMA5紀律控盤，安心持有。")
                elif _lvl == "purple":
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg,rgba(156,39,176,0.2),rgba(74,20,140,0.3));"
                        f"border:2px solid #ce93d8;border-radius:10px;padding:14px 18px;'>"
                        f"<span style='color:#f3e5f5;font-size:.9rem;'>{_msg}</span></div>",
                        unsafe_allow_html=True
                    )
                elif _lvl == "red":
                    st.warning(_msg)
                else:
                    st.success(_msg)

            mc1, mc2 = st.columns([3, 1])
            with mc1:
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    row_heights=[.7, .3], vertical_spacing=.04)
                # K線
                fig.add_trace(go.Candlestick(
                    x=df_ind.index,
                    open=df_ind["Open"], high=df_ind["High"],
                    low=df_ind["Low"],   close=df_ind["Close"],
                    increasing_line_color="#ff5252",
                    decreasing_line_color="#00e676",
                    name="K線", showlegend=False,
                ), row=1, col=1)
                # EMA5（黃）、SMA60（紫紅）
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["EMA5"],
                    mode="lines", name="EMA5",
                    line=dict(color="#ffeb3b", width=1.5),
                ), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["SMA60"],
                    mode="lines", name="SMA60",
                    line=dict(color="#e91e8c", width=1.5),
                ), row=1, col=1)
                # 布林通道 UB2/LB2（深藍）
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["UB2"],
                    mode="lines", name="UB2",
                    line=dict(color="#1565c0", width=1, dash="dot"),
                ), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["LB2"],
                    mode="lines", name="LB2",
                    line=dict(color="#1565c0", width=1, dash="dot"),
                    fill="tonexty", fillcolor="rgba(21,101,192,0.05)",
                ), row=1, col=1)
                # 布林通道 UB4/LB4（綠）
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["UB4"],
                    mode="lines", name="UB4",
                    line=dict(color="#00c853", width=1, dash="dash"),
                ), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=df_ind.index, y=df_ind["LB4"],
                    mode="lines", name="LB4",
                    line=dict(color="#00c853", width=1, dash="dash"),
                ), row=1, col=1)
                # 成交量
                fig.add_trace(go.Bar(
                    x=df_ind.index, y=df_ind["Volume"],
                    marker_color=["#ff5252" if c >= o else "#00e676"
                                  for c, o in zip(df_ind["Close"], df_ind["Open"])],
                    opacity=.5, showlegend=False, name="量",
                ), row=2, col=1)
                fig.update_layout(**base_layout(f"{name_watch} 日線走勢", 460),
                                  xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, width='stretch')
            with mc2:
                # RSI 雙線圖
                rst = df_ind.tail(60)
                frsi = go.Figure()
                frsi.add_trace(go.Scatter(x=rst.index, y=rst["RSI5"],
                    mode="lines", name="RSI5", line=dict(color="#ffeb3b", width=1.5)))
                frsi.add_trace(go.Scatter(x=rst.index, y=rst["RSI20"],
                    mode="lines", name="RSI20", line=dict(color="#00d4ff", width=1.5)))
                frsi.add_hrect(y0=80, y1=100, fillcolor="rgba(255,82,82,.10)", line_width=0)
                frsi.add_hrect(y0=0,  y1=20,  fillcolor="rgba(0,230,118,.10)", line_width=0)
                frsi.add_hline(y=80, line_dash="dot", line_color="#ff5252", line_width=1)
                frsi.add_hline(y=20, line_dash="dot", line_color="#00e676", line_width=1)
                frsi.update_layout(**base_layout("RSI（近60日）", 460))
                frsi.update_yaxes(range=[0, 100])
                st.plotly_chart(frsi, width='stretch')

            # ══════════════════════════════════════════════
            # 子模組 2：籌碼純度
            # ══════════════════════════════════════════════
            st.markdown("<div class='sec-title'>🧮 籌碼純度</div>",
                        unsafe_allow_html=True)

            df_c_w, ok_c_w = get_chips(sid_watch)
            if ok_c_w and not df_c_w.empty:
                # 法人
                inst_w = df_c_w[df_c_w.get("source", pd.Series(dtype=str)) == "institutional"] \
                         if "source" in df_c_w.columns else df_c_w
                if inst_w.empty and "net" in df_c_w.columns:
                    inst_w = df_c_w

                margin_w = df_c_w[df_c_w["source"] == "margin"] \
                           if "source" in df_c_w.columns else pd.DataFrame()

                # 警告偵測
                if not inst_w.empty and "net" in inst_w.columns and not margin_w.empty:
                    mg_col_w = next((c for c in margin_w.columns if "MarginPurchaseTodayBalance" in c or ("TodayBalance" in c and "Short" not in c)), None)
                    if mg_col_w:
                        net_sum = pd.to_numeric(inst_w.groupby("date")["net"].sum().iloc[-5:].sum() if "date" in inst_w.columns else pd.Series([0]), errors="coerce")
                        bal_w   = pd.to_numeric(margin_w[mg_col_w], errors="coerce").dropna()
                        mg_rising = len(bal_w) >= 2 and float(bal_w.iloc[-1]) > float(bal_w.iloc[-2])
                        if float(net_sum.sum()) < 0 and mg_rising:
                            st.markdown(
                                "<div class='sig-warn'>⚠️ 籌碼發散：法人賣超且融資增加，請提高警覺</div>",
                                unsafe_allow_html=True,
                            )

                # 雙軸圖
                fig2 = make_subplots(specs=[[{"secondary_y": True}]])
                if not inst_w.empty and "net" in inst_w.columns and "date" in inst_w.columns:
                    piv = inst_w.pivot_table(
                        index="date", columns="name", values="net", aggfunc="sum"
                    ).fillna(0).sort_index()
                    cmap = {"外資": "#00d4ff", "投信": "#e040fb", "自營": "#ffab40"}
                    for col in piv.columns:
                        clr = next((v for k,v in cmap.items() if k in str(col)), "#546e7a")
                        v   = piv[col] / 1e4
                        fig2.add_trace(go.Bar(
                            x=piv.index, y=v, name=str(col)[:4],
                            marker_color=[clr if x >= 0 else "#ff5252" for x in v],
                            opacity=.75,
                        ), secondary_y=False)
                if not margin_w.empty:
                    mg_col_w = next((c for c in margin_w.columns if "MarginPurchaseTodayBalance" in c or ("TodayBalance" in c and "Short" not in c)), None)
                    if mg_col_w and "date" in margin_w.columns:
                        mw2 = margin_w.set_index("date")
                        fig2.add_trace(go.Scatter(
                            x=mw2.index,
                            y=pd.to_numeric(mw2[mg_col_w], errors="coerce") / 1e8,
                            mode="lines", name="融資餘額(億)",
                            line=dict(color="#ff9800", width=2),
                        ), secondary_y=True)
                fig2.update_layout(**base_layout("三大法人買賣超（萬股）＋融資餘額", 380),
                                   bargap=0.02, bargroupgap=0.01)
                fig2.update_yaxes(gridcolor=GRID_COL, secondary_y=False)
                fig2.update_yaxes(showgrid=False, secondary_y=True)
                st.plotly_chart(fig2, width='stretch')
            else:
                st.info("籌碼 CSV 尚無此股票資料，請執行 update_data.py")

            # ══════════════════════════════════════════════
            # 子模組 3：基本面追蹤
            # ══════════════════════════════════════════════
            st.markdown("<div class='sec-title'>📋 基本面追蹤</div>",
                        unsafe_allow_html=True)

            df_fw, ok_fw = get_financials(sid_watch)
            if ok_fw and not df_fw.empty:
                nc = "origin_name" if "origin_name" in df_fw.columns else df_fw.columns[2]
                vc = "value"

                def extract_series(kw):
                    rows = df_fw[df_fw[nc].str.contains(kw, case=False, na=False)]
                    s    = pd.to_numeric(rows[vc], errors="coerce").dropna()
                    idx  = pd.to_datetime(rows.loc[s.index, "date"], errors="coerce") \
                           if "date" in rows.columns else pd.RangeIndex(len(s))
                    return pd.Series(s.values, index=idx.values).sort_index()

                eps_s = extract_series("每股盈餘|BasicEPS")
                gm_s  = extract_series("毛利率|GrossMargin")
                om_s  = extract_series("營業利益率|OperatingMargin")

                bm1, bm2, bm3 = st.columns(3)
                mcard(bm1, "最新毛利率",
                      f"{float(gm_s.iloc[-1]):.1f}%" if len(gm_s) else "—", "up")
                mcard(bm2, "最新營益率",
                      f"{float(om_s.iloc[-1]):.1f}%" if len(om_s) else "—", "up")
                mcard(bm3, "最新 EPS",
                      f"{float(eps_s.iloc[-1]):.2f}" if len(eps_s) else "—", "up")
                st.markdown("<br>", unsafe_allow_html=True)

                fg = make_subplots(specs=[[{"secondary_y": True}]])
                if len(gm_s):
                    fg.add_trace(go.Scatter(
                        x=gm_s.index, y=gm_s.values,
                        mode="lines+markers", name="毛利率%",
                        line=dict(color="#00e676", width=2.5), marker=dict(size=6),
                    ), secondary_y=False)
                if len(om_s):
                    fg.add_trace(go.Scatter(
                        x=om_s.index, y=om_s.values,
                        mode="lines+markers", name="營益率%",
                        line=dict(color="#00d4ff", width=2, dash="dot"),
                        marker=dict(size=5),
                    ), secondary_y=False)
                if len(eps_s):
                    fg.add_trace(go.Bar(
                        x=eps_s.index, y=eps_s.values, name="EPS",
                        marker_color=["#ff5252" if v >= 0 else "#00e676"
                                      for v in eps_s.values],
                        opacity=.7,
                    ), secondary_y=True)
                fg.update_layout(**base_layout(f"{name_watch} 財務趨勢", 380))
                fg.update_yaxes(title_text="利率(%)",   gridcolor=GRID_COL, secondary_y=False)
                fg.update_yaxes(title_text="EPS(元)",   showgrid=False,     secondary_y=True)
                st.plotly_chart(fg, width='stretch')
            else:
                st.info("財報 CSV 尚無此股票資料")

# ──────────────────────────────────────────────────────────────
# ▌ 換股推薦面板（Tab3 底部）
# ──────────────────────────────────────────────────────────────

with tab3:
    # ════════════════════════════════════════════════════════
    # ▌ 持股監控（從 portfolio.json 讀取，給操作建議）
    # 只顯示已買入的持倉，買入就新增，完全出清就自動刪除
    # ════════════════════════════════════════════════════════
    st.markdown("<div class='sec-title'>📋 持股監控 · 操作建議</div>",
                unsafe_allow_html=True)

    import gc as _gc2
    _pf_mon = load_portfolio()
    _sb_mon = get_sector_breadth()
    _sb_above_mon  = _sb_mon.get("above_sma20", 0)
    _sb_total_mon  = _sb_mon.get("total", 17)
    _mkt_ratio_mon = _sb_above_mon / _sb_total_mon if _sb_total_mon else 0

    if not _pf_mon:
        st.info("📭 目前無持倉。請先在下方「帳務登記櫃檯→買入登記」新增持倉。")
    else:
        for _m_sid, _m_pos in _pf_mon.items():
            _m_bp   = float(_m_pos.get("buy_price", 0))
            _m_qty  = int(_m_pos.get("qty", 0))
            _m_sl   = float(_m_pos.get("stop_loss", 0))
            _m_sp   = float(_m_pos.get("stop_profit", 0))
            _m_date = _m_pos.get("buy_date", "—")
            if _m_qty <= 0 or _m_bp <= 0:
                continue

            # 取即時現價
            try:
                _df_m, _ok_m = load_price_csv(_m_sid)
                _m_cp = float(_df_m["Close"].iloc[-1]) if _ok_m and not _df_m.empty else _m_bp
                # 計算技術指標
                _df_mi = add_indicators(_df_m) if _ok_m and not _df_m.empty else None
            except Exception:
                _m_cp  = _m_bp
                _df_mi = None

            # 損益計算（含費均價，不重複計費）
            _m_hold_cost   = _m_bp * _m_qty
            _m_inflow_now  = calc_net_inflow(_m_cp, _m_qty)
            _m_profit      = _m_inflow_now - _m_hold_cost
            _m_roi         = (_m_profit / _m_hold_cost * 100) if _m_hold_cost > 0 else 0.0
            _m_pnl_color   = "#ff4444" if _m_profit > 0 else "#00cc66" if _m_profit < 0 else "#e8f4fd"

            # 技術面判斷
            _m_tech_signal = "⚪ 無法判斷"
            if _df_mi is not None and len(_df_mi) >= 20:
                _lt = _df_mi.iloc[-1]
                _ema5  = float(_lt.get("EMA5",  float("nan")))
                _bbmid = float(_lt.get("BB_MID", float("nan")))
                _ma20  = float(_lt.get("MA20",  float("nan")))
                if not any(np.isnan(_v) for _v in [_ema5, _bbmid, _ma20]):
                    if _m_cp > _ema5 and _m_cp > _bbmid:
                        _m_tech_signal = "🔴 強勢站上均線"
                    elif _m_cp < _ema5 and _m_cp < _bbmid:
                        _m_tech_signal = "🟢 弱勢跌破均線"
                    else:
                        _m_tech_signal = "🟡 盤整區間"

            # 操作建議邏輯
            if _m_sl > 0 and _m_cp <= _m_sl:
                _suggestion = "🚨 停損出場：已跌破防線，強制執行風控"
                _sug_color  = "#ff1744"
            elif _m_sp > 0 and _m_cp >= _m_sp:
                _suggestion = "🎯 停利出場：已達目標價，評估獲利了結"
                _sug_color  = "#ff4444"
            elif _m_roi >= 20 and _mkt_ratio_mon < 0.4:
                _suggestion = "📉 減碼保利：市場結構偏弱，建議鎖定部分利潤"
                _sug_color  = "#ff9800"
            elif _m_roi <= -8:
                _suggestion = "⚠️ 加碼攤平 or 停損：虧損擴大，需決策"
                _sug_color  = "#ff9800"
            elif _m_roi > 5 and _mkt_ratio_mon >= 0.5:
                _suggestion = "📈 加碼：市場健康且持股獲利，可考慮加倉"
                _sug_color  = "#ff4444"
            else:
                _suggestion = "⏸️ 維持現況：靜待訊號明朗"
                _sug_color  = "#7fb3d3"

            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03);border:1px solid #1e3a5f;"
                f"border-left:4px solid {_m_pnl_color};border-radius:8px;"
                f"padding:14px 16px;margin:8px 0;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                f"<span style='color:#e8f4fd;font-size:1rem;font-weight:700;'>"
                f"{_m_sid}　<span style='color:#7fb3d3;font-size:.85rem;font-weight:400;'>買入日：{_m_date}　均價：{_m_bp:.2f}　持股：{_m_qty:,}股</span></span>"
                f"<span style='color:{_m_pnl_color};font-size:1rem;font-weight:700;'>"
                f"現價 {_m_cp:.2f}　{_m_profit:+,.0f}元（{_m_roi:+.2f}%）</span>"
                f"</div>"
                f"<div style='margin-top:8px;'>"
                f"<span style='color:#9fb8d4;font-size:.82rem;'>技術面：{_m_tech_signal}　｜　"
                f"市場龍頭站月線：{_sb_above_mon}/{_sb_total_mon}</span>"
                f"</div>"
                f"<div style='margin-top:6px;background:rgba(0,0,0,0.2);border-radius:6px;"
                f"padding:8px 12px;'>"
                f"<span style='color:{_sug_color};font-weight:600;font-size:.9rem;'>"
                f"💡 操作建議：{_suggestion}</span>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True
            )
            del _df_m
            _gc2.collect()

# ──────────────────────────────────────────────────────────────
# ▌ TAB 4：戰略儲備庫（精兵回頭草雷達）
# ──────────────────────────────────────────────────────────────
with tab4:
    st.markdown("<div class='sec-title'>🏆 王者候選名單 · 精兵儲備庫</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>存放值得長期等待的優質標的。"
        "Rex Priority Score 每日自動排名，告訴你今天最值得關注哪幾檔。</div>",
        unsafe_allow_html=True
    )

    # ══════════════════════════════════════════════════════════════
    # ▌ Phase 3：產業景氣燈號（手動標記，影響 Rex Priority Score）
    # 每月更新一次，反映你對產業週期的主觀判斷
    # ══════════════════════════════════════════════════════════════
    with st.expander("🌐 產業景氣燈號（每月人工標記一次）", expanded=False):
        st.caption("這是你作為產業趨勢投資人最核心的判斷，系統無法替代，請每月更新一次。")

        # 初始化
        if "sector_tags" not in st.session_state:
            st.session_state.sector_tags = {}

        _sector_options = {
            "🌱 起步": 4,
            "🚀 加速": 5,
            "⚡ 高峰": 2,
            "🌙 衰退": 0,
            "❓ 未標記": 2,
        }

        _sectors = [
            ("AI算力",      ["2330", "2454"]),
            ("高速傳輸",    ["2383", "2345"]),
            ("散熱電源",    ["3017", "2308"]),
            ("電力基建",    ["1519"]),
            ("低軌衛星",    ["3491"]),
            ("半導體供應鏈", ["3037", "8299"]),
            ("網通",        ["6285"]),
            ("其他電子",    []),
        ]

        _sc1, _sc2 = st.columns(2)
        for _si, (_sname, _sids) in enumerate(_sectors):
            _col = _sc1 if _si % 2 == 0 else _sc2
            with _col:
                _cur_tag = st.session_state.sector_tags.get(_sname, "❓ 未標記")
                _new_tag = st.selectbox(
                    f"{_sname}（{', '.join(_sids) if _sids else '—'}）",
                    options=list(_sector_options.keys()),
                    index=list(_sector_options.keys()).index(_cur_tag)
                          if _cur_tag in _sector_options else 4,
                    key=f"sector_tag_{_sname}"
                )
                if _new_tag != _cur_tag:
                    st.session_state.sector_tags[_sname] = _new_tag

        st.caption(f"🕐 上次標記：{st.session_state.get('sector_tags_updated', '尚未標記')}")
        if st.button("💾 儲存本次標記", key="save_sector_tags"):
            st.session_state.sector_tags_updated = datetime.now(
                ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
            st.success("✅ 產業景氣燈號已儲存")

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════
    # ▌ Rex Priority Score — 今日優先研究 TOP 5
    # 注意力排序工具，不是買進訊號。
    # 回答：「今天我應該把研究時間花在哪幾檔？」
    # ══════════════════════════════════════════════════════════════
    with st.expander("🏆 Rex Priority Score｜今日優先研究排行榜", expanded=True):
        st.caption(
            "⚠️ Rex Priority Score 是注意力排序工具，不是買進訊號。"
            "告訴你「今天最值得研究哪幾檔」，不告訴你「今天要買哪幾檔」。"
        )

        if not st.session_state.get("reserve_list"):
            # 嘗試從本地 watchlist.json 直接讀取，不等 session 初始化
            try:
                import json as _jj, os as _oo
                _wl_path = _oo.path.join("data", "watchlist.json")
                if _oo.path.exists(_wl_path):
                    with open(_wl_path, "r", encoding="utf-8") as _ff:
                        _wl_data = _jj.load(_ff)
                    _pre_rsv = _wl_data.get("reserve", [])
                    if _pre_rsv:
                        st.session_state.reserve_list = _pre_rsv
            except Exception:
                pass

        if not st.session_state.get("reserve_list"):
            st.info("儲備庫尚無標的，請先加入精兵。")
        else:
            # 取得市場溫度（讀現有的三軌風控燈號）
            try:
                _rex_mkt_info = get_risk_status()
                _rex_mkt_level = _rex_mkt_info.get("level", "YELLOW_ALERT")
                if _rex_mkt_level == "GREEN_NORMAL":
                    _rex_mkt_sig = "🟢"
                elif _rex_mkt_level == "RED_ALERT":
                    _rex_mkt_sig = "🔴"
                else:
                    _rex_mkt_sig = "🟡"
            except Exception:
                _rex_mkt_sig = "🟡"

            _rex_ids = tuple(item["id"] for item in st.session_state.reserve_list)
            _rex_nm  = {item["id"]: item.get("name", item["id"])
                        for item in st.session_state.reserve_list}

            with st.spinner("計算 Rex Priority Score 中..."):
                _rex_scores = calc_rex_priority_scores(_rex_ids, _rex_mkt_sig)

            if not _rex_scores:
                st.warning("資料不足，無法計算評分。請確認 K線/財報/籌碼資料已更新。")
            else:
                # ── 市場環境燈號顯示
                _rex_mkt_txt = {"🟢": "適合布局", "🟡": "謹慎觀望", "🔴": "縮手防守"}
                st.markdown(
                    f"<div style='font-size:.85rem;color:#9fb8d4;margin-bottom:12px;'>"
                    f"市場環境：{_rex_mkt_sig} {_rex_mkt_txt.get(_rex_mkt_sig,'—')}"
                    f"　｜　環境分：{_rex_market_score(_rex_mkt_sig)}/20"
                    f"　｜　共 {len(_rex_scores)} 檔參與排名</div>",
                    unsafe_allow_html=True
                )

                # ── TOP 5 卡片
                _top5 = _rex_scores[:5]
                for _rank, _rs in enumerate(_top5, 1):
                    _sid  = _rs["stock_id"]
                    _name = _rex_nm.get(_sid, _sid)
                    _tot  = _rs["total"]
                    _flag = _rs["flag"]

                    # 總分燈號
                    if _tot >= 75:   _tc, _tl = "#ff6b6b", "🔴 高度優先"
                    elif _tot >= 60: _tc, _tl = "#fbbf24", "🟡 次要優先"
                    else:            _tc, _tl = "#7fb3d3", "⚪ 觀察中"

                    # 旗標樣式
                    _flag_html = ""
                    if _flag:
                        _fc = "#ff4444" if "⛔" in _flag or "🚨" in _flag else "#fbbf24"
                        _flag_html = (
                            f"<span style='background:{_fc}22;color:{_fc};"
                            f"border:1px solid {_fc};border-radius:4px;"
                            f"padding:1px 6px;font-size:.78rem;margin-left:6px;'>{_flag}</span>"
                        )

                    st.markdown(
                        f"<div style='border:1px solid {_tc};border-left:4px solid {_tc};"
                        f"border-radius:8px;padding:12px 16px;margin:8px 0;"
                        f"background:rgba(255,255,255,0.02);'>"

                        # 標題列
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<span style='font-size:1rem;font-weight:700;color:#e8f4fd;'>"
                        f"#{_rank}　{_sid} {_name}</span>"
                        f"<span style='font-size:1.1rem;font-weight:700;color:{_tc};'>"
                        f"{_tot}/100　{_tl}</span>"
                        f"</div>"
                        f"{_flag_html}"

                        # 三層分數條
                        f"<div style='display:flex;gap:16px;margin:10px 0 6px;font-size:.82rem;'>"
                        f"<span style='color:#a8d8ea;'>👑 王者 {_rs['king_total']}/40</span>"
                        f"<span style='color:#ffd700;'>⚔️ 攻擊 {_rs['attack_total']}/40</span>"
                        f"<span style='color:#90ee90;'>🌡️ 環境 {_rs['mkt_score']}/20</span>"
                        f"</div>"

                        # 王者分數明細
                        f"<div style='font-size:.78rem;color:#9fb8d4;line-height:1.8;"
                        f"border-top:1px solid rgba(255,255,255,0.08);padding-top:6px;'>"
                        f"<b style='color:#a8d8ea;'>👑 王者分數</b>　"
                        f"營收YoY {_rs['revenue_yoy_score']}/10"
                        f"（{('+'+str(int(_rs['revenue_yoy_val']))+'%') if _rs['revenue_yoy_val'] is not None else '—'}）　"
                        f"EPS YoY {_rs['eps_yoy_score']}/10"
                        f"（{('+'+str(int(_rs['eps_yoy_val']))+'%') if _rs['eps_yoy_val'] is not None else '—'}）　"
                        f"毛利率 {_rs['gm_score']}/10（{_rs['gm_trend']}）　"
                        f"產業風口 {_rs['sector_score']}/5　"
                        f"大戶趨勢 {_rs['holder_score']}/5（{_rs['holder_trend']}）"
                        f"</div>"

                        # 攻擊分數明細
                        f"<div style='font-size:.78rem;color:#9fb8d4;line-height:1.8;"
                        f"margin-top:4px;'>"
                        f"<b style='color:#ffd700;'>⚔️ 攻擊分數</b>　"
                        f"支撐位置 {_rs['support_score']}/10（{_rs['support_detail']}）　"
                        f"MA結構 {_rs['ma_score']}/10（{_rs['ma_detail']}）　"
                        f"MOM動能 {_rs['mom_score']}/10（{_rs['mom_detail'][:25]}...）　"
                        f"籌碼沉澱 {_rs['chips_score']}/10（{_rs['chips_detail']}）"
                        f"</div>"

                        f"</div>",
                        unsafe_allow_html=True
                    )

                # ── 完整排名摺疊表（所有標的）
                with st.expander(f"📋 完整排名（共 {len(_rex_scores)} 檔）", expanded=False):
                    _rank_html = ""
                    for _i, _rs in enumerate(_rex_scores, 1):
                        _sid  = _rs["stock_id"]
                        _name = _rex_nm.get(_sid, _sid)
                        _tot  = _rs["total"]
                        _flag = _rs["flag"]
                        _fc   = "#ff6b6b" if _tot >= 75 else "#fbbf24" if _tot >= 60 else "#7fb3d3"
                        _fl   = f" {_rs['flag']}" if _rs["flag"] else ""
                        _rank_html += (
                            f"<div style='padding:5px 10px;border-bottom:1px solid #1e3a5f;"
                            f"font-size:.82rem;color:#e8f4fd;display:flex;"
                            f"justify-content:space-between;'>"
                            f"<span>#{_i}　{_sid} {_name}{_fl}</span>"
                            f"<span style='color:{_fc};font-weight:600;'>{_tot}分　"
                            f"<span style='color:#a8d8ea;font-weight:400;'>王{_rs['king_total']}</span>+"
                            f"<span style='color:#ffd700;font-weight:400;'>攻{_rs['attack_total']}</span>+"
                            f"<span style='color:#90ee90;font-weight:400;'>環{_rs['mkt_score']}</span>"
                            f"</span></div>"
                        )
                    st.markdown(
                        f"<div style='border:1px solid #1e3a5f;border-radius:6px;"
                        f"max-height:400px;overflow-y:auto;'>{_rank_html}</div>",
                        unsafe_allow_html=True
                    )

    st.markdown("---")

    # ── Session State 初始化（戰略儲備清單）
    if "reserve_list" not in st.session_state:
        # 從 watchlist.json 讀取（若有）
        _rsv = st.session_state.get("_reserve_raw", [])
        st.session_state.reserve_list = _rsv

    # 從 GitHub 讀取 reserve（整合進 watchlist.json 的 reserve key）
    if "reserve_loaded" not in st.session_state:
        try:
            _ru = f"{GITHUB_RAW}/watchlist.json"
            import requests as _rqr
            _rr = _rqr.get(_ru, timeout=8)
            if _rr.status_code == 200:
                _rd = _rr.json()
                st.session_state.reserve_list = _rd.get("reserve", [])
        except Exception:
            pass
        st.session_state.reserve_loaded = True

    # ── 每次進入 Tab4 執行籌碼健檢（只提示，不自動除名）
    if st.session_state.get("reserve_list"):
        _removed, _kept = refresh_reserve_metabolism()
        if _removed:
            st.markdown(
                "<div style='background:rgba(255,152,0,0.12);border:1px solid #ff9800;"
                "border-radius:10px;padding:12px 16px;margin-bottom:10px;'>"
                "<b style='color:#ff9800;'>⚠️ 籌碼健檢警報：以下標的出現除名訊號，請手動確認是否移除</b>"
                "</div>",
                unsafe_allow_html=True
            )
            for _rm in _removed:
                _rm_c1, _rm_c2 = st.columns([5, 1])
                _rm_c1.markdown(
                    f"<span style='color:#ff9800;font-size:.88rem;'>"
                    f"🚨 **{_rm.get('name','')}（{_rm['id']}）**｜"
                    f"除名原因：{_rm.get('_remove_reason','—')}｜"
                    f"20% 特赦風險提升，建議手動複核後移除</span>",
                    unsafe_allow_html=True
                )
                if _rm_c2.button("🗑️ 除名", key=f"meta_rm_{_rm['id']}", use_container_width=True):
                    st.session_state.reserve_list = [
                        r for r in st.session_state.reserve_list if r["id"] != _rm["id"]
                    ]
                    save_watchlist_to_github(
                        st.session_state.watchlist,
                        st.session_state.watchlist_scan,
                        {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0},
                        reserve=st.session_state.reserve_list
                    )
                    st.toast(f"✅ {_rm.get('name','')} 已從儲備庫除名", icon="✅")
                    st.rerun()

    st.markdown("### ➕ 加入戰略儲備")


    rsv_c1, rsv_c2, rsv_c3, rsv_c4 = st.columns([3, 1.2, 0.8, 0.8])
    with rsv_c1:
        rsv_sid = st.text_input("股票代號（可加備註，如：2345 散熱主力股）",
                                placeholder="輸入代號，如：2345",
                                key="rsv_sid", label_visibility="collapsed")
    # 策略標籤選擇（在代號輸入欄旁邊）
    _rsv_strat_radio = st.radio(
        "戰略標籤", ["🛡️ 長線防空洞", "⚡ 短線突擊隊"],
        horizontal=True, key="rsv_strat_tag",
        label_visibility="collapsed"
    )
    _rsv_strat_val = "LONG" if "長線" in _rsv_strat_radio else "SHORT"
    with rsv_c3:
        import json as _json
        if st.session_state.get("reserve_list"):
            _bk = _json.dumps(st.session_state.reserve_list, ensure_ascii=False, indent=2)
            st.download_button("💾 備份", data=_bk.encode("utf-8"),
                file_name=f"reserve_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json", key="dl_reserve_backup", use_container_width=True)
        else:
            st.button("💾", disabled=True, key="dl_rsv_bk_empty", use_container_width=True)
    with rsv_c4:
        st.markdown("""<style>
        div[data-testid="stFileUploader"] > section {
            border:none !important; background:transparent !important; padding:0 !important;
        }
        div[data-testid="stFileUploaderDropzoneInstructions"] { display:none !important; }
        div[data-testid="stFileUploader"] label { display:none !important; }
        </style>""", unsafe_allow_html=True)
        _up = st.file_uploader("還原", type=["json"], key="restore_reserve_upload",
                               label_visibility="collapsed")
        if _up:
            try:
                _restored = _json.loads(_up.read().decode("utf-8"))
                if isinstance(_restored, list) and len(_restored) > 0:
                    st.session_state.reserve_list = _restored
                    save_watchlist_to_github(
                        st.session_state.watchlist, st.session_state.watchlist_scan,
                        {k: v for k, v in st.session_state.get("etf_shares", {}).items() if v > 0},
                        reserve=_restored)
                    st.toast(f"✅ 已還原 {len(_restored)} 檔", icon="✅")
                    st.rerun()
            except Exception as _e:
                st.error(f"❌ {_e}")

    with rsv_c2:
        if st.button("🏹 加入儲備庫", key="rsv_add", use_container_width=True):
            # 解析代號和備註（如 "2345 散熱主力股"）
            _raw = rsv_sid.strip()
            _parts = _raw.split(" ", 1)
            sid_r = _parts[0].strip()
            rsv_note = _parts[1].strip() if len(_parts) > 1 else ""
            if sid_r and not any(r["id"] == sid_r for r in st.session_state.reserve_list):
                df_si_r, ok_si_r = get_stock_info()
                name_r = sid_r
                if ok_si_r and not df_si_r.empty:
                    row_r = df_si_r[df_si_r["stock_id"] == sid_r]
                    if not row_r.empty:
                        name_r = str(row_r["stock_name"].iloc[0])
                st.session_state.reserve_list.append({
                    "id": sid_r, "name": name_r,
                    "note": rsv_note.strip(),
                    "added_at": datetime.now().strftime("%Y-%m-%d"),
                    "strategy_tag": _rsv_strat_val,
                })
                # 存回 GitHub
                _wl = st.session_state.watchlist
                _sc = st.session_state.watchlist_scan
                _es = {k: v for k, v in st.session_state.etf_shares.items() if v > 0}
                save_watchlist_to_github(_wl, _sc, _es,
                                         reserve=st.session_state.reserve_list)
                st.toast(f"✅ {sid_r} {name_r} 已加入戰略儲備庫", icon="✅")
                st.rerun()
            elif not sid_r:
                st.warning("請輸入股票代號")

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════
    # ▌ 大盤層級全天候監控（系統核心定錨，永遠置頂，不可移除）
    # ──────────────────────────────────────────────────────────────
    # 設計說明：
    #   1. 此區塊完全獨立於 st.session_state.reserve_list，不寫入
    #      watchlist.json，僅在 UI 渲染層注入，保持資料檔案乾淨。
    #   2. 監控標的：台股加權指數(^TWII) ／ 費城半導體指數(^SOX)，
    #      代表「大盤層級」與「美股半導體領先指標」雙重宏觀防線。
    #   3. 100% 動態抓取 yfinance，拒絕任何寫死數值。
    #   4. 沒收刪除權限：UI不渲染移除按鈕，僅顯示「系統核心定錨」字樣。
    # ══════════════════════════════════════════════════════════════
    st.markdown("#### 🌐 大盤層級全天候監控（系統核心定錨）")
    st.caption("台股加權指數 ＋ 費城半導體指數，雙重宏觀防線，永遠置頂，剛性特赦不可移除")

    _index_anchors = [
        {"ticker": "^TWII", "name": "台股加權指數", "scope": "TOTAL_MARKET"},
        {"ticker": "^SOX",  "name": "費城半導體指數", "scope": "US_SEMICON"},
    ]

    _macro_overload_triggered = False  # 大盤總體重力超載旗標

    for _idx_item in _index_anchors:
        _idx_data = get_index_bias20(_idx_item["ticker"])

        if _idx_data is None:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03);border:1px solid #1e3a5f;"
                f"border-radius:8px;padding:12px 16px;margin:8px 0;'>"
                f"<b>{_idx_item['name']}</b>　"
                f"<span style='color:#7fb3d3;'>⚪ 資料載入中（yfinance連線中）</span>"
                f"</div>",
                unsafe_allow_html=True
            )
            continue

        _idx_price = _idx_data["price"]
        _idx_ma20  = _idx_data["ma20"]
        _idx_bias  = _idx_data["bias_20"]

        # ── 大盤層級乖離率燈號（門檻 8.0，較個股 10.0 更保守）
        if _idx_bias >= 8.0:
            _idx_color, _idx_icon, _idx_status = "#ff4444", "🔴", "過熱重災區"
            _macro_overload_triggered = True
        elif _idx_bias <= -8.0:
            _idx_color, _idx_icon, _idx_status = "#00cc66", "🟢", "底部超賣區"
        else:
            _idx_color, _idx_icon, _idx_status = "#fbbf24", "🟡", "正常區間"

        _idx_c1, _idx_c2 = st.columns([10, 2])
        with _idx_c1:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03);border:1px solid {_idx_color};"
                f"border-left:4px solid {_idx_color};border-radius:8px;padding:12px 16px;margin:8px 0;'>"
                f"<b style='color:#e8f4fd;font-size:1rem;'>{_idx_item['name']}</b>　"
                f"<span style='color:#9fb8d4;font-size:.85rem;'>"
                f"現價 {_idx_price:,.1f}　月線(20MA) {_idx_ma20:,.1f}</span><br>"
                f"<span style='color:{_idx_color};font-weight:600;'>"
                f"{_idx_icon} 月乖離 {_idx_bias:+.1f}%（{_idx_status}）</span>"
                f"</div>",
                unsafe_allow_html=True
            )
        with _idx_c2:
            # 沒收刪除權限：僅顯示靜態文字，不渲染任何刪除按鈕
            st.markdown(
                "<div style='margin-top:18px;color:#7a8fa0;font-size:.78rem;text-align:center;"
                "padding:6px 4px;background:rgba(255,255,255,0.02);border-radius:6px;'>"
                "🔒 系統核心定錨<br>（剛性特赦，不可移除）</div>",
                unsafe_allow_html=True
            )

    # ── 大盤總體重力超載警告（任一指數觸發即全域亮燈）
    if _macro_overload_triggered:
        st.error(
            "🚨 **總體重力超載：大盤月乖離已進入過熱重災區**　"
            "系統全域啟動以退為進防禦姿態，嚴禁對下方個股盲目追高！"
        )

    st.markdown("---")


    if not st.session_state.reserve_list:
        st.info("🏹 戰略儲備庫尚無標的，請從上方加入或從 Tab3 持股監控移入。")
    else:
        st.markdown(f"### 📡 精兵回頭草雷達｜共 {len(st.session_state.reserve_list)} 檔監控中")

        # ── 全局視角切換（影響所有精兵的買入決策建議）
        _rsv_view = st.radio(
            "買入視角",
            ["🛡️ 長線布局視角", "⚡ 短線突擊視角"],
            horizontal=True,
            key="tab4_view_mode",
            help="長線：負乖離大+融資大減=黃金布局點｜短線：籌碼分離=禁止，安全=快狠準"
        )
        _rsv_long_view = "長線" in _rsv_view
        triggered = []
        waiting   = []

        # 一次性讀取籌碼事實（快取5分鐘，避免重複IO）
        _rv_chips_map = get_chips_facts_map()

        for item in st.session_state.reserve_list:
            sid_rv       = item["id"]
            name_rv      = item.get("name", sid_rv)
            note_rv      = item.get("note", "")
            # 讀取策略標籤（可在加入儲備庫時設定，未設定則預設長線）
            strategy_rv  = item.get("strategy_tag", "LONG")
            strat_label  = "🛡️ 長線防空洞" if strategy_rv == "LONG" else "⚡ 短線突擊隊"

            # 讀取籌碼事實
            _rv_chip     = _rv_chips_map.get(sid_rv, {})
            _rv_margin   = _rv_chip.get("margin_chg_pct", None)   # 融資增減(%)
            _rv_foreign  = _rv_chip.get("foreign_net",   None)    # 外資買超(張)

            df_rv, ok_rv = load_price_csv(sid_rv)
            if not ok_rv or df_rv.empty or len(df_rv) < 10:
                waiting.append((sid_rv, name_rv, note_rv, None, None, "無K線資料",
                                False, False, False, None, None, None, "",
                                strategy_rv, strat_label, _rv_margin, _rv_foreign))
                continue

            df_rv = add_indicators(df_rv)
            lt_rv = df_rv.iloc[-1]
            close_rv  = float(lt_rv["Close"])
            ema5_rv   = float(lt_rv.get("EMA5",  float("nan")))
            sma20_rv  = float(lt_rv.get("MA20",  float("nan")))
            vol_rv    = float(lt_rv.get("Volume", 0))
            vma5_rv   = float(lt_rv.get("VMA5",  float("nan")))
            open_rv   = float(lt_rv.get("Open",  close_rv))
            # K線最新日期
            try:
                kline_date_rv = str(df_rv.index[-1])[:10]
            except:
                kline_date_rv = ""

            # 乖離率
            bias_rv = (close_rv - sma20_rv) / sma20_rv * 100 if not np.isnan(sma20_rv) and sma20_rv > 0 else float("nan")

            # 條件一：成交量連續3日萎縮（< VMA5 * 0.5）
            if not np.isnan(vma5_rv) and vma5_rv > 0 and len(df_rv) >= 4:
                vols = df_rv["Volume"].astype(float).tail(3).tolist()
                cond1 = all(v < vma5_rv * 0.5 for v in vols)
            else:
                cond1 = False

            # 條件二：乖離率 <= 5%
            cond2 = not np.isnan(bias_rv) and bias_rv <= 5

            # 條件三：今日收紅K 且現價 > EMA5
            cond3 = (close_rv > open_rv) and (not np.isnan(ema5_rv)) and (close_rv > ema5_rv)

            if cond1 and cond2 and cond3:
                triggered.append((sid_rv, name_rv, note_rv, close_rv, bias_rv,
                                  strategy_rv, strat_label, _rv_margin, _rv_foreign))
            else:
                conds_met = sum([cond1, cond2, cond3])
                # 計算量比（當日量/VMA5）
                vol_ratio_rv = round(vol_rv / vma5_rv, 2) if not np.isnan(vma5_rv) and vma5_rv > 0 else None
                waiting.append((sid_rv, name_rv, note_rv, close_rv, bias_rv,
                                f"{conds_met}/3 條件成立",
                                cond1, cond2, cond3,
                                vol_ratio_rv, ema5_rv, sma20_rv, kline_date_rv,
                                strategy_rv, strat_label, _rv_margin, _rv_foreign))

        # ══════════════════════════════════════════════
        # 📊 精兵回頭草總表排名
        # ══════════════════════════════════════════════
        st.markdown("#### 📊 精兵綜合評分排名")
        with st.expander("💡 9分制實戰執行意義與進場 SOP"):
            st.markdown("""
| 分數 | 意義 | 進場 SOP |
|------|------|---------|
| **7~9分 👑** | 安全基期已到，今日實質煞車收紅，籌碼極度冷靜 | **13:25 尾盤建立 1/3 底倉**，停損設 EMA5 下 |
| **5~6分 🎯** | 已到防守地基，心跳訊號初現，仍在洗盤 | **鎖定重點觀察**，早盤爆量突破直接閃擊 |
| **3~4分 🟠** | 部分條件成立，尚未完全沉澱 | **耐心等待**，不宜進場 |
| **0~2分 ⏳** | 高空過熱，短線乖離過大 | **嚴禁追高**，靜待回檔排毒 |
            """)

        _rank_rows = []
        def _calc_score(cond1, cond2, cond3, vol_ratio, bias):
            """量化三條件各給 0-3 分，總分最高 9 分"""
            # 條件一：量縮評分（量比越低越好）
            if cond1:
                s1 = 3 if (vol_ratio is not None and vol_ratio < 0.3) else 2
            else:
                s1 = 1 if (vol_ratio is not None and vol_ratio < 0.8) else 0
            # 條件二：乖離評分（乖離越低越好）
            if cond2:
                b = bias if (bias is not None and not np.isnan(bias)) else 5
                s2 = 3 if b <= 1 else (2 if b <= 3 else 1)
            else:
                b = bias if (bias is not None and not np.isnan(bias)) else 99
                s2 = 1 if b <= 8 else 0
            # 條件三：收紅EMA5（是/否）
            s3 = 3 if cond3 else 0
            return s1 + s2 + s3, s1, s2, s3

        # triggered 全部 3/3
        for sid_rv, name_rv, note_rv, close_rv, bias_rv in triggered:
            _vr = None
            _total, _s1, _s2, _s3 = _calc_score(True, True, True, _vr, bias_rv)
            _rank_rows.append({
                "股號": sid_rv, "名稱": name_rv,
                "現價": f"{close_rv:.1f}" if close_rv else "—",
                "乖離%": f"{bias_rv:+.1f}" if close_rv and not np.isnan(bias_rv) else "—",
                "量縮": f"✅{_s1}",  "乖離≤5%": f"✅{_s2}", "收紅EMA5": f"✅{_s3}",
                "總分/9": _total, "_score": _total, "K線日期": kline_date_rv,
            })
        # waiting
        for _w in waiting:
            if len(_w) < 9:
                continue
            sid_rv, name_rv, note_rv, close_rv, bias_rv, status = _w[:6]
            _c1, _c2, _c3 = _w[6], _w[7], _w[8]
            _vr9 = _w[9] if len(_w) > 9 else None
            _total, _s1, _s2, _s3 = _calc_score(_c1, _c2, _c3, _vr9, bias_rv)
            _rank_rows.append({
                "股號": sid_rv, "名稱": name_rv,
                "現價": f"{close_rv:.1f}" if close_rv else "—",
                "乖離%": f"{bias_rv:+.1f}" if close_rv and not np.isnan(bias_rv) else "—",
                "量縮": f"✅{_s1}" if _c1 else f"❌{_s1}",
                "乖離≤5%": f"✅{_s2}" if _c2 else f"❌{_s2}",
                "收紅EMA5": f"✅{_s3}" if _c3 else f"❌{_s3}",
                "總分/9": _total, "_score": _total,
                "K線日期": _w[12] if len(_w) > 12 else "",
            })

        # ── V6 三軌聯鎖風控斷路器
        _risk_st, _risk_i = get_system_risk_status()
        _is_danger        = _risk_st == "RED_ALERT"
        _is_yellow        = _risk_st == "YELLOW_ALERT"
        _is_short_squeeze = _risk_st == "SHORT_SQUEEZE"
        _tx_net    = _risk_i["tx_net"]
        _mtx_retail = _risk_i["mtx_retail"]
        _pc_ratio  = _risk_i["pc_ratio"]
        _days_evt  = _risk_i["days"]
        _evt_name  = _risk_i["event"]
        if _is_danger:
            st.error(f"🔴 **【高度警戒・強制禁買】** 大台空單 {abs(_tx_net):,}口 × 散戶多單 {_mtx_retail:,}口"
                     f"{'　⚡ 總經核彈倒數'+str(_days_evt)+'天：'+_evt_name[:15] if _days_evt<=3 else ''}"
                     f"　CBOE P/C {_pc_ratio:.2f}。**台美散戶瘋狂接刀，系統強制關閉所有買進權限！**")
        elif _is_yellow:
            st.warning(f"🟡 **【常規警戒・資金減半】** 大台空單 {abs(_tx_net):,}口 × 散戶多單 {_mtx_retail:,}口！"
                       f"**系統強制限制進場資金砍半（僅能建立 1/6 底倉），控管風險邊界！**")
        elif _is_short_squeeze:
            st.info(f"🔥 **【黃金軋空・全力進擊】** 散戶放空 {abs(_mtx_retail):,}口 × CBOE P/C {_pc_ratio:.2f}（恐慌頂點）！"
                    f"**台美散戶同步嚇破膽，解鎖軋空特赦！短線火箭全面放行甚至加碼！**")
        else:
            st.success(f"🟢 **【全球環境安全】** 大台 {_tx_net:+,}口 × 散戶 {_mtx_retail:+,}口"
                       f"　CBOE P/C {_pc_ratio:.2f}，台美籌碼結構正常，依正常 SOP 執行。")

        if _rank_rows:
            _rank_rows.sort(key=lambda x: (-x["_score"], x["股號"]))
            _cards = []
            for _r in _rank_rows:
                _sc = _r["_score"]
                if _sc >= 7:
                    if _is_danger:
                        _rc = "#e11d48"; _bg = "background:rgba(244,63,94,0.12);border-left:5px solid #e11d48;"
                        _sop = f"🛑 <b>【高度警戒・強制禁買】</b>：個股雖達 <b>{_sc}分</b>，但大台空單 {abs(_tx_net):,} 口+小台散戶多單 {_mtx_retail:,} 口！土石流即將無差別拋售，<b>系統強制關閉交易！</b>"
                    elif _is_yellow:
                        _rc = "#fbbf24"; _bg = "background:rgba(251,191,36,0.08);border-left:5px solid #fbbf24;"
                        if _rsv_long_view:
                            _sop = f"🛡️ <b>【黃燈警戒・長線視角】</b>：個股技術面 <b>{_sc}分</b>，大盤黃燈但長線布局不受短線大盤影響。融資若持續大減，此處更是長線黃金種子倉建立點，建倉資金砍半保守建立！"
                        else:
                            _sop = f"⚠️ <b>【常規警戒・資金減半】</b>：個股技術面 <b>{_sc}分</b>，但大盤進入黃燈警戒。<b>建倉資金強制砍半（僅能建立 1/6 底倉）</b>，嚴格控管風險！"
                    elif _is_short_squeeze:
                        _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.12);border-left:5px solid #ff9900;"
                        _sop = f"🚀 <b>【黃金軋空・全力進擊】</b>：個股高達 <b>{_sc}分</b>，且散戶放空 {abs(_mtx_retail):,} 口！史詩級軋空點，建議尾盤無懸念建立 1/3~1/2 波段先鋒倉！"
                    elif _is_short_squeeze:
                        _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.12);border-left:5px solid #ff9900;"
                        _sop = f"🔥 <b>【黃金軋空・全力進擊】</b>：個股高達 <b>{_sc}分</b>，台美散戶同步嚇破膽放空！市場具備強烈軋空基因，允許利用小股期進行非對稱動能加壓閃擊！"
                    else:
                        if _rsv_long_view:
                            _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.08);border-left:5px solid #ff9900;"
                            # 長線視角：讀籌碼判斷是否為黃金布局點
                            _rv_c   = _rv_chips_map.get(_r["股號"], {})
                            _rv_mg  = _rv_c.get("margin_chg_pct", None)
                            _rv_fgn = _rv_c.get("foreign_net", None)
                            _rv_bi  = float(_r["乖離%"].replace("+","")) if _r["乖離%"] != "—" else 0
                            if _rv_mg is not None and _rv_mg <= -2 and _rv_bi <= -5:
                                _sop = (f"💡 <b>【長線黃金埋伏點】</b>：技術面 {_sc}分，"
                                        f"且融資大減 {abs(_rv_mg):.1f}%（散戶洗盤），"
                                        f"乖離 {_rv_bi:+.1f}%（極度超賣）。"
                                        f"<b>動用場外現金分批砸入第1筆現貨種子部位，雷打不動！</b>")
                            else:
                                _fgn_txt = f"外資 {_rv_fgn:+,.0f}張" if _rv_fgn is not None else "外資數據待更新"
                                _sop = (f"🛡️ <b>【長線布局觀察中】</b>：技術面 {_sc}分，"
                                        f"籌碼：{_fgn_txt}｜融資增減 {f'{_rv_mg:+.2f}%' if _rv_mg is not None else '—'}。"
                                        f"等待乖離擴大或融資大減後分批建立長線底倉。")
                        else:
                            _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.08);border-left:5px solid #ff9900;"
                            _sop = f"👑 <b>【短線黃金特赦區 SOP】</b>：大盤環境安全，個股拉回止跌！建議 <b>13:25 尾盤建立 1/3 常規基本底倉</b>，停損設 EMA5 下。"
                elif _sc >= 5:
                    if _is_danger:
                        _rc = "#8892b0"; _bg = "background:rgba(255,255,255,0.01);border-left:5px solid #44475a;"
                        _sop = f"⏳ <b>【高度警戒・取消閃擊】</b>：個股蓄勢（{_sc}分），但大台空單 {abs(_tx_net):,}+散戶多單 {_mtx_retail:,} 口，取消進場，繼續觀望。"
                    elif _is_yellow:
                        _rc = "#fbbf24"; _bg = "background:rgba(251,191,36,0.05);border-left:5px solid #fbbf24;"
                        if _rsv_long_view:
                            _rv_c3  = _rv_chips_map.get(_r["股號"], {})
                            _rv_mg3 = _rv_c3.get("margin_chg_pct", None)
                            if _rv_mg3 is not None and _rv_mg3 <= -2:
                                _sop = f"💡 <b>【黃燈+融資大減=長線洗盤進行中】</b>：個股蓄勢（{_sc}分），大盤黃燈但融資大減 {abs(_rv_mg3):.1f}%。長線視角：散戶正在被清洗，是長線布局的前置訊號，繼續追蹤！"
                            else:
                                _sop = f"🛡️ <b>【黃燈・長線觀察中】</b>：個股蓄勢（{_sc}分），大盤黃燈。長線繼續沉澱，等待融資大減訊號出現。"
                        else:
                            _sop = f"⚠️ <b>【黃燈警戒・延後閃擊】</b>：個股蓄勢（{_sc}分），但大盤黃燈，等黃燈解除後再執行閃擊計畫。"
                    elif _is_short_squeeze:
                        _rc = "#ffee55"; _bg = "background:rgba(255,238,85,0.08);border-left:5px solid #ffee55;"
                        _sop = f"🎯 <b>【蓄勢+軋空加持】</b>：個股蓄勢（{_sc}分）且散戶放空 {abs(_mtx_retail):,} 口，<b>早盤爆量即刻閃擊！</b>"
                    else:
                        if _rsv_long_view:
                            _rc = "#ffee55"; _bg = "background:rgba(255,238,85,0.06);border-left:5px solid #ffee55;"
                            _rv_c2  = _rv_chips_map.get(_r["股號"], {})
                            _rv_mg2 = _rv_c2.get("margin_chg_pct", None)
                            _rv_bi2 = float(_r["乖離%"].replace("+","")) if _r["乖離%"] != "—" else 0
                            if _rv_mg2 is not None and _rv_mg2 <= -2:
                                _sop = (f"💡 <b>【長線蓄勢+洗盤中】</b>：技術面 {_sc}分，"
                                        f"融資大減 {abs(_rv_mg2):.1f}%，散戶持續出清。"
                                        f"長線繼續觀察，等3條件全觸發後分批建倉。")
                            else:
                                _sop = f"🛡️ <b>【長線繼續沉澱】</b>：技術面 {_sc}分，籌碼尚未充分沉澱。耐心等待量縮+乖離回落+融資減少三條件齊發。"
                        else:
                            _rc = "#ffee55"; _bg = "background:rgba(255,238,85,0.06);border-left:5px solid #ffee55;"
                            _sop = "🎯 <b>【短線動能蓄勢區 SOP】</b>：已到技術防守地基，建議<b>鎖定為首選儲備</b>，早盤爆量突破 EMA5 直接閃擊。"
                elif _sc >= 3:
                    _rc = "#ff6b35"; _bg = "background:rgba(255,107,53,0.05);border-left:5px solid #ff6b35;"
                    _sop = "🟠 <b>【條件未齊 SOP】</b>：部分訊號成立，繼續等待量縮或乖離回落，<b>耐心觀察，不宜進場</b>。"
                else:
                    _rc = "#8892b0"; _bg = "background:rgba(255,255,255,0.01);border-left:5px solid #44475a;"
                    _sop = "⏳ <b>【高空過熱區 SOP】</b>：高高掛在天上，短線乖離過大！<b>嚴禁手癢追高</b>，靜待其回檔打底。"

                _cards.append(
                    f"<div style='font-size:.85rem;margin-bottom:10px;padding:8px 14px;"
                    f"border-radius:6px;{_bg}color:{_rc};line-height:1.7;'>"
                    f"<div style='font-size:.9rem;'>"
                    f"<b>{_r['股號']} {_r['名稱']}</b>｜"
                    f"總分 <span style='font-size:1.1rem;font-weight:700;'>{_sc}/9</span>｜"
                    f"現價 {_r['現價']}｜乖離 {_r['乖離%']}｜"
                    f"量縮 {_r['量縮']}｜乖離≤5% {_r['乖離≤5%']}｜收紅EMA5 {_r['收紅EMA5']}｜"
                    f"<span style='color:#7fb3d3;font-size:.78rem;'>K線:{_r.get('K線日期','—')}</span>"
                    f"</div>"
                    f"<div style='margin-top:5px;padding:4px 10px;background:rgba(0,0,0,0.25);"
                    f"border-radius:4px;font-size:.8rem;color:#ffffff;"
                    f"border-top:1px dashed rgba(255,255,255,0.1);'>{_sop}</div>"
                    f"</div>"
                )
            st.markdown("".join(_cards), unsafe_allow_html=True)

        # ══════════════════════════════════════════════
        # ⚠️ 異常變盤因果律健檢面板（100% 動態，拒絕寫死）
        # ──────────────────────────────────────────────
        # 邏輯：
        #   1. 動態遍歷 st.session_state.reserve_list（拒絕硬編碼任何股票代號）
        #   2. 即時讀取 margin.csv（融資增減%）與 chips_data.csv（外資買賣超）
        #   3. 即時計算 20MA 乖離率，動態判定 market_level：
        #      bias_20 >= +10% → HIGH_RISK（高位階重災區）
        #      bias_20 <= -10% → BOTTOM_SAFE（底部隔離避風港）
        #      其餘           → NORMAL（橫盤監控區）
        #   4. 交叉比對 strategy_type（LONG/SHORT）+ market_level + 籌碼 Facts，
        #      動態輸出「以退為進」或「鑽石級布局」指令
        # ══════════════════════════════════════════════
        st.markdown("---")
        st.markdown("#### ⚠️ 異常變盤因果律健檢面板")
        st.caption("動態掃描儲備庫全部標的，交叉比對乖離率位階 × 籌碼真實 Facts，自動觸發變盤警示")

        _yn_chips_map = get_chips_facts_map()  # 一次性讀取，5分鐘快取，不重複IO
        _yn_alert_count = 0

        for _yn_item in st.session_state.reserve_list:
            _yn_sid   = _yn_item["id"]
            _yn_name  = _yn_item.get("name", _yn_sid)
            _yn_strat = _yn_item.get("strategy_tag", "LONG")  # 預設長線

            # ── 即時讀取K線，動態計算現價與20MA
            try:
                _yn_df, _yn_ok = load_price_csv(_yn_sid)
                if not _yn_ok or _yn_df.empty or len(_yn_df) < 20:
                    continue
                _yn_closes = pd.to_numeric(_yn_df["Close"], errors="coerce").dropna()
                if len(_yn_closes) < 20:
                    continue
                _yn_cp   = float(_yn_closes.iloc[-1])
                _yn_ma20 = float(_yn_closes.tail(20).mean())
                del _yn_df
                import gc; gc.collect()
            except Exception:
                continue

            # ── 即時籌碼 Facts（融資增減% + 外資買賣超張）
            _yn_chip   = _yn_chips_map.get(_yn_sid, {})
            _yn_margin = _yn_chip.get("margin_chg_pct", None)
            _yn_fgn    = _yn_chip.get("foreign_net",   None)

            # ── 呼叫全域共用的異常變盤因果律決策函數
            _yn_result = check_anomaly_variant(
                stock_id=f"{_yn_sid} {_yn_name}",
                strategy_type=_yn_strat,
                current_price=_yn_cp,
                ma20=_yn_ma20,
                foreign_buy=_yn_fgn,
                margin_change=_yn_margin,
            )

            if not _yn_result["triggered"]:
                continue

            _yn_alert_count += 1

            if _yn_result["level"] == "AS_RETREAT":
                _yn_c1, _yn_c2 = st.columns([10, 1])
                with _yn_c1:
                    st.error(_yn_result["message"])
                with _yn_c2:
                    st.markdown("<div style='margin-top:30px;'></div>", unsafe_allow_html=True)
                    if st.button("🗑️ 物理除名", key=f"yn_remove_{_yn_sid}"):
                        st.session_state.reserve_list = [
                            r for r in st.session_state.reserve_list if r["id"] != _yn_sid
                        ]
                        st.success(f"已將 {_yn_sid} {_yn_name} 從戰備庫物理除名")
                        st.rerun()

            elif _yn_result["level"] == "DIAMOND_BUY":
                st.info(_yn_result["message"])

        if _yn_alert_count == 0:
            st.caption("✅ 目前儲備庫全數標的籌碼結構正常，未觸發任何異常變盤因果律警示。")

        # ══════════════════════════════════════════════
        # 🕵️ 潛伏期法人鎖碼雷達掃描（快取1小時）
        # ══════════════════════════════════════════════
        st.markdown("#### 🕵️ 潛伏期法人暗中鎖碼雷達")

        def _cached_accum_scan(reserve_ids_tuple):
            alerts, watch = [], []
            for sid_ac, name_ac in reserve_ids_tuple:
                ac = scan_accumulation_phase(sid_ac)

                if ac["alert"]:
                    alerts.append((sid_ac, name_ac, ac))
                elif ac["facts"]:
                    watch.append((sid_ac, name_ac, ac))
            return alerts, watch

        _reserve_ids_tuple = tuple((r["id"], r.get("name", r["id"]))
                                   for r in st.session_state.reserve_list)
        _accum_alerts = []
        _accum_watch  = []
        with st.spinner("掃描潛伏期籌碼密度中..."):
            _accum_alerts, _accum_watch = _cached_accum_scan(_reserve_ids_tuple)

        # 合併所有標的，統一渲染
        # 排序：3/3 > 2/3 > 1/3 > 0/3，同分依股號
        _all_accum = sorted(
            _accum_alerts + _accum_watch,
            key=lambda x: (-x[2]["facts"].get("conds", 0), x[0])
        )
        if not _all_accum:
            st.caption("⏳ 目前儲備庫無標的觸發潛伏期鎖碼警報。")

        else:

            _rows_html = []
            for _sid_ac, _name_ac, _ac in _all_accum:
                _f     = _ac["facts"]
                _conds = _f.get("conds", 0)

                # 條件達成率顏色
                if _conds == 3:
                    _col  = "#ffee55"
                    _ico  = "👑"
                    _label = "3/3 戰略黃金起漲點"
                    _bg   = "background:rgba(255,238,85,0.08);border-left:4px solid #ffee55;"
                elif _conds == 2:
                    _col  = "#ffcc00"
                    _ico  = "🟡"
                    _label = "2/3 高度關注臨界點"
                    _bg   = "background:rgba(255,204,0,0.06);border-left:4px solid #ffcc00;"
                elif _conds == 1:
                    _col  = "#ff6b35"
                    _ico  = "🟠"
                    _label = "1/3 條件成立"
                    _bg   = "background:rgba(255,107,53,0.04);border-left:4px solid #ff6b35;"
                else:
                    _col  = "#8892b0"
                    _ico  = "⏳"
                    _label = "0/3 條件成立"
                    _bg   = "background:rgba(255,255,255,0.01);border-left:4px solid #44475a;"

                # 張數（台股：紅買 綠賣）
                _i5  = int(_f.get("inst_5d",  0))
                _i15 = int(_f.get("inst_15d", 0))
                _c5  = "#ff3333" if _i5  >= 0 else "#00cc44"
                _c15 = "#ff3333" if _i15 >= 0 else "#00cc44"
                _s5  = f"+{_i5:,}"  if _i5  >= 0 else f"{_i5:,}"
                _s15 = f"+{_i15:,}" if _i15 >= 0 else f"{_i15:,}"

                _rows_html.append(
                    f"<div style='font-size:.84rem;margin-bottom:8px;padding:6px 12px;"
                    f"border-radius:4px;{_bg}color:{_col};'>"
                    f"{_ico} <b>{_sid_ac} {_name_ac}</b>｜"
                    f"箱體 {_f.get('box_amp','—')}%｜"
                    f"大戶 <b>{_f.get('big_pct','—')}%</b>｜"
                    f"投信連買 <u>{_f.get('inst_streak',0)} 天</u>（起:{_f.get('inst_streak_start','')}）｜"
                    f"近5日 <span style='color:{_c5};font-weight:700;'>{_s5}張</span>｜"
                    f"15日 <span style='color:{_c15};font-weight:700;'>{_s15}張</span>｜"
                    f"<span style='background:rgba(255,255,255,0.08);padding:1px 6px;"
                    f"border-radius:3px;font-size:.78rem;'>{_label}</span>"
                    f"</div>"
                )

            st.markdown(
                "<div style='background:rgba(0,0,0,0.2);border:1px solid #1e3a5f;"
                "border-radius:10px;padding:10px 4px;'>"
                + "".join(_rows_html) + "</div>",
                unsafe_allow_html=True
            )

        st.markdown("---")

        # ══════════════════════════════════════════════
        # 🚀 短線火箭雷達（儲備庫個股，快取1小時）
        # ══════════════════════════════════════════════
        st.markdown("#### 🚀 短線火箭雷達")

        def _cached_rocket_scan(reserve_tuple):
            results = []
            for sid_r, name_r in reserve_tuple:
                rm = scan_short_term_momentum(sid_r)
                rm["sid"] = sid_r; rm["name"] = name_r
                results.append(rm)
            return results

        _reserve_tuple_rk = tuple((r["id"], r.get("name", r["id"]))
                                   for r in st.session_state.reserve_list)
        _rocket_results = _cached_rocket_scan(_reserve_tuple_rk)

        # 全部合併，依觸發狀態+分數排序，統一色卡渲染
        _rocket_results.sort(key=lambda x: (x.get("trigger",False), x.get("score",0)), reverse=True)
        if _rocket_results:
            _rows_rk4 = []
            for _r in _rocket_results:
                _f   = _r.get("facts", {})
                _sc  = _r.get("score", 0)
                _trg = _r.get("trigger", False)
                # 顏色
                if _trg:
                    _col = "#ffee55"; _ico = "🚀"; _bg = "background:rgba(255,238,85,0.08);border-left:4px solid #ffee55;"
                elif _sc >= 2:
                    _col = "#ff9900"; _ico = "⚡"; _bg = "background:rgba(255,153,0,0.06);border-left:4px solid #ff9900;"
                elif _sc == 1:
                    _col = "#ff6b35"; _ico = "🟠"; _bg = "background:rgba(255,107,53,0.04);border-left:4px solid #ff6b35;"
                else:
                    _col = "#8892b0"; _ico = "⏳"; _bg = "background:rgba(255,255,255,0.01);border-left:4px solid #44475a;"
                # 法人張數（台股紅買綠賣）
                _inst = _f.get("inst_net3", 0)
                _ic   = "#ff3333" if _inst >= 0 else "#00cc44"
                _is   = f"+{int(_inst):,}" if _inst >= 0 else f"{int(_inst):,}"
                _label = f"觸發！得分{_sc}/3" if _trg else f"{_sc}/3 條件成立"
                # 法人最新日期
                _detail = _f.get("inst_net3_detail", [])
                _last_date = _detail[-1][0][5:] if _detail else ""  # 只取最新日期 MM/DD
                _detail_str = f"<span style='color:#888;font-size:.72rem;'>({_last_date})</span>" if _last_date else ""
                # 融資近3日明細
                _md = _f.get("margin_detail", [])
                _m3 = _f.get("margin_change_3d", 0)
                _m5 = _f.get("margin_change_5d", 0)
                _m3c = "#00cc44" if _m3 <= 0 else "#ff3333"
                _m5c = "#00cc44" if _m5 <= 0 else "#ff3333"
                _mlast = _md[-1][0][5:] if _md else ""
                _margin_str = (
                    f"融資 <span style='color:{_m3c};'>3d:{_m3:+.1f}%</span>"
                    f"/<span style='color:{_m5c};'>5d:{_m5:+.1f}%</span>"
                    + (f"<span style='color:#888;font-size:.72rem;'>({_mlast})</span>" if _mlast else "")
                )
                _rows_rk4.append(
                    f"<div style='font-size:.84rem;margin-bottom:8px;padding:6px 12px;"
                    f"border-radius:4px;{_bg}color:{_col};'>"
                    f"{_ico} <b>{_r['sid']} {_r['name']}</b>｜"
                    f"法人3日 <span style='color:{_ic};font-weight:700;'>{_is}張</span>"
                    f" {_detail_str}｜"
                    f"{_margin_str}｜"
                    f"資券比 {_f.get('margin_short_ratio',0):.1f}%｜"
                    f"<span style='background:rgba(255,255,255,0.08);padding:1px 6px;"
                    f"border-radius:3px;font-size:.78rem;'>{_label}</span>"
                    f"</div>"
                )
            st.markdown(
                "<div style='background:rgba(0,0,0,0.2);border:1px solid #1e3a5f;"
                "border-radius:10px;padding:10px 4px;'>"
                + "".join(_rows_rk4) + "</div>",
                unsafe_allow_html=True
            )

        st.markdown("---")

        # ── 觸發警報顯示
        if triggered:
            for _t in triggered:
                sid_rv, name_rv, note_rv, close_rv, bias_rv = _t[:5]
                _t_strat  = _t[5] if len(_t) > 5 else "LONG"
                _t_slabel = _t[6] if len(_t) > 6 else "🛡️ 長線防空洞"
                _t_margin = _t[7] if len(_t) > 7 else None
                _t_fgn    = _t[8] if len(_t) > 8 else None

                _alert_msg = (
                    f"🎯 精兵回頭草警報：戰略儲備股 {name_rv}（{sid_rv}）已在冷宮完成沉澱！"
                    f" 今日現價 {close_rv:.1f} 元，與月線乖離率僅 +{bias_rv:.1f}%（符合<5%限制），"
                    f"且成交量極致萎縮後首度帶量收復5日線。"
                    f" 基本面基因優良，短線防禦安全邊際極高，准許執行手動第二波精準獵殺！"
                )
                st.info(_alert_msg)

                # ── 買入決策提示（依策略標籤分軌）
                _chips_txt = (
                    f"融資增減 {_t_margin:+.2f}%｜" if _t_margin is not None else ""
                ) + (
                    f"外資 {_t_fgn:+,.0f} 張" if _t_fgn is not None else "籌碼數據待更新"
                )
                if _t_strat == "LONG":
                    st.success(
                        f"💡 **{sid_rv} 長線黃金布局點確認**\n\n"
                        f"籌碼狀態：{_chips_txt}\n\n"
                        f"精兵回頭草三條件全數觸發！長線特性屏蔽短線雜訊，"
                        f"此處為絕佳長線種子部位建立點。"
                        f"建議動用場外現金**分批砸入第1筆現貨**，雷打不動！"
                    )
                else:
                    _chips_sep = ((_t_fgn or 0) < -500) or ((_t_margin or 0) > 3.0)
                    if _chips_sep:
                        st.error(
                            f"🚨 **{sid_rv} 短線｜籌碼分離警告，禁止進場**\n\n"
                            f"籌碼狀態：{_chips_txt}\n\n"
                            f"雖然技術面觸發回頭草，但籌碼發生惡性分離！"
                            f"外資調節+融資散戶逆向接刀，時機點不對，**系統強制封印！**"
                        )
                    else:
                        st.warning(
                            f"⚡ **{sid_rv} 短線突擊准許進場**\n\n"
                            f"籌碼狀態：{_chips_txt}\n\n"
                            f"籌碼結構健康，短線回頭草三條件觸發。"
                            f"快狠準進場，**設好停損後立即執行**，嚴禁抱成長線！"
                        )

        # ── 等待中標的（依條件數排序，同分依股號，色卡格式）
        st.markdown("#### ⏳ 籌碼沉澱中...")
        rm_rsv = None
        waiting.sort(key=lambda x: (-int(x[5].split("/")[0]) if "/" in x[5] else 0, x[0]))
        _wait_rows = []
        _wait_rm_btns = []
        for _w in waiting:
            sid_rv, name_rv, note_rv, close_rv, bias_rv, status = _w[:6]
            _c1 = _w[6] if len(_w) > 6 else None
            _c2 = _w[7] if len(_w) > 7 else None
            _c3 = _w[8] if len(_w) > 8 else None
            _vr = _w[9] if len(_w) > 9 else None
            _e5 = _w[10] if len(_w) > 10 else None
            _s20= _w[11] if len(_w) > 11 else None
            _kd = _w[12] if len(_w) > 12 else ""

            conds_n = int(status.split("/")[0]) if "/" in status else 0
            if conds_n == 2:
                _col = "#ffcc00"; _ico = "🟡"
                _bg  = "background:rgba(255,204,0,0.06);border-left:4px solid #ffcc00;"
            elif conds_n == 1:
                _col = "#ff6b35"; _ico = "🟠"
                _bg  = "background:rgba(255,107,53,0.04);border-left:4px solid #ff6b35;"
            else:
                _col = "#8892b0"; _ico = "⏳"
                _bg  = "background:rgba(255,255,255,0.01);border-left:4px solid #44475a;"

            # 條件指示燈
            _c1_txt = f"<span style='color:{'#ff3333' if _c1 else '#546e7a'}'>{'✅' if _c1 else '❌'}量縮</span>" if _c1 is not None else ""
            _c2_txt = f"<span style='color:{'#ff3333' if _c2 else '#546e7a'}'>{'✅' if _c2 else '❌'}乖離{'≤5%' if _c2 else f'{bias_rv:+.1f}%'}</span>" if _c2 is not None and close_rv else ""
            _c3_txt = f"<span style='color:{'#ff3333' if _c3 else '#546e7a'}'>{'✅' if _c3 else '❌'}收紅守EMA5</span>" if _c3 is not None else ""
            _vr_txt = f"量比{_vr:.2f}" if _vr is not None else ""
            _e5_txt = f"EMA5={_e5:.1f}" if _e5 and not np.isnan(_e5) else ""
            _note_txt = f"　💬{note_rv}" if note_rv else ""

            _close_str = f"{close_rv:.1f}元｜" if close_rv else "—｜"
            _bias_str  = f"乖離{bias_rv:+.1f}%｜" if close_rv and not np.isnan(bias_rv) else ""
            _kd_str    = f"<span style='color:#888;font-size:.72rem;'>K線:{_kd}</span>｜" if _kd else ""

            _wait_rows.append(
                f"<div style='font-size:.83rem;margin-bottom:4px;padding:6px 12px;"
                f"border-radius:4px;{_bg}color:{_col};'>"
                f"{_ico} <b>{sid_rv} {name_rv}</b>｜"
                f"{_close_str}{_bias_str}{_kd_str}"
                f"{_c1_txt} {_c2_txt} {_c3_txt}"
                f"{'｜'+_vr_txt if _vr_txt else ''}{'｜'+_e5_txt if _e5_txt else ''}"
                f"{_note_txt}"
                f"</div>"
            )

            # ── 策略標籤 + 買入決策提示（在色卡下方）
            _w_strat  = _w[13] if len(_w) > 13 else "LONG"
            _w_slabel = _w[14] if len(_w) > 14 else "🛡️ 長線防空洞"
            _w_margin = _w[15] if len(_w) > 15 else None
            _w_fgn    = _w[16] if len(_w) > 16 else None
            _w_bias   = bias_rv if close_rv and not np.isnan(bias_rv if bias_rv is not None else float('nan')) else None

            # 決策提示：長線 - 負乖離≤-10% + 融資大減
            if _w_strat == "LONG" and _w_bias is not None and _w_bias <= -10 and (_w_margin or 0) <= -2.0:
                _w_chips_txt = f"融資大減 {abs(_w_margin):.2f}%｜外資 {_w_fgn:+,.0f}張" if _w_fgn is not None else f"融資大減 {abs(_w_margin):.2f}%"
                _wait_rows.append(
                    f"<div style='font-size:.8rem;margin-bottom:8px;padding:6px 12px;"
                    f"background:rgba(0,212,255,0.08);border-left:3px solid #00d4ff;"
                    f"border-radius:4px;color:#c8dff0;'>"
                    f"💡 <b>{sid_rv} 長線黃金埋伏點</b>｜{_w_slabel}｜{_w_chips_txt}<br>"
                    f"月線負乖離 {_w_bias:.1f}%（極度超賣）+ 散戶融資割肉，大戶低位惡意洗盤。"
                    f"90%勝率長線布局點，動用場外現金分批砸入第1筆種子部位，雷打不動！"
                    f"</div>"
                )
            # 決策提示：短線 - 籌碼分離警告
            elif _w_strat == "SHORT":
                _chips_sep = ((_w_fgn or 0) < -500) or ((_w_margin or 0) > 3.0)
                if _chips_sep:
                    _w_chips_txt = f"外資 {_w_fgn:+,.0f}張｜融資 {_w_margin:+.2f}%" if _w_margin is not None and _w_fgn is not None else "籌碼分離偵測"
                    _wait_rows.append(
                        f"<div style='font-size:.8rem;margin-bottom:8px;padding:6px 12px;"
                        f"background:rgba(255,68,68,0.08);border-left:3px solid #ff4444;"
                        f"border-radius:4px;color:#ffaaaa;'>"
                        f"🚨 <b>{sid_rv} 短線禁止進場</b>｜{_w_slabel}｜{_w_chips_txt}<br>"
                        f"籌碼惡性分離！外資倒貨+散戶融資逆向接刀，時機極度危險，系統強制封印！"
                        f"靜待融資徹底踩踏崩潰後再重回視線。"
                        f"</div>"
                    )
                else:
                    _wait_rows.append(
                        f"<div style='font-size:.78rem;margin-bottom:8px;padding:4px 10px;"
                        f"background:rgba(255,152,0,0.06);border-left:2px solid #ff9800;"
                        f"border-radius:4px;color:#ffcc80;'>"
                        f"⚡ {sid_rv} {_w_slabel}｜籌碼正常，靜待技術面觸發"
                        f"</div>"
                    )
            else:
                _wait_rows.append(
                    f"<div style='font-size:.78rem;margin-bottom:8px;padding:4px 10px;"
                    f"background:rgba(0,0,0,0.1);border-radius:4px;color:#7fb3d3;'>"
                    f"　{_w_slabel}｜繼續沉澱中，等待條件成熟"
                    f"</div>"
                )

            _wait_rm_btns.append(sid_rv)

        if _wait_rows:
            # _wait_rows 和 _wait_rm_btns 數量可能不同（每股有多行HTML）
            # 改為直接在迴圈裡渲染，不用 zip
            _btn_idx = 0
            _row_idx = 0
            while _row_idx < len(_wait_rows):
                _wrow = _wait_rows[_row_idx]
                # 判斷是否是主色卡行（有刪除按鈕）還是決策提示行（不需要按鈕）
                _is_main_card = (_btn_idx < len(_wait_rm_btns) and
                                 f"<b>{_wait_rm_btns[_btn_idx]}" in _wrow)
                if _is_main_card:
                    _wc_main, _wc_btn = st.columns([20, 1])
                    with _wc_main:
                        st.markdown(_wrow, unsafe_allow_html=True)
                    with _wc_btn:
                        st.markdown("<div style='margin-top:4px;'></div>", unsafe_allow_html=True)
                        if st.button("✕", key=f"rsv_rm_{_wait_rm_btns[_btn_idx]}", use_container_width=True):
                            rm_rsv = _wait_rm_btns[_btn_idx]
                    _btn_idx += 1
                else:
                    # 決策提示行，不需要刪除按鈕
                    st.markdown(_wrow, unsafe_allow_html=True)
                _row_idx += 1

        if rm_rsv:
            st.session_state.reserve_list = [
                r for r in st.session_state.reserve_list if r["id"] != rm_rsv
            ]
            _wl = st.session_state.watchlist
            _sc = st.session_state.watchlist_scan
            _es = {k: v for k, v in st.session_state.etf_shares.items() if v > 0}
            save_watchlist_to_github(_wl, _sc, _es,
                                     reserve=st.session_state.reserve_list)
            st.rerun()

# ──────────────────────────────────────────────────────────────
# ▌ TAB 5：大盤預警
# ──────────────────────────────────────────────────────────────
with tab5:
    st.markdown("<div class='sec-title'>🌡️ 市場溫度計 · 大盤預警</div>",
                unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════
    # ▌ 市場溫度計：六指標快速判讀（Phase 5 新增）
    # 回答一個問題：「現在適合出手嗎？」
    # ══════════════════════════════════════════════════════════════
    try:
        # 讀取六個核心指標
        _t5_risk_status, _t5_risk = get_system_risk_status()
        _t5_tx_net   = _t5_risk.get("tx_net", 0)
        _t5_retail   = _t5_risk.get("mtx_retail", 0)
        _t5_vix      = get_vix()
        _t5_mg       = get_total_margin_balance()
        _t5_mg_bal   = _t5_mg["balance"] if _t5_mg else 0
        _t5_twii     = get_index_bias20("^TWII")
        _t5_sox      = get_index_bias20("^SOX")

        # 各指標燈號
        _t5_tx_s  = "🟢" if _t5_tx_net <= -25000 else "🔴" if _t5_tx_net > -10000 else "🟡"
        _t5_rt_s  = "🟢" if _t5_retail < 0 else "🔴" if _t5_retail > 12000 else "🟡"
        _t5_vx_s  = "🟢" if (_t5_vix or 99) < 20 else "🔴" if (_t5_vix or 0) > 30 else "🟡"
        _t5_mg_s  = "🟢" if _t5_mg_bal < 4500 else "🔴" if _t5_mg_bal >= 5000 else "🟡"
        _t5_tw_s  = ("🔴" if _t5_twii and _t5_twii["bias_20"] >= 8
                     else "🟢" if _t5_twii and _t5_twii["bias_20"] <= -8 else "🟡")
        _t5_sx_s  = ("🔴" if _t5_sox and _t5_sox["bias_20"] >= 8
                     else "🟢" if _t5_sox and _t5_sox["bias_20"] <= -8 else "🟡")

        # 燈號計分 → 總體判斷
        _t5_tw_val = f"{_t5_twii['bias_20']:+.1f}%" if _t5_twii else '—'
        _t5_sx_val = f"{_t5_sox['bias_20']:+.1f}%" if _t5_sox else '—'
        _t5_all = [_t5_tx_s, _t5_rt_s, _t5_vx_s, _t5_mg_s, _t5_tw_s, _t5_sx_s]
        _t5_red  = _t5_all.count("🔴")
        _t5_grn  = _t5_all.count("🟢")

        if _t5_red >= 3:
            _t5_overall = "🔴 縮手防守"
            _t5_oc      = "#ff4444"
            _t5_advice  = "多項指標亮紅燈，環境不利，停止新增部位，保留現金。"
        elif _t5_grn >= 4:
            _t5_overall = "🟢 適合布局"
            _t5_oc      = "#00cc66"
            _t5_advice  = "環境健康，可執行王者候選名單中的布局計畫。"
        else:
            _t5_overall = "🟡 謹慎觀望"
            _t5_oc      = "#fbbf24"
            _t5_advice  = "訊號混沌，等待更清晰的方向，輕倉為主。"

        # 渲染溫度計卡片
        st.markdown(
            f"<div style='border:2px solid {_t5_oc};border-radius:10px;"
            f"padding:14px 18px;margin:12px 0;background:rgba(255,255,255,0.02);'>"
            f"<div style='font-size:1.2rem;font-weight:700;color:{_t5_oc};"
            f"margin-bottom:10px;'>{_t5_overall}</div>"
            f"<div style='display:flex;gap:20px;flex-wrap:wrap;font-size:.88rem;"
            f"margin-bottom:10px;'>"
            f"<span>{_t5_tx_s} 外資大台 {_t5_tx_net:+,}口</span>"
            f"<span>{_t5_rt_s} 小台散戶 {_t5_retail:+,}口</span>"
            f"<span>{_t5_vx_s} VIX {f'{_t5_vix:.1f}' if _t5_vix else '—'}</span>"
            f"<span>{_t5_mg_s} 融資水位 {f'{_t5_mg_bal:,.0f}億' if _t5_mg_bal else '—'}</span>"
            f"<span>{_t5_tw_s} 台股月乖離 {_t5_tw_val}</span>"
            f"<span>{_t5_sx_s} 費半月乖離 {_t5_sx_val}</span>"
            f"</div>"
            f"<div style='color:#9fb8d4;font-size:.83rem;'>{_t5_advice}</div>"
            f"</div>",
            unsafe_allow_html=True
        )
    except Exception:
        st.caption("市場溫度計載入中，請稍候...")

    st.markdown("---")
    st.markdown(
        "<div style='color:#7fb3d3;font-size:.8rem;margin-bottom:8px;'>"
        "⬇️ 以下為完整大盤預警詳細指標（需要深入研究時使用）</div>",
        unsafe_allow_html=True
    )

    # ── 每次載入都清除四個板塊的 session_state key，
    #    確保 multiselect 的 default 永遠來自 watch_list.json 最新值，
    #    而不是被舊的 session 值覆蓋（這是預設值重疊 bug 的根本原因）
    for _wl_sk in ["wl_ai_semi", "wl_ai_infra", "wl_next_gen", "wl_shipping_fin"]:
        if _wl_sk in st.session_state:
            del st.session_state[_wl_sk]

    with st.expander("👑 全域類股龍頭監控天網（動態微調分列矩陣）", expanded=True):

        # 讀取現有設定（本地JSON → GitHub備援 → 預設值）
        _wl_data = load_watch_list()

        # 建立 id→name 輔助顯示（stock_list.csv，找不到就用代號本身）
        _wl_id2name = {}
        try:
            _sl_df, _sl_ok = load_csv("stock_list.csv")
            if _sl_ok and not _sl_df.empty and "stock_id" in _sl_df.columns:
                _sl_df["stock_id"] = _sl_df["stock_id"].astype(str).str.strip()
                for _, _slr in _sl_df.drop_duplicates("stock_id").iterrows():
                    _wl_id2name[_slr["stock_id"]] = _slr.get("stock_name", _slr["stock_id"])
        except Exception:
            pass

        def _wl_label(sid: str) -> str:
            """顯示格式：代號＋名稱（如 2330 台積電）"""
            name = _wl_id2name.get(str(sid), "")
            return f"{sid} {name}".strip() if name and name != sid else sid

        def _wl_parse(labels: list) -> list:
            """把 multiselect 回傳的 label 列表，只取出代號部分（空白前）"""
            return [str(lb).split(" ")[0].strip() for lb in labels if lb]

        # ══════════════════════════════════════════════════════════
        # 核心修正：每個板塊各自獨立定義 options 和 default，完全解耦
        # options = 本板塊現有標的（可額外輸入任意新代號）
        # default = 從 watch_list.json 讀取本板塊的當前名單
        # 兩者都只包含本板塊的標的，不共用、不污染其他板塊
        # ══════════════════════════════════════════════════════════

        # 行列一：🟢 AI 與半導體核心
        _ids_ai_semi   = _wl_data.get("ai_semi",      WATCH_LIST_DEFAULTS["ai_semi"])
        _opts_ai_semi  = [_wl_label(s) for s in _ids_ai_semi]
        _sel_ai_semi   = st.multiselect(
            "🟢 AI 與半導體核心",
            options=_opts_ai_semi,
            default=_opts_ai_semi,
            key="wl_ai_semi",
            accept_new_options=True,
            help="輸入任意台股代號新增（如 3034）；點下方鎖定才寫入",
        )

        # 行列二：🔵 AI 剛需基礎建設
        _ids_ai_infra  = _wl_data.get("ai_infra",     WATCH_LIST_DEFAULTS["ai_infra"])
        _opts_ai_infra = [_wl_label(s) for s in _ids_ai_infra]
        _sel_ai_infra  = st.multiselect(
            "🔵 AI 剛需基礎建設",
            options=_opts_ai_infra,
            default=_opts_ai_infra,
            key="wl_ai_infra",
            accept_new_options=True,
            help="輸入任意台股代號新增（如 6669）；點下方鎖定才寫入",
        )

        # 行列三：🟡 次世代戰略兵器
        _ids_next_gen  = _wl_data.get("next_gen",     WATCH_LIST_DEFAULTS["next_gen"])
        _opts_next_gen = [_wl_label(s) for s in _ids_next_gen]
        _sel_next_gen  = st.multiselect(
            "🟡 次世代戰略兵器",
            options=_opts_next_gen,
            default=_opts_next_gen,
            key="wl_next_gen",
            accept_new_options=True,
            help="輸入任意台股代號新增（如 1519）；點下方鎖定才寫入",
        )

        # 行列四：🔴 航運傳產與大型金融
        _ids_ship_fin  = _wl_data.get("shipping_fin", WATCH_LIST_DEFAULTS["shipping_fin"])
        _opts_ship_fin = [_wl_label(s) for s in _ids_ship_fin]
        _sel_ship_fin  = st.multiselect(
            "🔴 航運傳產與大型金融",
            options=_opts_ship_fin,
            default=_opts_ship_fin,
            key="wl_shipping_fin",
            accept_new_options=True,
            help="輸入任意台股代號新增（如 2884）；點下方鎖定才寫入",
        )

        # 整合四板塊最新選取結果
        _wl_new_sectors = {
            "ai_semi":      _wl_parse(_sel_ai_semi),
            "ai_infra":     _wl_parse(_sel_ai_infra),
            "next_gen":     _wl_parse(_sel_next_gen),
            "shipping_fin": _wl_parse(_sel_ship_fin),
        }

        # 統計檔數
        _wl_grand_total = sum(len(v) for v in _wl_new_sectors.values())
        _wl_over_limit  = any(len(v) > WATCH_LIST_MAX_PER_ROW for v in _wl_new_sectors.values())
        st.caption(
            f"四大板塊共計 **{_wl_grand_total}** 檔　"
            f"（AI半導體{len(_wl_new_sectors['ai_semi'])} ＋ "
            f"AI建設{len(_wl_new_sectors['ai_infra'])} ＋ "
            f"次世代{len(_wl_new_sectors['next_gen'])} ＋ "
            f"傳產金融{len(_wl_new_sectors['shipping_fin'])}）　"
            f"每行上限 {WATCH_LIST_MAX_PER_ROW} 檔"
        )

        # ── 龍頭多空定錨（分板塊顯示，不用無意義的合計數字）
        _sb = get_sector_breadth()
        if _sb and _sb.get("by_sector"):
            _by = _sb["by_sector"]
            _sb_icons = {"ai_semi":"🟢 AI半導體","ai_infra":"🔵 AI建設","next_gen":"🟡 次世代","shipping_fin":"🔴 傳產金融"}
            _parts = []
            for _k, _icon in _sb_icons.items():
                _s = _by.get(_k, {})
                if _s.get("total"):
                    _a20, _tot = _s.get("above_sma20",0), _s["total"]
                    _color = "🔴" if _a20/_tot < 0.35 else "🟡" if _a20/_tot < 0.6 else "🟢"
                    _parts.append(f"{_icon} {_a20}/{_tot}{_color}")
            st.markdown(
                f"<div style='color:#c8dff0;font-size:.82rem;padding:4px 0 8px;'>"
                f"📊 類股龍頭站月線(SMA20)：{'　'.join(_parts)}"
                f"　｜　{_sb.get('date','—')}</div>",
                unsafe_allow_html=True
            )
        elif _sb and _sb.get("total"):
            _sb_above = _sb.get("above_sma20", 0)
            _sb_total = _sb["total"]
            st.markdown(
                f"<div style='color:#c8dff0;font-size:.82rem;padding:4px 0 8px;'>"
                f"📊 類股龍頭多空定錨：{_sb_above}/{_sb_total} 站月線　｜　{_sb.get('date','—')}</div>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                "<div style='color:#7fb3d3;font-size:.78rem;padding:4px 0 8px;'>"
                "📊 類股龍頭多空定錨：等待每日 17:00 盤後排程計算（daily_scan.py）</div>",
                unsafe_allow_html=True
            )

        # 💾 置頂鋼鐵藍色鎖定按鈕（正中央置放）
        _wl_col_l, _wl_col_c, _wl_col_r = st.columns([1, 2, 1])
        with _wl_col_c:
            if st.button(
                "💾 鎖定新風向（實時覆寫大類股監控名單）",
                use_container_width=True,
                disabled=_wl_over_limit,
                key="wl_save_btn",
                type="primary",
            ):
                _ok_save = save_watch_list_to_github(_wl_new_sectors)
                if _ok_save:
                    st.success(
                        "👑 報告指揮官：17檔大類股防線已物理死鎖，全域雷達已同步移防！"
                    )
                else:
                    st.warning(
                        "⚠️ 本地已寫入，但 GitHub 推送失敗。"
                        "請確認 GH_TOKEN 已設定（st.secrets[\"GH_TOKEN\"]），或稍後重試。"
                    )
            if _wl_over_limit:
                st.error(f"⛔ 某行超過 {WATCH_LIST_MAX_PER_ROW} 檔上限，請先移除多餘標的！")
    # ════════════════════════════════════════════════════════

    # ── V6 三軌風控儀表板
    _risk_status, _risk_info = get_system_risk_status()
    _vix        = get_vix()
    _macro_ind  = get_macro_indicators()

    # ══════════════════════════════════════════════════════════
    # 第一行：全球籌碼與核彈排毒雷達（5 欄）
    # ══════════════════════════════════════════════════════════
    def _metric_html(label, value, status, hint, ref=""):
        """用 HTML 自訂 metric，確保字體大小舒適且顏色醒目；ref為標準參考值，灰色小字顯示"""
        color = {"🔴":"#ff4444","🟡":"#fbbf24","🟢":"#00cc66","⚪":"#8892b0"}.get(status[0], "#8892b0")
        _ref_line = f"<div style='color:#9fb8d4;font-size:.78rem;margin-top:4px;'>標準：{ref}</div>" if ref else ""
        return (
            f"<div style='background:rgba(255,255,255,0.03);border:1px solid #1e3a5f;"
            f"border-radius:8px;padding:10px 8px;text-align:center;border-top:3px solid {color};'>"
            f"<div style='color:#7fb3d3;font-size:.72rem;letter-spacing:.5px;margin-bottom:4px;'>{label}</div>"
            f"<div style='color:#e8f4fd;font-size:1.25rem;font-weight:700;line-height:1.2;'>{value}</div>"
            f"<div style='color:{color};font-size:.7rem;margin-top:4px;'>{status} {hint}</div>"
            f"{_ref_line}"
            f"</div>"
        )

    _r1 = st.columns(6)

    # ── 欄1：大台外資（外資台指期淨未平倉 ＋ 結轉/轉倉追蹤）
    _tx = _risk_info["tx_net"]
    _rollover     = get_tx_rollover_info()
    _daily_chg    = _rollover.get("daily_change", 0)
    _cum_rollover = _rollover.get("cum_rollover", 0)

    # 🚨 剛性風控：外資空單 ≥45,000口 或 近5日結轉空單累計突破 -70,000口
    #    → 無條件觸發【毀滅警戒：空單死鎖/全速清空現貨】極端紅燈
    if _tx <= -45000 or _cum_rollover <= -70000:
        _tx_s, _tx_h = "🔴", "毀滅警戒:空單死鎖/全速清空現貨"
    elif _tx >= -25000:
        _tx_s, _tx_h = "🟢", "波段安全"
    else:
        # 回補至 -45,000 口以下（即未達紅燈門檻）維持黃燈觀察
        _tx_s, _tx_h = "🟡", "回補觀察中"

    _tx_delta_line = f"轉倉:{_daily_chg:+,}口／累計:{_cum_rollover:+,}口"
    _r1[0].markdown(
        _metric_html("大台外資", f"{_tx:+,}口", _tx_s, f"{_tx_h}｜{_tx_delta_line}",
                     ref=f"全合約加總（含遠月）；-25,000~-45,000口安全區；≦-45,000口為毀滅警戒"),
        unsafe_allow_html=True
    )

    # ── 欄2：小台散戶多空比 %（含逐日增減自適應判定，100% 動態，拒絕寫死）
    _retail_dd  = get_mtx_retail_position_with_delta()
    _retail     = _retail_dd["today"] if _retail_dd["today"] is not None else _risk_info["mtx_retail"]
    _delta_r    = _retail_dd["delta"]  # None 或 今日-昨日增減口數

    _mtx_total  = abs(_retail) + 10000  # 近似全市場
    _retail_pct = round(_retail / _mtx_total * 100, 1) if _mtx_total else 0

    # ── 基礎燈號：依多空比%絕對水位判定（沿用原有門檻）
    if _retail_pct >= 15:
        _rt_s, _rt_base_h = "🔴", "散戶抄底踩踏"
    elif _retail_pct <= -20:
        _rt_s, _rt_base_h = "🟢", "籌碼乾淨"
    else:
        _rt_s, _rt_base_h = "⚪", "中性"

    # ── 逐日增減自適應描述：在高位散戶多單的前提下，
    #    區分「認賠減倉」（風險降溫）vs「逆勢爆增」（接刀加劇）
    if _retail > 0 and _delta_r is not None:
        if _delta_r < 0:
            _rt_h = f"{_rt_base_h}｜散戶多單高位減少（認賠減倉，風險降溫）"
        else:
            _rt_h = f"{_rt_base_h}｜散戶多單逆勢爆增（高位接刀，風險加劇）"
    elif _delta_r is not None:
        _rt_h = f"{_rt_base_h}｜日增減 {_delta_r:+,}口"
    else:
        _rt_h = _rt_base_h

    _r1[1].markdown(_metric_html("小台散戶", f"{_retail:+,}口", _rt_s, _rt_h,
                     ref="多空比 -20%~+15% 為中性區間；高位減倉=降溫，高位爆增=接刀加劇"), unsafe_allow_html=True)

    # ── 欄3：大盤月乖離（從第二行移到第一行，更核心）
    _bias_r1 = _macro_ind.get("bias")
    if _bias_r1 is not None:
        if _bias_r1 > 4:      _bias_r1_s, _bias_r1_h = "🔴", "極端超漲"
        elif _bias_r1 < -4:   _bias_r1_s, _bias_r1_h = "🟢", "黃金打底區"
        else:                  _bias_r1_s, _bias_r1_h = "⚪", "正常範圍"
        _r1[3].markdown(_metric_html("大盤月乖離", f"{_bias_r1:+.1f}%", _bias_r1_s, _bias_r1_h,
                         ref="-4%~+4% 正常；>+4% 超漲；<-4% 打底區"), unsafe_allow_html=True)
    else:
        _r1[3].markdown(_metric_html("大盤月乖離", "—", "⚪", "計算中",
                         ref="-4%~+4% 正常；>+4% 超漲；<-4% 打底區"), unsafe_allow_html=True)

    # CBOE P/C 保留在第二行（_r2）顯示
    _pc = _risk_info["pc_ratio"]
    if _pc < 0.8:    _pc_s, _pc_h = "🔴", "極度貪婪"
    elif _pc > 1.2:  _pc_s, _pc_h = "🟢", "恐慌買點"
    else:            _pc_s, _pc_h = "⚪", "正常"

    # ── 欄4：VIX（渲染移到第二行區塊，避免_r2未定義錯誤）
    if _vix is not None:
        if _vix > 25:    _vix_s, _vix_h = "🔴", "市場去槓桿"
        elif _vix < 15:  _vix_s, _vix_h = "🟢", "風平浪靜"
        else:            _vix_s, _vix_h = "⚪", "警戒中"
    else:
        _vix_s, _vix_h = "⚪", "載入中"

    # ── 欄5：全市場融資水位（讀取 margin_summary.json，來源：證交所彙總）
    _mg_data = get_total_margin_balance()
    _margin_balance = _mg_data["balance"] if _mg_data else 0.0
    _mg_date = _mg_data["date"] if _mg_data else "—"

    if _margin_balance <= 0:
        _mg_s, _mg_h = "⚪", "資料更新中"
    elif _margin_balance < 4500:
        _mg_s, _mg_h = "🟢", "安全水位"
    elif _margin_balance < 5000:
        _mg_s, _mg_h = "🟡", "過熱警戒"
    else:
        _mg_s, _mg_h = "🔴", "毀滅級超載"

    _r1[2].markdown(
        _metric_html(
            f"{_mg_s} 全市場融資水位",
            f"{_margin_balance:,.0f} 億" if _margin_balance > 0 else "—",
            _mg_s, _mg_h,
            ref=f"資料日期：{_mg_date}｜來源：證交所彙總；≥4500億警戒；≥5000億超載"
        ),
        unsafe_allow_html=True
    )

    # ── 欄6：全場均線排列結構（全市場站上季線SMA60比例 × 大盤距高點背離偵測）
    _breadth   = get_market_breadth_sma60()
    _high_prox = get_twii_high_proximity()
    if _breadth is not None:
        # 🚨 結構極致背離：站上季線比例 <35% 但大盤仍在歷史高檔5%以內
        #    → 個股集體下陷，主力可能正在大盤指數上拉假象出貨
        if _breadth < 35 and _high_prox is not None and _high_prox <= 5:
            _bd_s, _bd_h = "🔴", f"結構極致背離:個股集體下陷・嚴禁滿倉（距高{_high_prox:.1f}%）"
        elif _breadth < 35:
            _bd_s, _bd_h = "🟡", "站上季線家數偏低"
        elif _breadth >= 60:
            _bd_s, _bd_h = "🟢", "多頭排列健康"
        else:
            _bd_s, _bd_h = "⚪", "結構中性"
        _r1[4].markdown(_metric_html("全場均線結構", f"{_breadth:.1f}%站季線", _bd_s, _bd_h,
                         ref="35%~60% 中性；≥60% 健康；<35%且大盤距高<5% 為背離警戒"), unsafe_allow_html=True)
    else:
        _r1[4].markdown(_metric_html("全場均線結構", "—", "⚪", "計算中",
                         ref="35%~60% 中性；≥60% 健康；<35%且大盤距高<5% 為背離警戒"), unsafe_allow_html=True)

    st.markdown("<div style='margin:6px 0;'></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # 第二行：總經核彈與大盤邊界防線（5 欄）
    # ══════════════════════════════════════════════════════════
    _r2 = st.columns(6)

    # ── 欄1：通膨雙指標（CPI / PPI 年增率）
    _cpi = _macro_ind.get("cpi")
    _ppi = get_us_ppi()
    _cpi_month = _macro_ind.get("cpi_month", "")
    _cpi_label = f"CPI/PPI {_cpi_month[2:]}" if _cpi_month else "CPI / PPI 年增率"

    _cpi_str = f"{_cpi:.1f}%" if _cpi is not None else "—"
    _ppi_str = f"{_ppi:.1f}%" if _ppi is not None else "—"
    _infl_val = f"{_cpi_str} / {_ppi_str}"

    # 🚨 PPI ≥5.5% 為最高優先風控：通膨復燃壓制估值（PPI領先反映成本端壓力）
    if _ppi is not None and _ppi >= 5.5:
        _cpi_s, _cpi_h = "🔴", f"通膨復燃:壓制估值（PPI {_ppi:.1f}%）"
    elif _cpi is not None and _cpi > 3.5:
        _cpi_s, _cpi_h = "🔴", "通膨復燃"
    elif _cpi is not None and _cpi <= 3.0:
        _cpi_s, _cpi_h = "🟢", "穩定降溫"
    else:
        _cpi_s, _cpi_h = "⚪", "觀察中"
    _r2[0].markdown(_metric_html(_cpi_label, _infl_val, _cpi_s, _cpi_h,
                     ref="CPI≤3.0% 降溫／>3.5% 復燃；PPI≥5.5% 死鎖紅燈"), unsafe_allow_html=True)

    # ── 欄2：油價（布倫特 / 杜拜）
    _br = _macro_ind.get("brent")
    _du = _macro_ind.get("dubai")
    _oil_val = f"{_br:.1f} / {_du:.1f}" if (_br and _du) else ("—")
    _oil_warn = (_br and _br > 88) or (_du and _du > 85)
    _oil_ok   = (_br and 70 <= _br <= 80) and (_du and 70 <= _du <= 80)
    if _oil_warn:    _oil_s, _oil_h = "🔴", "通膨前導警戒"
    elif _oil_ok:    _oil_s, _oil_h = "🟢", "區間穩定"
    else:            _oil_s, _oil_h = "⚪", "觀察中"
    _r2[1].markdown(_metric_html("油價 布/杜", _oil_val, _oil_s, _oil_h,
                     ref="70~80美元 穩定區；布>88或杜>85 通膨警戒"), unsafe_allow_html=True)

    # ── 欄3：美債 10 年期殖利率
    _tnx = _macro_ind.get("tnx")
    if _tnx is not None:
        if _tnx > 4.4:   _tnx_s, _tnx_h = "🔴", "估值壓制"
        elif _tnx < 4.0: _tnx_s, _tnx_h = "🟢", "資金行情解封"
        else:             _tnx_s, _tnx_h = "⚪", "觀察中"
        _r2[2].markdown(_metric_html("美債10年", f"{_tnx:.2f}%", _tnx_s, _tnx_h,
                         ref="<4.0% 資金解封；4.0%~4.4% 觀察；>4.4% 估值壓制"), unsafe_allow_html=True)
    else:
        _r2[2].markdown(_metric_html("美債10年", "—", "⚪", "載入中",
                         ref="<4.0% 資金解封；4.0%~4.4% 觀察；>4.4% 估值壓制"), unsafe_allow_html=True)

    # ── 欄4：CBOE P/C（從第一行移入）
    _r2[3].markdown(_metric_html("CBOE P/C", f"{_pc:.2f}", _pc_s, _pc_h,
                     ref="0.8~1.2 常態；<0.8 過熱／>1.2 恐慌"), unsafe_allow_html=True)

    # ── 欄5：航運指數（BDI 波羅的海乾散裝指數，以 BDRY ETF×100 近似）
    #    說明：SCFI（上海貨櫃運價指數）無公開免費即時來源，故不顯示，
    #    避免「— / 數字」造成誤導；僅以 BDI 作為航運景氣代理指標。
    _bdi  = _macro_ind.get("bdi")
    _ship_val = f"{_bdi:,}" if _bdi else "—"
    if _bdi and _bdi > 2000:   _ship_s, _ship_h = "🔴", "通膨隱憂"
    elif _bdi and _bdi < 1000: _ship_s, _ship_h = "🟢", "資金歸建電子"
    else:                       _ship_s, _ship_h = "⚪", "盤整中"
    _r2[4].markdown(_metric_html("航運 BDI", _ship_val, _ship_s, _ship_h,
                     ref="<1000 資金歸建電子；1000~2000 盤整；>2000 通膨隱憂"), unsafe_allow_html=True)

    # ── 欄6：VIX 恐慌指數（從第一行移入，與總經指標同排）
    _r2[5].markdown(
        _metric_html("VIX 恐慌",
                     f"{_vix:.1f}" if _vix is not None else "—",
                     _vix_s, _vix_h,
                     ref="<15 平靜；15~25 觀察；>25 去槓桿"),
        unsafe_allow_html=True
    )

    # ── 欄6（原）：利多不漲排毒（後端 daily_scan.py 每日17:00盤後掃描，前端僅讀結果）
    # 此卡片【只顯示】是否有個股符合「炒作熱度高＋收黑/長上影線＋外資大倒貨」
    # 龍頭多空定錨已移至頂端天網區塊獨立顯示，兩者完全解耦
    _alerts_today = get_triggered_alerts_today()

    if _alerts_today:
        _names = "、".join(a.get("name", a.get("stock_id","?")) for a in _alerts_today[:3])
        _more  = f" 等{len(_alerts_today)}檔" if len(_alerts_today) > 3 else ""
        _trap_s, _trap_h = "🔴", f"🚨 {len(_alerts_today)}檔出貨：{_names}{_more}"
        _trap_val = f"{len(_alerts_today)}檔觸發"
    else:
        _trap_s, _trap_h = "🟢", "全域安全"
        _trap_val = "戰備軍無毒"

    _r1[5].markdown(
        _metric_html("利多不漲排毒", _trap_val, _trap_s, _trap_h,
                     ref="每日17:00盤後自動掃描龍頭清單；0檔=安全，>0檔=炒作出貨陷阱"),
        unsafe_allow_html=True
    )

    # ══════════════════════════════════════════════════════════
    # 第三行：美股動態流動性與信用天網（6 欄）
    # 100% 直接抓取 yfinance / FRED，零基本檔維護成本
    # ══════════════════════════════════════════════════════════
    st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)
    _r3 = st.columns(6)

    # ── 欄1：Fed 淨流動性（兆美元）
    _net_liq = get_fed_net_liquidity()
    if _net_liq is not None:
        _nl_s = "🟢" if _net_liq >= 5.5 else "🟡" if _net_liq >= 5.0 else "🔴"
        _nl_h = "資金面寬鬆" if _net_liq >= 5.5 else "資金面中性" if _net_liq >= 5.0 else "資金面緊縮"
        _r3[0].markdown(
            _metric_html("Fed淨流動性", f"${_net_liq:.2f}兆", _nl_s, _nl_h,
                         ref="WALCL−TGA−RRP；數值越高市場資金越寬鬆"),
            unsafe_allow_html=True
        )
    else:
        _r3[0].markdown(_metric_html("Fed淨流動性", "—", "⚪", "載入中", ref="FRED API"),
                         unsafe_allow_html=True)

    # ── 欄2：泰德利差（TED Spread）
    _ted = get_ted_spread()
    if _ted is not None:
        _ted_s = "🟢" if _ted < 0.3 else "🟡" if _ted < 0.5 else "🔴"
        _ted_h = "銀行信用穩定" if _ted < 0.3 else "信用壓力升溫" if _ted < 0.5 else "信用緊縮警戒"
        _r3[1].markdown(
            _metric_html("泰德利差", f"{_ted:.2f}%", _ted_s, _ted_h,
                         ref="銀行間信用風險指標；<0.3%穩定，≥0.5%緊縮警戒"),
            unsafe_allow_html=True
        )
    else:
        _r3[1].markdown(_metric_html("泰德利差", "—", "⚪", "載入中", ref="FRED: TEDRATE"),
                         unsafe_allow_html=True)

    # ── 欄3：那斯達克100 月乖離（^NDX）
    _ndx = get_index_bias20("^NDX")
    if _ndx is not None:
        _ndx_bias = _ndx["bias_20"]
        if _ndx_bias >= 10.0:
            _ndx_s, _ndx_h = "🔴", "高位階背離泡沫"
        elif _ndx_bias <= -10.0:
            _ndx_s, _ndx_h = "🟢", "絕對底部超賣"
        else:
            _ndx_s, _ndx_h = "🟡", "正常區間"
        _r3[2].markdown(
            _metric_html("那指100月乖離", f"{_ndx_bias:+.1f}%", _ndx_s,
                         f"{_ndx_h}｜現價{_ndx['price']:,.0f}",
                         ref="(現價−20MA)/20MA；≥+10%泡沫警戒，≤-10%超賣布局"),
            unsafe_allow_html=True
        )
    else:
        _r3[2].markdown(_metric_html("那指100月乖離", "—", "⚪", "載入中", ref="yf: ^NDX"),
                         unsafe_allow_html=True)

    # ── 欄4：費城半導體 月乖離（^SOX，與台股高階晶片高度正相關）
    _sox = get_index_bias20("^SOX")
    if _sox is not None:
        _sox_bias = _sox["bias_20"]
        if _sox_bias >= 10.0:
            _sox_s, _sox_h = "🔴", "高位階背離泡沫"
        elif _sox_bias <= -10.0:
            _sox_s, _sox_h = "🟢", "絕對底部超賣"
        else:
            _sox_s, _sox_h = "🟡", "正常區間"
        _r3[3].markdown(
            _metric_html("費半月乖離", f"{_sox_bias:+.1f}%", _sox_s,
                         f"{_sox_h}｜現價{_sox['price']:,.0f}",
                         ref="與台股高階晶片高度正相關；≥+10%泡沫，≤-10%超賣"),
            unsafe_allow_html=True
        )
    else:
        _r3[3].markdown(_metric_html("費半月乖離", "—", "⚪", "載入中", ref="yf: ^SOX"),
                         unsafe_allow_html=True)

    # ── 欄5：高收益債信用利差
    _hy_spread = get_high_yield_spread()
    if _hy_spread is not None:
        _hy_s = "🟢" if _hy_spread < 4.0 else "🟡" if _hy_spread < 6.0 else "🔴"
        _hy_h = "信用市場健康" if _hy_spread < 4.0 else "信用利差擴大" if _hy_spread < 6.0 else "信用危機警戒"
        _r3[4].markdown(
            _metric_html("高收益債利差", f"{_hy_spread:.2f}%", _hy_s, _hy_h,
                         ref="反映企業違約風險溢酬；<4%健康，≥6%危機警戒"),
            unsafe_allow_html=True
        )
    else:
        _r3[4].markdown(_metric_html("高收益債利差", "—", "⚪", "載入中", ref="FRED: BAMLH0A0HYM2"),
                         unsafe_allow_html=True)

    # ── 欄6：美元指數（DXY）
    _dxy = get_dxy_index()
    if _dxy is not None:
        _dxy_s = "🟢" if _dxy < 100 else "🟡" if _dxy < 105 else "🔴"
        _dxy_h = "美元偏弱有利新興市場" if _dxy < 100 else "美元中性" if _dxy < 105 else "美元強勢資金回流"
        _r3[5].markdown(
            _metric_html("美元指數DXY", f"{_dxy:.1f}", _dxy_s, _dxy_h,
                         ref="美元相對一籃子貨幣強弱；<100偏弱，≥105強勢"),
            unsafe_allow_html=True
        )
    else:
        _r3[5].markdown(_metric_html("美元指數DXY", "—", "⚪", "載入中", ref="yf: DX-Y.NYB"),
                         unsafe_allow_html=True)

    st.markdown("<div style='margin:4px 0;'></div>", unsafe_allow_html=True)

    if _risk_status == "RED_ALERT":
        st.error(f"🔴 **【全球熔斷最高警戒】** 台美散戶同步過熱"
                 f"{'＋總經核彈倒數'+str(_risk_info['days'])+'天！' if _risk_info['is_event'] else '！'}"
                 f" 大台空單 {abs(_risk_info['tx_net']):,}口 × 小台散戶 {_risk_info['mtx_retail']:,}口"
                 f"，**全面禁買，嚴防土石流！**")
    elif _risk_status == "YELLOW_ALERT":
        st.warning(f"🟡 **【常規警戒升溫】** 大台空單 {abs(_risk_info['tx_net']):,}口"
                   f" × 小台散戶多單 {_risk_info['mtx_retail']:,}口，**建倉資金嚴格減半！**")
    elif _risk_status == "SHORT_SQUEEZE":
        st.info(f"🔥 **【黃金軋空特赦】** 小台散戶放空 {abs(_risk_info['mtx_retail']):,}口"
                f" × CBOE P/C {_risk_info['pc_ratio']:.2f}（恐慌頂點），**短線火箭全面放行！**")
    else:
        st.success(f"🟢 **【全球環境安全】** 台美籌碼結構正常，依個股技術面執行 SOP。")

    st.divider()

    # ── 智慧刷新：比對期貨資料日期是否為今日
    _today_tw = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    _fut_df, _fut_ok = get_futures()
    _fut_date = "—"
    if _fut_ok and not _fut_df.empty and "date" in _fut_df.columns:
        _fut_date = str(pd.to_datetime(_fut_df["date"], errors="coerce").max())[:10]

    _fut_stale = _fut_date != _today_tw
    _refresh_col, _status_col = st.columns([2, 5])
    with _refresh_col:
        if st.button("🔄 刷新期貨籌碼", key="refresh_futures",
                     type="primary" if _fut_stale else "secondary",
                     use_container_width=True):
            # 清除 CSV 快取，強制重新載入
            load_csv.clear()
            st.toast("✅ 期貨籌碼已刷新", icon="✅")
            st.rerun()
    with _status_col:
        if _fut_stale:
            st.markdown(
                f"<span style='color:#ff9800;font-size:.85rem;'>⚠️ 期貨資料停在 {_fut_date}，今日（{_today_tw}）尚未更新</span>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f"<span style='color:#00e676;font-size:.85rem;'>✅ 期貨資料已是今日（{_today_tw}）最新</span>",
                unsafe_allow_html=True
            )

    # ══════════════════════════════════════════════════════════════
    # ▌ 盤後三大健康指標全自動診斷面板（100% 動態運算，零手動輸入）
    # ══════════════════════════════════════════════════════════════
    st.markdown("<div class='sec-title'>🩺 盤後三大健康指標診斷（全自動）</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>系統自動讀取本地資料源即時研判大盤真實健康狀態，"
        "零手動輸入、零主觀判斷。即使技術警示分數達 8/8，此診斷可識別「假警報」與「真危機」。</div>",
        unsafe_allow_html=True
    )

    @st.cache_data(ttl=1800, show_spinner=False)
    def _auto_diagnose_market_health(sample_size: int = 120):
        """
        全自動計算三大健康指標，拒絕任何手動輸入或假數據。
        快取30分鐘，避免每次刷新都重新批次運算。

        取樣策略：stock_list.csv 沒有產業別欄位，改用「股號區間分散取樣」
        模擬跨類股覆蓋（台股股號大致依掛牌時間/產業群聚，例如 1xxx傳產／
        2xxx電子金融／3xxx電子零組件／4xxx生技／5~9xxx其他），
        避免取樣集中在單一產業造成偏誤。

        回傳：(adl_status, adl_detail, foreign_status, foreign_detail,
               pillars_status, pillars_detail)
        """
        import gc as _gc_diag

        def _get_dispersed_sample(all_ids: list, n: int) -> list:
            """
            按股號首碼分組（1~9），每組依比例平均抽樣，
            確保取樣分散在不同股號區間（近似跨產業分散）。
            """
            if len(all_ids) <= n:
                return all_ids
            groups = {}
            for sid in all_ids:
                key = sid[0]  # 股號首碼 1~9
                groups.setdefault(key, []).append(sid)
            # 依首碼排序，輪流從每組抽取，確保分散
            sorted_keys = sorted(groups.keys())
            result = []
            idx = 0
            while len(result) < n and any(groups[k] for k in sorted_keys):
                k = sorted_keys[idx % len(sorted_keys)]
                if groups[k]:
                    result.append(groups[k].pop(0))
                idx += 1
            return result[:n]

        # ══════════════════════════════════════
        # ① ADL 騰落線動態計算
        # 邏輯：取樣股票池，比對最新收盤 vs 前一日收盤，
        #      統計上漲家數(up_shares) vs 下跌家數(down_shares)
        # ══════════════════════════════════════
        _up_shares, _down_shares, _flat_shares = 0, 0, 0
        _sample_total, _sample_failed = 0, 0
        try:
            df_sl, ok_sl = load_csv("stock_list.csv")
            if ok_sl and not df_sl.empty and "stock_id" in df_sl.columns:
                _all_ids = df_sl["stock_id"].dropna().astype(str).unique().tolist()
                _all_ids = [s for s in _all_ids if s.isdigit() and len(s) == 4]
                _ids = _get_dispersed_sample(_all_ids, sample_size)
                _sample_total = len(_ids)
                for _sid in _ids:
                    _df_p, _ok_p = load_csv(f"prices/{_sid}.csv")
                    if not _ok_p or _df_p.empty:
                        _sample_failed += 1
                        continue
                    _cc = next((c for c in _df_p.columns if c.lower() == "close"), None)
                    if not _cc:
                        _sample_failed += 1
                        continue
                    _closes = pd.to_numeric(_df_p[_cc], errors="coerce").dropna()
                    if len(_closes) < 2:
                        _sample_failed += 1
                        continue
                    _today_c = _closes.iloc[-1]
                    _yest_c  = _closes.iloc[-2]
                    if _today_c > _yest_c:
                        _up_shares += 1
                    elif _today_c < _yest_c:
                        _down_shares += 1
                    else:
                        _flat_shares += 1
                    del _df_p, _closes
        except Exception:
            pass

        if (_up_shares + _down_shares + _flat_shares) == 0:
            adl_status, adl_detail = "UNKNOWN", "資料不足，無法判定"
        elif _up_shares >= _down_shares:
            adl_status = "HEALTHY"
            adl_detail = (f"上漲 {_up_shares}／下跌 {_down_shares}／平盤 {_flat_shares} 家"
                          f"（健康輪動，取樣{_sample_total}檔，{_sample_failed}檔無資料）")
        else:
            adl_status = "BEARISH"
            adl_detail = (f"上漲 {_up_shares}／下跌 {_down_shares}／平盤 {_flat_shares} 家"
                          f"（多空背離，取樣{_sample_total}檔，{_sample_failed}檔無資料）")

        _gc_diag.collect()

        # ══════════════════════════════════════
        # ② 外資現貨共振動態計算
        # 邏輯：取樣股票池，加總「外資買賣超張數 × 現價」估算金額，
        #      賣超金額 ≥ 100億元 視為大舉砸盤
        # ══════════════════════════════════════
        _foreign_amount = 0.0  # 單位：元（正=買超，負=賣超）
        try:
            _chips_map = get_chips_facts_map()
            df_sl2, ok_sl2 = load_csv("stock_list.csv")
            if ok_sl2 and not df_sl2.empty and "stock_id" in df_sl2.columns:
                _all_ids2 = df_sl2["stock_id"].dropna().astype(str).unique().tolist()
                _all_ids2 = [s for s in _all_ids2 if s.isdigit() and len(s) == 4]
                _ids2 = _get_dispersed_sample(_all_ids2, sample_size)
                for _sid2 in _ids2:
                    _fgn_zhang = _chips_map.get(_sid2, {}).get("foreign_net")  # 張
                    if _fgn_zhang is None:
                        continue
                    _df_p2, _ok_p2 = load_csv(f"prices/{_sid2}.csv")
                    if not _ok_p2 or _df_p2.empty:
                        continue
                    _cc2 = next((c for c in _df_p2.columns if c.lower() == "close"), None)
                    if not _cc2:
                        continue
                    _cp2 = pd.to_numeric(_df_p2[_cc2], errors="coerce").dropna()
                    if _cp2.empty:
                        continue
                    _price = float(_cp2.iloc[-1])
                    # 張 × 1000股 × 股價 = 金額(元)
                    _foreign_amount += _fgn_zhang * 1000 * _price
                    del _df_p2, _cp2
        except Exception:
            pass

        _foreign_amount_yi = _foreign_amount / 1e8  # 轉換為「億元」
        if _foreign_amount_yi <= -100:
            foreign_status = "DUMP"
            foreign_detail = f"外資現貨估算大賣超 {abs(_foreign_amount_yi):.1f} 億元（砸盤）"
        else:
            foreign_status = "SAFE"
            foreign_detail = f"外資現貨估算 {_foreign_amount_yi:+.1f} 億元（買超或微幅賣超，安全）"

        _gc_diag.collect()

        # ══════════════════════════════════════
        # ③ 權值雙雄技術掃描（台積電 2330 ／ 聯發科 2454）
        # 邏輯：動態讀取現價與 20MA，至少一檔站上 20MA 視為多頭健康
        # ══════════════════════════════════════
        _pillar_results = {}
        for _pid, _pname in [("2330", "台積電"), ("2454", "聯發科")]:
            try:
                _df_pil, _ok_pil = load_price_csv(_pid)
                if not _ok_pil or _df_pil.empty or len(_df_pil) < 20:
                    _pillar_results[_pid] = None
                    continue
                _closes_pil = pd.to_numeric(_df_pil["Close"], errors="coerce").dropna()
                if len(_closes_pil) < 20:
                    _pillar_results[_pid] = None
                    continue
                _cp_pil  = float(_closes_pil.iloc[-1])
                _ma20_pil = float(_closes_pil.tail(20).mean())
                _pillar_results[_pid] = {
                    "name": _pname, "price": _cp_pil, "ma20": _ma20_pil,
                    "above": _cp_pil >= _ma20_pil
                }
                del _df_pil, _closes_pil
            except Exception:
                _pillar_results[_pid] = None

        _valid_pillars = [v for v in _pillar_results.values() if v is not None]
        _above_count = sum(1 for v in _valid_pillars if v["above"])

        if not _valid_pillars:
            pillars_status, pillars_detail = "UNKNOWN", "資料不足，無法判定"
        elif _above_count >= 1:
            pillars_status = "HEALTHY"
            _names = "、".join(f"{v['name']}{v['price']:.1f}(20MA:{v['ma20']:.1f})" for v in _valid_pillars)
            pillars_detail = f"至少一檔站上20MA（多頭健康）｜{_names}"
        else:
            pillars_status = "BREAK"
            _names = "、".join(f"{v['name']}{v['price']:.1f}(20MA:{v['ma20']:.1f})" for v in _valid_pillars)
            pillars_detail = f"雙雙跌破20MA（多頭崩解）｜{_names}"

        _gc_diag.collect()

        return (adl_status, adl_detail, foreign_status, foreign_detail,
                pillars_status, pillars_detail)

    (_adl_status, _adl_detail, _foreign_status, _foreign_detail,
     _pillars_status, _pillars_detail) = _auto_diagnose_market_health()

    diag_c1, diag_c2, diag_c3 = st.columns(3)

    with diag_c1:
        st.markdown("**① 騰落線（ADL）趨勢**")
        _adl_icon  = "🟢" if _adl_status == "HEALTHY" else "🔴" if _adl_status == "BEARISH" else "⚪"
        _adl_color = "#00e676" if _adl_status == "HEALTHY" else "#ff5252" if _adl_status == "BEARISH" else "#7fb3d3"
        st.markdown(
            f"<div style='padding:10px 12px;border-radius:8px;border-left:3px solid {_adl_color};"
            f"background:rgba(0,0,0,0.2);font-size:.85rem;color:{_adl_color};'>"
            f"{_adl_icon} {_adl_detail}</div>",
            unsafe_allow_html=True
        )
    adl_healthy = (_adl_status == "HEALTHY")

    with diag_c2:
        st.markdown("**② 外資期現貨共振狀態**")
        _fgn_icon  = "🟢" if _foreign_status == "SAFE" else "🔴"
        _fgn_color = "#00e676" if _foreign_status == "SAFE" else "#ff5252"
        st.markdown(
            f"<div style='padding:10px 12px;border-radius:8px;border-left:3px solid {_fgn_color};"
            f"background:rgba(0,0,0,0.2);font-size:.85rem;color:{_fgn_color};'>"
            f"{_fgn_icon} {_foreign_detail}</div>",
            unsafe_allow_html=True
        )
    foreign_healthy = (_foreign_status == "SAFE")

    with diag_c3:
        st.markdown("**③ 台股多頭支柱技術型態**")
        _pil_icon  = "🟢" if _pillars_status == "HEALTHY" else "🔴" if _pillars_status == "BREAK" else "⚪"
        _pil_color = "#00e676" if _pillars_status == "HEALTHY" else "#ff5252" if _pillars_status == "BREAK" else "#7fb3d3"
        st.markdown(
            f"<div style='padding:10px 12px;border-radius:8px;border-left:3px solid {_pil_color};"
            f"background:rgba(0,0,0,0.2);font-size:.8rem;color:{_pil_color};'>"
            f"{_pil_icon} {_pillars_detail}</div>",
            unsafe_allow_html=True
        )
    pillar_healthy = (_pillars_status == "HEALTHY")

    # ── 自動決策輸出
    healthy_count = sum([adl_healthy, foreign_healthy, pillar_healthy])
    danger_count  = sum([_adl_status == "BEARISH", _foreign_status == "DUMP", _pillars_status == "BREAK"])

    st.markdown("<br>", unsafe_allow_html=True)

    # 總診斷自適應綜合指引（依需求規格：3項全過＝假警報；外資DUMP+雙雄崩解＝真危機）
    if healthy_count == 3:
        st.success(
            "✅ **安全診斷：假警報！健康的板塊輪動**　"
            "（3/3 項健康指標通過）　"
            "現股部位維持綠燈續抱，無須恐慌。"
        )
    elif _foreign_status == "DUMP" and _pillars_status == "BREAK":
        st.error(
            "❌ **高危診斷：真危機！大資金撤離且多頭支柱破位**　"
            "系統啟動風控防線，短線標的加速以退為進！"
        )
    elif danger_count >= 2:
        st.error(
            f"❌ **危險診斷：真警報！多頭結構性崩解**　"
            f"（{danger_count}/3 項出現危險訊號）　"
            f"多頭已失守關鍵支撐，建議啟動防守機制。"
        )
    else:
        st.warning(
            "⚠️ **中性觀望：訊號混沌，暫不明確**　"
            "多空各有訊號交織，建議縮小部位靜觀其變。"
        )

    # ── 診斷明細
    with st.expander("📋 診斷明細（自動運算依據）", expanded=False):
        items = [
            ("騰落線（ADL）", adl_healthy, _adl_detail),
            ("外資期現貨共振", foreign_healthy, _foreign_detail),
            ("台股多頭支柱", pillar_healthy, _pillars_detail),
        ]
        for label, is_healthy, val in items:
            color = "#00e676" if is_healthy else "#ff5252"
            icon  = "✅" if is_healthy else "❌"
            st.markdown(
                f"<div style='padding:8px 12px;margin:4px 0;border-radius:6px;"
                f"border-left:3px solid {color};background:rgba(0,0,0,0.2);'>"
                f"<b style='color:{color};'>{icon} {label}</b>　"
                f"<span style='color:#b0cce0;font-size:.85rem;'>{val}</span></div>",
                unsafe_allow_html=True
            )
        st.caption("📌 取樣股票池前40檔（依 stock_list.csv 順序），每30分鐘重新計算一次。")

    st.markdown("---")

    df_fut, ok_fut = get_futures()

    # ── 解析期貨籌碼
    def parse_futures_chips(df_fut):
        result = dict(
            tx_foreign=None, mtx_dealer=None,
            mtx_trust=None,  mtx_foreign=None,
            mtx_oi=None, data_date="未知", is_real=False,
        )
        if not ok_fut or df_fut.empty:
            return result

        df_fut = df_fut.copy()
        df_fut["date"] = pd.to_datetime(df_fut["date"], errors="coerce")

        # 欄位名稱映射（相容新舊格式）
        name_col = next((c for c in ["name","institutional_investors"] if c in df_fut.columns), None)
        lc = next((c for c in df_fut.columns if "long_open_interest_balance" in c and "amount" not in c), None)
        sc = next((c for c in df_fut.columns if "short_open_interest_balance" in c and "amount" not in c), None)

        # 大台外資未平倉
        inst_df = df_fut[df_fut["source"] == "institutional"] if "source" in df_fut.columns else df_fut
        tx_df   = inst_df[inst_df["contract"] == "TX"] if "contract" in inst_df.columns else pd.DataFrame()

        if not tx_df.empty and lc and sc and name_col:
            ld = tx_df["date"].max()
            result["data_date"] = str(ld)[:10]
            row = tx_df[(tx_df["date"] == ld) & tx_df[name_col].astype(str).str.contains("外資", na=False)]
            if not row.empty:
                try:
                    result["tx_foreign"] = int(float(row[lc].values[0])) - int(float(row[sc].values[0]))
                    result["is_real"] = True
                except:
                    pass

        # 小台三大法人
        mtx_df = inst_df[inst_df["contract"] == "MTX"] if "contract" in inst_df.columns else pd.DataFrame()
        if not mtx_df.empty and lc and sc and name_col:
            ld = mtx_df["date"].max()
            if result["data_date"] == "未知":
                result["data_date"] = str(ld)[:10]
            for kw, key in [("自營","mtx_dealer"),("投信","mtx_trust"),("外資","mtx_foreign")]:
                r = mtx_df[(mtx_df["date"] == ld) & mtx_df[name_col].astype(str).str.contains(kw, na=False)]
                if not r.empty:
                    try:
                        result[key] = int(float(r[lc].values[0])) - int(float(r[sc].values[0]))
                        result["is_real"] = True
                    except:
                        pass

        # 小台全市場未平倉
        daily_df = df_fut[df_fut["source"] == "daily"] if "source" in df_fut.columns else pd.DataFrame()
        if not daily_df.empty:
            ld2  = daily_df["date"].max()
            oi_c = next((c for c in daily_df.columns if c == "open_interest"), None)
            if oi_c:
                try:
                    result["mtx_oi"] = int(pd.to_numeric(
                        daily_df[daily_df["date"] == ld2][oi_c], errors="coerce").sum())
                except:
                    pass

        return result
    chips = parse_futures_chips(df_fut)

    # ── 顯示資料來源標籤
    if chips["is_real"]:
        st.markdown(
            f"<span style='background:rgba(0,230,118,.12);border:1px solid #00e676;"
            f"color:#00e676;border-radius:4px;padding:2px 8px;font-size:.72rem;'>"
            f"🟢 CSV 真實數據｜資料日期：{chips['data_date']}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span style='background:rgba(255,171,64,.12);border:1px solid #ffab40;"
            "color:#ffab40;border-radius:4px;padding:2px 8px;font-size:.72rem;'>"
            "🟡 預設值（請執行 update_data.py --only futures）</span>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 手動輸入（CSV 讀取的值自動帶入）
    st.markdown("<div class='sec-title'>📡 期貨籌碼</div>", unsafe_allow_html=True)
    pi1, pi2, pi3 = st.columns(3)
    with pi1:
        st.markdown("<span style='color:#ff9800;font-size:.78rem;font-weight:600;'>大台指（TX）</span>",
                    unsafe_allow_html=True)
        tx_foreign  = st.number_input("外資未平倉淨額（口）",
                                       value=int(chips["tx_foreign"] or -52000), step=500)
    with pi2:
        st.markdown("<span style='color:#00d4ff;font-size:.78rem;font-weight:600;'>小台（MTX）法人</span>",
                    unsafe_allow_html=True)
        mtx_dealer  = st.number_input("自營商淨額（口）", value=int(chips["mtx_dealer"]  if chips["mtx_dealer"]  is not None else -8500),  step=100)
        mtx_trust   = st.number_input("投信淨額（口）",   value=int(chips["mtx_trust"]   if chips["mtx_trust"]   is not None else -3200),  step=100)
        mtx_foreign = st.number_input("外資淨額（口）",   value=int(chips["mtx_foreign"] if chips["mtx_foreign"] is not None else -18300), step=100)
    with pi3:
        st.markdown("<span style='color:#e040fb;font-size:.78rem;font-weight:600;'>小台市場</span>",
                    unsafe_allow_html=True)
        mtx_oi = st.number_input("全市場未平倉（口）",
                                  value=int(chips["mtx_oi"] or 98000), step=500, min_value=1)

    # ── 計算核心指標
    mtx_inst_total = mtx_dealer + mtx_trust + mtx_foreign
    retail_net     = mtx_inst_total * (-1)
    retail_ratio   = retail_net / mtx_oi * 100

    # ── 期貨預警引擎紅綠燈
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='sec-title'>🚦 期貨預警引擎</div>", unsafe_allow_html=True)
    pl1, pl2, pl3 = st.columns(3)

    # 地雷指標
    with pl1:
        st.markdown("<span style='color:#ff9800;font-weight:600;font-size:.8rem;'>地雷指標｜大台外資</span>",
                    unsafe_allow_html=True)
        if tx_foreign < -60000:
            st.markdown("<div class='sig-red'>🧨 極端危險｜外資超過6萬口空單</div>",
                        unsafe_allow_html=True)
        elif tx_foreign < -40000:
            st.markdown("<div class='sig-red'>🧨 系統性地雷：外資重倉空單警戒！</div>",
                        unsafe_allow_html=True)
        elif tx_foreign < -20000:
            st.markdown("<div class='sig-warn'>⚠️ 輕度警示｜淨空 2～4 萬口</div>",
                        unsafe_allow_html=True)
        elif tx_foreign > 20000:
            st.markdown("<div class='sig-green'>✅ 外資偏多｜淨多超過 2 萬口</div>",
                        unsafe_allow_html=True)
        else:
            st.info("🔵 中性｜無明確方向")

    # 導火線
    with pl2:
        st.markdown("<span style='color:#ff5252;font-weight:600;font-size:.8rem;'>導火線｜散戶部位</span>",
                    unsafe_allow_html=True)
        if retail_net > 30000:
            st.markdown("<div class='sig-red'>🔥 崩盤導火線已燃：散戶大量加槓桿！</div>",
                        unsafe_allow_html=True)
        elif retail_net > 10000:
            st.markdown("<div class='sig-red'>🔥 導火線燃燒中：散戶正在接刀！</div>",
                        unsafe_allow_html=True)
        elif retail_net > 0:
            st.markdown("<div class='sig-warn'>🟡 微燃｜散戶小幅淨多</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<div class='sig-green'>✅ 散戶偏空｜踩踏風險低</div>",
                        unsafe_allow_html=True)

    # 綜合危險等級
    danger = 0
    if tx_foreign < -40000:   danger += 3
    elif tx_foreign < -20000: danger += 1
    if retail_net > 10000:    danger += 3
    elif retail_net > 0:      danger += 1
    if retail_ratio > 20:     danger += 2
    elif retail_ratio > 10:   danger += 1

    with pl3:
        st.markdown("<span style='color:#e040fb;font-weight:600;font-size:.8rem;'>綜合危險等級</span>",
                    unsafe_allow_html=True)
        if danger >= 5:
            st.markdown(f"<div class='sig-red'>🔴 極高風險｜評分：{danger}/8</div>",
                        unsafe_allow_html=True)
        elif danger >= 3:
            st.markdown(f"<div class='sig-warn'>🟠 中高風險｜評分：{danger}/8</div>",
                        unsafe_allow_html=True)
        elif danger >= 1:
            st.markdown(f"<div class='sig-warn'>🟡 輕度警示｜評分：{danger}/8</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='sig-green'>🟢 低風險｜評分：{danger}/8</div>",
                        unsafe_allow_html=True)

    # ── 視覺化：部位比較圖 + 雷達圖
    st.markdown("<br>", unsafe_allow_html=True)
    vc1, vc2 = st.columns(2)
    with vc1:
        cats = ["大台外資","小台自營","小台投信","小台外資","散戶淨多"]
        vals = [tx_foreign, mtx_dealer, mtx_trust, mtx_foreign, retail_net]
        fbar = go.Figure(go.Bar(
            y=cats, x=vals, orientation="h",
            marker_color=["#ff5252" if v < 0 else "#00e676" for v in vals],
            text=[f"{v:+,}" for v in vals], textposition="outside",
            textfont=dict(size=10, color="#e8f4fd"),
        ))
        fbar.add_vline(x=0,       line_color="#546e7a",  line_width=1)
        fbar.add_vline(x=-40000,  line_dash="dot",
                       line_color="#ff5252", line_width=1.5,
                       annotation_text="地雷線", annotation_font_color="#ff5252")
        fbar.update_layout(**base_layout("法人 vs 散戶部位（口）", 300))
        st.plotly_chart(fbar, width='stretch')

    with vc2:
        r_cats = ["大台外資空壓","散戶導火線","散戶多空比","行為學分數","綜合危險"]
        r_max  = [80000, 50000, 50, 100, 8]
        r_act  = [min(abs(tx_foreign), 80000),
                  min(max(retail_net, 0), 50000),
                  min(max(retail_ratio, 0), 50),
                  0,   # 行為學分數稍後填入
                  danger / 8 * 100]
        r_pct  = [a/m*100 for a, m in zip(r_act, r_max)]
        r_pct_c = r_pct + [r_pct[0]]
        r_cats_c = r_cats + [r_cats[0]]
        frad = go.Figure()
        frad.add_trace(go.Scatterpolar(
            r=r_pct_c, theta=r_cats_c, fill="toself",
            name="風險", line_color="#ff5252",
            fillcolor="rgba(255,82,82,.15)",
        ))
        frad.add_trace(go.Scatterpolar(
            r=[50]*len(r_cats_c), theta=r_cats_c,
            mode="lines", name="警戒線",
            line=dict(color="#ffab40", dash="dot", width=1.5),
        ))
        frad.update_layout(
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(visible=True, range=[0,100],
                                gridcolor=GRID_COL, color=TEXT_COL,
                                ticksuffix="%"),
                angularaxis=dict(gridcolor=GRID_COL, color="#e8f4fd"),
            ),
            **base_layout("崩盤預警雷達", 300),
            showlegend=True,
        )
        st.plotly_chart(frad, width='stretch')

    # ══════════════════════════════════════════════
    # 蒙格行為學清單
    # ══════════════════════════════════════════════
    st.markdown("<div class='sec-title'>🧠 查理·蒙格行為學大崩盤信號</div>",
                unsafe_allow_html=True)

    # ── 讀取 AI 蒙格情緒分析結果
    munger_ai = None
    try:
        _mu = f"{GITHUB_RAW}/munger_sentiment.json"
        import requests as _rqmu
        _rmu = _rqmu.get(_mu, timeout=8)
        if _rmu.status_code == 200:
            munger_ai = _rmu.json()
    except Exception:
        pass

    # ── AI 趨勢總結展示
    if munger_ai:
        _trend    = munger_ai.get("trend_analysis", "")
        _dscore   = munger_ai.get("danger_score", 0)
        _days     = munger_ai.get("analysis_days", 1)
        _gen_at   = munger_ai.get("generated_at", "")
        _triggered = munger_ai.get("triggered_indexes", [])
        _keys     = munger_ai.get("key_signals", [])

        # 危險分數顏色
        _dc = "#ff5252" if _dscore >= 60 else "#ff9800" if _dscore >= 30 else "#00e676"
        _di = "🔴" if _dscore >= 60 else "🟠" if _dscore >= 30 else "🟢"

        st.markdown(
            f"<div style='background:linear-gradient(135deg,rgba(30,58,95,0.6),rgba(15,32,39,0.8));"
            f"border:1px solid #1e3a5f;border-left:4px solid {_dc};"
            f"border-radius:10px;padding:14px 18px;margin-bottom:10px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<span style='color:#00d4ff;font-size:.82rem;font-weight:700;letter-spacing:.05em;'>"
            f"🧠 AI 連續 {_days} 日情緒趨勢分析</span>"
            f"<span style='color:{_dc};font-size:1.1rem;font-weight:700;'>"
            f"{_di} 危險分數 {_dscore}/100</span></div>"
            f"<div style='color:#b0cce0;font-size:.85rem;margin-top:8px;line-height:1.6;'>{_trend}</div>"
            + (f"<div style='margin-top:8px;'>"
               + "".join(f"<span style='background:rgba(255,82,82,0.15);color:#ff8a80;"
                         f"border:1px solid #ff5252;padding:2px 8px;border-radius:10px;"
                         f"font-size:.75rem;margin:2px;display:inline-block;'>⚠️ {s}</span>"
                         for s in _keys)
               + "</div>" if _keys else "")
            + f"<div style='color:#546e7a;font-size:.72rem;margin-top:6px;'>生成時間：{_gen_at}</div>"
            f"</div>",
            unsafe_allow_html=True
        )
    else:
        st.caption("🧠 AI情緒分析尚無資料，請執行 generate_munger_sentiment.py")

    # AI 自動觸發的信號索引（1-based）
    _auto = set(munger_ai.get("triggered_indexes", [])) if munger_ai else set()

    bc1, bc2 = st.columns(2)
    with bc1:
        st.markdown("<span style='color:#ffab40;font-size:.76rem;font-weight:600;'>📣 市場情緒面</span>",
                    unsafe_allow_html=True)
        b1  = st.checkbox("市場充斥「這次不一樣、估值重構」言論",  value=(1 in _auto))
        b2  = st.checkbox("散戶對利空麻木，認為拉回就是買點",      value=(2 in _auto))
        b3  = st.checkbox("強勢股（AI概念）出現大量散戶追捧",      value=(3 in _auto))
        b4  = st.checkbox("媒體頻繁出現「萬八萬九」類標題",        value=(4 in _auto))
        b5  = st.checkbox("身邊非投資人士開始詢問如何開戶",        value=(5 in _auto))
        b6  = st.checkbox("散戶急於向下攤平，加碼重挫個股",        value=(6 in _auto))
    with bc2:
        st.markdown("<span style='color:#e040fb;font-size:.76rem;font-weight:600;'>📊 技術籌碼面</span>",
                    unsafe_allow_html=True)
        b7  = st.checkbox("融資餘額創近期新高或大量新增信用帳戶",  value=(7 in _auto))
        b8  = st.checkbox("指數創新高但多數個股跌破均線（背離）",  value=(8 in _auto))
        b9  = st.checkbox("外資連續多日在現貨大額賣超",            value=(9 in _auto))
        b10 = st.checkbox("量縮價穩假象（成交量萎縮指數卻在高點）", value=(10 in _auto))
        b11 = st.checkbox("權值股無量上攻後急跌，籌碼鬆動",        value=(11 in _auto))
        b12 = st.checkbox("期貨逆價差擴大（法人對沖意願增強）",    value=(12 in _auto))

    checks    = [b1,b2,b3,b4,b5,b6,b7,b8,b9,b10,b11,b12]
    chk_count = sum(checks)
    beh_score = chk_count / 12

    # 更新雷達圖行為學分數（已顯示，此處計算用）
    beh_label = (
        "🔴 極度貪婪危險區" if beh_score >= .67 else
        "🟠 行為異常警戒區" if beh_score >= .42 else
        "🟡 輕度情緒偏熱"   if beh_score >= .17 else
        "🟢 市場情緒正常"
    )

    sp1, sp2 = st.columns([3, 1])
    with sp1:
        st.markdown(
            f"<div style='color:#e8f4fd;font-size:.82rem;font-weight:600;"
            f"margin-bottom:5px;'>行為學風險指數：{beh_label}</div>",
            unsafe_allow_html=True,
        )
        st.progress(beh_score)
    with sp2:
        bc = "#ff5252" if beh_score >= .67 else "#ffab40" if beh_score >= .42 else "#00e676"
        mcard(sp2, "勾選數", f"{chk_count}/12")

    # ══════════════════════════════════════════════
    # AI 綜合診斷（if-else 邏輯）
    # ══════════════════════════════════════════════
    st.markdown("<div class='sec-title'>🤖 AI 綜合診斷報告</div>",
                unsafe_allow_html=True)

    def generate_diagnosis(tx_f, r_net, r_ratio, d_score, b_score, b3_on, chk_n, is_real):
        note = "（CSV真實數據）" if is_real else "（預設值，請更新資料）"

        # 市場階段判斷
        if d_score >= 5 and b_score >= .5:
            stage = "🔴 高檔誘多 · 末升段出貨結構"
            desc  = "外資重倉空單＋散戶追高＋行為學過熱，典型法人出貨陷阱。市場表面強勢實為誘多，主力正在出清部位。"
            action = (
                "- **強烈建議降倉至三成以下**，保留現金等待系統性回調\n"
                "- 所有持多單設停損於支撐下方 1~2%\n"
                "- 嚴禁加碼或攤平"
            )
        elif d_score >= 3 and b_score >= .33:
            stage = "🟠 高檔震盪 · 籌碼鬆動期"
            desc  = "部分法人開始減碼，散戶情緒偏熱，波動即將擴大。"
            action = (
                "- **降倉至五成**，強勢股先行鎖利\n"
                "- 設好停損，回調 5~8% 可試探分批承接\n"
                "- 留意爆量長黑作為出場信號"
            )
        elif r_net < 0 and tx_f > -20000:
            stage = "🟢 相對安全 · 波段低點偵測"
            desc  = "散戶已偏空、外資空壓不大，悲觀情緒已反映。"
            action = (
                "- **維持正常持倉**，留意技術低點布局機會\n"
                "- 籌碼乾淨個股可逐步加碼\n"
                "- 設好停損保護利潤"
            )
        else:
            stage = "🟡 中性盤整 · 持續觀察"
            desc  = "目前訊號混雜，無明確多空方向。"
            action = (
                "- **輕倉等待方向明朗**\n"
                "- 觀察外資未平倉變化作為方向指引\n"
                "- 個股選擇籌碼乾淨、法人持續買入者"
            )

        # 散戶多空比四區間警戒
        if r_ratio < -10:
            retail_alert = "🟢 **散戶偏空（{:.1f}%）**：具備軋空動能，相對安全區".format(r_ratio)
        elif r_ratio <= 10:
            retail_alert = "⚪ **散戶中性（{:.1f}%）**：盤整區，暫無明確方向".format(r_ratio)
        elif r_ratio <= 20:
            retail_alert = "🟠 **散戶偏多（{:.1f}%）**：主力高檔倒貨警戒，注意出貨陷阱".format(r_ratio)
        else:
            retail_alert = "🔴 **散戶極度樂觀（{:.1f}%）**：歷史崩盤高危區，極端危險".format(r_ratio)

        ai_text = (
            f"**📍 當前階段{note}：{stage}**\n\n"
            f"> {desc}\n\n"
            f"**📊 散戶多空比警戒**\n{retail_alert}\n\n"
            f"**📌 現貨操作建議**\n{action}\n\n"
        )

        if b3_on and d_score >= 3:
            ai_text += (
                "**🤖 AI強勢股警示**\n"
                "- ⚠️ 社群熱度與法人動向背離，**建議減持 AI 概念股 50% 以上**\n"
                "- 待爆量長黑（散戶認賠出場）後才是真正布局點\n\n"
            )

        if chk_n >= 8:
            ai_text += (
                "**🧠 蒙格警語**\n"
                "> *在別人貪婪時恐懼，在別人恐懼時貪婪。—— 巴菲特*\n\n"
                f"勾選 {chk_n}/12 項過熱信號，歷史大崩盤前此指標高度重疊。"
                "請認真考慮清倉或加大避險部位。\n"
            )
        elif chk_n >= 4:
            ai_text += f"\n> ⚠️ 勾選 {chk_n}/12 行為警訊，保持冷靜，嚴格控制倉位。\n"

        return ai_text

    diag = generate_diagnosis(
        tx_foreign, retail_net, retail_ratio,
        danger, beh_score, b3, chk_count, chips["is_real"],
    )

    d_border = (
        "#ff5252" if danger >= 5 or beh_score >= .67 else
        "#ffab40" if danger >= 3 or beh_score >= .42 else
        "#00d4ff"
    )
    d_bg = (
        "rgba(61,10,10,.5)"  if danger >= 5 or beh_score >= .67 else
        "rgba(45,27,0,.5)"   if danger >= 3 or beh_score >= .42 else
        "rgba(10,20,40,.5)"
    )

    st.markdown(
        f"<div style='background:{d_bg};border:1px solid {d_border};"
        f"border-left:4px solid {d_border};border-radius:10px;"
        f"padding:16px 20px;margin-top:6px;'>"
        f"<div style='color:{d_border};font-size:.74rem;font-weight:700;"
        f"letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;'>"
        f"🤖 AI 診斷報告 ── 自動生成</div>",
        unsafe_allow_html=True,
    )
    st.markdown(diag)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        "<div style='color:#546e7a;font-size:.68rem;margin-top:8px;text-align:center;'>"
        "⚠️ 以上診斷僅供參考，不構成投資建議。期貨數據請以台灣期交所官方公告為準。"
        "</div>",
        unsafe_allow_html=True,
    )

# ──────────────────────────────────────────────────────────────
# ▌ TAB 6：每日作戰總部（MTFA 專屬報告）
# ──────────────────────────────────────────────────────────────
with tab6:
    st.markdown("<div class='sec-title'>📝 每日作戰總部 · MTFA 狙擊報告</div>",
                unsafe_allow_html=True)

    # ── 大小台雙軌聯鎖警示（頂部置頂）
    _alert_lvl_t6, _tx_net_t6, _mtx_retail_t6 = get_dual_alert()
    _danger_t6  = _alert_lvl_t6 == "red"
    _yellow_t6  = _alert_lvl_t6 == "yellow"
    _squeeze_t6 = _mtx_retail_t6 <= -15000
    if _danger_t6:
        st.error(f"🔴 **【最高防空警報・高度警戒】** 大台空單 {abs(_tx_net_t6):,} 口 × 小台散戶多單 {_mtx_retail_t6:,} 口！"
                 f"短線部位全面縮緊，隨時準備盤中緊急平倉！")
    elif _yellow_t6:
        st.warning(f"🟡 **【環境風險升溫・常規警戒】** 大台空單 {abs(_tx_net_t6):,} 口 × 小台散戶多單 {_mtx_retail_t6:,} 口！"
                   f"散戶接刀初現，短線建倉資金嚴格控管！")
    elif _squeeze_t6:
        st.info(f"🔥 **【黃金軋空訊號】** 小台散戶放空 {abs(_mtx_retail_t6):,} 口！"
                f"主力大機率發動軋空，短線動能充足！")
    else:
        st.success(f"🟢 **【大盤環境安全】** 大台 {_tx_net_t6:+,} 口 × 小台散戶 {_mtx_retail_t6:+,} 口，環境健康。")

    _LONG_TERM = {"2330","2317","2454","2308","2357","3037","8299","3289","2301",
                  "2313","2345","2383","3044","6274","6285","2634","3149","7828"}
    _SHORT_TERM = {"3491","2359","6188","3661","3324","3017","3653","3533","6669",
                   "3131","6187","6510","6515","3455","6239","2059","2368","6271",
                   "3665","3481","2404","2327","2344","2379","8358","1519","1503",
                   "8033","8027","8064"}
    # ── 智慧刷新 + 大盤聯鎖警示
    _today_tw6 = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    _price_date = st.session_state.get("live_prices_date", "—")
    _prices_stale = _price_date != _today_tw6

    # 讀取期貨空單
    _df_fut_t6, _ok_fut_t6 = get_futures()
    _tx_net_t6 = 0
    if _ok_fut_t6 and not _df_fut_t6.empty:
        try:
            _nm_t6 = next((c for c in ["name","institutional_investors"]
                           if c in _df_fut_t6.columns), None)
            _lc_t6 = next((c for c in _df_fut_t6.columns
                           if "long_open_interest_balance" in c and "amount" not in c), None)
            _sc_t6 = next((c for c in _df_fut_t6.columns
                           if "short_open_interest_balance" in c and "amount" not in c), None)
            _inst_t6 = _df_fut_t6[_df_fut_t6["source"]=="institutional"]                        if "source" in _df_fut_t6.columns else _df_fut_t6
            _tx_t6  = _inst_t6[_inst_t6["contract"]=="TX"]                       if "contract" in _inst_t6.columns else pd.DataFrame()
            if not _tx_t6.empty and _lc_t6 and _sc_t6 and _nm_t6:
                _ld_t6 = _tx_t6["date"].max()
                _row_t6 = _tx_t6[(_tx_t6["date"]==_ld_t6) &
                                  _tx_t6[_nm_t6].astype(str).str.contains("外資", na=False)]
                if not _row_t6.empty:
                    _tx_net_t6 = int(float(_row_t6[_lc_t6].values[0])) -                                  int(float(_row_t6[_sc_t6].values[0]))
        except:
            pass
    _danger_t6 = _tx_net_t6 <= -30000

    # 期貨警示
    if _danger_t6:
        st.error(f"🚨 **【最高防空警報】** 外資期貨空單 {abs(_tx_net_t6):,} 口！"
                 f"短線部位請全面縮緊，隨時準備盤中緊急平倉！")
    else:
        st.success(f"🟢 **【大盤環境安全】** 外資期貨空單 {abs(_tx_net_t6):,} 口，"
                   f"依各別個股策略常規控盤。")

    _r6c1, _r6c2 = st.columns([2, 5])
    with _r6c1:
        if st.button("🔄 刷新個股即時價", key="refresh_live_tab6",
                     type="primary" if _prices_stale else "secondary",
                     use_container_width=True):
            st.session_state.live_prices = {}
            st.session_state.live_prices_date = _today_tw6
            all_wl6 = list({w["id"] for w in
                           st.session_state.get("watchlist", []) +
                           st.session_state.get("watchlist_scan", [])})
            with st.spinner(f"抓取 {len(all_wl6)} 檔即時報價..."):
                for _sid6 in all_wl6:
                    _lv6 = fetch_live_price(_sid6)
                    if _lv6:
                        st.session_state.live_prices[_sid6] = _lv6
            st.toast(f"✅ 已更新 {len(st.session_state.live_prices)} 檔即時報價", icon="✅")
            st.rerun()
    with _r6c2:
        if _prices_stale:
            st.markdown(
                f"<span style='color:#ff9800;font-size:.85rem;'>⚠️ 個股報價停在 {_price_date}，點左側按鈕刷新</span>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f"<span style='color:#00e676;font-size:.85rem;'>✅ 個股報價已是今日（{_today_tw6}）最新</span>",
                unsafe_allow_html=True
            )

    # ── 國際總經核彈倒數日曆（動態版，自動讀 macro_events.json）
    with st.expander("🌐 國際總經核彈倒數計時器", expanded=False):
        from datetime import date as _date
        _today = datetime.now(ZoneInfo("Asia/Taipei")).date()

        @st.cache_data(ttl=3600, show_spinner=False)
        def _load_macro_events():
            """讀取 macro_events.json，過濾過期事件，回傳未來事件列表"""
            import json as _json, os as _os
            events = []
            _local = _os.path.join("data", "macro_events.json")
            if _os.path.exists(_local):
                try:
                    with open(_local, "r", encoding="utf-8") as f:
                        events = _json.load(f).get("events", [])
                except Exception:
                    pass
            if not events:
                try:
                    import requests as _req
                    _r = _req.get(f"{GITHUB_RAW}/macro_events.json", timeout=8)
                    if _r.status_code == 200:
                        events = _r.json().get("events", [])
                except Exception:
                    pass
            _today_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
            return sorted(
                [e for e in events if isinstance(e.get("date"), str) and e["date"] >= _today_str],
                key=lambda x: x["date"]
            )

        _events = _load_macro_events()
        if not _events:
            st.caption("⚡ 近期無重大總經事件（請執行 update_macro_events.py 更新）")
        else:
            def _render_event_row(ev):
                try:
                    _ed   = _date.fromisoformat(ev["date"])
                    _dd   = (_ed - _today).days
                    _name = ev.get("event", "")
                    _ctry = ev.get("country", "🌐")
                    _stars = "⭐" * min(ev.get("level", 2), 3)
                    if _dd == 0:
                        _dc = "#ff3333"; _ds = "🔴 今天！"
                    elif _dd <= 3:
                        _dc = "#ff6b35"; _ds = f"🟠 {_dd}天後！"
                    elif _dd <= 7:
                        _dc = "#fbbf24"; _ds = f"🟡 {_dd}天後"
                    else:
                        _dc = "#8892b0"; _ds = f"{_dd}天後"
                    return (
                        f"<div style='display:flex;justify-content:space-between;padding:5px 0;"
                        f"border-bottom:1px solid #1e3a5f;font-size:.84rem;'>"
                        f"<span style='color:#e8f4fd;'>{_ctry} {_name} <span style='color:#556;font-size:.72rem;'>{_stars}</span></span>"
                        f"<span style='color:{_dc};font-weight:700;'>{_ed.strftime('%m/%d')} ({_ds})</span>"
                        f"</div>"
                    )
                except Exception:
                    return ""

            # 預設顯示前 2 條
            _top2 = [_render_event_row(e) for e in _events[:2]]
            st.markdown("".join(_top2), unsafe_allow_html=True)

            # 其餘收合在 expander 裡
            if len(_events) > 2:
                with st.expander(f"＋ 查看其餘 {len(_events)-2} 個事件", expanded=False):
                    _rest = [_render_event_row(e) for e in _events[2:]]
                    st.markdown("".join(_rest), unsafe_allow_html=True)

    # ── AI 今日市場資金題材洞察
    def load_dynamic_themes():
        try:
            import requests as _req, base64 as _b64, json as _json
            # 用 GitHub API 讀取（完全繞過 CDN 快取）
            _api_url = (f"https://api.github.com/repos/"
                       f"RabbitAstronaut/taiwan-stock-dashboard/"
                       f"contents/data/dynamic_themes.json")
            _headers = {"Accept": "application/vnd.github.v3+json"}
            if GITHUB_TOKEN:
                _headers["Authorization"] = f"token {GITHUB_TOKEN}"
            _r = _req.get(_api_url, headers=_headers, timeout=10)
            if _r.status_code == 200:
                _content = _r.json().get("content", "")
                _decoded = _b64.b64decode(_content).decode("utf-8")
                return _json.loads(_decoded)
        except Exception:
            pass
        return None

    themes_data = load_dynamic_themes()
    st.markdown("<div class='sec-title'>🤖 AI 今日市場資金題材洞察</div>",
                unsafe_allow_html=True)

    if themes_data:
        themes   = themes_data.get("themes", [])
        reason   = themes_data.get("reason", "")
        trade_dt = themes_data.get("trade_date", "")
        is_fb    = themes_data.get("is_fallback", False)

        # 題材標籤
        tag_html = "".join(
            f"<span style='background:linear-gradient(135deg,#0066cc,#0044aa);"
            f"color:#fff;padding:5px 16px;border-radius:20px;font-size:.92rem;"
            f"font-weight:700;margin-right:10px;letter-spacing:.05em;'>"
            f"🔥 {t}</span>"
            for t in themes
        )
        st.markdown(
            f"<div style='background:rgba(0,100,200,0.08);border:1px solid #0066cc;"
            f"border-radius:12px;padding:16px 20px;margin-bottom:8px;'>"
            f"<div style='margin-bottom:10px;'>{tag_html}</div>"
            f"<div style='color:#b0cce0;font-size:.88rem;line-height:1.6;'>"
            f"💡 <b style='color:#e8f4fd;'>深度解析：</b>{reason}</div>"
            + ("<div style='color:#ff9800;font-size:.75rem;margin-top:6px;'>⚠️ 使用預設題材（API 降級）</div>" if is_fb else "")
            + "</div>",
            unsafe_allow_html=True
        )
        # 相關個股清單 + 加入監控
        top15 = themes_data.get("top15", [])
        if top15:
            st.markdown(
                "<div style='margin-top:10px;margin-bottom:6px;'>"
                "<span style='color:#7fb3d3;font-size:.82rem;font-weight:600;"
                "letter-spacing:.05em;'>📋 相關個股（點選加入監控）：</span></div>",
                unsafe_allow_html=True
            )
            # 每行8個，按鈕小字：「2887 台新金 ＋」
            st.markdown("""<style>
            div[data-testid="stHorizontalBlock"] button{
                font-size:.72rem!important;padding:2px 4px!important;
                background:linear-gradient(135deg,#162535,#1e3a5f)!important;
                color:#e8f4fd!important;border:1px solid #2a5080!important;
                min-height:28px!important;line-height:1.2!important;}
            div[data-testid="stHorizontalBlock"] button p{color:#e8f4fd!important;}
            div[data-testid="stHorizontalBlock"] button:hover{border-color:#00d4ff!important;}
            </style>""", unsafe_allow_html=True)
            rows = [top15[i:i+8] for i in range(0, len(top15), 8)]
            for row in rows:
                cols = st.columns(len(row))
                for col, stock_str in zip(cols, row):
                    parts = stock_str.split(" ", 1)
                    sid   = parts[0].strip()
                    sname = parts[1].strip() if len(parts) > 1 else sid
                    with col:
                        already = any(w["id"] == sid for w in
                                      st.session_state.get("watchlist", []) +
                                      st.session_state.get("watchlist_scan", []))
                        label = f"{sid} ✅" if already else f"{sid} {sname[:3]} ＋"
                        if st.button(label, key=f"ai_add_{sid}",
                                     use_container_width=True,
                                     help=f"{sid} {sname}"):
                            if not already:
                                st.session_state.watchlist.append(
                                    {"id": sid, "name": sname})
                                save_watchlist_to_github(
                                    st.session_state.watchlist,
                                    st.session_state.watchlist_scan,
                                    {k: v for k, v in st.session_state.etf_shares.items() if v > 0},
                                    reserve=st.session_state.get("reserve_list", [])
                                )
                                st.toast(f"✅ {sid} {sname} 已加入監控", icon="✅")
                                st.rerun()
        st.caption(
            f"*此報告由 Gemini 2.5 Flash 分析 {trade_dt} 法人買超 Top 15 個股自動生成"
        )
    else:
        st.caption("🤖 題材分析引擎尚無資料，請等待 GitHub Actions 每日排程更新。")

    st.markdown("---")

    # 合併手動+掃描清單
    all_wl = st.session_state.get("watchlist", []) + st.session_state.get("watchlist_scan", [])
    # 去重
    seen = set()
    all_wl_dedup = []
    for w in all_wl:
        if w["id"] not in seen:
            seen.add(w["id"])
            all_wl_dedup.append(w)

    if not all_wl_dedup:
        st.markdown("""
        <div style='background:#0f2027;border:2px dashed #1e3a5f;border-radius:12px;
             padding:50px;text-align:center;'>
            <div style='font-size:2rem;margin-bottom:10px;'>📋</div>
            <div style='color:#e8f4fd;font-size:.92rem;font-weight:600;'>監控清單為空</div>
            <div style='color:#7fb3d3;font-size:.8rem;margin-top:8px;'>
                請先在 Tab1 或 Sidebar 加入監控標的
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='infobox'>共 <b style='color:#00d4ff;'>{len(all_wl_dedup)}</b> 檔標的，"
            f"每張卡片同時顯示 🔴 警示 與 🟢 買進信號，供多空判斷。</div>",
            unsafe_allow_html=True
        )

        for w in all_wl_dedup:
            sid  = w["id"]
            name = w.get("name", sid)

            # 載入 K 線 + 計算指標
            df_w, ok_w = load_price_csv(sid)
            if not ok_w or df_w.empty or len(df_w) < 20:
                with st.expander(f"⚠️ {sid} {name}｜資料不足", expanded=False):
                    st.caption("K 線資料不足 20 筆，無法產生報告。")
                continue

            df_w = add_indicators(df_w)
            lt   = df_w.iloc[-1]   # 最新一筆
            pv   = df_w.iloc[-2]   # 前一筆

            # ── 籌碼資料（投信連續買超）
            df_c, ok_c = get_chips(sid)
            trust_consec = 0
            if ok_c and not df_c.empty and "name" in df_c.columns and "net" in df_c.columns:
                trust = df_c[df_c["name"].astype(str).str.contains("Investment_Trust|投信", na=False)]
                if "date" in trust.columns:
                    trust = trust.sort_values("date")
                    trust_vals = trust.groupby("date")["net"].sum().tail(5)
                    # 從最新往前數連續買超天數
                    for v in reversed(trust_vals.values):
                        if v > 0:
                            trust_consec += 1
                        else:
                            break

            # ── 信號判斷
            alerts  = []  # 紅色警示
            signals = []  # 綠色買進

            # 即時資料優先
            live_t4 = st.session_state.live_prices.get(sid)
            close_now  = float(live_t4["close"]) if live_t4 else float(lt["Close"])
            close_prev = float(pv["Close"])

            # 若有即時現價，動態替換最後一筆收盤價重算 EMA5
            if live_t4:
                _df_dyn = df_w.copy()
                _df_dyn.loc[_df_dyn.index[-1], "Close"] = close_now
                _ema5_series = _df_dyn["Close"].astype(float).ewm(span=5, adjust=False).mean()
                ema5 = float(_ema5_series.iloc[-1])
            else:
                ema5 = float(lt.get("EMA5", float("nan")))
            rsi5  = float(lt.get("RSI5",  float("nan")))
            rsi20 = float(lt.get("RSI20", float("nan")))
            bb_mid = float(lt.get("BB_MID", float("nan")))
            lb2    = float(lt.get("LB2",    float("nan")))
            bb_mid_prev = float(pv.get("BB_MID", float("nan")))

            # 紅色警示
            if not np.isnan(ema5) and close_now < ema5:
                alerts.append(f"🔴 股價跌破 EMA5（{ema5:.1f}），短線趨勢轉弱")
            if not np.isnan(rsi5) and not np.isnan(rsi20):
                if rsi5 > 80 and rsi5 < rsi20:
                    alerts.append(f"🔴 RSI(5)={rsi5:.1f} 高檔回落且低於 RSI(20)，注意獲利了結")

            # 🟢 買進信號 1：籌碼點火（投信連續買超 >= 2天）
            if trust_consec >= 2:
                signals.append(
                    f"🟢 【籌碼點火】投信連續買超 {trust_consec} 天，"
                    f"正規軍建倉中，具備波段發動潛力。"
                )

            # 🟢 買進信號 2：動能轉強（RSI 黃金交叉）
            if not np.isnan(rsi5) and not np.isnan(rsi20):
                if rsi5 > rsi20 and 40 <= rsi5 <= 60:
                    signals.append(
                        f"🟢 【動能轉強】RSI(5)={rsi5:.1f} > RSI(20)={rsi20:.1f} "
                        f"且處於法人安全吃貨區 (40~60)，短線多方奪回優勢。"
                    )

            # 🟢 買進信號 3a：向上突破布林中軌
            if not np.isnan(bb_mid) and not np.isnan(bb_mid_prev):
                if close_now > bb_mid and close_prev < bb_mid_prev:
                    signals.append(
                        f"🟢 【均線突破】收盤價（{close_now:.1f}）站上布林通道中軌（{bb_mid:.1f}），"
                        f"趨勢轉多。"
                    )
                # 🟢 買進信號 3b：下軌支撐
                elif not np.isnan(lb2) and close_now >= lb2 and close_now <= lb2 * 1.015:
                    signals.append(
                        f"🟢 【下軌支撐】股價（{close_now:.1f}）回測布林下軌 LB2（{lb2:.1f}）"
                        f"具備支撐，可留意右側反轉買點。"
                    )

            # ── 卡片顏色判斷
            if alerts and not signals:
                card_border = "#ff5252"
                card_bg     = "rgba(255,82,82,0.06)"
                status_icon = "🔴"
                status_text = "需要防守"
            elif signals and not alerts:
                card_border = "#00e676"
                card_bg     = "rgba(0,230,118,0.06)"
                status_icon = "🟢"
                status_text = "適合買進"
            elif signals and alerts:
                card_border = "#ff9800"
                card_bg     = "rgba(255,152,0,0.06)"
                status_icon = "🟡"
                status_text = "多空交雜"
            else:
                card_border = "#1e3a5f"
                card_bg     = "rgba(30,58,95,0.3)"
                status_icon = "⚪"
                status_text = "觀望"

            # ── 長短線分類標籤
            _is_long  = sid in _LONG_TERM
            _is_short = sid in _SHORT_TERM
            _type_tag = "🏢長線" if _is_long else ("⚡短線" if _is_short else "📊觀察")

            # ── V6 三軌聯鎖 × 長短線策略 × 即時 EMA5 → 戰術 SOP
            _ema5_break = not np.isnan(ema5) and close_now < ema5
            _risk_st_t6b, _risk_i_t6b = get_system_risk_status()

            if _is_short:
                # 短線特種兵：重視即時 EMA5 + 大盤聯鎖
                if (_risk_st_t6b == "RED_ALERT") and _ema5_break:
                    st.markdown(
                        f"<div style='background:rgba(244,63,94,0.15);border:2px solid #e11d48;"
                        f"border-radius:8px;padding:8px 14px;margin-bottom:4px;'>"
                        f"<b style='color:#f43f5e;'>🛑 【盤中緊急平倉令！】</b>"
                        f"<span style='color:#f43f5e;font-size:.87rem;'>"
                        f"大盤三軌高度警戒+現價 {close_now:.1f} 跌破實時 EMA5（{ema5:.1f}）！"
                        f"短線防禦崩塌，<b>請立即 100% 市價平倉，絕不留戀！</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )
                elif _risk_st_t6b == "RED_ALERT":
                    st.markdown(
                        f"<div style='background:rgba(251,191,36,0.08);border:1px solid #fbbf24;"
                        f"border-radius:8px;padding:6px 14px;margin-bottom:4px;'>"
                        f"<span style='color:#fbbf24;font-size:.87rem;'>"
                        f"⚠️ 大盤高度警戒！現價 {close_now:.1f} 守住 EMA5（{ema5:.1f}），"
                        f"<b>縮緊停利至今日低點，隨時準備平倉。</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )
                elif _ema5_break:
                    st.markdown(
                        f"<div style='background:rgba(251,191,36,0.06);border:1px solid #fbbf24;"
                        f"border-radius:8px;padding:6px 14px;margin-bottom:4px;'>"
                        f"<span style='color:#fbbf24;font-size:.87rem;'>"
                        f"⚠️ 短線技術面轉弱：現價 {close_now:.1f} 跌破實時 EMA5（{ema5:.1f}），"
                        f"<b>建議減碼 1/2 或執行停利。</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )
                elif _risk_st_t6b == "SHORT_SQUEEZE":
                    st.markdown(
                        f"<div style='background:rgba(255,153,0,0.06);border:1px solid #ff9900;"
                        f"border-radius:8px;padding:6px 14px;margin-bottom:4px;'>"
                        f"<span style='color:#ff9900;font-size:.87rem;'>"
                        f"🔥 軋空特赦！現價 {close_now:.1f} 踩穩 EMA5（{ema5:.1f}），"
                        f"<b>大戶全力點火，安心享受動能利潤！</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )
            elif _is_long:
                # 長線常規軍：無視盤中震盪，看大戶和30週線
                if _risk_st_t6b == "RED_ALERT" and bias_ma20 < -5:
                    st.markdown(
                        f"<div style='background:rgba(244,63,94,0.1);border:1px solid #f43f5e;"
                        f"border-radius:8px;padding:6px 14px;margin-bottom:4px;'>"
                        f"<span style='color:#f43f5e;font-size:.87rem;'>"
                        f"🛑 長線：大盤高度警戒+弱勢套牢（乖離{bias_ma20:.1f}%），"
                        f"<b>考慮執行尾盤停損！</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        f"<div style='background:rgba(74,222,128,0.05);border-left:3px solid #4ade80;"
                        f"border-radius:4px;padding:4px 12px;margin-bottom:4px;'>"
                        f"<span style='color:#86efac;font-size:.82rem;'>"
                        f"🛡️ 長線核心：乖離{bias_ma20:.1f}%，無視盤中震盪，"
                        f"遵循30週線趨勢與大戶鎖碼節奏，<b>嚴禁恐慌亂砍！</b>"
                        f"</span></div>", unsafe_allow_html=True
                    )

            # ── 長短線雙重 SOP 建議
            _is_above_ema5 = not np.isnan(ema5) and close_now >= ema5
            _long_sop = (f"🛡️ 長線：乖離 {bias_ma20:.1f}%，趨勢健康，無視盤中震盪續抱。"
                         if bias_ma20 <= 15 else
                         f"🛡️ 長線：乖離 {bias_ma20:.1f}% 偏高，縮緊至30週線為停利基準。")
            if _danger_t6 and not _is_above_ema5:
                _short_sop = f"🛑 短線：大台空單+散戶多單壓頂+跌破EMA5，立即平倉！"
                _sop_color = "#f43f5e"
            elif _yellow_t6 and not _is_above_ema5:
                _short_sop = f"⚠️ 短線：黃燈警戒+跌破EMA5，減碼1/2。"
                _sop_color = "#fbbf24"
            elif _danger_t6 or _yellow_t6:
                _short_sop = f"⚠️ 短線：大盤警戒中，縮緊停利至EMA5({ema5:.1f})。"
                _sop_color = "#fbbf24"
                _sop_color = "#fbbf24"
            elif not _is_above_ema5:
                _short_sop = f"⚠️ 短線：跌破EMA5（{ema5:.1f}），減碼1/2或停利。"
                _sop_color = "#fbbf24"
            else:
                _short_sop = f"🚀 短線：站上EMA5（{ema5:.1f}），動能正常，安心續抱。"
                _sop_color = "#ff9900"

            # ── 渲染卡片
            _price_tag = "⚡" if live_t4 else "📅"
            _price_lbl = f"{_price_tag} {close_now:.1f}"
            with st.expander(
                f"{status_icon} {sid} {name}｜{_type_tag}｜{status_text}"
                f"  ┊  {_price_lbl}"
                f"  ┊  RSI5 {rsi5:.0f}"
                f"  ┊  EMA5 {ema5:.1f}" if not np.isnan(ema5) else
                f"{status_icon} {sid} {name}｜{_type_tag}｜{status_text}",
                expanded=True
            ):
                # ── 長短線 SOP 區
                st.markdown(
                    f"<div style='display:flex;gap:8px;margin-bottom:8px;'>"
                    f"<div style='flex:1;background:rgba(74,222,128,0.08);border-left:3px solid #4ade80;"
                    f"border-radius:4px;padding:5px 10px;font-size:.78rem;color:#a7f3d0;'>{_long_sop}</div>"
                    f"<div style='flex:1;background:rgba(255,255,255,0.03);border-left:3px solid {_sop_color};"
                    f"border-radius:4px;padding:5px 10px;font-size:.78rem;color:{_sop_color};'>{_short_sop}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )

                # 警示區
                if alerts:
                    st.markdown("<div style='margin-bottom:6px;'>", unsafe_allow_html=True)
                    for a in alerts:
                        st.markdown(
                            f"<div style='background:rgba(255,82,82,0.12);border-left:3px solid #ff5252;"
                            f"border-radius:6px;padding:8px 12px;margin:4px 0;"
                            f"color:#ff8a80;font-size:.83rem;'>{a}</div>",
                            unsafe_allow_html=True
                        )
                    st.markdown("</div>", unsafe_allow_html=True)

                # 買進信號區
                if signals:
                    st.markdown("<div style='margin-bottom:6px;'>", unsafe_allow_html=True)
                    for s in signals:
                        st.markdown(
                            f"<div style='background:rgba(0,230,118,0.10);border-left:3px solid #00e676;"
                            f"border-radius:6px;padding:8px 12px;margin:4px 0;"
                            f"color:#69f0ae;font-size:.83rem;'>{s}</div>",
                            unsafe_allow_html=True
                        )
                    st.markdown("</div>", unsafe_allow_html=True)

                # 無信號
                if not alerts and not signals:
                    st.markdown(
                        "<div style='color:#546e7a;font-size:.83rem;padding:8px 0;'>"
                        "⚪ 目前無明顯信號，持續觀察中。</div>",
                        unsafe_allow_html=True
                    )

                # 指標數值小結
                cols = st.columns(4)
                def _fmt(v): return f"{v:.1f}" if not np.isnan(v) else "—"
                cols[0].metric("EMA5",   _fmt(ema5))
                cols[1].metric("RSI5",   _fmt(rsi5))
                cols[2].metric("RSI20",  _fmt(rsi20))
                cols[3].metric("BB中軌", _fmt(bb_mid))




# ──────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────
# ▌ TAB 2：台股新大陸大數據雷達
# ──────────────────────────────────────────────────────────────
with tab2:
    st.markdown("<div class='sec-title'>📡 台股新大陸大數據雷達</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>自動掃描全台股，透過三大量化雷達抓出自選清單以外的黑馬主流。"
        "資料來源：日線 CSV + 籌碼 CSV。</div>",
        unsafe_allow_html=True
    )

    @st.cache_data(ttl=1800, show_spinner="大數據雷達掃描中...")
    def run_radar():
        """掃描全台股三大雷達，回傳結果 DataFrame"""
        # 載入基礎資料
        df_si, ok_si   = get_stock_info()
        df_ch, ok_ch   = get_chips()
        df_fin, ok_fin = get_financials()
        df_sh, ok_sh   = get_shareholder()

        if not ok_si or df_si.empty:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        df_si["stock_id"] = df_si["stock_id"].astype(str).str.strip()

        # 整理籌碼
        foreign_net = pd.Series(dtype=float)
        trust_net   = pd.Series(dtype=float)
        if ok_ch and not df_ch.empty:
            df_ch["stock_id"] = df_ch["stock_id"].astype(str).str.strip()
            df_ch["net"] = pd.to_numeric(df_ch.get("net", 0), errors="coerce").fillna(0)
            if "name" in df_ch.columns and "date" in df_ch.columns:
                df_ch["date"] = pd.to_datetime(df_ch["date"], errors="coerce")
                latest_date = df_ch["date"].max()
                df_latest = df_ch[df_ch["date"] == latest_date]
                f_df = df_latest[df_latest["name"].astype(str).str.contains("Foreign_Investor", na=False)]
                t_df = df_latest[df_latest["name"].astype(str).str.contains("Investment_Trust", na=False)]
                foreign_net = f_df.groupby("stock_id")["net"].sum() / 1000  # 換算張數
                trust_net   = t_df.groupby("stock_id")["net"].sum() / 1000

        # 整理大戶持股
        big_pct = pd.Series(dtype=float)
        if ok_sh and not df_sh.empty:
            df_sh["stock_id"] = df_sh["stock_id"].astype(str).str.strip()
            if "HoldingSharesLevel" in df_sh.columns and "percent" in df_sh.columns:
                df_sh["percent"] = pd.to_numeric(df_sh["percent"], errors="coerce")
                df_sh["date"] = pd.to_datetime(df_sh.get("date"), errors="coerce")
                latest_sh = df_sh["date"].max()
                df_sh_l = df_sh[df_sh["date"] == latest_sh]
                df_sh_l = df_sh_l[~df_sh_l["HoldingSharesLevel"].astype(str).str.contains("total|差異", na=False)]
                big_kw = ["400,001","600,001","800,001","1,000,001","more than"]
                is_big = df_sh_l["HoldingSharesLevel"].astype(str).str.contains("|".join(big_kw), na=False)
                big_pct = df_sh_l[is_big].groupby("stock_id")["percent"].sum()

        # 整理EPS YoY
        eps_yoy = pd.Series(dtype=float)
        if ok_fin and not df_fin.empty:
            df_fin["stock_id"] = df_fin["stock_id"].astype(str).str.strip()
            df_fin["date"] = pd.to_datetime(df_fin.get("date"), errors="coerce")
            df_fin["value"] = pd.to_numeric(df_fin.get("value"), errors="coerce")
            eps_df = df_fin[df_fin.get("type","") == "EPS"] if "type" in df_fin.columns else pd.DataFrame()
            if not eps_df.empty:
                for sid, grp in eps_df.groupby("stock_id"):
                    grp = grp.sort_values("date")
                    if len(grp) >= 5:
                        latest = grp.iloc[-1]["value"]
                        yoy    = grp.iloc[-5]["value"]
                        if yoy and yoy != 0:
                            eps_yoy[sid] = (latest - yoy) / abs(yoy) * 100

        # 掃描所有股票
        radar1, radar2, radar3 = [], [], []
        all_sids = df_si["stock_id"].tolist()

        for sid in all_sids:
            df_p, ok_p = load_price_csv(sid)
            if not ok_p or df_p.empty or len(df_p) < 20:
                continue

            df_p = add_indicators(df_p)
            lt   = df_p.iloc[-1]
            close  = float(lt["Close"])
            ema5   = float(lt.get("EMA5",   float("nan")))
            sma20  = float(lt.get("MA20",   float("nan")))
            vol    = float(lt.get("Volume", 0))
            vma5   = float(lt.get("VMA5",   float("nan")))

            if any(np.isnan(v) for v in [ema5, sma20]):
                continue

            name = sid
            name_row = df_si[df_si["stock_id"] == sid]
            if not name_row.empty:
                name = str(name_row["stock_name"].iloc[0])

            f_net = float(foreign_net.get(sid, 0))
            t_net = float(trust_net.get(sid, 0))

            # ── 雷達1：土洋認養
            if (f_net > 1500 and t_net > 800 and
                close > ema5 and ema5 > sma20):
                radar1.append({
                    "代號": sid, "名稱": name,
                    "現價": round(close, 1),
                    "外資買超(張)": int(f_net),
                    "投信買超(張)": int(t_net),
                    "EMA5": round(ema5, 1),
                    "SMA20": round(sma20, 1),
                    "AI戰略評語": "🟢 內外資共振，趨勢向上，可積極布局" if close > sma20 * 1.02 else "🟡 剛啟動，觀察量能確認"
                })

            # ── 雷達2：黃金窒息量
            if (not np.isnan(vma5) and vma5 > 0 and
                vol <= vma5 * 0.45 and close >= ema5):
                # 檢查5日內是否有漲幅>7%長紅K
                recent5 = df_p.tail(6)
                has_long_red = False
                for i in range(1, len(recent5)):
                    prev_c = float(recent5.iloc[i-1]["Close"])
                    curr_c = float(recent5.iloc[i]["Close"])
                    if prev_c > 0 and (curr_c - prev_c) / prev_c > 0.07:
                        has_long_red = True
                        break
                if has_long_red:
                    radar2.append({
                        "代號": sid, "名稱": name,
                        "現價": round(close, 1),
                        "量比(vol/VMA5)": round(vol/vma5, 2),
                        "EMA5": round(ema5, 1),
                        "投信買超(張)": int(t_net),
                        "AI戰略評語": "🔥 主力鎖倉惜售，噴發前兆，等量縮突破" if close >= sma20 else "🟡 守EMA5，等月線確認"
                    })

            # ── 雷達3：大戶硬漢
            bp = float(big_pct.get(sid, 0))
            ey = float(eps_yoy.get(sid, float("nan")))
            if (not np.isnan(ey) and ey > 50 and
                bp > 70 and close > sma20):
                radar3.append({
                    "代號": sid, "名稱": name,
                    "現價": round(close, 1),
                    "EPS YoY%": round(ey, 1),
                    "千張大戶持股%": round(bp, 1),
                    "投信買超(張)": int(t_net),
                    "AI戰略評語": "💎 基本面硬核+股權集中，長線黑馬首選" if bp > 80 else "🟢 大戶穩健持有，基本面支撐強"
                })

        return (pd.DataFrame(radar1) if radar1 else pd.DataFrame(),
                pd.DataFrame(radar2) if radar2 else pd.DataFrame(),
                pd.DataFrame(radar3) if radar3 else pd.DataFrame())

    # 執行掃描
    if st.button("🔍 啟動大數據雷達掃描", type="primary", key="radar_scan"):
        st.cache_data.clear()
        st.rerun()

    df_r1, df_r2, df_r3 = run_radar()

    # 存入 session_state 供 Tab1 使用
    st.session_state["radar_r1"] = df_r1
    st.session_state["radar_r2"] = df_r2
    st.session_state["radar_r3"] = df_r3

    # 頂部 metric
    m1, m2, m3 = st.columns(3)
    m1.metric("🌊 土洋認養雷達", f"{len(df_r1)} 檔", delta="內外資共振")
    m2.metric("⚡ 黃金窒息量雷達", f"{len(df_r2)} 檔", delta="主力鎖倉惜售")
    m3.metric("💎 大戶硬漢雷達", f"{len(df_r3)} 檔", delta="基本面硬核")

    st.markdown("---")

    # 整合顯示
    for title, df_r, color in [
        ("🌊 土洋認養雷達（內外資共振起漲股）", df_r1, "#00d4ff"),
        ("⚡ 黃金窒息量雷達（主力鎖倉噴發前兆）", df_r2, "#ffeb3b"),
        ("💎 大戶硬漢雷達（基本面黑馬長線首選）", df_r3, "#00e676"),
    ]:
        st.markdown(
            f"<div style='border-left:4px solid {color};padding:4px 12px;margin:12px 0;'>"
            f"<b style='color:{color};'>{title}</b></div>",
            unsafe_allow_html=True
        )
        if df_r.empty:
            st.info("今日無符合條件的個股")
        else:
            st.markdown(df_to_html(df_r, height=300), unsafe_allow_html=True)
            # 加入監控按鈕
            add_radar = st.multiselect(
                "加入監控清單",
                [f"{r['代號']} {r['名稱']}" for _, r in df_r.iterrows()],
                key=f"radar_add_{title[:3]}"
            )
            if st.button("⭐ 加入監控", key=f"radar_btn_{title[:3]}"):
                added = 0
                for item in add_radar:
                    code = item.split()[0]
                    name = " ".join(item.split()[1:])
                    if not any(w["id"]==code for w in st.session_state.watchlist_scan):
                        st.session_state.watchlist_scan.append({"id":code,"name":name})
                        added += 1
                if added > 0:
                    save_watchlist_to_github(st.session_state.watchlist,
                                             st.session_state.watchlist_scan,
                                             {k:v for k,v in st.session_state.etf_shares.items() if v>0},
                                             reserve=st.session_state.get("reserve_list", []))
                    st.toast(f"✅ 已加入 {added} 檔到掃描監控清單")

# ▌ TAB 7：ETF 存股現金流管家
# ──────────────────────────────────────────────────────────────
with tab7:
    st.markdown("<div class='sec-title'>💰 ETF 存股現金流管家</div>", unsafe_allow_html=True)
    # ETF 配息資料更新日期
    try:
        _etf_df, _etf_ok = load_csv("etf_dividend_data.csv")
        if _etf_ok and not _etf_df.empty:
            _etf_date_col = next((c for c in ["ex_dividend_date","ExDividendDate","date"] if c in _etf_df.columns), None)
            if _etf_date_col:
                _etf_latest = pd.to_datetime(_etf_df[_etf_date_col], errors="coerce").max()
                _etf_date_str = str(_etf_latest)[:10] if pd.notna(_etf_latest) else "未知"
                st.caption(f"📅 配息資料截至：{_etf_date_str}")
    except Exception:
        pass
    st.markdown("<div class='infobox'>在下方輸入持有張數，系統即時試算投入本金、預估年化殖利率與未來 12 個月現金流。</div>", unsafe_allow_html=True)

    @st.cache_data(ttl=300)
    def fetch_etf_price(stock_id: str) -> tuple:
        """回傳 (現值, 更新時間字串)"""
        try:
            import yfinance as yf
            # ETF 代號補零到4碼（如 50 → 0050）
            sid = str(stock_id).strip().zfill(4)
            for suffix in [".TW", ".TWO"]:
                tk = yf.Ticker(sid + suffix)
                hist = tk.history(period="5d")
                if not hist.empty:
                    price = round(float(hist["Close"].iloc[-1]), 2)
                    update_time = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%m/%d %H:%M")
                    return price, update_time
        except Exception:
            pass
        return 0.0, ""

    @st.cache_data(ttl=3600, show_spinner="載入 ETF 配息資料...")
    def build_etf_menu() -> pd.DataFrame:
        df_csv, ok = load_csv("etf_dividend_data.csv")
        if not ok or df_csv.empty:
            return pd.DataFrame(columns=["代號","最新配息/股","年化配息/股","頻率","配息月份"])
        amt_col  = next((c for c in ["CashDividend","cash_dividend","dividend"] if c in df_csv.columns), None)
        date_col = next((c for c in ["ex_dividend_date","ExDividendDate","date"] if c in df_csv.columns), None)
        if not amt_col or not date_col:
            return pd.DataFrame(columns=["代號","最新配息/股","年化配息/股","頻率","配息月份"])
        df_csv["stock_id"] = df_csv["stock_id"].astype(str).str.strip()
        df_csv[date_col]   = pd.to_datetime(df_csv[date_col], errors="coerce")
        df_csv[amt_col]    = pd.to_numeric(df_csv[amt_col], errors="coerce").fillna(0)
        df_csv = df_csv.dropna(subset=[date_col])
        rows = []
        for sid, grp in df_csv.groupby("stock_id"):
            grp = grp.sort_values(date_col)
            latest_div = round(float(grp[amt_col].iloc[-1]), 4)
            one_year = grp[grp[date_col] >= pd.Timestamp(datetime.now() - timedelta(days=365))]
            freq = len(one_year) if not one_year.empty else len(grp.tail(4))
            freq_label = "月配" if freq >= 10 else ("季配" if freq >= 3 else ("半年配" if freq >= 2 else "年配"))
            annual_div = round(float(grp[amt_col].tail(max(freq,1)).sum()), 4)
            div_months = sorted(one_year[date_col].dt.month.unique().tolist()) if not one_year.empty \
                         else sorted(grp.tail(max(freq,1))[date_col].dt.month.unique().tolist())
            months_str = "/".join(str(m) for m in div_months) + "月"
            rows.append({"代號": sid, "最新配息/股": latest_div, "年化配息/股": annual_div,
                         "頻率": freq_label, "配息月份": months_str})
        df_out = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["代號","最新配息/股","年化配息/股","頻率","配息月份"])
        return df_out.sort_values("代號").reset_index(drop=True)

    df_menu = build_etf_menu()
    if df_menu.empty:
        st.warning("ETF 配息資料載入失敗，請確認 data/etf_dividend_data.csv 是否存在。")
        pass  # removed st.stop()

    # etf_shares 已在啟動時從 GitHub 載入，不重設
    if "etf_shares" not in st.session_state:
        st.session_state.etf_shares = {}  # 備用（通常不會執行到）
    # 自動還原上次確認的試算組合
    if "etf_confirmed_portfolio" not in st.session_state:
        st.session_state.etf_confirmed_portfolio = {
            sid: sh for sid, sh in st.session_state.etf_shares.items() if sh > 0
        }

    st.markdown(f"### 📋 ETF 清單　共 {len(df_menu)} 檔　輸入張數後自動試算")
    st.caption("最新配息/股=最近一次除息　年化配息/股=近1年合計　配息月份=歷史除息月份")

    # 批次抓取所有 ETF 股價（快取5分鐘）
    @st.cache_data(ttl=300)
    def get_all_etf_prices(sids: tuple) -> dict:
        result = {}
        for sid in sids:
            val = fetch_etf_price(sid)
            result[sid] = val[0] if isinstance(val, tuple) else val
        return result

    price_map = get_all_etf_prices(tuple(df_menu["代號"].tolist()))

    # 顯示最後更新時間
    _etf_last_update = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%m/%d %H:%M")
    st.caption(f"⚡ 現值最後更新：{_etf_last_update}（每5分鐘自動刷新）")

    # ── 匯出 CSV 按鈕（直接下載，不需額外套件）
    import io as _io
    _rows_export = []
    for _, _row in df_menu.iterrows():
        _sid   = str(_row["代號"])
        _price = price_map.get(_sid, 0.0)
        _yr    = round(_row["年化配息/股"] / _price * 100, 2) if _price > 0 else 0.0
        _rows_export.append({
            "代號": _sid,
            "現股價": _price if _price > 0 else "",
            "最新配息/股": _row["最新配息/股"],
            "年化配息/股": _row["年化配息/股"],
            "年化殖利率%": _yr,
            "配息頻率": _row["頻率"],
            "配息月份": _row["配息月份"],
        })
    _df_export = pd.DataFrame(_rows_export)
    _csv_buf = _io.StringIO()
    _df_export.to_csv(_csv_buf, index=False, encoding='utf-8-sig')
    st.download_button(
        label="📥 匯出 ETF 清單 CSV",
        data=_csv_buf.getvalue().encode('utf-8-sig'),
        file_name=f"ETF清單_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_etf_csv"
    )

    h = st.columns([1, 1, 1, 1, 1, 1, 1.5, 1.5])
    for col, txt in zip(h, ["代號","現股價","最新配息/股","年化配息/股","年化殖利率","頻率","配息月份","持有張數"]):
        col.markdown(f"<b style='color:#00d4ff;font-size:1.1rem;'>{txt}</b>", unsafe_allow_html=True)
    st.markdown("<div style='border-bottom:1px solid #1e3a5f;margin-bottom:4px;'></div>", unsafe_allow_html=True)

    for _, row in df_menu.iterrows():
        sid = str(row["代號"])
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1, 1, 1, 1, 1, 1, 1.5, 1.5])
        c1.markdown(f"<span style='color:#e8f4fd;font-size:1.1rem;font-weight:600;'>{sid}</span>", unsafe_allow_html=True)
        price = price_map.get(sid, 0.0)
        price_str = f"{price:.2f}" if price > 0 else "—"
        c2.markdown(f"<span style='color:#e8f4fd;font-size:1.1rem;'>{price_str}</span>", unsafe_allow_html=True)
        c3.markdown(f"<span style='color:#ffeb3b;font-size:1.1rem;'>{row['最新配息/股']:.4f}</span>", unsafe_allow_html=True)
        c4.markdown(f"<span style='color:#00e676;font-size:1.1rem;'>{row['年化配息/股']:.4f}</span>", unsafe_allow_html=True)
        # 年化殖利率
        if price > 0:
            yield_r = row['年化配息/股'] / price * 100
            yield_color = "#ff5252" if yield_r >= 6 else ("#ffeb3b" if yield_r >= 4 else "#e8f4fd")
            yield_str = f"{yield_r:.2f}%"
        else:
            yield_color = "#546e7a"
            yield_str = "—"
        c5.markdown(f"<span style='color:{yield_color};font-size:1.1rem;font-weight:600;'>{yield_str}</span>", unsafe_allow_html=True)
        freq_color = "#00d4ff" if row["頻率"] == "月配" else ("#ff9800" if row["頻率"] == "季配" else "#e8f4fd")
        c6.markdown(f"<span style='color:{freq_color};font-size:1.1rem;'>{row['頻率']}</span>", unsafe_allow_html=True)
        c7.markdown(f"<span style='color:#b0cce0;font-size:1.1rem;'>{row['配息月份']}</span>", unsafe_allow_html=True)
        cur_sh = st.session_state.etf_shares.get(sid, 0)
        new_sh = c8.number_input("張", value=cur_sh, min_value=0, step=1,
                                  key=f"t5_sh_{sid}", label_visibility="collapsed")
        if int(new_sh) != cur_sh:
            st.session_state.etf_shares[sid] = int(new_sh)
            save_watchlist_to_github(
                st.session_state.watchlist,
                st.session_state.watchlist_scan,
                {k: v for k, v in st.session_state.etf_shares.items() if v > 0}
            )

    # 確認鈕
    st.markdown("<br>", unsafe_allow_html=True)
    confirm_col, _ = st.columns([2, 6])
    with confirm_col:
        confirm = st.button("✅ 確認試算", key="etf_confirm", use_container_width=True, type="primary")

    # 試算結果存入 session_state，按確認才更新
    if confirm:
        st.session_state.etf_confirmed_portfolio = {
            sid: sh for sid, sh in st.session_state.etf_shares.items() if sh > 0
        }

    portfolio = st.session_state.get("etf_confirmed_portfolio", {})
    if not portfolio:
        st.info("👆 請在上方清單輸入張數後，按「✅ 確認試算」開始計算。")
        pass  # removed st.stop()

    st.markdown("---")
    st.markdown(f"### 📊 試算組合：{len(portfolio)} 檔 ETF")

    total_cost = 0.0
    with st.spinner("抓取最新股價..."):
        for sid in portfolio:
            _pval = fetch_etf_price(sid)
            price = _pval[0] if isinstance(_pval, tuple) else _pval
            total_cost += price * portfolio[sid] * 1000

    today_dt = datetime.now()
    months   = pd.date_range(today_dt, periods=12, freq="MS")
    forecast_rows = []
    total_annual_div = 0.0

    for sid, shares in portfolio.items():
        row_menu = df_menu[df_menu["代號"] == sid]
        if row_menu.empty:
            continue
        annual = float(row_menu["年化配息/股"].iloc[0])
        freq_l = row_menu["頻率"].iloc[0]
        freq   = 12 if freq_l == "月配" else (4 if freq_l == "季配" else (2 if freq_l == "半年配" else 1))
        per_time = annual / max(freq, 1)
        total_annual_div += annual * shares * 1000

        # 用真實配息月份（從 div_months 欄位解析）
        months_str = str(row_menu["配息月份"].iloc[0]) if "配息月份" in row_menu.columns else ""
        real_months = []
        try:
            real_months = [int(x) for x in months_str.replace("月","").split("/") if x.strip().isdigit()]
        except Exception:
            real_months = []

        # 若無真實月份資料，退化為等間隔
        if not real_months:
            interval = max(1, 12 // freq)
            for j, m in enumerate(months):
                if j % interval == 0:
                    forecast_rows.append({"月份": m.strftime("%Y-%m"), "ETF": sid,
                                          "預估現金流": round(per_time * shares * 1000, 0)})
        else:
            # 用真實配息月份決定現金流
            real_months_set = set(real_months)
            for m in months:
                if m.month in real_months_set:
                    forecast_rows.append({"月份": m.strftime("%Y-%m"), "ETF": sid,
                                          "預估現金流": round(per_time * shares * 1000, 0)})

    yield_rate = (total_annual_div / total_cost * 100) if total_cost > 0 else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("💰 總投入預估本金",   f"${total_cost:,.0f}")
    m2.metric("📅 未來一年預估股息", f"${total_annual_div:,.0f}")
    m3.metric("📈 預估年化殖利率",   f"{yield_rate:.2f}%",
              delta="高" if yield_rate >= 5 else ("中" if yield_rate >= 3 else "低"))
    st.markdown("---")

    if not forecast_rows:
        st.info("無法推算未來配息。")
        pass  # removed st.stop()

    df_forecast = pd.DataFrame(forecast_rows)
    st.markdown("### 📅 未來 12 個月預估現金流")
    colors_etf = ["#00d4ff","#ffeb3b","#00e676","#e91e8c","#ff9800","#e040fb","#69f0ae","#ff6e40","#40c4ff","#b2ff59"]
    fig_etf = go.Figure()
    for idx, sid in enumerate(portfolio.keys()):
        sub_f = df_forecast[df_forecast["ETF"] == sid]
        if sub_f.empty:
            continue
        fig_etf.add_trace(go.Bar(x=sub_f["月份"], y=sub_f["預估現金流"], name=sid,
                                  marker_color=colors_etf[idx % len(colors_etf)]))
    fig_etf.update_layout(**base_layout("未來12個月預估現金流（元）", 400), barmode="stack")
    st.plotly_chart(fig_etf, width='stretch')

    st.markdown("### 📋 財務總表")
    if not df_forecast.empty and "ETF" in df_forecast.columns and "月份" in df_forecast.columns:
        pivot = df_forecast.pivot_table(index="月份", columns="ETF", values="預估現金流", aggfunc="sum", fill_value=0).reset_index()
        pivot.columns.name = None
        etf_cols = [c for c in pivot.columns if c != "月份"]
        pivot["合計（元）"] = pivot[etf_cols].sum(axis=1)
        st.markdown(df_to_html(pivot, height=440, font_size="1.1rem"), unsafe_allow_html=True)
    else:
        st.info("請先輸入張數並確認試算以顯示財務總表。")


    # ══════════════════════════════════════════════
    # 🔍 主力資金流向雷達（ETF 籌碼追蹤）
    # ══════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div class='sec-title'>🔍 主力資金流向雷達 · ETF 籌碼追蹤</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>選擇 ETF 查看近 20 日三大法人買賣超與融資餘額變化，"
        "系統自動診斷主力資金動向。</div>",
        unsafe_allow_html=True
    )

    # ETF 選單（來自總表）
    # 只顯示已確認試算組合的 ETF
    etf_options = list(portfolio.keys()) if portfolio else []
    if not etf_options:
        st.info("請先在上方輸入張數並按「✅ 確認試算」。")
        pass  # removed st.stop()

    radar_sid = st.selectbox("選擇 ETF", etf_options, key="radar_etf")

    # 讀取籌碼資料（真實三大法人+融資券）
    df_c_etf, ok_c_etf = get_chips(radar_sid)

    if not ok_c_etf or df_c_etf.empty:
        st.warning(
            f"{radar_sid} 在 chips_data.csv 中尚無資料。"
            " 請執行：python update_data.py --only chips --force"
        )
        pass  # removed st.stop()

    df_c = df_c_etf.copy()
    df_c["stock_id"] = df_c["stock_id"].astype(str).str.strip()
    df_c = df_c[df_c["stock_id"] == str(radar_sid).strip()]

    if "date" in df_c.columns:
        df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
        df_c = df_c.sort_values("date")

    net_col  = "net"  if "net"  in df_c.columns else None
    name_col = "name" if "name" in df_c.columns else None

    if not net_col or not name_col:
        st.warning(f"欄位不足：{df_c.columns.tolist()}")
        pass  # removed st.stop()

    df_c[net_col] = pd.to_numeric(df_c[net_col], errors="coerce").fillna(0)

    # 外資、投信
    foreign = df_c[df_c[name_col].astype(str).str.contains("Foreign_Investor", na=False)]
    trust   = df_c[df_c[name_col].astype(str).str.contains("Investment_Trust", na=False)]

    def daily_net(df_sub, n=20):
        if df_sub.empty:
            return pd.Series(dtype=float)
        return df_sub.groupby("date")[net_col].sum().sort_index().tail(n)

    f_net = daily_net(foreign)
    t_net = daily_net(trust)

    if f_net.empty and t_net.empty:
        st.warning(f"{radar_sid} 近期無外資或投信資料，請更新籌碼資料。")
        pass  # removed st.stop()

    all_dates = sorted(set(f_net.index.tolist() + t_net.index.tolist()))
    f_vals    = [float(f_net.get(d, 0)) for d in all_dates]
    t_vals    = [float(t_net.get(d, 0)) for d in all_dates]
    date_strs = [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d) for d in all_dates]

    # 融資餘額
    margin_col = next((c for c in df_c.columns if "MarginPurchaseTodayBalance" in c), None)
    margin_vals, margin_dates = [], []
    if margin_col:
        margin_df = df_c[df_c["source"].astype(str) == "margin"] if "source" in df_c.columns else pd.DataFrame()
        if not margin_df.empty:
            mg = margin_df.groupby("date")[margin_col].last().sort_index().tail(20)
            margin_vals  = pd.to_numeric(mg, errors="coerce").fillna(0).tolist()
            margin_dates = [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d) for d in mg.index]

    # ── AI 短評（近5日）
    recent_f = f_vals[-5:] if len(f_vals) >= 5 else f_vals
    recent_t = t_vals[-5:] if len(t_vals) >= 5 else t_vals
    combined  = [f + t for f, t in zip(recent_f, recent_t)]
    buy_days  = sum(1 for v in combined if v > 0)
    total_net = sum(combined)

    if buy_days >= 3 and total_net > 0:
        st.success(
            f"🔥 【大資金湧入】法人近5日買超 {buy_days} 天，"
            f"累積買超 {total_net/1000:.0f} 張，"
            f"暗示其背後產業板塊具備波段動能，可作為選股方向！"
        )
    elif buy_days <= 2 and total_net < 0:
        st.warning(
            f"⚠️ 【主力提款】法人近5日賣超居多，"
            f"累積賣超 {abs(total_net)/1000:.0f} 張，"
            f"請留意該 ETF 關聯產業之修正風險。"
        )
    else:
        st.info(f"📊 近5日法人買超 {buy_days} 天，多空訊號混沌，持續觀察中。")

    # ── 雙軸圖：外資/投信買賣超（主軸）+ 融資餘額（副軸）
    fig_radar = make_subplots(specs=[[{"secondary_y": True}]])

    fig_radar.add_trace(go.Bar(
        x=date_strs, y=f_vals, name="外資買賣超",
        marker_color=["#ff5252" if v >= 0 else "#00e676" for v in f_vals],
        opacity=0.85,
    ), secondary_y=False)

    fig_radar.add_trace(go.Bar(
        x=date_strs, y=t_vals, name="投信買賣超",
        marker_color=["#ff9800" if v >= 0 else "#69f0ae" for v in t_vals],
        opacity=0.85,
    ), secondary_y=False)

    if margin_vals:
        fig_radar.add_trace(go.Scatter(
            x=margin_dates, y=margin_vals,
            name="融資餘額", mode="lines+markers",
            line=dict(color="#e040fb", width=2),
            marker=dict(size=5),
        ), secondary_y=True)

    fig_radar.update_layout(
        **base_layout(f"{radar_sid} 近20日主力資金雷達", 420),
        barmode="relative",
    )
    fig_radar.update_yaxes(title_text="買賣超（股）", secondary_y=False, gridcolor="#1e3a5f")
    fig_radar.update_yaxes(title_text="融資餘額（股）", secondary_y=True, showgrid=False)
    st.plotly_chart(fig_radar, width='stretch')


    # ══════════════════════════════════════════════
    # 🌐 產業板塊資金熱力圖
    # ══════════════════════════════════════════════
    st.markdown("---")
    st.markdown("<div class='sec-title'>🌐 產業板塊資金熱力圖 · Sector Fund Flow</div>",
                unsafe_allow_html=True)

    st.info(
        "💡 **量化戰略提示**：結合 Tab1 的選股掃描儀，優先在「法人資金淨流入排行榜前三名」"
        "的產業板塊中，尋找突破均線的強勢個股，勝率最高。"
    )

    @st.cache_data(ttl=3600, show_spinner="計算產業板塊資金流向...")
    def build_sector_flow() -> pd.DataFrame:
        # 讀取籌碼
        df_c, ok_c = load_csv("chips_data.csv")
        if not ok_c or df_c.empty:
            return pd.DataFrame()

        # 讀取股票資訊（含產業別）
        df_si, ok_si = load_csv("stock_info.csv")
        if not ok_si or df_si.empty:
            return pd.DataFrame()

        df_si["stock_id"] = df_si["stock_id"].astype(str).str.strip()
        df_c["stock_id"]  = df_c["stock_id"].astype(str).str.strip()

        # 取得產業別（只取有名稱的）
        if "industry_category" not in df_si.columns:
            return pd.DataFrame()

        # 優先取有中文名稱的 stock_info
        df_si_clean = df_si[df_si["stock_name"] != df_si["stock_id"]].copy()
        df_si_clean = df_si_clean[["stock_id","industry_category"]].drop_duplicates("stock_id")
        df_si_clean = df_si_clean[df_si_clean["industry_category"].notna()]

        # 整理籌碼資料
        if "date" not in df_c.columns or "name" not in df_c.columns or "net" not in df_c.columns:
            return pd.DataFrame()

        df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
        df_c["net"]  = pd.to_numeric(df_c["net"], errors="coerce").fillna(0)

        latest = df_c["date"].max()
        df_latest = df_c[df_c["date"] == latest].copy()

        # 外資、投信
        foreign = df_latest[df_latest["name"].astype(str).str.contains("Foreign_Investor", na=False)]
        trust   = df_latest[df_latest["name"].astype(str).str.contains("Investment_Trust", na=False)]

        f_net = foreign.groupby("stock_id")["net"].sum().reset_index().rename(columns={"net":"外資淨買（股）"})
        t_net = trust.groupby("stock_id")["net"].sum().reset_index().rename(columns={"net":"投信淨買（股）"})

        # Merge
        df_merge = df_si_clean.merge(f_net, on="stock_id", how="left")
        df_merge = df_merge.merge(t_net, on="stock_id", how="left")
        df_merge["外資淨買（股）"] = df_merge["外資淨買（股）"].fillna(0)
        df_merge["投信淨買（股）"] = df_merge["投信淨買（股）"].fillna(0)

        # 換算億元（1股≈1000股/張，此為股數）
        df_merge["外資淨買（億）"] = (df_merge["外資淨買（股）"] / 1e8).round(2)
        df_merge["投信淨買（億）"] = (df_merge["投信淨買（股）"] / 1e8).round(2)

        # Groupby 產業
        sector = df_merge.groupby("industry_category").agg(
            外資淨買=("外資淨買（億）", "sum"),
            投信淨買=("投信淨買（億）", "sum"),
            檔數=("stock_id", "count")
        ).reset_index()
        sector = sector.rename(columns={"industry_category": "產業別"})
        sector["外資淨買"] = sector["外資淨買"].round(2)
        sector["投信淨買"] = sector["投信淨買"].round(2)
        return sector.sort_values("外資淨買", ascending=False).reset_index(drop=True)

    df_sector = build_sector_flow()

    if df_sector.empty:
        st.warning("產業板塊資料載入失敗，請確認 chips_data.csv 與 stock_info.csv 是否存在。")
    else:
        # 選擇法人類型
        flow_type = st.radio(
            "選擇資金流向",
            ["🏦 外資板塊資金流", "📊 投信板塊資金流"],
            horizontal=True, key="sector_flow_type"
        )
        col_name = "外資淨買" if "外資" in flow_type else "投信淨買"
        label    = "外資" if "外資" in flow_type else "投信"

        # 排序
        df_sorted = df_sector.sort_values(col_name, ascending=False).reset_index(drop=True)

        # 取前15買超 + 前10賣超
        top_buy  = df_sorted.head(15)
        top_sell = df_sorted[df_sorted[col_name] < 0].tail(10)
        df_plot  = pd.concat([top_buy, top_sell]).drop_duplicates("產業別")
        df_plot  = df_plot.sort_values(col_name, ascending=True)  # 水平長條圖由小到大

        colors = ["#ff5252" if v >= 0 else "#00e676" for v in df_plot[col_name]]

        fig_sector = go.Figure(go.Bar(
            x=df_plot[col_name],
            y=df_plot["產業別"],
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.2f}億" for v in df_plot[col_name]],
            textposition="outside",
            hovertemplate="%{y}<br>" + label + "淨買：%{x:.2f}億<extra></extra>",
        ))
        layout_s = base_layout(f"{label}板塊資金流向（最新交易日，單位：億元）", 580)
        layout_s["margin"] = dict(l=180, r=80, t=44, b=34)
        layout_s["xaxis_title"] = f"{label}淨買超（億元）"
        layout_s["yaxis_title"] = ""
        fig_sector.update_layout(**layout_s)
        fig_sector.update_xaxes(gridcolor="#1e3a5f")
        st.plotly_chart(fig_sector, width='stretch')

        # 前三名提示
        top3 = df_sorted.head(3)["產業別"].tolist()
        st.success(
            f"🏆 **{label}資金淨流入前三大板塊：** "
            + "　".join(f"**{i+1}. {s}**" for i, s in enumerate(top3))
            + "　→ 建議優先在這些板塊中執行 Tab1 選股掃描！"
        )

        # 明細表
        with st.expander("📋 完整產業板塊明細", expanded=False):
            show_df = df_sector[["產業別", "外資淨買", "投信淨買", "檔數"]].copy()
            show_df.columns = ["產業別", "外資淨買（億）", "投信淨買（億）", "涵蓋檔數"]
            st.markdown(df_to_html(show_df, height=400), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
with tab8:
    st.header("🧪 策略回測實驗室")
    st.info("選擇股票與策略維度，驗證財報/技術/籌碼因子的有效性。")

    bt_c1, bt_c2, bt_c3 = st.columns([2, 2, 3])
    with bt_c1:
        bt_sid = st.text_input("股票代號", value="2330", key="bt_sid")
    with bt_c2:
        bt_capital = st.number_input("初始資金（元）", value=1000000, step=50000,
                                      min_value=10000, key="bt_capital")
    with bt_c3:
        bt_strategy = st.radio(
            "策略維度",
            ["1️⃣ 純財報基本面", "2️⃣ 技術面＋籌碼面", "3️⃣ 財報＋技術＋籌碼（全方位）"],
            horizontal=True, key="bt_strategy"
        )

    bt_sl_c1, bt_sl_c2 = st.columns([2, 6])
    with bt_sl_c1:
        bt_stop_loss = st.number_input(
            "🛡️ 單筆強制停損 (%)",
            value=10.0, step=1.0, min_value=1.0, max_value=50.0,
            key="bt_stop_loss"
        )

    st.markdown("---")

    if st.button("🚀 開始回測", type="primary", key="bt_run"):
        sid_bt = bt_sid.strip()
        if not sid_bt:
            st.warning("請輸入股票代號")
        else:
            try:
                with st.spinner(f"載入 {sid_bt} 資料中..."):
                    df_k, ok_k = load_price_csv(sid_bt)

                if not ok_k or df_k.empty or len(df_k) < 30:
                    st.error(f"{sid_bt} 無足夠 K 線資料（需 30 日以上）")
                else:
                    # ── K線整理（index 已是 date）
                    df_k = df_k.copy()
                    df_k.index = pd.to_datetime(df_k.index, errors="coerce")
                    df_k = df_k[~df_k.index.isna()].sort_index()
                    df_k["Close"]  = pd.to_numeric(df_k["Close"],  errors="coerce")
                    df_k["Volume"] = pd.to_numeric(
                        df_k["Volume"] if "Volume" in df_k.columns else 0,
                        errors="coerce").fillna(0)
                    df_k["MA20"]  = df_k["Close"].rolling(20).mean()
                    df_k["VMA5"]  = df_k["Volume"].rolling(5).mean()
                    df_k = df_k.dropna(subset=["Close", "MA20"])

                    # ── 財報（EPS）
                    df_fin_bt, ok_fin_bt = get_financials(sid_bt)
                    eps_series = pd.Series(dtype=float)
                    if ok_fin_bt and not df_fin_bt.empty and "type" in df_fin_bt.columns:
                        df_fin_bt["date"]  = pd.to_datetime(df_fin_bt["date"], errors="coerce")
                        df_fin_bt["value"] = pd.to_numeric(df_fin_bt["value"], errors="coerce")
                        eps_q = df_fin_bt[df_fin_bt["type"] == "EPS"].dropna(subset=["date"])
                        if not eps_q.empty:
                            eps_series = eps_q.set_index("date")["value"].sort_index()

                    # ── 籌碼（外資+投信+融資）
                    df_c_bt, ok_c_bt = get_chips(sid_bt)
                    foreign_series = pd.Series(0.0, index=df_k.index, name="foreign")
                    trust_series   = pd.Series(0.0, index=df_k.index, name="trust")
                    margin_series  = pd.Series(0.0, index=df_k.index, name="margin_chg")

                    if ok_c_bt and not df_c_bt.empty:
                        df_c_bt["date"] = pd.to_datetime(df_c_bt.get("date"), errors="coerce")
                        df_c_bt["net"]  = pd.to_numeric(df_c_bt.get("net", 0), errors="coerce").fillna(0)
                        if "name" in df_c_bt.columns:
                            f_df = df_c_bt[df_c_bt["name"].astype(str).str.contains("Foreign_Investor", na=False)]
                            t_df = df_c_bt[df_c_bt["name"].astype(str).str.contains("Investment_Trust", na=False)]
                            if not f_df.empty:
                                f_grp = f_df.groupby("date")["net"].sum()
                                foreign_series = f_grp.reindex(df_k.index).fillna(0)
                            if not t_df.empty:
                                t_grp = t_df.groupby("date")["net"].sum()
                                trust_series = t_grp.reindex(df_k.index).fillna(0)
                        # 融資變化
                        mg_col = next((c for c in df_c_bt.columns if "MarginPurchaseTodayBalance" in c), None)
                        if mg_col:
                            mg_df = df_c_bt.dropna(subset=["date"]).groupby("date")[mg_col].last().sort_index()
                            mg_num = pd.to_numeric(mg_df, errors="coerce").ffill()
                            mg_chg = mg_num.diff().reindex(df_k.index).fillna(0)
                            margin_series = mg_chg

                    # ── 合併到 df_bt
                    df_bt = df_k.copy()
                    if not eps_series.empty:
                        combined_idx = df_bt.index.union(eps_series.index)
                        df_bt["eps_q"] = eps_series.reindex(combined_idx).ffill().reindex(df_bt.index)
                    else:
                        df_bt["eps_q"] = np.nan

                    df_bt["foreign"]    = foreign_series.values if len(foreign_series)==len(df_bt) else 0
                    df_bt["trust"]      = trust_series.values   if len(trust_series)==len(df_bt)   else 0
                    df_bt["margin_chg"] = margin_series.values  if len(margin_series)==len(df_bt)  else 0
                    df_bt = df_bt.fillna({"foreign":0,"trust":0,"margin_chg":0})
                    # Reset index 確保 iloc 正確對應
                    df_bt = df_bt.reset_index()
                    if "date" not in df_bt.columns and "index" in df_bt.columns:
                        df_bt = df_bt.rename(columns={"index": "date"})

                    # ── 訊號條件
                    above_ma20   = df_bt["Close"] > df_bt["MA20"]
                    below_ma20   = df_bt["Close"] < df_bt["MA20"]
                    eps_positive = df_bt["eps_q"].fillna(0) > 0

                    # V6 量縮放寬：今日或昨日量 < VMA5 * 0.7 即算籌碼沉澱
                    _vma5_bt = df_bt["VMA5"].fillna(df_bt["Volume"].rolling(5).mean())
                    vol_shrink = (
                        (df_bt["Volume"] < _vma5_bt * 0.7) |
                        (df_bt["Volume"].shift(1) < _vma5_bt * 0.7)
                    )

                    # 策略2：技籌條件（放寬：任何大資金或放量跡象）
                    any_buy_chip = (
                        (df_bt["foreign"]    > 0) |
                        (df_bt["trust"]      > 0) |
                        (df_bt["margin_chg"] > 0) |
                        (df_bt["Volume"]     > _vma5_bt)
                    )

                    # 有籌碼資料才用，否則退化為純技術
                    has_chip = (df_bt["foreign"] != 0).any() or (df_bt["trust"] != 0).any()

                    if "1️⃣" in bt_strategy:
                        raw_buy  = eps_positive
                        raw_exit = ~eps_positive
                        strat_name = "純財報基本面"
                    elif "2️⃣" in bt_strategy:
                        if has_chip:
                            raw_buy = above_ma20 & any_buy_chip
                        else:
                            # 無籌碼資料：純技術（站上MA20即進場）
                            raw_buy = above_ma20
                        raw_exit   = below_ma20
                        strat_name = "技術面＋籌碼面"
                    else:
                        if has_chip:
                            raw_buy = eps_positive & above_ma20 & any_buy_chip
                        else:
                            raw_buy = eps_positive & above_ma20
                        raw_exit   = below_ma20 | (~eps_positive)
                        strat_name = "財報＋技術＋籌碼"

                    # ── 持倉狀態（買進後抱住 + 硬性停損保護）
                    stop_loss_pct = bt_stop_loss / 100.0
                    position_arr  = np.zeros(len(df_bt), dtype=int)
                    in_pos        = False
                    entry_price   = 0.0
                    for i in range(len(df_bt)):
                        current_price = float(df_bt["Close"].iloc[i])
                        if not in_pos and raw_buy.iloc[i]:
                            in_pos      = True
                            entry_price = current_price
                        elif in_pos:
                            # 第一道防線：硬性停損
                            if entry_price > 0 and (current_price - entry_price) / entry_price <= -stop_loss_pct:
                                in_pos = False
                            # 第二道防線：策略技術出場
                            elif raw_exit.iloc[i]:
                                in_pos = False
                        position_arr[i] = 1 if in_pos else 0

                    df_bt["position"] = position_arr

                    # ── 除錯資訊
                    _buy_count = int(raw_buy.sum())
                    _pos_count = int((position_arr==1).sum())
                    if _buy_count == 0:
                        st.warning(f"⚠️ 策略無任何買進訊號（{strat_name}）。"
                                   f"可能原因：籌碼資料不足或條件過嚴。"
                                   f"嘗試切換策略維度或選擇其他股票。")
                    else:
                        st.caption(f"📊 買進訊號 {_buy_count} 天，持倉 {_pos_count} 天，交易 {trades} 次")

                    # ── 資金曲線
                    capital  = float(bt_capital)
                    cash     = capital
                    shares   = 0
                    equity   = []
                    trades   = 0

                    for i in range(len(df_bt)):
                        price = float(df_bt["Close"].iloc[i])
                        pos   = df_bt["position"].iloc[i]
                        prev  = df_bt["position"].iloc[i-1] if i > 0 else 0

                        if pos == 1 and prev == 0:  # 買進（零股精確模式）
                            max_alloc = capital * 0.10  # 最大單一持倉 10%
                            alloc = min(cash, max_alloc)
                            exact_shares = alloc / price   # 零股精確股數
                            if exact_shares > 0:
                                shares = exact_shares
                                cash  -= shares * price
                                trades += 1
                        elif pos == 0 and prev == 1:  # 賣出
                            cash  += shares * price
                            shares = 0

                        equity.append(cash + shares * price)

                    df_bt["equity_strat"] = equity
                    df_bt["equity_bnh"]   = capital * (df_bt["Close"] / df_bt["Close"].iloc[0])

                    # 若最後還在倉，計算已實現+未實現
                    if shares > 0:
                        last_price = float(df_bt["Close"].iloc[-1])
                        final_strat = cash + shares * last_price
                    else:
                        final_strat = df_bt["equity_strat"].iloc[-1]

                    final_bnh  = df_bt["equity_bnh"].iloc[-1]
                    ret_strat  = (final_strat - capital) / capital * 100
                    ret_bnh    = (final_bnh   - capital) / capital * 100
                    peak       = df_bt["equity_strat"].cummax()
                    dd_strat   = ((df_bt["equity_strat"] - peak) / peak * 100).min()

                    # ── 績效顯示
                    st.markdown(f"### 📊 {sid_bt} 回測結果｜策略：{strat_name}")
                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("策略總報酬", f"{ret_strat:+.1f}%",
                              delta=f"{'優' if ret_strat>ret_bnh else '遜'}於B&H {ret_strat-ret_bnh:+.1f}%")
                    m2.metric("買入持有報酬", f"{ret_bnh:+.1f}%")
                    m3.metric("策略最大回撤", f"{dd_strat:.1f}%")
                    m4.metric("交易次數", f"{trades} 次")
                    m5.metric("回測天數", f"{len(df_bt)} 日")

                    # ── 資金曲線圖
                    st.markdown("### 📈 資金曲線對比")
                    fig_bt = go.Figure()
                    fig_bt.add_trace(go.Scatter(
                        x=df_bt.index, y=df_bt["equity_bnh"],
                        name="📦 Buy & Hold 基準",
                        mode="lines", line=dict(color="#546e7a", width=1.5, dash="dot"),
                    ))
                    fig_bt.add_trace(go.Scatter(
                        x=df_bt.index, y=df_bt["equity_strat"],
                        name=f"🎯 {strat_name}",
                        mode="lines", line=dict(color="#00d4ff", width=2),
                        fill="tonexty", fillcolor="rgba(0,212,255,0.05)"
                    ))
                    # 標記買賣點
                    buy_pts  = df_bt[(df_bt["position"]==1) & (df_bt["position"].shift(1).fillna(0)==0)]
                    sell_pts = df_bt[(df_bt["position"]==0) & (df_bt["position"].shift(1).fillna(0)==1)]
                    if not buy_pts.empty:
                        fig_bt.add_trace(go.Scatter(
                            x=buy_pts.index, y=buy_pts["equity_strat"],
                            mode="markers", name="買入",
                            marker=dict(color="#ff5252", size=8, symbol="triangle-up")
                        ))
                    if not sell_pts.empty:
                        fig_bt.add_trace(go.Scatter(
                            x=sell_pts.index, y=sell_pts["equity_strat"],
                            mode="markers", name="賣出",
                            marker=dict(color="#00e676", size=8, symbol="triangle-down")
                        ))
                    fig_bt.update_layout(**base_layout(f"{sid_bt} 策略回測資金曲線", 480))
                    fig_bt.update_yaxes(gridcolor="#1e3a5f")
                    st.plotly_chart(fig_bt, width='stretch')

                    # ── 持倉區間說明
                    st.caption(f"▲紅三角=買入  ▼綠三角=賣出  共 {trades} 次進場")

            except Exception as _e:
                st.error(f"回測發生錯誤：{_e}")
                import traceback as _tb
                st.code(_tb.format_exc())
