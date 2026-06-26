"""
calc_rex_scores.py
Rex Priority Score 預計算腳本
每日排程跑一次（建議加入 daily_scan.yml），結果存成 data/rex_scores.json
App 直接讀 JSON 顯示，不在前端即時計算，大幅降低載入時間。
"""
import json, os, gc
import pandas as pd
from datetime import datetime

DATA_DIR   = "data"
OUTPUT     = os.path.join(DATA_DIR, "rex_scores.json")
WATCHLIST  = os.path.join(DATA_DIR, "watchlist.json")

# ── 載入工具函式
def load_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return pd.DataFrame(), False
    try:
        df = pd.read_csv(path, low_memory=False)
        return df, True
    except Exception:
        return pd.DataFrame(), False

def load_price_csv(stock_id):
    path = os.path.join(DATA_DIR, "prices", f"{stock_id}.csv")
    if not os.path.exists(path):
        return pd.DataFrame(), False
    try:
        df = pd.read_csv(path)
        if "Close" not in df.columns:
            df.columns = [c.strip() for c in df.columns]
        return df, True
    except Exception:
        return pd.DataFrame(), False

def get_financials(stock_id):
    df, ok = load_csv("financial_data.csv")
    if not ok or df.empty:
        return pd.DataFrame(), False
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    df = df[df["stock_id"] == str(stock_id).strip()]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.sort_values("date") if "date" in df.columns else df, True

def get_chips_facts_map():
    """讀取 chips_data.csv，回傳 {stock_id: {foreign_net, margin_chg_pct}} """
    result = {}
    try:
        df_c, ok_c = load_csv("chips_data.csv")
        if not ok_c or df_c.empty:
            return result

        # stock_id 欄位
        id_col   = next((c for c in df_c.columns if c.lower() in ["stock_id","stockid"]), None)
        name_col = next((c for c in df_c.columns if c.lower() in ["name","institutional_investors"]), None)
        if not id_col:
            return result

        df_c[id_col] = df_c[id_col].astype(str).str.strip()
        if "date" in df_c.columns:
            df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
            latest_date  = df_c["date"].max()
            df_c = df_c[df_c["date"] == latest_date]

        for sid in df_c[id_col].unique():
            df_s = df_c[df_c[id_col] == sid]
            entry = {}

            # 外資買賣超
            if name_col:
                df_fgn = df_s[df_s[name_col].astype(str).str.contains("Foreign_Investor", na=False)]
                buy_col  = next((c for c in df_fgn.columns if "buy" in c.lower() and "shares" in c.lower()), None)
                sell_col = next((c for c in df_fgn.columns if "sell" in c.lower() and "shares" in c.lower()), None)
                if not df_fgn.empty and buy_col and sell_col:
                    try:
                        b = float(df_fgn[buy_col].iloc[0])
                        s = float(df_fgn[sell_col].iloc[0])
                        entry["foreign_net"] = (b - s) / 1000  # 張
                    except Exception:
                        pass

            result[sid] = entry
    except Exception:
        pass

    # 補充融資增減
    try:
        df_m, ok_m = load_csv("margin.csv")
        if ok_m and not df_m.empty:
            df_m["stock_id"] = df_m["stock_id"].astype(str).str.strip()
            if "date" in df_m.columns:
                df_m["date"] = pd.to_datetime(df_m["date"], errors="coerce")
                dates = sorted(df_m["date"].dropna().unique())
                if len(dates) >= 2:
                    today_m = df_m[df_m["date"] == dates[-1]]
                    yest_m  = df_m[df_m["date"] == dates[-2]]
                    bal_col = next((c for c in df_m.columns if "TodayBalance" in c and "Short" not in c), None)
                    if bal_col:
                        for sid in today_m["stock_id"].unique():
                            t = today_m[today_m["stock_id"] == sid][bal_col]
                            y = yest_m[yest_m["stock_id"] == sid][bal_col]
                            if not t.empty and not y.empty:
                                tv = pd.to_numeric(t.iloc[0], errors="coerce")
                                yv = pd.to_numeric(y.iloc[0], errors="coerce")
                                if pd.notna(tv) and pd.notna(yv) and yv != 0:
                                    pct = (tv - yv) / abs(yv) * 100
                                    if sid not in result:
                                        result[sid] = {}
                                    result[sid]["margin_chg_pct"] = round(pct, 2)
    except Exception:
        pass

    return result


