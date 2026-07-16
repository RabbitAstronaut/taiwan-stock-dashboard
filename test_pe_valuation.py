"""
test_pe_valuation.py — pe_valuation.py 核心計算邏輯測試
執行：py test_pe_valuation.py
"""
import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(__file__))
import pe_valuation as pv

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


def run():
    tmpdir = tempfile.mkdtemp()
    pv.DATA_DIR = tmpdir
    pv.PE_CACHE_PATH = os.path.join(tmpdir, "pe_history_cache.json")

    # ── 模擬 fetch_pe_history 的快取結果（100天PE序列，10~30之間均勻分布，最新值=25）
    import numpy as np
    rng = np.random.default_rng(1)
    pe_vals = list(rng.uniform(10, 30, 99)) + [25.0]  # 最後一筆固定=25，方便驗證百分位
    dates = [f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}" for i in range(100)]
    fake_cache = {
        "2330": {
            "fetched_at": pv._now(), "status": "已更新",
            "data": [{"date": d, "PER": round(p, 2), "PBR": 5.0, "dividend_yield": 2.0}
                     for d, p in zip(dates, pe_vals)],
        }
    }
    with open(pv.PE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(fake_cache, f)

    # ── 百分位計算
    result = pv.calculate_pe_percentile("2330")
    check("目前PE正確抓到最新值(25.0)", result["current_pe"] == 25.0)
    check("樣本數=100", result["sample_size"] == 100)
    check("百分位有算出來(樣本數足夠)", result["percentile"] is not None)
    check("均值有算出來", result["mean"] is not None)
    check("前值有算出來(不是最新值本身)", result["previous"] is not None and result["previous"] != 25.0)

    # ── 樣本不足的情況（只給10天資料）
    small_cache = {
        "9999": {
            "fetched_at": pv._now(), "status": "已更新",
            "data": [{"date": f"2026-07-{i+1:02d}", "PER": 20.0, "PBR": 3.0, "dividend_yield": 1.0}
                     for i in range(10)],
        }
    }
    with open(pv.PE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(small_cache, f)
    result_small = pv.calculate_pe_percentile("9999")
    check("樣本不足時百分位=None(不硬算)", result_small["percentile"] is None)
    check("樣本不足時status有說明原因", "樣本數" in result_small["status"])

    # ── 查無資料的股票
    result_none = pv.calculate_pe_percentile("00000")
    check("查無資料時current_pe=None", result_none["current_pe"] is None)
    check("查無資料時status有說明", result_none["status"] == "查無本益比歷史資料")

    # ── 評等分類
    stars5, label5 = pv.classify_valuation_rating(10)  # 百分位10% → 顯著低估
    check("百分位10% → 5星", stars5 == 5)
    stars1, label1 = pv.classify_valuation_rating(90)  # 百分位90% → 顯著昂貴
    check("百分位90% → 1星", stars1 == 1)
    stars0, label0 = pv.classify_valuation_rating(None)  # 無資料
    check("百分位None → 0星(資料不足)", stars0 == 0)
    check("0星文字有說明資料不足", "資料不足" in label0)

    # ── PEG計算（模擬rex_scores.json）
    rex_data = {
        "calculated_at": "2026-07-16",
        "scores": [
            {"stock_id": "2330", "name": "台積電", "eps_yoy_val": 25.0},
            {"stock_id": "8888", "name": "負成長股", "eps_yoy_val": -10.0},
        ],
    }
    with open(os.path.join(tmpdir, "rex_scores.json"), "w", encoding="utf-8") as f:
        json.dump(rex_data, f)

    # 重新寫入2330的PE快取(確保還在)
    with open(pv.PE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(fake_cache, f)

    peg_result = pv.calculate_peg("2330")
    check(f"PEG計算：PE25/成長25% = 1.0 (實際{peg_result['peg']})", peg_result["peg"] == 1.0)

    peg_negative = pv.calculate_peg("8888")
    check("EPS負成長時不計算PEG", peg_negative["peg"] is None)
    check("EPS負成長時有說明原因", "負" in peg_negative["status"] or "無資料" in peg_negative["status"])

    peg_no_data = pv.calculate_peg("77777")
    check("不在評分名單時PEG=None", peg_no_data["peg"] is None)

    # ── 整合摘要
    summary = pv.build_valuation_summary("2330")
    check("摘要包含目前PE", summary["current_pe"] == 25.0)
    check("摘要包含PEG", summary["peg"] == 1.0)
    check("摘要包含評等星數", summary["rating_stars"] in (0, 1, 2, 3, 4, 5))

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
