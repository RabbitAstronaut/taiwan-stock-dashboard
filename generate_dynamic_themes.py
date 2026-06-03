"""
generate_dynamic_themes.py  ── Production 版 (google-genai 新版 SDK)
從 chips_data.csv 動態萃取當日法人買超前15大個股，用 Gemini 2.5 Flash 提煉市場核心題材
執行（PowerShell）：
  $env:GEMINI_API_KEY="你的key"; python generate_dynamic_themes.py
執行（CMD）：
  set GEMINI_API_KEY=你的key && python generate_dynamic_themes.py
"""
import os, json, re, time, sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import types

# ── 路徑設定
DATA_DIR  = Path("data")
CHIPS_CSV = DATA_DIR / "chips_data.csv"
INFO_CSV  = DATA_DIR / "stock_info.csv"
OUT_FILE  = DATA_DIR / "dynamic_themes.json"
os.makedirs(DATA_DIR, exist_ok=True)

# ── API 設定
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_KEY:
    print("❌ 未設定 GEMINI_API_KEY")
    print("   PowerShell: $env:GEMINI_API_KEY=\"你的key\"")
    print("   CMD:        set GEMINI_API_KEY=你的key")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_KEY)

# ══════════════════════════════════════════════
# 步驟一：從 CSV 動態萃取法人買超前15大個股
# ══════════════════════════════════════════════
def get_top15_stocks() -> list[str]:
    for f in [CHIPS_CSV, INFO_CSV]:
        if not f.exists():
            print(f"❌ 找不到 {f}，請先執行 update_data.py")
            if OUT_FILE.exists():
                print("✅ 保留舊有 dynamic_themes.json")
            sys.exit(0)

    df = pd.read_csv(CHIPS_CSV, dtype=str)
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    df["net"]      = pd.to_numeric(df["net"], errors="coerce").fillna(0)
    df["date"]     = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    latest = df["date"].max()
    print(f"📅 最新交易日：{latest.strftime('%Y-%m-%d')}")
    today_df = df[df["date"] == latest].copy()

    name_col = next((c for c in ["name", "institutional_investors"]
                     if c in today_df.columns), None)
    if not name_col:
        print("❌ 找不到法人名稱欄位")
        sys.exit(1)

    inst_mask = today_df[name_col].astype(str).str.contains(
        "Foreign_Investor|Investment_Trust", na=False)
    inst_df = today_df[inst_mask].copy()
    inst_df = inst_df[~inst_df["stock_id"].str.startswith("00")]

    top15 = (inst_df.groupby("stock_id")["net"]
             .sum().sort_values(ascending=False)
             .head(15).index.tolist())
    print(f"📊 法人買超前15：{top15}")

    df_info  = pd.read_csv(INFO_CSV, dtype=str)
    df_info["stock_id"] = df_info["stock_id"].astype(str).str.strip()
    name_col_i = next((c for c in ["stock_name","name"] if c in df_info.columns), None)
    if name_col_i:
        df_info["stock_name"] = df_info[name_col_i]
    else:
        df_info["stock_name"] = df_info["stock_id"]

    name_map = (df_info[df_info["stock_name"] != df_info["stock_id"]]
                .drop_duplicates("stock_id")
                .set_index("stock_id")["stock_name"].to_dict())

    result = [f"{sid} {name_map.get(sid, sid)}" for sid in top15]
    print(f"✅ 完成對應：{result}")
    return result, latest.strftime('%Y-%m-%d')

# ══════════════════════════════════════════════
# 步驟二：Prompt 組裝
# ══════════════════════════════════════════════
def build_prompt(stock_list: list[str], trade_date: str) -> str:
    stocks_str = "、".join(stock_list)
    return (
        f"你是一位台股資深分析師。\n\n"
        f"以下是 {trade_date} 外資與投信淨買超最大的前15大個股：\n"
        f"{stocks_str}\n\n"
        f"請分析這些公司的業務交集，總結出 1 到 2 個驅動它們上漲的"
        f"最新市場核心題材（例如：矽光子、重電、AI伺服器）。\n\n"
        f"強制要求：只輸出純 JSON，不加任何 Markdown 或說明文字，直接從 {{ 開始：\n"
        f'{{\n'
        f'  "themes": ["題材一", "題材二"],\n'
        f'  "reason": "一句話解釋業務交集與市場驅動力",\n'
        f'  "top15": {json.dumps(stock_list, ensure_ascii=False)},\n'
        f'  "trade_date": "{trade_date}",\n'
        f'  "generated_at": "{datetime.now().strftime("%Y-%m-%d %H:%M")}"\n'
        f'}}'
    )

# ══════════════════════════════════════════════
# 步驟三：呼叫 Gemini
# ══════════════════════════════════════════════
def call_gemini(prompt: str) -> dict | None:
    for attempt in range(3):
        try:
            print(f"🤖 呼叫 Gemini 2.5 Flash（第 {attempt+1} 次）...")
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = resp.text.strip()
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text).strip()
            result = json.loads(text)
            print("✅ 解析成功！")
            return result
        except json.JSONDecodeError:
            print(f"⚠️ JSON 解析失敗：{text[:200]}")
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ API 失敗（{attempt+1}）：{e}")
            time.sleep(5 * (attempt + 1))
    return None

# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════
def main():
    print("=" * 55)
    print("🚀 generate_dynamic_themes.py  Production 版")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    stock_list, trade_date = get_top15_stocks()
    prompt = build_prompt(stock_list, trade_date)
    result = call_gemini(prompt)

    if result:
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 結果存至 {OUT_FILE}")
        print(f"   🏷️  題材：{result.get('themes', [])}")
        print(f"   💬 理由：{result.get('reason', '')}")
    else:
        print("\n❌ Gemini 失敗，保留舊有 JSON")
        if not OUT_FILE.exists():
            fallback = {
                "themes": ["AI伺服器", "散熱模組"],
                "reason": "API 失敗，使用預設題材",
                "top15": stock_list,
                "trade_date": trade_date,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "is_fallback": True
            }
            with open(OUT_FILE, "w", encoding="utf-8") as f:
                json.dump(fallback, f, ensure_ascii=False, indent=2)

    print("=" * 55)

if __name__ == "__main__":
    main()
