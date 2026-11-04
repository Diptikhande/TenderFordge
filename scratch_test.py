from pdf_processor import extract_text_from_pdf
from analyzer import run_deterministic_checks, extract_tender_missing_profile_fields

for name in ['T001.pdf', 'T003.pdf']:
    path = f'data/uploads/{name}'
    res = extract_text_from_pdf(path)
    text = res['text']
    empty_profile = {}
    matrix = run_deterministic_checks(text, empty_profile, pdf_path=path)
    print(f"\n==================== {name} Matrix items ({len(matrix)}) ====================")
    for m in matrix:
        print(f"  [{m.get('requirement_id')}] (p.{m.get('source_page')}) {m.get('requirement')[:60]} -> {m.get('status')}")
    
    missing = extract_tender_missing_profile_fields(text, empty_profile, pdf_path=path)
    print(f"\n==================== {name} Missing Fields ({len(missing)}) ====================")
    for f in missing:
        print(f"  [Sec {f['section_id']}] {f['field_label']} (Page {f['source_page']}) [Key: {f['field_key']}]")
        print(f"       Req: {f['tender_quote'][:80]}")
