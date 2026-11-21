"""STEP 1 - Extract & Load: อ่าน CSV จาก Kaggle แล้วโหลดลง PostgreSQL (schema raw_data)"""
import pandas as pd
from sqlalchemy import text

import config


def ingest() -> int:
    print(f"[ingest] อ่านไฟล์ {config.CSV_PATH}")
    df = pd.read_csv(config.CSV_PATH)
    print(f"[ingest] พบ {len(df):,} แถว, {df.shape[1]} คอลัมน์")

    engine = config.get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {config.RAW_SCHEMA}"))

    # if_exists="replace" ทำให้รันซ้ำได้ (idempotent)
    df.to_sql(
        config.RAW_TABLE,
        engine,
        schema=config.RAW_SCHEMA,
        if_exists="replace",
        index=False,
        chunksize=10_000,
        method="multi",
    )
    print(f"[ingest] โหลดลง {config.RAW_SCHEMA}.{config.RAW_TABLE} สำเร็จ")
    return len(df)


if __name__ == "__main__":
    ingest()
