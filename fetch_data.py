"""
fetch_data.py
─────────────────────────────────────────────────────────────
台股量化儀表板 ── 本機資料爬蟲腳本
執行環境：你的電腦（台灣IP），每天收盤後執行一次

功能：
  1. 從 FinMind 抓取所有群組股票的三大法人、融資餘額、財報、期貨籌碼
  2. 存成 CSV 到 data/ 資料夾
  3. 自動 git push 到 GitHub

使用方式：
  python fetch_data.py                    # 執行今日資料更新
  python fetch_data.py --stock 2454       # 只更新單一股票
  python fetch_data.py --no-push          # 只抓資料不推送 GitHub

安裝依賴：
  pip install requests pandas tqdm gitpython
─────────────────────────────────────────────────────────────
"""

import os
import sys
import time
import argparse
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# ── 嘗試匯入 tqdm（進度條），沒有也沒關係
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ══════════════════════════════════════════════
# 設定區（請修改這裡）
# ══════════════════════════════════════════════
CONFIG = {
    # ──────────────────────────────────────────
    # FinMind Token
    # 免費帳號申請：https://finmindtrade.com
    # ──────────────────────────────────────────
    "fm_token": "",

    # GitHub repo 本地路徑（預設當前目錄）
    "github_repo_path": ".",
    "github_commit_msg": "Auto update: {date} 台股資料更新",

    # 資料存放目錄
    "data_dir": "data",

    # ──────────────────────────────────────────
    # 抓取天數（系統自動判斷，通常不需要手動修改）
    #
    # 第一次執行（data 資料夾是空的）→ 自動用長天數抓歷史
    # 之後每天執行（已有資料）      → 自動用短天數只抓最新
    # 財報永遠用 730 天確保年報/半年報/季報都抓得到
    # ──────────────────────────────────────────

    # 第一次執行的天數（歷史資料）
    "days_institutional_first": 60,   # 三大法人：60天
    "days_margin_first":        60,   # 融資融券：60天
    "days_futures_first":       30,   # 期貨：30天

    # 每日更新的天數（只抓最新）
    "days_institutional_daily": 3,    # 三大法人：3天（含假日緩衝）
    "days_margin_daily":        3,    # 融資融券：3天
    "days_futures_daily":       3,    # 期貨：3天

    # 財報固定用 730 天（確保年報/半年報都在）
    "days_financials":         730,

    # ──────────────────────────────────────────
    # API 效能設定
    # ──────────────────────────────────────────

    # 請求間隔（秒）
    # 有 Token 免費版：0.8　付費版：0.3
    "request_delay": 0.8,

    # 每批股票數
    # 第一次執行：150（避免超出配額）
    # 每日更新：999（不分批，因為請求少）
    "batch_size_first": 150,
    "batch_size_daily": 999,

    # 批次間暫停秒數（第一次執行才需要）
    "batch_pause": 70,
}

