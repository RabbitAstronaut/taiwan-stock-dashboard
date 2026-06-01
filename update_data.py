"""
update_data.py  ── 台股量化系統 V4 後端爬蟲
═══════════════════════════════════════════════════════════════
架構說明：
  本機（台灣IP）或 GitHub Actions 執行，抓取 FinMind API 資料
  存成 CSV 放入 data/ 資料夾，由 Streamlit Cloud 前端讀取

輸出 CSV：
  data/stock_info.csv          股票清單（上市+上櫃）
  data/chips_data.csv          三大法人＋融資券
  data/financial_data.csv      財務報表（毛利率/營益率/EPS）
  data/futures_data.csv        期貨法人未平倉＋全市場未平倉
  data/shareholder_data.csv    大戶持股結構（500張以上）
  data/prices/{stock_id}.csv   個股日K線（yfinance）
  data/last_update.json        更新時間戳記

使用方式：
  python update_data.py                    全量更新
  python update_data.py --only chips       只更新籌碼
  python update_data.py --only prices      只更新K線
  python update_data.py --force            強制重新下載
═══════════════════════════════════════════════════════════════
"""

import os, sys, json, time, argparse, logging
import requests
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path

# ── 套件安裝確認
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False
    print("⚠️ yfinance 未安裝，K線資料將跳過。請執行：pip install yfinance")

# ══════════════════════════════════════════════════════════════
# ▌ 設定區（請修改這裡）
# ══════════════════════════════════════════════════════════════
CONFIG = {
    # FinMind API Token
    # 免費版留空；付費版填入可大幅提升請求限制
    # 申請：https://finmindtrade.com/analysis/#/Sponsor/signin
    "fm_token": os.environ.get("FINMIND_TOKEN", ""),

    # GitHub repo 本地路徑（用於 git push）
    "github_repo_path": ".",
    "github_commit_msg": "chore: auto update {date}",

    # 資料目錄
    "data_dir":   "data",
    "prices_dir": "data/prices",

    # ── 是否使用全台股模式（付費版用）
    # True = 從 FinMind stock_info.csv 取得所有上市櫃股票（約1700檔）
    # False = 只跑 SECTOR_STOCKS 定義的 295 檔
    "use_full_market": False,

    # ── 歷史資料天數（第一次執行）
    "days_chips_first":       60,
    "days_financials_first": 730,   # 財報需2年確保年報完整
    "days_futures_first":     30,
    "days_shareholder_first":180,
    "days_prices_first":     365,

    # ── 每日更新天數
    "days_chips_daily":        3,
    "days_financials_daily":  90,
    "days_futures_daily":      3,
    "days_shareholder_daily": 30,
    "days_prices_daily":       5,

    # ── API 效能（免費版）
    "request_delay":    0.8,   # 每次請求間隔（秒）
    "batch_size_first": 150,   # 第一次批次大小
    "batch_size_daily": 999,   # 每日更新不分批
    "batch_pause":       70,   # 批次間暫停（秒）

    # ── API 效能（付費版，use_paid=True 時生效）
    "use_paid": False,
    "request_delay_paid":  0.2,   # 付費版請求間隔（秒）
    "batch_size_paid":     999,   # 付費版不分批
    "batch_pause_paid":      5,   # 付費版批次間隔（秒）
}

