"""
test_attack_engine.py — attack_engine.py 單元測試
執行方式：py test_attack_engine.py   （或 python3 test_attack_engine.py）
測試使用暫存目錄，不會動到 data/ 底下的正式資料。
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import attack_engine as ae

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
    ae.DATA_DIR = tmpdir
    ae.EVIDENCE_REGISTRY_PATH = os.path.join(tmpdir, "evidence_registry.json")
    ae.ATTACK_SCORES_PATH = os.path.join(tmpdir, "attack_scores.json")
    ae.ATTACK_HISTORY_PATH = os.path.join(tmpdir, "attack_score_history.json")
    ae.COUNTEREVIDENCE_PATH = os.path.join(tmpdir, "argument_counterevidence.json")
    ae.MANUAL_REVIEWS_PATH = os.path.join(tmpdir, "manual_reviews.json")

    # 1. 證據登記與讀取
    ae.register_evidence("2330", "ev1", category="fundamental",
                          value={"score_ratio": 0.8}, source="法說會",
                          date="2026-07-01", grade="A", ttl_days=90)
    valid = ae.get_valid_evidence("2330", category="fundamental")
    check("register_evidence + get_valid_evidence", len(valid) == 1 and valid[0]["grade"] == "A")

    # 2. 過期證據自動排除
    ae.register_evidence("2330", "ev_old", category="price",
                          value={"score_ratio": 0.9}, source="舊資料",
                          date="2020-01-01", grade="C", ttl_days=1)
    valid_price = ae.get_valid_evidence("2330", category="price")
    check("過期證據被排除在計分之外", len(valid_price) == 0)

    # 3. 資料不足時應誠實回報（無證據 subject）
    empty_result = ae.calculate_attack_score("9999")
    check("無證據時 data_sufficient=False 且分數為0",
          empty_result["data_sufficient"] is False and empty_result["total_score"] == 0)

    # 4. 硬性否決
    ae.register_evidence("2330", "veto1", category="fundamental",
                          value={"veto": True, "veto_reason": "營收年減超過門檻"},
                          source="財報", date="2026-07-01", grade="A", ttl_days=90)
    evs = ae.get_valid_evidence("2330")
    triggered, reasons = ae.evaluate_hard_veto(evs)
    check("硬性否決被正確觸發", triggered and len(reasons) == 1)

    # 5. 否決後分數上限 49，且不可進入第一擊以上階段
    result = ae.calculate_attack_score("2330")
    check("否決後分數 <= 49", result["total_score"] <= ae.HARD_VETO_CAP)
    check("否決後不進入確認進攻/趨勢攻擊", result["stage"] not in ("確認進攻", "趨勢攻擊"))

    # 6. 滿分情境：無否決、四大分類齊全 A 級證據 → 100分、趨勢攻擊
    for cat in ("fundamental", "valuation", "price", "chips"):
        ae.register_evidence("2317", f"{cat}_1", category=cat,
                              value={"score_ratio": 1.0}, source="系統計算",
                              date="2026-07-01", grade="A", ttl_days=90)
    r2 = ae.calculate_attack_score("2317")
    check("四大分類滿分 -> 總分100", r2["total_score"] == 100.0)
    check("總分100 -> 趨勢攻擊階段", r2["stage"] == "趨勢攻擊")

    # 7. 分數保存 + 歷史紀錄
    ae.save_attack_score("2317", r2)
    hist = ae.load_score_history()
    check("歷史分數已保存", "2317" in hist and len(hist["2317"]) == 1)

    # 8. 證據衝突偵測
    ae.register_evidence("2454", "f_up", category="fundamental",
                          value={"direction": "up"}, source="財報",
                          date="2026-07-01", grade="A", ttl_days=90)
    ae.register_evidence("2454", "p_down", category="price",
                          value={"direction": "down"}, source="技術面",
                          date="2026-07-01", grade="B", ttl_days=90)
    conflict, msg = ae.detect_evidence_conflict("2454")
    check("證據衝突（基本面多、價格弱）被偵測到", conflict and "價格轉弱" in msg)

    # 9. 人工覆核紀錄
    ae.add_manual_review(subject_key="2330", field="fundamental_score",
                          before=30, after=25, reason="人工下修，財報數字待覆核")
    reviews = ae.load_manual_reviews()
    check("人工覆核紀錄已寫入", len(reviews) == 1)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
