"""STEP 2 - Transform: อ่าน raw_data -> ทำความสะอาด/สร้างฟีเจอร์/สรุป -> เขียนลง schema production"""
import numpy as np
import pandas as pd
from sqlalchemy import text

import config

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
# ฤดูกาลตามภูมิอากาศของโปรตุเกส (ซีกโลกเหนือ)
SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
          6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}


# ---------------------------------------------------------------- dimensions
def build_dim_month() -> pd.DataFrame:
    return pd.DataFrame({
        "arrival_month_num": range(1, 13),
        "arrival_date_month": MONTHS,
        "season": [SEASON[m] for m in range(1, 13)],
    })


def build_dim_country(codes) -> pd.DataFrame:
    """แปลงรหัสประเทศ ISO-3 -> ชื่อประเทศ (Joining กับตาราง dimension)"""
    try:
        import pycountry
        def name(c):
            hit = pycountry.countries.get(alpha_3=c)
            return hit.name if hit else c
    except ImportError:
        def name(c):
            return c
    manual = {"CN": "China", "TMP": "East Timor", "Unknown": "Unknown"}
    rows = [{"country": c, "country_name": manual.get(c) or name(c)} for c in sorted(set(codes))]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ cleaning
def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1) Missing values
    df["children"] = df["children"].fillna(0).astype(int)
    df["country"] = df["country"].fillna("Unknown")
    df["has_agent"] = df["agent"].notna().astype(int)
    df["has_company"] = df["company"].notna().astype(int)
    df = df.drop(columns=["agent", "company"])  # คอลัมน์ ID ไม่ได้ใช้วิเคราะห์ (company ว่าง ~94%)

    # 2) ค่า Undefined -> Unknown
    for col in ["meal", "market_segment", "distribution_channel"]:
        df[col] = df[col].replace("Undefined", "Unknown")

    # 3) Data types
    df["reservation_status_date"] = pd.to_datetime(df["reservation_status_date"], format="%d/%m/%Y")
    df["arrival_month_num"] = df["arrival_date_month"].map({m: i + 1 for i, m in enumerate(MONTHS)})
    df["arrival_date"] = pd.to_datetime(dict(
        year=df["arrival_date_year"], month=df["arrival_month_num"], day=df["arrival_date_day_of_month"]))

    # 4) ตัดแถวที่ไม่สมเหตุสมผล
    df["total_guests"] = df["adults"] + df["children"] + df["babies"]
    df["total_nights"] = df["stays_in_weekend_nights"] + df["stays_in_week_nights"]
    before = len(df)
    df = df[df["total_guests"] > 0]                 # ไม่มีผู้เข้าพักเลย
    df = df[(df["adr"] >= 0) & (df["adr"] < 1000)]  # ADR ติดลบ / outlier (เช่น 5,400)
    print(f"[transform] ตัดแถวผิดปกติ {before - len(df):,} แถว (เหลือ {len(df):,})")
    return df.reset_index(drop=True)


# ------------------------------------------------------------ feature engineering
def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["booking_date"] = df["arrival_date"] - pd.to_timedelta(df["lead_time"], unit="D")
    df["year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    df["arrival_weekday"] = df["arrival_date"].dt.day_name()
    df["season"] = df["arrival_month_num"].map(SEASON)
    df["stay_revenue"] = (df["adr"] * df["total_nights"]).round(2)          # รายได้ตามที่จอง
    df["realized_revenue"] = np.where(df["is_canceled"] == 0, df["stay_revenue"], 0.0)  # รายได้จริง
    df["lead_time_bucket"] = pd.cut(
        df["lead_time"], bins=[-1, 7, 30, 90, 180, 365, 10_000],
        labels=["0-7 days", "8-30 days", "31-90 days", "91-180 days", "181-365 days", "365+ days"]).astype(str)
    df["has_kids"] = ((df["children"] + df["babies"]) > 0).astype(int)
    df["room_changed"] = (df["reserved_room_type"] != df["assigned_room_type"]).astype(int)
    df["is_weekend_stay"] = (df["stays_in_weekend_nights"] > 0).astype(int)
    df["cancel_status"] = df["is_canceled"].map({0: "Not Canceled", 1: "Canceled"})
    df["guest_type"] = df["is_repeated_guest"].map({0: "New Guest", 1: "Repeated Guest"})
    return df


# --------------------------------------------------------------- aggregations
def _agg(df, by):
    g = df.groupby(by, observed=True)
    out = g.agg(
        bookings=("is_canceled", "size"),
        canceled=("is_canceled", "sum"),
        avg_adr=("adr", "mean"),
        avg_lead_time=("lead_time", "mean"),
        avg_nights=("total_nights", "mean"),
        total_revenue=("realized_revenue", "sum"),
    ).reset_index()
    out["cancel_rate_pct"] = (out["canceled"] / out["bookings"] * 100).round(2)
    for c in ["avg_adr", "avg_lead_time", "avg_nights", "total_revenue"]:
        out[c] = out[c].round(2)
    return out


def build_tables(raw: pd.DataFrame) -> dict:
    df = add_features(clean(raw))
    dim_month = build_dim_month()
    dim_country = build_dim_country(df["country"].unique())

    # Joining: เติมข้อมูลมิติเข้ากับ fact
    fact = df.merge(dim_country, on="country", how="left")

    monthly = _agg(fact, ["hotel", "arrival_date_year", "arrival_month_num", "arrival_date_month", "year_month"])
    monthly = monthly.merge(dim_month[["arrival_month_num", "season"]], on="arrival_month_num")
    monthly = monthly.sort_values(["year_month", "hotel"]).reset_index(drop=True)

    tables = {
        "fact_bookings": fact[[
            "hotel", "cancel_status", "is_canceled", "lead_time", "lead_time_bucket", "arrival_date",
            "arrival_date_year", "arrival_month_num", "arrival_date_month", "season", "year_month",
            "arrival_weekday", "total_nights", "total_guests", "has_kids", "meal", "country", "country_name",
            "market_segment", "distribution_channel", "guest_type", "customer_type", "deposit_type",
            "reserved_room_type", "assigned_room_type", "room_changed", "booking_changes",
            "total_of_special_requests", "required_car_parking_spaces", "adr", "stay_revenue",
            "realized_revenue"]],
        "agg_monthly": monthly,
        "agg_market_segment": _agg(fact, ["hotel", "market_segment"]),
        "agg_country": _agg(fact, ["country", "country_name"]).sort_values("bookings", ascending=False),
        "agg_lead_time": _agg(fact, ["hotel", "lead_time_bucket", "deposit_type"]),
        "agg_guest_type": _agg(fact, ["hotel", "guest_type", "customer_type"]),
    }
    return tables


# ----------------------------------------------------------------------- main
def transform():
    engine = config.get_engine()
    raw = pd.read_sql_table(config.RAW_TABLE, engine, schema=config.RAW_SCHEMA)
    print(f"[transform] อ่าน {config.RAW_SCHEMA}.{config.RAW_TABLE}: {len(raw):,} แถว")
    tables = build_tables(raw)

    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {config.PROD_SCHEMA}"))
    for name, tbl in tables.items():
        tbl.to_sql(name, engine, schema=config.PROD_SCHEMA, if_exists="replace",
                   index=False, chunksize=10_000, method="multi")
        print(f"[transform] เขียน {config.PROD_SCHEMA}.{name}: {len(tbl):,} แถว x {tbl.shape[1]} คอลัมน์")
    return {k: len(v) for k, v in tables.items()}


if __name__ == "__main__":
    transform()
