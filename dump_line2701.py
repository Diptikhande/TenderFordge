with open("app.py", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find("save_tender_requirement_response")
print(text[idx-50:idx+250])