# ══════════════════════════════════════════════════════════════
# ▌ 掃描股票清單（依產業分類）
# ══════════════════════════════════════════════════════════════
SECTOR_STOCKS = {
    "半導體IC設計":  [
        "2454","2379","3034","2303","2449","2388","3515","5347","4966","3443",
        "6770","2344","2408","3653","6523","3661","6415","3035","2363","6533",
        "3141","6643","3014","5274","4968","6269","3596","6789","2436","3494",
        "2471","6510","3532","6147","8081","3209","6278","2406","6803","4919",
        "3037","6230","5269","4961","3376","6214","3706","2397","3228","6442",
    ],
    "晶圓代工封測":  [
        "2330","2337","2325","3711","6274","2368","2351","6257","3016","2455",
        "6271","2441","6239","3105","2329","3530","5483","6488","2383","3038",
        "2475","3260","2340","2393","2409","3481","3691","6146","3057","4142",
    ],
    "AI伺服器雲端":  [
        "2382","2356","2353","2357","6669","3231","2301","2324","3017","2399",
        "3533","6461","3583","6285","3023","2383","3189","5269","4938","3706",
        "3062","2397","5354","2365","3044","3057","6230","3085","6442","6146",
        "2332","3376","6257","2462","6510","3597","2406","6214","3228","2308",
    ],
    "消費電子手機":  [
        "2317","2354","2498","3008","2439","3406","4958","2327","3036","2429",
        "6278","2474","4961","2421","2393","6120","2308","6277","3376","6415",
        "4906","3028","5371","2049","3017","2365","2364","3034","2332","6285",
        "3059","6271","2340","3030","3023","2351","1590","3533","2460",
    ],
    "電動車綠能":    [
        "2308","6415","5483","6244","1590","1504","1514","1537","8210","1560",
        "2207","2201","2204","1605","1603","1608","1609","1612","5009","1466",
        "1710","1711","3211","6409","3593","3576","3548","2327","2399","6257",
    ],
    "網通5G":        [
        "2412","4904","3045","2332","2345","3047","6456","4906","3518","6277",
        "3062","6285","6227","3059","6409","3707","4960","6510","3596","2348",
    ],
    "金融銀行保險":  [
        "2881","2882","2891","2886","2887","2884","2885","2892","2880","5880",
        "2801","2820","2834","2838","2849","2850","2851","2852","2855","2856",
        "2867","2883","2888","2889","2890","5876","5878","2823","2824","6005",
    ],
    "石化塑膠鋼鐵":  [
        "6505","1301","1303","1326","1402","2002","1101","1102","2006","2007",
        "2008","2009","2010","2012","2013","2014","2015","1304","1305","1307",
    ],
    "營建不動產":    [
        "5522","2528","2534","2511","2597","2515","5533","5536","5546","2543",
        "2535","2536","2537","2538","2540","2542","2545","2546","2547","2548",
    ],
    "生技醫療":      [
        "4743","1789","4144","4147","6446","1760","4174","4162","4141","6547",
        "4106","4108","4119","4121","4123","4126","4128","4130","4133","4148",
    ],
    "零售百貨電商":  [
        "2912","2903","2915","5904","2910","2905","2908","2911","2914","2923",
        "8044","5903","2718","2719","2720","1210","1215","1216","1217","1218",
    ],
    "機械工具機":    [
        "2049","1590","1560","2059","2061","2062","2063","2064","2065","2201",
        "2204","2207","2208","1580","1582","1583","1584","1585","1586","1589",
    ],
    "光電面板":      [
        "3481","2409","2475","5371","3008","3406","3691","2383","3028","3049",
        "3059","2455","3031","3033","3034","3040","3041","3042","3046","3048",
    ],
}

# 所有不重複股票代號
ALL_STOCKS = sorted(set(s for v in SECTOR_STOCKS.values() for s in v))

# ══════════════════════════════════════════════════════════════
# ▌ 日誌設定
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("update_data.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# ▌ 智慧更新：判斷今天是否已下載
# ══════════════════════════════════════════════════════════════
STATUS_FILE = Path(CONFIG["data_dir"]) / "download_status.json"

def _load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except:
            pass
    return {}

def _save_status(status: dict):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8"
    )

def already_today(module: str) -> bool:
    """今天已下載過，則跳過"""
    today = datetime.today().strftime("%Y-%m-%d")
    if _load_status().get(module) == today:
        log.info(f"  ⏭️  {module} 今天已下載，跳過")
        return True
    return False

def mark_done(module: str):
    status = _load_status()
    status[module] = datetime.today().strftime("%Y-%m-%d")
    _save_status(status)

def is_first_run() -> bool:
    """institutional.csv 不存在或資料量極少，視為首次執行"""
    path = Path(CONFIG["data_dir"]) / "chips_data.csv"
    if not path.exists():
        return True
    try:
        return len(pd.read_csv(path)) < 100
    except:
        return True

def get_days(key: str) -> int:
    first = is_first_run()
    return CONFIG.get(f"days_{key}_first", 60) if first \
        else CONFIG.get(f"days_{key}_daily", 3)

