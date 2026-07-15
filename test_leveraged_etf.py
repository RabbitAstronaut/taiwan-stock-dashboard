"""
test_leveraged_etf.py — leveraged_etf.py 核心計算邏輯測試
用合成的價格資料驗證（不需要真實網路連線），執行：py test_leveraged_etf.py
"""
import os
import sys
import tempfile
import shutil

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import leveraged_etf as le

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        print(f"✅ {name}")
        passed += 1
    else:
        print(f"❌ {name}")
        failed += 1


def make_fake_price_csv(path, start_price=50.0, days=250, trend=0.0003, vol=0.02, seed=42):
    """產生一組合成的價格序列（含隨機漲跌）存成CSV，供測試用"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    rets = rng.normal(trend, vol, size=days)
    prices = start_price * (1 + rets).cumprod()
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
        "Close": prices, "Volume": rng.integers(1000, 50000, size=days),
    })
    df.to_csv(path, index=False)
    return df


def run():
    tmpdir = tempfile.mkdtemp()
    le.DATA_DIR = tmpdir
    le.PRICES_DIR = os.path.join(tmpdir, "prices")
    le.CACHE_PATH = os.path.join(tmpdir, "leveraged_etf_price_cache.json")
    os.makedirs(le.PRICES_DIR, exist_ok=True)

    # 產生合成價格資料：00631L 走勢向上，用來測試一次投入模擬
    make_fake_price_csv(os.path.join(le.PRICES_DIR, "00631L.csv"),
                         start_price=100.0, days=250, trend=0.0015, vol=0.025)
    for t in ["00685L", "00663L", "00675L"]:
        make_fake_price_csv(os.path.join(le.PRICES_DIR, f"{t}.csv"),
                             start_price=80.0, days=250, trend=0.0005, vol=0.02, seed=hash(t) % 1000)

    # ── 交易成本計算
    cost = le.calculate_trading_cost(100000, side="buy")
    check("交易成本：手續費有算出來(未免除)", cost["fee"] > 0)
    check("交易成本：買進不收證交稅", cost["tax"] == 0)
    cost_sell = le.calculate_trading_cost(100000, side="sell")
    check("交易成本：賣出有收證交稅", cost_sell["tax"] > 0)
    cost_min = le.calculate_trading_cost(100, side="buy")  # 金額很小，應該吃最低手續費
    check("交易成本：小額交易吃最低手續費", cost_min["fee"] == le.DEFAULT_MIN_FEE)

    # ── 最大回撤
    series = pd.Series([100, 110, 90, 95, 80, 120], index=pd.date_range("2026-01-01", periods=6))
    dd = le.calculate_max_drawdown(series)
    check(f"最大回撤：從110跌到80應約為-27.3% (實際{dd['max_drawdown_pct']})",
          abs(dd["max_drawdown_pct"] - (-27.27)) < 1)

    # ── 一次投入模擬（Phase1唯一支援的買進方式）
    df_00631l = pd.read_csv(os.path.join(le.PRICES_DIR, "00631L.csv"))
    start_date = df_00631l["date"].iloc[30]
    end_date = df_00631l["date"].iloc[-1]
    result = le.simulate_lump_sum("00631L", start_date, end_date, 100000)
    check("案例一：模擬結果沒有error", "error" not in result)
    check("案例一：使用實際ETF歷史價格(非指數×2捷徑)", result.get("entry_price") is not None and result.get("exit_price") is not None)
    check("案例一：有算出股數", result["shares"] > 0)
    check("案例一：有算出平均成本", result["avg_cost"] > 0)
    check("案例一：有算出期末市值", result["final_market_value"] > 0)
    check("案例一：有算出總損益", "total_pnl" in result)
    check("案例一：有扣除手續費(fee_total>0)", result["fee_total"] > 0)
    check("案例一：有算出最大回撤", result["max_drawdown_pct"] is not None)
    check("案例一：等值市場曝險=市值×2(正2槓桿)",
          abs(result["estimated_market_exposure"] - result["final_market_value"] * 2) <= 2)

    # ── 找不到資料的情況要誠實回報error，不能生假資料
    bad_result = le.simulate_lump_sum("99999X", "2026-01-01", "2026-06-01", 100000)
    check("案例：不存在的標的回傳error，不產生假資料", "error" in bad_result)

    # ── 日期區間內沒有交易日的情況
    no_data_result = le.simulate_lump_sum("00631L", "2099-01-01", "2099-06-01", 100000)
    check("案例：未來日期(無交易日)回傳error", "error" in no_data_result)

    # ── 案例二：四檔比較，不用大盤報酬×2取代實際報酬
    compare = le.compare_etfs()
    check("案例二：四檔都有比較結果", len(compare) == 4)
    check("案例二：每檔都是用實際價格算報酬(不是共用同一個數字)",
          len({c.get("return_20d_pct") for c in compare if c.get("data_available")}) > 1)
    for c in compare:
        if c["data_available"]:
            check(f"{c['ticker']} 有20日報酬", c.get("return_20d_pct") is not None)
            check(f"{c['ticker']} 有20日最大回撤", c.get("max_drawdown_20d_pct") is not None)
            check(f"{c['ticker']} 買賣價差資料誠實顯示為None(尚未接入)", c.get("bid_ask_spread_pct") is None)

    # ── 市場狀態（沒有attack_engine時的保守預設）
    state = le.get_market_state_note(attack_engine_module=None)
    check("市場狀態：沒有攻擊引擎時預設為僅限模擬觀察", state["strategy"] == "僅限模擬觀察")

    # ── 資料異常偵測（單日變動超過50%視為資料錯誤，不是真實走勢）
    normal_series = pd.Series([100, 101, 99, 102, 100.5], index=pd.date_range("2026-01-01", periods=5))
    has_anom, _ = le.detect_price_anomaly(normal_series)
    check("異常偵測：正常走勢不誤判", has_anom is False)
    anomaly_series = pd.Series([100, 101, 99, 10, 100.5], index=pd.date_range("2026-01-01", periods=5))
    has_anom2, anom_dates = le.detect_price_anomaly(anomaly_series)
    check("異常偵測：單日暴跌90%正確抓到", has_anom2 is True and len(anom_dates) > 0)

    # ── 分割處理：改用分割後的乾淨資料，不是整組停用（依Rex指示修正）
    split_series = pd.Series(
        [300, 305, 298, 11, 11.2, 11.5, 11.8],  # 第4天發生分割斷崖(298→11)
        index=pd.date_range("2026-01-01", periods=7)
    )
    clean, split_detected, split_date = le.get_clean_price_series(split_series, lookback_days=10)
    check("分割處理：正確偵測到分割事件", split_detected is True)
    check("分割處理：分割後序列只保留分割後的資料(3筆)", len(clean) == 3)
    check("分割處理：分割後序列的值都是分割後的價格(11附近)", clean.max() < 20)

    no_split_series = pd.Series([100, 101, 99, 102, 100.5], index=pd.date_range("2026-01-01", periods=5))
    clean2, split_detected2, _ = le.get_clean_price_series(no_split_series)
    check("分割處理：沒有分割時回傳原始完整序列", split_detected2 is False and len(clean2) == len(no_split_series))

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
