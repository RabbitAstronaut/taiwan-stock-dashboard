
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
        "desc": "IC設計、類比IC、混合訊號IC、IP授權、EDA",
        "stocks": [
            ("2454","聯發科","上市"),("2379","瑞昱","上市"),("3034","聯詠","上市"),
            ("2303","聯電","上市"),("2449","京元電子","上市"),("2388","威盛","上市"),
            ("3515","華擎","上市"),("5347","世界先進","上市"),("4966","譜瑞-KY","上市"),
            ("3443","創意","上市"),("6770","力積電","上市"),("2344","華邦電","上市"),
            ("2408","南亞科","上市"),("3653","健策","上市"),("6523","達發科技","上市"),
            ("3661","世芯-KY","上市"),("6415","矽力-KY","上市"),("3035","智原","上市"),
            ("2363","矽統","上市"),("6533","晶心科","上市"),("3141","晶宏","上市"),
            ("6643","M31","上市"),("3014","聯陽","上市"),("5274","信驊","上市"),
            ("4968","立積","上市"),("6269","台郡","上市"),("3596","智易","上市"),
            ("6789","采鈺","上市"),("2436","偉詮電","上市"),("3494","誠研","上市"),
            ("2471","資通","上市"),("6510","精測","上市"),("3532","台勝科","上市"),
            ("6147","頎邦","上市"),("8081","致新","上市"),("3209","全科","上市"),
            ("6278","台表科","上市"),("2406","國碩","上市"),("6803","崇越電","上市"),
            ("4919","新唐","上市"),("3037","欣興","上市"),("6230","超眾","上市"),
            ("5269","祥碩","上市"),("4961","天鈺","上市"),("3376","新日興","上市"),
            ("6214","精誠","上市"),("3706","神達","上市"),("2397","友通","上市"),
            ("3228","金麗科","上市"),("6442","光聖","上市"),
        ]
    },
    "⚡ 半導體｜晶圓代工＆封測": {
        "color": "#00bcd4",
        "desc": "晶圓代工、先進封裝、測試、載板、基板",
        "stocks": [
            ("2330","台積電","上市"),("2337","旺宏","上市"),("2325","矽品","上市"),
            ("3711","日月光投控","上市"),("6274","台燿","上市"),("2368","金像電","上市"),
            ("2351","順德","上市"),("6257","矽格","上市"),("3016","嘉晶","上市"),
            ("2455","全訊","上市"),("6271","同欣電","上市"),("2441","超豐","上市"),
            ("6239","力成","上市"),("3105","穩懋","上市"),("2329","華泰","上市"),
            ("3530","晶相光","上市"),("5483","中美晶","上市"),("6488","環球晶","上市"),
            ("2383","台光電","上市"),("3038","全台晶像","上市"),("2475","華映","上市"),
            ("3260","威剛","上市"),("2340","光磊","上市"),("2393","億光","上市"),
            ("2409","友達","上市"),("3481","群創","上市"),("3691","碩禾","上市"),
            ("6146","耕興","上市"),("3057","喬鼎","上市"),("4142","國光生","上市"),
        ]
    },
    "💻 AI伺服器｜雲端運算": {
        "color": "#e040fb",
        "desc": "AI伺服器、雲端基礎設施、散熱、PCB、電源",
        "stocks": [
            ("2382","廣達","上市"),("2356","英業達","上市"),("2353","宏碁","上市"),
            ("2357","華碩","上市"),("6669","緯穎","上市"),("3231","緯創","上市"),
            ("2301","光寶科","上市"),("2324","仁寶","上市"),("3017","奇鋐","上市"),
            ("2399","映泰","上市"),("3533","嘉澤","上市"),("6461","益登","上市"),
            ("3583","辛耘","上市"),("6285","啟碁","上市"),("3023","信邦","上市"),
            ("2383","台光電","上市"),("3189","景碩","上市"),("5269","祥碩","上市"),
            ("4938","和碩","上市"),("3706","神達","上市"),("3062","建漢","上市"),
            ("2397","友通","上市"),("5354","豐藝","上市"),("2365","昆盈","上市"),
            ("3044","健鼎","上市"),("3057","喬鼎","上市"),("6230","超眾","上市"),
            ("3085","碩天","上市"),("6442","光聖","上市"),("6146","耕興","上市"),
            ("2332","友訊","上市"),("3376","新日興","上市"),("6257","矽格","上市"),
            ("2462","探針","上市"),("6510","精測","上市"),("3597","通昱","上市"),
            ("2406","國碩","上市"),("6214","精誠","上市"),("3228","金麗科","上市"),
            ("2308","台達電","上市"),("3003","健和興","上市"),("6277","宏正","上市"),
        ]
    },
    "📱 消費電子｜手機零組件": {
        "color": "#ffab40",
        "desc": "手機、穿戴裝置、鏡頭、聲學元件、連接器、機構件",
        "stocks": [
            ("2317","鴻海","上市"),("2354","鴻準","上市"),("2498","宏達電","上市"),
            ("3008","大立光","上市"),("2439","美律","上市"),("3406","玉晶光","上市"),
            ("4958","臻鼎-KY","上市"),("2327","國巨","上市"),("3036","文曄","上市"),
            ("2429","銘異","上市"),("6278","台表科","上市"),("2474","可成","上市"),
            ("4961","天鈺","上市"),("2421","建準","上市"),("2393","億光","上市"),
            ("6120","輔信","上市"),("2308","台達電","上市"),("6277","宏正","上市"),
            ("3376","新日興","上市"),("6415","矽力-KY","上市"),("4906","正文","上市"),
            ("3028","增你強","上市"),("5371","中光電","上市"),("2049","上銀","上市"),
            ("3017","奇鋐","上市"),("2365","昆盈","上市"),("2364","倫飛","上市"),
            ("3034","聯詠","上市"),("2332","友訊","上市"),("6285","啟碁","上市"),
            ("3059","華晶科","上市"),("6271","同欣電","上市"),("2340","光磊","上市"),
            ("3030","晶碩","上市"),("3023","信邦","上市"),("2351","順德","上市"),
            ("1590","亞德客-KY","上市"),("3533","嘉澤","上市"),("2460","建通","上市"),
        ]
    },
    "🔋 電動車｜綠能儲能": {
        "color": "#00e676",
        "desc": "電動車、電池、太陽能、儲能系統、充電設備、被動元件",
        "stocks": [
            ("2308","台達電","上市"),("6415","矽力-KY","上市"),("5483","中美晶","上市"),
            ("6244","茂迪","上市"),("1590","亞德客-KY","上市"),("1504","東元","上市"),
            ("1514","亞力","上市"),("1537","廣隆","上市"),("8210","勝一","上市"),
            ("1560","中砂","上市"),("2207","和泰車","上市"),("2201","裕隆","上市"),
            ("2204","中華","上市"),("1605","華新","上市"),("1603","華電","上市"),
            ("1608","華榮","上市"),("1609","大亞","上市"),("1612","宏泰","上市"),
            ("5009","榮剛","上市"),("1466","聚隆","上市"),("1710","東聯","上市"),
            ("1711","永光","上市"),("3211","順達科","上市"),("6409","旭隼","上市"),
            ("3593","力致","上市"),("3576","新日光","上市"),("3548","嘉利","上市"),
            ("2327","國巨","上市"),("2399","映泰","上市"),("6257","矽格","上市"),
            ("3037","欣興","上市"),("1519","華城","上市"),("1513","中興電","上市"),
            ("1515","力山","上市"),("1516","川飛","上市"),("1529","樂事博客","上市"),
            ("1530","亞崴","上市"),("1477","中華化","上市"),("3013","映興","上市"),
        ]
    },
    "🌐 網通｜5G基礎建設": {
        "color": "#40c4ff",
        "desc": "電信服務、網路設備、WiFi、光纖、資安、雲端服務",
        "stocks": [
            ("2412","中華電","上市"),("4904","遠傳","上市"),("3045","台灣大","上市"),
            ("2332","友訊","上市"),("2345","智邦","上市"),("3047","訊舟","上市"),
            ("6456","GIS-KY","上市"),("4906","正文","上市"),("3518","柏騰","上市"),
            ("6277","宏正","上市"),("3062","建漢","上市"),("6285","啟碁","上市"),
            ("6227","原相","上市"),("3059","華晶科","上市"),("6409","旭隼","上市"),
            ("3707","漢磊","上市"),("4960","誠美材","上市"),("6510","精測","上市"),
            ("3596","智易","上市"),("2348","海韻電","上市"),("6263","普萊德","上市"),
            ("6414","樺漢","上市"),("3686","達能","上市"),("3230","昱泓","上市"),
            ("3049","和鑫","上市"),("2706","第一店","上市"),("3376","新日興","上市"),
            ("6146","耕興","上市"),("3023","信邦","上市"),("3706","神達","上市"),
            ("2397","友通","上市"),("6214","精誠","上市"),("3062","建漢","上市"),
        ]
    },
    "🏦 金融｜銀行保險券商": {
        "color": "#ffd740",
        "desc": "金控、銀行、保險、證券、票券",
        "stocks": [
            ("2881","富邦金","上市"),("2882","國泰金","上市"),("2891","中信金","上市"),
            ("2886","兆豐金","上市"),("2887","台新金","上市"),("2884","玉山金","上市"),
            ("2885","元大金","上市"),("2892","第一金","上市"),("2880","華南金","上市"),
            ("5880","合庫金","上市"),("2801","彰銀","上市"),("2820","華票","上市"),
            ("2834","臺企銀","上市"),("2838","聯邦銀","上市"),("2849","安泰銀","上市"),
            ("2850","新產","上市"),("2851","中再保","上市"),("2852","第一保","上市"),
            ("2855","統一證","上市"),("2856","元富證","上市"),("2867","三商壽","上市"),
            ("2883","開發金","上市"),("2888","新光金","上市"),("2889","國票金","上市"),
            ("2890","永豐金","上市"),("5876","上海商銀","上市"),("5878","台中銀","上市"),
            ("2823","中壽","上市"),("2824","台壽保","上市"),("6005","群益金鼎證","上市"),
            ("2809","京城銀","上市"),("2812","台中商銀","上市"),("2816","旺旺保","上市"),
            ("2826","南山人壽","上市"),("2860","新產","上市"),("2867","三商壽","上市"),
        ]
    },
    "🧪 傳統產業｜石化塑膠鋼鐵": {
        "color": "#78909c",
        "desc": "石化、塑膠、鋼鐵、橡膠、化工原料",
        "stocks": [
            ("6505","台塑化","上市"),("1301","台塑","上市"),("1303","南亞","上市"),
            ("1326","台化","上市"),("1402","遠東新","上市"),("2002","中鋼","上市"),
            ("1101","台泥","上市"),("1102","亞泥","上市"),("2006","東和鋼鐵","上市"),
            ("2007","燁興","上市"),("2008","高興昌","上市"),("2009","第一銅","上市"),
            ("2010","春源鋼鐵","上市"),("2012","春雨","上市"),("2013","中鋼構","上市"),
            ("2014","中鴻","上市"),("2015","豐興","上市"),("1304","台聚","上市"),
            ("1305","華夏","上市"),("1307","三芳化","上市"),("1308","亞聚","上市"),
            ("1309","台達化","上市"),("1310","台苯","上市"),("1312","國喬","上市"),
            ("1313","聯成","上市"),("1314","中石化","上市"),("1317","太洋","上市"),
            ("1319","東陽","上市"),("1321","大洋","上市"),("2103","台橡","上市"),
            ("1703","南亞塑膠","上市"),("1711","永光","上市"),("1712","興農","上市"),
            ("1713","國化","上市"),("1717","長興","上市"),("1718","中纖","上市"),
            ("1722","台肥","上市"),("1723","中碳","上市"),("1725","元禎","上市"),
            ("1726","永記","上市"),("1727","中華化","上市"),("1730","花仙子","上市"),
        ]
    },
    "🏗️ 營建｜不動產": {
        "color": "#ff9800",
        "desc": "建設、營造、房仲、建材、裝修",
        "stocks": [
            ("5522","遠雄","上市"),("2528","皇翔","上市"),("2534","宏盛","上市"),
            ("2511","太子","上市"),("2597","潤弘","上市"),("2515","中工","上市"),
            ("5533","三發地產","上市"),("5536","聖暉","上市"),("5546","永信建","上市"),
            ("2543","皇昌","上市"),("2535","達欣工","上市"),("2536","宏普","上市"),
            ("2537","聯上發","上市"),("2538","基泰","上市"),("2540","愛山林","上市"),
            ("2542","興富發","上市"),("2545","皇龍","上市"),("2546","根基","上市"),
            ("2547","日勝生","上市"),("2548","華固","上市"),("5512","力麒","上市"),
            ("5515","建國工程","上市"),("5519","隆大","上市"),("5521","工信","上市"),
            ("5523","廣宇","上市"),("5525","順天","上市"),("5531","鉅陞","上市"),
            ("5534","長虹","上市"),("5538","日新","上市"),("2501","國建","上市"),
            ("2502","宏國","上市"),("2504","國產","上市"),("2505","國揚","上市"),
            ("2506","太設","上市"),("2509","全坤建","上市"),("2514","龍邦","上市"),
            ("2516","新建","上市"),("2520","冠德","上市"),("2524","京城","上市"),
            ("2525","寶徠","上市"),("2526","山林水","上市"),
        ]
    },
    "💊 生技醫療｜製藥器材": {
        "color": "#ce93d8",
        "desc": "生技新藥、製藥、醫療器材、CRO、健康照護",
        "stocks": [
            ("4743","合一","上市"),("1789","神隆","上市"),("4144","宜特","上市"),
            ("4147","中裕","上市"),("6446","藥華藥","上市"),("1760","寶齡富錦","上市"),
            ("4174","浩鼎","上市"),("4162","智擎","上市"),("4141","龍燈-KY","上市"),
            ("6547","泰福-KY","上市"),("4106","雃博","上市"),("4108","懷特","上市"),
            ("4119","旭富","上市"),("4121","優盛","上市"),("4123","晟德","上市"),
            ("4126","太醫","上市"),("4128","中天","上市"),("4130","健亞","上市"),
            ("4133","亞諾法","上市"),("4148","全福生技","上市"),("4152","台微體","上市"),
            ("4160","基亞","上市"),("4163","鐿鈦","上市"),("4168","醣聯","上市"),
            ("4171","瑞基","上市"),("4175","杏昕","上市"),("1777","生達","上市"),
            ("1701","中化","上市"),("1733","五鼎","上市"),("1762","中化生","上市"),
            ("1763","愛之味","上市"),("1784","訓達","上市"),("1786","科妍","上市"),
            ("1788","創源","上市"),("1790","晶碩","上市"),("4205","中華食","上市"),
            ("4207","環泰","上市"),("4209","鐿鈦","上市"),("4210","尚志","上市"),
            ("6194","成大生技","上市"),("6245","立康","上市"),("6409","旭隼","上市"),
            ("4116","明基醫","上市"),("4117","葡萄王","上市"),("4118","進階","上市"),
        ]
    },
    "🛒 零售百貨｜電商物流": {
        "color": "#ff6e40",
        "desc": "量販店、超商、電商平台、物流、餐飲、觀光",
        "stocks": [
            ("2912","統一超","上市"),("2903","遠百","上市"),("2915","潤泰全","上市"),
            ("5904","寶雅","上市"),("2910","統領","上市"),("2905","三商行","上市"),
            ("2908","欣泰","上市"),("2911","麗嬰房","上市"),("2914","統一企業","上市"),
            ("2923","鼎固-KY","上市"),("8044","網家","上市"),("5903","全家","上市"),
            ("2718","晶華","上市"),("2719","燦星旅","上市"),("2720","鳳凰旅遊","上市"),
            ("1210","大成長城","上市"),("1215","卜蜂","上市"),("1216","統一","上市"),
            ("1217","愛之味","上市"),("1218","泰山","上市"),("1219","福壽","上市"),
            ("1225","福懋油","上市"),("1227","佳格","上市"),("1229","聯華","上市"),
            ("1230","聯華食","上市"),("1232","大統益","上市"),("1233","天仁","上市"),
            ("1234","黑松","上市"),("1235","興泰","上市"),("1236","宏亞","上市"),
            ("1256","鮮活果汁-KY","上市"),("2712","長榮航","上市"),("2717","中華航","上市"),
            ("2905","三商行","上市"),("2723","美食-KY","上市"),("2726","雅茗-KY","上市"),
        ]
    },
    "🏭 機械設備｜精密工具機": {
        "color": "#b0bec5",
        "desc": "工具機、精密機械、自動化設備、機器人、模具",
        "stocks": [
            ("2049","上銀","上市"),("1590","亞德客-KY","上市"),("1560","中砂","上市"),
            ("2059","川湖","上市"),("2061","風神","上市"),("2062","橋椿","上市"),
            ("2063","世鎧","上市"),("2064","晉勝","上市"),("2065","世德","上市"),
            ("2201","裕隆","上市"),("2204","中華","上市"),("2207","和泰車","上市"),
            ("2208","台船","上市"),("1580","新麥","上市"),("1582","信錦","上市"),
            ("1583","程泰","上市"),("1584","精剛","上市"),("1585","亞福","上市"),
            ("1586","和勤","上市"),("1589","美亞","上市"),("1591","精成科","上市"),
            ("2014","中鴻","上市"),("1513","中興電","上市"),("1515","力山","上市"),
            ("1516","川飛","上市"),("1519","華城","上市"),("1520","力肯","上市"),
            ("1521","大億","上市"),("1522","堤維西","上市"),("1524","耿鼎","上市"),
            ("1525","江申","上市"),("1526","日馳","上市"),("1527","鑽全","上市"),
            ("1528","恩德","上市"),("1530","亞崴","上市"),("1531","高林股","上市"),
            ("1532","勤美","上市"),("1533","車王電","上市"),("1535","中宇","上市"),
            ("1536","和大","上市"),("1538","正峰新","上市"),("1541","錩泰","上市"),
            ("1542","興勤","上市"),("1543","強盛","上市"),("1545","力泰","上市"),
        ]
    },
    "📺 光電面板｜顯示器": {
        "color": "#80cbc4",
        "desc": "面板、背光模組、驅動IC、光學膜、顯示材料",
        "stocks": [
            ("3481","群創","上市"),("2409","友達","上市"),("2475","華映","上市"),
            ("5371","中光電","上市"),("3008","大立光","上市"),("3406","玉晶光","上市"),
            ("3691","碩禾","上市"),("2383","台光電","上市"),("3028","增你強","上市"),
            ("3049","和鑫","上市"),("3059","華晶科","上市"),("2455","全訊","上市"),
            ("3031","佰鴻","上市"),("3033","威健","上市"),("3034","聯詠","上市"),
            ("3040","遠見","上市"),("3041","揚智","上市"),("3042","晶技","上市"),
            ("3046","建碁","上市"),("3048","益登","上市"),("3050","鈦鼎","上市"),
            ("3051","力特","上市"),("2340","光磊","上市"),("2393","億光","上市"),
            ("2460","建通","上市"),("2461","光聖","上市"),("3530","晶相光","上市"),
            ("3550","映泰","上市"),("6277","宏正","上市"),("2384","勝華","上市"),
            ("3032","偉訓","上市"),("3043","科風","上市"),("3052","夆典","上市"),
            ("2460","建通","上市"),("5274","信驊","上市"),("3596","智易","上市"),
        ]
    },
    "✏️ 自訂股票組合": {
        "color": "#7fb3d3",
        "desc": "手動輸入任意股票代號，不限產業",
        "stocks": []
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

        def _download_with_retry(tk, retries=3):
            for attempt in range(retries):
                try:
                    time.sleep(attempt * 1.2)
                    df = yf.download(tk, period="6mo", auto_adjust=True,
                                     progress=False, timeout=15)
                    df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
                    if not df.empty and len(df) >= 25:
                        return df
                except: pass
            alt = tk.replace(".TW",".TWO") if tk.endswith(".TW") else tk.replace(".TWO",".TW")
            for attempt in range(2):
                try:
                    time.sleep(1 + attempt)
                    df = yf.download(alt, period="6mo", auto_adjust=True,
                                     progress=False, timeout=15)
                    df.columns = [c[0] if isinstance(c,tuple) else c for c in df.columns]
                    if not df.empty and len(df) >= 25:
                        return df
                except: pass
            return pd.DataFrame()

        for i, (ticker, code, name, mkt) in enumerate(scan_list):
            prog.progress((i+1)/total)
            stat.markdown(f"<div style='color:#7fb3d3;font-size:0.76rem;'>[{i+1}/{total}] {code} {name}</div>", unsafe_allow_html=True)
            try:
                df_tmp = _download_with_retry(ticker)
                if df_tmp.empty:
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

# ── KPI Bar ── 強化版資料取得（重試 + 多 ticker 格式 + TWSE 備援）
@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(tk, pd_):
    """
    多層備援策略：
    1. yfinance 原始 ticker（例：2454.TW）
    2. yfinance 替換後綴（.TWO 上櫃備援）
    3. 直接用 requests 呼叫 TWSE OpenAPI 取歷史日線
    每層最多重試 3 次
    """
    import requests as req_mod

    def _clean(df):
        if df is None or df.empty: return pd.DataFrame()
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=["Close","Open","High","Low","Volume"])
        return df if len(df) >= 10 else pd.DataFrame()

    # ── 層1：yfinance 原始
    for attempt in range(3):
        try:
            time.sleep(attempt * 1.5)
            df = yf.download(tk, period=pd_, auto_adjust=True,
                             progress=False, timeout=15)
            df = _clean(df)
            if not df.empty: return df
        except: pass

    # ── 層2：切換後綴重試（.TW ↔ .TWO）
    alt_tk = tk.replace(".TW", ".TWO") if tk.endswith(".TW") else tk.replace(".TWO", ".TW")
    for attempt in range(2):
        try:
            time.sleep(1 + attempt)
            df = yf.download(alt_tk, period=pd_, auto_adjust=True,
                             progress=False, timeout=15)
            df = _clean(df)
            if not df.empty: return df
        except: pass

    # ── 層3：TWSE OpenAPI 日線備援（僅上市股，取近一年）
    code = tk.replace(".TW","").replace(".TWO","")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        # 取近12個月資料
        rows_all = []
        from datetime import date
        today = date.today()
        for m_offset in range(12):
            mo = today.month - m_offset
            yr = today.year
            while mo <= 0: mo += 12; yr -= 1
            yyyymm = f"{yr}{mo:02d}01"
            url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={yyyymm}&stockNo={code}"
            r = req_mod.get(url, headers=headers, timeout=10)
            if r.status_code != 200: continue
            jd = r.json()
            if jd.get("stat") != "OK" or "data" not in jd: continue
            for row in jd["data"]:
                try:
                    # 民國年轉西元
                    parts = row[0].split("/")
                    yr_ad = int(parts[0]) + 1911
                    dt = pd.Timestamp(f"{yr_ad}-{parts[1]}-{parts[2]}")
                    o = float(row[3].replace(",",""))
                    h = float(row[4].replace(",",""))
                    l = float(row[5].replace(",",""))
                    c = float(row[6].replace(",",""))
                    v = float(row[1].replace(",","")) * 1000
                    rows_all.append({"Date":dt,"Open":o,"High":h,"Low":l,"Close":c,"Volume":v})
                except: continue
            time.sleep(0.3)

        if rows_all:
            df_twse = pd.DataFrame(rows_all).set_index("Date").sort_index()
            df_twse = df_twse[~df_twse.index.duplicated()]
            if len(df_twse) >= 10:
                return df_twse
    except: pass

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

