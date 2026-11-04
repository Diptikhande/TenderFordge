import sys
import re
from pdf_processor import extract_text_from_pdf
from analyzer import find_source_page

sys.stdout.reconfigure(encoding='utf-8')

res1 = extract_text_from_pdf('data/uploads/T001.pdf')
text1 = res1['text']

res3 = extract_text_from_pdf('data/uploads/T003.pdf')
text3 = res3['text']

# Check T001 matches
print("=== T001 KEY MATCHES ===")
p_reg = re.search(r"Registration certificate as a contractor with the[^\n.]*", text1, re.I)
if p_reg:
    print("Contractor reg:", p_reg.group(0), "-> Page:", find_source_page(text1, p_reg.group(0)))

p_poa = re.search(r"person who holds the power of Attorney[^\n.]*", text1, re.I)
if p_poa:
    print("POA:", p_poa.group(0), "-> Page:", find_source_page(text1, p_poa.group(0)))

p_mach = re.search(r"plant and machinery required for the execution[^\n.]*", text1, re.I)
if p_mach:
    print("Machinery:", p_mach.group(0), "-> Page:", find_source_page(text1, p_mach.group(0)))

p_decl = re.search(r"furnish a declaration along with the tender showing all works[^\n.]*", text1, re.I)
if p_decl:
    print("Decl works:", p_decl.group(0), "-> Page:", find_source_page(text1, p_decl.group(0)))


# Check T003 matches
print("\n=== T003 KEY MATCHES ===")
p_tov = re.search(r"average turnover of bidder for the last 3 Financial years[^\n.]*", text3, re.I)
if p_tov:
    print("Turnover:", p_tov.group(0), "-> Page:", find_source_page(text3, p_tov.group(0)))

p_exp = re.search(r"completed\s*/\s*delivered at least Three similar work[^\n.]*", text3, re.I)
if p_exp:
    print("Past Exp:", p_exp.group(0), "-> Page:", find_source_page(text3, p_exp.group(0)))

p_nw = re.search(r"net worth of the Bidder firm[^\n.]*should not be negative[^\n.]*", text3, re.I)
if p_nw:
    print("Net Worth:", p_nw.group(0), "-> Page:", find_source_page(text3, p_nw.group(0)))

p_gst = re.search(r"Copy of G\.S\.T\. Registration Certificate[^\n.]*", text3, re.I)
if p_gst:
    print("GST:", p_gst.group(0), "-> Page:", find_source_page(text3, p_gst.group(0)))

p_iso = re.search(r"ISO\s*13485[^\n.]*", text3, re.I)
if p_iso:
    print("ISO 13485:", p_iso.group(0), "-> Page:", find_source_page(text3, p_iso.group(0)))

p_oem = re.search(r"Manufacturer's\s*Authori[sz]ation\s*Form[^\n.]*|MAF[^\n.]*", text3, re.I)
if p_oem:
    print("OEM MAF:", p_oem.group(0), "-> Page:", find_source_page(text3, p_oem.group(0)))
