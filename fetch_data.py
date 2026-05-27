"""
fetch_data.py  v3
─────────────────────────────────────────────────────────────
台股量化儀表板 ── 本機資料爬蟲
執行環境：你的電腦（台灣IP），每天收盤後執行一次

功能：
  1. 股票清單（FinMind）
  2. 三大法人買賣超（FinMind）
  3. 融資融券（FinMind）
  4. 財務報表（FinMind）
  5. 大戶持股結構（FinMind）★新增
  6. 期貨籌碼（FinMind）
  7. 每日K線股價（yfinance）★新增
  8. 基本財務（yfinance）

智慧更新：
  - 今天已下載的模組 → 自動跳過
  - 只補最新資料，不重複下載
  - 第一次執行自動抓歷史
─────────────────────────────────────────────────────────────
"""

import os, sys, time, argparse, logging, json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ══════════════════════════════════════════════
# 設定（請修改這裡）
# ══════════════════════════════════════════════
CONFIG = {
    "fm_token":          "",       # FinMind Token
    "github_repo_path":  ".",      # GitHub repo 本地路徑
    "github_commit_msg": "Auto update: {date} 台股資料更新",
    "data_dir":          "data",
    "prices_dir":        "data/prices",  # 個股K線存放位置

    # ── 歷史資料天數（第一次執行用）
    "days_institutional_first": 60,
    "days_margin_first":        60,
    "days_financials_first":   730,
    "days_futures_first":       30,
    "days_shareholder_first":  180,
    "days_prices_first":       365,  # K線抓1年

    # ── 每日更新天數（已有資料後用）
    "days_institutional_daily": 3,
    "days_margin_daily":        3,
    "days_financials_daily":   90,
    "days_futures_daily":       3,
    "days_shareholder_daily":  30,   # 大戶資料每週公布，抓30天確保不漏
    "days_prices_daily":        5,   # K線只補最新5天

    # ── API設定
    "request_delay":     0.8,
    "batch_size_first":  150,
    "batch_size_daily":  999,
    "batch_pause":        70,
}

# ══════════════════════════════════════════════
# 股票清單
# ══════════════════════════════════════════════
SECTOR_STOCKS = {
    "半導體IC設計":       ["2454","2379","3034","2303","2449","2388","3515","5347","4966","3443","6770","2344","2408","3653","6523","3661","6415","3035","2363","6533","3141","6643","3014","5274","4968","6269","3596","6789","2436","3494","2471","6510","3532","6147","8081","3209","6278","2406","6803","4919","3037","6230","5269","4961","3376","6214","3706","2397","3228","6442"],
    "晶圓代工封測":       ["2330","2337","2325","3711","6274","2368","2351","6257","3016","2455","6271","2441","6239","3105","2329","3530","5483","6488","2383","3038","2475","3260","2340","2393","2409","3481","3691","6146","3057","4142"],
    "AI伺服器雲端":       ["2382","2356","2353","2357","6669","3231","2301","2324","3017","2399","3533","6461","3583","6285","3023","2383","3189","5269","4938","3706","3062","2397","5354","2365","3044","3057","6230","3085","6442","6146","2332","3376","6257","2462","6510","3597","2406","6214","3228","2308","3003"],
    "消費電子手機":       ["2317","2354","2498","3008","2439","3406","4958","2327","3036","2429","6278","2474","4961","2421","2393","6120","2308","6277","3376","6415","4906","3028","5371","2049","3017","2365","2364","3034","2332","6285","3059","6271","2340","3030","3023","2351","1590","3533","2460"],
    "電動車綠能":         ["2308","6415","5483","6244","1590","1504","1514","1537","8210","1560","2207","2201","2204","1605","1603","1608","1609","1612","5009","1466","1710","1711","3211","6409","3593","3576","3548","2327","2399","6257","3037","1519","1513","1515","1516","1529","1530","1477","3013"],
    "網通5G":             ["2412","4904","3045","2332","2345","3047","6456","4906","3518","6277","3062","6285","6227","3059","6409","3707","4960","6510","3596","2348","6263","6414","3686","3230","3049","3376","6146","3023","3706","2397"],
    "金融銀行保險":       ["2881","2882","2891","2886","2887","2884","2885","2892","2880","5880","2801","2820","2834","2838","2849","2850","2851","2852","2855","2856","2867","2883","2888","2889","2890","5876","5878","2823","2824","6005","2809","2812","2816","2826","2860"],
    "石化塑膠鋼鐵":       ["6505","1301","1303","1326","1402","2002","1101","1102","2006","2007","2008","2009","2010","2012","2013","2014","2015","1304","1305","1307","1308","1309","1310","1312","1313","1314","1317","1319","1321","2103","1703","1711","1712","1713","1717","1718","1722","1723","1725","1726"],
    "營建不動產":         ["5522","2528","2534","2511","2597","2515","5533","5536","5546","2543","2535","2536","2537","2538","2540","2542","2545","2546","2547","2548","5512","5515","5519","5521","5523","5525","5531","5534","5538","2501","2502","2504","2505","2506","2509","2514","2516","2520","2524","2525"],
    "生技醫療":           ["4743","1789","4144","4147","6446","1760","4174","4162","4141","6547","4106","4108","4119","4121","4123","4126","4128","4130","4133","4148","4152","4160","4163","4168","4171","4175","1777","1701","1733","1762","1784","1786","1788","1790","4116","4117","4118","4209","6194","6245"],
    "零售百貨電商":       ["2912","2903","2915","5904","2910","2905","2908","2911","2914","2923","8044","5903","2718","2719","2720","1210","1215","1216","1217","1218","1219","1225","1227","1229","1230","1232","1233","1234","1236","1256","2712","2717","2723","2726"],
    "機械工具機":         ["2049","1590","1560","2059","2061","2062","2063","2064","2065","2201","2204","2207","2208","1580","1582","1583","1584","1585","1586","1589","1591","2014","1513","1515","1516","1519","1520","1521","1522","1524","1525","1526","1527","1528","1530","1531","1532","1533","1535","1536"],
    "光電面板":           ["3481","2409","2475","5371","3008","3406","3691","2383","3028","3049","3059","2455","3031","3033","3034","3040","3041","3042","3046","3048","3050","3051","2340","2393","2460","2461","3530","3550","6277","2384","3032","3043","3052","5274","3596"],
}