# 取資料，顯示友善的等待訊息
with st.spinner(f"載入 {selected_name} 資料中..."):
    df_raw = get_chart_data(ticker, period)

if df_raw.empty:
    st.error(f"""\n\n❌ 無法取得 **{selected_name}** 的資料\n\n可能原因：\n\n- Yahoo Finance 暫時限制此 IP（雲端常見）→ 請等 30 秒後重新整理頁面\n\n- 股票代號格式錯誤（上市用 `.TW`、上櫃用 `.TWO`）\n\n- 該標的已下市或停牌\n\n**建議做法：** 點左側 Sidebar 換一個標的試試，或稍候再重整頁面。\n\n""")
    st.info("💡 系統已嘗試三層備援（yfinance → 切換後綴 → TWSE OpenAPI）均失敗")
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
tab1,tab2,tab3,tab4,tab5 = st.tabs([
    "🔍 選股掃描儀","🚨 即時防守監控牆","🧮 籌碼純度檢驗","📋 基本面追蹤","🚨 大盤崩盤預警"])

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



# ══════════════════════════════════════════════
# TAB 5：大盤崩盤預警與行為學 (Crash Warning)
# ══════════════════════════════════════════════
with tab5:
    st.markdown("<div class='section-title'>大盤崩盤預警與行為學 · CRASH WARNING SYSTEM</div>", unsafe_allow_html=True)

    st.markdown(
        "<div style='background:linear-gradient(90deg,#1a0a0a,#2d0f0f);"
        "border:1px solid #ff5252;border-left:4px solid #ff5252;border-radius:8px;"
        "padding:12px 18px;margin-bottom:14px;'>"
        "<span style='color:#ff5252;font-weight:700;font-size:0.88rem;'>⚠️ 期貨籌碼預警系統</span>"
        "<span style='color:#7fb3d3;font-size:0.8rem;'>"
        " ｜ 整合大台/小台未平倉 × 蒙格行為學檢核表 × AI綜合診斷</span></div>",
        unsafe_allow_html=True
    )

    # ════════════════════════════════
    # SECTION 1：數據輸入區
    # ════════════════════════════════
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>📡 期貨盤後籌碼輸入</div>", unsafe_allow_html=True)
    inp1, inp2, inp3 = st.columns(3)

    with inp1:
        st.markdown("<div style='color:#ff9800;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>大台指（TX）</div>", unsafe_allow_html=True)
        tx_foreign = st.number_input(
            "外資未平倉淨額（口）", value=-52000, step=500,
            help="負值=外資淨空；超過 -40000 口觸發地雷警示"
        )

    with inp2:
        st.markdown("<div style='color:#00d4ff;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>小台指（MTX）三大法人</div>", unsafe_allow_html=True)
        mtx_dealer  = st.number_input("自營商淨額（口）", value=-8500,  step=100)
        mtx_trust   = st.number_input("投信淨額（口）",   value=-3200,  step=100)
        mtx_foreign = st.number_input("外資淨額（口）",   value=-18300, step=100)

    with inp3:
        st.markdown("<div style='color:#e040fb;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>小台指（MTX）市場</div>", unsafe_allow_html=True)
        mtx_oi = st.number_input(
            "全市場未平倉量（口）", value=98000, step=500, min_value=1,
            help="市場總未平倉量，用於計算散戶多空比"
        )

    # ── 核心公式計算
    mtx_inst_total  = mtx_dealer + mtx_trust + mtx_foreign   # 小台三大法人合計
    retail_net_long = mtx_inst_total * (-1)                   # 散戶淨多單（導火線）
    retail_ratio    = (retail_net_long / mtx_oi) * 100        # 散戶多空比 %

    # ════════════════════════════════
    # SECTION 2：核心引擎計算結果
    # ════════════════════════════════
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>🧮 核心引擎計算結果</div>", unsafe_allow_html=True)
    mc1, mc2, mc3, mc4 = st.columns(4)

    def _mcard(col, label, val_str, color):
        col.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-label'>{label}</div>"
            f"<div class='metric-value' style='color:{color};font-size:1.05rem;'>{val_str}</div>"
            f"</div>", unsafe_allow_html=True
        )

    _mcard(mc1, "大台外資淨額（口）",      f"{tx_foreign:+,}",
           "#ff5252" if tx_foreign < -40000 else "#ffab40" if tx_foreign < 0 else "#00e676")
    _mcard(mc2, "小台三大法人合計（口）",   f"{mtx_inst_total:+,}",
           "#ff5252" if mtx_inst_total < 0 else "#00e676")
    _mcard(mc3, "散戶淨多單·導火線（口）",  f"{retail_net_long:+,}",
           "#ff5252" if retail_net_long > 0 else "#00e676")
    _mcard(mc4, "散戶多空比",               f"{retail_ratio:+.1f}%",
           "#ff5252" if retail_ratio > 10 else "#ffab40" if retail_ratio > 0 else "#00e676")

    with st.expander("📐 公式推導說明", expanded=False):
        st.markdown(
            f"| 公式 | 計算過程 | 結果 |\n"
            f"|------|----------|------|\n"
            f"| 小台三大法人合計 | {mtx_dealer:+,} ＋ {mtx_trust:+,} ＋ {mtx_foreign:+,} | **{mtx_inst_total:+,} 口** |\n"
            f"| 散戶淨多單（導火線）| ({mtx_inst_total:+,}) × (−1) | **{retail_net_long:+,} 口** |\n"
            f"| 散戶多空比 | {retail_net_long:+,} ÷ {mtx_oi:,} × 100 | **{retail_ratio:+.2f}%** |\n\n"
            f"> 邏輯：法人合計淨空 → 散戶被迫持有對應淨多部位 → 散戶多空比越高市場越脆弱"
        )

    # ════════════════════════════════
    # SECTION 3：動態風險紅綠燈
    # ════════════════════════════════
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>🚦 動態風險紅綠燈</div>", unsafe_allow_html=True)
    lc1, lc2, lc3 = st.columns(3)

    with lc1:
        st.markdown("<div style='color:#ff9800;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>地雷指標｜大台外資</div>", unsafe_allow_html=True)
        if tx_foreign < -60000:
            st.error("🧨 極端危險｜外資重倉空單超過6萬口，系統性崩盤風險極高")
        elif tx_foreign < -40000:
            st.error("🧨 系統性地雷：外資重倉空單警戒！｜淨空超過4萬口，主力正在部署對沖")
        elif tx_foreign < -20000:
            st.warning("⚠️ 輕度警示｜外資淨空2～4萬口，需持續觀察方向")
        elif tx_foreign > 20000:
            st.success("✅ 外資偏多｜淨多超過2萬口，期現貨偏多訊號")
        else:
            st.info("🔵 中性｜外資未平倉接近平衡，無明確方向訊號")

    with lc2:
        st.markdown("<div style='color:#ff5252;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>導火線指標｜散戶部位</div>", unsafe_allow_html=True)
        if retail_net_long > 30000:
            st.error("🔥 崩盤導火線已點燃：散戶大量加槓桿接刀！｜一旦反轉將出現踩踏")
        elif retail_net_long > 10000:
            st.error("🔥 崩盤導火線燃燒中：散戶正在加槓桿接刀！｜市場脆弱度升高")
        elif retail_net_long > 0:
            st.warning("🟡 導火線微燃｜散戶小幅淨多，保持警戒輕倉應對")
        else:
            st.success("✅ 散戶偏空或中性｜未過度加槓桿，踩踏風險相對低")

    # 綜合危險評分（0~8分）
    danger_score = 0
    if tx_foreign < -40000:     danger_score += 3
    elif tx_foreign < -20000:   danger_score += 1
    if retail_net_long > 10000: danger_score += 3
    elif retail_net_long > 0:   danger_score += 1
    if retail_ratio > 20:       danger_score += 2
    elif retail_ratio > 10:     danger_score += 1

    with lc3:
        st.markdown("<div style='color:#e040fb;font-size:0.78rem;font-weight:600;margin-bottom:6px;'>綜合警示等級</div>", unsafe_allow_html=True)
        if danger_score >= 5:
            st.error(f"🔴 極高風險｜危險評分：{danger_score}/8｜建議清倉觀望或做空避險")
        elif danger_score >= 3:
            st.warning(f"🟠 中高風險｜危險評分：{danger_score}/8｜輕倉、設停損、勿攤平")
        elif danger_score >= 1:
            st.warning(f"🟡 輕度警示｜危險評分：{danger_score}/8｜正常操作但提高警覺")
        else:
            st.success(f"🟢 低風險｜危險評分：{danger_score}/8｜期現貨籌碼無明顯異常")

    # ════════════════════════════════
    # SECTION 4：蒙格行為學檢核表
    # ════════════════════════════════
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>🧠 查理·蒙格行為學大崩盤信號檢核表</div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='color:#7fb3d3;font-size:0.8rem;margin-bottom:10px;'>"
        "勾選今日市場中觀察到的現象，系統將動態調整行為學風險指數：</div>",
        unsafe_allow_html=True
    )

    bc1, bc2 = st.columns(2)
    with bc1:
        st.markdown("<div style='color:#ffab40;font-size:0.76rem;font-weight:600;margin-bottom:6px;'>📣 市場情緒面</div>", unsafe_allow_html=True)
        b1  = st.checkbox("市場充斥「這次不一樣、科技股估值重構」的樂觀言論")
        b2  = st.checkbox("散戶對利空消息麻木，認為拉回就是買點")
        b3  = st.checkbox("強勢股（AI概念）出現大量散戶社群討論與接盤行為")
        b4  = st.checkbox("媒體頻繁出現「萬八、萬九不是夢」類標題")
        b5  = st.checkbox("身邊非投資人士開始詢問如何開戶買股")
        b6  = st.checkbox("散戶急於向下攤平，加碼跌停或重挫個股")

    with bc2:
        st.markdown("<div style='color:#e040fb;font-size:0.76rem;font-weight:600;margin-bottom:6px;'>📊 技術籌碼面</div>", unsafe_allow_html=True)
        b7  = st.checkbox("大量新增信用帳戶或融資餘額創近期新高")
        b8  = st.checkbox("指數創新高但多數個股已跌破均線（頭部背離）")
        b9  = st.checkbox("外資連續多日在現貨市場大額賣超")
        b10 = st.checkbox("集中市場成交量萎縮，但指數仍在高點（量縮假象）")
        b11 = st.checkbox("權值股出現無量上攻後急跌，籌碼明顯鬆動")
        b12 = st.checkbox("期貨逆價差持續擴大（現貨>期貨，法人對沖意願強）")

    behavior_checks = [b1, b2, b3, b4, b5, b6, b7, b8, b9, b10, b11, b12]
    checked_count   = sum(behavior_checks)
    beh_score       = checked_count / 12  # 0.0 ~ 1.0

    # 行為學進度條
    st.markdown("<br>", unsafe_allow_html=True)
    prog_l, prog_r = st.columns([3, 1])
    with prog_l:
        if beh_score >= 0.67:   bar_lbl = "🔴 極度貪婪危險區"
        elif beh_score >= 0.42: bar_lbl = "🟠 行為異常警戒區"
        elif beh_score >= 0.17: bar_lbl = "🟡 輕度情緒偏熱"
        else:                   bar_lbl = "🟢 市場情緒正常"
        st.markdown(
            f"<div style='color:#e8f4fd;font-size:0.84rem;font-weight:600;margin-bottom:6px;'>"
            f"📊 行為學風險指數：{bar_lbl}</div>",
            unsafe_allow_html=True
        )
        st.progress(beh_score)
    with prog_r:
        beh_c = "#ff5252" if beh_score >= 0.67 else "#ffab40" if beh_score >= 0.42 else "#00e676"
        st.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-label'>勾選項目</div>"
            f"<div class='metric-value' style='color:{beh_c};'>{checked_count}/12</div>"
            f"</div>", unsafe_allow_html=True
        )

    # ════════════════════════════════
    # SECTION 5：視覺化圖表
    # ════════════════════════════════
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>📈 籌碼結構視覺化</div>", unsafe_allow_html=True)
    vc1, vc2 = st.columns(2)

    with vc1:
        # 法人 vs 散戶 部位橫條圖
        cats5  = ["大台外資", "小台自營", "小台投信", "小台外資", "散戶淨多（推算）"]
        vals5  = [tx_foreign, mtx_dealer, mtx_trust, mtx_foreign, retail_net_long]
        clrs5  = ["#ff5252" if v < 0 else "#00e676" for v in vals5]
        clrs5[-1] = "#ff5252" if retail_net_long > 0 else "#00e676"

        fig5_bar = go.Figure()
        fig5_bar.add_trace(go.Bar(
            y=cats5, x=vals5, orientation="h",
            marker_color=clrs5,
            text=[f"{v:+,}" for v in vals5],
            textposition="outside",
            textfont=dict(size=10, color="#e8f4fd"),
        ))
        fig5_bar.add_vline(x=0, line_color="#546e7a", line_width=1)
        fig5_bar.add_vline(x=-40000, line_dash="dot", line_color="#ff5252", line_width=1.5,
                           annotation_text="地雷警戒線(-40000)",
                           annotation_font_color="#ff5252",
                           annotation_position="top right")
        fig5_bar.update_layout(
            **base_layout("法人 vs 散戶部位對比（口）", 320),
            xaxis_title="未平倉淨額（口）"
        )
        st.plotly_chart(fig5_bar, use_container_width=True)

    with vc2:
        # 崩盤預警雷達圖
        r_cats = ["大台外資空壓", "散戶導火線", "散戶多空比", "行為學分數", "綜合危險分"]
        r_max  = [80000, 50000, 50, 100, 8]
        r_act  = [
            min(abs(tx_foreign), 80000),
            min(max(retail_net_long, 0), 50000),
            min(max(retail_ratio, 0), 50),
            beh_score * 100,
            danger_score / 8 * 100,
        ]
        r_pct = [a / m * 100 for a, m in zip(r_act, r_max)]
        r_pct_c = r_pct + [r_pct[0]]
        r_cats_c = r_cats + [r_cats[0]]

        fig5_radar = go.Figure()
        fig5_radar.add_trace(go.Scatterpolar(
            r=r_pct_c, theta=r_cats_c,
            fill="toself", name="風險指標",
            line_color="#ff5252", fillcolor="rgba(255,82,82,0.15)"
        ))
        fig5_radar.add_trace(go.Scatterpolar(
            r=[50] * len(r_cats_c), theta=r_cats_c,
            mode="lines", name="警戒基準(50%)",
            line=dict(color="#ffab40", dash="dot", width=1.5)
        ))
        fig5_radar.update_layout(
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(visible=True, range=[0, 100],
                                gridcolor=GRID_COLOR, color=TEXT_COLOR, ticksuffix="%"),
                angularaxis=dict(gridcolor=GRID_COLOR, color="#e8f4fd"),
            ),
            **base_layout("崩盤預警雷達圖", 320),
            showlegend=True,
        )
        st.plotly_chart(fig5_radar, use_container_width=True)

    # ════════════════════════════════
    # SECTION 6：AI 綜合診斷報告
    # ════════════════════════════════
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div class='section-title' style='font-size:0.82rem;'>🤖 AI 綜合診斷報告</div>", unsafe_allow_html=True)

    def generate_diagnosis(tx_f, r_net, r_ratio, d_score, b_score, b3_on, b_count):
        """依量化數據與質化勾選，自動組合今日操作建議文字"""
        parts = []

        # ── 1. 大盤階段判斷
        if d_score >= 5 and b_score >= 0.5:
            stage = "🔴 高檔誘多末升段"
            sdesc = ("外資大台重倉做空，散戶仍在高點追多，"
                     "這是教科書級別的「法人出貨、散戶接刀」結構。"
                     "市場表面強勢實為誘多陷阱，隨時可能轉為快速下殺。")
        elif d_score >= 3 and b_score >= 0.33:
            stage = "🟠 高檔震盪、籌碼鬆動期"
            sdesc = ("籌碼面出現法人減碼訊號，行為面也有散戶過熱跡象。"
                     "主力逐步撤退，上方壓力沉重，波動度將持續擴大。")
        elif r_net < 0 and tx_f > -20000:
            stage = "🟢 相對安全、可留意波段買點"
            sdesc = ("散戶已偏空或中性，外資空單壓力不大，"
                     "市場悲觀情緒已有效反映。"
                     "若同時出現強勢股止跌訊號，波段買點可能正在成形。")
        else:
            stage = "🟡 中性偏謹慎、持續觀察"
            sdesc = ("部分指標出現警訊但尚未全面引爆，"
                     "建議維持輕倉，等待籌碼方向明朗後再採取行動。")

        parts.append(f"**📍 當前大盤階段：{stage}**")
        parts.append(f"> {sdesc}")
        parts.append("")

        # ── 2. 現貨操作建議
        parts.append("**📌 現貨操作建議**")
        if d_score >= 5:
            parts.append("- 強烈建議**保留現金觀望**，持倉比例降至 30% 以下")
            parts.append("- 已持多單請立即設停損於近期支撐下方 1～2%")
            parts.append("- 此時段避免加碼任何個股，強勢股也可能是主力出貨標的")
        elif d_score >= 3:
            parts.append("- **減倉至五成以下**，持有強勢股可先鎖住部分獲利")
            parts.append("- 設好各部位停損，本波回調超過 5～8% 時，可試探性分批承接")
            parts.append("- 不追高、不攤平，等待籌碼明朗再加碼")
        else:
            parts.append("- 籌碼面無明顯異常，**可維持正常持倉**")
            parts.append("- 若散戶多空比持續走低（散戶轉空），留意波段低點買點機會")
        parts.append("")

        # ── 3. AI 強勢股操作建議
        parts.append("**🤖 AI 強勢股（概念股）操作建議**")
        if b3_on and d_score >= 3:
            parts.append("- ⚠️ **社群熱度與籌碼背離警示**：散戶大量湧入 AI 概念股，同時法人在期貨大量做空對沖")
            parts.append("- 此為典型「借題材出貨」訊號，**建議減持 AI 強勢股至少 50%**")
            parts.append("- 待出現**大量散戶認賠殺出**（爆量長黑、跌停）時，才是真正的波段買點，而非現在")
        elif d_score < 2:
            parts.append("- AI 強勢股籌碼尚無明顯警訊，**可正常持有**")
            parts.append("- 仍需設好停損，防範突發性利空衝擊")
        else:
            parts.append("- AI 強勢股處於觀察期：**不加碼、不追高**")
            parts.append("- 等待回測均線確認支撐後，再評估是否買進")
        parts.append("")

        # ── 4. 蒙格行為學警語（勾選越多越嚴重）
        if b_count >= 8:
            parts.append("**🧠 蒙格行為學極端警語**")
            parts.append("> *「在別人貪婪時恐懼，在別人恐懼時貪婪。」—— 華倫·巴菲特*")
            parts.append(f"你今日勾選了 **{b_count}/12** 項市場過熱信號——這是歷史大崩盤前的高度警示組合。")
            parts.append("**現在市場感覺最安全的時候，往往是風險最高的時候。請認真考慮清倉或避險。**")
        elif b_count >= 5:
            parts.append("**🧠 蒙格行為學警語**")
            parts.append(f"> 勾選 {b_count}/12 項行為過熱信號，市場情緒明顯偏熱。")
            parts.append("歷史上每次大崩盤前這些信號都高度重疊出現，請保持冷靜、控制倉位。")
        elif b_count >= 2:
            parts.append(f"> ⚠️ 勾選 {b_count}/12 項行為警訊，輕度過熱，持續觀察。")

        return "\n\n".join(parts)

    # 生成診斷文字
    diag_text = generate_diagnosis(
        tx_foreign, retail_net_long, retail_ratio,
        danger_score, beh_score, b3, checked_count
    )

    # 決定診斷框顏色
    if danger_score >= 5 or beh_score >= 0.67:
        d_border, d_bg = "#ff5252", "rgba(61,10,10,0.5)"
    elif danger_score >= 3 or beh_score >= 0.42:
        d_border, d_bg = "#ffab40", "rgba(45,27,0,0.5)"
    else:
        d_border, d_bg = "#00d4ff", "rgba(10,20,40,0.5)"

    st.markdown(
        f"<div style='background:{d_bg};border:1px solid {d_border};"
        f"border-left:4px solid {d_border};border-radius:10px;"
        f"padding:16px 20px;margin-top:6px;'>"
        f"<div style='color:{d_border};font-size:0.76rem;font-weight:700;"
        f"letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;'>"
        f"🤖 AI 綜合診斷報告 ── 依輸入數據自動生成</div>",
        unsafe_allow_html=True
    )
    st.markdown(diag_text)
    st.markdown("</div>", unsafe_allow_html=True)

    # 免責聲明
    st.markdown(
        "<div style='color:#546e7a;font-size:0.7rem;margin-top:10px;text-align:center;'>"
        "⚠️ 本系統診斷僅供參考，不構成任何投資建議。"
        "籌碼數據請以台灣期貨交易所官方公告為準。"
        "行為學評分基於主觀勾選，請結合自身風險承受能力做決策。"
        "</div>",
        unsafe_allow_html=True
    )
