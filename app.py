"""
app.py  ── 台股全週期量化交易系統 V4
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
import time, warnings, json
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# ▌ 頁面基礎設定
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="台股量化系統 V4",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── GitHub raw URL 前綴（★ 請修改為你的帳號/repo）
GITHUB_RAW = "https://raw.githubusercontent.com/RabbitAstronaut/taiwan-stock-dashboard/main/data"

# ══════════════════════════════════════════════════════════════
# ▌ CSS 主題
# ══════════════════════════════════════════════════════════════
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
.metric-value.up{color:#00e676;}.metric-value.down{color:#ff5252;}

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

/* ── 按鈕 */
.stButton>button{color:#fff!important;font-weight:600!important;}
.stButton>button[kind="primary"]{background:linear-gradient(135deg,#0066cc,#0044aa)!important;border:1px solid #00d4ff!important;}

/* ── 評分徽章 */
.badge-green{display:inline-block;background:rgba(0,230,118,.12);border:1px solid #00e676;color:#00e676;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}
.badge-red  {display:inline-block;background:rgba(255,82,82,.12);border:1px solid #ff5252;color:#ff5252;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}
.badge-gray {display:inline-block;background:rgba(84,110,122,.2);border:1px solid #546e7a;color:#7fb3d3;border-radius:14px;padding:2px 10px;font-size:.74rem;margin:2px;}

/* ── Infobox */
.infobox{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:.78rem;color:#7fb3d3;line-height:1.6;}

@keyframes pulse{0%{box-shadow:0 0 8px rgba(255,82,82,.2);}50%{box-shadow:0 0 18px rgba(255,82,82,.5);}100%{box-shadow:0 0 8px rgba(255,82,82,.2);}}
</style>
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
@st.cache_data(ttl=3600, show_spinner=False)
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

@st.cache_data(ttl=300, show_spinner=False)
def load_price_csv(stock_id: str) -> tuple[pd.DataFrame, bool]:
    """讀取個股 K 線 CSV"""
    import os
    local = os.path.join("data", "prices", f"{stock_id}.csv")
    if os.path.exists(local):
        try:
            df = pd.read_csv(local)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df.dropna(subset=["date"]).set_index("date").sort_index()
            for c in ["Open","High","Low","Close","Volume"]:
                # 嘗試大小寫
                matches = [x for x in df.columns if x.lower() == c.lower()]
                if matches:
                    df[c] = pd.to_numeric(df[matches[0]], errors="coerce")
            df = df.dropna(subset=["Close"])
            return (df, True) if len(df) >= 10 else (pd.DataFrame(), False)
        except:
            pass
    return pd.DataFrame(), False

# ── 衍生載入函式
def get_stock_info():
    df, ok = load_csv("stock_info.csv")
    if ok and not df.empty:
        return df[["stock_id","stock_name"]].drop_duplicates("stock_id"), True
    return pd.DataFrame(), False

def get_chips(stock_id=None):
    df, ok = load_csv("chips_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    if stock_id:
        df = df[df["stock_id"] == stock_id]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["buy","sell","net","MarginPurchaseTodayBalance"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date") if "date" in df.columns else df, True

def get_financials(stock_id=None):
    df, ok = load_csv("financial_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    if stock_id:
        df = df[df["stock_id"] == stock_id]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.sort_values("date") if "date" in df.columns else df, True

def get_futures():
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
    if stock_id:
        df = df[df["stock_id"] == stock_id]
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

    df[f"MA{ma_s}"]  = c.rolling(ma_s).mean()
    df[f"MA{ma_m}"]  = c.rolling(ma_m).mean()
    df[f"MA{ma_l}"]  = c.rolling(ma_l).mean()
    df[f"VMA{ma_s}"] = v.rolling(ma_s).mean()

    # KD
    lo9 = df["Low"].astype(float).rolling(9).min()
    hi9 = df["High"].astype(float).rolling(9).max()
    rsv = (c - lo9) / (hi9 - lo9 + 1e-9) * 100
    K, D = [50.0], [50.0]
    for r in rsv.iloc[1:]:
        K.append(K[-1]*2/3 + r*1/3)
        D.append(D[-1]*2/3 + K[-1]*1/3)
    df["K"], df["D"] = K, D

    # VWAP (當日近似)
    tp  = (df["High"].astype(float) + df["Low"].astype(float) + c) / 3
    df["VWAP"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
    return df

def add_indicators(df, ws=5, wm=20, wl=60):
    return calc_indicators(df, ws, wm, wl)

# ══════════════════════════════════════════════════════════════
# ▌ Session State 初始化
# ══════════════════════════════════════════════════════════════
if "watchlist" not in st.session_state:
    st.session_state.watchlist: list[dict] = []
    # 範例：[{"id":"2454","name":"聯發科"}]

# ══════════════════════════════════════════════════════════════
# ▌ SIDEBAR
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:10px 0 14px;">
        <div style="font-size:2rem;">📈</div>
        <div style="color:#00d4ff;font-size:.9rem;font-weight:700;letter-spacing:2px;">台股量化系統</div>
        <div style="color:#7fb3d3;font-size:.66rem;margin-top:3px;">QUANT TRADING SYSTEM V4</div>
    </div>""", unsafe_allow_html=True)

    # ── 資料更新時間
    meta = load_json_meta()
    upd  = meta.get("updated_at", "尚未更新")
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
        if st.button("加入", use_container_width=True, key="sb_add_btn"):
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
                    st.success(f"已加入：{name}")
                else:
                    st.info("已在清單中")
            else:
                st.warning("請輸入 4 位數字代號")

        # 清單與刪除
        if st.session_state.watchlist:
            rm_idx = None
            for i, w in enumerate(st.session_state.watchlist):
                c1, c2 = st.columns([5, 1])
                c1.markdown(
                    f"<span style='color:#e8f4fd;font-size:.78rem;'>"
                    f"{w['id']} {w['name']}</span>",
                    unsafe_allow_html=True,
                )
                if c2.button("✕", key=f"rm_{i}", use_container_width=True):
                    rm_idx = i
            if rm_idx is not None:
                st.session_state.watchlist.pop(rm_idx)
                st.rerun()
        else:
            st.caption("清單為空，請輸入代號加入")

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
    if st.button("🔄 清除快取（強制重整）", use_container_width=True):
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
        📊 台股全週期量化交易系統 V4
    </h1>
    <p style="color:#7fb3d3;margin:4px 0 0;font-size:.76rem;">
        架構：本機爬蟲 → GitHub CSV → Streamlit Cloud ｜
        資料更新：{upd} ｜
        監控清單：{len(st.session_state.watchlist)} 檔
    </p>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# ▌ 三大分頁
