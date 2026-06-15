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


# ▌ 個股「利多不漲」排毒器：新聞熱度 × K線結構 × 法人籌碼交叉比對
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False)
def get_news_heat_score(stock_name: str):
    """
    爬取鉅亨網新聞搜尋頁，以多頭關鍵字出現次數做為「新聞熱度分數」。
    這是簡化版的文字探勘（關鍵字計數），用於捕捉「市場炒作熱度」的
    相對高低，並非嚴謹的情緒分析模型。

    回傳：熱度分數（整數），爬取失敗時回傳 0
    """
    _keywords = ["AI", "大漲", "目標價", "上修", "買超", "利多", "強勢", "噴出", "法人", "推升", "創高", "熱潮"]
    score = 0
    try:
        import requests as _req
        url = f"https://www.cnyes.com/search/news?q={stock_name}"
        r = _req.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=8)
        if r.status_code == 200:
            text = r.text
            for kw in _keywords:
                score += text.count(kw)
    except:
        pass
    return score


def scan_bullish_no_rise_trap(stock_id: str, stock_name: str,
                               news_score_threshold: int = 15,
                               shadow_pct_threshold: float = 1.5):
    """
    『利多不漲：大戶高位出貨陷阱』交叉比對邏輯。

    三項條件同時成立才會觸發紅燈：
      1. 新聞熱度分數 > news_score_threshold（市場處於高位炒作狀態）
      2. 當日K線收黑，或上影線比例 ≥ shadow_pct_threshold%（價格無法續攻）
      3. 當日外資（三大法人之一）為淨賣超（籌碼面實質出貨）

    回傳 dict：
      {"trigger": bool, "news_score": int, "bad_candle": bool,
       "foreign_net": float, "shadow_pct": float}
    任一資料缺失時 trigger 會是 False，並盡量回填可取得的欄位供前端顯示。
    """
    result = {"trigger": False, "news_score": 0, "bad_candle": False,
              "foreign_net": 0.0, "shadow_pct": 0.0}
    try:
        # 1) 新聞熱度
        result["news_score"] = get_news_heat_score(stock_name)

        # 2) K線結構：收黑 或 長上影線
        df_p, ok_p = load_price_csv(stock_id)
        if ok_p and not df_p.empty:
            lt = df_p.iloc[-1]
            _open, _close, _high = float(lt["Open"]), float(lt["Close"]), float(lt["High"])
            is_red_candle = _close < _open
            shadow_pct = (_high - max(_close, _open)) / _close * 100 if _close > 0 else 0
            result["shadow_pct"] = round(shadow_pct, 2)
            result["bad_candle"] = bool(is_red_candle or shadow_pct >= shadow_pct_threshold)

        # 3) 法人籌碼：外資當日淨賣超
        df_c, ok_c = get_chips(stock_id)
        if ok_c and not df_c.empty and "name" in df_c.columns:
            foreign = df_c[df_c["name"].astype(str).str.contains("Foreign_Investor", na=False)]
            if not foreign.empty:
                _last = foreign.sort_values("date").iloc[-1]
                result["foreign_net"] = float(pd.to_numeric(_last.get("net", 0), errors="coerce") or 0)

        # 三項條件同時成立 → 觸發
        result["trigger"] = (
            result["news_score"] > news_score_threshold
            and result["bad_candle"]
            and result["foreign_net"] < 0
        )
    except:
        pass
    return result


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
    回傳小台散戶淨留倉口數
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

