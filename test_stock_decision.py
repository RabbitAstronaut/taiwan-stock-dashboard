"""
test_stock_decision.py — 驗證規格書四個驗收案例
執行：py test_stock_decision.py
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import attack_engine as ae
import stock_decision as sd

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
    sd.DATA_DIR = tmpdir

    today = datetime.now().strftime("%Y-%m-%d")

    # 模擬 rex_scores.json：台光電 研究優先77分、攻擊時機尚未給證據(=48分需靠evidence湊)
    rex_data = {
        "calculated_at": today,
        "scores": [
            {"stock_id": "2383", "name": "台光電", "stock_class": "King",
             "base_total": 77, "king_total": 32, "attack_total": 21},
            {"stock_id": "6199", "name": "勤誠", "stock_class": "Hunter",
             "base_total": 65, "king_total": 20, "attack_total": 30},
            {"stock_id": "9999", "name": "測試股", "stock_class": "Prince",
             "base_total": 80, "king_total": 25, "attack_total": 25},
        ],
    }
    with open(os.path.join(tmpdir, "rex_scores.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(rex_data, f, ensure_ascii=False)

    # ── 案例一：台光電 研究優先77分、攻擊時機48分（攻擊準備），
    #    Tab3應顯示「優先研究、攻擊準備」，Tab4研究佇列裡不得顯示第一擊
    for cat, ratio in [("fundamental", 0.55), ("valuation", 0.5), ("price", 0.4), ("chips", 0.3)]:
        ae.register_evidence("2383", f"{cat}_1", category=cat, value={"score_ratio": ratio},
                              source="test", date=today, grade="A", ttl_days=30)
    d1 = sd.build_stock_decision("2383")
    check("案例一：研究優先分=77", d1["research_priority_score"] == 77)
    check("案例一：研究狀態=優先研究", d1["research_state"] == "優先研究")
    check(f"案例一：攻擊分數在40~54之間(攻擊準備) (實際{d1['attack_score']})", 40 <= d1["attack_score"] <= 54)
    check("案例一：攻擊階段=攻擊準備", d1["attack_stage"] == "攻擊準備")
    check("案例一：行動不是第一擊(只是觀察)", d1["recommended_action"] != "第一擊")

    # ── 案例二：勤誠 距高點跌27%進入重新估值，尚未有價格確認
    #    預期顯示「重新估值、暫不買進」，不得因跌幅大直接成為攻擊候選
    king_event = {"reason": "距高點下跌27%，觸發重新估值"}
    d2 = sd.build_stock_decision("6199", king_discount_event=king_event)
    check("案例二：研究狀態=重新估值", d2["research_state"] == "重新估值")
    check("案例二：沒有價格證據時攻擊分數應偏低", d2["attack_score"] < 55)
    check("案例二：行動不是加碼/第一擊(暫不買進)", d2["recommended_action"] not in ("第一擊", "加碼"))

    # ── 案例三：某股票攻擊時機58分且未觸發否決 → 應進入第一擊，20~30%部位
    for cat, ratio in [("fundamental", 0.6), ("valuation", 0.55), ("price", 0.6), ("chips", 0.5)]:
        ae.register_evidence("9999", f"{cat}_1", category=cat, value={"score_ratio": ratio},
                              source="test", date=today, grade="A", ttl_days=30)
    d3 = sd.build_stock_decision("9999")
    check(f"案例三：攻擊分數落在55~69(第一擊)區間 (實際{d3['attack_score']})", 55 <= d3["attack_score"] <= 69)
    check("案例三：攻擊階段=第一擊", d3["attack_stage"] == "第一擊")
    check("案例三：行動=試單", d3["recommended_action"] == "試單")
    check("案例三：建議部位為20~30%", "20%" in d3["position_limit"])

    # ── 案例四：研究優先80分，但基本面觸發硬性否決 → 仍可優先研究，行動須為停止抄底/退出
    ae.register_evidence("8888", "veto_test", category="fundamental",
                          value={"veto": True, "veto_reason": "營收年減超過門檻"},
                          source="test", date=today, grade="A", ttl_days=30)
    rex_data["scores"].append({"stock_id": "8888", "name": "否決測試股", "stock_class": "Prince",
                                "base_total": 80, "king_total": 30, "attack_total": 20})
    with open(os.path.join(tmpdir, "rex_scores.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(rex_data, f, ensure_ascii=False)
    d4 = sd.build_stock_decision("8888")
    check("案例四：研究狀態仍為優先研究(不因否決被排除研究)", d4["research_state"] == "優先研究")
    check("案例四：硬性否決=True", d4["hard_veto"] is True)
    check("案例四：攻擊階段=停止抄底", d4["attack_stage"] == "停止抄底")
    check("案例四：行動=退出", d4["recommended_action"] == "退出")

    # ── 統一詞彙檢查
    for d in (d1, d2, d3, d4):
        check(f"{d['ticker']} 研究狀態合法", d["research_state"] in sd.RESEARCH_STATES)
        check(f"{d['ticker']} 攻擊階段合法", d["attack_stage"] in sd.ATTACK_STAGES)
        check(f"{d['ticker']} 行動合法", d["recommended_action"] in sd.ACTIONS)
        check(f"{d['ticker']} 證據狀態合法", d["evidence_completeness"] in sd.EVIDENCE_STATES)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{passed} 通過, {failed} 失敗")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
