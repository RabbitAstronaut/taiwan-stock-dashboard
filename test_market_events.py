"""
test_market_events.py — market_events.py 回歸測試
執行：py test_market_events.py
使用暫存目錄，不動 data/ 正式資料。案例數字為模擬情境，非任何特定交易日的真實硬寫死數值。
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import market_events as me

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
    me.DATA_DIR = tmpdir
    me.PIVOT_HISTORY_PATH = os.path.join(tmpdir, "market_pivot_history.json")
    me.PRICE_EVENTS_PATH = os.path.join(tmpdir, "market_price_events.json")

    # ── 案例一：盤中大跌、收復60%以上、外資淨空增加、布林未擴張 ──
    # 模擬：前收22000，盤中最低21200(跌800≈-3.6%)，收盤21800(收復75%)，長下影線
    m1 = me.calculate_intraday_recovery_metrics(
        previous_close=22000, open_price=21850, high_price=21900, low_price=21200, close_price=21800
    )
    check("案例一：recovery_metrics有效", m1["valid"])
    check("案例一：跌幅收復比例約75%", m1["recovery_ratio"] is not None and 70 <= m1["recovery_ratio"] <= 80)

    state1 = me.classify_intraday_reversal(m1)
    check("案例一：分類為強烈承接", state1 == "強烈承接")

    bb1 = me.calculate_bollinger_extended(
        bb_mid_today=21600, lb1_today=21500, lb1_prev=21550, lb1_3d_ago=21650,
        ub1_today=22100, low_price_today=21200, close_price_today=21800,
        close_below_streak_prev=0
    )
    check("案例一：布林延伸指標有效", bb1["valid"])
    check("案例一：收盤有回到下軌內", bb1["close_reclaimed_lower_band"] is True)

    bw_chg1 = me.update_bandwidth_change(bb1["bandwidth"], 0.023)
    expanding1 = me.is_lower_band_expanding(bb1["lower_band_slope_3d"], bw_chg1 or 0, bb1["close_below_streak"])
    bstate1 = me.classify_bollinger_event(
        low_price=21200, close_price=21800, lower_band_1=21500,
        expanding=expanding1, close_below_streak=bb1["close_below_streak"], was_below_recently=False
    )
    check("案例一：布林狀態為刺穿後收回（未擴張）", bstate1 == "盤中刺穿後收回")

    fut1 = me.normalize_index_futures_exposure(
        large_net_lots=-9000, small_net_lots=-500, index_price=21800,
        prev_large_net_lots=-7000, prev_small_net_lots=-400
    )
    check("案例一：外資期貨為新增淨空", fut1["posture"] == "新增淨空")

    conflict1 = me.evaluate_market_evidence_conflict(
        intraday_reversal_state=state1, recovery_ratio=m1["recovery_ratio"],
        bollinger_event_state=bstate1, lower_band_expanding=expanding1,
        foreign_futures_change=fut1["large_change_lots"], foreign_cash_flow=-5.0,
    )
    check("案例一：整體狀態為證據衝突", conflict1["state"] == "證據衝突")
    check("案例一：衝突訊息包含外資避險未退", any("外資避險未退" in c for c in conflict1["conflicts"]))

    score1 = me.calculate_price_confirmation_score(
        recovery_ratio=m1["recovery_ratio"], close_location_value=m1["close_location_value"],
        pierced_and_reclaimed=True, closed_back_above_lb1=True, days_without_new_low=0,
    )
    check("案例一：價格確認為暫定分數", score1["is_provisional"] is True)
    check("案例一：暫定分數落在3~5分區間", 3 <= score1["total"] <= 5)
    check("案例一：不得達到第一擊門檻(20分中55%以上才算, 這裡只檢查遠低於門檻)", score1["total"] < 10)

    # ── 案例二：後續三日不破低並重新站回下軌 → 低點確認、暫定轉正式 ──
    me.update_pivot_events("2099-01-10", intraday_low=21200, close_price=21800, previous_close=22000,
                            recovery_metrics=m1, bollinger_state=bstate1, futures_posture=fut1["posture"])
    me.update_pivot_events("2099-01-13", intraday_low=21400, close_price=21900, previous_close=21800,
                            recovery_metrics={"recovery_ratio": None}, bollinger_state="通道內正常波動",
                            futures_posture="與前日持平")
    me.update_pivot_events("2099-01-14", intraday_low=21500, close_price=22000, previous_close=21900,
                            recovery_metrics={"recovery_ratio": None}, bollinger_state="通道內正常波動",
                            futures_posture="空單回補或轉多")
    result2 = me.update_pivot_events("2099-01-15", intraday_low=21600, close_price=22100, previous_close=22000,
                                      recovery_metrics={"recovery_ratio": None}, bollinger_state="通道內正常波動",
                                      futures_posture="空單回補或轉多")
    active_ev = [e for e in result2 if e["event_date"] == "2099-01-10"][0]
    check("案例二：三日不破低後事件被確認", active_ev["confirmation_status"] == "confirmed")

    score2 = me.calculate_price_confirmation_score(
        recovery_ratio=m1["recovery_ratio"], close_location_value=m1["close_location_value"],
        pierced_and_reclaimed=True, closed_back_above_lb1=True, next_day_held_lb1=True,
        days_without_new_low=3, formed_higher_low=True,
    )
    check("案例二：三日確認後轉為正式分數(非暫定)", score2["is_provisional"] is False)
    check("案例二：分數應高於案例一暫定分數", score2["total"] > score1["total"])

    # ── 案例三：隔日跌破低點並收在低檔 → 事件失效 ──
    result3 = me.update_pivot_events("2099-02-10", intraday_low=20000, close_price=20100, previous_close=21000,
                                      recovery_metrics={"recovery_ratio": 70}, bollinger_state="盤中刺穿後收回",
                                      futures_posture="新增淨空")
    result3b = me.update_pivot_events("2099-02-11", intraday_low=19500, close_price=19600, previous_close=20100,
                                       recovery_metrics={"recovery_ratio": None}, bollinger_state="收盤跌破但尚未擴張",
                                       futures_posture="新增淨空")
    ev3 = [e for e in result3b if e["event_date"] == "2099-02-10"][0]
    check("案例三：跌破事件低點後標記失效", ev3["invalidated"] is True)

    # ── 案例四：外資大台淨空增加，但小台微台為淨多 → 不可直接相加口數 ──
    fut4 = me.normalize_index_futures_exposure(
        large_net_lots=-8000, small_net_lots=3000, micro_net_lots=1500, index_price=21800,
    )
    check("案例四：大中小台分開保存原始口數", fut4["large_net_lots"] == -8000 and fut4["small_net_lots"] == 3000)
    check("案例四：有計算契約金額(未被口數簡單相加取代)",
          fut4["large_value_ntd"] is not None and fut4["large_value_ntd"] != (-8000 + 3000 + 1500))
    check("案例四：有合計等值口數欄位", fut4["large_equivalent_lots"] is not None)

    # ── 案例五：期貨結算日前後訊號降權 ──
    settle_date, dist = me.is_near_futures_settlement()
    check("案例五：可計算下一個結算日", settle_date is not None)
    weight_near, is_near = me.get_futures_signal_weight(0)
    check("案例五：結算日當天權重降至60%", weight_near == 0.6 and is_near is True)
    weight_far, is_far = me.get_futures_signal_weight(10)
    check("案例五：非結算日附近權重維持100%", weight_far == 1.0 and is_far is False)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
