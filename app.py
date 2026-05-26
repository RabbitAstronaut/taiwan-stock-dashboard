import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════
# 頁面設定
# ══════════════════════════════════════════════
st.set_page_config(
    page_title="台股全週期量化交易儀表板",
    page_icon="📈", layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;700&display=swap');
html,body,[class*="css"]{font-family:"Noto Sans TC",sans-serif;}
.stApp{background:linear-gradient(135deg,#0a0e1a 0%,#0d1321 50%,#0a1628 100%);}
.main-header{background:linear-gradient(90deg,#0f2027,#203a43,#2c5364);border-radius:12px;padding:18px 26px;margin-bottom:14px;border-left:4px solid #00d4ff;box-shadow:0 4px 20px rgba(0,212,255,0.15);}
.main-header h1{color:#e8f4fd;font-size:1.45rem;font-weight:700;margin:0;letter-spacing:1px;}
.main-header p{color:#7fb3d3;margin:4px 0 0 0;font-size:0.78rem;}
.metric-card{background:linear-gradient(135deg,#0f2027,#162535);border:1px solid #1e3a5f;border-radius:10px;padding:13px 14px;text-align:center;transition:transform 0.2s;}
.metric-card:hover{transform:translateY(-2px);}
.metric-label{color:#7fb3d3;font-size:0.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px;}
.metric-value{color:#e8f4fd;font-size:1.2rem;font-weight:700;font-family:"JetBrains Mono",monospace;}
.metric-value.up{color:#00e676;}.metric-value.down{color:#ff5252;}
.signal-green{background:linear-gradient(135deg,#0a3d0a,#0f5c0f);border:1px solid #00e676;border-radius:8px;padding:12px 14px;color:#00e676;font-weight:600;text-align:center;}
.signal-red{background:linear-gradient(135deg,#3d0a0a,#5c0f0f);border:1px solid #ff5252;border-radius:8px;padding:12px 14px;color:#ff5252;font-weight:600;text-align:center;animation:pulse 2s infinite;}
.warning-banner{background:linear-gradient(90deg,#2d1b00,#3d2500);border:1px solid #ffab40;border-left:4px solid #ffab40;border-radius:8px;padding:10px 14px;color:#ffab40;font-weight:600;margin:6px 0;}
.section-title{color:#00d4ff;font-size:0.88rem;font-weight:600;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #1e3a5f;padding-bottom:7px;margin:14px 0 10px 0;}
.chip-green{display:inline-block;background:rgba(0,230,118,0.1);border:1px solid #00e676;color:#00e676;border-radius:14px;padding:2px 10px;font-size:0.74rem;margin:2px;font-family:"JetBrains Mono",monospace;}
.chip-blue{display:inline-block;background:rgba(0,212,255,0.08);border:1px solid #00d4ff;color:#00d4ff;border-radius:14px;padding:2px 10px;font-size:0.74rem;margin:2px;font-family:"JetBrains Mono",monospace;}
.infobox{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:0.78rem;color:#7fb3d3;line-height:1.6;}
.api-badge-real{display:inline-block;background:rgba(0,230,118,0.12);border:1px solid #00e676;color:#00e676;border-radius:4px;padding:1px 7px;font-size:0.68rem;margin-left:6px;}
.api-badge-sim{display:inline-block;background:rgba(255,171,64,0.12);border:1px solid #ffab40;color:#ffab40;border-radius:4px;padding:1px 7px;font-size:0.68rem;margin-left:6px;}
@keyframes pulse{0%{box-shadow:0 0 8px rgba(255,82,82,0.2);}50%{box-shadow:0 0 18px rgba(255,82,82,0.5);}100%{box-shadow:0 0 8px rgba(255,82,82,0.2);}}
.stTabs [data-baseweb="tab-list"]{gap:4px;background:#0d1321;padding:4px;border-radius:10px;}
.stTabs [data-baseweb="tab"]{color:#7fb3d3;background:transparent;border-radius:8px;font-size:0.84rem;padding:7px 15px;}
.stTabs [aria-selected="true"]{color:#00d4ff!important;background:linear-gradient(135deg,#0f2027,#162535)!important;border-bottom:2px solid #00d4ff!important;}
div[data-testid="stExpander"]{background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;}

/* ── 按鈕文字顏色修正（解決 Windows 看不到字的問題）── */
.stButton > button {
    color: #ffffff !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
}
/* 主要按鈕（藍色）*/
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #0066cc, #0044aa) !important;
    border: 1px solid #00d4ff !important;
    color: #ffffff !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #0077dd, #0055bb) !important;
    color: #ffffff !important;
}
/* 一般按鈕（深色）*/
.stButton > button[kind="secondary"],
.stButton > button:not([kind]) {
    background: linear-gradient(135deg, #162535, #1e3a5f) !important;
    border: 1px solid #2a5080 !important;
    color: #e8f4fd !important;
}
.stButton > button:hover {
    border-color: #00d4ff !important;
    color: #ffffff !important;
}
/* 下拉選單文字 */
.stSelectbox label, .stMultiSelect label,
.stSlider label, .stRadio label,
.stTextInput label, .stTextArea label,
.stNumberInput label {
    color: #b0cce0 !important;
    font-size: 0.82rem !important;
}
/* 下拉選單選項文字 */
.stSelectbox div[data-baseweb="select"] span,
.stSelectbox div[data-baseweb="select"] div {
    color: #e8f4fd !important;
}
/* Toggle 文字 */
.stCheckbox label, .stToggle label {
    color: #c8dff0 !important;
}
/* Expander 標題文字 */
div[data-testid="stExpander"] summary {
    color: #b0cce0 !important;
    font-weight: 500 !important;
}
/* Radio 選項文字 */
.stRadio div[role="radiogroup"] label {
    color: #c8dff0 !important;
}
/* 數字輸入框文字 */
.stNumberInput input {
    color: #e8f4fd !important;
    background: #0f1e30 !important;
}
/* 文字輸入框 */
.stTextInput input, .stTextArea textarea {
    color: #e8f4fd !important;
    background: #0f1e30 !important;
}
/* Slider 數值文字 */
.stSlider div[data-testid="stTickBarMin"],
.stSlider div[data-testid="stTickBarMax"] {
    color: #7fb3d3 !important;
}
/* 一般文字 */
.stMarkdown p, .stMarkdown li {
    color: #c8dff0 !important;
}
/* Caption 文字 */
.stCaption {
    color: #7fb3d3 !important;
}
/* Progress bar */
.stProgress > div > div {
    background: linear-gradient(90deg, #00d4ff, #00e676) !important;
}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════
# 共用設定
# ══════════════════════════════════════════════
PLOT_BG = "rgba(10,14,26,0)"; PAPER_BG = "rgba(10,14,26,0)"
GRID_COLOR = "#1e3a5f"; TEXT_COLOR = "#7fb3d3"
FM_BASE = "https://api.finmindtrade.com/api/v4/data"

def base_layout(title="", height=400):
    return dict(
        title=dict(text=title, font=dict(color="#e8f4fd", size=13)),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
        font=dict(color=TEXT_COLOR, family="JetBrains Mono,Noto Sans TC", size=11),
        height=height, margin=dict(l=48, r=18, t=44, b=34),
        xaxis=dict(gridcolor=GRID_COLOR, linecolor=GRID_COLOR, showgrid=True),
        yaxis=dict(gridcolor=GRID_COLOR, linecolor=GRID_COLOR, showgrid=True),
        legend=dict(bgcolor="rgba(10,14,26,0.8)", bordercolor="#1e3a5f", borderwidth=1),
    )

def _mcard(col, label, val_str, color="#e8f4fd"):
    col.markdown(
        f"<div class='metric-card'><div class='metric-label'>{label}</div>"
        f"<div class='metric-value' style='color:{color};font-size:1.05rem;'>{val_str}</div></div>",
        unsafe_allow_html=True
    )

# ══════════════════════════════════════════════
# FinMind API 核心函式
# ══════════════════════════════════════════════
def fm_request(dataset, data_id=None, start_date=None, end_date=None, token=None):
    """FinMind API 統一請求入口，含錯誤處理"""
    params = {"dataset": dataset}
    if data_id:   params["data_id"]    = data_id
    if start_date: params["start_date"] = start_date
    if end_date:   params["end_date"]   = end_date
    if token:      params["token"]      = token
    try:
        r = requests.get(FM_BASE, params=params, timeout=20)
        j = r.json()
        if j.get("status") == 200 and isinstance(j.get("data"), list):
            return pd.DataFrame(j["data"]), True
        return pd.DataFrame(), False
    except Exception:
        return pd.DataFrame(), False

# ── 1. 股票清單
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_list(token=""):
    df, ok = fm_request("TaiwanStockInfo", token=token or None)
    if ok and not df.empty:
        df = df[df["type"].isin(["twse","tpex"])].copy()
        df = df[~df["stock_id"].str.startswith("00")]  # 排除ETF
        df = df[df["stock_id"].str.match(r"^[0-9]{4}$")]
        df["label"] = df["stock_id"] + " " + df["stock_name"]
        df["market"] = df["type"].map({"twse":"上市","tpex":"上櫃"})
        df["yf_ticker"] = df.apply(
            lambda r: f"{r['stock_id']}.TW" if r["type"]=="twse" else f"{r['stock_id']}.TWO", axis=1)
        return df[["stock_id","stock_name","market","yf_ticker","label"]], True
    return pd.DataFrame(), False

# ── 2. 三大法人買賣超
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_institutional(stock_id, start_date, token=""):
    df, ok = fm_request("TaiwanStockInstitutionalInvestorsBuySell",
                        data_id=stock_id, start_date=start_date, token=token or None)
    if ok and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
        return df, True
    return pd.DataFrame(), False

# ── 3. 融資融券
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_margin(stock_id, start_date, token=""):
    df, ok = fm_request("TaiwanStockMarginPurchaseShortSale",
                        data_id=stock_id, start_date=start_date, token=token or None)
    if ok and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        return df, True
    return pd.DataFrame(), False

# ── 4. 財務報表
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_financials(stock_id, start_date, token=""):
    df, ok = fm_request("TaiwanStockFinancialStatements",
                        data_id=stock_id, start_date=start_date, token=token or None)
    if ok and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        return df, True
    return pd.DataFrame(), False

# ── 5. 期貨法人未平倉
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_futures_inst(contract, start_date, token=""):
    df, ok = fm_request("TaiwanFuturesInstitutionalInvestors",
                        data_id=contract, start_date=start_date, token=token or None)
    if ok and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        return df, True
    return pd.DataFrame(), False

# ── 6. 期貨每日資料（全市場未平倉）
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_futures_daily(contract, start_date, token=""):
    df, ok = fm_request("TaiwanFuturesDaily",
                        data_id=contract, start_date=start_date, token=token or None)
    if ok and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        return df, True
    return pd.DataFrame(), False

# ── 期貨籌碼解析
def parse_futures_chips(token=""):
    """解析大台/小台法人未平倉，回傳 dict"""
    start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    result = {
        "tx_foreign": None, "mtx_dealer": None,
        "mtx_trust": None,  "mtx_foreign": None, "mtx_oi": None,
        "data_date": None,  "is_real": False,
    }

    # 大台外資
    df_tx, ok_tx = fetch_futures_inst("TX", start, token)
    if ok_tx and not df_tx.empty:
        latest_date = df_tx["date"].max()
        result["data_date"] = latest_date.strftime("%Y-%m-%d")
        row = df_tx[(df_tx["date"]==latest_date) &
                    (df_tx["name"].str.contains("外資|Foreign", na=False))]
        if not row.empty:
            try:
                long_col  = [c for c in row.columns if "long_open_interest_balance" in c][0]
                short_col = [c for c in row.columns if "short_open_interest_balance" in c][0]
                result["tx_foreign"] = int(row[long_col].values[0]) - int(row[short_col].values[0])
            except: pass

    # 小台三大法人
    df_mtx, ok_mtx = fetch_futures_inst("MTX", start, token)
    if ok_mtx and not df_mtx.empty:
        latest_date = df_mtx["date"].max()
        if result["data_date"] is None:
            result["data_date"] = latest_date.strftime("%Y-%m-%d")
        try:
            long_col  = [c for c in df_mtx.columns if "long_open_interest_balance" in c][0]
            short_col = [c for c in df_mtx.columns if "short_open_interest_balance" in c][0]
            for inst, key in [("自營|Dealer","mtx_dealer"),("投信|Investment","mtx_trust"),("外資|Foreign","mtx_foreign")]:
                row = df_mtx[(df_mtx["date"]==latest_date) &
                             (df_mtx["name"].str.contains(inst.split("|")[0], na=False))]
                if not row.empty:
                    result[key] = int(row[long_col].values[0]) - int(row[short_col].values[0])
        except: pass

    # 小台全市場未平倉
    df_oi, ok_oi = fetch_futures_daily("MTX", start, token)
    if ok_oi and not df_oi.empty:
        try:
            latest = df_oi.sort_values("date").iloc[-1]
            # 近月合約（無到期日欄位時取最大未平倉）
            oi_col = [c for c in df_oi.columns if "open_interest" in c.lower()][0]
            result["mtx_oi"] = int(df_oi[df_oi["date"]==df_oi["date"].max()][oi_col].sum())
        except: pass

    # 判斷是否取得真實資料
    if any(v is not None for v in [result["tx_foreign"], result["mtx_dealer"]]):
        result["is_real"] = True

    return result

# ══════════════════════════════════════════════
# yfinance 股價資料（三層備援）
# ══════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(tk, pd_):
    import requests as req_mod
    def _clean(df):
        if df is None or df.empty: return pd.DataFrame()
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=["Close","Open","High","Low","Volume"])
        return df if len(df) >= 10 else pd.DataFrame()

    for attempt in range(3):
        try:
            time.sleep(attempt * 1.5)
            df = yf.download(tk, period=pd_, auto_adjust=True, progress=False, timeout=15)
            df = _clean(df)
            if not df.empty: return df
        except: pass

    alt_tk = tk.replace(".TW",".TWO") if tk.endswith(".TW") else tk.replace(".TWO",".TW")
    for attempt in range(2):
        try:
            time.sleep(1 + attempt)
            df = yf.download(alt_tk, period=pd_, auto_adjust=True, progress=False, timeout=15)
            df = _clean(df)
            if not df.empty: return df
        except: pass

    code = tk.replace(".TW","").replace(".TWO","")
    try:
        headers = {"User-Agent":"Mozilla/5.0"}
        rows_all = []
        from datetime import date
        today = date.today()
        for m_offset in range(12):
            mo = today.month - m_offset; yr = today.year
            while mo <= 0: mo += 12; yr -= 1
            yyyymm = f"{yr}{mo:02d}01"
            url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={yyyymm}&stockNo={code}"
            r = req_mod.get(url, headers=headers, timeout=10)
            if r.status_code != 200: continue
            jd = r.json()
            if jd.get("stat") != "OK": continue
            for row in jd.get("data",[]):
                try:
                    parts = row[0].split("/"); yr_ad = int(parts[0])+1911
                    dt = pd.Timestamp(f"{yr_ad}-{parts[1]}-{parts[2]}")
                    rows_all.append({"Date":dt,
                        "Open":float(row[3].replace(",","")), "High":float(row[4].replace(",","")),
                        "Low":float(row[5].replace(",","")),  "Close":float(row[6].replace(",","")),
                        "Volume":float(row[1].replace(",",""))*1000})
                except: continue
            time.sleep(0.3)
        if rows_all:
            df2 = pd.DataFrame(rows_all).set_index("Date").sort_index()
            if len(df2) >= 10: return df2
    except: pass
    return pd.DataFrame()

def add_indicators(df, ws, wm, wl):
    for w in [ws,wm,wl]:
        df[f"MA{w}"] = df["Close"].rolling(w).mean()
    lm = df["Low"].rolling(9).min(); hm = df["High"].rolling(9).max()
    rsv = (df["Close"]-lm)/(hm-lm+1e-9)*100
    k,d = [50.0],[50.0]
    for r in rsv.iloc[1:]:
        k.append(k[-1]*2/3+r*1/3); d.append(d[-1]*2/3+k[-1]*1/3)
    df["K"]=k; df["D"]=d
    return df

# ══════════════════════════════════════════════
# 產業群組（靜態，作為 FinMind 股票清單的分類標籤）
# ══════════════════════════════════════════════
SECTOR_MAP = {
    "🔬 半導體｜IC設計": {
        "color":"#00d4ff","desc":"IC設計、類比IC、混合訊號IC、IP授權、EDA",
        "ids":["2454","2379","3034","2303","2449","2388","3515","5347","4966","3443",
               "6770","2344","2408","3653","6523","3661","6415","3035","2363","6533",
               "3141","6643","3014","5274","4968","6269","3596","6789","2436","3494",
               "2471","6510","3532","6147","8081","3209","6278","2406","6803","4919",
               "3037","6230","5269","4961","3376","6214","3706","2397","3228","6442"]},
    "⚡ 半導體｜晶圓代工＆封測": {
        "color":"#00bcd4","desc":"晶圓代工、先進封裝、測試、載板、基板",
        "ids":["2330","2337","2325","3711","6274","2368","2351","6257","3016","2455",
               "6271","2441","6239","3105","2329","3530","5483","6488","2383","3038",
               "2475","3260","2340","2393","2409","3481","3691","6146","3057","4142"]},
    "💻 AI伺服器｜雲端運算": {
        "color":"#e040fb","desc":"AI伺服器、雲端基礎設施、散熱、PCB、電源",
        "ids":["2382","2356","2353","2357","6669","3231","2301","2324","3017","2399",
               "3533","6461","3583","6285","3023","2383","3189","5269","4938","3706",
               "3062","2397","5354","2365","3044","3057","6230","3085","6442","6146",
               "2332","3376","6257","2462","6510","3597","2406","6214","3228","2308","3003"]},
    "📱 消費電子｜手機零組件": {
        "color":"#ffab40","desc":"手機、穿戴裝置、鏡頭、聲學元件、連接器",
        "ids":["2317","2354","2498","3008","2439","3406","4958","2327","3036","2429",
               "6278","2474","4961","2421","2393","6120","2308","6277","3376","6415",
               "4906","3028","5371","2049","3017","2365","2364","3034","2332","6285",
               "3059","6271","2340","3030","3023","2351","1590","3533","2460"]},
    "🔋 電動車｜綠能儲能": {
        "color":"#00e676","desc":"電動車、電池、太陽能、儲能、充電設備、被動元件",
        "ids":["2308","6415","5483","6244","1590","1504","1514","1537","8210","1560",
               "2207","2201","2204","1605","1603","1608","1609","1612","5009","1466",
               "1710","1711","3211","6409","3593","3576","3548","2327","2399","6257",
               "3037","1519","1513","1515","1516","1529","1530","1477","3013"]},
    "🌐 網通｜5G基礎建設": {
        "color":"#40c4ff","desc":"電信、網路設備、WiFi、光纖、資安",
        "ids":["2412","4904","3045","2332","2345","3047","6456","4906","3518","6277",
               "3062","6285","6227","3059","6409","3707","4960","6510","3596","2348",
               "6263","6414","3686","3230","3049","3376","6146","3023","3706","2397","6214"]},
    "🏦 金融｜銀行保險券商": {
        "color":"#ffd740","desc":"金控、銀行、保險、證券、票券",
        "ids":["2881","2882","2891","2886","2887","2884","2885","2892","2880","5880",
               "2801","2820","2834","2838","2849","2850","2851","2852","2855","2856",
               "2867","2883","2888","2889","2890","5876","5878","2823","2824","6005",
               "2809","2812","2816","2826","2860"]},
    "🧪 傳統產業｜石化塑膠鋼鐵": {
        "color":"#78909c","desc":"石化、塑膠、鋼鐵、橡膠、化工原料",
        "ids":["6505","1301","1303","1326","1402","2002","1101","1102","2006","2007",
               "2008","2009","2010","2012","2013","2014","2015","1304","1305","1307",
               "1308","1309","1310","1312","1313","1314","1317","1319","1321","2103",
               "1703","1711","1712","1713","1717","1718","1722","1723","1725","1726","1730"]},
    "🏗️ 營建｜不動產": {
        "color":"#ff9800","desc":"建設、營造、房仲、建材",
        "ids":["5522","2528","2534","2511","2597","2515","5533","5536","5546","2543",
               "2535","2536","2537","2538","2540","2542","2545","2546","2547","2548",
               "5512","5515","5519","5521","5523","5525","5531","5534","5538","2501",
               "2502","2504","2505","2506","2509","2514","2516","2520","2524","2525","2526"]},
    "💊 生技醫療｜製藥器材": {
        "color":"#ce93d8","desc":"生技新藥、製藥、醫療器材、CRO",
        "ids":["4743","1789","4144","4147","6446","1760","4174","4162","4141","6547",
               "4106","4108","4119","4121","4123","4126","4128","4130","4133","4148",
               "4152","4160","4163","4168","4171","4175","1777","1701","1733","1762",
               "1784","1786","1788","1790","4116","4117","4118","4209","6194","6245","6409"]},
    "🛒 零售百貨｜電商物流": {
        "color":"#ff6e40","desc":"量販、超商、電商平台、物流、餐飲、觀光",
        "ids":["2912","2903","2915","5904","2910","2905","2908","2911","2914","2923",
               "8044","5903","2718","2719","2720","1210","1215","1216","1217","1218",
               "1219","1225","1227","1229","1230","1232","1233","1234","1236","1256",
               "2712","2717","2723","2726"]},
    "🏭 機械設備｜精密工具機": {
        "color":"#b0bec5","desc":"工具機、精密機械、自動化、機器人",
        "ids":["2049","1590","1560","2059","2061","2062","2063","2064","2065","2201",
               "2204","2207","2208","1580","1582","1583","1584","1585","1586","1589",
               "1591","2014","1513","1515","1516","1519","1520","1521","1522","1524",
               "1525","1526","1527","1528","1530","1531","1532","1533","1535","1536",
               "1538","1541","1542","1543","1545"]},
    "📺 光電面板｜顯示器": {
        "color":"#80cbc4","desc":"面板、背光模組、驅動IC、光學膜",
        "ids":["3481","2409","2475","5371","3008","3406","3691","2383","3028","3049",
               "3059","2455","3031","3033","3034","3040","3041","3042","3046","3048",
               "3050","3051","2340","2393","2460","2461","3530","3550","6277","2384",
               "3032","3043","3052","5274","3596"]},
    "✏️ 自訂股票組合": {
        "color":"#7fb3d3","desc":"手動輸入任意股票代號，不限產業","ids":[]},
}

# ══════════════════════════════════════════════
# 大盤環境自動偵測
# ══════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False)
def detect_market_mode():
    """
    自動偵測大盤環境
    用加權指數 ^TWII 判斷多頭/盤整/弱市
    回傳：(mode_key, mode_label, description, color)
    """
    try:
        df = yf.download("^TWII", period="3mo", auto_adjust=True, progress=False)
        if df.empty: raise Exception("無資料")
        df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
        close  = df["Close"].values.flatten().astype(float)
        ma20   = float(pd.Series(close).rolling(20).mean().iloc[-1])
        ma60   = float(pd.Series(close).rolling(60).mean().iloc[-1])
        last   = float(close[-1])
        prev5  = float(pd.Series(close).rolling(5).mean().iloc[-6]) if len(close)>=6 else ma20
        ma20_trend = ma20 - prev5  # MA20 方向

        bias = (last - ma20) / ma20 * 100

        if last > ma20 and ma20_trend > 0 and bias > 2:
            return ("bull",  "🚀 多頭追蹤", f"加權指數強勢，位於MA20上方 {bias:.1f}%", "#00e676")
        elif abs(bias) <= 3:
            return ("range", "📊 盤整低接", f"加權指數貼近MA20，乖離 {bias:+.1f}%", "#ffab40")
        else:
            return ("bear",  "🛡️ 弱市防守", f"加權指數位於MA20下方 {bias:.1f}%", "#ff5252")
    except:
        return ("range", "📊 盤整低接（預設）", "無法取得大盤資料，使用預設模式", "#ffab40")

# 三種模式的篩選條件預設值
MARKET_MODE_PARAMS = {
    "bull": dict(
        eps_min=5.0,  pe_max=50, gm_min=20,
        margin_max=3.0,  inst_min=3.0,
        bias_max=10.0, vol_max=1.2,
        label="🚀 多頭追蹤",
        desc="法人持續買、均線多頭、量能溫和放大",
    ),
    "range": dict(
        eps_min=3.0,  pe_max=45, gm_min=15,
        margin_max=-1.5, inst_min=5.0,
        bias_max=6.0,  vol_max=0.7,
        label="📊 盤整低接",
        desc="融資減少、量縮回測、貼近均線",
    ),
    "bear": dict(
        eps_min=8.0,  pe_max=35, gm_min=30,
        margin_max=-5.0, inst_min=10.0,
        bias_max=3.0,  vol_max=0.6,
        label="🛡️ 弱市防守",
        desc="籌碼極乾淨、強勢抗跌、外資持續買",
    ),
}

# ══════════════════════════════════════════════
# Session State 初始化
# ══════════════════════════════════════════════
for k,v in {
    "fm_token":"","fm_stock_list":None,"fm_list_ok":False,
    "scan_result":None,"scanned":False,"scanned_group":"",
    "selected_pool":[],"watch_ticker":"2454.TW","watch_name":"聯發科 (2454)",
    "custom_stocks":[],"chip_data":{},"futures_data":{},
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ══════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:10px 0 12px;">
        <div style="font-size:1.8rem;">📈</div>
        <div style="color:#00d4ff;font-size:0.88rem;font-weight:700;letter-spacing:2px;">台股量化儀表板</div>
        <div style="color:#7fb3d3;font-size:0.66rem;margin-top:2px;">QUANT TRADING SYSTEM v4</div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")

    # ── FinMind Token 設定
    st.markdown("<div style='color:#00e676;font-size:0.75rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>🔑 FinMind API Token</div>", unsafe_allow_html=True)
    token_input = st.text_input(
        "Token（免費版可留空）", type="password",
        value=st.session_state.fm_token,
        placeholder="貼上 FinMind token（留空用免費額度）",
        label_visibility="collapsed"
    )
    if token_input != st.session_state.fm_token:
        st.session_state.fm_token = token_input
        st.session_state.fm_stock_list = None

    # 載入股票清單
    if st.button("📥 載入全市場股票清單", use_container_width=True):
        with st.spinner("從 FinMind 載入中..."):
            df_list, ok = fetch_stock_list(st.session_state.fm_token)
        if ok:
            st.session_state.fm_stock_list = df_list
            st.session_state.fm_list_ok    = True
            st.success(f"✅ 載入 {len(df_list)} 檔（上市＋上櫃，已排除ETF）")
        else:
            st.session_state.fm_list_ok = False
            st.warning("⚠️ FinMind 無法連線（IP限制），使用內建靜態股票庫")

    if st.session_state.fm_list_ok and st.session_state.fm_stock_list is not None:
        n = len(st.session_state.fm_stock_list)
        st.markdown(f"<div class='infobox'>FinMind 動態股票池：<b style='color:#00d4ff;'>{n}</b> 檔</div>", unsafe_allow_html=True)

    st.markdown("---")

    # ── STEP 1：選擇群組
    st.markdown("<div style='color:#ffab40;font-size:0.74rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>STEP 1 ｜ 選擇掃描群組</div>", unsafe_allow_html=True)
    group_names  = list(SECTOR_MAP.keys())
    selected_group = st.selectbox("產業類股群組", group_names, help="選擇後預覽股票再執行掃描")
    group_info   = SECTOR_MAP[selected_group]

    if selected_group != "✏️ 自訂股票組合":
        # 若有 FinMind 清單，從中撈出對應股票資訊
        ids = group_info["ids"]
        if st.session_state.fm_list_ok and st.session_state.fm_stock_list is not None:
            df_grp = st.session_state.fm_stock_list[
                st.session_state.fm_stock_list["stock_id"].isin(ids)]
            stocks_in_group = [(r.stock_id, r.stock_name, r.market, r.yf_ticker)
                               for _, r in df_grp.iterrows()]
            src_label = "FinMind 動態"
        else:
            # 靜態對照（只有代號，名稱待掃描時由 yfinance 補）
            stocks_in_group = [(i, i, "上市", f"{i}.TW") for i in ids]
            src_label = "靜態內建"

        st.markdown(
            f"<div class='infobox'><span style='color:{group_info['color']};font-weight:600;'>{selected_group}</span><br>"
            f"{group_info['desc']}<br>"
            f"共 <b style='color:#e8f4fd;'>{len(stocks_in_group)}</b> 檔 "
            f"<span style='background:rgba(0,212,255,0.1);border:1px solid #00d4ff;color:#00d4ff;"
            f"border-radius:4px;padding:1px 6px;font-size:0.66rem;'>{src_label}</span></div>",
            unsafe_allow_html=True
        )
        with st.expander("📋 查看群組股票清單", expanded=False):
            for sid, sname, mkt, _ in stocks_in_group[:30]:
                mc = "#00d4ff" if mkt=="上市" else "#ffab40"
                st.markdown(
                    f"<span style='background:#162535;border-radius:4px;padding:1px 7px;"
                    f"font-size:0.78rem;color:#e8f4fd;font-family:monospace;'>{sid}</span> "
                    f"<span style='color:#e8f4fd;font-size:0.8rem;'>{sname}</span> "
                    f"<span style='color:{mc};font-size:0.68rem;'>{mkt}</span>",
                    unsafe_allow_html=True
                )
            if len(stocks_in_group) > 30:
                st.markdown(f"<span style='color:#546e7a;font-size:0.74rem;'>... 還有 {len(stocks_in_group)-30} 檔</span>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#7fb3d3;font-size:0.76rem;margin-bottom:6px;'>輸入股票代號（逗號或換行分隔）</div>", unsafe_allow_html=True)
        custom_input = st.text_area("股票代號", placeholder="例：2330,2454,2308", height=90, label_visibility="collapsed")
        if custom_input.strip():
            raw = custom_input.replace("\n",",").replace("，",",").replace(" ","")
            codes = [c.strip() for c in raw.split(",") if c.strip().isdigit() and len(c.strip())==4]
            stocks_in_group = [(c, c, "自訂", f"{c}.TW") for c in codes]
            st.session_state.custom_stocks = stocks_in_group
            st.markdown(f"<div class='infobox'>已解析 <b style='color:#00e676;'>{len(codes)}</b> 檔</div>", unsafe_allow_html=True)
        else:
            stocks_in_group = []

    # 合併額外群組
    with st.expander("➕ 合併多個群組掃描", expanded=False):
        extra_groups = st.multiselect("額外加入群組",
            [g for g in group_names if g != selected_group and g != "✏️ 自訂股票組合"],
            label_visibility="collapsed")

    # 最終掃描清單
    def build_scan_list():
        seen = {}
        for sid, sname, mkt, ytk in stocks_in_group:
            if sid not in seen: seen[sid] = (sid,sname,mkt,ytk)
        for g in extra_groups:
            for sid in SECTOR_MAP[g]["ids"]:
                if sid not in seen:
                    seen[sid] = (sid, sid, "上市", f"{sid}.TW")
        return list(seen.values())

    scan_list   = build_scan_list()
    total_count = len(scan_list)
    est_sec     = total_count * 1.3
    est_str     = f"{int(est_sec//60)}分{int(est_sec%60)}秒" if est_sec>=60 else f"{int(est_sec)}秒"

    st.markdown("---")
    st.markdown("<div style='color:#ffab40;font-size:0.74rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>STEP 2 ｜ 市場環境 ＋ 篩選條件</div>", unsafe_allow_html=True)

    # ── 大盤自動偵測
    auto_mode_key, auto_mode_label, auto_mode_desc, auto_color = detect_market_mode()

    st.markdown(
        f"<div class='infobox'>📡 大盤自動偵測：<b style='color:{auto_color};'>{auto_mode_label}</b><br>"
        f"<span style='font-size:0.74rem;'>{auto_mode_desc}</span></div>",
        unsafe_allow_html=True
    )

    # ── 手動切換市場模式
    mode_options = {
        "🚀 多頭追蹤｜法人持續買、量能放大": "bull",
        "📊 盤整低接｜量縮回測、融資減少":   "range",
        "🛡️ 弱市防守｜籌碼極乾淨、強勢抗跌": "bear",
    }
    mode_labels  = list(mode_options.keys())
    # 預設選自動偵測的模式
    auto_idx = {"bull":0,"range":1,"bear":2}.get(auto_mode_key, 1)
    selected_mode_label = st.radio(
        "市場模式（可手動覆蓋）",
        mode_labels,
        index=auto_idx,
        horizontal=False,
        label_visibility="collapsed",
    )
    mode_key    = mode_options[selected_mode_label]
    mode_params = MARKET_MODE_PARAMS[mode_key]

    # ── 顯示模式說明
    mode_colors = {"bull":"#00e676","range":"#ffab40","bear":"#ff5252"}
    mc = mode_colors[mode_key]
    st.markdown(
        f"<div style='background:rgba({"0,230,118" if mode_key=="bull" else "255,171,64" if mode_key=="range" else "255,82,82"},0.08);"
        f"border:1px solid {mc};border-radius:8px;padding:8px 12px;margin:4px 0;'>"
        f"<span style='color:{mc};font-weight:600;font-size:0.8rem;'>{mode_params["label"]}</span>"
        f"<span style='color:#7fb3d3;font-size:0.74rem;'> ｜ {mode_params["desc"]}</span></div>",
        unsafe_allow_html=True
    )

    # ── 篩選條件滑桿（預設值自動帶入選擇的模式）
    with st.expander("第一道：基本面護城河", expanded=False):
        eps_min = st.slider("EPS(TTM) 最低", 0.0, 20.0,
                            float(mode_params["eps_min"]), 0.5)
        pe_max  = st.slider("P/E 最高",       0,   80,
                            int(mode_params["pe_max"]),  1)
        gm_min  = st.slider("毛利率% 最低",   0,   60,
                            int(mode_params["gm_min"]),  1)
    with st.expander("第二道：籌碼黃金交叉", expanded=False):
        margin_max = st.slider("融資5日變動% 上限", -10.0, 5.0,
                               float(mode_params["margin_max"]), 0.5)
        inst_min   = st.slider("法人買超% 下限",     0.0,  30.0,
                               float(mode_params["inst_min"]),   0.5)
        st.caption("⚠️ 多頭模式下融資上限放寬至正值，允許適度追多")
    with st.expander("第三道：右側均線防守", expanded=False):
        bias_max  = st.slider("MA20乖離% 上限", 1.0, 15.0,
                              float(mode_params["bias_max"]), 0.5)
        vol_max   = st.slider("量比(5MA) 上限", 0.3,  1.5,
                              float(mode_params["vol_max"]),  0.05)
        st.caption("多頭模式允許量比放大到1.2，盤整模式要求量縮0.7以下")

    params = dict(eps_min=eps_min, pe_max=pe_max, gm_min=gm_min,
                  margin_max=margin_max, inst_min=inst_min,
                  bias_max=bias_max, vol_max=vol_max,
                  market_mode=mode_key)

    st.markdown("---")
    st.markdown("<div style='color:#ffab40;font-size:0.74rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;'>STEP 3 ｜ 執行掃描</div>", unsafe_allow_html=True)
    if total_count > 0:
        st.markdown(f"<div class='infobox'>掃描 <b style='color:#00d4ff;'>{total_count}</b> 檔 ｜ 預估 <b style='color:#ffab40;'>{est_str}</b></div>", unsafe_allow_html=True)

    def apply_filters(df, p):
        r = df.copy()
        r["pass1"] = ((r["EPS_TTM"]>p["eps_min"]).fillna(False) &
                      (r["PE"]<p["pe_max"]).fillna(True) &
                      (r["毛利率%"]>p["gm_min"]).fillna(False))
        r["pass2"] = r["pass1"] & (r["融資5日變動%"]<p["margin_max"]) &                      (r["法人買超%"]>p["inst_min"]) & (r["大戶持股增"]==True)
        r["pass3"] = r["pass2"] & (r["MA20乖離%"]>0) & (r["MA20乖離%"]<p["bias_max"]) &                      (r["量比(5MA)"]<p["vol_max"]) & (r["底部墊高"]==True)
        return r

    run_btn = st.button("🚀 開始掃描此群組", type="primary",
                        use_container_width=True, disabled=(total_count==0))

    if run_btn:
        prog = st.progress(0); stat = st.empty(); rows = []; errors = []
        fm_tok = st.session_state.fm_token
        start_chip = (datetime.today()-timedelta(days=30)).strftime("%Y-%m-%d")

        def _dl(tk, retries=3):
            for attempt in range(retries):
                try:
                    time.sleep(attempt*1.2)
                    df = yf.download(tk, period="6mo", auto_adjust=True, progress=False, timeout=15)
                    df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
                    if not df.empty and len(df)>=25: return df
                except: pass
            alt = tk.replace(".TW",".TWO") if tk.endswith(".TW") else tk.replace(".TWO",".TW")
            for attempt in range(2):
                try:
                    time.sleep(1+attempt)
                    df = yf.download(alt, period="6mo", auto_adjust=True, progress=False, timeout=15)
                    df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
                    if not df.empty and len(df)>=25: return df
                except: pass
            return pd.DataFrame()

        for i,(sid,sname,mkt,ytk) in enumerate(scan_list):
            prog.progress((i+1)/len(scan_list))
            stat.markdown(f"<div style='color:#7fb3d3;font-size:0.74rem;'>[{i+1}/{len(scan_list)}] {sid} {sname}</div>", unsafe_allow_html=True)
            try:
                df_tmp = _dl(ytk)
                if df_tmp.empty: errors.append(sid); continue
                close = df_tmp["Close"].values.flatten().astype(float)
                vol   = df_tmp["Volume"].values.flatten().astype(float)
                high  = df_tmp["High"].values.flatten().astype(float)
                low_  = df_tmp["Low"].values.flatten().astype(float)
                s = pd.Series(close)
                ma20_v = float(s.rolling(20).mean().iloc[-1])
                vma5_v = float(pd.Series(vol).rolling(5).mean().iloc[-1])
                last_c = float(close[-1]); last_v = float(vol[-1])
                ma20_b = (last_c-ma20_v)/ma20_v*100 if ma20_v>0 else 0
                vol_r  = last_v/vma5_v if vma5_v>0 else 99
                rl = min(low_[-20:]) if len(low_)>=20 else min(low_)
                ol = min(low_[-40:-20]) if len(low_)>=40 else min(low_)
                hl = bool(rl>ol)
                lm=pd.Series(low_).rolling(9).min(); hm=pd.Series(high).rolling(9).max()
                rsv=(s-lm)/(hm-lm+1e-9)*100; kv=dv=50.0
                for rv_ in rsv.dropna(): kv=kv*2/3+float(rv_)*1/3; dv=dv*2/3+kv*1/3
                pe_v=gm_v=eps_v=np.nan
                try:
                    info=yf.Ticker(ytk).info or {}
                    pe_v=info.get("trailingPE",np.nan); eps_v=info.get("trailingEps",np.nan)
                    gm_v=info.get("grossMargins",np.nan)
                    if gm_v and not np.isnan(float(gm_v)) and float(gm_v)<1: gm_v=float(gm_v)*100
                except: pass

                # FinMind 真實籌碼（若可用）
                margin_chg = np.random.uniform(-7,4); inst_buy = np.random.uniform(-5,25)
                big_holder = bool(np.random.choice([True,False],p=[0.45,0.55]))
                chip_real  = False
                df_inst, ok_inst = fetch_institutional(sid, start_chip, fm_tok)
                if ok_inst and not df_inst.empty:
                    try:
                        total_net_5d = df_inst.groupby("date")["net"].sum().iloc[-5:].sum()
                        total_oi     = abs(df_inst["buy"].astype(float).sum()+df_inst["sell"].astype(float).sum())
                        inst_buy  = (total_net_5d/total_oi*100) if total_oi>0 else 0
                        chip_real = True
                    except: pass
                df_mg, ok_mg = fetch_margin(sid, start_chip, fm_tok)
                if ok_mg and not df_mg.empty:
                    try:
                        col_bal = [c for c in df_mg.columns if "MarginPurchaseTodayBalance" in c or "margin_purchase_today_balance" in c]
                        if col_bal:                            bal = df_mg[col_bal[0]].astype(float)
                            margin_chg = (bal.iloc[-1]-bal.iloc[min(-5,len(bal)-1)])/bal.iloc[min(-5,len(bal)-1)]*100 if bal.iloc[min(-5,len(bal)-1)]!=0 else 0
                            big_holder = margin_chg < 0
                            chip_real  = True
                    except: pass

                rows.append({
                    "代號":sid,"名稱":sname,"市場":mkt,"yf_ticker":ytk,
                    "收盤價":round(last_c,1),"MA20乖離%":round(ma20_b,2),
                    "量比(5MA)":round(vol_r,2),"底部墊高":hl,
                    "K值":round(kv,1),"D值":round(dv,1),
                    "PE":round(float(pe_v),1) if pe_v and not np.isnan(float(pe_v)) else np.nan,
                    "EPS_TTM":round(float(eps_v),2) if eps_v and not np.isnan(float(eps_v)) else np.nan,
                    "毛利率%":round(float(gm_v),1) if gm_v and not np.isnan(float(gm_v)) else np.nan,
                    "融資5日變動%":round(float(margin_chg),1),
                    "法人買超%":round(float(inst_buy),1),
                    "大戶持股增":bool(big_holder),
                    "籌碼真實":chip_real,
                })
            except: errors.append(sid)
            time.sleep(0.05)

        prog.empty(); stat.empty()
        if rows:
            df_scan = pd.DataFrame(rows)
            df_filt = apply_filters(df_scan, params)
            st.session_state.scan_result   = df_filt
            st.session_state.scanned       = True
            st.session_state.scanned_group = selected_group
            passed3 = df_filt[df_filt["pass3"]==True]
            st.session_state.selected_pool = [(r.yf_ticker,f"{r.代號} {r.名稱}") for _,r in passed3.iterrows()]
            if errors: st.warning(f"⚠️ {len(errors)} 檔無資料")
            st.success(f"✅ 完成！精選 {len(passed3)} 檔")
        else: st.error("掃描失敗，請確認網路連線")

    if st.session_state.scanned and st.session_state.scan_result is not None:
        df_r=st.session_state.scan_result
        n1=int(df_r["pass1"].sum()); n2=int(df_r["pass2"].sum()); n3=int(df_r["pass3"].sum())
        chip_real_n = int(df_r.get("籌碼真實",pd.Series([False]*len(df_r))).sum())
        st.markdown(
            f"<div class='infobox'><span style='color:#7fb3d3;font-size:0.7rem;'>上次：{st.session_state.scanned_group}</span><br>"
            f"掃描 <b style='color:#e8f4fd;'>{len(df_r)}</b> → 一道 <b style='color:#00d4ff;'>{n1}</b> → "
            f"二道 <b style='color:#e040fb;'>{n2}</b> → <b style='color:#00e676;'>精選 {n3} ✅</b><br>"
            f"<span style='color:#00e676;font-size:0.68rem;'>真實籌碼：{chip_real_n} 檔</span> "
            f"<span style='color:#ffab40;font-size:0.68rem;'>模擬籌碼：{len(df_r)-chip_real_n} 檔</span></div>",
            unsafe_allow_html=True
        )
        if st.button("🔄 重新套用條件（不重新下載）", use_container_width=True):
            df_re = apply_filters(df_r.drop(columns=["pass1","pass2","pass3"],errors="ignore"), params)
            st.session_state.scan_result = df_re
            passed3 = df_re[df_re["pass3"]==True]
            st.session_state.selected_pool = [(r.yf_ticker,f"{r.代號} {r.名稱}") for _,r in passed3.iterrows()]
            st.rerun()

    st.markdown("---")
    st.markdown("<div style='color:#7fb3d3;font-size:0.74rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px;'>🎯 監控標的</div>", unsafe_allow_html=True)
    pool = st.session_state.selected_pool
    if pool:
        pool_map = {label:tick for tick,label in pool}
        chosen   = st.selectbox("精選標的", list(pool_map.keys()), label_visibility="collapsed")
        ticker   = pool_map[chosen]; selected_name = chosen
    else:
        fb = {"聯發科 (2454)":"2454.TW","台積電 (2330)":"2330.TW","台達電 (2308)":"2308.TW","廣達 (2382)":"2382.TW"}
        selected_name = st.selectbox("預設標的", list(fb.keys()), label_visibility="collapsed")
        ticker = fb[selected_name]
        st.markdown("<div style='color:#ffab40;font-size:0.7rem;'>💡 掃描後自動填入精選股</div>", unsafe_allow_html=True)
    st.session_state.watch_ticker = ticker; st.session_state.watch_name = selected_name

    st.markdown("---")
    ma_short = st.slider("短均線",3,10,5); ma_mid=st.slider("中均線",10,30,20); ma_long=st.slider("長均線",40,120,60)
    period   = st.select_slider("K線週期",["3mo","6mo","1y","2y"],value="1y")
    st.markdown(f"""
    <div style="background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;padding:8px;text-align:center;margin-top:8px;">
        <div style="color:#00d4ff;font-size:0.78rem;font-family:monospace;">{datetime.now().strftime("%Y/%m/%d %H:%M")}</div>
        <div style="color:#2ecc71;font-size:0.66rem;margin-top:3px;">● Yahoo Finance ＋ FinMind API</div>
    </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════
# Header + KPI
# ══════════════════════════════════════════════
st.markdown(f"""
<div class="main-header">
    <h1>📊 台股全週期量化交易儀表板 v4</h1>
    <p>監控：{selected_name} ｜ 數據：Yahoo Finance ＋ FinMind API（真實籌碼/財報） ｜ 期貨需 Token</p>
</div>""", unsafe_allow_html=True)

with st.spinner(f"載入 {selected_name} ..."):
    df_raw = get_chart_data(ticker, period)

if df_raw.empty:
    st.error(f"❌ 無法取得 {selected_name} 資料，請稍後重試或換一個標的。")
    st.stop()

df = add_indicators(df_raw.copy(), ma_short, ma_mid, ma_long)
lt=df.iloc[-1]; pv=df.iloc[-2]
chg=(lt["Close"]-pv["Close"])/pv["Close"]*100
chg_cls="up" if chg>=0 else "down"; chg_sym="▲" if chg>=0 else "▼"

cols6=st.columns(6)
for col,(lbl,val,cls) in zip(cols6,[
    ("收盤價",f"{lt['Close']:.1f}",chg_cls),
    ("漲跌幅",f"{chg_sym}{abs(chg):.2f}%",chg_cls),
    (f"MA{ma_short}",f"{lt[f'MA{ma_short}']:.1f}",""),
    (f"MA{ma_mid}",  f"{lt[f'MA{ma_mid}']:.1f}",""),
    ("K值",f"{lt['K']:.1f}",""),("D值",f"{lt['D']:.1f}",""),
]):
    with col:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
st.markdown("<br>",unsafe_allow_html=True)

# ══════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════
tab1,tab2,tab3,tab4,tab5=st.tabs(["🔍 選股掃描儀","🚨 即時防守監控","🧮 籌碼純度檢驗","📋 基本面追蹤","🚨 大盤崩盤預警"])

# ──────────────────────────────────────────────
# TAB 1：掃描結果
# ──────────────────────────────────────────────
with tab1:
    st.markdown("<div class='section-title'>波段潛力核心自選股清單 · 三道篩選結果</div>",unsafe_allow_html=True)
    gc1,gc2,gc3=st.columns(3)
    for col,title,color,desc in zip([gc1,gc2,gc3],
        ["第一道·基本面","第二道·籌碼","第三道·均線防守"],
        ["#00d4ff","#e040fb","#ffab40"],
        [f"EPS>{eps_min} PE<{pe_max} 毛利>{gm_min}%",
         f"融資<{margin_max}% 法人>{inst_min}%",
         f"MA20乖離<{bias_max}% 量比<{vol_max}"]):
        with col:
            st.markdown(f"<div style='background:linear-gradient(135deg,#0f2027,#162535);border:1px solid #1e3a5f;border-left:3px solid {color};border-radius:8px;padding:10px 12px;'><div style='color:{color};font-size:0.7rem;letter-spacing:1px;margin-bottom:4px;'>{title}</div><div style='color:#e8f4fd;font-size:0.76rem;line-height:1.6;'>{desc}</div></div>",unsafe_allow_html=True)
    st.markdown("<br>",unsafe_allow_html=True)
    if not st.session_state.scanned:
        st.markdown("<div style='background:#0f2027;border:2px dashed #1e3a5f;border-radius:12px;padding:50px;text-align:center;'><div style='font-size:2rem;margin-bottom:10px;'>🔬</div><div style='color:#e8f4fd;font-size:0.92rem;font-weight:600;'>尚未執行掃描</div><div style='color:#7fb3d3;font-size:0.8rem;margin-top:8px;'>左側 STEP1 選群組 → STEP2 設條件 → STEP3 執行掃描</div></div>",unsafe_allow_html=True)
    else:
        df_r=st.session_state.scan_result
        n_all=len(df_r); n1=int(df_r["pass1"].sum()); n2=int(df_r["pass2"].sum()); n3=int(df_r["pass3"].sum())
        mc1,mc2,mc3,mc4=st.columns(4)
        for col,(lbl,val,cls) in zip([mc1,mc2,mc3,mc4],[("掃描股數",str(n_all),""),("第一道",str(n1),"up"),("第二道",str(n2),"up"),("精選",str(n3),"up")]):
            with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
        st.markdown("<br>",unsafe_allow_html=True)
        fl,fr=st.columns([1,2])
        with fl:
            fig_f=go.Figure(go.Funnel(y=["群組股票池","基本面通過","籌碼通過","精選核心"],x=[n_all,n1,n2,n3],
                textinfo="value+percent initial",marker=dict(color=["#1e3a5f","#00d4ff","#e040fb","#00e676"]),
                textfont=dict(color="#e8f4fd",size=11),connector=dict(line=dict(color="#1e3a5f",width=1))))
            fig_f.update_layout(**base_layout("篩選漏斗",280))
            st.plotly_chart(fig_f,use_container_width=True)
        with fr:
            passed3=df_r[df_r["pass3"]==True]; passed2=df_r[df_r["pass2"]==True]
            st.markdown(f"<div style='color:#00e676;font-weight:700;font-size:0.86rem;margin-bottom:6px;'>✅ 精選股（{n3}檔）</div>",unsafe_allow_html=True)
            if not passed3.empty:
                tags="".join([f"<span class='chip-green'>{r.代號} {r.名稱}</span>" for _,r in passed3.iterrows()])
                st.markdown(tags,unsafe_allow_html=True)
            else: st.markdown("<div style='color:#ff5252;font-size:0.8rem;'>無三道全通過，請調整條件</div>",unsafe_allow_html=True)
            st.markdown(f"<div style='color:#00d4ff;font-weight:600;font-size:0.82rem;margin:10px 0 5px;'>📌 二道觀察（{n2}檔）</div>",unsafe_allow_html=True)
            tags2="".join([f"<span class='chip-blue'>{r.代號} {r.名稱}</span>" for _,r in passed2.iterrows()])
            st.markdown(tags2 or "<span style='color:#546e7a;'>無</span>",unsafe_allow_html=True)
        st.markdown("<br>",unsafe_allow_html=True)
        tc1,tc2,tc3=st.columns(3)
        with tc1: show_pass=st.selectbox("顯示範圍",["三道全通過","二道以上","一道以上","全部掃描"])
        with tc2: sort_col=st.selectbox("排序",["毛利率%","EPS_TTM","PE","MA20乖離%","量比(5MA)","法人買超%"])
        with tc3: sort_asc=(st.radio("方向",["↓高到低","↑低到高"],horizontal=True)=="↑低到高")
        pm={"三道全通過":df_r[df_r["pass3"]==True],"二道以上":df_r[df_r["pass2"]==True],"一道以上":df_r[df_r["pass1"]==True],"全部掃描":df_r}
        dv=pm[show_pass].copy()
        if sort_col in dv.columns: dv=dv.sort_values(sort_col,ascending=sort_asc,na_position="last")
        sc=[c for c in ["代號","名稱","市場","收盤價","EPS_TTM","毛利率%","PE","MA20乖離%","量比(5MA)","融資5日變動%","法人買超%","籌碼真實","pass1","pass2","pass3"] if c in dv.columns]
        dd=dv[sc].copy()
        for p in ["pass1","pass2","pass3"]:
            if p in dd.columns: dd[p]=dd[p].map({True:"✅",False:"❌"})
        if "籌碼真實" in dd.columns: dd["籌碼真實"]=dd["籌碼真實"].map({True:"🟢實","False":"🟡模"})
        dd.columns=[c.replace("pass1","第一道").replace("pass2","第二道").replace("pass3","第三道") for c in dd.columns]
        def row_hl(row):
            if row.get("第三道","")=="✅": return ["background:rgba(0,230,118,0.07);color:#e8f4fd"]*len(row)
            if row.get("第二道","")=="✅": return ["background:rgba(0,212,255,0.05);color:#e8f4fd"]*len(row)
            return ["color:#7fb3d3"]*len(row)
        fmt={c:"{:.1f}" for c in ["毛利率%","MA20乖離%","融資5日變動%","法人買超%"]}
        fmt.update({"收盤價":"{:.1f}","EPS_TTM":"{:.2f}","PE":"{:.1f}","量比(5MA)":"{:.2f}"})
        fmt_v={k:v for k,v in fmt.items() if k in dd.columns}
        st.dataframe(dd.style.apply(row_hl,axis=1).format(fmt_v,na_rep="—"),use_container_width=True,height=340)
        st.caption(f"共 {len(dd)} 檔 ｜ 綠色=真實籌碼(FinMind) 黃色=模擬籌碼")
        # ════════════════════════════════════════
        # 第四層：低風險伏擊點分析
        # ════════════════════════════════════════
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>🎯 低風險伏擊點分析 · 第四層精選</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='infobox'>從篩選通過的標的中，綜合『法人買超程度』、"
            "『量縮回測均線狀態』、『散戶去槓桿程度』，自動找出最佳進場時機的前3檔，"
            "並標示建議伏擊區間與停損線。</div>",
            unsafe_allow_html=True
        )

        if st.button("🎯 分析低風險伏擊點（從第一道以上標的分析）", use_container_width=True):
            analyze_pool = df_r[df_r["pass1"]==True].copy()
            if analyze_pool.empty:
                st.warning("請先執行掃描，且至少要有通過第一道的標的")
            else:
                with st.spinner(f"分析 {len(analyze_pool)} 檔標的中..."):
                    ambush_rows = []
                    for _, row in analyze_pool.iterrows():
                        try:
                            tk = row["yf_ticker"]
                            df_p = yf.download(tk, period="3mo", auto_adjust=True, progress=False)
                            if df_p.empty or len(df_p) < 20: continue
                            df_p.columns = [c[0] if isinstance(c,tuple) else c for c in df_p.columns]
                            close = df_p["Close"].values.flatten().astype(float)
                            vol   = df_p["Volume"].values.flatten().astype(float)
                            high  = df_p["High"].values.flatten().astype(float)
                            low_  = df_p["Low"].values.flatten().astype(float)
                            last_c = float(close[-1])
                            ma5    = float(pd.Series(close).rolling(5).mean().iloc[-1])
                            ma20   = float(pd.Series(close).rolling(20).mean().iloc[-1])
                            ma60   = float(pd.Series(close).rolling(min(60,len(close))).mean().iloc[-1])
                            vma5   = float(pd.Series(vol).rolling(5).mean().iloc[-1])
                            vol_ratio = float(vol[-1]) / vma5 if vma5 > 0 else 99
                            recent_low  = min(low_[-20:])
                            recent_high = max(high[-20:])
                            score = 0; reasons = []
                            ma5_bias  = (last_c - ma5)  / ma5  * 100
                            ma20_bias = (last_c - ma20) / ma20 * 100
                            near_ma5  = abs(ma5_bias)  < 2.0
                            near_ma20 = abs(ma20_bias) < 3.0
                            vol_shrink = vol_ratio < 0.7

                            if near_ma5 and vol_shrink:
                                score += 40; reasons.append(f"量縮貼近MA5（{ma5_bias:+.1f}%）")
                            elif near_ma20 and vol_shrink:
                                score += 30; reasons.append(f"量縮回測MA20（{ma20_bias:+.1f}%）")
                            elif near_ma20:
                                score += 15; reasons.append(f"接近MA20（{ma20_bias:+.1f}%）")

                            inst_buy = float(row.get("法人買超%", 0) or 0)
                            if inst_buy > 15:   score += 30; reasons.append(f"法人大量買超{inst_buy:.1f}%")
                            elif inst_buy > 8:  score += 20; reasons.append(f"法人持續買超{inst_buy:.1f}%")
                            elif inst_buy > 3:  score += 10; reasons.append(f"法人小幅買超{inst_buy:.1f}%")

                            mg_chg = float(row.get("融資5日變動%", 0) or 0)
                            if mg_chg < -5:   score += 20; reasons.append(f"融資大減{mg_chg:.1f}%（籌碼極乾淨）")
                            elif mg_chg < -2: score += 12; reasons.append(f"融資減少{mg_chg:.1f}%")
                            elif mg_chg < 0:  score += 5;  reasons.append(f"融資微減{mg_chg:.1f}%")

                            if last_c > ma5 > ma20 > ma60:
                                score += 10; reasons.append("均線多頭排列")
                            elif last_c > ma5 > ma20:
                                score += 5; reasons.append("短中期多頭")

                            if score < 25: continue

                            if near_ma5:
                                entry_low  = round(ma5 * 0.990, 1)
                                entry_high = round(ma5 * 1.010, 1)
                                ref_line = "MA5"; ref_price = ma5
                            else:
                                entry_low  = round(ma20 * 0.990, 1)
                                entry_high = round(ma20 * 1.015, 1)
                                ref_line = "MA20"; ref_price = ma20

                            stop_loss = max(round(ref_price * 0.970, 1), round(recent_low * 0.990, 1))
                            target    = round(min(recent_high * 1.02, last_c * 1.08), 1)
                            risk      = last_c - stop_loss
                            reward    = target - last_c
                            rr        = round(reward / risk, 1) if risk > 0 else 0

                            ambush_rows.append({
                                "代號": row["代號"], "名稱": row["名稱"],
                                "現價": last_c, "參考均線": ref_line,
                                "伏擊下限": entry_low, "伏擊上限": entry_high,
                                "停損線": stop_loss, "目標價": target,
                                "風險報酬比": rr,
                                "法人買超%": round(inst_buy, 1),
                                "融資變動%": round(mg_chg, 1),
                                "量比": round(vol_ratio, 2),
                                "綜合評分": score,
                                "進場理由": "｜".join(reasons),
                                "pass2": bool(row.get("pass2", False)),
                                "pass3": bool(row.get("pass3", False)),
                            })
                        except: continue
                        time.sleep(0.05)

                if not ambush_rows:
                    st.warning("目前無符合伏擊條件的標的，市場可能強勢追高或弱勢無量")
                else:
                    df_amb = pd.DataFrame(ambush_rows).sort_values("綜合評分", ascending=False)
                    top3   = df_amb.head(3).reset_index(drop=True)
                    st.markdown(f"<div style='color:#00e676;font-weight:700;font-size:0.9rem;margin:10px 0 6px;'>🏆 最佳伏擊標的（前{len(top3)}檔）</div>", unsafe_allow_html=True)

                    tier_colors = ["#ffd700","#c0c0c0","#cd7f32"]
                    for rank in range(len(top3)):
                        r = top3.iloc[rank]
                        tc = tier_colors[rank]
                        if r.get("pass3"):   pbadge = "<span style='background:rgba(0,230,118,0.15);border:1px solid #00e676;color:#00e676;border-radius:4px;padding:1px 6px;font-size:0.68rem;'>三道精選</span>"
                        elif r.get("pass2"): pbadge = "<span style='background:rgba(0,212,255,0.1);border:1px solid #00d4ff;color:#00d4ff;border-radius:4px;padding:1px 6px;font-size:0.68rem;'>二道通過</span>"
                        else:                pbadge = "<span style='background:rgba(255,171,64,0.1);border:1px solid #ffab40;color:#ffab40;border-radius:4px;padding:1px 6px;font-size:0.68rem;'>一道通過</span>"
                        mg_c = "#00e676" if r["融資變動%"]<0 else "#ff5252"

                        st.markdown(f"""
                        <div style='background:linear-gradient(135deg,#0f2027,#162535);
                            border:1px solid #1e3a5f;border-left:4px solid {tc};
                            border-radius:10px;padding:16px 18px;margin:8px 0;'>
                            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>
                                <div>
                                    <span style='color:{tc};font-size:1.1rem;font-weight:700;'>#{rank+1}</span>
                                    <span style='color:#e8f4fd;font-size:1rem;font-weight:700;margin-left:8px;'>{r["代號"]} {r["名稱"]}</span>
                                    <span style='margin-left:8px;'>{pbadge}</span>
                                </div>
                                <div style='background:rgba(0,230,118,0.1);border:1px solid #00e676;border-radius:20px;padding:3px 12px;'>
                                    <span style='color:#00e676;font-weight:700;'>評分 {r["綜合評分"]}</span>
                                </div>
                            </div>
                            <div style='display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;'>
                                <div style='background:#0a1628;border-radius:6px;padding:8px;text-align:center;'>
                                    <div style='color:#7fb3d3;font-size:0.68rem;'>現價</div>
                                    <div style='color:#e8f4fd;font-size:1rem;font-weight:700;font-family:monospace;'>{r["現價"]:.1f}</div>
                                </div>
                                <div style='background:rgba(0,230,118,0.05);border:1px solid rgba(0,230,118,0.3);border-radius:6px;padding:8px;text-align:center;'>
                                    <div style='color:#7fb3d3;font-size:0.68rem;'>🎯 伏擊區間</div>
                                    <div style='color:#00e676;font-size:0.88rem;font-weight:700;font-family:monospace;'>{r["伏擊下限"]} ~ {r["伏擊上限"]}</div>
                                </div>
                                <div style='background:rgba(255,82,82,0.05);border:1px solid rgba(255,82,82,0.3);border-radius:6px;padding:8px;text-align:center;'>
                                    <div style='color:#7fb3d3;font-size:0.68rem;'>🛑 停損線</div>
                                    <div style='color:#ff5252;font-size:0.88rem;font-weight:700;font-family:monospace;'>{r["停損線"]}</div>
                                </div>
                                <div style='background:rgba(0,212,255,0.05);border:1px solid rgba(0,212,255,0.3);border-radius:6px;padding:8px;text-align:center;'>
                                    <div style='color:#7fb3d3;font-size:0.68rem;'>🎪 目標價</div>
                                    <div style='color:#00d4ff;font-size:0.88rem;font-weight:700;font-family:monospace;'>{r["目標價"]}</div>
                                </div>
                            </div>
                            <div style='display:flex;gap:16px;margin-bottom:8px;flex-wrap:wrap;'>
                                <span style='color:#7fb3d3;font-size:0.76rem;'>法人買超：<b style='color:#00d4ff;'>{r["法人買超%"]:+.1f}%</b></span>
                                <span style='color:#7fb3d3;font-size:0.76rem;'>融資變動：<b style='color:{mg_c};'>{r["融資變動%"]:+.1f}%</b></span>
                                <span style='color:#7fb3d3;font-size:0.76rem;'>量比：<b style='color:#ffab40;'>{r["量比"]:.2f}</b></span>
                                <span style='color:#7fb3d3;font-size:0.76rem;'>風險報酬：<b style='color:#e8f4fd;'>1:{r["風險報酬比"]}</b></span>
                                <span style='color:#7fb3d3;font-size:0.76rem;'>參考：<b style='color:#ffab40;'>{r["參考均線"]}</b></span>
                            </div>
                            <div style='background:#0a1628;border-radius:6px;padding:8px 10px;'>
                                <span style='color:#7fb3d3;font-size:0.72rem;'>📋 進場理由：</span>
                                <span style='color:#c8dff0;font-size:0.74rem;'>{r["進場理由"]}</span>
                            </div>
                        </div>""", unsafe_allow_html=True)

                    # 視覺化
                    fig_amb = go.Figure()
                    for i in range(len(top3)):
                        r    = top3.iloc[i]
                        name = f"{r['代號']} {r['名稱']}"
                        fig_amb.add_trace(go.Bar(x=[name], y=[r["伏擊上限"]-r["伏擊下限"]], base=[r["伏擊下限"]],
                            marker_color="rgba(0,230,118,0.25)", marker_line_color="#00e676", marker_line_width=1.5,
                            name="伏擊區間", showlegend=(i==0)))
                        fig_amb.add_trace(go.Scatter(x=[name], y=[r["現價"]], mode="markers",
                            marker=dict(color="#e8f4fd",size=14,symbol="diamond"), name="現價", showlegend=(i==0)))
                        fig_amb.add_trace(go.Scatter(x=[name], y=[r["停損線"]], mode="markers",
                            marker=dict(color="#ff5252",size=10,symbol="triangle-down"), name="停損線", showlegend=(i==0)))
                        fig_amb.add_trace(go.Scatter(x=[name], y=[r["目標價"]], mode="markers",
                            marker=dict(color="#00d4ff",size=10,symbol="triangle-up"), name="目標價", showlegend=(i==0)))
                    fig_amb.update_layout(**base_layout("低風險伏擊點 · 價格區間圖", 360),
                                         barmode="overlay", yaxis_title="股價（元）")
                    st.plotly_chart(fig_amb, use_container_width=True)

                    with st.expander(f"📋 全部伏擊候選（{len(df_amb)}檔）"):
                        sc = ["代號","名稱","現價","伏擊下限","伏擊上限","停損線","目標價","風險報酬比","法人買超%","融資變動%","量比","綜合評分"]
                        st.dataframe(df_amb[sc].style.format({
                            "現價":"{:.1f}","伏擊下限":"{:.1f}","伏擊上限":"{:.1f}","停損線":"{:.1f}",
                            "目標價":"{:.1f}","風險報酬比":"1:{:.1f}","法人買超%":"{:+.1f}%","融資變動%":"{:+.1f}%","量比":"{:.2f}",
                        }).background_gradient(cmap="Greens",subset=["綜合評分"]),
                        use_container_width=True, height=280)
                    st.caption("⚠️ 以上分析僅供參考，不構成投資建議。請結合基本面與市場環境自行判斷。")

# ──────────────────────────────────────────────
# TAB 2：防守監控
# ──────────────────────────────────────────────
with tab2:
    st.markdown("<div class='section-title'>即時防守監控牆 · INTRADAY RISK MONITOR</div>",unsafe_allow_html=True)
    above_ma5=lt["Close"]>lt[f"MA{ma_short}"]; kd_cross=(lt["K"]<lt["D"]) and (lt["K"]>80)
    n_i=78; ti=pd.date_range("09:00",periods=n_i,freq="5min")
    pi=float(lt["Close"])+np.cumsum(np.random.randn(n_i)*3)
    vi=np.mean(pi[:n_i//2])+np.random.randn(n_i)*1.5
    below_vwap=pi[-1]<vi[-1]
    alerts=[]
    if not above_ma5: alerts.append(f"股價跌破 MA{ma_short}（{lt[f'MA{ma_short}']:.1f}）")
    if kd_cross:      alerts.append(f"KD死叉（K={lt['K']:.1f}<D={lt['D']:.1f}，K>80）")
    if below_vwap:    alerts.append(f"盤中模擬跌破VWAP（{vi[-1]:.1f}）")
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
        fig.add_trace(go.Candlestick(x=df.index,open=df["Open"],high=df["High"],low=df["Low"],close=df["Close"],
            increasing_line_color="#00e676",decreasing_line_color="#ff5252",name="K線",showlegend=False),row=1,col=1)
        for mw,mc in [(ma_short,"#ff9800"),(ma_mid,"#00d4ff"),(ma_long,"#e040fb")]:
            fig.add_trace(go.Scatter(x=df.index,y=df[f"MA{mw}"],mode="lines",name=f"MA{mw}",line=dict(color=mc,width=1.5)),row=1,col=1)
        fig.add_trace(go.Bar(x=df.index,y=df["Volume"],name="量",
            marker_color=["#00e676" if c>=o else "#ff5252" for c,o in zip(df["Close"],df["Open"])],
            opacity=0.5,showlegend=False),row=2,col=1)
        fig.update_layout(**base_layout(f"{selected_name} 日線走勢",460),xaxis_rangeslider_visible=False)
        st.plotly_chart(fig,use_container_width=True)
    with cr:
        kdt=df.tail(60); fk=go.Figure()
        fk.add_trace(go.Scatter(x=kdt.index,y=kdt["K"],mode="lines",name="K",line=dict(color="#ff9800",width=1.5)))
        fk.add_trace(go.Scatter(x=kdt.index,y=kdt["D"],mode="lines",name="D",line=dict(color="#00d4ff",width=1.5)))
        fk.add_hrect(y0=80,y1=100,fillcolor="rgba(255,82,82,0.08)",line_width=0)
        fk.add_hrect(y0=0,y1=20,fillcolor="rgba(0,230,118,0.08)",line_width=0)
        fk.add_hline(y=80,line_dash="dot",line_color="#ff5252",line_width=1)
        fk.add_hline(y=20,line_dash="dot",line_color="#00e676",line_width=1)
        fk.update_layout(**base_layout("KD（近60日）",230))
        st.plotly_chart(fk,use_container_width=True)
        fv=go.Figure()
        fv.add_trace(go.Scatter(x=ti,y=pi,mode="lines",name="盤中",line=dict(color="#e8f4fd",width=1.5)))
        fv.add_trace(go.Scatter(x=ti,y=vi,mode="lines",name="VWAP",line=dict(color="#ffab40",width=1.5,dash="dot")))
        fv.update_layout(**base_layout("盤中模擬 vs VWAP",230))
        st.plotly_chart(fv,use_container_width=True)

# ──────────────────────────────────────────────
# TAB 3：籌碼 FinMind 真實資料
# ──────────────────────────────────────────────
with tab3:
    st.markdown("<div class='section-title'>籌碼純度檢驗 · CHIPS ANALYSIS</div>",unsafe_allow_html=True)
    stock_id_watch = ticker.replace(".TW","").replace(".TWO","")
    start_chip3 = (datetime.today()-timedelta(days=60)).strftime("%Y-%m-%d")
    fm_tok = st.session_state.fm_token

    df_inst3, ok_inst3 = fetch_institutional(stock_id_watch, start_chip3, fm_tok)
    df_mg3,   ok_mg3   = fetch_margin(stock_id_watch, start_chip3, fm_tok)

    if ok_inst3 and not df_inst3.empty:
        st.markdown("<span class='api-badge-real'>🟢 FinMind 真實數據</span>",unsafe_allow_html=True)
        inst_pivot = df_inst3.pivot_table(index="date",columns="name",values="net",aggfunc="sum").fillna(0)
        inst_pivot.index = pd.to_datetime(inst_pivot.index)
        inst_pivot = inst_pivot.sort_index()
        recent5_total = inst_pivot.iloc[-5:].sum().sum()
        recent_mg_chg = 0.0
        if ok_mg3 and not df_mg3.empty:
            col_bal=[c for c in df_mg3.columns if "TodayBalance" in c or "today_balance" in c]
            if col_bal:
                bal=df_mg3[col_bal[0]].astype(float)
                recent_mg_chg=(bal.iloc[-1]-bal.iloc[min(-5,len(bal)-1)])/max(abs(bal.iloc[min(-5,len(bal)-1)]),1)*100
        ri_5 = inst_pivot.iloc[-5:].sum().sum()
        if ri_5<0 and recent_mg_chg>0:
            st.markdown("<div class='warning-banner' style='text-align:center;'>⚠️ 籌碼發散，請提高警覺｜法人賣超 且 融資增加</div>",unsafe_allow_html=True)
        m1c,m2c,m3c,m4c=st.columns(4)
        col_names=list(inst_pivot.columns)
        for col,(lbl,val,cls) in zip([m1c,m2c,m3c,m4c],[
            ("外資近5日",f"{inst_pivot[[c for c in col_names if '外資' in c or 'Foreign' in c]].iloc[-5:].sum().sum()/1e8:.2f}億" if any('外資' in c or 'Foreign' in c for c in col_names) else "—","up"),
            ("投信近5日",f"{inst_pivot[[c for c in col_names if '投信' in c or 'Investment' in c]].iloc[-5:].sum().sum()/1e8:.2f}億" if any('投信' in c or 'Investment' in c for c in col_names) else "—","up"),
            ("自營近5日",f"{inst_pivot[[c for c in col_names if '自營' in c or 'Dealer' in c]].iloc[-5:].sum().sum()/1e8:.2f}億" if any('自營' in c or 'Dealer' in c for c in col_names) else "—","up"),
            ("融資5日變動",f"{recent_mg_chg:+.1f}%","up" if recent_mg_chg<0 else "down"),
        ]):
            with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
        st.markdown("<br>",unsafe_allow_html=True)
        cc1,cc2=st.columns([3,2])
        with cc1:
            fi2=make_subplots(specs=[[{"secondary_y":True}]])
            colors_map={"外資":"#00d4ff","Foreign":"#00d4ff","投信":"#e040fb","Investment":"#e040fb","自營":"#ffab40","Dealer":"#ffab40"}
            for col_name in inst_pivot.columns:
                clr=next((v for k,v in colors_map.items() if k in str(col_name)),"#546e7a")
                v=inst_pivot[col_name]/1e4
                fi2.add_trace(go.Bar(x=inst_pivot.index,y=v,name=str(col_name)[:4],
                    marker_color=[clr if x>=0 else "#ff5252" for x in v],opacity=0.75),secondary_y=False)
            if ok_mg3 and not df_mg3.empty:
                col_bal=[c for c in df_mg3.columns if "TodayBalance" in c or "today_balance" in c]
                if col_bal:
                    df_mg3_s=df_mg3.set_index("date")
                    fi2.add_trace(go.Scatter(x=df_mg3_s.index,y=df_mg3_s[col_bal[0]].astype(float)/1e8,
                        mode="lines",name="融資餘額(億)",line=dict(color="#ff9800",width=2)),secondary_y=True)
            fi2.update_layout(**base_layout(f"{selected_name} 三大法人買賣超（萬股）＋融資餘額",400))
            fi2.update_yaxes(gridcolor=GRID_COLOR,secondary_y=False)
            fi2.update_yaxes(showgrid=False,secondary_y=True)
            st.plotly_chart(fi2,use_container_width=True)
        with cc2:
            i5={str(c)[:4]:inst_pivot[c].iloc[-5:].sum()/1e4 for c in inst_pivot.columns}
            fb2=go.Figure(go.Bar(x=list(i5.values()),y=list(i5.keys()),orientation="h",
                marker_color=["#00e676" if v>=0 else "#ff5252" for v in i5.values()],
                text=[f"{v:.1f}萬" for v in i5.values()],textposition="outside"))
            fb2.update_layout(**base_layout("近5日法人（萬股）",200))
            st.plotly_chart(fb2,use_container_width=True)
    else:
        st.markdown(
            "<div class='warning-banner'>⚠️ FinMind 籌碼資料無法取得（需台灣IP或有效Token）｜顯示模擬數據</div>",
            unsafe_allow_html=True
        )
        n_c=60; dc_=pd.date_range(end=datetime.today(),periods=n_c,freq="B")
        fg=np.random.randint(-8000,15000,n_c).cumsum()//10
        tr_=np.random.randint(-3000,6000,n_c).cumsum()//10
        dl_=np.random.randint(-2000,4000,n_c).cumsum()//10
        mg_=45000+np.cumsum(np.random.randint(-500,800,n_c))
        dchip=pd.DataFrame({"日期":dc_,"外資":fg/100,"投信":tr_/100,"自營商":dl_/100,"融資餘額":mg_/100})
        fi2=make_subplots(specs=[[{"secondary_y":True}]])
        for nm,cl_ in [("外資","#00d4ff"),("投信","#e040fb"),("自營商","#ffab40")]:
            v=dchip[nm]
            fi2.add_trace(go.Bar(x=dchip["日期"],y=v,name=nm,marker_color=[cl_ if x>=0 else "#ff5252" for x in v],opacity=0.75),secondary_y=False)
        fi2.add_trace(go.Scatter(x=dchip["日期"],y=dchip["融資餘額"],mode="lines",name="融資餘額",line=dict(color="#ff9800",width=2)),secondary_y=True)
        fi2.update_layout(**base_layout("三大法人買賣超＋融資（模擬）",400))
        fi2.update_yaxes(gridcolor=GRID_COLOR,secondary_y=False); fi2.update_yaxes(showgrid=False,secondary_y=True)
        st.plotly_chart(fi2,use_container_width=True)

    with st.expander("📋 籌碼明細（近20日）",expanded=False):
        if ok_inst3 and not df_inst3.empty:
            st.dataframe(df_inst3.tail(40).sort_values("date",ascending=False),use_container_width=True)
        else:
            st.caption("FinMind 不可用，請輸入 Token 後重試")

# ──────────────────────────────────────────────
# TAB 4：基本面 FinMind 真實財報
# ──────────────────────────────────────────────
with tab4:
    st.markdown("<div class='section-title'>基本面追蹤 · FUNDAMENTALS</div>",unsafe_allow_html=True)
    start_fin=(datetime.today()-timedelta(days=730)).strftime("%Y-%m-%d")
    df_fin,ok_fin=fetch_financials(stock_id_watch,start_fin,fm_tok)

    if ok_fin and not df_fin.empty:
        st.markdown("<span class='api-badge-real'>🟢 FinMind 真實財報</span><br><br>",unsafe_allow_html=True)
        TARGET_ITEMS={"毛利率":["毛利率","GrossMargin","gross_margin","Gross Profit Margin"],
                      "營益率":["營業利益率","OperatingMargin","operating_margin","Operating Income Margin"],
                      "EPS":["每股盈餘","EPS","BasicEPS","基本每股盈餘"]}
        extracted={}
        name_col="origin_name" if "origin_name" in df_fin.columns else ("item" if "item" in df_fin.columns else df_fin.columns[2])
        val_col ="value" if "value" in df_fin.columns else df_fin.columns[-1]
        for key,patterns in TARGET_ITEMS.items():
            for pat in patterns:
                sub=df_fin[df_fin[name_col].str.contains(pat,case=False,na=False)]
                if not sub.empty:
                    extracted[key]=sub[["date",val_col]].rename(columns={val_col:key}).set_index("date")
                    extracted[key][key]=pd.to_numeric(extracted[key][key],errors="coerce")
                    break
        if extracted:
            combined=pd.concat(extracted.values(),axis=1).sort_index()
            lq=combined.iloc[-1]
            m1f,m2f,m3f,m4f=st.columns(4)
            gm_v_=lq.get("毛利率",np.nan); op_v_=lq.get("營益率",np.nan); eps_v_=lq.get("EPS",np.nan)
            for col,(lbl,val,cls) in zip([m1f,m2f,m3f,m4f],[
                ("最新毛利率",f"{gm_v_:.1f}%" if not np.isnan(gm_v_) else "—","up"),
                ("最新營益率",f"{op_v_:.1f}%" if not np.isnan(op_v_) else "—","up"),
                ("最新EPS",  f"{eps_v_:.2f}" if not np.isnan(eps_v_) else "—","up"),
                ("資料筆數",  str(len(combined)),""),
            ]):
                with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
            st.markdown("<br>",unsafe_allow_html=True)
            cl4,cr4=st.columns([1,2])
            with cl4:
                fp=go.Figure(go.Pie(labels=["手機業務","ASIC/HPC","IoT/穿戴","電源管理","其他"],
                    values=[42,28,15,10,5],hole=0.44,
                    marker=dict(colors=["#00d4ff","#e040fb","#00e676","#ffab40","#546e7a"],line=dict(color="#0a0e1a",width=2)),
                    textinfo="percent+label",textfont=dict(size=10,color="#e8f4fd")))
                fp.update_layout(**base_layout("營收結構（示意）",280),annotations=[dict(text="營收<br>結構",x=0.5,y=0.5,font_size=11,font_color="#7fb3d3",showarrow=False)])
                st.plotly_chart(fp,use_container_width=True)
            with cr4:
                fm4=make_subplots(specs=[[{"secondary_y":True}]])
                if "毛利率" in combined.columns:
                    fm4.add_trace(go.Scatter(x=combined.index,y=combined["毛利率"],mode="lines+markers",name="毛利率%",line=dict(color="#00e676",width=2.5),marker=dict(size=6)),secondary_y=False)
                if "營益率" in combined.columns:
                    fm4.add_trace(go.Scatter(x=combined.index,y=combined["營益率"],mode="lines+markers",name="營益率%",line=dict(color="#00d4ff",width=2.5,dash="dot"),marker=dict(size=6)),secondary_y=False)
                if "EPS" in combined.columns:
                    fm4.add_trace(go.Bar(x=combined.index,y=combined["EPS"],name="EPS(元)",marker_color=["#00e676" if v>=0 else "#ff5252" for v in combined["EPS"].fillna(0)],opacity=0.7),secondary_y=True)
                fm4.update_layout(**base_layout(f"{selected_name} 財務趨勢（FinMind真實資料）",420))
                fm4.update_yaxes(title_text="利率(%)",gridcolor=GRID_COLOR,secondary_y=False)
                fm4.update_yaxes(title_text="EPS(元)",showgrid=False,secondary_y=True)
                st.plotly_chart(fm4,use_container_width=True)
            with st.expander("📋 完整財報明細",expanded=False):
                st.dataframe(combined.sort_index(ascending=False).style.format("{:.2f}",na_rep="—"),use_container_width=True)
        else:
            st.warning("FinMind 財報資料欄位無法解析，請確認 API 回傳格式")
    else:
        st.markdown("<div class='warning-banner'>⚠️ FinMind 財報無法取得（需台灣IP或有效Token）｜顯示示意數據</div>",unsafe_allow_html=True)
        qs=["23Q1","23Q2","23Q3","23Q4","24Q1","24Q2","24Q3","24Q4","25Q1"]
        gm_=[42.1,43.5,44.2,45.0,46.1,47.3,47.8,48.5,49.2]; om_=[28.3,29.1,30.2,31.5,32.1,33.4,34.0,34.8,35.5]
        ep_=[12.5,13.8,14.2,15.6,16.9,17.3,18.1,19.4,21.2]; rv_=[1380,1420,1510,1680,1750,1820,1950,2100,2250]
        df4s=pd.DataFrame({"季度":qs,"毛利率%":gm_,"營益率%":om_,"營收(億)":rv_})
        lq=df4s.iloc[-1]
        m1f,m2f,m3f,m4f=st.columns(4)
        for col,(lbl,val,cls) in zip([m1f,m2f,m3f,m4f],[("最新毛利率(示意)",f"{lq['毛利率%']:.1f}%","up"),("最新營益率(示意)",f"{lq['營益率%']:.1f}%","up"),("最新季營收(示意)",f"{lq['營收(億)']:.0f}億",""),("YoY成長(示意)","+22.4%","up")]):
            with col: st.markdown(f"<div class='metric-card'><div class='metric-label'>{lbl}</div><div class='metric-value {cls}'>{val}</div></div>",unsafe_allow_html=True)
        st.markdown("<br>",unsafe_allow_html=True)
        cl4s,cr4s=st.columns([1,2])
        with cl4s:
            fp=go.Figure(go.Pie(labels=["手機業務","ASIC/HPC","IoT/穿戴","電源管理","其他"],values=[42,28,15,10,5],hole=0.44,
                marker=dict(colors=["#00d4ff","#e040fb","#00e676","#ffab40","#546e7a"],line=dict(color="#0a0e1a",width=2)),
                textinfo="percent+label",textfont=dict(size=10,color="#e8f4fd")))
            fp.update_layout(**base_layout("營收結構（示意）",280),annotations=[dict(text="示意",x=0.5,y=0.5,font_size=11,font_color="#ffab40",showarrow=False)])
            st.plotly_chart(fp,use_container_width=True)
        with cr4s:
            fm4s=make_subplots(specs=[[{"secondary_y":True}]])
            fm4s.add_trace(go.Scatter(x=df4s["季度"],y=df4s["毛利率%"],mode="lines+markers",name="毛利率%（示意）",line=dict(color="#00e676",width=2.5),marker=dict(size=6)),secondary_y=False)
            fm4s.add_trace(go.Scatter(x=df4s["季度"],y=df4s["營益率%"],mode="lines+markers",name="營益率%（示意）",line=dict(color="#00d4ff",width=2.5,dash="dot"),marker=dict(size=6)),secondary_y=False)
            fm4s.add_trace(go.Bar(x=df4s["季度"],y=ep_,name="EPS（示意）",marker_color=["#00e676" if e>0 else "#ff5252" for e in ep_],opacity=0.7),secondary_y=True)
            fm4s.update_layout(**base_layout("財務趨勢（示意—需FinMind Token）",420))
            fm4s.update_yaxes(gridcolor=GRID_COLOR,secondary_y=False); fm4s.update_yaxes(showgrid=False,secondary_y=True)
            st.plotly_chart(fm4s,use_container_width=True)

# ──────────────────────────────────────────────
# TAB 5：崩盤預警 FinMind 自動抓取
# ──────────────────────────────────────────────
with tab5:
    st.markdown("<div class='section-title'>大盤崩盤預警與行為學 · CRASH WARNING SYSTEM</div>",unsafe_allow_html=True)
    st.markdown(
        "<div style='background:linear-gradient(90deg,#1a0a0a,#2d0f0f);border:1px solid #ff5252;"
        "border-left:4px solid #ff5252;border-radius:8px;padding:12px 18px;margin-bottom:14px;'>"
        "<span style='color:#ff5252;font-weight:700;font-size:0.88rem;'>⚠️ 期貨籌碼預警系統</span>"
        "<span style='color:#7fb3d3;font-size:0.8rem;'> ｜ FinMind自動抓取大台/小台未平倉 × 蒙格行為學 × AI診斷</span></div>",
        unsafe_allow_html=True
    )

    # FinMind 自動抓取期貨籌碼
    col_auto, col_manual = st.columns([2,1])
    with col_auto:
        if st.button("🔄 自動抓取最新期貨籌碼（FinMind）", use_container_width=True):
            with st.spinner("從 FinMind 抓取期貨法人資料..."):
                st.session_state.futures_data = parse_futures_chips(fm_tok)

    futures = st.session_state.futures_data
    is_real_futures = futures.get("is_real", False)

    if is_real_futures:
        st.markdown(
            f"<span class='api-badge-real'>🟢 FinMind 真實數據</span> "
            f"<span style='color:#7fb3d3;font-size:0.74rem;'>資料日期：{futures.get('data_date','—')}</span>",
            unsafe_allow_html=True
        )
        tx_foreign  = futures.get("tx_foreign")  or -52000
        mtx_dealer  = futures.get("mtx_dealer")  or -8500
        mtx_trust   = futures.get("mtx_trust")   or -3200
        mtx_foreign = futures.get("mtx_foreign") or -18300
        mtx_oi      = futures.get("mtx_oi")      or 98000
    else:
        st.markdown(
            "<span class='api-badge-sim'>🟡 手動輸入（FinMind不可用）</span>",
            unsafe_allow_html=True
        )

    # 手動輸入（自動抓到時顯示抓到的值，否則顯示預設）
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>📡 期貨盤後籌碼</div>",unsafe_allow_html=True)
    inp1,inp2,inp3=st.columns(3)
    with inp1:
        st.markdown("<div style='color:#ff9800;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>大台指（TX）</div>",unsafe_allow_html=True)
        tx_foreign=st.number_input("外資未平倉淨額（口）",value=int(futures.get("tx_foreign") or -52000),step=500,help="負值=外資淨空；< -40000 觸發地雷警示")
    with inp2:
        st.markdown("<div style='color:#00d4ff;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>小台指（MTX）三大法人</div>",unsafe_allow_html=True)
        mtx_dealer =st.number_input("自營商淨額（口）",value=int(futures.get("mtx_dealer") or -8500),step=100)
        mtx_trust  =st.number_input("投信淨額（口）",  value=int(futures.get("mtx_trust")  or -3200),step=100)
        mtx_foreign=st.number_input("外資淨額（口）",  value=int(futures.get("mtx_foreign") or -18300),step=100)
    with inp3:
        st.markdown("<div style='color:#e040fb;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>小台指（MTX）市場</div>",unsafe_allow_html=True)
        mtx_oi=st.number_input("全市場未平倉量（口）",value=int(futures.get("mtx_oi") or 98000),step=500,min_value=1)

    mtx_inst_total =mtx_dealer+mtx_trust+mtx_foreign
    retail_net_long=mtx_inst_total*(-1)
    retail_ratio   =(retail_net_long/mtx_oi)*100

    st.markdown("<br>",unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>🧮 核心引擎計算結果</div>",unsafe_allow_html=True)
    mc1,mc2,mc3,mc4=st.columns(4)
    _mcard(mc1,"大台外資淨額（口）",f"{tx_foreign:+,}","#ff5252" if tx_foreign<-40000 else "#ffab40" if tx_foreign<0 else "#00e676")
    _mcard(mc2,"小台三大法人合計",  f"{mtx_inst_total:+,}","#ff5252" if mtx_inst_total<0 else "#00e676")
    _mcard(mc3,"散戶淨多（導火線）", f"{retail_net_long:+,}","#ff5252" if retail_net_long>0 else "#00e676")
    _mcard(mc4,"散戶多空比",         f"{retail_ratio:+.1f}%","#ff5252" if retail_ratio>10 else "#ffab40" if retail_ratio>0 else "#00e676")

    with st.expander("📐 公式推導",expanded=False):
        st.markdown(f"| 公式 | 計算 | 結果 |\n|---|---|---|\n|小台三大法人|{mtx_dealer:+,}＋{mtx_trust:+,}＋{mtx_foreign:+,}|**{mtx_inst_total:+,}口**|\n|散戶淨多（導火線）|({mtx_inst_total:+,})×(−1)|**{retail_net_long:+,}口**|\n|散戶多空比|{retail_net_long:+,}÷{mtx_oi:,}×100|**{retail_ratio:+.2f}%**|\n\n> 法人淨空 → 散戶被迫持有對應淨多 → 散戶多空比越高市場越脆弱")

    st.markdown("<br>",unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>🚦 動態風險紅綠燈</div>",unsafe_allow_html=True)
    lc1,lc2,lc3=st.columns(3)
    with lc1:
        st.markdown("<div style='color:#ff9800;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>地雷指標｜大台外資</div>",unsafe_allow_html=True)
        if tx_foreign<-60000:   st.error("🧨 極端危險｜外資超過6萬口空單")
        elif tx_foreign<-40000: st.error("🧨 系統性地雷：外資重倉空單警戒！")
        elif tx_foreign<-20000: st.warning("⚠️ 輕度警示｜外資淨空2～4萬口")
        elif tx_foreign>20000:  st.success("✅ 外資偏多｜淨多超過2萬口")
        else:                   st.info("🔵 中性｜無明確方向")
    with lc2:
        st.markdown("<div style='color:#ff5252;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>導火線｜散戶部位</div>",unsafe_allow_html=True)
        if retail_net_long>30000:   st.error("🔥 崩盤導火線已點燃：散戶大量加槓桿接刀！")
        elif retail_net_long>10000: st.error("🔥 崩盤導火線燃燒中：散戶正在加槓桿接刀！")
        elif retail_net_long>0:     st.warning("🟡 導火線微燃｜散戶小幅淨多")
        else:                       st.success("✅ 散戶偏空或中性｜踩踏風險低")

    danger_score=0
    if tx_foreign<-40000:     danger_score+=3
    elif tx_foreign<-20000:   danger_score+=1
    if retail_net_long>10000: danger_score+=3
    elif retail_net_long>0:   danger_score+=1
    if retail_ratio>20:       danger_score+=2
    elif retail_ratio>10:     danger_score+=1

    with lc3:
        st.markdown("<div style='color:#e040fb;font-size:0.76rem;font-weight:600;margin-bottom:5px;'>綜合警示等級</div>",unsafe_allow_html=True)
        if danger_score>=5:   st.error(f"🔴 極高風險｜評分：{danger_score}/8")
        elif danger_score>=3: st.warning(f"🟠 中高風險｜評分：{danger_score}/8")
        elif danger_score>=1: st.warning(f"🟡 輕度警示｜評分：{danger_score}/8")
        else:                 st.success(f"🟢 低風險｜評分：{danger_score}/8")

    st.markdown("<br>",unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>🧠 蒙格行為學大崩盤信號檢核表</div>",unsafe_allow_html=True)
    bc1,bc2=st.columns(2)
    with bc1:
        st.markdown("<div style='color:#ffab40;font-size:0.74rem;font-weight:600;margin-bottom:5px;'>📣 市場情緒面</div>",unsafe_allow_html=True)
        b1=st.checkbox("市場充斥「這次不一樣、科技股估值重構」的樂觀言論")
        b2=st.checkbox("散戶對利空麻木，認為拉回就是買點")
        b3=st.checkbox("強勢股（AI概念）出現大量散戶社群討論與接盤")
        b4=st.checkbox("媒體頻繁出現「萬八、萬九不是夢」類標題")
        b5=st.checkbox("身邊非投資人士開始詢問如何開戶")
        b6=st.checkbox("散戶急於向下攤平，加碼重挫個股")
    with bc2:
        st.markdown("<div style='color:#e040fb;font-size:0.74rem;font-weight:600;margin-bottom:5px;'>📊 技術籌碼面</div>",unsafe_allow_html=True)
        b7=st.checkbox("大量新增信用帳戶或融資餘額創近期新高")
        b8=st.checkbox("指數創新高但多數個股已跌破均線（背離）")
        b9=st.checkbox("外資連續多日在現貨大額賣超")
        b10=st.checkbox("量縮價穩假象（成交量萎縮但指數在高點）")
        b11=st.checkbox("權值股無量上攻後急跌，籌碼明顯鬆動")
        b12=st.checkbox("期貨逆價差擴大（法人對沖意願強）")

    behavior_checks=[b1,b2,b3,b4,b5,b6,b7,b8,b9,b10,b11,b12]
    checked_count=sum(behavior_checks); beh_score=checked_count/12
    st.markdown("<br>",unsafe_allow_html=True)
    pl,pr=st.columns([3,1])
    with pl:
        if beh_score>=0.67:   bl="🔴 極度貪婪危險區"
        elif beh_score>=0.42: bl="🟠 行為異常警戒區"
        elif beh_score>=0.17: bl="🟡 輕度情緒偏熱"
        else:                 bl="🟢 市場情緒正常"
        st.markdown(f"<div style='color:#e8f4fd;font-size:0.82rem;font-weight:600;margin-bottom:5px;'>📊 行為學風險指數：{bl}</div>",unsafe_allow_html=True)
        st.progress(beh_score)
    with pr:
        bc_=("#ff5252" if beh_score>=0.67 else "#ffab40" if beh_score>=0.42 else "#00e676")
        st.markdown(f"<div class='metric-card' style='margin-top:14px;'><div class='metric-label'>勾選</div><div class='metric-value' style='color:{bc_};'>{checked_count}/12</div></div>",unsafe_allow_html=True)

    st.markdown("<br>",unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>📈 籌碼結構視覺化</div>",unsafe_allow_html=True)
    vc1,vc2=st.columns(2)
    with vc1:
        cats5=["大台外資","小台自營","小台投信","小台外資","散戶淨多（推算）"]
        vals5=[tx_foreign,mtx_dealer,mtx_trust,mtx_foreign,retail_net_long]
        clrs5=["#ff5252" if v<0 else "#00e676" for v in vals5]; clrs5[-1]="#ff5252" if retail_net_long>0 else "#00e676"
        fig5_bar=go.Figure()
        fig5_bar.add_trace(go.Bar(y=cats5,x=vals5,orientation="h",marker_color=clrs5,text=[f"{v:+,}" for v in vals5],textposition="outside",textfont=dict(size=10,color="#e8f4fd")))
        fig5_bar.add_vline(x=0,line_color="#546e7a",line_width=1)
        fig5_bar.add_vline(x=-40000,line_dash="dot",line_color="#ff5252",line_width=1.5,annotation_text="地雷線",annotation_font_color="#ff5252",annotation_position="top right")
        fig5_bar.update_layout(**base_layout("法人 vs 散戶部位對比（口）",300),xaxis_title="未平倉淨額（口）")
        st.plotly_chart(fig5_bar,use_container_width=True)
    with vc2:
        r_cats=["大台外資空壓","散戶導火線","散戶多空比","行為學分數","綜合危險分"]
        r_max=[80000,50000,50,100,8]
        r_act=[min(abs(tx_foreign),80000),min(max(retail_net_long,0),50000),min(max(retail_ratio,0),50),beh_score*100,danger_score/8*100]
        r_pct=[a/m*100 for a,m in zip(r_act,r_max)]
        r_pc=r_pct+[r_pct[0]]; r_cc=r_cats+[r_cats[0]]
        fig5_r=go.Figure()
        fig5_r.add_trace(go.Scatterpolar(r=r_pc,theta=r_cc,fill="toself",name="風險",line_color="#ff5252",fillcolor="rgba(255,82,82,0.15)"))
        fig5_r.add_trace(go.Scatterpolar(r=[50]*len(r_cc),theta=r_cc,mode="lines",name="警戒線",line=dict(color="#ffab40",dash="dot",width=1.5)))
        fig5_r.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)",radialaxis=dict(visible=True,range=[0,100],gridcolor=GRID_COLOR,color=TEXT_COLOR,ticksuffix="%"),angularaxis=dict(gridcolor=GRID_COLOR,color="#e8f4fd")),**base_layout("崩盤預警雷達",300),showlegend=True)
        st.plotly_chart(fig5_r,use_container_width=True)

    st.markdown("<br>",unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.8rem;'>🤖 AI 綜合診斷報告</div>",unsafe_allow_html=True)

    def gen_diag(tx_f,r_net,r_ratio,d_score,b_score,b3_on,b_cnt,is_real):
        parts=[]
        data_note="（FinMind真實數據）" if is_real else "（手動輸入，請自行核對）"
        if d_score>=5 and b_score>=0.5:
            stage="🔴 高檔誘多末升段"; sdesc="外資重倉做空、散戶追多——典型法人出貨結構。市場表面強勢實為誘多陷阱。"
        elif d_score>=3 and b_score>=0.33:
            stage="🟠 高檔震盪、籌碼鬆動期"; sdesc="法人減碼訊號出現，散戶情緒偏熱，主力逐步撤退，波動將擴大。"
        elif r_net<0 and tx_f>-20000:
            stage="🟢 相對安全、留意波段買點"; sdesc="散戶偏空、外資空壓不大，悲觀情緒已反映，波段買點可能成形。"
        else:
            stage="🟡 中性偏謹慎，持續觀察"; sdesc="部分警訊出現但未全面引爆，輕倉等待方向明朗。"
        parts.append(f"**📍 當前階段{data_note}：{stage}**")
        parts.append(f"> {sdesc}\n")
        parts.append("**📌 現貨操作建議**")
        if d_score>=5:
            parts.append("- 強烈建議**保留現金觀望**，持倉降至30%以下\n- 已持多單設停損於支撐下方1～2%\n- 避免加碼任何個股")
        elif d_score>=3:
            parts.append("- **減倉至五成以下**，強勢股先鎖利\n- 設好停損，回調5～8%可試探分批承接")
        else:
            parts.append("- 籌碼無明顯異常，**維持正常持倉**\n- 散戶轉空時留意波段低點")
        parts.append("\n**🤖 AI強勢股建議**")
        if b3_on and d_score>=3: parts.append("- ⚠️ 社群熱度與籌碼背離，**建議減持AI概念股50%以上**\n- 待爆量長黑（散戶認賠）時才是真正買點")
        elif d_score<2:           parts.append("- AI強勢股籌碼正常，**可正常持有**，設好停損")
        else:                     parts.append("- **不加碼、不追高**，等回測均線確認後再進場")
        if b_cnt>=8:
            parts.append(f"\n**🧠 蒙格警語**\n> *在別人貪婪時恐懼——巴菲特*\n\n勾選 {b_cnt}/12 項過熱信號，歷史大崩盤前高度重疊，請認真考慮清倉或避險。")
        elif b_cnt>=4:
            parts.append(f"\n> ⚠️ 勾選 {b_cnt}/12 項行為警訊，保持冷靜控制倉位。")
        return "\n\n".join(parts)

    diag_text=gen_diag(tx_foreign,retail_net_long,retail_ratio,danger_score,beh_score,b3,checked_count,is_real_futures)
    if danger_score>=5 or beh_score>=0.67: d_b,d_bg="#ff5252","rgba(61,10,10,0.5)"
    elif danger_score>=3 or beh_score>=0.42: d_b,d_bg="#ffab40","rgba(45,27,0,0.5)"
    else: d_b,d_bg="#00d4ff","rgba(10,20,40,0.5)"
    st.markdown(f"<div style='background:{d_bg};border:1px solid {d_b};border-left:4px solid {d_b};border-radius:10px;padding:16px 20px;margin-top:6px;'><div style='color:{d_b};font-size:0.74rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;'>🤖 AI診斷報告{'（FinMind真實數據）' if is_real_futures else '（手動輸入）'}──自動生成</div>",unsafe_allow_html=True)
    st.markdown(diag_text)
    st.markdown("</div>",unsafe_allow_html=True)
    st.markdown("<div style='color:#546e7a;font-size:0.68rem;margin-top:8px;text-align:center;'>⚠️ 本系統診斷僅供參考，不構成投資建議。籌碼數據請以台灣期貨交易所官方公告為準。</div>",unsafe_allow_html=True)
