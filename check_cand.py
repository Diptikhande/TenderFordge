with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

target_str = '        user_display_name = user.get("full_name") or "User"'

idx = text.find(target_str)
assert idx != -1, "target_str not found"

target_block = text[idx:idx+250]
print("Found target block:\n", repr(target_block))

# Find line breaks
lines = target_block.splitlines(keepends=True)
sep = '\r\n' if '\r\n' in lines[0] else '\n'

repl = (
    '        user_display_name = user.get("full_name") or "User"' + sep +
    '        user_profile = get_company_profile(user_id) or {}' + sep +
    '        company_name = user_profile.get("company_name") or user_display_name' + sep +
    '        user_initials = "".join([part[0].upper() for part in (company_name or user_display_name).split()[:2]]) or "TF"' + sep
)

to_replace = lines[0] + lines[1]
text = text.replace(to_replace, repl, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Saved app.py successfully")
