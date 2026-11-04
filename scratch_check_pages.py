import sqlite3
import sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/tenderforge.db')
cur = conn.cursor()

# Check T001
cur.execute("SELECT extracted_text FROM tenders WHERE id=80")
text1 = cur.fetchone()[0]

print("=== T001 OCCURRENCES ===")
for p in text1.split("--- [Page "):
    if not p.strip(): continue
    p_num = p.split("] ---")[0]
    for l in p.splitlines():
        low = l.lower()
        if any(w in low for w in ["appropriate class", "contractor registration", "class of registration", "plant and machinery", "works in hand", "power of attorney"]):
            print(f"Page {p_num}: {l.strip()}")

# Check T003
cur.execute("SELECT extracted_text FROM tenders WHERE id=79")
text3 = cur.fetchone()[0]

print("\n=== T003 OCCURRENCES ===")
for p in text3.split("--- [Page "):
    if not p.strip(): continue
    p_num = p.split("] ---")[0]
    for l in p.splitlines():
        low = l.lower()
        if any(w in low for w in ["40%", "turnover", "net worth", "g.s.t", "iso 13485", "power of attorney", "authorization form", "similar items", "similar works"]):
            print(f"Page {p_num}: {l.strip()}")
