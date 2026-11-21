"""STEP 3 - Publish: อ่านตารางจาก schema production แล้วเขียนขึ้น Google Sheets"""
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe

import config

# เลือกตารางที่ส่งขึ้น Sheets (fact ใหญ่สุด ~ 118k x 32 = 3.8M เซลล์ ซึ่งยังต่ำกว่าเพดาน 10M เซลล์)
TABLES = ["agg_monthly", "agg_market_segment", "agg_country", "agg_lead_time",
          "agg_guest_type", "fact_bookings"]
MAX_CELLS = 10_000_000


def _open_sheet(gc):
    if config.GOOGLE_SHEET_ID:
        return gc.open_by_key(config.GOOGLE_SHEET_ID)
    try:
        return gc.open(config.GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        raise SystemExit(
            f"ไม่พบ Google Sheet ชื่อ '{config.GOOGLE_SHEET_NAME}' "
            "- สร้างไฟล์เปล่าใน Google Drive แล้วแชร์ให้ service account (สิทธิ์ Editor)")


def publish(tables=None):
    tables = tables or TABLES
    engine = config.get_engine()
    gc = gspread.service_account(filename=config.GOOGLE_CREDENTIALS_FILE)
    sh = _open_sheet(gc)

    total_cells = 0
    frames = {}
    for t in tables:
        df = pd.read_sql_table(t, engine, schema=config.PROD_SCHEMA)
        # Google Sheets ไม่รับ datetime/NaN โดยตรง -> แปลงเป็นข้อความ/ค่าว่าง
        for c in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        df = df.where(df.notna(), "")
        frames[t] = df
        total_cells += df.shape[0] * df.shape[1]
    if total_cells > MAX_CELLS:
        raise SystemExit(f"ข้อมูลรวม {total_cells:,} เซลล์ เกินเพดาน Google Sheets ({MAX_CELLS:,})")
    print(f"[publish] รวมทั้งหมด {total_cells:,} เซลล์")

    existing = {ws.title: ws for ws in sh.worksheets()}
    for t, df in frames.items():
        ws = existing.get(t) or sh.add_worksheet(title=t, rows=1, cols=1)
        ws.clear()
        ws.resize(rows=len(df) + 1, cols=df.shape[1])
        set_with_dataframe(ws, df, include_index=False, resize=False)
        print(f"[publish] อัปโหลด {t}: {len(df):,} แถว")
    print(f"[publish] เสร็จสิ้น: {sh.url}")


if __name__ == "__main__":
    publish()
