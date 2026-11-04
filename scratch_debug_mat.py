import sqlite3
from analyzer import run_deterministic_checks

conn = sqlite3.connect('data/tenderforge.db')
cur = conn.cursor()
cur.execute("SELECT extracted_text, filepath FROM tenders WHERE filename LIKE '%T003%' LIMIT 1")
txt, fp = cur.fetchone()
conn.close()

mat = run_deterministic_checks(txt, {}, pdf_path=fp)
for c in mat:
    if "Tender specifies" in c.get("tender_evidence", ""):
        print(f"ID: {c.get('requirement_id')} | Page: {c.get('source_page')} | Req: {c.get('requirement')} | Ev: {c.get('tender_evidence')}")
