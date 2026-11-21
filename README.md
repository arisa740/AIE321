# Hotel Booking Data Pipeline (Kaggle → PostgreSQL → Google Sheets → Looker Studio)

ไปป์ไลน์ข้อมูลแบบครบวงจรบนเครื่องตัวเอง สำหรับชุดข้อมูล **Hotel Booking Demand** (Kaggle, 119,390 การจอง ปี 2015–2017)

```
CSV → ingest.py → PostgreSQL raw_data → transform.py → PostgreSQL production → publish.py → Google Sheets → Looker Studio
                              └──────────── run_pipeline.py (สั่งรันทั้งหมดในคำสั่งเดียว) ────────────┘
```

## โครงสร้างโปรเจกต์
| ไฟล์ | หน้าที่ |
|---|---|
| `docker-compose.yml` | รัน PostgreSQL 16 ใน Docker |
| `ingest.py` | โหลด CSV ลง `raw_data.hotel_bookings` |
| `transform.py` | Cleaning, Feature Engineering, Joining, Aggregation → schema `production` |
| `publish.py` | ส่งตารางใน `production` ขึ้น Google Sheets ด้วย gspread |
| `run_pipeline.py` | รัน ingest → transform → publish ต่อเนื่องกัน |
| `config.py` | อ่านค่าตั้งต้นจาก `.env` |

## วิธีรัน
1. **ติดตั้ง** Python 3.10+ และ Docker แล้วรัน
   ```bash
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **ดาวน์โหลดข้อมูล** จาก Kaggle (Hotel booking demand) แล้ววางไว้ที่ `data/hotel_bookings.csv`
3. **ตั้งค่า** `cp .env.example .env` (แก้ค่าตามต้องการ)
4. **เปิดฐานข้อมูล** `docker compose up -d`
5. **ตั้งค่า Google API** (ดูด้านล่าง) แล้ววาง `credentials.json` ไว้ที่โฟลเดอร์โปรเจกต์
6. **รันไปป์ไลน์**
   ```bash
   python run_pipeline.py                  # ครบทุกขั้นตอน
   python run_pipeline.py --skip-publish   # เฉพาะ ingest + transform (ไม่ต้องใช้ Google)
   ```

## ตั้งค่า Google API Credentials
1. เข้า [Google Cloud Console](https://console.cloud.google.com) → สร้าง Project ใหม่
2. เมนู **APIs & Services → Library** → เปิดใช้งาน **Google Sheets API** และ **Google Drive API**
3. **APIs & Services → Credentials → Create Credentials → Service Account** ตั้งชื่อแล้วกด Done
4. เปิด Service Account ที่สร้าง → แท็บ **Keys → Add key → Create new key → JSON** ไฟล์จะถูกดาวน์โหลด
5. เปลี่ยนชื่อไฟล์เป็น `credentials.json` วางไว้ที่โฟลเดอร์โปรเจกต์ (**ห้าม commit** - อยู่ใน `.gitignore` แล้ว)
6. สร้าง Google Sheet เปล่าชื่อ `Hotel Booking Dashboard Data` แล้วกด **Share** ให้อีเมลของ Service Account (`...@...iam.gserviceaccount.com` ในไฟล์ JSON) สิทธิ์ **Editor**

## ตารางใน schema `production`
| ตาราง | คำอธิบาย |
|---|---|
| `fact_bookings` | รายการจองที่ทำความสะอาดแล้ว + ฟีเจอร์ใหม่ (lead_time_bucket, season, stay_revenue, realized_revenue ฯลฯ) |
| `agg_monthly` | สรุปรายเดือน × โรงแรม |
| `agg_market_segment` | สรุปตามช่องทางการขาย |
| `agg_country` | สรุปตามประเทศ |
| `agg_lead_time` | สรุปตามช่วง Lead time × ประเภทเงินมัดจำ |
| `agg_guest_type` | สรุปตามลูกค้าใหม่/เก่า × ประเภทลูกค้า |

## Looker Studio
สร้าง Data Source → connector **Google Sheets** → เลือก Spreadsheet และ Worksheet (`agg_*` ใช้ทำกราฟสรุป, `fact_bookings` ใช้ทำ Filter/Scorecard) แล้วตั้งค่าแชร์ Report เป็น *Anyone with the link can view*

## ถาม-ตอบข้อมูลด้วย AI ใน Terminal (ask.py)
พิมพ์คำถามภาษาไทยแล้วให้ Claude เขียน SQL ดึงข้อมูลจากตาราง `production` และสรุปคำตอบให้ (รันได้หลังทำ ingest + transform แล้ว)
1. ขอ API Key ที่ console.anthropic.com แล้วใส่ในไฟล์ `.env` เป็น `ANTHROPIC_API_KEY=...` (ห้าม commit)
2. `pip install -r requirements.txt`
3. `python ask.py` (เพิ่ม `--hide-sql` ถ้าไม่อยากให้แสดง SQL ที่ใช้)

SQL ที่ AI สร้างถูกจำกัดให้เป็น SELECT อย่างเดียว รันใน read-only transaction และมี timeout 15 วินาที