# ══════════════════════════════════════════════════════════════
tab1, tab2, tab3 = st.tabs([
    "🔍 選股掃描儀",
    "🚨 持股監控",
    "📡 大盤預警",
])

# ──────────────────────────────────────────────────────────────
# ▌ TAB 1：選股掃描儀（階層式篩選＋評分）
# ──────────────────────────────────────────────────────────────
with tab1:
    st.markdown("<div class='sec-title'>🔍 選股掃描儀 · 三道階層篩選</div>",
                unsafe_allow_html=True)

    # ── 說明
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
            ["📂 產業分類", "🔢 股號開頭", "🌏 全市場", "✏️ 自訂代號"],
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
    with st.expander("⚙️ 調整篩選條件", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            st.markdown("**第一道：基本面**")
            eps_min = st.number_input("近四季 EPS 合計 >", value=10.0, step=0.5, key="t1_eps")
            pe_max  = st.number_input("P/E <",            value=40.0, step=1.0, key="t1_pe")
            gm_min  = st.number_input("最新季毛利率% >",  value=30.0, step=1.0, key="t1_gm")
        with fc2:
            st.markdown("**第二道：籌碼技術**")
            mg_max       = st.number_input("融資5日變動% <", value=-3.0, step=0.5, key="t1_mg")
            inst_min_pct = st.number_input("法人買超比例% >", value=10.0, step=1.0, key="t1_inst")
            bias_max     = st.number_input("MA20乖離% <",    value=5.0,  step=0.5, key="t1_bias")
            vol_max_r    = st.number_input("量比(5MA) <",    value=0.6,  step=0.05,key="t1_vol")
        with fc3:
            st.markdown("**第三道：財報趨勢**")
            rev_yoy_min  = st.number_input("月營收 YoY% >",  value=10.0, step=1.0, key="t1_rev")
            eps_yoy_min  = st.number_input("EPS YoY% >",     value=20.0, step=1.0, key="t1_epsy")

    if st.button("🚀 開始掃描", type="primary", use_container_width=True, key="t1_scan"):
        # ── 載入所有資料
        with st.spinner("載入資料中..."):
            df_si, ok_si = get_stock_info()
            df_fin, ok_fin = get_financials()
            df_chips, ok_chips = get_chips()

        if not (ok_fin and ok_chips):
            st.error("❌ CSV 資料不足，請先執行 update_data.py 並推送 GitHub")
        else:
            prog = st.progress(0)
            results = []

            name_col = "origin_name" if "origin_name" in df_fin.columns else df_fin.columns[2] if len(df_fin.columns) > 2 else "name"

            # ── 依掃描範圍決定股票池
            all_fin_ids = df_fin["stock_id"].dropna().unique().tolist()

            if rng_type == "📂 產業分類":
                stock_ids = [s for s in scan_pool_ids if s in all_fin_ids]
            elif rng_type == "🔢 股號開頭":
                stock_ids = [s for s in all_fin_ids if s.startswith(prefix_digit)]
            elif rng_type == "✏️ 自訂代號":
                stock_ids = [s for s in scan_pool_ids if s in all_fin_ids]
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
                df_f = df_fin[df_fin["stock_id"] == sid].copy()
                if df_f.empty:
                    continue

                # EPS
                eps_rows = df_f[df_f[name_col].str.contains("每股盈餘|BasicEPS|EPS", case=False, na=False)]
                eps_vals = pd.to_numeric(eps_rows["value"], errors="coerce").dropna()
                eps_ttm  = eps_vals.tail(4).sum() if len(eps_vals) >= 4 else np.nan

                # 毛利率
                gm_rows  = df_f[df_f[name_col].str.contains("毛利率|GrossMargin", case=False, na=False)]
                gm_vals  = pd.to_numeric(gm_rows["value"], errors="coerce").dropna()
                gm_latest= float(gm_vals.iloc[-1]) if not gm_vals.empty else np.nan

                # P/E（從 K 線近期算，或用預設值 nan）
                pe_val = np.nan
                df_prc, ok_prc = load_price_csv(sid)
                if ok_prc and not df_prc.empty and not np.isnan(eps_ttm) and eps_ttm > 0:
                    last_close = float(df_prc["Close"].iloc[-1]) if "Close" in df_prc.columns else np.nan
                    if not np.isnan(last_close):
                        pe_val = last_close / eps_ttm * 4  # 年化近似

                # 第一道判斷
                p1_eps = (not np.isnan(eps_ttm)) and eps_ttm > eps_min
                p1_pe  = np.isnan(pe_val) or pe_val < pe_max   # PE 缺失視為通過
                p1_gm  = (not np.isnan(gm_latest)) and gm_latest > gm_min
                pass1  = p1_eps and p1_pe and p1_gm
                if not pass1:
                    continue

                # ─── 第二道：籌碼＋技術（6 項評分）
                df_c  = df_chips[df_chips["stock_id"] == sid]
                score2 = 0
                s2 = {}

                # 融資5日變動
                margin_rows = df_c[df_c.get("source", pd.Series()) == "margin"] if "source" in df_c.columns else df_c
                mg_chg = np.nan
                mg_col = next((c for c in margin_rows.columns if "TodayBalance" in c), None)
                if mg_col and not margin_rows.empty:
                    bal = pd.to_numeric(margin_rows[mg_col], errors="coerce").dropna()
                    if len(bal) >= 5:
                        mg_chg = float((bal.iloc[-1] - bal.iloc[-5]) / max(abs(float(bal.iloc[-5])), 1) * 100)
                s2["融資5日變動"] = (not np.isnan(mg_chg)) and mg_chg < mg_max
                if s2["融資5日變動"]: score2 += 1

                # 法人買超比例
                inst_rows = df_c[df_c.get("source", pd.Series()) == "institutional"] if "source" in df_c.columns else pd.DataFrame()
                if inst_rows.empty and "net" in df_c.columns:
                    inst_rows = df_c
                inst_buy_pct = np.nan
                if not inst_rows.empty and "net" in inst_rows.columns:
                    net_5 = pd.to_numeric(inst_rows.groupby("date")["net"].sum().iloc[-5:].sum() if "date" in inst_rows.columns else pd.Series([0]), errors="coerce")
                    total_v = abs(pd.to_numeric(inst_rows.get("buy", 0), errors="coerce").sum() + pd.to_numeric(inst_rows.get("sell", 0), errors="coerce").sum())
                    inst_buy_pct = float(net_5.sum() / max(total_v, 1) * 100) if total_v > 0 else 0
                s2["法人買超"] = (not np.isnan(inst_buy_pct)) and inst_buy_pct > inst_min_pct
                if s2["法人買超"]: score2 += 1

                # 大戶持股（shareholder CSV）
                df_sh, ok_sh = get_shareholder(sid)
                big_rising = False
                if ok_sh and not df_sh.empty:
                    lv_col  = next((c for c in df_sh.columns if "level" in c.lower() or "Level" in c), None)
                    pct_col = next((c for c in df_sh.columns if "percent" in c.lower()), None)
                    if lv_col and pct_col:
                        big_kw = ["50000","100000","200000","400000","over"]
                        is_big = df_sh[lv_col].astype(str).str.contains("|".join(big_kw), na=False)
                        big_series = df_sh[is_big].groupby("date")[pct_col].sum()
                        big_series = pd.to_numeric(big_series, errors="coerce").dropna()
                        if len(big_series) >= 2:
                            big_rising = float(big_series.iloc[-1]) > float(big_series.iloc[-2])
                s2["大戶持股上升"] = big_rising
                if big_rising: score2 += 1

                # MA20 乖離
                ma20_ok = False
                if ok_prc and not df_prc.empty:
                    prc_c = df_prc["Close"].astype(float)
                    if len(prc_c) >= 20:
                        ma20    = float(prc_c.rolling(20).mean().iloc[-1])
                        last_p  = float(prc_c.iloc[-1])
                        bias    = (last_p - ma20) / ma20 * 100
                        ma20_ok = (last_p >= ma20) and (abs(bias) < bias_max)
                s2["MA20乖離<5%"] = ma20_ok
                if ma20_ok: score2 += 1

                # 窒息量（量比 < vol_max_r）
                vol_ok = False
                if ok_prc and not df_prc.empty and "Volume" in df_prc.columns:
                    vol = df_prc["Volume"].astype(float)
                    if len(vol) >= 5:
                        vma5  = float(vol.rolling(5).mean().iloc[-1])
                        last_v = float(vol.iloc[-1])
                        vol_ok = (vma5 > 0) and (last_v / vma5 < vol_max_r)
                s2["窒息量"] = vol_ok
                if vol_ok: score2 += 1

                # 近3日低點≥前10日低點
                hl_ok = False
                if ok_prc and not df_prc.empty and "Low" in df_prc.columns:
                    lo = df_prc["Low"].astype(float)
                    if len(lo) >= 13:
                        hl_ok = float(lo.iloc[-3:].min()) >= float(lo.iloc[-13:-3].min())
                s2["低點墊高"] = hl_ok
                if hl_ok: score2 += 1

                # ─── 第三道：財報趨勢（3 項評分）
                score3 = 0
                s3 = {}

                # 近3個月月營收 YoY > rev_yoy_min
                rev_rows = df_f[df_f[name_col].str.contains("營業收入|Revenue", case=False, na=False)]
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
                    m = df_si[df_si["stock_id"] == sid]
                    if not m.empty:
                        sid_name = str(m["stock_name"].iloc[0])

                results.append({
                    "代號":    sid,
                    "名稱":    sid_name,
                    "EPS_TTM": round(eps_ttm,  2) if not np.isnan(eps_ttm)  else None,
                    "P/E":     round(pe_val,   1) if not np.isnan(pe_val)   else None,
                    "毛利率%": round(gm_latest, 1) if not np.isnan(gm_latest) else None,
                    "法人買超%": round(inst_buy_pct,1) if not np.isnan(inst_buy_pct) else None,
                    "融資5日變動%": round(mg_chg,1) if not np.isnan(mg_chg) else None,
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

    # ── 顯示結果
    if st.session_state.get("scan_done") and st.session_state.get("scan_results"):
        results = st.session_state["scan_results"]
        df_res  = pd.DataFrame(results)

        # ── 篩選排序
        rc1, rc2, rc3 = st.columns(3)
        min_chip  = rc1.slider("籌碼得分 ≥", 0, 6, 3, key="r_chip")
        min_fin   = rc2.slider("財報得分 ≥", 0, 3, 1, key="r_fin")
        sort_by   = rc3.selectbox("排序依據",
            ["總得分","EPS_TTM","毛利率%","籌碼得分","財報得分"], key="r_sort")

        df_show = df_res[
            (df_res["籌碼得分"] >= min_chip) &
            (df_res["財報得分"] >= min_fin)
        ].sort_values(sort_by, ascending=False)

        st.markdown(f"""
        <div class='infobox'>
            基本面母體：<b style='color:#e8f4fd;'>{len(df_res)}</b> 檔 ｜
            篩選後：<b style='color:#00e676;'>{len(df_show)}</b> 檔 ｜
            點擊「加入監控」追蹤個股
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
            st.dataframe(
                df_table.style
                .background_gradient(subset=["總得分"], cmap="YlGn")
                .format({
                    "EPS_TTM":"{:.2f}", "P/E":"{:.1f}",
                    "毛利率%":"{:.1f}", "法人買超%":"{:.1f}",
                    "融資5日變動%":"{:.1f}",
                }, na_rep="—"),
                width='stretch', height=360,
            )

        # ── 個股評分卡 + 加入監控
        if not df_show.empty:
            st.markdown("<div class='sec-title'>📋 個股評分卡</div>",
                        unsafe_allow_html=True)
            for _, row in df_show.head(10).iterrows():
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
                            use_container_width=True,
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
with tab2:
    st.markdown("<div class='sec-title'>🚨 持股監控 · 即時防守 ＋ 籌碼 ＋ 基本面</div>",
                unsafe_allow_html=True)

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
        # 選擇監控標的
        wl_options = [f"{w['id']} {w['name']}" for w in wl]
        selected   = st.selectbox("選擇監控標的", wl_options, key="t2_sel")
        sid_watch  = selected.split()[0]
        name_watch = " ".join(selected.split()[1:])

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
            chg_s  = "up" if chg >= 0 else "down"

            # ── KPI 列
            kpi_cols = st.columns(6)
            mcard(kpi_cols[0], "收盤價",   f"{lt['Close']:.1f}",         chg_s)
            mcard(kpi_cols[1], "漲跌幅",   f"{'▲' if chg>=0 else '▼'}{abs(chg):.2f}%", chg_s)
            mcard(kpi_cols[2], f"MA{MA_S}", f"{lt[f'MA{MA_S}']:.1f}",     "")
            mcard(kpi_cols[3], f"MA{MA_M}", f"{lt[f'MA{MA_M}']:.1f}",     "")
            mcard(kpi_cols[4], "K值",       f"{lt['K']:.1f}",             "")
            mcard(kpi_cols[5], "D值",       f"{lt['D']:.1f}",             "")
            st.markdown("<br>", unsafe_allow_html=True)

            # ══════════════════════════════════════════════
            # 子模組 1：即時防守
            # ══════════════════════════════════════════════
            st.markdown("<div class='sec-title'>🛡️ 即時防守警示</div>",
                        unsafe_allow_html=True)

            alerts = []
            if lt["Close"] < lt[f"MA{MA_S}"]:
                alerts.append(f"股價跌破 MA{MA_S}（{lt[f'MA{MA_S}']:.1f}）")
            if lt["Close"] < lt.get("VWAP", float("inf")):
                alerts.append(f"跌破日內 VWAP（{lt.get('VWAP',0):.1f}）")
            kd_dead = (lt["K"] < lt["D"]) and (lt["K"] > 70) and (pv["K"] >= pv["D"])
            if kd_dead:
                alerts.append(f"KD 高檔死叉（K={lt['K']:.1f}）")

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

            # K線主圖
            mc1, mc2 = st.columns([3, 1])
            with mc1:
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    row_heights=[.7, .3], vertical_spacing=.04)
                fig.add_trace(go.Candlestick(
                    x=df_ind.index,
                    open=df_ind["Open"], high=df_ind["High"],
                    low=df_ind["Low"],   close=df_ind["Close"],
                    increasing_line_color="#00e676",
                    decreasing_line_color="#ff5252",
                    name="K線", showlegend=False,
                ), row=1, col=1)
                for mw, mc in [(MA_S,"#ff9800"),(MA_M,"#00d4ff"),(MA_L,"#e040fb")]:
                    fig.add_trace(go.Scatter(
                        x=df_ind.index, y=df_ind[f"MA{mw}"],
                        mode="lines", name=f"MA{mw}",
                        line=dict(color=mc, width=1.5),
                    ), row=1, col=1)
                fig.add_trace(go.Bar(
                    x=df_ind.index, y=df_ind["Volume"],
                    marker_color=["#00e676" if c >= o else "#ff5252"
                                  for c, o in zip(df_ind["Close"], df_ind["Open"])],
                    opacity=.5, showlegend=False, name="量",
                ), row=2, col=1)
                fig.update_layout(**base_layout(f"{name_watch} 日線走勢", 460),
                                  xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, width='stretch')
            with mc2:
                # KD 圖
                kdt = df_ind.tail(60)
                fkd = go.Figure()
                fkd.add_trace(go.Scatter(x=kdt.index, y=kdt["K"],
                    mode="lines", name="K", line=dict(color="#ff9800",width=1.5)))
                fkd.add_trace(go.Scatter(x=kdt.index, y=kdt["D"],
                    mode="lines", name="D", line=dict(color="#00d4ff",width=1.5)))
                fkd.add_hrect(y0=80, y1=100, fillcolor="rgba(255,82,82,.08)",  line_width=0)
                fkd.add_hrect(y0=0,  y1=20,  fillcolor="rgba(0,230,118,.08)", line_width=0)
                fkd.add_hline(y=80, line_dash="dot", line_color="#ff5252", line_width=1)
                fkd.add_hline(y=20, line_dash="dot", line_color="#00e676", line_width=1)
                fkd.update_layout(**base_layout("KD（近60日）", 230))
                st.plotly_chart(fkd, width='stretch')

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

                margin_w = df_c_w[df_c_w.get("source", pd.Series(dtype=str)) == "margin"] \
                           if "source" in df_c_w.columns else pd.DataFrame()

                # 警告偵測
                if not inst_w.empty and "net" in inst_w.columns and not margin_w.empty:
                    mg_col_w = next((c for c in margin_w.columns if "TodayBalance" in c), None)
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
                    mg_col_w = next((c for c in margin_w.columns if "TodayBalance" in c), None)
                    if mg_col_w and "date" in margin_w.columns:
                        mw2 = margin_w.set_index("date")
                        fig2.add_trace(go.Scatter(
                            x=mw2.index,
                            y=pd.to_numeric(mw2[mg_col_w], errors="coerce") / 1e8,
                            mode="lines", name="融資餘額(億)",
                            line=dict(color="#ff9800", width=2),
                        ), secondary_y=True)
                fig2.update_layout(**base_layout("三大法人買賣超（萬股）＋融資餘額", 380))
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
                        marker_color=["#00e676" if v >= 0 else "#ff5252"
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
# ▌ TAB 3：大盤預警
# ──────────────────────────────────────────────────────────────
with tab3:
    st.markdown("<div class='sec-title'>📡 大盤預警 · 期貨引擎 ＋ 蒙格行為學 ＋ AI診斷</div>",
                unsafe_allow_html=True)

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

        # 大台外資未平倉
        inst_df = df_fut[df_fut.get("source","") == "institutional"] \
                  if "source" in df_fut.columns else df_fut

        tx_df = inst_df[inst_df.get("contract","") == "TX"] \
                if "contract" in inst_df.columns else pd.DataFrame()
        if not tx_df.empty:
            ld = tx_df["date"].max()
            result["data_date"] = str(ld)[:10]
            row = tx_df[(tx_df["date"] == ld) &
                        (tx_df.get("name","").astype(str).str.contains("外資", na=False))]
            if not row.empty:
                lc = next((c for c in row.columns if "long_open_interest_balance" in c), None)
                sc = next((c for c in row.columns if "short_open_interest_balance" in c), None)
                if lc and sc:
                    try:
                        result["tx_foreign"] = int(float(row[lc].values[0])) - \
                                               int(float(row[sc].values[0]))
                        result["is_real"] = True
                    except:
                        pass

        # 小台三大法人
        mtx_df = inst_df[inst_df.get("contract","") == "MTX"] \
                 if "contract" in inst_df.columns else pd.DataFrame()
        if not mtx_df.empty:
            ld = mtx_df["date"].max()
            lc = next((c for c in mtx_df.columns if "long_open_interest_balance" in c), None)
            sc = next((c for c in mtx_df.columns if "short_open_interest_balance" in c), None)
            if lc and sc:
                for kw, key in [("自營","mtx_dealer"),("投信","mtx_trust"),("外資","mtx_foreign")]:
                    r = mtx_df[(mtx_df["date"] == ld) &
                               (mtx_df.get("name","").astype(str).str.contains(kw, na=False))]
                    if not r.empty:
                        try:
                            result[key] = int(float(r[lc].values[0])) - \
                                          int(float(r[sc].values[0]))
                            result["is_real"] = True
                        except:
                            pass

        # 小台全市場未平倉
        daily_df = df_fut[df_fut.get("source","") == "daily"] \
                   if "source" in df_fut.columns else pd.DataFrame()
        if not daily_df.empty:
            ld2  = daily_df["date"].max()
            oi_c = next((c for c in daily_df.columns if "open_interest" in c.lower()), None)
            if oi_c:
                try:
                    result["mtx_oi"] = int(
                        pd.to_numeric(
                            daily_df[daily_df["date"] == ld2][oi_c], errors="coerce"
                        ).sum()
                    )
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
        mtx_dealer  = st.number_input("自營商淨額（口）", value=int(chips["mtx_dealer"]  or -8500),  step=100)
        mtx_trust   = st.number_input("投信淨額（口）",   value=int(chips["mtx_trust"]   or -3200),  step=100)
        mtx_foreign = st.number_input("外資淨額（口）",   value=int(chips["mtx_foreign"] or -18300), step=100)
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
    mcard(pk4, "散戶多空比", f"{retail_ratio:+.1f}%",
          "down" if retail_ratio > 10 else "up" if retail_ratio < 0 else "")

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

    bc1, bc2 = st.columns(2)
    with bc1:
        st.markdown("<span style='color:#ffab40;font-size:.76rem;font-weight:600;'>📣 市場情緒面</span>",
                    unsafe_allow_html=True)
        b1  = st.checkbox("市場充斥「這次不一樣、估值重構」言論")
        b2  = st.checkbox("散戶對利空麻木，認為拉回就是買點")
        b3  = st.checkbox("強勢股（AI概念）出現大量散戶追捧")
        b4  = st.checkbox("媒體頻繁出現「萬八萬九」類標題")
        b5  = st.checkbox("身邊非投資人士開始詢問如何開戶")
        b6  = st.checkbox("散戶急於向下攤平，加碼重挫個股")
    with bc2:
        st.markdown("<span style='color:#e040fb;font-size:.76rem;font-weight:600;'>📊 技術籌碼面</span>",
                    unsafe_allow_html=True)
        b7  = st.checkbox("融資餘額創近期新高或大量新增信用帳戶")
        b8  = st.checkbox("指數創新高但多數個股跌破均線（背離）")
        b9  = st.checkbox("外資連續多日在現貨大額賣超")
        b10 = st.checkbox("量縮價穩假象（成交量萎縮指數卻在高點）")
        b11 = st.checkbox("權值股無量上攻後急跌，籌碼鬆動")
        b12 = st.checkbox("期貨逆價差擴大（法人對沖意願增強）")

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

        ai_text = (
            f"**📍 當前階段{note}：{stage}**\n\n"
            f"> {desc}\n\n"
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