def get_batch_size() -> int:
    return CONFIG["batch_size_first"] if is_first_run() \
        else CONFIG["batch_size_daily"]

# ══════════════════════════════════════════════════════════════
# ▌ FinMind API 核心
# ══════════════════════════════════════════════════════════════
FM_BASE = "https://api.finmindtrade.com/api/v4/data"

def fm_get(
    dataset: str,
    data_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, bool]:
    """
    呼叫 FinMind API。
    - 自動帶入 Token（若有設定）
    - 觸發頻率上限時自動等待重試（最多5次）
    - 回傳 (DataFrame, 成功?)
    """
    params: dict = {"dataset": dataset}
    if data_id:    params["data_id"]    = data_id
    if start_date: params["start_date"] = start_date
    if end_date:   params["end_date"]   = end_date
    if CONFIG["fm_token"]:
        params["token"] = CONFIG["fm_token"]

    for attempt in range(5):
        try:
            r = requests.get(FM_BASE, params=params, timeout=20)
            j = r.json()

            if j.get("status") == 200 and isinstance(j.get("data"), list):
                return pd.DataFrame(j["data"]), True

            msg = j.get("msg", "unknown")
            # 頻率上限：自動等待
            if "upper limit" in msg.lower() or "reach" in msg.lower():
                wait = 65 if attempt == 0 else 120
                log.warning(
                    f"  ⏳ API 頻率上限（第{attempt+1}次），等待 {wait}s | "
                    f"id={data_id}"
                )
                for s in range(wait, 0, -5):
                    print(f"\r  ⏳ {s:3d}s", end="", flush=True)
                    time.sleep(5)
                print()
                continue

            log.warning(f"  ⚠️  {msg} | {dataset} id={data_id}")
            return pd.DataFrame(), False

        except Exception as e:
            log.warning(f"  連線失敗（{attempt+1}/5）：{e}")
            time.sleep(2 ** attempt)

    log.error(f"  ❌ 放棄 | {dataset} id={data_id}")
    return pd.DataFrame(), False