def calc_king_score(stock_id):
    result = {
        "total": 0,
        "revenue_yoy_score": 0, "revenue_yoy_val": None,
        "eps_yoy_score": 0,     "eps_yoy_val": None,
        "gm_score": 0,          "gm_trend": "—",
        "sector_score": 2,      "sector_tag": "未標記",
        "holder_score": 0,      "holder_trend": "—",
    }
    try:
        sid = str(stock_id).strip()
        df_fin, ok_fin = get_financials(sid)

        if ok_fin and not df_fin.empty and "type" in df_fin.columns:
            def get_series(df, kw):
                sub = df[df["type"].astype(str).str.contains(kw, case=False, na=False)].copy()
                if "date" in sub.columns:
                    sub = sub.sort_values("date")
                return sub["value"].dropna().astype(float).tolist() if "value" in sub.columns else []

            rev = get_series(df_fin, "Revenue")
            if len(rev) >= 2 and rev[-2] != 0:
                yoy = (rev[-1] - rev[-2]) / abs(rev[-2]) * 100
                result["revenue_yoy_val"] = round(yoy, 1)
                result["revenue_yoy_score"] = 10 if yoy>=30 else 7 if yoy>=15 else 4 if yoy>=0 else 0

            eat = get_series(df_fin, "IncomeAfterTaxes")
            if len(eat) >= 2 and eat[-2] != 0:
                yoy = (eat[-1] - eat[-2]) / abs(eat[-2]) * 100
                result["eps_yoy_val"] = round(yoy, 1)
                result["eps_yoy_score"] = 10 if yoy>=30 else 7 if yoy>=15 else 4 if yoy>=0 else 0

            gp  = get_series(df_fin, "GrossProfit")
            rev2 = get_series(df_fin, "Revenue")
            if min(len(gp), len(rev2)) >= 3:
                rates = [gp[i]/rev2[i]*100 if rev2[i] else None for i in [-3,-2,-1]]
                rates = [r for r in rates if r is not None]
                if len(rates) >= 3:
                    g0, g1, g2 = rates
                    if g2>g1>g0:   result["gm_score"], result["gm_trend"] = 10, f"連續提升({g2:.1f}%) ✅"
                    elif g2>g1:    result["gm_score"], result["gm_trend"] = 7,  f"近季提升({g2:.1f}%)"
                    elif abs(g2-g1)<1: result["gm_score"], result["gm_trend"] = 5, f"持平({g2:.1f}%)"
                    elif g2<g1:    result["gm_score"], result["gm_trend"] = 2,  f"單季下滑({g2:.1f}%) ⚠️"
                    else:          result["gm_score"], result["gm_trend"] = 0,  f"連續下滑({g2:.1f}%) ❌"

        # 大戶持股
        try:
            df_sh, ok_sh = load_csv("shareholder_data.csv")
            if ok_sh and not df_sh.empty:
                df_sh["stock_id"] = df_sh["stock_id"].astype(str).str.strip()
                sh_s = df_sh[df_sh["stock_id"] == sid].copy()
                big_lvl = {"400,001-600,000","600,001-800,000","800,001-1,000,000","more than 1,000,001"}
                sh_big = sh_s[sh_s["HoldingSharesLevel"].isin(big_lvl)].copy()
                if not sh_big.empty and "date" in sh_big.columns and "percent" in sh_big.columns:
                    sh_big["percent"] = pd.to_numeric(sh_big["percent"], errors="coerce")
                    sh_big["date"]    = pd.to_datetime(sh_big["date"], errors="coerce")
                    sh_agg = sh_big.groupby("date")["percent"].sum().sort_index()
                    if len(sh_agg) >= 2:
                        diff = float(sh_agg.iloc[-1]) - float(sh_agg.iloc[-2])
                        if diff > 0.5:
                            result["holder_score"], result["holder_trend"] = 5, f"持續上升({sh_agg.iloc[-1]:.1f}%) ✅"
                        elif diff >= 0:
                            result["holder_score"], result["holder_trend"] = 3, f"持平({sh_agg.iloc[-1]:.1f}%)"
                        else:
                            result["holder_score"], result["holder_trend"] = 0, f"下滑({sh_agg.iloc[-1]:.1f}%) ⚠️"
        except Exception:
            pass

    except Exception:
        pass

    result["total"] = (result["revenue_yoy_score"] + result["eps_yoy_score"] +
                       result["gm_score"] + result["sector_score"] + result["holder_score"])
    return result


