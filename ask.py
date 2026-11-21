"""
Hotel Booking AI Chatbot
Text-to-SQL using Ollama + Qwen 2.5 3B

Run: python ask.py
Type exit to quit, reset to clear history.
"""

import re
import sys

import ollama
from sqlalchemy import text

import config


MODEL = "qwen2.5:3b"
MAX_ROWS = 50
MAX_REPAIR_ATTEMPTS = 2
STATEMENT_TIMEOUT = "15s"

DESCRIPTIONS = {
    "fact_bookings": "ข้อมูลการจองรายรายการ ใช้วิเคราะห์รายละเอียด เช่น วันที่ ประเทศ จำนวนคืน ลูกค้า และรายได้",
    "agg_monthly": "สรุปตามโรงแรมและเดือน มี hotel, bookings, canceled, cancel_rate_pct, avg_adr, total_revenue",
    "agg_market_segment": "สรุปตามโรงแรมและ market_segment",
    "agg_country": "สรุปตามประเทศ มี country/country_name แต่ไม่มีคอลัมน์ hotel",
    "agg_lead_time": "สรุปตามโรงแรม ช่วง lead time และ deposit_type",
    "agg_guest_type": "สรุปตามโรงแรม guest_type และ customer_type",
}

# คำสั่ง/ฟังก์ชันที่ไม่อนุญาต แม้จะอยู่ใน SELECT
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|do|execute|vacuum|set|reset|refresh|lock|prepare|"
    r"deallocate|notify|listen|unlisten|pg_read_file|pg_read_binary_file|"
    r"pg_ls_dir|lo_import|lo_export)\b",
    re.IGNORECASE,
)


