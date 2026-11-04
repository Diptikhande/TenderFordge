import sys
import re
from pdf_processor import extract_text_from_pdf

sys.stdout.reconfigure(encoding='utf-8')

res1 = extract_text_from_pdf('data/uploads/T001.pdf')
text1 = res1['text']

print(f"Total pages in T001: {res1['page_count']}")

# Search for envelope 1 / technical bid requirements in T001
for page_block in text1.split("--- [Page "):
    if not page_block.strip():
        continue
    page_num = page_block.split("] ---")[0]
    content = page_block.split("] ---")[1] if "] ---" in page_block else page_block
    
    # Check for keywords
    lower = content.lower()
    if any(k in lower for k in ["envelope no. 1", "envelope no.1", "technical bid", "documents to be submitted", "qualification criteria", "contractor registration", "class"]):
        if any(w in lower for w in ["registration", "machinery", "turnover", "experience", "power of attorney", "demand draft", "validity"]):
            print(f"\n--- MATCH ON PAGE {page_num} ---")
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            for l in lines[:15]:
                print(f"  {l}")