def calc_attack_score(stock_id, chips_map):
    result = {
        "total": 0,
        "support_score": 0, "support_detail": "—",
        "ma_score": 0,      "ma_detail": "—",
        "mom_score": 0,     "mom_detail": "—",
        "chips_score": 0,   "chips_detail": "—",
        "downgrade_flag": None,
        "bias_20": None,
    }
    try:
        df_p, ok_p = load_price_csv(stock_id)
        if not ok_p or df_p.empty or len(df_p) < 65:
            return result
        closes = pd.to_numeric(df_p["Close"], errors="coerce").dropna()
        if len(closes) < 65:
            return result

        price   = float(closes.iloc[-1])
        sma20   = float(closes.tail(20).mean())
        sma60   = float(closes.tail(60).mean())
        bias_20 = (price - sma20) / sma20 * 100 if sma20 > 0 else 0.0
        result["bias_20"] = round(bias_20, 1)

        sma20_prev = float(closes.iloc[-21:-1].mean()) if len(closes) >= 21 else sma20
        sma60_prev = float(closes.iloc[-61:-1].mean()) if len(closes) >= 61 else sma60
        sma20_up = sma20 > sma20_prev
        sma60_up = sma60 > sma60_prev

        # 支撐位置
        if bias_20 <= -10 and price >= sma60:
            result["support_score"], result["support_detail"] = 10, f"深度回測月線({bias_20:+.1f}%)且守季線 ✅"
        elif bias_20 <= -5 and price >= sma60:
            result["support_score"], result["support_detail"] = 8,  f"回測月線({bias_20:+.1f}%)且季線健在"
        elif bias_20 <= -2 and price >= sma20:
            result["support_score"], result["support_detail"] = 6,  f"輕微回落至月線附近({bias_20:+.1f}%)"
        elif -2 < bias_20 <= 5:
            result["support_score"], result["support_detail"] = 3,  f"月線上方不遠({bias_20:+.1f}%)"
        else:
            result["support_score"], result["support_detail"] = 1,  f"正乖離({bias_20:+.1f}%)，追高風險偏高"
        if price < sma60:
            result["support_score"] = max(0, result["support_score"] - 3)
            result["support_detail"] += "｜跌破季線 ⚠️"

        # MA結構
        if price >= sma20 >= sma60 and sma20_up:
            result["ma_score"], result["ma_detail"] = 10, "月線>季線 且月線上彎 ✅"
        elif price >= sma20 >= sma60 and not sma20_up:
            result["ma_score"], result["ma_detail"] = 7, "月線>季線 但月線走平"
        elif sma20 >= sma60 and price < sma20:
            result["ma_score"], result["ma_detail"] = 5, "月線仍>季線，股價回測月線"
        elif sma20 < sma60 and sma60_up:
            result["ma_score"], result["ma_detail"] = 4, "月線跌破季線 但季線仍向上 ⚠️"
        else:
            result["ma_score"], result["ma_detail"] = 0, "月線跌破季線 且季線下彎 ❌"
            result["downgrade_flag"] = "⛔ 趨勢破壞"

        # MOM動能
        if len(closes) >= 61:
            m20 = (price - float(closes.iloc[-21])) / float(closes.iloc[-21]) * 100
            m60 = (price - float(closes.iloc[-61])) / float(closes.iloc[-61]) * 100
            if m20 > 0 and m60 > 0:
                result["mom_score"], result["mom_detail"] = 10, f"短中期動能皆正(20D:{m20:+.1f}% 60D:{m60:+.1f}%) ✅"
            elif m20 > 0:
                result["mom_score"], result["mom_detail"] = 7,  f"短線反彈 中期仍弱(20D:{m20:+.1f}% 60D:{m60:+.1f}%)"
            elif m60 > 0:
                result["mom_score"], result["mom_detail"] = 5,  f"短線回落 中期仍強(20D:{m20:+.1f}% 60D:{m60:+.1f}%) 洗盤機會"
            else:
                result["mom_score"], result["mom_detail"] = 2,  f"短中期動能皆負(20D:{m20:+.1f}% 60D:{m60:+.1f}%)"

        # 籌碼沉澱
        chip     = chips_map.get(str(stock_id), {})
        fgn      = chip.get("foreign_net")
        mgp      = chip.get("margin_chg_pct")
        fgn_s    = 4 if (fgn and fgn > 500) else 2 if (fgn and fgn > 0) else 0
        mg_s     = 6 if (mgp and mgp <= -2) else 4 if (mgp and mgp <= 0) else 1 if (mgp and mgp <= 2) else 0
        result["chips_score"]  = fgn_s + mg_s
        result["chips_detail"] = f"外資{fgn:+,.0f}張｜融資{mgp:+.2f}%" if (fgn and mgp) else "—"

        if fgn and fgn < -500 and mgp and mgp > 2:
            result["downgrade_flag"] = result.get("downgrade_flag") or "🚨 籌碼惡化"

    except Exception:
        pass

    result["total"] = (result["support_score"] + result["ma_score"] +
                       result["mom_score"] + result["chips_score"])
    return result


