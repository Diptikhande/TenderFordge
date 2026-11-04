import sqlite3
from analyzer import extract_tender_missing_profile_fields

conn = sqlite3.connect('data/tenderforge.db')
cur = conn.cursor()
cur.execute("SELECT extracted_text, filepath FROM tenders WHERE filename LIKE '%T003%' LIMIT 1")
txt, fp = cur.fetchone()
conn.close()

fields = extract_tender_missing_profile_fields(txt, {}, pdf_path=fp)
for f in fields:
    print(f"{f['field_key']:30} | Page: {f['source_page']} | Sec: {f['section_id']} | Label: {f['field_label'][:30]} | Quote: {f['tender_quote'][:40]}")
