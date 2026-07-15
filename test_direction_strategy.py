"""
test_direction_strategy.py — 驗證正2/反1/現金策略六個驗收案例
執行：py test_direction_strategy.py
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import attack_engine as ae
import market_events as me
import direction_strategy as ds

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


def setup_isolated_storage():
    tmpdir = tempfile.mkdtemp()
    ae.DATA_DIR = tmpdir
    ae.EVIDENCE_REGISTRY_PATH = os.path.join(tmpdir, "evidence_registry.json")
    ae.ATTACK_SCORES_PATH = os.path.join(tmpdir, "attack_scores.json")
    ae.ATTACK_HISTORY_PATH = os.path.join(tmpdir, "attack_score_history.json")
    ae.COUNTEREVIDENCE_PATH = os.path.join(tmpdir, "argument_counterevidence.json")
    ae.MANUAL_REVIEWS_PATH = os.path.join(tmpdir, "manual_reviews.json")
    me.DATA_DIR = tmpdir
    me.PIVOT_HISTORY_PATH = os.path.join(tmpdir, "market_pivot_history.json")
    me.PRICE_EVENTS_PATH = os.path.join(tmpdir, "market_price_events.json")
    return tmpdir


def run():
    today = datetime.now().strftime("%Y-%m-%d")

    # ── 案例一：盤中V形反彈、外資淨空增加、布林未擴張 → 正式策略應為現金觀察
    setup_isolated_storage()
    ae.register_evidence("market", "price_bollinger", category="price", value={
        "intraday_reversal_state": "強烈承接", "bollinger_event_state": "盤中刺穿後收回",
        "lower_band_expanding": False, "is_provisional": True, "close_return_pct": 1.2,
    }, source="test", date=today, grade="C", ttl_days=1)
    ae.register_evidence("market", "chips_futures_margin", category="chips", value={
        "posture": "新增淨空", "near_settlement": False,
    }, source="test", date=today, grade="A", ttl_days=1)
    ae.register_evidence("market", "evidence_conflict", category="conflict", value={
        "state": "證據衝突", "conflicts": ["價格出現低檔承接，但外資避險未退。"],
    }, source="test", date=today, grade="C", ttl_days=1)

    result1 = ds.get_direction_strategy_state(ae, me)
    check("案例一：正式策略為現金觀察", result1["strategy_state"] == "現金觀察")
    check("案例一：偵測到證據衝突", result1["evidence_conflict"] is True)

    # ── 案例二：三日不破低、站回下軌、市場廣度改善 → 方向分數轉正，符合門檻後才輸出正2
    setup_isolated_storage()
    ae.register_evidence("market", "price_bollinger", category="price", value={
        "intraday_reversal_state": "盤中完全反轉", "bollinger_event_state": "超賣後回收",
        "lower_band_expanding": True, "is_provisional": False, "close_return_pct": 2.0,
    }, source="test", date=today, grade="C", ttl_days=1)
    ae.register_evidence("market", "chips_futures_margin", category="chips", value={
        "posture": "空單回補或轉多", "near_settlement": False,
    }, source="test", date=today, grade="A", ttl_days=1)
    ae.register_evidence("market", "valuation_pe_scenario", category="valuation", value={
        "score_ratio": 0.9,
    }, source="test", date=today, grade="B", ttl_days=1)

    result2 = ds.get_direction_strategy_state(ae, me)
    check(f"案例二：方向分數轉正 (實際{result2['market_direction_score']})", result2["market_direction_score"] > 0)
    check(f"案例二：符合門檻後正式輸出正2 (分數{result2['market_direction_score']}, 可信度{result2['direction_confidence_score']})",
          result2["strategy_state"] == "正2")

    # ── 案例三：跌破關鍵低點、收低、布林下軌擴張、外資現貨期貨同步偏空 → 反1
    setup_isolated_storage()
    ae.register_evidence("market", "price_bollinger", category="price", value={
        "intraday_reversal_state": "無明顯反轉", "bollinger_event_state": "沿下軌加速下跌",
        "lower_band_expanding": True, "is_provisional": False, "close_return_pct": -3.0,
    }, source="test", date=today, grade="C", ttl_days=1)
    ae.register_evidence("market", "chips_futures_margin", category="chips", value={
        "posture": "新增淨空", "near_settlement": False,
    }, source="test", date=today, grade="A", ttl_days=1)
    ae.register_evidence("market", "valuation_pe_scenario", category="valuation", value={
        "score_ratio": 0.1,
    }, source="test", date=today, grade="B", ttl_days=1)
    me.update_pivot_events("2099-01-10", intraday_low=100, close_price=101, previous_close=105,
                            recovery_metrics={"recovery_ratio": None}, bollinger_state="沿下軌加速下跌",
                            futures_posture="新增淨空")
    me.update_pivot_events(today, intraday_low=95, close_price=96, previous_close=100,
                            recovery_metrics={"recovery_ratio": None}, bollinger_state="沿下軌加速下跌",
                            futures_posture="新增淨空")

    result3 = ds.get_direction_strategy_state(ae, me)
    check(f"案例三：方向分數低於-45 (實際{result3['market_direction_score']})", result3["market_direction_score"] <= -45)
    check(f"案例三：可信度達60以上後才輸出反1 (可信度{result3['direction_confidence_score']}, 狀態{result3['strategy_state']})",
          result3["strategy_state"] in ("反1", "反1模擬觀察"))

    # ── 案例四：方向偏空但接近期貨結算 → 期貨證據降權，可信度不足只能模擬觀察
    setup_isolated_storage()
    ae.register_evidence("market", "price_bollinger", category="price", value={
        "intraday_reversal_state": "無明顯反轉", "bollinger_event_state": "沿下軌加速下跌",
        "lower_band_expanding": True, "is_provisional": True, "close_return_pct": -3.0,
    }, source="test", date=today, grade="C", ttl_days=1)
    ae.register_evidence("market", "chips_futures_margin", category="chips", value={
        "posture": "新增淨空", "near_settlement": True,
    }, source="test", date=today, grade="A", ttl_days=1)

    result4 = ds.get_direction_strategy_state(ae, me)
    check("案例四：接近結算時可信度分數有被扣分",
          any("結算" in k for k in result4["confidence_breakdown"]))
    check(f"案例四：可信度不足只能模擬觀察 (狀態{result4['strategy_state']})",
          result4["strategy_state"] != "反1")

    # ── 案例五：正2投入10萬 vs 反1投入10萬 → 系統警告方向曝險不對稱
    cmp5 = ds.compare_symmetric_exposure(100000, 100000)
    check("案例五：正2曝險是本金的2倍", cmp5["long_exposure"] == 200000)
    check("案例五：反1曝險是本金的-1倍", cmp5["short_exposure"] == -100000)
    check("案例五：本金相同時曝險不對稱", cmp5["is_symmetric_exposure"] is False)
    check("案例五：系統有警告訊息", cmp5["warning"] is not None)

    # ── 案例六：正2與反1同時持有 → 分別顯示多空曝險與總曝險，不只顯示淨額
    positions6 = [
        {"ticker": "00631L", "direction": "long", "market_value": 100000},
        {"ticker": "00632R", "direction": "short", "market_value": 100000},
    ]
    agg6 = ds.aggregate_exposure(positions6)
    check("案例六：多方曝險=200000", agg6["long_notional_exposure"] == 200000)
    check("案例六：空方曝險=-100000", agg6["short_notional_exposure"] == -100000)
    check("案例六：淨曝險=100000(不是0)", agg6["net_exposure"] == 100000)
    check("案例六：總曝險=300000(不是只看淨額)", agg6["gross_exposure"] == 300000)

    # ── 五態合法性檢查
    for r in (result1, result2, result3, result4):
        check(f"策略狀態合法：{r['strategy_state']}", r["strategy_state"] in ds.ALLOWED_STRATEGY_STATES)

    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
