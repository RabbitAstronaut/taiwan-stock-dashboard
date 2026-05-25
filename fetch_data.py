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
    # FinMind Token（免費版留空，有帳號請填入）
    # 申請：https://finmindtrade.com/analysis/#/Sponsor/signin
    "fm_token": "",

    # GitHub 設定（用於自動 push）
    # 若不想自動 push，執行時加 --no-push 參數
    "github_repo_path": ".",          # GitHub repo 本地路徑（預設當前目錄）
    "github_commit_msg": "Auto update: {date} 台股資料更新",

    # 資料存放目錄
    "data_dir": "data",

    # 抓取天數設定
    "days_institutional": 60,    # 三大法人近幾天
    "days_margin":        60,    # 融資餘額近幾天
    "days_financials":   730,    # 財報近幾天（建議2年）
    "days_futures":       30,    # 期貨近幾天

    # API 請求間隔（秒），避免超過頻率限制
    "request_delay": 0.5,
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

    for attempt in range(3):
        try:
            r = requests.get(FM_BASE, params=params, timeout=20)
            j = r.json()
            if j.get("status") == 200 and isinstance(j.get("data"), list):
                df = pd.DataFrame(j["data"])
                return df, True
            else:
                log.warning(f"  API 回傳異常：{j.get('msg','unknown')} | dataset={dataset} id={data_id}")
                return pd.DataFrame(), False
        except Exception as e:
            log.warning(f"  第{attempt+1}次失敗：{e}")
            time.sleep(2 ** attempt)
    return pd.DataFrame(), False

# ══════════════════════════════════════════════
# 資料抓取函式
# ══════════════════════════════════════════════

def fetch_stock_list():
    """抓取全市場股票清單"""
    log.info("📋 抓取全市場股票清單...")
    df, ok = fm_get("TaiwanStockInfo")
    if ok and not df.empty:
        # 過濾上市上櫃，排除ETF/權證
        df = df[df["type"].isin(["twse", "tpex"])].copy()
        df = df[df["stock_id"].str.match(r"^\d{4}$")]
        df = df[~df["stock_id"].str.startswith("00")]
        exclude_kw = ["ETF","ETN","指數","權證","特別","存託","基金","REITs"]
        mask = ~df["stock_name"].str.contains("|".join(exclude_kw), na=False)
        df = df[mask].reset_index(drop=True)
        log.info(f"  ✅ 取得 {len(df)} 檔股票")
        return df, True
    log.error("  ❌ 股票清單抓取失敗")
    return pd.DataFrame(), False


def fetch_institutional_all(stock_ids, start_date):
    """批次抓取三大法人買賣超"""
    log.info(f"📊 抓取三大法人買賣超（{len(stock_ids)} 檔）...")
    all_rows = []
    it = tqdm(stock_ids, desc="三大法人") if HAS_TQDM else stock_ids

    for sid in it:
        df, ok = fm_get(
            "TaiwanStockInstitutionalInvestorsBuySell",
            data_id=sid, start_date=start_date
        )
        if ok and not df.empty:
            # 欄位：date, stock_id, name, buy, sell
            df["stock_id"] = sid
            df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
            all_rows.append(df)
        time.sleep(CONFIG["request_delay"])

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ 共 {len(result)} 筆法人資料")
        return result, True
    log.warning("  ⚠️ 無法取得法人資料")
    return pd.DataFrame(), False


def fetch_margin_all(stock_ids, start_date):
    """批次抓取融資融券"""
    log.info(f"💰 抓取融資融券（{len(stock_ids)} 檔）...")
    all_rows = []
    it = tqdm(stock_ids, desc="融資融券") if HAS_TQDM else stock_ids

    for sid in it:
        df, ok = fm_get(
            "TaiwanStockMarginPurchaseShortSale",
            data_id=sid, start_date=start_date
        )
        if ok and not df.empty:
            # 欄位：date, stock_id, MarginPurchaseBuy, MarginPurchaseSell,
            #        MarginPurchaseCashRepayment, MarginPurchaseYesterdayBalance,
            #        MarginPurchaseTodayBalance, MarginPurchaseLimit,
            #        ShortSaleBuy, ShortSaleSell, ...
            df["stock_id"] = sid
            all_rows.append(df)
        time.sleep(CONFIG["request_delay"])

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ 共 {len(result)} 筆融資資料")
        return result, True
    log.warning("  ⚠️ 無法取得融資資料")
    return pd.DataFrame(), False


def fetch_financials_all(stock_ids, start_date):
    """批次抓取財務報表"""
    log.info(f"📈 抓取財務報表（{len(stock_ids)} 檔）...")
    all_rows = []
    it = tqdm(stock_ids, desc="財務報表") if HAS_TQDM else stock_ids

    for sid in it:
        df, ok = fm_get(
            "TaiwanStockFinancialStatements",
            data_id=sid, start_date=start_date
        )
        if ok and not df.empty:
            # 欄位：date, stock_id, type, value, origin_name
            df["stock_id"] = sid
            # 只保留需要的項目
            target = ["毛利率", "營業利益率", "每股盈餘", "營業收入",
                      "GrossMargin", "OperatingMargin", "BasicEPS"]
            mask = df["origin_name"].str.contains(
                "|".join(target), case=False, na=False)
            df = df[mask]
            if not df.empty:
                all_rows.append(df)
        time.sleep(CONFIG["request_delay"])

    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        log.info(f"  ✅ 共 {len(result)} 筆財報資料")
        return result, True
    log.warning("  ⚠️ 無法取得財報資料")
    return pd.DataFrame(), False


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

    # 計算起始日期
    start_inst = (today - timedelta(days=CONFIG["days_institutional"])).strftime("%Y-%m-%d")
    start_mg   = (today - timedelta(days=CONFIG["days_margin"])).strftime("%Y-%m-%d")
    start_fin  = (today - timedelta(days=CONFIG["days_financials"])).strftime("%Y-%m-%d")
    start_fut  = (today - timedelta(days=CONFIG["days_futures"])).strftime("%Y-%m-%d")

    success_count = 0

    # ── 1. 股票清單
    df_list, ok = fetch_stock_list()
    if ok:
        save_data(df_list, "stock_list.csv", data_dir)
        success_count += 1

    # ── 2. 三大法人
    df_inst, ok = fetch_institutional_all(stock_ids, start_inst)
    if ok:
        save_data(df_inst, "institutional.csv", data_dir)
        success_count += 1

    # ── 3. 融資融券
    df_mg, ok = fetch_margin_all(stock_ids, start_mg)
    if ok:
        save_data(df_mg, "margin.csv", data_dir)
        success_count += 1

    # ── 4. 財務報表
    df_fin, ok = fetch_financials_all(stock_ids, start_fin)
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