ALL_STOCKS = sorted(set(s for v in SECTOR_STOCKS.values() for s in v))

# ══════════════════════════════════════════════
# 日誌
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
# 智慧更新判斷
# ══════════════════════════════════════════════
def get_last_download_date(module_name, data_dir):
    """讀取上次下載日期"""
    path = Path(data_dir) / "download_status.json"
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        return status.get(module_name)
    except:
        return None

def set_download_date(module_name, data_dir, date_str=None):
    """記錄下載日期"""
    path = Path(data_dir) / "download_status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except:
        status = {}
    status[module_name] = date_str or datetime.today().strftime("%Y-%m-%d")
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

def already_downloaded_today(module_name, data_dir):
    """今天已下載過？"""
    last = get_last_download_date(module_name, data_dir)
    today = datetime.today().strftime("%Y-%m-%d")
    if last == today:
        log.info(f"  ⏭️  {module_name} 今天已下載，跳過")
        return True
    return False

def is_first_run(data_dir):
    """是否為第一次執行（institutional.csv 不存在或太小）"""
    path = Path(data_dir) / "institutional.csv"
    if not path.exists():
        return True
    try:
        df = pd.read_csv(path)
        return len(df) < 100
    except:
        return True

def get_days(key, data_dir):
    if key == "financials":
        return CONFIG["days_financials_first"] if is_first_run(data_dir) else CONFIG["days_financials_daily"]
    if key == "shareholder":
        return CONFIG["days_shareholder_first"] if is_first_run(data_dir) else CONFIG["days_shareholder_daily"]
    if key == "prices":
        return CONFIG["days_prices_first"] if is_first_run(data_dir) else CONFIG["days_prices_daily"]
    first = is_first_run(data_dir)
    return CONFIG.get(f"days_{key}_first", 60) if first else CONFIG.get(f"days_{key}_daily", 3)

def get_batch_size(data_dir):
    return CONFIG["batch_size_first"] if is_first_run(data_dir) else CONFIG["batch_size_daily"]

# ══════════════════════════════════════════════
# FinMind API
# ══════════════════════════════════════════════
FM_BASE = "https://api.finmindtrade.com/api/v4/data"

