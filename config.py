"""ค่าตั้งต้นและตัวช่วยเชื่อมต่อฐานข้อมูล (อ่านจากไฟล์ .env)"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

PG_USER = os.getenv("POSTGRES_USER", "de_user")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "de_pass")
PG_DB = os.getenv("POSTGRES_DB", "hotel_db")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")

CSV_PATH = os.getenv("CSV_PATH", "data/hotel_bookings.csv")
RAW_TABLE = os.getenv("RAW_TABLE", "hotel_bookings")
RAW_SCHEMA = "raw_data"
PROD_SCHEMA = "production"

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Hotel Booking Dashboard Data")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")


def get_engine():
    url = f"postgresql+psycopg2://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    return create_engine(url)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
