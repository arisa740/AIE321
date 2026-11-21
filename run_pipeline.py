"""รันทั้งไปป์ไลน์ด้วยคำสั่งเดียว:  python run_pipeline.py
   ตัวเลือก:  --skip-publish  (รันเฉพาะ ingest + transform โดยไม่ส่งขึ้น Google Sheets)"""
import argparse
import sys
import time

import ingest
import transform


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-publish", action="store_true")
    args = parser.parse_args()

    steps = [("INGEST", ingest.ingest), ("TRANSFORM", transform.transform)]
    if not args.skip_publish:
        import publish
        steps.append(("PUBLISH", publish.publish))

    for name, fn in steps:
        t0 = time.time()
        print(f"\n===== {name} =====")
        try:
            fn()
        except Exception as e:
            print(f"!! ขั้นตอน {name} ล้มเหลว: {e}")
            sys.exit(1)
        print(f"===== {name} เสร็จใน {time.time() - t0:.1f} วินาที =====")
    print("\nPipeline เสร็จสมบูรณ์")


if __name__ == "__main__":
    main()
