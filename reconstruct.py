import py_compile

with open('TenderFordge/app.py', 'r', encoding='utf-8') as f:
    text_sub = f.read()

with open('app.py', 'r', encoding='utf-8') as f:
    text_cur = f.read()

marker = 'sidebar-brand'
idx_sub = text_sub.find(marker)
idx_cur = text_cur.find(marker)

# Find the start of the line containing the marker
line_start_sub = text_sub.rfind('\n', 0, idx_sub) + 1

# In text_cur, find '<div class="' before marker
div_idx = text_cur.rfind('<div', 0, idx_cur)
print("div_idx in text_cur:", div_idx)
print("text_cur prefix:", repr(text_cur[:div_idx]))

candidate = text_sub[:line_start_sub] + text_cur[div_idx:]

with open('candidate_app.py', 'w', encoding='utf-8') as f:
    f.write(candidate)

print('candidate_app.py written, size:', len(candidate), 'lines:', len(candidate.splitlines()))

try:
    py_compile.compile('candidate_app.py', doraise=True)
    print('candidate_app.py compiles cleanly!')
except Exception as e:
    print('Compilation error:', e)
