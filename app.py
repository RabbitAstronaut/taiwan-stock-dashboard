import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime
import time
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(
    page_title="台股全週期量化交易儀表板",
    page_icon="📈", layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;700&display=swap');
html,body,[class*="css"]{font-family:'Noto Sans TC',sans-serif;}
.stApp{background:linear-gradient(135deg,#0a0e1a 0%,#0d1321 50%,#0a1628 100%);}
.main-header{background:linear-gradient(90deg,#0f2027,#203a43,#2c5364);border-radius:12px;padding:20px 28px;margin-bottom:16px;border-left:4px solid #00d4ff;box-shadow:0 4px 20px rgba(0,212,255,0.15);}
.main-header h1{color:#e8f4fd;font-size:1.5rem;font-weight:700;margin:0;letter-spacing:1px;}
.main-header p{color:#7fb3d3;margin:5px 0 0 0;font-size:0.8rem;}
.metric-card{background:linear-gradient(135deg,#0f2027,#162535);border:1px solid #1e3a5f;border-radius:10px;padding:13px 14px;text-align:center;transition:transform 0.2s;}
.metric-card:hover{transform:translateY(-2px);}
.metric-label{color:#7fb3d3;font-size:0.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px;}
.metric-value{color:#e8f4fd;font-size:1.25rem;font-weight:700;font-family:'JetBrains Mono',monospace;}
.metric-value.up{color:#00e676;}.metric-value.down{color:#ff5252;}
.signal-green{background:linear-gradient(135deg,#0a3d0a,#0f5c0f);border:1px solid #00e676;border-radius:8px;padding:12px 14px;color:#00e676;font-weight:600;text-align:center;}
.signal-red{background:linear-gradient(135deg,#3d0a0a,#5c0f0f);border:1px solid #ff5252;border-radius:8px;padding:12px 14px;color:#ff5252;font-weight:600;text-align:center;animation:pulse 2s infinite;}
.warning-banner{background:linear-gradient(90deg,#2d1b00,#3d2500);border:1px solid #ffab40;border-left:4px solid #ffab40;border-radius:8px;padding:10px 14px;color:#ffab40;font-weight:600;margin:6px 0;}
.section-title{color:#00d4ff;font-size:0.9rem;font-weight:600;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #1e3a5f;padding-bottom:7px;margin:14px 0 10px 0;}
.group-card{background:linear-gradient(135deg,#0f2027,#111d2e);border:1px solid #1e3a5f;border-radius:10px;padding:12px 14px;margin:4px 0;cursor:pointer;transition:all 0.2s;}
.group-card:hover{border-color:#00d4ff;box-shadow:0 0 10px rgba(0,212,255,0.15);}
.group-card.active{border-color:#00d4ff;background:linear-gradient(135deg,#0d2035,#122840);box-shadow:0 0 12px rgba(0,212,255,0.2);}
.chip-green{display:inline-block;background:rgba(0,230,118,0.1);border:1px solid #00e676;color:#00e676;border-radius:14px;padding:2px 10px;font-size:0.76rem;margin:2px;font-family:'JetBrains Mono',monospace;}
.chip-blue{display:inline-block;background:rgba(0,212,255,0.08);border:1px solid #00d4ff;color:#00d4ff;border-radius:14px;padding:2px 10px;font-size:0.76rem;margin:2px;font-family:'JetBrains Mono',monospace;}
.chip-gray{display:inline-block;background:rgba(84,110,122,0.15);border:1px solid #546e7a;color:#546e7a;border-radius:14px;padding:2px 10px;font-size:0.76rem;margin:2px;font-family:'JetBrains Mono',monospace;}
.infobox{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:0.8rem;color:#7fb3d3;line-height:1.6;}
.sector-badge{display:inline-block;border-radius:6px;padding:3px 10px;font-size:0.72rem;font-weight:600;margin:2px;}
@keyframes pulse{0%{box-shadow:0 0 8px rgba(255,82,82,0.2);}50%{box-shadow:0 0 18px rgba(255,82,82,0.5);}100%{box-shadow:0 0 8px rgba(255,82,82,0.2);}}
.stTabs [data-baseweb="tab-list"]{gap:4px;background:#0d1321;padding:4px;border-radius:10px;}
.stTabs [data-baseweb="tab"]{color:#7fb3d3;background:transparent;border-radius:8px;font-size:0.86rem;padding:7px 16px;}
.stTabs [aria-selected="true"]{color:#00d4ff!important;background:linear-gradient(135deg,#0f2027,#162535)!important;border-bottom:2px solid #00d4ff!important;}
div[data-testid="stExpander"]{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;}
</style>
""", unsafe_allow_html=True)

PLOT_BG="rgba(10,14,26,0)"; PAPER_BG="rgba(10,14,26,0)"
GRID_COLOR="#1e3a5f"; TEXT_COLOR="#7fb3d3"

def base_layout(title="", height=400):
    return dict(
        title=dict(text=title, font=dict(color="#e8f4fd", size=13)),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
        font=dict(color=TEXT_COLOR, family="JetBrains Mono,Noto Sans TC", size=11),
        height=height, margin=dict(l=48,r=18,t=44,b=34),
        xaxis=dict(gridcolor=GRID_COLOR, linecolor=GRID_COLOR, showgrid=True),
        yaxis=dict(gridcolor=GRID_COLOR, linecolor=GRID_COLOR, showgrid=True),
        legend=dict(bgcolor="rgba(10,14,26,0.8)", bordercolor="#1e3a5f", borderwidth=1),
    )

# ══════════════════════════════════════════════
# 類股群組定義（可自由擴充）
# 格式：群組名稱 → [(代號, 名稱, 市場), ...]
# ══════════════════════════════════════════════
SECTOR_GROUPS = {
    "🔬 半導體｜IC設計": {
        "color": "#00d4ff",
        "desc": "晶片設計、IP、EDA 相關",
        "stocks": [
            ("2454","聯發科","上市"),("2303","聯電","上市"),("2379","瑞昱","上市"),
            ("3034","聯詠","上市"),("2344","華邦電","上市"),("2408","南亞科","上市"),
            ("3443","創意","上市"),("3711","日月光投控","上市"),("2449","京元電子","上市"),
            ("6770","力積電","上市"),("2388","威盛","上市"),("3515","華擎","上市"),
            ("5347","世界先進","上市"),("2369","菱生精密","上市"),("4966","譜瑞-KY","上市"),
        ]
    },
    "⚡ 半導體｜晶圓代工＆封測": {
        "color": "#00d4ff",
        "desc": "晶圓代工、封裝測試",
        "stocks": [
            ("2330","台積電","上市"),("2337","旺宏","上市"),("2351","順德","上市"),
            ("3711","日月光投控","上市"),("2325","矽品","上市"),("6274","台燿","上市"),
            ("3014","聯陽","上市"),("2368","金像電","上市"),
        ]
    },
    "💻 AI伺服器｜雲端運算": {
        "color": "#e040fb",
        "desc": "AI晶片、伺服器、資料中心",
        "stocks": [
            ("2382","廣達","上市"),("2356","英業達","上市"),("2353","宏碁","上市"),
            ("2357","華碩","上市"),("6669","緯穎","上市"),("3231","緯創","上市"),
            ("2301","光寶科","上市"),("2324","仁寶","上市"),("3017","奇鋐","上市"),
            ("2399","映泰","上市"),("6770","力積電","上市"),("3583","辛耘","上市"),
            ("6415","矽力-KY","上市"),("3533","嘉澤","上市"),("8044","網家","上市"),
        ]
    },
    "📱 消費電子｜手機零組件": {
        "color": "#ffab40",
        "desc": "手機、穿戴、零組件供應鏈",
        "stocks": [
            ("2317","鴻海","上市"),("2354","鴻準","上市"),("2498","宏達電","上市"),
            ("3008","大立光","上市"),("6239","力成","上市"),("2439","美律","上市"),
            ("3406","玉晶光","上市"),("4958","臻鼎-KY","上市"),("6488","環球晶","上市"),
            ("2327","國巨","上市"),("2351","順德","上市"),("3036","文曄","上市"),
            ("2429","銘異","上市"),("6278","台表科","上市"),
        ]
    },
    "🔋 電動車｜電源管理": {
        "color": "#00e676",
        "desc": "電動車、儲能、電源IC",
        "stocks": [
            ("2308","台達電","上市"),("6415","矽力-KY","上市"),("2436","偉詮電","上市"),
            ("3044","健鼎","上市"),("3035","智原","上市"),("5483","中美晶","上市"),
            ("6244","茂迪","上市"),("3481","群創","上市"),("1590","亞德客-KY","上市"),
            ("2207","和泰車","上市"),("1504","東元","上市"),("1514","亞力","上市"),
            ("1537","廣隆","上市"),("8210","勝一","上市"),
        ]
    },
    "🌐 網通｜5G基礎建設": {
        "color": "#00d4ff",
        "desc": "網路設備、5G、光通訊",
        "stocks": [
            ("2412","中華電","上市"),("4904","遠傳","上市"),("3045","台灣大","上市"),
            ("2332","友訊","上市"),("3617","碩天","上市"),("6277","宏正","上市"),
            ("4205","中華食","上市"),("3518","柏騰","上市"),("2345","智邦","上市"),
            ("6456","GIS-KY","上市"),("3047","訊舟","上市"),("4960","誠美材","上市"),
        ]
    },
    "🏦 金融｜銀行保險": {
        "color": "#ffab40",
        "desc": "銀行、保險、金控",
        "stocks": [
            ("2881","富邦金","上市"),("2882","國泰金","上市"),("2891","中信金","上市"),
            ("2886","兆豐金","上市"),("2887","台新金","上市"),("2884","玉山金","上市"),
            ("2885","元大金","上市"),("2892","第一金","上市"),("2880","華南金","上市"),
            ("5880","合庫金","上市"),("2801","彰銀","上市"),("2820","華票","上市"),
        ]
    },
    "🧪 傳統產業｜石化塑膠": {
        "color": "#546e7a",
        "desc": "石化、塑膠、原物料",
        "stocks": [
            ("6505","台塑化","上市"),("1301","台塑","上市"),("1303","南亞","上市"),
            ("1326","台化","上市"),("1402","遠東新","上市"),("1301","台塑","上市"),
            ("2002","中鋼","上市"),("1101","台泥","上市"),("1102","亞泥","上市"),
            ("1216","統一","上市"),("1201","味全","上市"),("1303","南亞","上市"),
        ]
    },
    "🏗️ 營建｜不動產": {
        "color": "#ff9800",
        "desc": "建設、營造、房仲",
        "stocks": [
            ("5522","遠雄","上市"),("2528","皇翔","上市"),("5533","三發地產","上市"),
            ("2534","宏盛","上市"),("5536","聖暉","上市"),("2515","中工","上市"),
            ("1702","南僑","上市"),("2543","皇昌","上市"),("5546","永信建","上市"),
            ("2511","太子","上市"),("2597","潤弘","上市"),
        ]
    },
    "💊 生技｜醫療": {
        "color": "#e040fb",
        "desc": "生技製藥、醫療器材",
        "stocks": [
            ("4743","合一","上市"),("6547","泰福-KY","上市"),("1789","神隆","上市"),
            ("4144","宜特","上市"),("4147","中裕","上市"),("6446","藥華藥","上市"),
            ("1760","寶齡富錦","上市"),("4174","浩鼎","上市"),("6594","台灣醫療","上市"),
            ("4141","龍燈-KY","上市"),("4162","智擎","上市"),("6616","特昇-KY","上市"),
        ]
    },
    "🛒 零售｜電商": {
        "color": "#ff9800",
        "desc": "電商、量販、連鎖通路",
        "stocks": [
            ("2912","統一超","上市"),("2903","遠百","上市"),("2915","潤泰全","上市"),
            ("5904","寶雅","上市"),("2923","鼎固-KY","上市"),("2882","國泰金","上市"),
            ("6509","聚和","上市"),("2481","強茂","上市"),("8044","網家","上市"),
            ("3697","vivo","上市"),("2915","潤泰全","上市"),
        ]
    },
    "✏️ 自訂股票組合": {
        "color": "#7fb3d3",
        "desc": "手動輸入任意股票代號",
        "stocks": []   # 由使用者輸入
    },
}

# ══════════════════════════════════════════════
# 取得單一群組資料（帶快取）
# ══════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def fetch_group_data(tickers_tuple):
    """批次下載一個群組的股票資料"""
    results = []
    tickers = list(tickers_tuple)  # tuple → list（for cache hashability）

    for ticker, code, name, market in tickers:
        try:
            df = yf.download(ticker, period="6mo", auto_adjust=True, progress=False)
            if df.empty or len(df) < 25:
                continue
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

            close = df["Close"].values.flatten().astype(float)
            vol   = df["Volume"].values.flatten().astype(float)
            high  = df["High"].values.flatten().astype(float)
            low_  = df["Low"].values.flatten().astype(float)

            # 均線
            s = pd.Series(close)
            ma5_v  = float(s.rolling(5).mean().iloc[-1])
            ma20_v = float(s.rolling(20).mean().iloc[-1])
            vma5_v = float(pd.Series(vol).rolling(5).mean().iloc[-1])

            last_close = close[-1]
            last_vol   = vol[-1]
            ma20_bias  = (last_close - ma20_v) / ma20_v * 100 if ma20_v > 0 else 0
            vol_ratio  = last_vol / vma5_v if vma5_v > 0 else 99

            # 底部墊高
            recent_low = min(low_[-20:]) if len(low_) >= 20 else min(low_)
            old_low    = min(low_[-40:-20]) if len(low_) >= 40 else min(low_)
            higher_low = bool(recent_low > old_low)

            # KD
            low_s  = pd.Series(low_);  high_s = pd.Series(high)
            lm = low_s.rolling(9).min(); hm = high_s.rolling(9).max()
            rsv = (s - lm) / (hm - lm + 1e-9) * 100
            k_v = d_v = 50.0
            for r in rsv.dropna():
                k_v = k_v*2/3 + float(r)*1/3
                d_v = d_v*2/3 + k_v*1/3

            # 財務（yfinance info）
            pe_v = gm_v = eps_v = np.nan
            try:
                info = yf.Ticker(ticker).info or {}
                pe_v  = info.get("trailingPE", np.nan)
                eps_v = info.get("trailingEps", np.nan)
                gm_v  = info.get("grossMargins", np.nan)
                if gm_v and not np.isnan(gm_v) and gm_v < 1:
                    gm_v = gm_v * 100
            except:
                pass

            results.append({
                "代號": code, "名稱": name, "市場": market,
                "yf_ticker": ticker,
                "收盤價": round(last_close, 1),
                "MA5": round(ma5_v, 1),
                "MA20": round(ma20_v, 1),
                "MA20乖離%": round(ma20_bias, 2),
                "量比(5MA)": round(vol_ratio, 2),
                "底部墊高": higher_low,
                "K值": round(k_v, 1),
                "D值": round(d_v, 1),
                "PE": round(pe_v, 1) if pe_v and not np.isnan(pe_v) else np.nan,
                "EPS_TTM": round(eps_v, 2) if eps_v and not np.isnan(eps_v) else np.nan,
                "毛利率%": round(gm_v, 1) if gm_v and not np.isnan(gm_v) else np.nan,
                # 籌碼模擬（yfinance 無法取得真實資料）
                "融資5日變動%": round(np.random.uniform(-7, 4), 1),
                "法人買超%":    round(np.random.uniform(-5, 25), 1),
                "大戶持股增":   bool(np.random.choice([True, False], p=[0.45, 0.55])),
            })
        except:
            continue
        time.sleep(0.04)

    return pd.DataFrame(results)

def apply_filters(df, params):
    r = df.copy()
    c1 = (r["EPS_TTM"] > params["eps_min"]).fillna(False)
    c2 = (r["PE"] < params["pe_max"]).fillna(True)
    c3 = (r["毛利率%"] > params["gm_min"]).fillna(False)
    r["pass1"] = c1 & c2 & c3
    r["pass2"] = r["pass1"] & (r["融資5日變動%"] < params["margin_max"]) & \
                 (r["法人買超%"] > params["inst_min"]) & (r["大戶持股增"] == True)
    r["pass3"] = r["pass2"] & \
                 (r["MA20乖離%"] > 0) & (r["MA20乖離%"] < params["bias_max"]) & \
                 (r["量比(5MA)"] < params["vol_max"]) & (r["底部墊高"] == True)
    return r

# ══════════════════════════════════════════════
# Session State
# ══════════════════════════════════════════════
defaults = {
    "scan_result":   None,
    "scanned":       False,
    "selected_pool": [],
    "custom_stocks": [],
    "watch_ticker":  "2454.TW",
    "watch_name":    "聯發科 (2454)",
    "scanned_group": "",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:10px 0 14px;'>
        <div style='font-size:1.8rem;'>📈</div>
        <div style='color:#00d4ff;font-size:0.9rem;font-weight:700;letter-spacing:2px;'>台股量化儀表板</div>
        <div style='color:#7fb3d3;font-size:0.68rem;margin-top:2px;'>QUANT TRADING SYSTEM v3</div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")

    # ── STEP 1：選擇類股群組
    st.markdown("<div style='color:#ffab40;font-size:0.76rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;'>STEP 1 ｜ 選擇掃描群組</div>", unsafe_allow_html=True)

    group_names = list(SECTOR_GROUPS.keys())
    selected_group = st.selectbox(
        "產業類股群組",
        group_names,
        help="選擇後可預覽該群組股票，再執行掃描"
    )
    group_info = SECTOR_GROUPS[selected_group]

    # 群組預覽
    if selected_group != "✏️ 自訂股票組合":
        stock_count = len(group_info["stocks"])
        st.markdown(f"""
        <div class='infobox'>
            <span style='color:{group_info["color"]};font-weight:600;'>{selected_group}</span><br>
            {group_info["desc"]}<br>
            <span style='color:#e8f4fd;'>共 <b>{stock_count}</b> 檔股票</span>
        </div>""", unsafe_allow_html=True)

        # 展開顯示群組股票清單
        with st.expander("📋 查看群組股票清單", expanded=False):
            for code, name, mkt in group_info["stocks"]:
                mkt_color = "#00d4ff" if mkt == "上市" else "#ffab40"
                st.markdown(
                    f"<span class='chip-gray'>{code}</span> "
                    f"<span style='color:#e8f4fd;font-size:0.82rem;'>{name}</span> "
                    f"<span style='color:{mkt_color};font-size:0.7rem;'>{mkt}</span>",
                    unsafe_allow_html=True
                )
    else:
        # 自訂輸入
        st.markdown("<div style='color:#7fb3d3;font-size:0.78rem;margin-bottom:6px;'>輸入股票代號（逗號分隔）</div>", unsafe_allow_html=True)
        custom_input = st.text_area(
            "股票代號",
            placeholder="例：2330,2454,2308,3711\n每行或逗號分隔皆可",
            height=100, label_visibility="collapsed"
        )
        custom_note = st.text_input("備註名稱（選填）", placeholder="例：我的自選股")

        # 解析輸入
        if custom_input.strip():
            raw = custom_input.replace("\n", ",").replace("，", ",").replace(" ", "")
            codes = [c.strip() for c in raw.split(",") if c.strip().isdigit() and len(c.strip()) == 4]
            custom_stocks = []
            for code in codes:
                # 上市優先，若失敗則試上櫃
                custom_stocks.append((f"{code}.TW", code, code, "自訂"))
            st.session_state.custom_stocks = custom_stocks
            st.markdown(f"<div class='infobox'>已解析 <b style='color:#00e676;'>{len(codes)}</b> 檔股票代號</div>", unsafe_allow_html=True)
        else:
            st.session_state.custom_stocks = []

    # ── 多群組合併選項
    st.markdown("---")
    with st.expander("➕ 合併多個群組一起掃描", expanded=False):
        st.markdown("<div style='color:#7fb3d3;font-size:0.76rem;margin-bottom:6px;'>可多選，將合併掃描（注意：選越多越慢）</div>", unsafe_allow_html=True)
        extra_groups = st.multiselect(
            "額外加入群組",
            [g for g in group_names if g != selected_group and g != "✏️ 自訂股票組合"],
            label_visibility="collapsed"
        )

    # ── STEP 2：篩選條件
    st.markdown("---")
    st.markdown("<div style='color:#ffab40;font-size:0.76rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>STEP 2 ｜ 設定篩選條件</div>", unsafe_allow_html=True)

    with st.expander("第一道：基本面護城河", expanded=False):
        eps_min    = st.slider("EPS(TTM) 最低", 0.0, 20.0, 3.0, 0.5)
        pe_max     = st.slider("P/E 最高",       0,   80,   45,  1)
        gm_min     = st.slider("毛利率% 最低",   0,   60,   15,  1)
        st.caption("⚠️ 金融股毛利率定義不同，建議設低於15%")

    with st.expander("第二道：籌碼黃金交叉", expanded=False):
        margin_max = st.slider("融資5日變動% 上限", -10.0, 2.0, -1.5, 0.5)
        inst_min   = st.slider("法人買超% 下限",     0.0,  30.0,  5.0, 0.5)
        st.caption("⚠️ 籌碼資料為模擬值，僅供 UI 展示")

    with st.expander("第三道：右側均線防守", expanded=False):
        bias_max   = st.slider("MA20乖離% 上限",    1.0, 15.0, 6.0, 0.5)
        vol_max    = st.slider("量比(5MA) 上限",     0.3,  1.5, 0.7, 0.05)

    params = dict(eps_min=eps_min, pe_max=pe_max, gm_min=gm_min,
                  margin_max=margin_max, inst_min=inst_min,
                  bias_max=bias_max, vol_max=vol_max)

    # ── STEP 3：執行掃描
    st.markdown("---")
    st.markdown("<div style='color:#ffab40;font-size:0.76rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>STEP 3 ｜ 執行掃描</div>", unsafe_allow_html=True)

    # 組合最終股票清單
    def build_scan_list():
        stocks = []
        seen   = set()

        if selected_group == "✏️ 自訂股票組合":
            for ticker, code, name, mkt in st.session_state.custom_stocks:
                if code not in seen:
                    stocks.append((ticker, code, name, mkt))
                    seen.add(code)
        else:
            for code, name, mkt in SECTOR_GROUPS[selected_group]["stocks"]:
                if code not in seen:
                    ticker = f"{code}.TW" if mkt == "上市" else f"{code}.TWO"
                    stocks.append((ticker, code, name, mkt))
                    seen.add(code)
            for g in extra_groups:
                for code, name, mkt in SECTOR_GROUPS[g]["stocks"]:
                    if code not in seen:
                        ticker = f"{code}.TW" if mkt == "上市" else f"{code}.TWO"
                        stocks.append((ticker, code, name, mkt))
                        seen.add(code)
        return stocks

    scan_list   = build_scan_list()
    total_count = len(scan_list)
    est_sec     = total_count * 1.2
    est_str     = f"{int(est_sec//60)}分{int(est_sec%60)}秒" if est_sec >= 60 else f"{int(est_sec)}秒"

    if total_count > 0:
        st.markdown(f"""
        <div class='infobox'>
            準備掃描 <b style='color:#00d4ff;'>{total_count}</b> 檔股票<br>
            預估時間：<b style='color:#ffab40;'>{est_str}</b>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#ff5252;font-size:0.78rem;'>⚠️ 請先選擇群組或輸入股票代號</div>", unsafe_allow_html=True)

    run_btn = st.button(
        "🚀 開始掃描此群組",
        type="primary",
        use_container_width=True,
        disabled=(total_count == 0)
    )

    if run_btn:
        prog   = st.progress(0)
        stat   = st.empty()
        total  = len(scan_list)
        rows   = []
        errors = []

        for i, (ticker, code, name, mkt) in enumerate(scan_list):
            prog.progress((i+1)/total)
            stat.markdown(f"<div style='color:#7fb3d3;font-size:0.76rem;'>[{i+1}/{total}] {code} {name}</div>", unsafe_allow_html=True)
            try:
                df_tmp = yf.download(ticker, period="6mo", auto_adjust=True, progress=False)
                if df_tmp.empty or len(df_tmp) < 25:
                    errors.append(code); continue
                df_tmp.columns = [c[0] if isinstance(c,tuple) else c for c in df_tmp.columns]

                close = df_tmp["Close"].values.flatten().astype(float)
                vol   = df_tmp["Volume"].values.flatten().astype(float)
                high  = df_tmp["High"].values.flatten().astype(float)
                low_  = df_tmp["Low"].values.flatten().astype(float)

                s = pd.Series(close)
                ma20_v = float(s.rolling(20).mean().iloc[-1])
                vma5_v = float(pd.Series(vol).rolling(5).mean().iloc[-1])
                last_c = float(close[-1])
                last_v = float(vol[-1])
                ma20_b = (last_c - ma20_v) / ma20_v * 100 if ma20_v > 0 else 0
                vol_r  = last_v / vma5_v if vma5_v > 0 else 99

                rl = min(low_[-20:]) if len(low_)>=20 else min(low_)
                ol = min(low_[-40:-20]) if len(low_)>=40 else min(low_)
                hl = bool(rl > ol)

                lm = pd.Series(low_).rolling(9).min()
                hm = pd.Series(high).rolling(9).max()
                rsv = (s - lm) / (hm - lm + 1e-9) * 100
                kv = dv = 50.0
                for r in rsv.dropna():
                    kv = kv*2/3 + float(r)*1/3
                    dv = dv*2/3 + kv*1/3

                pe_v = gm_v = eps_v = np.nan
                try:
                    info = yf.Ticker(ticker).info or {}
                    pe_v  = info.get("trailingPE", np.nan)
                    eps_v = info.get("trailingEps", np.nan)
                    gm_v  = info.get("grossMargins", np.nan)
                    if gm_v and not np.isnan(float(gm_v)) and float(gm_v) < 1:
                        gm_v = float(gm_v) * 100
                except: pass

                rows.append({
                    "代號":code,"名稱":name,"市場":mkt,"yf_ticker":ticker,
                    "收盤價":round(last_c,1),
                    "MA20乖離%":round(ma20_b,2),"量比(5MA)":round(vol_r,2),
                    "底部墊高":hl,"K值":round(kv,1),"D值":round(dv,1),
                    "PE":round(float(pe_v),1) if pe_v and not np.isnan(float(pe_v)) else np.nan,
                    "EPS_TTM":round(float(eps_v),2) if eps_v and not np.isnan(float(eps_v)) else np.nan,
                    "毛利率%":round(float(gm_v),1) if gm_v and not np.isnan(float(gm_v)) else np.nan,
                    "融資5日變動%":round(np.random.uniform(-7,4),1),
                    "法人買超%":round(np.random.uniform(-5,25),1),
                    "大戶持股增":bool(np.random.choice([True,False],p=[0.45,0.55])),
                })
            except: errors.append(code)
            time.sleep(0.04)

        prog.empty(); stat.empty()

        if rows:
            df_raw_scan = pd.DataFrame(rows)
            df_filtered = apply_filters(df_raw_scan, params)
            st.session_state.scan_result   = df_filtered
            st.session_state.scanned       = True
            st.session_state.scanned_group = selected_group
            passed3 = df_filtered[df_filtered["pass3"]==True]
            st.session_state.selected_pool = [
                (r["yf_ticker"], f"{r['代號']} {r['名稱']}")
                for _, r in passed3.iterrows()
            ]
            n3 = len(passed3)
            if errors:
                st.warning(f"⚠️ {len(errors)} 檔無資料（{','.join(errors[:5])}{'...' if len(errors)>5 else ''}）")
            st.success(f"✅ 掃描完成！精選 {n3} 檔通過三道篩選")
        else:
            st.error("掃描失敗，請確認網路連線")

    # 篩選結果摘要
    if st.session_state.scanned and st.session_state.scan_result is not None:
        df_r = st.session_state.scan_result
        n1=int(df_r["pass1"].sum()); n2=int(df_r["pass2"].sum()); n3=int(df_r["pass3"].sum())
        st.markdown(f"""
        <div class='infobox'>
            <span style='color:#7fb3d3;font-size:0.72rem;'>上次掃描：{st.session_state.scanned_group}</span><br>
            掃描 <b style='color:#e8f4fd;'>{len(df_r)}</b> 檔 →
            一道 <b style='color:#00d4ff;'>{n1}</b> →
            二道 <b style='color:#e040fb;'>{n2}</b> →
            <b style='color:#00e676;'>精選 {n3} ✅</b>
        </div>""", unsafe_allow_html=True)

    # 再次篩選（不重新下載資料）
    if st.session_state.scanned and st.session_state.scan_result is not None:
        if st.button("🔄 重新套用條件（不重新下載）", use_container_width=True):
            df_refilter = apply_filters(
                st.session_state.scan_result.drop(columns=["pass1","pass2","pass3"], errors="ignore"),
                params
            )
            st.session_state.scan_result = df_refilter
            passed3 = df_refilter[df_refilter["pass3"]==True]
            st.session_state.selected_pool = [
                (r["yf_ticker"], f"{r['代號']} {r['名稱']}")
                for _, r in passed3.iterrows()
            ]
            st.rerun()

    # ── 監控標的選擇
    st.markdown("---")
    st.markdown("<div style='color:#7fb3d3;font-size:0.76rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>🎯 監控標的</div>", unsafe_allow_html=True)

    pool = st.session_state.selected_pool
    if pool:
        st.markdown(f"<div style='color:#00e676;font-size:0.76rem;margin-bottom:5px;'>● 精選清單 {len(pool)} 檔（三道全通過）</div>", unsafe_allow_html=True)
        pool_map = {label: tick for tick, label in pool}
        chosen   = st.selectbox("精選標的", list(pool_map.keys()), label_visibility="collapsed")
        ticker        = pool_map[chosen]
        selected_name = chosen
    else:
        fallback = {
            "聯發科 (2454)":"2454.TW","台積電 (2330)":"2330.TW",
            "台達電 (2308)":"2308.TW","廣達 (2382)":"2382.TW",
        }
        selected_name = st.selectbox("預設標的", list(fallback.keys()), label_visibility="collapsed")
        ticker = fallback[selected_name]
        st.markdown("<div style='color:#ffab40;font-size:0.72rem;'>💡 掃描後自動填入精選股</div>", unsafe_allow_html=True)

    st.session_state.watch_ticker = ticker
    st.session_state.watch_name   = selected_name

    st.markdown("---")
    st.markdown("<div style='color:#7fb3d3;font-size:0.76rem;letter-spacing:1px;text-transform:uppercase;'>📐 技術參數</div>", unsafe_allow_html=True)
    ma_short = st.slider("短均線", 3, 10, 5)
    ma_mid   = st.slider("中均線", 10, 30, 20)
    ma_long  = st.slider("長均線", 40, 120, 60)
    period   = st.select_slider("K線週期", ["3mo","6mo","1y","2y"], value="1y")

    st.markdown("---")
    st.markdown(f"""
    <div style='background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:9px;text-align:center;'>
        <div style='color:#00d4ff;font-size:0.8rem;font-family:monospace;'>{datetime.now().strftime("%Y/%m/%d %H:%M")}</div>
        <div style='color:#2ecc71;font-size:0.68rem;margin-top:3px;'>● Yahoo Finance ＋ 模擬籌碼</div>
    </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════
# Header
# ══════════════════════════════════════════════
st.markdown(f"""
<div class='main-header'>
    <h1>📊 台股全週期量化交易儀表板 v3</h1>
    <p>監控標的：{selected_name} ｜ 群組：{selected_group} ｜ 籌碼為模擬值，技術面來自 Yahoo Finance</p>
</div>""", unsafe_allow_html=True)

# ── KPI Bar
@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(tk, pd_):
    try:
        df = yf.download(tk, period=pd_, auto_adjust=True, progress=False)
        df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
        return df.dropna()
    except:
        return pd.DataFrame()

def add_indicators(df, ws, wm, wl):
    for w in [ws,wm,wl]:
        df[f'MA{w}'] = df['Close'].rolling(w).mean()
    lm=df['Low'].rolling(9).min(); hm=df['High'].rolling(9).max()
    rsv=(df['Close']-lm)/(hm-lm+1e-9)*100
    k,d=[50.0],[50.0]
    for r in rsv.iloc[1:]:
        k.append(k[-1]*2/3+r*1/3); d.append(d[-1]*2/3+k[-1]*1/3)
    df['K']=k; df['D']=d
    return df

df_raw = get_chart_data(ticker, period)
if df_raw.empty:
    st.error("❌ 無法取得資料，請稍後再試或換一個標的。")
    st.stop()

df = add_indicators(df_raw.copy(), ma_short, ma_mid, ma_long)
lt = df.iloc[-1]; pv = df.iloc[-2]
chg = (lt['Close']-pv['Close'])/pv['Close']*100
chg_cls = "up" if chg>=0 else "down"; chg_sym = "▲" if chg>=0 else "▼"

c1,c2,c3,c4,c5,c6 = st.columns(6)
for col,(lbl,val,cls) in zip([c1,c2,c3,c4,c5,c6],[
    ("收盤價",f"{lt['Close']:.1f}",chg_cls),
    ("漲跌幅",f"{chg_sym}{abs(chg):.2f}%",chg_cls),
    (f"MA{ma_short}",f"{lt[f'MA{ma_short}']:.1f}",""),
    (f"MA{ma_mid}",f"{lt[f'MA{ma_mid}']:.1f}",""),
    ("K值",f"{lt['K']:.1f}",""),("D值",f"{lt['D']:.1f}",""),
]):
    with col:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)

st.markdown("<br>",unsafe_allow_html=True)

# ══════════════════════════════════════════════
# 四分頁
# ══════════════════════════════════════════════
tab1,tab2,tab3,tab4 = st.tabs([
    "🔍 選股掃描儀","🚨 即時防守監控牆","🧮 籌碼純度檢驗","📋 基本面追蹤"])

# ── TAB 1
with tab1:
    st.markdown("<div class='section-title'>波段潛力核心自選股清單 · 三道篩選結果</div>",unsafe_allow_html=True)

    # 群組概覽卡片
    gc1,gc2,gc3 = st.columns(3)
    for col,title,color,desc in zip([gc1,gc2,gc3],[
        "第一道 · 基本面護城河","第二道 · 籌碼黃金交叉","第三道 · 右側均線防守"],[
        "#00d4ff","#e040fb","#ffab40"],[
        f"EPS>{eps_min} ｜ PE<{pe_max} ｜ 毛利率>{gm_min}%",
        f"融資<{margin_max}% ｜ 法人>{inst_min}% ｜ 大戶增",
        f"MA20乖離<{bias_max}% ｜ 量比<{vol_max} ｜ 底高"]):
        with col:
            st.markdown(f"""
            <div style='background:linear-gradient(135deg,#0f2027,#162535);
                border:1px solid #1e3a5f;border-left:3px solid {color};
                border-radius:8px;padding:10px 12px;'>
                <div style='color:{color};font-size:0.72rem;letter-spacing:1px;margin-bottom:5px;'>{title}</div>
                <div style='color:#e8f4fd;font-size:0.78rem;line-height:1.6;'>{desc}</div>
            </div>""",unsafe_allow_html=True)

    st.markdown("<br>",unsafe_allow_html=True)

    if not st.session_state.scanned:
        st.markdown("""
        <div style='background:#0f2027;border:2px dashed #1e3a5f;border-radius:12px;padding:50px;text-align:center;'>
            <div style='font-size:2rem;margin-bottom:10px;'>🔬</div>
            <div style='color:#e8f4fd;font-size:0.95rem;font-weight:600;'>尚未執行掃描</div>
            <div style='color:#7fb3d3;font-size:0.82rem;margin-top:8px;line-height:1.8;'>
                左側 Sidebar：<br>
                STEP 1 選擇產業群組 →
                STEP 2 設定篩選條件 →
                STEP 3 點「🚀 開始掃描」
            </div>
        </div>""",unsafe_allow_html=True)
    else:
        df_r = st.session_state.scan_result
        n_all=len(df_r); n1=int(df_r["pass1"].sum()); n2=int(df_r["pass2"].sum()); n3=int(df_r["pass3"].sum())

        mc1,mc2,mc3,mc4 = st.columns(4)
        for col,(lbl,val,cls) in zip([mc1,mc2,mc3,mc4],[
            ("掃描股數",str(n_all),""),("第一道通過",str(n1),"up"),
            ("第二道通過",str(n2),"up"),("精選通過",str(n3),"up"),
        ]):
            with col:
                st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)

        st.markdown("<br>",unsafe_allow_html=True)
        fl, fr = st.columns([1,2])

        with fl:
            fig_f = go.Figure(go.Funnel(
                y=["群組股票池","基本面通過","籌碼通過","精選核心"],
                x=[n_all,n1,n2,n3],
                textinfo="value+percent initial",
                marker=dict(color=["#1e3a5f","#00d4ff","#e040fb","#00e676"]),
                textfont=dict(color="#e8f4fd",size=11),
                connector=dict(line=dict(color="#1e3a5f",width=1)),
            ))
            fig_f.update_layout(**base_layout("篩選漏斗",300))
            st.plotly_chart(fig_f,use_container_width=True)

        with fr:
            passed3=df_r[df_r["pass3"]==True]
            st.markdown(f"<div style='color:#00e676;font-weight:700;font-size:0.88rem;margin-bottom:8px;'>✅ 三道精選股（{n3}檔）</div>",unsafe_allow_html=True)
            if not passed3.empty:
                st.markdown(" ".join([f"<span class='chip-green'>{r['代號']} {r['名稱']}</span>" for _,r in passed3.iterrows()]),unsafe_allow_html=True)
            else:
                st.markdown("<div style='color:#ff5252;font-size:0.82rem;'>無股票通過三道篩選，請調整條件後點「重新套用」</div>",unsafe_allow_html=True)

            passed2=df_r[df_r["pass2"]==True]
            st.markdown(f"<div style='color:#00d4ff;font-weight:600;font-size:0.85rem;margin:12px 0 6px;'>📌 二道觀察名單（{n2}檔）</div>",unsafe_allow_html=True)
            st.markdown(" ".join([f"<span class='chip-blue'>{r['代號']} {r['名稱']}</span>" for _,r in passed2.iterrows()]) or "<span style='color:#546e7a;'>無</span>",unsafe_allow_html=True)

        # 互動篩選表
        st.markdown("<br>",unsafe_allow_html=True)
        st.markdown("<div class='section-title' style='font-size:0.8rem;'>📊 互動選股視窗</div>",unsafe_allow_html=True)

        tc1,tc2,tc3 = st.columns(3)
        with tc1: show_pass = st.selectbox("顯示範圍",["三道全通過","二道以上","一道以上","全部掃描"])
        with tc2: sort_col  = st.selectbox("排序欄位",["毛利率%","EPS_TTM","PE","MA20乖離%","量比(5MA)","法人買超%"])
        with tc3: sort_asc  = (st.radio("排序",["↓ 高到低","↑ 低到高"],horizontal=True) == "↑ 低到高")

        pass_map = {
            "三道全通過":  df_r[df_r["pass3"]==True],
            "二道以上":    df_r[df_r["pass2"]==True],
            "一道以上":    df_r[df_r["pass1"]==True],
            "全部掃描":    df_r,
        }
        dv = pass_map[show_pass].copy()
        if sort_col in dv.columns:
            dv = dv.sort_values(sort_col,ascending=sort_asc,na_position='last')

        show_cols = [c for c in ["代號","名稱","市場","收盤價","EPS_TTM","毛利率%","PE","MA20乖離%","量比(5MA)","融資5日變動%","法人買超%","pass1","pass2","pass3"] if c in dv.columns]
        dd = dv[show_cols].copy()
        for p in ["pass1","pass2","pass3"]:
            if p in dd.columns:
                dd[p] = dd[p].map({True:"✅",False:"❌"})
        dd.columns = [c.replace("pass1","第一道").replace("pass2","第二道").replace("pass3","第三道") for c in dd.columns]

        def row_hl(row):
            if row.get("第三道","")=="✅": return ['background:rgba(0,230,118,0.07);color:#e8f4fd']*len(row)
            if row.get("第二道","")=="✅": return ['background:rgba(0,212,255,0.05);color:#e8f4fd']*len(row)
            return ['color:#7fb3d3']*len(row)

        fmt={c:"{:.1f}" for c in ["毛利率%","MA20乖離%","融資5日變動%","法人買超%"]}
        fmt.update({"收盤價":"{:.1f}","EPS_TTM":"{:.2f}","PE":"{:.1f}","量比(5MA)":"{:.2f}"})
        fmt_v={k:v for k,v in fmt.items() if k in dd.columns}
        st.dataframe(dd.style.apply(row_hl,axis=1).format(fmt_v,na_rep="—"),
                     use_container_width=True, height=360)
        st.caption(f"共 {len(dd)} 檔 ｜ 在左側 Sidebar 選擇標的後，切換分頁查看詳細分析")

# ── TAB 2
with tab2:
    st.markdown("<div class='section-title'>即時防守監控牆 · INTRADAY RISK MONITOR</div>",unsafe_allow_html=True)
    above_ma5 = lt['Close'] > lt[f'MA{ma_short}']
    kd_cross  = (lt['K'] < lt['D']) and (lt['K'] > 80)
    n_i=78; ti=pd.date_range("09:00",periods=n_i,freq="5min")
    pi=float(lt['Close'])+np.cumsum(np.random.randn(n_i)*3)
    vi=np.mean(pi[:n_i//2])+np.random.randn(n_i)*1.5
    below_vwap = pi[-1] < vi[-1]

    alerts=[]
    if not above_ma5: alerts.append(f"股價跌破 MA{ma_short}（{lt[f'MA{ma_short}']:.1f}）")
    if kd_cross:      alerts.append(f"KD 死叉（K={lt['K']:.1f} < D={lt['D']:.1f}，K>80）")
    if below_vwap:    alerts.append(f"盤中模擬跌破 VWAP（{vi[-1]:.1f}）")

    cs,cd=st.columns([1,3])
    with cs:
        if alerts: st.markdown(f"<div class='signal-red'>🔴 高警戒<br><small>觸發{len(alerts)}項</small></div>",unsafe_allow_html=True)
        else:      st.markdown("<div class='signal-green'>🟢 持倉安全<br><small>無警示</small></div>",unsafe_allow_html=True)
    with cd:
        for a in alerts: st.markdown(f"<div class='warning-banner'>⚠️ {a}</div>",unsafe_allow_html=True)
        if not alerts: st.markdown("<div style='color:#2ecc71;padding:8px;'>✅ 所有指標正常</div>",unsafe_allow_html=True)

    cl,cr=st.columns([3,1])
    with cl:
        fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[0.7,0.3],vertical_spacing=0.04)
        fig.add_trace(go.Candlestick(x=df.index,open=df['Open'],high=df['High'],low=df['Low'],close=df['Close'],
            increasing_line_color='#00e676',decreasing_line_color='#ff5252',name='K線',showlegend=False),row=1,col=1)
        for mw,mc in [(ma_short,'#ff9800'),(ma_mid,'#00d4ff'),(ma_long,'#e040fb')]:
            fig.add_trace(go.Scatter(x=df.index,y=df[f'MA{mw}'],mode='lines',name=f'MA{mw}',line=dict(color=mc,width=1.5)),row=1,col=1)
        fig.add_trace(go.Bar(x=df.index,y=df['Volume'],name='量',
            marker_color=['#00e676' if c>=o else '#ff5252' for c,o in zip(df['Close'],df['Open'])],
            opacity=0.5,showlegend=False),row=2,col=1)
        fig.update_layout(**base_layout(f"{selected_name} 日線走勢",460),xaxis_rangeslider_visible=False)
        st.plotly_chart(fig,use_container_width=True)
    with cr:
        kdt=df.tail(60)
        fk=go.Figure()
        fk.add_trace(go.Scatter(x=kdt.index,y=kdt['K'],mode='lines',name='K',line=dict(color='#ff9800',width=1.5)))
        fk.add_trace(go.Scatter(x=kdt.index,y=kdt['D'],mode='lines',name='D',line=dict(color='#00d4ff',width=1.5)))
        fk.add_hrect(y0=80,y1=100,fillcolor="rgba(255,82,82,0.08)",line_width=0)
        fk.add_hrect(y0=0,y1=20,fillcolor="rgba(0,230,118,0.08)",line_width=0)
        fk.add_hline(y=80,line_dash="dot",line_color="#ff5252",line_width=1)
        fk.add_hline(y=20,line_dash="dot",line_color="#00e676",line_width=1)
        fk.update_layout(**base_layout("KD（近60日）",230))
        st.plotly_chart(fk,use_container_width=True)
        fv=go.Figure()
        fv.add_trace(go.Scatter(x=ti,y=pi,mode='lines',name='盤中',line=dict(color='#e8f4fd',width=1.5)))
        fv.add_trace(go.Scatter(x=ti,y=vi,mode='lines',name='VWAP',line=dict(color='#ffab40',width=1.5,dash='dot')))
        fv.update_layout(**base_layout("盤中模擬 vs VWAP",230))
        st.plotly_chart(fv,use_container_width=True)

# ── TAB 3
with tab3:
    st.markdown("<div class='section-title'>籌碼純度檢驗 · CHIPS ANALYSIS</div>",unsafe_allow_html=True)
    st.markdown("<div class='warning-banner' style='font-size:0.78rem;'>ℹ️ 以下籌碼資料為模擬值，僅供介面展示，不代表真實市場資訊</div>",unsafe_allow_html=True)
    n_c=60; dc_=pd.date_range(end=datetime.today(),periods=n_c,freq='B')
    fg=np.random.randint(-8000,15000,n_c).cumsum()//10
    tr=np.random.randint(-3000,6000,n_c).cumsum()//10
    dl=np.random.randint(-2000,4000,n_c).cumsum()//10
    mg=45000+np.cumsum(np.random.randint(-500,800,n_c))
    dchip=pd.DataFrame({"日期":dc_,"外資":fg/100,"投信":tr/100,"自營商":dl/100,"融資餘額":mg/100})
    ri=dchip['外資'].iloc[-5:].sum()+dchip['投信'].iloc[-5:].sum()
    rm=dchip['融資餘額'].iloc[-1]-dchip['融資餘額'].iloc[-5]
    if ri<0 and rm>0:
        st.markdown("<div class='warning-banner' style='text-align:center;'>⚠️ 籌碼發散，請提高警覺｜法人賣超 且 融資增加</div>",unsafe_allow_html=True)
    m1c,m2c,m3c,m4c=st.columns(4)
    for col,(lbl,val,cls) in zip([m1c,m2c,m3c,m4c],[
        ("外資近5日",f"{dchip['外資'].iloc[-5:].sum():.1f}億","up" if dchip['外資'].iloc[-5:].sum()>0 else "down"),
        ("投信近5日",f"{dchip['投信'].iloc[-5:].sum():.1f}億","up" if dchip['投信'].iloc[-5:].sum()>0 else "down"),
        ("自營近5日",f"{dchip['自營商'].iloc[-5:].sum():.1f}億","up" if dchip['自營商'].iloc[-5:].sum()>0 else "down"),
        ("融資變動",f"{rm:+.1f}億","up" if rm<0 else "down"),
    ]):
        with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
    st.markdown("<br>",unsafe_allow_html=True)
    cc1,cc2=st.columns([3,2])
    with cc1:
        fi2=make_subplots(specs=[[{"secondary_y":True}]])
        for nm,cl_ in [("外資","#00d4ff"),("投信","#e040fb"),("自營商","#ffab40")]:
            v=dchip[nm]
            fi2.add_trace(go.Bar(x=dchip['日期'],y=v,name=nm,
                marker_color=[cl_ if x>=0 else '#ff5252' for x in v],opacity=0.75),secondary_y=False)
        fi2.add_trace(go.Scatter(x=dchip['日期'],y=dchip['融資餘額'],mode='lines',name='融資餘額',line=dict(color='#ff9800',width=2)),secondary_y=True)
        fi2.update_layout(**base_layout("三大法人買賣超 ＋ 融資餘額",380))
        fi2.update_yaxes(gridcolor=GRID_COLOR,secondary_y=False)
        fi2.update_yaxes(showgrid=False,secondary_y=True)
        st.plotly_chart(fi2,use_container_width=True)
    with cc2:
        i5={"外資":dchip['外資'].iloc[-5:].sum(),"投信":dchip['投信'].iloc[-5:].sum(),"自營商":dchip['自營商'].iloc[-5:].sum()}
        fb2=go.Figure(go.Bar(x=list(i5.values()),y=list(i5.keys()),orientation='h',
            marker_color=['#00e676' if v>=0 else '#ff5252' for v in i5.values()],
            text=[f'{v:.1f}億' for v in i5.values()],textposition='outside'))
        fb2.update_layout(**base_layout("近5日法人",190))
        st.plotly_chart(fb2,use_container_width=True)
        cats=['外資持股','投信持股','籌碼集中','融資比低','董監持股']
        vs=[np.random.uniform(50,90) for _ in range(5)]; vs+=[vs[0]]; cats+=[cats[0]]
        fr2=go.Figure(go.Scatterpolar(r=vs,theta=cats,fill='toself',line_color='#00d4ff',fillcolor='rgba(0,212,255,0.12)'))
        fr2.update_layout(polar=dict(bgcolor='rgba(0,0,0,0)',
            radialaxis=dict(visible=True,range=[0,100],gridcolor=GRID_COLOR,color=TEXT_COLOR),
            angularaxis=dict(gridcolor=GRID_COLOR,color=TEXT_COLOR)),**base_layout("籌碼純度雷達",210))
        st.plotly_chart(fr2,use_container_width=True)

# ── TAB 4
with tab4:
    st.markdown("<div class='section-title'>基本面追蹤 · FUNDAMENTALS</div>",unsafe_allow_html=True)
    qs=["23Q1","23Q2","23Q3","23Q4","24Q1","24Q2","24Q3","24Q4","25Q1"]
    gm=[42.1,43.5,44.2,45.0,46.1,47.3,47.8,48.5,49.2]
    om=[28.3,29.1,30.2,31.5,32.1,33.4,34.0,34.8,35.5]
    rv=[1380,1420,1510,1680,1750,1820,1950,2100,2250]
    ep=[12.5,13.8,14.2,15.6,16.9,17.3,18.1,19.4,21.2]
    df4=pd.DataFrame({"季度":qs,"毛利率%":gm,"營益率%":om,"營收(億)":rv})
    lq=df4.iloc[-1]
    m1f,m2f,m3f,m4f=st.columns(4)
    for col,(lbl,val,cls) in zip([m1f,m2f,m3f,m4f],[
        ("最新毛利率",f"{lq['毛利率%']:.1f}%","up"),("最新營益率",f"{lq['營益率%']:.1f}%","up"),
        ("最新季營收",f"{lq['營收(億)']:.0f}億",""),("YoY成長","+22.4%","up"),
    ]):
        with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
    st.markdown("<br>",unsafe_allow_html=True)
    fl4,fr4=st.columns([1,2])
    with fl4:
        fp=go.Figure(go.Pie(labels=['手機業務','ASIC/HPC','IoT/穿戴','電源管理','其他'],
            values=[42,28,15,10,5],hole=0.44,
            marker=dict(colors=['#00d4ff','#e040fb','#00e676','#ffab40','#546e7a'],line=dict(color='#0a0e1a',width=2)),
            textinfo='percent+label',textfont=dict(size=10,color='#e8f4fd')))
        fp.update_layout(**base_layout("營收結構（模擬）",300),
            annotations=[dict(text='營收<br>結構',x=0.5,y=0.5,font_size=11,font_color='#7fb3d3',showarrow=False)])
        st.plotly_chart(fp,use_container_width=True)
        fbiz=go.Figure()
        for nm,vs_,c_ in [('手機',[620,640,680,750,810],'#00d4ff'),('ASIC',[420,470,520,590,670],'#e040fb')]:
            fbiz.add_trace(go.Bar(x=["24Q1","24Q2","24Q3","24Q4","25Q1"],y=vs_,name=nm,marker_color=c_,opacity=0.8))
        fbiz.update_layout(**base_layout("主力業務營收（億）",240),barmode='group')
        st.plotly_chart(fbiz,use_container_width=True)
    with fr4:
        fm4=make_subplots(specs=[[{"secondary_y":True}]])
        fm4.add_trace(go.Scatter(x=df4['季度'],y=df4['毛利率%'],mode='lines+markers',name='毛利率%',
            line=dict(color='#00e676',width=2.5),marker=dict(size=7),
            fill='tonexty',fillcolor='rgba(0,230,118,0.05)'),secondary_y=False)
        fm4.add_trace(go.Scatter(x=df4['季度'],y=df4['營益率%'],mode='lines+markers',name='營益率%',
            line=dict(color='#00d4ff',width=2.5,dash='dot'),marker=dict(size=7)),secondary_y=False)
        fm4.add_trace(go.Bar(x=df4['季度'],y=df4['營收(億)'],name='季營收',marker_color='rgba(224,64,251,0.22)'),secondary_y=True)
        for q,g in zip(df4['季度'],df4['毛利率%']):
            fm4.add_annotation(x=q,y=g+0.5,text=f"{g:.1f}%",showarrow=False,font=dict(size=9,color='#00e676'))
        fm4.update_layout(**base_layout("毛利率/營益率/季營收",380))
        fm4.update_yaxes(gridcolor=GRID_COLOR,secondary_y=False)
        fm4.update_yaxes(showgrid=False,secondary_y=True)
        st.plotly_chart(fm4,use_container_width=True)
        fe4=go.Figure(go.Bar(x=qs,y=ep,
            marker_color=['#00e676' if e>15 else '#ffab40' for e in ep],
            text=[f'{e:.1f}' for e in ep],textposition='outside'))
        fe4.add_hline(y=eps_min,line_dash="dot",line_color="#ff5252",line_width=1,
            annotation_text=f"篩選線={eps_min}",annotation_font_color="#ff5252")
        fe4.update_layout(**base_layout("單季EPS趨勢（元）",230))
        st.plotly_chart(fe4,use_container_width=True)