def load_schema(engine):
    """อ่านชื่อตารางและคอลัมน์จริงจาก production schema"""
    query = text("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = :schema
        ORDER BY table_name, ordinal_position
    """)

    tables = {}
    with engine.connect() as conn:
        rows = conn.execute(query, {"schema": config.PROD_SCHEMA})
        for table, column, data_type in rows:
            tables.setdefault(table, []).append((column, data_type))

    if not tables:
        raise RuntimeError(
            f"ไม่พบตารางใน schema '{config.PROD_SCHEMA}' "
            "กรุณารัน python run_pipeline.py --skip-publish ก่อน"
        )

    lines = []
    for table, columns in tables.items():
        description = DESCRIPTIONS.get(table, "")
        column_text = ", ".join(
            f"{name} ({dtype})" for name, dtype in columns
        )
        lines.append(
            f"- {config.PROD_SCHEMA}.{table}: {description}\n"
            f"  Columns: {column_text}"
        )

    return tables, "\n".join(lines)


def clean_sql(raw):
    """ดึง SQL ออกจากคำตอบของโมเดล"""
    if not raw:
        return ""

    query = raw.strip()
    query = re.sub(r"```(?:sql)?", "", query, flags=re.IGNORECASE)
    query = query.replace("```", "").strip()

    # ตัดข้อความก่อน SELECT/WITH ที่โมเดลอาจใส่มา
    match = re.search(r"\b(SELECT|WITH)\b", query, flags=re.IGNORECASE)
    if not match:
        return ""

    query = query[match.start():].strip()

    # ลบ SQL comments
    query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    query = re.sub(r"--[^\n]*", " ", query)

    # อนุญาต semicolon เฉพาะท้ายคำสั่ง
    query = query.strip()
    if query.endswith(";"):
        query = query[:-1].strip()

    return query


def validate_sql(query, tables):
    """ตรวจสอบรูปแบบ SQL และชื่อตารางก่อนส่งไปฐานข้อมูล"""
    if not query:
        return "ERROR: ไม่พบ SQL กรุณาสร้างคำสั่ง SELECT ใหม่"

    if ";" in query:
        return "ERROR: อนุญาต SQL ได้ครั้งละ 1 คำสั่งเท่านั้น"

    if not re.match(r"^(SELECT|WITH)\b", query, flags=re.IGNORECASE):
        return "ERROR: อนุญาตเฉพาะ SELECT หรือ WITH เท่านั้น"

    if FORBIDDEN.search(query):
        return "ERROR: พบคำสั่งหรือฟังก์ชันที่ไม่ได้รับอนุญาต"

    schema = config.PROD_SCHEMA
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        return "ERROR: ชื่อ schema ใน config.py ไม่ถูกต้อง"

    # ตรวจชื่อ relation หลัง FROM/JOIN ว่าอยู่ใน production และมีจริง
    relation_pattern = re.compile(
        r'\b(?:FROM|JOIN)\s+'
        r'(?:(?:"?([A-Za-z_][A-Za-z0-9_]*)"?)[.])?'
        r'"?([A-Za-z_][A-Za-z0-9_]*)"?',
        flags=re.IGNORECASE,
    )

    for match in relation_pattern.finditer(query):
        found_schema, table = match.groups()

        if found_schema and found_schema.lower() != schema.lower():
            return (
                f"ERROR: อนุญาตให้อ่านเฉพาะ schema {schema} "
                f"แต่พบ schema {found_schema}"
            )

        if table.lower() not in {name.lower() for name in tables}:
            return (
                f"ERROR: ไม่พบตาราง '{table}' ใน schema {schema} "
                "กรุณาเลือกตารางจากรายการ schema ที่ให้ไว้"
            )

    return None


def run_sql(engine, query, tables):
    query = clean_sql(query)
    validation_error = validate_sql(query, tables)
    if validation_error:
        return validation_error

    schema = config.PROD_SCHEMA

    try:
        with engine.connect() as conn:
            with conn.begin():
                # บังคับ transaction เป็น read-only
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(
                    text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                )
                conn.execute(
                    text(f'SET LOCAL search_path TO "{schema}"')
                )

                result = conn.execute(text(query))
                columns = list(result.keys())
                rows = result.fetchmany(MAX_ROWS + 1)

        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]

        if not rows:
            return "(ไม่มีผลลัพธ์)"

        output = [" | ".join(str(col) for col in columns)]
        for row in rows:
            output.append(
                " | ".join(
                    "" if value is None else str(value)
                    for value in row
                )
            )

        if truncated:
            output.append(f"... แสดงเพียง {MAX_ROWS} แถวแรก")

        return "\n".join(output)

    except Exception as exc:
        message = str(exc).splitlines()[0]
        return f"ERROR: {message}"


def build_system(schema_text):
    schema = config.PROD_SCHEMA

    return f"""
คุณเป็นผู้ช่วยวิเคราะห์ข้อมูล Hotel Booking Demand และต้องสร้าง SQL สำหรับ PostgreSQL เท่านั้น

ฐานข้อมูลมีข้อมูล City Hotel และ Resort Hotel ในประเทศโปรตุเกส ช่วงปี 2015-2017
แต่ห้ามเพิ่มเงื่อนไขประเทศ ปี เดือน หรือค่าใด ๆ ถ้าผู้ใช้ไม่ได้ถาม

โครงสร้างตารางจริง:
{schema_text}

กฎสำคัญ:
1. ตอบคำถามด้วย SQL SELECT หรือ WITH เพียง 1 คำสั่ง ห้ามอธิบาย ห้ามใส่ Markdown
2. ใช้เฉพาะตารางและคอลัมน์ที่แสดงใน schema ด้านบนเท่านั้น
3. ต้องเลือกตารางจากความหมายของคำถาม ห้ามเดาชื่อตารางหรือคอลัมน์
4. ตารางที่ใช้ได้ต้องอยู่ใน schema {schema}
5. ถ้าคำถามถามเปรียบเทียบโรงแรม ให้เลือกตารางที่มีคอลัมน์ hotel
6. agg_country ไม่มีคอลัมน์ hotel จึงห้ามใช้ตอบคำถามที่ต้องจัดอันดับโรงแรม
7. ใช้ agg_monthly สำหรับภาพรวมตามโรงแรม/เดือน, agg_market_segment สำหรับช่องทางตลาด,
   agg_country สำหรับประเทศ, agg_guest_type สำหรับประเภทลูกค้า,
   agg_lead_time สำหรับช่วงเวลาจอง และ fact_bookings สำหรับข้อมูลระดับรายการ
8. ห้ามสมมติค่าเงื่อนไข เช่น Portugal, ปี หรือเดือน หากไม่มีในคำถาม

กฎการคำนวณ:
- อัตรายกเลิกโดยรวมจากตาราง agg_* ต้องคำนวณแบบถ่วงน้ำหนัก:
  SUM(canceled) * 100.0 / NULLIF(SUM(bookings), 0)
  ห้ามใช้ MAX(cancel_rate_pct) หรือ AVG(cancel_rate_pct) เพื่อหาอัตรารวม
- อัตรายกเลิกจาก fact_bookings ใช้ AVG(is_canceled) * 100.0
- ถ้าถามอัตรายกเลิกสูงสุด "รายโรงแรม" ให้ใช้ agg_monthly และ GROUP BY hotel
- รายได้จากตาราง agg_* ใช้ SUM(total_revenue)
- รายได้จาก fact_bookings ใช้ SUM(realized_revenue)
- ADR จากตาราง agg_* ใช้ avg_adr; จาก fact_bookings ใช้ AVG(adr)
- เมื่อเปรียบเทียบรายปี ให้แจ้งข้อจำกัดในคำตอบสรุป: ปี 2015 มีข้อมูลกรกฎาคม-ธันวาคม
  และปี 2017 มีข้อมูลถึงเดือนสิงหาคมเท่านั้น

ตัวอย่าง:
คำถาม: โรงแรมไหนมีอัตราการยกเลิกสูงที่สุด
SQL:
SELECT hotel,
       SUM(bookings) AS total_bookings,
       SUM(canceled) AS canceled_bookings,
       SUM(canceled) * 100.0 / NULLIF(SUM(bookings), 0) AS cancel_rate_pct
FROM {schema}.agg_monthly
GROUP BY hotel
ORDER BY cancel_rate_pct DESC
LIMIT 1

ถ้าคำถามไม่เกี่ยวกับข้อมูลการจองโรงแรม ให้ตอบเพียง OUT_OF_SCOPE
ห้ามสร้างตัวเลขเอง และห้ามตอบจากความรู้ทั่วไปแทนผล SQL
"""


def ask_ollama(messages):
    response = ollama.chat(
        model=MODEL,
        messages=messages,
        options={"temperature": 0},
    )
    return response["message"]["content"].strip()


def summarize_result(question, result):
    messages = [
        {
            "role": "system",
            "content": (
                "คุณเป็นนักวิเคราะห์ข้อมูลโรงแรม ตอบภาษาไทย กระชับ "
                "ใช้เฉพาะข้อมูลในผล SQL ห้ามแต่งตัวเลขหรือสรุปเกินข้อมูล "
                "ถ้าผลเป็น (ไม่มีผลลัพธ์) ให้แจ้งว่าไม่พบข้อมูล"
            ),
        },
        {
            "role": "user",
            "content": f"คำถาม: {question}\n\nผล SQL:\n{result}\n\nสรุปคำตอบให้ผู้ใช้",
        },
    ]
    return ask_ollama(messages)


def answer(engine, tables, system, history, question, show_sql=True):
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-6:])
    messages.append({
        "role": "user",
        "content": (
            f"คำถามปัจจุบัน: {question}\n"
            "สร้าง SQL เพื่อตอบคำถามนี้เท่านั้น "
            "ห้ามเพิ่มตัวกรองที่ผู้ใช้ไม่ได้ระบุ"
        ),
    })

    seen_sql = set()
    result = None

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        raw = ask_ollama(messages)

        if "OUT_OF_SCOPE" in raw.upper():
            response = (
                "ขออภัย ฉันตอบได้เฉพาะคำถามเกี่ยวกับข้อมูลการจองโรงแรม "
                "ในฐานข้อมูลนี้เท่านั้น"
            )
            history.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": response},
            ])
            return response

        sql = clean_sql(raw)

        if sql and sql in seen_sql:
            error = "ERROR: SQL นี้ถูกสร้างซ้ำแล้ว กรุณาเปลี่ยนวิธีเขียน SQL โดยใช้ schema จริง"
        else:
            if sql:
                seen_sql.add(sql)
                if show_sql:
                    print(f"\n[SQL] {sql}")

            result = run_sql(engine, sql, tables)

            if not result.startswith("ERROR:"):
                break

            error = result

        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                f"{error}\n"
                "ให้ตรวจสอบ schema ที่ให้ไว้ แล้วแก้ SQL ใหม่ "
                "ส่งเฉพาะ SQL 1 คำสั่ง ห้ามใช้ตาราง/คอลัมน์ที่ไม่มีจริง "
                "และห้ามเพิ่มเงื่อนไขที่ไม่ได้ถาม"
            ),
        })
    else:
        return (
            "ขออภัย ยังสร้าง SQL ที่ถูกต้องไม่ได้ "
            "กรุณาลองถามใหม่หรือถามให้เจาะจงขึ้น"
        )

    final_answer = summarize_result(question, result)

    history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": final_answer},
    ])

    return final_answer


def main():
    print("=" * 45)
    print("       HOTEL BOOKING AI ASSISTANT")
    print("=" * 45)

    try:
        engine = config.get_engine()

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        print("[OK] PostgreSQL connected")

        tables, schema_text = load_schema(engine)
        print("[OK] Production schema loaded")

        # ตรวจสอบว่าโมเดลมีอยู่ใน Ollama
        ollama.show(MODEL)
        print(f"[OK] Ollama model: {MODEL}")

    except Exception as exc:
        raise SystemExit(f"\nไม่สามารถเริ่มระบบได้: {exc}")

    system = build_system(schema_text)
    history = []
    show_sql = "--hide-sql" not in sys.argv

    print("\nถามข้อมูลโรงแรมได้เลย")
    print("พิมพ์ exit เพื่อออก | reset เพื่อเริ่มใหม่")
    print("ตัวอย่าง: โรงแรมไหนมีอัตราการยกเลิกสูงที่สุด?\n")

    while True:
        try:
            question = input("คุณ> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nออกจากระบบ")
            break

        if not question:
            continue

        if question.lower() in {"exit", "quit", "q", "ออก"}:
            print("Goodbye!")
            break

        if question.lower() == "reset":
            history.clear()
            print("เริ่มบทสนทนาใหม่แล้ว\n")
            continue

        try:
            print("\nAI> ", end="", flush=True)
            response = answer(
                engine, tables, system, history, question, show_sql
            )
            print(response)
            print()
        except Exception as exc:
            print(f"\nเกิดข้อผิดพลาด: {exc}\n")


if __name__ == "__main__":
    main()