def fm_get(dataset, data_id=None, start_date=None, end_date=None, token=None):
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
            if "upper limit" in msg.lower() or "reach" in msg.lower():
                wait = 65 if attempt == 0 else 120
                log.warning(f"  ⏳ API上限（第{attempt+1}次），等待{wait}秒... id={data_id}")
                for remaining in range(wait, 0, -5):
                    print(f"\r  ⏳ 等待中... {remaining}秒", end="", flush=True)
                    time.sleep(5)
                print()
                continue
            log.warning(f"  API異常：{msg} | {dataset} id={data_id}")
            return pd.DataFrame(), False
        except Exception as e:
            log.warning(f"  第{attempt+1}次失敗：{e}")
            time.sleep(2 ** attempt)
    return pd.DataFrame(), False

# ══════════════════════════════════════════════
# 通用批次抓取
# ══════════════════════════════════════════════
def fetch_batch(dataset, stock_ids, start_date, label, extra_process=None,
                data_dir=None, batch_size=None):
    batch_size  = batch_size or get_batch_size(data_dir)
    batch_pause = CONFIG.get("batch_pause", 70)
    all_rows    = []
    batches     = [stock_ids[i:i+batch_size] for i in range(0, len(stock_ids), batch_size)]
    log.info(f"  共 {len(stock_ids)} 檔，分 {len(batches)} 批（每批 {batch_size} 檔）")

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
                if not df.empty:
                    batch_rows.append(df)
            time.sleep(CONFIG["request_delay"])

        if batch_rows and data_dir:
            batch_df = pd.concat(batch_rows, ignore_index=True)
            all_rows.append(batch_df)
            fname = f"{label}.csv"
            save_data(batch_df, fname, data_dir)
            log.info(f"  ✅ 第{b_idx+1}批完成，已存入 {fname}")

        if b_idx < len(batches) - 1:
            log.info(f"  ⏸  批次間暫停 {batch_pause} 秒...")
            for remaining in range(batch_pause, 0, -10):
                print(f"\r  ⏸  {remaining}秒後開始下一批", end="", flush=True)
                time.sleep(10)
            print()

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ 完成，共 {len(result)} 筆 {label}")
        return result, True
    return pd.DataFrame(), False