def run():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 開始計算 Rex Priority Score...")

    # 讀取儲備庫
    try:
        with open(WATCHLIST, encoding="utf-8") as f:
            wl = json.load(f)
        reserve = wl.get("reserve", [])
    except Exception as e:
        print(f"❌ 無法讀取 watchlist.json：{e}")
        return

    if not reserve:
        print("❌ 儲備庫為空")
        return

    class_map = {r["id"]: r.get("class", "Prince") for r in reserve}
    chips_map = get_chips_facts_map()
    results   = []
    mkt_score = 10  # 預設黃燈10分，市場燈號由前端即時覆蓋

    for item in reserve:
        sid = item["id"]
        try:
            king   = calc_king_score(sid)
            attack = calc_attack_score(sid, chips_map)

            stock_class = class_map.get(sid, "Prince")
            if stock_class == "King":
                wk, wa, wm = 1.00, 0.75, 1.50
            elif stock_class == "Hunter":
                wk, wa, wm = 0.50, 1.25, 1.50
            else:
                wk, wa, wm = 0.875, 0.875, 1.50

            base_total = int(king["total"] * wk + attack["total"] * wa + mkt_score * wm)

            flag = attack.get("downgrade_flag")
            if king.get("revenue_yoy_val") is not None and king.get("eps_yoy_val") is not None:
                if king["revenue_yoy_val"] < 0 and king["eps_yoy_val"] < 0:
                    flag = flag or "⚠️ 基本面衰退"

            results.append({
                "stock_id":    sid,
                "name":        item.get("name", sid),
                "stock_class": stock_class,
                "base_total":  base_total,   # 不含市場環境分的基礎分
                "king_total":  king["total"],
                "attack_total": attack["total"],
                "flag":        flag,
                "revenue_yoy_score": king["revenue_yoy_score"],
                "revenue_yoy_val":   king["revenue_yoy_val"],
                "eps_yoy_score":     king["eps_yoy_score"],
                "eps_yoy_val":       king["eps_yoy_val"],
                "gm_score":          king["gm_score"],
                "gm_trend":          king["gm_trend"],
                "sector_score":      king["sector_score"],
                "holder_score":      king["holder_score"],
                "holder_trend":      king["holder_trend"],
                "support_score":  attack["support_score"],
                "support_detail": attack["support_detail"],
                "ma_score":       attack["ma_score"],
                "ma_detail":      attack["ma_detail"],
                "mom_score":      attack["mom_score"],
                "mom_detail":     attack["mom_detail"],
                "chips_score":    attack["chips_score"],
                "chips_detail":   attack["chips_detail"],
                "bias_20":        attack["bias_20"],
            })
            print(f"  ✅ {sid} {item.get('name','')} ({stock_class}) 基礎分:{base_total}")
        except Exception as e:
            print(f"  ❌ {sid} 計算失敗：{e}")
        finally:
            gc.collect()

    # 依基礎分排序
    results.sort(key=lambda x: (-x["base_total"], x["stock_id"]))

    output = {
        "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(results),
        "scores": results,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！共計算 {len(results)} 檔，結果已存入 {OUTPUT}")


if __name__ == "__main__":
    run()