# ══════════════════════════════════════════════
# 所有要抓取的股票代號（依產業群組）
# ══════════════════════════════════════════════
SECTOR_STOCKS = {
    "半導體IC設計": [
        "2454","2379","3034","2303","2449","2388","3515","5347","4966","3443",
        "6770","2344","2408","3653","6523","3661","6415","3035","2363","6533",
        "3141","6643","3014","5274","4968","6269","3596","6789","2436","3494",
        "2471","6510","3532","6147","8081","3209","6278","2406","6803","4919",
        "3037","6230","5269","4961","3376","6214","3706","2397","3228","6442",
    ],
    "晶圓代工封測": [
        "2330","2337","2325","3711","6274","2368","2351","6257","3016","2455",
        "6271","2441","6239","3105","2329","3530","5483","6488","2383","3038",
        "2475","3260","2340","2393","2409","3481","3691","6146","3057","4142",
    ],
    "AI伺服器雲端": [
        "2382","2356","2353","2357","6669","3231","2301","2324","3017","2399",
        "3533","6461","3583","6285","3023","2383","3189","5269","4938","3706",
        "3062","2397","5354","2365","3044","3057","6230","3085","6442","6146",
        "2332","3376","6257","2462","6510","3597","2406","6214","3228","2308",
    ],
    "消費電子手機": [
        "2317","2354","2498","3008","2439","3406","4958","2327","3036","2429",
        "6278","2474","4961","2421","2393","6120","2308","6277","3376","6415",
        "4906","3028","5371","2049","3017","2365","2364","3034","2332","6285",
        "3059","6271","2340","3030","3023","2351","1590","3533","2460",
    ],
    "電動車綠能": [
        "2308","6415","5483","6244","1590","1504","1514","1537","8210","1560",
        "2207","2201","2204","1605","1603","1608","1609","1612","5009","1466",
        "1710","1711","3211","6409","3593","3576","3548","2327","2399","6257",
        "3037","1519","1513","1515","1516","1529","1530","1477","3013",
    ],
    "網通5G": [
        "2412","4904","3045","2332","2345","3047","6456","4906","3518","6277",
        "3062","6285","6227","3059","6409","3707","4960","6510","3596","2348",
        "6263","6414","3686","3230","3049","3376","6146","3023","3706","2397",
    ],
    "金融銀行保險": [
        "2881","2882","2891","2886","2887","2884","2885","2892","2880","5880",
        "2801","2820","2834","2838","2849","2850","2851","2852","2855","2856",
        "2867","2883","2888","2889","2890","5876","5878","2823","2824","6005",
        "2809","2812","2816","2826","2860",
    ],
    "石化塑膠鋼鐵": [
        "6505","1301","1303","1326","1402","2002","1101","1102","2006","2007",
        "2008","2009","2010","2012","2013","2014","2015","1304","1305","1307",
        "1308","1309","1310","1312","1313","1314","1317","1319","1321","2103",
        "1703","1711","1712","1713","1717","1718","1722","1723","1725","1726",
    ],
    "營建不動產": [
        "5522","2528","2534","2511","2597","2515","5533","5536","5546","2543",
        "2535","2536","2537","2538","2540","2542","2545","2546","2547","2548",
        "5512","5515","5519","5521","5523","5525","5531","5534","5538","2501",
        "2502","2504","2505","2506","2509","2514","2516","2520","2524","2525",
    ],
    "生技醫療": [
        "4743","1789","4144","4147","6446","1760","4174","4162","4141","6547",
        "4106","4108","4119","4121","4123","4126","4128","4130","4133","4148",
        "4152","4160","4163","4168","4171","4175","1777","1701","1733","1762",
        "1784","1786","1788","1790","4116","4117","4118","4209","6194","6245",
    ],
    "零售百貨電商": [
        "2912","2903","2915","5904","2910","2905","2908","2911","2914","2923",
        "8044","5903","2718","2719","2720","1210","1215","1216","1217","1218",
        "1219","1225","1227","1229","1230","1232","1233","1234","1236","1256",
        "2712","2717","2723","2726",
    ],
    "機械工具機": [
        "2049","1590","1560","2059","2061","2062","2063","2064","2065","2201",
        "2204","2207","2208","1580","1582","1583","1584","1585","1586","1589",
        "1591","2014","1513","1515","1516","1519","1520","1521","1522","1524",
        "1525","1526","1527","1528","1530","1531","1532","1533","1535","1536",
    ],
    "光電面板": [
        "3481","2409","2475","5371","3008","3406","3691","2383","3028","3049",
        "3059","2455","3031","3033","3034","3040","3041","3042","3046","3048",
        "3050","3051","2340","2393","2460","2461","3530","3550","6277","2384",
        "3032","3043","3052","5274","3596",
    ],
}

# 取得所有不重複的股票代號
ALL_STOCKS = sorted(set(s for stocks in SECTOR_STOCKS.values() for s in stocks))