/* ── Sidebar */
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
    # 2330 daily debug
    try:
        _df_c2, _ok2 = get_chips('2330')
        if _ok2 and not _df_c2.empty:
            _trust2 = _df_c2[_df_c2['name'].astype(str).str.contains('Investment_Trust', na=False)].copy()
            _trust2['net'] = pd.to_numeric(_trust2['net'], errors='coerce').fillna(0)
            _daily2 = _trust2.groupby('date')['net'].sum().reset_index().sort_values('date')
            _last3 = [(str(r['date'])[:10], round(r['net'],0)) for _, r in _daily2.tail(3).iterrows()]
            upd += f" | 2330daily:{_last3}"
    except Exception as _e:
        upd += f" | daily_err:{_e}"

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
        st.caption(f"🔧 {st.session_state.get("wl_debug", "載入中...")}")

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
    "🔍 選股掃描儀",
    "📡 大數據雷達",
    "🚨 持股監控",
    "🏹 戰略儲備庫",
    "📡 大盤預警",
    "📝 每日作戰總部",
    "💰 ETF存股現金流",
    "🧪 策略回測實驗室",
])

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
    st.markdown("<div class='sec-title'>🔍 選股掃描儀 · 三道階層篩選</div>",
                unsafe_allow_html=True)

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

    wl = st.session_state.watchlist

    if not wl:
        st.markdown("""
        <div style='background:#0f2027;border:2px dashed #1e3a5f;border-radius:12px;
             padding:50px;text-align:center;'>
            <div style='font-size:2rem;margin-bottom:10px;'>📋</div>
            <div style='color:#e8f4fd;font-size:.92rem;font-weight:600;'>監控清單為空</div>
            <div style='color:#7fb3d3;font-size:.8rem;margin-top:8px;'>
                請至「選股掃描儀」點擊「加入監控」，<br>
                或在左側 Sidebar「➕ 加入監控清單」輸入代號
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # 選擇監控標的 - 分手動/掃描兩組
        wl_manual = st.session_state.watchlist
        wl_scan   = st.session_state.watchlist_scan

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
            src_options += ["── 手動加入 ──"] + manual_items
        if scan_items:
            src_options += ["── 掃描結果 ──"] + scan_items

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
    st.markdown("---")
    st.markdown("<div class='sec-title'>🔄 既有資產優化與換股推薦面板</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>系統自動掃描監控名單，識別弱勢與強勢標的。"
        "推薦結果依當前選定個股動態生成，切換標的時自動清除。</div>",
        unsafe_allow_html=True
    )

    # 取得所有監控標的
    all_watch = st.session_state.get("watchlist", []) + st.session_state.get("watchlist_scan", [])
    seen_ids = set()
    watch_dedup = []
    for w in all_watch:
        if w["id"] not in seen_ids:
            seen_ids.add(w["id"])
            watch_dedup.append(w)

    # 當前選定個股（來自上方 selectbox）
    _current_sid = st.session_state.get("last_stock_watch", "")

    if not watch_dedup:
        st.info("監控清單為空，請先加入標的。")
    else:
        weak_list   = []  # 弱勢待汰
        strong_list = []  # 強勢轉進

        for w in watch_dedup:
            sid  = w["id"]
            name = w.get("name", sid)
            df_p, ok_p = load_price_csv(sid)
            if not ok_p or df_p.empty or len(df_p) < 20:
                continue
            df_i = add_indicators(df_p)
            lt = df_i.iloc[-1]

            close   = float(lt["Close"])
            ema5    = float(lt.get("EMA5",   float("nan")))
            bb_mid  = float(lt.get("BB_MID", float("nan")))
            vol     = float(lt.get("Volume", 0))
            vma5    = float(lt.get("VMA5",   float("nan")))

            if any(np.isnan(v) for v in [ema5, bb_mid]):
                continue

            # 弱勢條件：現價 < EMA5 且 現價 < BB_MID 且 EMA5 < BB_MID
            is_weak = (close < ema5) and (close < bb_mid) and (ema5 < bb_mid)

            # 強勢條件：現價 >= EMA5 且 現價 >= BB_MID 且 量 <= VMA5*0.45
            is_strong = (
                (close >= ema5) and
                (close >= bb_mid) and
                (not np.isnan(vma5) and vma5 > 0 and vol <= vma5 * 0.45)
            )

            if is_weak:
                weak_list.append({"id": sid, "name": name, "close": close,
                                  "ema5": ema5, "bb_mid": bb_mid})
            if is_strong:
                strong_list.append({"id": sid, "name": name, "close": close,
                                    "ema5": ema5, "bb_mid": bb_mid, "vol_ratio": vol/vma5})

        # ── 弱勢警示
        if weak_list:
            st.markdown("#### 🔴 弱勢標的（建議汰弱）")
            for w in weak_list:
                st.markdown(
                    f"<div style='background:rgba(255,82,82,0.1);border-left:4px solid #ff5252;"
                    f"border-radius:8px;padding:12px 16px;margin:6px 0;'>"
                    f"<b style='color:#ff5252;'>{w['id']} {w['name']}</b>"
                    f"<span style='color:#ffcdd2;font-size:.85rem;'>"
                    f"　收盤 {w['close']:.1f} ｜ EMA5 {w['ema5']:.1f} ｜ 布林中軌 {w['bb_mid']:.1f}</span><br>"
                    f"<span style='color:#ff8a80;font-size:.88rem;'>"
                    f"⚠️ 建議執行汰弱留強，清倉此部位。</span></div>",
                    unsafe_allow_html=True
                )
        else:
            st.success("✅ 監控名單中目前無弱勢標的，持股結構健康。")

        st.markdown("<br>", unsafe_allow_html=True)

        # ══════════════════════════════════════════════
        # 🎯 戰略儲備庫輪動引擎（資金來源唯一限定儲備庫）
        # ══════════════════════════════════════════════
        st.markdown("#### 🎯 戰略儲備庫轉進訊號")

        reserve_list = st.session_state.get("reserve_list", [])

        if not reserve_list:
            st.caption("⏳ 戰略儲備庫尚無標的，請先在 Tab4 加入精兵。")
        else:
            # 即時掃描儲備庫所有個股的三大買進訊號
            reserve_ready   = []  # 3/3 訊號全中
            reserve_waiting = []  # 尚未成熟

            for rv in reserve_list:
                rv_sid  = rv["id"]
                rv_name = rv.get("name", rv_sid)
                if rv_sid == _current_sid:
                    continue  # 排除當前持股本身

                df_rv2, ok_rv2 = load_price_csv(rv_sid)
                if not ok_rv2 or df_rv2.empty or len(df_rv2) < 10:
                    continue

                df_rv2 = add_indicators(df_rv2)
                lt_rv2  = df_rv2.iloc[-1]
                close_rv2 = float(lt_rv2["Close"])
                ema5_rv2  = float(lt_rv2.get("EMA5",  float("nan")))
                sma20_rv2 = float(lt_rv2.get("MA20",  float("nan")))
                vol_rv2   = float(lt_rv2.get("Volume", 0))
                vma5_rv2  = float(lt_rv2.get("VMA5",  float("nan")))
                open_rv2  = float(lt_rv2.get("Open",  close_rv2))

                if np.isnan(sma20_rv2) or sma20_rv2 <= 0:
                    continue

                bias_rv2 = (close_rv2 - sma20_rv2) / sma20_rv2 * 100

                # 三大條件
                c1 = (not np.isnan(vma5_rv2) and vma5_rv2 > 0 and len(df_rv2) >= 4 and
                      all(float(df_rv2["Volume"].iloc[i]) < vma5_rv2 * 0.5 for i in [-1,-2,-3]))
                c2 = bias_rv2 <= 5
                c3 = close_rv2 > open_rv2 and not np.isnan(ema5_rv2) and close_rv2 > ema5_rv2

                if c1 and c2 and c3:
                    reserve_ready.append({
                        "id": rv_sid, "name": rv_name,
                        "close": close_rv2, "bias": bias_rv2,
                        "ema5": ema5_rv2, "sma20": sma20_rv2,
                    })
                else:
                    reserve_waiting.append({
                        "id": rv_sid, "name": rv_name,
                        "bias": bias_rv2,
                    })

            # 情境A：儲備精兵訊號亮起
            if reserve_ready:
                weak_names = "、".join(f"{w['name']}({w['id']})" for w in weak_list)                              if weak_list else "當前弱勢部位"
                for r in reserve_ready:
                    _msg = (
                        f"🎯 【儲備精兵回頭草推薦】：偵測到弱勢解凍部位。"
                        f"建議轉進儲備庫中的 {r['name']}（{r['id']}）。"
                        f" Fact 支撐：該股已完成量縮沉澱，今日現價 {r['close']:.1f} 元，"
                        f"與月線乖離僅 {r['bias']:.1f}%，"
                        f"符合低乖離安全卡閘，此為最佳資金效率換手點。"
                    )
                    st.info(_msg)

            # 情境B：儲備庫仍在沉澱
            elif reserve_waiting:
                names_str = "、".join(f"{r['name']}" for r in reserve_waiting[:3])
                st.markdown(
                    f"<div style='background:rgba(84,110,122,0.15);border:1px solid #546e7a;"
                    f"border-radius:8px;padding:12px 16px;color:#90a4ae;font-size:.85rem;'>"
                    f"⏳ <b>資金效率提示</b>：目前戰略儲備名單中的精兵（{names_str}等）"
                    f"正乖離率仍高，處於高檔籌碼沉澱期。"
                    f"若此時手動解凍持股，建議先保留現金在手，"
                    f"靜待儲備名單吹響右側反攻號角。</div>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("⏳ 戰略儲備庫所有精兵正在排除中，稍後再確認。")


# ──────────────────────────────────────────────────────────────
# ▌ TAB 4：戰略儲備庫（精兵回頭草雷達）
# ──────────────────────────────────────────────────────────────
with tab4:
    st.markdown("<div class='sec-title'>🏹 戰略儲備庫 · 精兵回頭草雷達</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>存放「曾持有但因短線破位暫時出清」的優質標的。"
        "系統每日自動監控，一旦籌碼沉澱完成即觸發精準獵殺警報。</div>",
        unsafe_allow_html=True
    )

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
                    "added_at": datetime.now().strftime("%Y-%m-%d")
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

    if not st.session_state.reserve_list:
        st.info("🏹 戰略儲備庫尚無標的，請從上方加入或從 Tab3 持股監控移入。")
    else:
        st.markdown(f"### 📡 精兵回頭草雷達｜共 {len(st.session_state.reserve_list)} 檔監控中")

        # 掃描所有儲備標的
        triggered = []
        waiting   = []

        for item in st.session_state.reserve_list:
            sid_rv  = item["id"]
            name_rv = item.get("name", sid_rv)
            note_rv = item.get("note", "")

            df_rv, ok_rv = load_price_csv(sid_rv)
            if not ok_rv or df_rv.empty or len(df_rv) < 10:
                waiting.append((sid_rv, name_rv, note_rv, None, None, "無K線資料"))
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
                triggered.append((sid_rv, name_rv, note_rv, close_rv, bias_rv))
            else:
                conds_met = sum([cond1, cond2, cond3])
                # 計算量比（當日量/VMA5）
                vol_ratio_rv = round(vol_rv / vma5_rv, 2) if not np.isnan(vma5_rv) and vma5_rv > 0 else None
                waiting.append((sid_rv, name_rv, note_rv, close_rv, bias_rv,
                                f"{conds_met}/3 條件成立",
                                cond1, cond2, cond3,
                                vol_ratio_rv, ema5_rv, sma20_rv, kline_date_rv))

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
                        _sop = f"⚠️ <b>【常規警戒・資金減半】</b>：個股技術面 <b>{_sc}分</b>，但大盤進入黃燈警戒。<b>建倉資金強制砍半（僅能建立 1/6 底倉）</b>，嚴格控管風險！"
                    elif _is_short_squeeze:
                        _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.12);border-left:5px solid #ff9900;"
                        _sop = f"🚀 <b>【黃金軋空・全力進擊】</b>：個股高達 <b>{_sc}分</b>，且散戶放空 {abs(_mtx_retail):,} 口！史詩級軋空點，建議尾盤無懸念建立 1/3~1/2 波段先鋒倉！"
                    elif _is_short_squeeze:
                        _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.12);border-left:5px solid #ff9900;"
                        _sop = f"🔥 <b>【黃金軋空・全力進擊】</b>：個股高達 <b>{_sc}分</b>，台美散戶同步嚇破膽放空！市場具備強烈軋空基因，允許利用小股期進行非對稱動能加壓閃擊！"
                    else:
                        _rc = "#ff9900"; _bg = "background:rgba(255,153,0,0.08);border-left:5px solid #ff9900;"
                        _sop = f"👑 <b>【黃金特赦區 SOP】</b>：大盤環境安全，個股拉回止跌！建議 <b>13:25 尾盤建立 1/3 常規基本底倉</b>，停損設 EMA5 下。"
                elif _sc >= 5:
                    if _is_danger:
                        _rc = "#8892b0"; _bg = "background:rgba(255,255,255,0.01);border-left:5px solid #44475a;"
                        _sop = f"⏳ <b>【高度警戒・取消閃擊】</b>：個股蓄勢（{_sc}分），但大台空單 {abs(_tx_net):,}+散戶多單 {_mtx_retail:,} 口，取消進場，繼續觀望。"
                    elif _is_yellow:
                        _rc = "#fbbf24"; _bg = "background:rgba(251,191,36,0.05);border-left:5px solid #fbbf24;"
                        _sop = f"⚠️ <b>【黃燈警戒・延後閃擊】</b>：個股蓄勢（{_sc}分），但大盤黃燈，等黃燈解除後再執行閃擊計畫。"
                    elif _is_short_squeeze:
                        _rc = "#ffee55"; _bg = "background:rgba(255,238,85,0.08);border-left:5px solid #ffee55;"
                        _sop = f"🎯 <b>【蓄勢+軋空加持】</b>：個股蓄勢（{_sc}分）且散戶放空 {abs(_mtx_retail):,} 口，<b>早盤爆量即刻閃擊！</b>"
                    else:
                        _rc = "#ffee55"; _bg = "background:rgba(255,238,85,0.06);border-left:5px solid #ffee55;"
                        _sop = "🎯 <b>【動能蓄勢區 SOP】</b>：已到技術防守地基，建議<b>鎖定為週一首選儲備</b>，早盤爆量突破 EMA5 直接閃擊。"
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
            for sid_rv, name_rv, note_rv, close_rv, bias_rv in triggered:
                _alert_msg = (
                    f"🎯 精兵回頭草警報：戰略儲備股 {name_rv}（{sid_rv}）已在冷宮完成沉澱！"
                    f" 今日現價 {close_rv:.1f} 元，與月線乖離率僅 +{bias_rv:.1f}%（符合<5%限制），"
                    f"且成交量極致萎縮後首度帶量收復5日線。"
                    f" 基本面基因優良，短線防禦安全邊際極高，准許執行手動第二波精準獵殺！"
                )
                st.info(_alert_msg)

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
                f"<div style='font-size:.83rem;margin-bottom:8px;padding:6px 12px;"
                f"border-radius:4px;{_bg}color:{_col};'>"
                f"{_ico} <b>{sid_rv} {name_rv}</b>｜"
                f"{_close_str}{_bias_str}{_kd_str}"
                f"{_c1_txt} {_c2_txt} {_c3_txt}"
                f"{'｜'+_vr_txt if _vr_txt else ''}{'｜'+_e5_txt if _e5_txt else ''}"
                f"{_note_txt}"
                f"</div>"
            )
            _wait_rm_btns.append(sid_rv)

        if _wait_rows:
            # 每行：色卡 + 刪除按鈕同一列
            for _wi, (_wrow, _wsid) in enumerate(zip(_wait_rows, _wait_rm_btns)):
                _wc_main, _wc_btn = st.columns([20, 1])
                with _wc_main:
                    st.markdown(_wrow, unsafe_allow_html=True)
                with _wc_btn:
                    st.markdown("<div style='margin-top:4px;'></div>", unsafe_allow_html=True)
                    if st.button("✕", key=f"rsv_rm_{_wsid}", use_container_width=True):
                        rm_rsv = _wsid

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
    st.markdown("<div class='sec-title'>📡 大盤預警 · 期貨引擎 ＋ 蒙格行為學 ＋ AI診斷</div>",
                unsafe_allow_html=True)

    # ── V6 三軌風控儀表板
    _risk_status, _risk_info = get_system_risk_status()
    _vix        = get_vix()
    _macro_ind  = get_macro_indicators()

    # ══════════════════════════════════════════════════════════
    # 第一行：全球籌碼與核彈排毒雷達（5 欄）
    # ══════════════════════════════════════════════════════════
    def _metric_html(label, value, status, hint):
        """用 HTML 自訂 metric，確保字體大小舒適且顏色醒目"""
        color = {"🔴":"#ff4444","🟡":"#fbbf24","🟢":"#00cc66","⚪":"#8892b0"}.get(status[0], "#8892b0")
        return (
            f"<div style='background:rgba(255,255,255,0.03);border:1px solid #1e3a5f;"
            f"border-radius:8px;padding:10px 8px;text-align:center;border-top:3px solid {color};'>"
            f"<div style='color:#7fb3d3;font-size:.72rem;letter-spacing:.5px;margin-bottom:4px;'>{label}</div>"
            f"<div style='color:#e8f4fd;font-size:1.25rem;font-weight:700;line-height:1.2;'>{value}</div>"
            f"<div style='color:{color};font-size:.7rem;margin-top:4px;'>{status} {hint}</div>"
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
        _metric_html("大台外資", f"{_tx:+,}口", _tx_s, f"{_tx_h}｜{_tx_delta_line}"),
        unsafe_allow_html=True
    )

    # ── 欄2：小台散戶多空比 %
    _retail     = _risk_info["mtx_retail"]
    _mtx_total  = abs(_retail) + 10000  # 近似全市場
    _retail_pct = round(_retail / _mtx_total * 100, 1) if _mtx_total else 0
    if _retail_pct >= 15:   _rt_s, _rt_h = "🔴", "散戶抄底踩踏"
    elif _retail_pct <= -20: _rt_s, _rt_h = "🟢", "籌碼乾淨"
    else:                    _rt_s, _rt_h = "⚪", "中性"
    _r1[1].markdown(_metric_html("小台散戶", f"{_retail:+,}口", _rt_s, _rt_h), unsafe_allow_html=True)

    # ── 欄3：CBOE P/C
    _pc = _risk_info["pc_ratio"]
    if _pc < 0.8:    _pc_s, _pc_h = "🔴", "極度貪婪"
    elif _pc > 1.2:  _pc_s, _pc_h = "🟢", "恐慌買點"
    else:            _pc_s, _pc_h = "⚪", "正常"
    _r1[2].markdown(_metric_html("CBOE P/C", f"{_pc:.2f}", _pc_s, _pc_h), unsafe_allow_html=True)

    # ── 欄4：VIX
    if _vix is not None:
        if _vix > 25:    _vix_s, _vix_h = "🔴", "市場去槓桿"
        elif _vix < 15:  _vix_s, _vix_h = "🟢", "風平浪靜"
        else:            _vix_s, _vix_h = "⚪", "警戒中"
        _r1[3].markdown(_metric_html("VIX 恐慌", f"{_vix:.1f}", _vix_s, _vix_h), unsafe_allow_html=True)
    else:
        _r1[3].markdown(_metric_html("VIX 恐慌", "—", "⚪", "載入中"), unsafe_allow_html=True)

    # ── 欄5：最近核彈
    _days = _risk_info["days"]
    _evt  = _risk_info["event"][:12] if _risk_info.get("event") else "—"
    if _days <= 3:    _nk_s, _nk_h = "🟡", "特種兵離線"
    elif _days <= 7:  _nk_s, _nk_h = "🟡", "開獎警戒"
    else:             _nk_s, _nk_h = "⚪", "安全"
    _r1[4].markdown(_metric_html("最近核彈", f"{_days}天", _nk_s, f"{_evt}"), unsafe_allow_html=True)

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
        _r1[5].markdown(_metric_html("全場均線結構", f"{_breadth:.1f}%站季線", _bd_s, _bd_h), unsafe_allow_html=True)
    else:
        _r1[5].markdown(_metric_html("全場均線結構", "—", "⚪", "計算中"), unsafe_allow_html=True)

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
    _r2[0].markdown(_metric_html(_cpi_label, _infl_val, _cpi_s, _cpi_h), unsafe_allow_html=True)

    # ── 欄2：油價（布倫特 / 杜拜）
    _br = _macro_ind.get("brent")
    _du = _macro_ind.get("dubai")
    _oil_val = f"{_br:.1f} / {_du:.1f}" if (_br and _du) else ("—")
    _oil_warn = (_br and _br > 88) or (_du and _du > 85)
    _oil_ok   = (_br and 70 <= _br <= 80) and (_du and 70 <= _du <= 80)
    if _oil_warn:    _oil_s, _oil_h = "🔴", "通膨前導警戒"
    elif _oil_ok:    _oil_s, _oil_h = "🟢", "區間穩定"
    else:            _oil_s, _oil_h = "⚪", "觀察中"
    _r2[1].markdown(_metric_html("油價 布/杜", _oil_val, _oil_s, _oil_h), unsafe_allow_html=True)

    # ── 欄3：美債 10 年期殖利率
    _tnx = _macro_ind.get("tnx")
    if _tnx is not None:
        if _tnx > 4.4:   _tnx_s, _tnx_h = "🔴", "估值壓制"
        elif _tnx < 4.0: _tnx_s, _tnx_h = "🟢", "資金行情解封"
        else:             _tnx_s, _tnx_h = "⚪", "觀察中"
        _r2[2].markdown(_metric_html("美債10年", f"{_tnx:.2f}%", _tnx_s, _tnx_h), unsafe_allow_html=True)
    else:
        _r2[2].markdown(_metric_html("美債10年", "—", "⚪", "載入中"), unsafe_allow_html=True)

    # ── 欄4：大盤月乖離
    _bias = _macro_ind.get("bias")
    if _bias is not None:
        if _bias > 4:      _bias_s, _bias_h = "🔴", "極端超漲"
        elif _bias < -4:   _bias_s, _bias_h = "🟢", "黃金打底區"
        else:              _bias_s, _bias_h = "⚪", "正常範圍"
        _r2[3].markdown(_metric_html("大盤月乖離", f"{_bias:+.1f}%", _bias_s, _bias_h), unsafe_allow_html=True)
    else:
        _r2[3].markdown(_metric_html("大盤月乖離", "—", "⚪", "計算中"), unsafe_allow_html=True)

    # ── 欄5：航運指數（SCFI / BDI）
    _bdi  = _macro_ind.get("bdi")
    _ship_val = f"— / {_bdi:,}" if _bdi else "— / —"
    if _bdi and _bdi > 2000:   _ship_s, _ship_h = "🔴", "通膨隱憂"
    elif _bdi and _bdi < 1000: _ship_s, _ship_h = "🟢", "資金歸建電子"
    else:                       _ship_s, _ship_h = "⚪", "盤整中"
    _r2[4].markdown(_metric_html("航運 S/BDI", _ship_val, _ship_s, _ship_h), unsafe_allow_html=True)

    # ── 欄6：個股利多不漲排毒器（掃描戰略儲備庫，統計觸發數量）
    #    交叉比對：新聞熱度（炒作高位） × K線結構（收黑/長上影線） × 外資籌碼（淨賣超）
    @st.cache_data(ttl=1800, show_spinner=False)
    def _scan_reserve_trap(reserve_tuple, max_scan=20):
        """
        掃描儲備庫前 max_scan 檔，統計觸發「利多不漲」的數量。
        包一層30分鐘快取：避免每次頁面互動都重新打新聞請求與重算K線，
        是降低記憶體與網路負載的關鍵。
        """
        hits, total = [], 0
        for sid_t, name_t in reserve_tuple[:max_scan]:
            if not sid_t:
                continue
            t = scan_bullish_no_rise_trap(sid_t, name_t)
            total += 1
            if t["trigger"]:
                hits.append((sid_t, name_t))
        return hits, total

    _reserve_for_trap = st.session_state.get("reserve_list", [])
    _reserve_tuple = tuple((r.get("id",""), r.get("name", r.get("id",""))) for r in _reserve_for_trap)
    _trap_hits, _trap_total = _scan_reserve_trap(_reserve_tuple)

    if _trap_total == 0:
        _trap_s, _trap_h = "⚪", "儲備庫無標的"
        _trap_val = "—"
    elif _trap_hits:
        _names = "、".join(n for _, n in _trap_hits[:3])
        _more  = f" 等{len(_trap_hits)}檔" if len(_trap_hits) > 3 else ""
        _trap_s, _trap_h = "🔴", "利多不漲:大戶高位出貨陷阱"
        _trap_val = f"{len(_trap_hits)}/{_trap_total}檔觸發：{_names}{_more}"
    else:
        _trap_s, _trap_h = "⚪", "正常監控中"
        _trap_val = f"0/{_trap_total}檔觸發"

    _r2[5].markdown(
        _metric_html("利多不漲排毒", _trap_val, _trap_s, _trap_h),
        unsafe_allow_html=True
    )

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
    # ▌ 盤後三大健康指標手動診斷面板
    # ══════════════════════════════════════════════════════════════
    st.markdown("<div class='sec-title'>🩺 盤後三大健康指標診斷</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='infobox'>手動輸入今日盤後觀察結果，系統自動研判大盤真實健康狀態。"
        "即使技術警示分數達 8/8，此診斷可識別「假警報」與「真危機」。</div>",
        unsafe_allow_html=True
    )

    diag_c1, diag_c2, diag_c3 = st.columns(3)

    with diag_c1:
        st.markdown("**① 騰落線（ADL）趨勢**")
        adl = st.radio(
            "騰落線狀態",
            ["📈 持續走高或高檔橫盤（健康）", "📉 連續數日下滑（多空背離）"],
            key="diag_adl", label_visibility="collapsed"
        )
        adl_healthy = adl.startswith("📈")

    with diag_c2:
        st.markdown("**② 外資期現貨共振狀態**")
        foreign = st.radio(
            "外資狀態",
            ["🟢 現貨持續買超或僅微幅賣超（安全）", "🔴 現貨連續單日百億以上大賣超（砸盤）"],
            key="diag_foreign", label_visibility="collapsed"
        )
        foreign_healthy = foreign.startswith("🟢")

    with diag_c3:
        st.markdown("**③ 台股多頭支柱技術型態**")
        pillar = st.radio(
            "權值股狀態",
            ["🟢 台積電/聯發科至少一檔守住布林中軌（多頭健康）",
             "🔴 台積電與聯發科雙雙跌破布林中軌（多頭崩解）"],
            key="diag_pillar", label_visibility="collapsed"
        )
        pillar_healthy = pillar.startswith("🟢")

    # ── 自動決策輸出
    healthy_count = sum([adl_healthy, foreign_healthy, pillar_healthy])
    danger_count  = 3 - healthy_count

    st.markdown("<br>", unsafe_allow_html=True)
    if healthy_count >= 2:
        st.success(
            f"✅ **安全診斷：假警報！健康的板塊輪動**　"
            f"（{healthy_count}/3 項健康指標通過）　"
            f"現股部位維持綠燈續抱，無須恐慌。"
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
            "多空各有 1~2 項訊號，建議縮小部位靜觀其變。"
        )

    # ── 診斷明細
    with st.expander("📋 診斷明細", expanded=False):
        items = [
            ("騰落線（ADL）", adl_healthy, adl),
            ("外資期現貨共振", foreign_healthy, foreign),
            ("台股多頭支柱", pillar_healthy, pillar),
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

    # ── KPI 顯示
    st.markdown("<br>", unsafe_allow_html=True)
    pk1, pk2, pk3, pk4 = st.columns(4)
    mcard(pk1, "大台外資淨額", f"{tx_foreign:+,}",
          "down" if tx_foreign < -40000 else "up" if tx_foreign > 0 else "")
    mcard(pk2, "小台三法人合計", f"{mtx_inst_total:+,}",
          "down" if mtx_inst_total < 0 else "up")
    mcard(pk3, "散戶淨多（導火線）", f"{retail_net:+,}",
          "down" if retail_net > 0 else "up")
    # 散戶多空比四色警戒
    if retail_ratio > 20:
        _rr_status = "down"   # 紅
    elif retail_ratio > 10:
        _rr_status = "warn"   # 橘
    elif retail_ratio < -10:
        _rr_status = "up"     # 綠
    else:
        _rr_status = ""       # 白
    mcard(pk4, "散戶多空比", f"{retail_ratio:+.1f}%", _rr_status)

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
            f"{'<div style="color:#ff9800;font-size:.75rem;margin-top:6px;">⚠️ 使用預設題材（API 降級）</div>' if is_fb else ''}"
            f"</div>",
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