# ══════════════════════════════════════════════
# 儲存 CSV（合併去重）
# ══════════════════════════════════════════════
def save_data(df, filename, data_dir):
    path = Path(data_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return
    df_save = df.astype(str)
    if path.exists():
        try:
            old = pd.read_csv(path, dtype=str)
            combined = pd.concat([old, df_save], ignore_index=True)
            dedup = ["date", "stock_id"]
            if "name" in combined.columns: dedup.append("name")
            if all(c in combined.columns for c in dedup):
                combined = combined.drop_duplicates(subset=dedup, keep="last")
            combined = combined.sort_values("date") if "date" in combined.columns else combined
            df_save = combined
        except Exception as e:
            log.warning(f"  合併失敗（{e}），直接覆寫")
    df_save.to_csv(path, index=False, encoding="utf-8-sig")
    log.info(f"  💾 {path.name}（{len(df_save)} 筆）")

def save_price_csv(df, stock_id, prices_dir):
    """個股K線存成獨立 CSV"""
    path = Path(prices_dir) / f"{stock_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return
    df_new = df.copy()
    df_new.index.name = "date"
    df_new = df_new.reset_index().astype(str)
    if path.exists():
        try:
            old = pd.read_csv(path, dtype=str)
            combined = pd.concat([old, df_new], ignore_index=True)
            combined = combined.drop_duplicates(subset=["date"], keep="last")
            combined = combined.sort_values("date")
            df_new = combined
        except: pass
    df_new.to_csv(path, index=False, encoding="utf-8-sig")

# ══════════════════════════════════════════════
# 各模組抓取函式
# ══════════════════════════════════════════════

def fetch_stock_list(data_dir):
    """股票清單"""
    log.info("📋 抓取股票清單...")
    token = CONFIG.get("fm_token", "")
    if token:
        df, ok = fm_get("TaiwanStockInfo")
        if ok and not df.empty:
            df = df[df["type"].isin(["twse","tpex"])].copy()
            df = df[df["stock_id"].str.match(r"^[0-9]{4}$")]
            df = df[~df["stock_id"].str.startswith("00")]
            exclude = ["ETF","ETN","指數","權證","特別","存託","基金","REITs"]
            df = df[~df["stock_name"].str.contains("|".join(exclude), na=False)]
            df = df.reset_index(drop=True)
            log.info(f"  ✅ FinMind 取得 {len(df)} 檔")
            save_data(df, "stock_list.csv", data_dir)
            return df, True

    # 靜態備援
    rows = []
    STOCK_NAME_MAP = {
        "2330":"台積電","2317":"鴻海","2454":"聯發科","2382":"廣達","2308":"台達電",
        "2303":"聯電","2881":"富邦金","2882":"國泰金","2886":"兆豐金","2891":"中信金",
        "2412":"中華電","4904":"遠傳","3045":"台灣大","2002":"中鋼","1301":"台塑",
        "1303":"南亞","6505":"台塑化","2357":"華碩","2353":"宏碁","2356":"英業達",
    }
    for sid in ALL_STOCKS:
        rows.append({"stock_id":sid,"stock_name":STOCK_NAME_MAP.get(sid,sid),"type":"twse","industry_category":""})
    df = pd.DataFrame(rows)
    save_data(df, "stock_list.csv", data_dir)
    log.info(f"  ✅ 靜態清單 {len(df)} 檔")
    return df, True


def fetch_institutional_all(stock_ids, start_date, data_dir):
    """三大法人"""
    log.info(f"📊 三大法人（{len(stock_ids)}檔）...")
    def process(df):
        df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
        return df
    return fetch_batch("TaiwanStockInstitutionalInvestorsBuySell",
                       stock_ids, start_date, "institutional", process, data_dir)


def fetch_margin_all(stock_ids, start_date, data_dir):
    """融資融券"""
    log.info(f"💰 融資融券（{len(stock_ids)}檔）...")
    return fetch_batch("TaiwanStockMarginPurchaseShortSale",
                       stock_ids, start_date, "margin", None, data_dir)


def fetch_financials_all(stock_ids, start_date, data_dir):
    """財務報表"""
    log.info(f"📈 財務報表（{len(stock_ids)}檔）...")
    target = ["毛利率","營業利益率","每股盈餘","營業收入","GrossMargin","OperatingMargin","BasicEPS"]
    def process(df):
        if "origin_name" in df.columns:
            mask = df["origin_name"].str.contains("|".join(target), case=False, na=False)
            df = df[mask]
        return df
    return fetch_batch("TaiwanStockFinancialStatements",
                       stock_ids, start_date, "financials", process, data_dir)


def fetch_shareholder_all(stock_ids, start_date, data_dir):
    """
    大戶持股結構（每週公布）
    HoldingSharesLevel: 持股張數級距
    """
    log.info(f"🏦 大戶持股結構（{len(stock_ids)}檔）...")
    def process(df):
        # 只保留 500 張以上的大戶級距
        # 標準：1張=1000股，500張=500,000股
        if "HoldingSharesLevel" in df.columns:
            # 大戶：持股500張（50萬股）以上
            big_levels = [
                "50000",  "100000", "200000", "400000", "600000",
                "800000", "1000000","over_1000000",
                # FinMind 可能的格式
                "50000-100000","100000-200000","200000-400000",
                "400000上","over400000",
            ]
            # 直接保留所有級距，讓 app.py 做分析
        return df
    result, ok = fetch_batch("TaiwanStockHoldingSharesPer",
                             stock_ids, start_date, "shareholder", process, data_dir)
    return result, ok


def fetch_futures_chips(start_date, data_dir):
    """期貨籌碼"""
    log.info("🔮 期貨籌碼...")
    for contract in ["TX","MTX"]:
        df, ok = fm_get("TaiwanFuturesInstitutionalInvestors",
                        data_id=contract, start_date=start_date)
        if ok and not df.empty:
            df["contract"] = contract
            save_data(df, f"futures_{contract}.csv", data_dir)
            log.info(f"  ✅ {contract}：{len(df)} 筆")
        time.sleep(CONFIG["request_delay"])
    df_d, ok_d = fm_get("TaiwanFuturesDaily", data_id="MTX", start_date=start_date)
    if ok_d and not df_d.empty:
        save_data(df_d, "futures_MTX_daily.csv", data_dir)
        log.info(f"  ✅ MTX_daily：{len(df_d)} 筆")
    time.sleep(CONFIG["request_delay"])


def fetch_prices_all(stock_ids, days, prices_dir, data_dir):
    """
    yfinance 抓取個股日線K線
    智慧更新：每檔檢查上次更新日期，只補缺少的資料
    """
    import yfinance as yf
    log.info(f"📉 個股日線K線（{len(stock_ids)}檔，{days}天）...")
    Path(prices_dir).mkdir(parents=True, exist_ok=True)

    today_str = datetime.today().strftime("%Y-%m-%d")
    success = 0; skip = 0; fail = 0

    it = tqdm(stock_ids, desc="K線") if HAS_TQDM else stock_ids
    for sid in it:
        price_path = Path(prices_dir) / f"{sid}.csv"

        # 智慧判斷：檢查這支股票今天是否已下載
        if price_path.exists():
            try:
                existing = pd.read_csv(price_path)
                if "date" in existing.columns and len(existing) > 0:
                    last_date = existing["date"].max()
                    if last_date >= today_str:
                        skip += 1
                        continue
                    # 只補缺少的部分
                    start_dt = (pd.Timestamp(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
                    period_param = None  # 用 start/end 而非 period
                else:
                    start_dt = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
                    period_param = None
            except:
                start_dt = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
                period_param = None
        else:
            start_dt = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
            period_param = None

        # 下載
        df_p = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            try:
                tk = f"{sid}{suffix}"
                if period_param:
                    df_tmp = yf.download(tk, period=period_param,
                                        auto_adjust=True, progress=False, timeout=15)
                else:
                    df_tmp = yf.download(tk, start=start_dt,
                                        auto_adjust=True, progress=False, timeout=15)
                if df_tmp is None or df_tmp.empty:
                    continue
                df_tmp.columns = [c[0] if isinstance(c,tuple) else c for c in df_tmp.columns]
                df_tmp = df_tmp.dropna(subset=["Close","Open","High","Low","Volume"])
                if len(df_tmp) > 0:
                    df_p = df_tmp
                    break
            except Exception as e:
                if "Rate" in str(e) or "Too Many" in str(e):
                    log.warning(f"  ⏳ yfinance 限速，等待 30 秒...")
                    time.sleep(30)
                continue

        if not df_p.empty:
            save_price_csv(df_p, sid, prices_dir)
            success += 1
        else:
            fail += 1
        time.sleep(0.3)

    log.info(f"  ✅ K線：成功 {success} / 跳過 {skip} / 失敗 {fail} 檔")
    return success > 0


def fetch_price_basic(stock_ids, data_dir):
    """yfinance 基本財務（PE/EPS/毛利率）"""
    import yfinance as yf
    log.info(f"💹 基本財務（{len(stock_ids)}檔）...")
    rows = []
    it = tqdm(stock_ids, desc="基本財務") if HAS_TQDM else stock_ids
    for sid in it:
        for suffix in [".TW",".TWO"]:
            try:
                info = yf.Ticker(f"{sid}{suffix}").info or {}
                if info.get("regularMarketPrice"):
                    gm = info.get("grossMargins")
                    if gm and gm < 1: gm *= 100
                    rows.append({
                        "stock_id":     sid,
                        "price":        info.get("regularMarketPrice"),
                        "pe":           info.get("trailingPE"),
                        "eps_ttm":      info.get("trailingEps"),
                        "gross_margin": gm,
                        "market_cap":   info.get("marketCap"),
                        "updated":      datetime.today().strftime("%Y-%m-%d"),
                    })
                    break
            except: pass
        time.sleep(0.1)
    if rows:
        df = pd.DataFrame(rows)
        save_data(df, "price_basic.csv", data_dir)
        log.info(f"  ✅ 基本財務 {len(df)} 檔")
        return df, True
    return pd.DataFrame(), False


def git_push(repo_path, commit_msg):
    """推送到 GitHub"""
    log.info("🚀 推送到 GitHub...")
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
            log.error(f"  ❌ {result.stderr[:200]}")
            return False
    log.info("  ✅ 推送成功")
    return True


# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="台股量化資料爬蟲 v3")
    parser.add_argument("--stock",    type=str, help="只更新單一股票代號")
    parser.add_argument("--no-push",  action="store_true")
    parser.add_argument("--no-price", action="store_true", help="不抓 K線和基本財務")
    parser.add_argument("--only",     type=str, default="",
                        help="只執行指定模組：inst/margin/financials/shareholder/futures/prices/basic")
    parser.add_argument("--force",    action="store_true", help="強制重新下載（忽略今日已下載判斷）")
    parser.add_argument("--token",    type=str)
    args = parser.parse_args()

    if args.token: CONFIG["fm_token"] = args.token

    today     = datetime.today()
    today_str = today.strftime("%Y-%m-%d")
    data_dir  = CONFIG["data_dir"]
    prices_dir= CONFIG["prices_dir"]
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(prices_dir).mkdir(parents=True, exist_ok=True)

    first_run = is_first_run(data_dir)
    run_mode  = "首次執行" if first_run else "每日更新"

    log.info("=" * 60)
    log.info(f"台股量化資料爬蟲 v3 ── {today_str}")
    log.info(f"Token: {'已設定' if CONFIG['fm_token'] else '未設定（免費版）'}")
    log.info(f"模式: {run_mode}")
    log.info("=" * 60)

    stock_ids = [args.stock.strip()] if args.stock else ALL_STOCKS
    log.info(f"股票清單: {len(stock_ids)} 檔")

    only = args.only.lower()
    force = args.force

    # ── 1. 股票清單
    if not only or only == "list":
        if force or not already_downloaded_today("stock_list", data_dir):
            fetch_stock_list(data_dir)
            set_download_date("stock_list", data_dir)

    # ── 2. 三大法人
    if not only or only == "inst":
        if force or not already_downloaded_today("institutional", data_dir):
            start = (today - timedelta(days=get_days("institutional", data_dir))).strftime("%Y-%m-%d")
            log.info(f"三大法人起始: {start}")
            df, ok = fetch_institutional_all(stock_ids, start, data_dir)
            if ok: set_download_date("institutional", data_dir)

    # ── 3. 融資融券
    if not only or only == "margin":
        if force or not already_downloaded_today("margin", data_dir):
            start = (today - timedelta(days=get_days("margin", data_dir))).strftime("%Y-%m-%d")
            log.info(f"融資融券起始: {start}")
            df, ok = fetch_margin_all(stock_ids, start, data_dir)
            if ok: set_download_date("margin", data_dir)

    # ── 4. 財務報表
    if not only or only == "financials":
        if force or not already_downloaded_today("financials", data_dir):
            start = (today - timedelta(days=get_days("financials", data_dir))).strftime("%Y-%m-%d")
            log.info(f"財務報表起始: {start}")
            df, ok = fetch_financials_all(stock_ids, start, data_dir)
            if ok: set_download_date("financials", data_dir)

    # ── 5. 大戶持股
    if not only or only == "shareholder":
        if force or not already_downloaded_today("shareholder", data_dir):
            start = (today - timedelta(days=get_days("shareholder", data_dir))).strftime("%Y-%m-%d")
            log.info(f"大戶持股起始: {start}")
            df, ok = fetch_shareholder_all(stock_ids, start, data_dir)
            if ok: set_download_date("shareholder", data_dir)

    # ── 6. 期貨籌碼
    if not only or only == "futures":
        if force or not already_downloaded_today("futures", data_dir):
            start = (today - timedelta(days=get_days("futures", data_dir))).strftime("%Y-%m-%d")
            log.info(f"期貨籌碼起始: {start}")
            fetch_futures_chips(start, data_dir)
            set_download_date("futures", data_dir)

    # ── 7. 個股K線
    if not args.no_price and (not only or only == "prices"):
        days_p = get_days("prices", data_dir)
        log.info(f"個股K線: {days_p}天（智慧增量更新）")
        ok = fetch_prices_all(stock_ids, days_p, prices_dir, data_dir)
        if ok: set_download_date("prices", data_dir)

    # ── 8. 基本財務
    if not args.no_price and (not only or only == "basic"):
        if force or not already_downloaded_today("price_basic", data_dir):
            df, ok = fetch_price_basic(stock_ids, data_dir)
            if ok: set_download_date("price_basic", data_dir)

    # ── 更新時間記錄
    meta = pd.DataFrame([{
        "updated_at":   today_str,
        "stock_count":  len(stock_ids),
        "run_mode":     run_mode,
    }])
    save_data(meta, "last_update.csv", data_dir)

    log.info("=" * 60)
    log.info("✅ 完成！")

    if not args.no_push:
        commit_msg = CONFIG["github_commit_msg"].format(date=today_str)
        git_push(CONFIG["github_repo_path"], commit_msg)

    log.info("=" * 60)


if __name__ == "__main__":
    main()