# ══════════════════════════════════════════════
# 日誌設定
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("fetch_data.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# 智慧判斷：第一次執行 or 每日更新
# ══════════════════════════════════════════════
def is_first_run(data_dir):
    """
    判斷是否為第一次執行
    條件：data 資料夾不存在，或 institutional.csv 不存在或是空的
    """
    import os
    csv_path = os.path.join(data_dir, "institutional.csv")
    if not os.path.exists(csv_path):
        return True
    try:
        df = pd.read_csv(csv_path)
        return len(df) < 100  # 少於100筆視為第一次
    except:
        return True

def get_days(key, data_dir):
    """
    依據是否第一次執行，自動選擇天數
    財報固定回傳 730 天
    """
    if key == "financials":
        return CONFIG["days_financials"]

    first = is_first_run(data_dir)
    if first:
        log.info(f"  📌 首次執行模式：使用長天數抓取歷史資料")
        return CONFIG.get(f"days_{key}_first", 60)
    else:
        log.info(f"  📌 每日更新模式：只抓最新資料")
        return CONFIG.get(f"days_{key}_daily", 3)

def get_batch_size(data_dir):
    """
    依據是否第一次執行，自動選擇批次大小
    """
    if is_first_run(data_dir):
        return CONFIG.get("batch_size_first", 150)
    return CONFIG.get("batch_size_daily", 999)

# ══════════════════════════════════════════════
# FinMind API 核心
# ══════════════════════════════════════════════
FM_BASE = "https://api.finmindtrade.com/api/v4/data"

def fm_get(dataset, data_id=None, start_date=None, end_date=None, token=None):
    """呼叫 FinMind API，回傳 DataFrame"""
    params = {"dataset": dataset}
    if data_id:    params["data_id"]    = data_id
    if start_date: params["start_date"] = start_date
    if end_date:   params["end_date"]   = end_date
    tok = token or CONFIG["fm_token"]
    if tok:        params["token"]      = tok

    for attempt in range(5):
        try:
            r = requests.get(FM_BASE, params=params, timeout=20)
            j = r.json()

            if j.get("status") == 200 and isinstance(j.get("data"), list):
                return pd.DataFrame(j["data"]), True

            msg = j.get("msg", "unknown")

            # ── 觸發頻率上限：自動等待後重試
            if "upper limit" in msg.lower() or "reach" in msg.lower():
                wait = 65 if attempt == 0 else 120  # 第一次等65秒，之後等2分鐘
                log.warning(
                    f"  ⏳ API 頻率上限（第{attempt+1}次），等待 {wait} 秒後重試... "
                    f"| id={data_id}"
                )
                # 顯示倒數
                for remaining in range(wait, 0, -5):
                    print(f"\r  ⏳ 等待中... {remaining} 秒", end="", flush=True)
                    time.sleep(5)
                print()  # 換行
                continue  # 重試

            # 其他錯誤直接回傳失敗
            log.warning(f"  API 回傳異常：{msg} | dataset={dataset} id={data_id}")
            return pd.DataFrame(), False

        except Exception as e:
            log.warning(f"  第{attempt+1}次連線失敗：{e}")
            time.sleep(2 ** attempt)

    log.error(f"  ❌ 達到最大重試次數，放棄 | dataset={dataset} id={data_id}")
    return pd.DataFrame(), False

# ══════════════════════════════════════════════
# 資料抓取函式
# ══════════════════════════════════════════════

# ──────────────────────────────────────────────
# 靜態股票名稱對照表（避免消耗 API 配額）
# 來源：TWSE/TPEx，涵蓋所有掃描群組股票
# ──────────────────────────────────────────────
STOCK_NAME_MAP = {
    "1101":"台泥","1102":"亞泥","1210":"大成長城","1213":"大飲","1215":"卜蜂",
    "1216":"統一","1217":"愛之味","1218":"泰山","1219":"福壽","1225":"福懋油",
    "1227":"佳格","1229":"聯華","1230":"聯華食","1232":"大統益","1233":"天仁",
    "1234":"黑松","1236":"宏亞","1256":"鮮活果汁-KY","1301":"台塑","1303":"南亞",
    "1304":"台聚","1305":"華夏","1307":"三芳化","1308":"亞聚","1309":"台達化",
    "1310":"台苯","1312":"國喬","1313":"聯成","1314":"中石化","1317":"太洋",
    "1319":"東陽","1321":"大洋","1326":"台化","1402":"遠東新","1466":"聚隆",
    "1477":"中華化","1504":"東元","1513":"中興電","1514":"亞力","1515":"力山",
    "1516":"川飛","1519":"華城","1520":"力肯","1521":"大億","1522":"堤維西",
    "1524":"耿鼎","1525":"江申","1526":"日馳","1527":"鑽全","1528":"恩德",
    "1530":"亞崴","1531":"高林股","1532":"勤美","1533":"車王電","1535":"中宇",
    "1536":"和大","1537":"廣隆","1538":"正峰新","1541":"錩泰","1542":"興勤",
    "1543":"強盛","1545":"力泰","1560":"中砂","1580":"新麥","1582":"信錦",
    "1583":"程泰","1584":"精剛","1585":"亞福","1586":"和勤","1589":"美亞",
    "1590":"亞德客-KY","1591":"精成科","1603":"華電","1605":"華新","1608":"華榮",
    "1609":"大亞","1612":"宏泰","1701":"中化","1703":"南亞塑膠","1710":"東聯",
    "1711":"永光","1712":"興農","1713":"國化","1717":"長興","1718":"中纖",
    "1722":"台肥","1723":"中碳","1725":"元禎","1726":"永記","1730":"花仙子",
    "1733":"五鼎","1760":"寶齡富錦","1762":"中化生","1777":"生達","1784":"訓達",
    "1786":"科妍","1788":"創源","1789":"神隆","1790":"晶碩","2002":"中鋼",
    "2006":"東和鋼鐵","2007":"燁興","2008":"高興昌","2009":"第一銅","2010":"春源鋼鐵",
    "2012":"春雨","2013":"中鋼構","2014":"中鴻","2015":"豐興","2049":"上銀",
    "2059":"川湖","2061":"風神","2062":"橋椿","2063":"世鎧","2064":"晉勝",
    "2065":"世德","2103":"台橡","2201":"裕隆","2204":"中華","2207":"和泰車",
    "2208":"台船","2301":"光寶科","2303":"聯電","2308":"台達電","2317":"鴻海",
    "2324":"仁寶","2325":"矽品","2327":"國巨","2329":"華泰","2330":"台積電",
    "2332":"友訊","2337":"旺宏","2340":"光磊","2344":"華邦電","2345":"智邦",
    "2348":"海韻電","2351":"順德","2353":"宏碁","2354":"鴻準","2356":"英業達",
    "2357":"華碩","2363":"矽統","2364":"倫飛","2365":"昆盈","2368":"金像電",
    "2379":"瑞昱","2382":"廣達","2383":"台光電","2384":"勝華","2388":"威盛",
    "2393":"億光","2397":"友通","2399":"映泰","2406":"國碩","2408":"南亞科",
    "2409":"友達","2412":"中華電","2421":"建準","2429":"銘異",
    "2436":"偉詮電","2439":"美律","2441":"超豐","2449":"京元電子","2454":"聯發科",
    "2455":"全訊","2460":"建通","2461":"光聖","2462":"探針","2471":"資通",
    "2474":"可成","2475":"華映","2476":"鉅祥","2492":"華新科","2498":"宏達電",
    "2501":"國建","2502":"宏國","2504":"國產","2505":"國揚","2506":"太設",
    "2509":"全坤建","2511":"太子","2514":"龍邦","2515":"中工","2516":"新建",
    "2520":"冠德","2524":"京城","2525":"寶徠","2526":"山林水","2528":"皇翔",
    "2534":"宏盛","2535":"達欣工","2536":"宏普","2537":"聯上發","2538":"基泰",
    "2540":"愛山林","2542":"興富發","2543":"皇昌","2545":"皇龍","2546":"根基",
    "2547":"日勝生","2548":"華固","2597":"潤弘","2712":"長榮航","2717":"中華航",
    "2718":"晶華","2719":"燦星旅","2720":"鳳凰旅遊","2723":"美食-KY","2726":"雅茗-KY",
    "2801":"彰銀","2809":"京城銀","2812":"台中商銀","2816":"旺旺保","2820":"華票",
    "2823":"中壽","2824":"台壽保","2826":"南山人壽","2834":"臺企銀","2838":"聯邦銀",
    "2849":"安泰銀","2850":"新產","2851":"中再保","2852":"第一保","2855":"統一證",
    "2856":"元富證","2860":"新產","2867":"三商壽","2880":"華南金","2881":"富邦金",
    "2882":"國泰金","2883":"開發金","2884":"玉山金","2885":"元大金","2886":"兆豐金",
    "2887":"台新金","2888":"新光金","2889":"國票金","2890":"永豐金","2891":"中信金",
    "2892":"第一金","2903":"遠百","2905":"三商行","2908":"欣泰","2910":"統領",
    "2911":"麗嬰房","2912":"統一超","2914":"統一企業","2915":"潤泰全","2923":"鼎固-KY",
    "3003":"健和興","3008":"大立光","3013":"映興","3014":"聯陽","3016":"嘉晶",
    "3017":"奇鋐","3023":"信邦","3028":"增你強","3030":"晶碩","3031":"佰鴻",
    "3032":"偉訓","3033":"威健","3034":"聯詠","3035":"智原",
    "3036":"文曄","3037":"欣興","3038":"全台晶像","3040":"遠見","3041":"揚智",
    "3042":"晶技","3043":"科風","3044":"健鼎","3045":"台灣大","3046":"建碁",
    "3047":"訊舟","3048":"益登","3049":"和鑫","3050":"鈦鼎","3051":"力特",
    "3052":"夆典","3057":"喬鼎","3059":"華晶科","3062":"建漢","3085":"碩天",
    "3105":"穩懋","3141":"晶宏","3143":"資板","3176":"基亞生技","3189":"景碩",
    "3209":"全科","3211":"順達科","3228":"金麗科","3230":"昱泓","3231":"緯創",
    "3260":"威剛","3376":"新日興","3406":"玉晶光","3437":"榮創","3443":"創意",
    "3481":"群創","3491":"昱捷","3494":"誠研","3515":"華擎","3518":"柏騰",
    "3529":"力旺","3530":"晶相光","3532":"台勝科","3533":"嘉澤","3536":"也思科",
    "3548":"嘉利","3550":"映泰","3576":"新日光","3583":"辛耘","3593":"力致",
    "3596":"智易","3597":"通昱","3607":"谷崧","3617":"碩天","3653":"健策",
    "3661":"世芯-KY","3686":"達能","3691":"碩禾","3706":"神達","3707":"漢磊",
    "3711":"日月光投控","4106":"雃博","4108":"懷特","4116":"明基醫","4117":"葡萄王",
    "4118":"進階","4119":"旭富","4121":"優盛","4123":"晟德","4126":"太醫",
    "4128":"中天","4130":"健亞","4133":"亞諾法","4141":"龍燈-KY","4142":"國光生",
    "4144":"宜特","4147":"中裕","4148":"全福生技","4152":"台微體","4160":"基亞",
    "4162":"智擎","4163":"鐿鈦","4168":"醣聯","4171":"瑞基","4174":"浩鼎",
    "4175":"杏昕","4205":"中華食","4209":"鐿鈦","4743":"合一","4904":"遠傳",
    "4906":"正文","4919":"新唐","4938":"和碩","4958":"臻鼎-KY","4960":"誠美材",
    "4961":"天鈺","4966":"譜瑞-KY","4968":"立積","5009":"榮剛","5274":"信驊",
    "5347":"世界先進","5354":"豐藝","5371":"中光電","5375":"智伸科","5483":"中美晶",
    "5512":"力麒","5515":"建國工程","5519":"隆大","5521":"工信","5522":"遠雄",
    "5523":"廣宇","5525":"順天","5531":"鉅陞","5533":"三發地產","5534":"長虹",
    "5536":"聖暉","5538":"日新","5546":"永信建","5876":"上海商銀","5878":"台中銀",
    "5880":"合庫金","5903":"全家","5904":"寶雅","6005":"群益金鼎證","6120":"輔信",
    "6146":"耕興","6147":"頎邦","6194":"成大生技","6214":"精誠","6227":"原相",
    "6230":"超眾","6239":"力成","6244":"茂迪","6245":"立康","6257":"矽格",
    "6263":"普萊德","6269":"台郡","6271":"同欣電","6274":"台燿","6277":"宏正",
    "6278":"台表科","6285":"啟碁","6409":"旭隼","6414":"樺漢","6415":"矽力-KY",
    "6442":"光聖","6446":"藥華藥","6456":"GIS-KY","6461":"益登","6488":"環球晶",
    "6505":"台塑化","6510":"精測","6523":"達發科技","6533":"晶心科","6547":"泰福-KY",
    "6643":"M31","6669":"緯穎","6770":"力積電","6789":"采鈺",
    "6803":"崇越電","8044":"網家","8081":"致新","8210":"勝一",
}

def fetch_stock_list():
    """
    建立股票清單 CSV
    優先從 FinMind 抓取（有 Token 且非付費版才消耗大量配額）
    免費版改用靜態對照表，避免消耗 API 配額
    """
    log.info("📋 建立股票清單...")
    token = CONFIG.get("fm_token", "")

    # 有付費 Token 才呼叫 API（付費版無請求數限制）
    if token:
        log.info("  使用 FinMind API 抓取完整清單（付費Token）")
        df, ok = fm_get("TaiwanStockInfo")
        if ok and not df.empty:
            df = df[df["type"].isin(["twse", "tpex"])].copy()
            df = df[df["stock_id"].str.match(r'^[0-9]{4}$')]
            df = df[~df["stock_id"].str.startswith("00")]
            exclude_kw = ["ETF","ETN","指數","權證","特別","存託","基金","REITs"]
            mask = ~df["stock_name"].str.contains("|".join(exclude_kw), na=False)
            df = df[mask].reset_index(drop=True)
            log.info(f"  ✅ FinMind 取得 {len(df)} 檔股票")
            return df, True

    # 免費版：先嘗試從 TWSE/TPEx 官方網頁抓完整清單（台灣IP可用，不消耗FinMind配額）
    log.info("  嘗試從 TWSE/TPEx 官方網頁取得完整股票清單...")
    twse_df = fetch_stock_list_from_twse()
    if not twse_df.empty:
        log.info(f"  ✅ TWSE/TPEx 取得 {len(twse_df)} 檔股票（完整清單）")
        return twse_df, True

    # 最後備援：用靜態對照表（407 檔）
    log.info("  使用靜態對照表備援（407 檔）")
    all_ids = sorted(set(s for stocks in SECTOR_STOCKS.values() for s in stocks))
    rows = []
    for sid in all_ids:
        name = STOCK_NAME_MAP.get(sid, sid)
        rows.append({
            "stock_id":   sid,
            "stock_name": name,
            "type":       "twse",
            "industry_category": "",
        })
    df = pd.DataFrame(rows)
    log.info(f"  ✅ 靜態清單建立完成：{len(df)} 檔")
    return df, True


def fetch_stock_list_from_twse():
    """
    從 TWSE（上市）和 TPEx（上櫃）官方 ISIN 頁面抓取完整股票清單
    完全免費、不消耗 FinMind 配額，但需要台灣 IP
    """
    import re
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    rows = []
    exclude_kw = ["ETF","ETN","指數","期信","權","特別","存託","基金","REITs","DR"]

    # ── 上市（TWSE）
    try:
        url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "big5"
        # 解析 HTML 表格
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_td = False; self.cells = []; self.row = []; self.rows = []
            def handle_starttag(self, tag, attrs):
                if tag == "td": self.in_td = True
                elif tag == "tr": self.row = []
            def handle_endtag(self, tag):
                if tag == "td": self.in_td = False
                elif tag == "tr":
                    if self.row: self.rows.append(self.row)
                    self.row = []
            def handle_data(self, data):
                if self.in_td: self.row.append(data.strip())

        parser = TableParser()
        parser.feed(r.text)

        for row in parser.rows:
            if len(row) < 2: continue
            first = row[0]
            if "　" in first:  # 全形空格分隔代號和名稱
                parts = first.split("　")
                if len(parts) >= 2:
                    sid  = parts[0].strip()
                    name = parts[1].strip()
                    if re.match(r'^[0-9]{4}$', sid):
                        if not any(k in name for k in exclude_kw):
                            rows.append({
                                "stock_id": sid, "stock_name": name,
                                "type": "twse", "industry_category": row[3] if len(row)>3 else ""
                            })
        log.info(f"    TWSE 上市：{sum(1 for r in rows if r['type']=='twse')} 檔")
    except Exception as e:
        log.warning(f"    TWSE 抓取失敗：{e}")

    # ── 上櫃（TPEx）
    try:
        url2 = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
        r2 = requests.get(url2, headers=headers, timeout=15)
        r2.encoding = "big5"
        parser2 = TableParser()
        parser2.feed(r2.text)
        tpex_count = 0
        for row in parser2.rows:
            if len(row) < 2: continue
            first = row[0]
            if "　" in first:
                parts = first.split("　")
                if len(parts) >= 2:
                    sid  = parts[0].strip()
                    name = parts[1].strip()
                    if re.match(r'^[0-9]{4}$', sid):
                        if not any(k in name for k in exclude_kw):
                            # 避免重複
                            if not any(r["stock_id"]==sid for r in rows):
                                rows.append({
                                    "stock_id": sid, "stock_name": name,
                                    "type": "tpex", "industry_category": row[3] if len(row)>3 else ""
                                })
                                tpex_count += 1
        log.info(f"    TPEx 上櫃：{tpex_count} 檔")
    except Exception as e:
        log.warning(f"    TPEx 抓取失敗：{e}")

    if rows:
        df = pd.DataFrame(rows).drop_duplicates(subset="stock_id").reset_index(drop=True)
        return df
    return pd.DataFrame()


def fetch_batch(dataset, stock_ids, start_date, label,
               extra_process=None, data_dir=None, batch_size=None):
    """
    通用批次抓取函式
    - 自動分批（batch_size）
    - 每批完成後立即存檔（中途中斷不會全部重來）
    - 碰到頻率上限自動等待（fm_get 已處理）
    - batch_size=None 時自動判斷首次/每日模式
    """
    if batch_size is None:
        batch_size = get_batch_size(data_dir) if data_dir else CONFIG.get("batch_size_first", 150)
    batch_pause = CONFIG.get("batch_pause", 70)
    all_rows    = []
    total       = len(stock_ids)
    batches     = [stock_ids[i:i+batch_size] for i in range(0, total, batch_size)]

    log.info(f"  共 {total} 檔，分 {len(batches)} 批（每批 {batch_size} 檔）")

    for b_idx, batch in enumerate(batches):
        log.info(f"  ── 第 {b_idx+1}/{len(batches)} 批（{batch[0]}～{batch[-1]}）")
        batch_rows = []
        it = tqdm(batch, desc=f"{label} 第{b_idx+1}批") if HAS_TQDM else batch

        for sid in it:
            df, ok = fm_get(dataset, data_id=sid, start_date=start_date)
            if ok and not df.empty:
                df["stock_id"] = sid
                if extra_process:
                    df = extra_process(df)
                batch_rows.append(df)
            time.sleep(CONFIG["request_delay"])

        # 每批完成後立即存檔（合併已有資料）
        if batch_rows and data_dir:
            batch_df = pd.concat(batch_rows, ignore_index=True)
            all_rows.append(batch_df)
            fname = f"{label.replace(' ','_')}.csv"
            save_data(batch_df, fname, data_dir)
            log.info(f"  ✅ 第{b_idx+1}批完成，存入 {fname}")

        # 批次間暫停（最後一批不需要）
        if b_idx < len(batches) - 1:
            log.info(f"  ⏸  批次間暫停 {batch_pause} 秒...")
            for remaining in range(batch_pause, 0, -10):
                print(f"\r  ⏸  下一批開始倒數：{remaining} 秒", end="", flush=True)
                time.sleep(10)
            print()

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ 全部完成，共 {len(result)} 筆 {label} 資料")
        return result, True
    log.warning(f"  ⚠️ 無法取得 {label} 資料")
    return pd.DataFrame(), False


def fetch_institutional_all(stock_ids, start_date, data_dir=None):
    """批次抓取三大法人買賣超（自動分批＋斷點儲存）"""
    log.info(f"📊 抓取三大法人買賣超（{len(stock_ids)} 檔）...")

    def process(df):
        df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
        return df

    bs = get_batch_size(data_dir) if data_dir else None
    return fetch_batch(
        "TaiwanStockInstitutionalInvestorsBuySell",
        stock_ids, start_date, "institutional", process, data_dir, batch_size=bs
    )


def fetch_margin_all(stock_ids, start_date, data_dir=None):
    """批次抓取融資融券（自動分批＋斷點儲存）"""
    log.info(f"💰 抓取融資融券（{len(stock_ids)} 檔）...")
    bs = get_batch_size(data_dir) if data_dir else None
    return fetch_batch(
        "TaiwanStockMarginPurchaseShortSale",
        stock_ids, start_date, "margin", None, data_dir, batch_size=bs
    )




def fetch_financials_all(stock_ids, start_date, data_dir=None):
    """批次抓取財務報表（自動分批＋斷點儲存）"""
    log.info(f"📈 抓取財務報表（{len(stock_ids)} 檔）...")
    target = ["毛利率","營業利益率","每股盈餘","營業收入","GrossMargin","OperatingMargin","BasicEPS"]

    def process(df):
        if "origin_name" in df.columns:
            mask = df["origin_name"].str.contains("|".join(target), case=False, na=False)
            df = df[mask]
        return df

    bs = get_batch_size(data_dir) if data_dir else None
    return fetch_batch(
        "TaiwanStockFinancialStatements",
        stock_ids, start_date, "financials", process, data_dir, batch_size=bs
    )


def fetch_futures_chips(start_date):
    """抓取期貨法人未平倉（大台TX + 小台MTX）"""
    log.info("🔮 抓取期貨法人未平倉...")
    results = {}

    for contract in ["TX", "MTX"]:
        df, ok = fm_get(
            "TaiwanFuturesInstitutionalInvestors",
            data_id=contract, start_date=start_date
        )
        if ok and not df.empty:
            # 欄位：date, name, long_open_interest, long_open_interest_balance,
            #        short_open_interest, short_open_interest_balance,
            #        net_open_interest, net_open_interest_balance (或類似)
            df["contract"] = contract
            results[contract] = df
            log.info(f"  ✅ {contract}：{len(df)} 筆")
        time.sleep(CONFIG["request_delay"])

    # 抓小台全市場未平倉
    df_daily, ok_d = fm_get(
        "TaiwanFuturesDaily",
        data_id="MTX", start_date=start_date
    )
    if ok_d and not df_daily.empty:
        results["MTX_daily"] = df_daily
        log.info(f"  ✅ MTX每日：{len(df_daily)} 筆")
    time.sleep(CONFIG["request_delay"])

    return results


def fetch_price_basic(stock_ids):
    """
    用 yfinance 補充股價基本資料（PE、EPS、毛利率）
    這部分在美國IP也能抓到
    """
    log.info(f"💹 用 yfinance 補充基本財務（{len(stock_ids)} 檔）...")
    try:
        import yfinance as yf
    except ImportError:
        log.warning("  yfinance 未安裝，跳過基本財務")
        return pd.DataFrame(), False

    rows = []
    it = tqdm(stock_ids, desc="yfinance") if HAS_TQDM else stock_ids
    for sid in it:
        for suffix in [".TW", ".TWO"]:
            try:
                info = yf.Ticker(f"{sid}{suffix}").info or {}
                if info.get("regularMarketPrice"):
                    pe  = info.get("trailingPE")
                    eps = info.get("trailingEps")
                    gm  = info.get("grossMargins")
                    if gm and gm < 1: gm = gm * 100
                    rows.append({
                        "stock_id": sid,
                        "price":    info.get("regularMarketPrice"),
                        "pe":       pe,
                        "eps_ttm":  eps,
                        "gross_margin": gm,
                        "market_cap":   info.get("marketCap"),
                        "updated":  datetime.today().strftime("%Y-%m-%d"),
                    })
                    break
            except: pass
        time.sleep(0.1)

    if rows:
        df = pd.DataFrame(rows)
        log.info(f"  ✅ 共 {len(df)} 檔基本財務")
        return df, True
    return pd.DataFrame(), False


# ══════════════════════════════════════════════
# 儲存資料
# ══════════════════════════════════════════════

def save_data(df, filename, data_dir):
    """存成 CSV，若已有舊資料則合併去重"""
    path = Path(data_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    if df.empty:
        log.warning(f"  ⚠️ {filename} 資料為空，跳過儲存")
        return

    if path.exists():
        try:
            old = pd.read_csv(path, dtype=str)
            combined = pd.concat([old, df.astype(str)], ignore_index=True)
            # 依日期和股票代號去重，保留最新
            if "date" in combined.columns and "stock_id" in combined.columns:
                dedup_cols = ["date", "stock_id"]
                if "name" in combined.columns:
                    dedup_cols.append("name")
                combined = combined.drop_duplicates(
                    subset=dedup_cols, keep="last"
                ).sort_values("date")
            df_save = combined
        except Exception as e:
            log.warning(f"  合併舊資料失敗（{e}），直接覆寫")
            df_save = df.astype(str)
    else:
        df_save = df.astype(str)

    df_save.to_csv(path, index=False, encoding="utf-8-sig")
    log.info(f"  💾 已存：{path}（{len(df_save)} 筆）")


def save_futures(results, data_dir):
    """存期貨資料"""
    for key, df in results.items():
        if not df.empty:
            save_data(df, f"futures_{key}.csv", data_dir)


# ══════════════════════════════════════════════
# Git 推送
# ══════════════════════════════════════════════

def git_push(repo_path, commit_msg):
    """自動 git add → commit → push"""
    log.info("🚀 推送資料到 GitHub...")
    try:
        import subprocess
        cmds = [
            ["git", "-C", repo_path, "add", "data/"],
            ["git", "-C", repo_path, "commit", "-m", commit_msg],
            ["git", "-C", repo_path, "push"],
        ]
        for cmd in cmds:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                if "nothing to commit" in result.stdout + result.stderr:
                    log.info("  ℹ️ 無新資料需要推送")
                    return True
                log.error(f"  ❌ Git 錯誤：{result.stderr}")
                return False
        log.info("  ✅ GitHub 推送成功")
        return True
    except Exception as e:
        log.error(f"  ❌ Git 推送失敗：{e}")
        return False


# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="台股量化資料爬蟲")
    parser.add_argument("--stock",    type=str, help="只更新單一股票代號，例如：2454")
    parser.add_argument("--no-push",  action="store_true", help="不推送到 GitHub")
    parser.add_argument("--no-price", action="store_true", help="不抓 yfinance 基本財務")
    parser.add_argument("--token",    type=str, help="FinMind Token（覆蓋設定檔）")
    args = parser.parse_args()

    if args.token:
        CONFIG["fm_token"] = args.token

    today     = datetime.today()
    today_str = today.strftime("%Y-%m-%d")
    data_dir  = CONFIG["data_dir"]

    log.info("=" * 55)
    log.info(f"台股量化資料爬蟲 ── 執行時間：{today_str}")
    log.info(f"FinMind Token：{'已設定' if CONFIG['fm_token'] else '未設定（免費版）'}")
    log.info("=" * 55)

    # 決定要抓的股票清單
    if args.stock:
        stock_ids = [args.stock.strip()]
        log.info(f"單一股票模式：{stock_ids}")
    else:
        stock_ids = ALL_STOCKS
        log.info(f"全量模式：共 {len(stock_ids)} 檔股票")

    # ── 智慧判斷：第一次執行 or 每日更新
    first_run  = is_first_run(data_dir)
    batch_size = get_batch_size(data_dir)
    run_mode   = "首次執行（歷史資料）" if first_run else "每日更新（最新資料）"
    log.info(f"執行模式：{run_mode}")
    log.info(f"批次大小：{batch_size} 檔/批")

    # ── 自動計算起始日期
    start_inst = (today - timedelta(days=get_days("institutional", data_dir))).strftime("%Y-%m-%d")
    start_mg   = (today - timedelta(days=get_days("margin",        data_dir))).strftime("%Y-%m-%d")
    start_fin  = (today - timedelta(days=get_days("financials",    data_dir))).strftime("%Y-%m-%d")
    start_fut  = (today - timedelta(days=get_days("futures",       data_dir))).strftime("%Y-%m-%d")

    log.info(f"三大法人起始：{start_inst}")
    log.info(f"融資融券起始：{start_mg}")
    log.info(f"財報起始    ：{start_fin}")
    log.info(f"期貨起始    ：{start_fut}")

    success_count = 0

    # ── 1. 股票清單
    df_list, ok = fetch_stock_list()
    if ok:
        save_data(df_list, "stock_list.csv", data_dir)
        success_count += 1

    # ── 2. 三大法人
    df_inst, ok = fetch_institutional_all(stock_ids, start_inst, data_dir)
    if ok:
        save_data(df_inst, "institutional.csv", data_dir)
        success_count += 1

    # ── 3. 融資融券
    df_mg, ok = fetch_margin_all(stock_ids, start_mg, data_dir)
    if ok:
        save_data(df_mg, "margin.csv", data_dir)
        success_count += 1

    # ── 4. 財務報表
    df_fin, ok = fetch_financials_all(stock_ids, start_fin, data_dir)
    if ok:
        save_data(df_fin, "financials.csv", data_dir)
        success_count += 1

    # ── 5. 期貨籌碼
    futures_data = fetch_futures_chips(start_fut)
    if futures_data:
        save_futures(futures_data, data_dir)
        success_count += 1

    # ── 6. yfinance 基本財務
    if not args.no_price:
        df_price, ok = fetch_price_basic(stock_ids)
        if ok:
            save_data(df_price, "price_basic.csv", data_dir)
            success_count += 1

    # ── 7. 寫入更新時間紀錄
    meta = pd.DataFrame([{
        "updated_at": today_str,
        "stock_count": len(stock_ids),
        "success_modules": success_count,
    }])
    save_data(meta, "last_update.csv", data_dir)

    log.info("=" * 55)
    log.info(f"✅ 完成！成功模組：{success_count}/6")
    log.info(f"📁 資料已存至：{Path(data_dir).absolute()}")

    # ── 8. 推送 GitHub
    if not args.no_push:
        commit_msg = CONFIG["github_commit_msg"].format(date=today_str)
        git_push(CONFIG["github_repo_path"], commit_msg)
    else:
        log.info("ℹ️ 跳過 GitHub 推送（--no-push）")

    log.info("=" * 55)


if __name__ == "__main__":
    main()