# ══════════════════════════════════════════════════════════════
# ▌ CSV 儲存（增量合併去重）
# ══════════════════════════════════════════════════════════════
def save_csv(
    df: pd.DataFrame,
    filename: str,
    data_dir: str,
    dedup_cols: list[str] | None = None,
) -> None:
    """將 df 合併到既有 CSV，依 dedup_cols 去重後儲存。"""
    path = Path(data_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    if df.empty:
        log.warning(f"  ⚠️  {filename} 空資料，跳過")
        return

    df_str = df.astype(str)

    if path.exists():
        try:
            old     = pd.read_csv(path, dtype=str)
            merged  = pd.concat([old, df_str], ignore_index=True)
            if dedup_cols and all(c in merged.columns for c in dedup_cols):
                merged = (
                    merged
                    .drop_duplicates(subset=dedup_cols, keep="last")
                    .sort_values(dedup_cols[0])
                )
            path.write_text(
                merged.to_csv(index=False), encoding="utf-8"
            )
            log.info(f"  💾 {filename}（{len(merged)} 筆）")
            return
        except Exception as e:
            log.warning(f"  合併失敗（{e}），直接覆寫")

    path.write_text(df_str.to_csv(index=False), encoding="utf-8")
    log.info(f"  💾 {filename}（{len(df_str)} 筆，新建）")


def save_price_csv(df: pd.DataFrame, stock_id: str, prices_dir: str) -> None:
    """個股 K 線：合併增量資料後儲存。"""
    path = Path(prices_dir) / f"{stock_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    df_new           = df.copy()
    df_new.index.name = "date"
    df_new           = df_new.reset_index().astype(str)

    if path.exists():
        try:
            old    = pd.read_csv(path, dtype=str)
            merged = (
                pd.concat([old, df_new], ignore_index=True)
                .drop_duplicates(subset=["date"], keep="last")
                .sort_values("date")
            )
            path.write_text(merged.to_csv(index=False), encoding="utf-8")
            return
        except:
            pass

    path.write_text(df_new.to_csv(index=False), encoding="utf-8")

# ══════════════════════════════════════════════════════════════
# ▌ 通用批次抓取引擎
# ══════════════════════════════════════════════════════════════
def fetch_batch(
    dataset:       str,
    stock_ids:     list[str],
    start_date:    str,
    label:         str,
    csv_filename:  str,
    data_dir:      str,
    extra_process  = None,
    batch_size:    int | None = None,
    dedup_cols:    list[str] | None = None,
) -> tuple[pd.DataFrame, bool]:
    """
    批次抓取多檔股票資料。
    - 分批執行，每批完成後立即存檔（中途中斷不全部重來）
    - 批次間暫停 batch_pause 秒（讓 API 計數器重置）
    """
    bs      = batch_size or get_batch_size()
    pause   = CONFIG["batch_pause"]
    batches = [stock_ids[i:i+bs] for i in range(0, len(stock_ids), bs)]
    log.info(f"  {len(stock_ids)} 檔 / 分 {len(batches)} 批（{bs} 檔/批）")

    all_rows: list[pd.DataFrame] = []

    for b_idx, batch in enumerate(batches):
        log.info(f"  ── 批次 {b_idx+1}/{len(batches)}（{batch[0]}～{batch[-1]}）")
        batch_rows: list[pd.DataFrame] = []
        it = tqdm(batch, desc=f"{label} {b_idx+1}/{len(batches)}", leave=False) \
             if HAS_TQDM else batch

        for sid in it:
            df, ok = fm_get(dataset, data_id=sid, start_date=start_date)
            if ok and not df.empty:
                df["stock_id"] = sid
                if extra_process:
                    df = extra_process(df)
                if not df.empty:
                    batch_rows.append(df)
            time.sleep(CONFIG["request_delay"])

        if batch_rows:
            batch_df = pd.concat(batch_rows, ignore_index=True)
            all_rows.append(batch_df)
            # 每批立即存檔
            save_csv(batch_df, csv_filename, data_dir, dedup_cols)

        # 批次間暫停（最後一批不需要）
        if b_idx < len(batches) - 1:
            log.info(f"  ⏸  暫停 {pause}s...")
            for s in range(pause, 0, -10):
                print(f"\r  ⏸  下批倒數 {s:3d}s", end="", flush=True)
                time.sleep(10)
            print()

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ {label} 完成，{len(result)} 筆")
        return result, True

    log.warning(f"  ⚠️  {label} 無資料")
    return pd.DataFrame(), False

# ══════════════════════════════════════════════════════════════
# ▌ 模組一：股票清單
# ══════════════════════════════════════════════════════════════
def run_stock_info(data_dir: str):
    log.info("━" * 55)
    log.info("📋 模組 1/6：股票清單")
    log.info("━" * 55)

    # 有 Token 才呼叫 API（TaiwanStockInfo 耗大量配額）
    if CONFIG["fm_token"]:
        df, ok = fm_get("TaiwanStockInfo")
        if ok and not df.empty:
            # 過濾上市上櫃，排除 ETF
            df = df[df["type"].isin(["twse", "tpex"])].copy()
            df = df[df["stock_id"].str.match(r"^\d{4}$")]
            df = df[~df["stock_id"].str.startswith("00")]
            exclude = ["ETF","ETN","指數","權證","特別","存託","基金","REITs"]
            df = df[~df["stock_name"].str.contains("|".join(exclude), na=False)]
            save_csv(df.reset_index(drop=True), "stock_info.csv", data_dir)
            return
        log.warning("  FinMind 股票清單失敗，改用靜態對照表")

    # 靜態備援
    NAME_MAP = {
        "2330":"台積電","2317":"鴻海","2454":"聯發科","2382":"廣達","2308":"台達電",
        "2303":"聯電","2881":"富邦金","2882":"國泰金","2886":"兆豐金","2891":"中信金",
        "2412":"中華電","4904":"遠傳","3045":"台灣大","2002":"中鋼","1301":"台塑",
        "6669":"緯穎","3661":"世芯","2379":"瑞昱","3034":"聯詠","6415":"矽力",
    }
    rows = [
        {"stock_id": s, "stock_name": NAME_MAP.get(s, s), "type": "twse"}
        for s in ALL_STOCKS
    ]
    save_csv(pd.DataFrame(rows), "stock_info.csv", data_dir)

# ══════════════════════════════════════════════════════════════
# ▌ 模組二：三大法人＋融資券（chips_data.csv）
# ══════════════════════════════════════════════════════════════
def run_chips(stock_ids: list[str], data_dir: str):
    log.info("━" * 55)
    log.info(f"📊 模組 2/6：三大法人（{len(stock_ids)} 檔）")
    log.info("━" * 55)

    start = (datetime.today() - timedelta(days=get_days("chips"))).strftime("%Y-%m-%d")
    log.info(f"  起始日期：{start}")

    def process_inst(df: pd.DataFrame) -> pd.DataFrame:
        df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
        df["source"] = "institutional"
        return df

    fetch_batch(
        dataset       = "TaiwanStockInstitutionalInvestorsBuySell",
        stock_ids     = stock_ids,
        start_date    = start,
        label         = "三大法人",
        csv_filename  = "chips_data.csv",
        data_dir      = data_dir,
        extra_process = process_inst,
        dedup_cols    = ["date", "stock_id", "name"],
    )

    log.info(f"💰 抓取融資券（{len(stock_ids)} 檔）")

    def process_margin(df: pd.DataFrame) -> pd.DataFrame:
        df["source"] = "margin"
        return df

    fetch_batch(
        dataset       = "TaiwanStockMarginPurchaseShortSale",
        stock_ids     = stock_ids,
        start_date    = start,
        label         = "融資券",
        csv_filename  = "chips_data.csv",
        data_dir      = data_dir,
        extra_process = process_margin,
        dedup_cols    = ["date", "stock_id", "source"],
    )

# ══════════════════════════════════════════════════════════════
# ▌ 模組三：財務報表（financial_data.csv）
# ══════════════════════════════════════════════════════════════
def run_financials(stock_ids: list[str], data_dir: str):
    log.info("━" * 55)
    log.info(f"📈 模組 3/6：財務報表（{len(stock_ids)} 檔）")
    log.info("━" * 55)

    start  = (datetime.today() - timedelta(days=get_days("financials"))).strftime("%Y-%m-%d")

    # origin_name（中文）和 type（英文）的關鍵字
    target_origin = ["毛利率","營業利益率","每股盈餘","營業收入","基本每股","稀釋每股"]
    target_type   = ["GrossMargin","OperatingMargin","BasicEPS","DilutedEPS","Revenue",
                     "EPS","Gross","Operating","NetIncome"]

    def process_fin(df: pd.DataFrame) -> pd.DataFrame:
        # 不篩選，保留所有財報項目（讓 app.py 自行解析）
        # 這樣毛利率、營益率、EPS 都能抓到
        return df

    fetch_batch(
        dataset       = "TaiwanStockFinancialStatements",
        stock_ids     = stock_ids,
        start_date    = start,
        label         = "財報",
        csv_filename  = "financial_data.csv",
        data_dir      = data_dir,
        extra_process = process_fin,
        dedup_cols    = ["date", "stock_id", "origin_name"],
    )

# ══════════════════════════════════════════════════════════════
# ▌ 模組四：期貨籌碼（futures_data.csv）
# ══════════════════════════════════════════════════════════════
def run_futures(data_dir: str):
    log.info("━" * 55)
    log.info("🔮 模組 4/6：期貨籌碼（TX＋MTX）")
    log.info("━" * 55)

    start = (datetime.today() - timedelta(days=get_days("futures"))).strftime("%Y-%m-%d")
    rows: list[pd.DataFrame] = []

    # 大台外資＋小台三大法人未平倉
    for contract in ["TX", "MTX"]:
        df, ok = fm_get("TaiwanFuturesInstitutionalInvestors",
                        data_id=contract, start_date=start)
        if ok and not df.empty:
            df["contract"] = contract
            df["source"]   = "institutional"
            rows.append(df)
            log.info(f"  ✅ {contract} 法人：{len(df)} 筆")
        time.sleep(CONFIG["request_delay"])

    # 小台全市場未平倉量
    df_d, ok_d = fm_get("TaiwanFuturesDaily", data_id="MTX", start_date=start)
    if ok_d and not df_d.empty:
        df_d["contract"] = "MTX"
        df_d["source"]   = "daily"
        rows.append(df_d)
        log.info(f"  ✅ MTX_daily：{len(df_d)} 筆")

    if rows:
        combined = pd.concat(rows, ignore_index=True)
        save_csv(combined, "futures_data.csv", data_dir,
                 dedup_cols=["date", "contract", "source", "name"])

# ══════════════════════════════════════════════════════════════
# ▌ 模組五：大戶持股結構（shareholder_data.csv）
# ══════════════════════════════════════════════════════════════
def run_shareholder(stock_ids: list[str], data_dir: str):
    log.info("━" * 55)
    log.info(f"🏦 模組 5/6：大戶持股結構（{len(stock_ids)} 檔）")
    log.info("━" * 55)

    start = (datetime.today() - timedelta(days=get_days("shareholder"))).strftime("%Y-%m-%d")

    fetch_batch(
        dataset      = "TaiwanStockHoldingSharesPer",
        stock_ids    = stock_ids,
        start_date   = start,
        label        = "大戶持股",
        csv_filename = "shareholder_data.csv",
        data_dir     = data_dir,
        dedup_cols   = ["date", "stock_id", "HoldingSharesLevel"],
    )

# ══════════════════════════════════════════════════════════════
# ▌ 模組六：個股 K 線（yfinance）
# ══════════════════════════════════════════════════════════════
def run_prices(stock_ids: list[str], prices_dir: str):
    if not HAS_YF:
        log.warning("  yfinance 未安裝，跳過 K 線下載")
        return

    log.info("━" * 55)
    log.info(f"📉 模組 6/6：個股 K 線（{len(stock_ids)} 檔）")
    log.info("━" * 55)

    days     = get_days("prices")
    today_s  = datetime.today().strftime("%Y-%m-%d")
    ok_cnt   = skip_cnt = fail_cnt = 0

    it = tqdm(stock_ids, desc="K線") if HAS_TQDM else stock_ids

    for sid in it:
        price_path = Path(prices_dir) / f"{sid}.csv"

        # 智慧增量：確認上次更新日期
        start_dt = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        if price_path.exists():
            try:
                last_date = pd.read_csv(price_path, usecols=["date"])["date"].max()
                if last_date >= today_s:
                    skip_cnt += 1
                    continue
                # 只補缺少的部分
                start_dt = (
                    pd.Timestamp(last_date) + timedelta(days=1)
                ).strftime("%Y-%m-%d")
            except:
                pass

        # 下載（先試上市 .TW，再試上櫃 .TWO）
        df_p = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            try:
                df_tmp = yf.download(
                    f"{sid}{suffix}",
                    start=start_dt,
                    auto_adjust=True,
                    progress=False,
                    timeout=15,
                )
                if df_tmp is None or df_tmp.empty:
                    continue
                df_tmp.columns = [
                    c[0] if isinstance(c, tuple) else c
                    for c in df_tmp.columns
                ]
                df_tmp = df_tmp.dropna(
                    subset=["Close","Open","High","Low","Volume"]
                )
                if len(df_tmp) > 0:
                    df_p = df_tmp
                    break
            except Exception as e:
                if "Rate" in str(e) or "Too Many" in str(e):
                    log.warning("  yfinance 限速，等待 30s...")
                    time.sleep(30)

        if not df_p.empty:
            save_price_csv(df_p, sid, prices_dir)
            ok_cnt += 1
        else:
            fail_cnt += 1

        time.sleep(0.3)

    log.info(
        f"  ✅ K線：成功 {ok_cnt} / 跳過 {skip_cnt} / 失敗 {fail_cnt} 檔"
    )

# ══════════════════════════════════════════════════════════════
# ▌ 模組七：yfinance 基本財務（price_basic.csv）
# ══════════════════════════════════════════════════════════════
def run_price_basic(stock_ids: list[str], data_dir: str):
    """
    用 yfinance 抓取：P/E、毛利率、市值
    本機台灣IP可正常使用，Streamlit Cloud 讀 CSV 不直接呼叫
    """
    if not HAS_YF:
        log.warning("  yfinance 未安裝，跳過基本財務下載")
        return

    log.info("━" * 55)
    log.info(f"💹 模組 7：yfinance 基本財務（{len(stock_ids)} 檔）")
    log.info("━" * 55)

    rows = []
    it   = tqdm(stock_ids, desc="基本財務") if HAS_TQDM else stock_ids
    ok_cnt = fail_cnt = 0

    for sid in it:
        for suffix in [".TW", ".TWO"]:
            try:
                info = yf.Ticker(f"{sid}{suffix}").info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                if not price:
                    continue
                gm = info.get("grossMargins")
                if gm and abs(gm) < 1:
                    gm = round(gm * 100, 2)
                rows.append({
                    "stock_id":     sid,
                    "price":        round(float(price), 2),
                    "pe":           info.get("trailingPE"),
                    "eps_ttm":      info.get("trailingEps"),
                    "gross_margin": gm,
                    "revenue_ttm":  info.get("totalRevenue"),
                    "market_cap":   info.get("marketCap"),
                    "updated":      datetime.today().strftime("%Y-%m-%d"),
                })
                ok_cnt += 1
                break
            except:
                pass
            time.sleep(0.1)
        else:
            fail_cnt += 1

    if rows:
        df = pd.DataFrame(rows)
        # 去重保留最新
        save_csv(df, "price_basic.csv", data_dir, dedup_cols=["stock_id"])
        log.info(f"  ✅ 基本財務：成功 {ok_cnt} / 失敗 {fail_cnt} 檔")
    else:
        log.warning("  ⚠️ 無基本財務資料")


# ══════════════════════════════════════════════════════════════
# ▌ Git 推送
# ══════════════════════════════════════════════════════════════
def git_push(repo_path: str, commit_msg: str):
    import subprocess
    log.info("🚀 推送至 GitHub...")
    for cmd in [
        ["git", "-C", repo_path, "add",    "data/"],
        ["git", "-C", repo_path, "commit", "-m", commit_msg],
        ["git", "-C", repo_path, "push"],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if "nothing to commit" in r.stdout + r.stderr:
                log.info("  ℹ️  無新資料")
                return True
            log.error(f"  ❌ {r.stderr[:200]}")
            return False
    log.info("  ✅ 推送成功")
    return True

# ══════════════════════════════════════════════════════════════
# ▌ 主程式
# ══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="台股量化系統 V4 資料爬蟲")
    parser.add_argument("--stock",       type=str,  help="只更新單一股票代號")
    parser.add_argument("--only",        type=str,  default="",
        help="只執行指定模組：info/chips/financials/futures/shareholder/prices")
    parser.add_argument("--no-push",     action="store_true", help="不推送 GitHub")
    parser.add_argument("--no-price",    action="store_true", help="跳過 K 線下載")
    parser.add_argument("--force",       action="store_true", help="忽略今日已下載判斷")
    parser.add_argument("--token",       type=str,  help="覆蓋 FinMind Token")
    parser.add_argument("--full-market", action="store_true",
        help="全台股模式（付費版）：從 stock_info.csv 取得所有上市櫃股票")
    parser.add_argument("--paid",        action="store_true",
        help="付費版模式：加快請求速度，縮短批次間隔")
    args = parser.parse_args()

    if args.token:
        CONFIG["fm_token"] = args.token

    # 付費版加速設定
    if args.paid or CONFIG.get("use_paid"):
        CONFIG["request_delay"] = CONFIG["request_delay_paid"]
        CONFIG["batch_size_first"] = CONFIG["batch_size_paid"]
        CONFIG["batch_size_daily"] = CONFIG["batch_size_paid"]
        CONFIG["batch_pause"]     = CONFIG["batch_pause_paid"]
        log.info("⚡ 付費版加速模式啟動")

    today_str  = datetime.today().strftime("%Y-%m-%d")
    data_dir   = CONFIG["data_dir"]
    prices_dir = CONFIG["prices_dir"]
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(prices_dir).mkdir(parents=True, exist_ok=True)

    first  = is_first_run()
    mode   = "首次執行（歷史資料）" if first else "每日更新（增量）"
    only   = args.only.lower()
    force  = args.force

    log.info("═" * 55)
    log.info(f"台股量化系統 V4 資料爬蟲  {today_str}")
    log.info(f"Token  : {'已設定' if CONFIG['fm_token'] else '未設定（免費版）'}")
    log.info(f"模式   : {mode}")
    log.info(f"批次   : {get_batch_size()} 檔/批")
    log.info("═" * 55)

    # ── 決定股票池
    if args.stock:
        # 指定單一股票
        stock_ids = [args.stock.strip()]
    elif args.full_market or CONFIG.get("use_full_market"):
        # 全台股模式：從 stock_info.csv 讀取（需先執行 --only info）
        info_path = Path(data_dir) / "stock_info.csv"
        if info_path.exists():
            try:
                df_info   = pd.read_csv(info_path, dtype=str)
                stock_ids = df_info["stock_id"].dropna().unique().tolist()
                # 過濾：4位數字代號，排除 ETF（00開頭）
                stock_ids = [s for s in stock_ids
                             if s.isdigit() and len(s)==4 and not s.startswith("00")]
                log.info(f"🌏 全台股模式：從 stock_info.csv 讀取 {len(stock_ids)} 檔")
            except Exception as e:
                log.warning(f"stock_info.csv 讀取失敗（{e}），改用預設清單")
                stock_ids = ALL_STOCKS
        else:
            log.warning("stock_info.csv 不存在，先執行 --only info 建立股票清單")
            log.warning("改用預設清單（295 檔）")
            stock_ids = ALL_STOCKS
    else:
        # 預設：使用 SECTOR_STOCKS 定義的清單（295 檔）
        stock_ids = ALL_STOCKS

    log.info(f"股票池 : {len(stock_ids)} 檔")

    def _should(module):
        return (not only or only == module) and (force or not already_today(module))

    # ── 1. 股票清單
    if _should("info"):
        run_stock_info(data_dir)
        mark_done("info")

    # ── 2. 三大法人＋融資券（個股 + ETF 動態清單）
    if _should("chips"):
        # 動態讀取 etf_dividend_data.csv 取得 ETF 清單（與 Tab5 自動同步）
        etf_ids = []
        etf_csv = Path(data_dir) / "etf_dividend_data.csv"
        if etf_csv.exists():
            try:
                df_etf = pd.read_csv(etf_csv, dtype=str)
                if "stock_id" in df_etf.columns:
                    etf_ids = df_etf["stock_id"].dropna().unique().tolist()
                    log.info(f"  從 etf_dividend_data.csv 讀取 {len(etf_ids)} 檔 ETF")
            except Exception as e:
                log.warning(f"  etf_dividend_data.csv 讀取失敗：{e}")
        else:
            log.warning("  etf_dividend_data.csv 不存在，籌碼只更新個股")
        # 合併個股+ETF清單（去重）
        chips_ids = list(dict.fromkeys(stock_ids + etf_ids))
        log.info(f"  籌碼清單：{len(stock_ids)} 個股 + {len(etf_ids)} ETF = {len(chips_ids)} 檔")
        run_chips(chips_ids, data_dir)
        mark_done("chips")

    # ── 3. 財務報表
    if _should("financials"):
        run_financials(stock_ids, data_dir)
        mark_done("financials")

    # ── 4. 期貨籌碼
    if _should("futures"):
        run_futures(data_dir)
        mark_done("futures")

    # ── 5. 大戶持股
    if _should("shareholder"):
        run_shareholder(stock_ids, data_dir)
        mark_done("shareholder")

    # ── 6. K 線
    if not args.no_price and _should("prices"):
        run_prices(stock_ids, prices_dir)
        mark_done("prices")

    # ── 7. yfinance 基本財務（PE/毛利率）
    if _should("basic"):
        run_price_basic(stock_ids, data_dir)
        mark_done("basic")

    # ── 更新時間戳記
    meta = {
        "updated_at":  today_str,
        "stock_count": len(stock_ids),
        "mode":        mode,
        "modules":     [k for k in ["info","chips","financials","futures","shareholder","prices"]
                        if not only or only == k],
    }
    (Path(data_dir) / "last_update.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log.info("═" * 55)
    log.info("✅ 全部完成")
    log.info("═" * 55)

    # ── Git Push
    if not args.no_push:
        commit_msg = CONFIG["github_commit_msg"].format(date=today_str)
        git_push(CONFIG["github_repo_path"], commit_msg)


if __name__ == "__main__":
    main()
