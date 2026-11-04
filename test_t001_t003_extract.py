import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
from analyzer import extract_tender_missing_profile_fields

conn = sqlite3.connect('data/tenderforge.db')
cur = conn.cursor()

cur.execute("SELECT extracted_text, filepath FROM tenders WHERE id=80")
t1_text, t1_path = cur.fetchone()

cur.execute("SELECT extracted_text, filepath FROM tenders WHERE id=79")
t3_text, t3_path = cur.fetchone()

print('=== T001 MISSING FIELDS ===')
m1 = extract_tender_missing_profile_fields(t1_text, {}, pdf_path=t1_path)
for item in m1:
    print(f"[{item['section_id']}. {item['section_name']}] {item['field_label']} (Page {item['source_page']}) | Key: {item['field_key']} | Quote: {item['tender_quote'][:70]}")

print('\n=== T003 MISSING FIELDS ===')
m3 = extract_tender_missing_profile_fields(t3_text, {}, pdf_path=t3_path)
for item in m3:
    print(f"[{item['section_id']}. {item['section_name']}] {item['field_label']} (Page {item['source_page']}) | Key: {item['field_key']} | Quote: {item['tender_quote'][:70]}")

