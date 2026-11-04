import tf_test_env  # must precede project imports
import os
import database as db
import profile_fields as PF
from streamlit.testing.v1 import AppTest

def _item(req_id, text, category="Eligibility", status="INFORMATION REQUIRED", page=1):
    return {"requirement_id": req_id, "requirement": text, "category": category,
            "status": status, "source_page": page, "source_quotation": text, "is_mandatory": True}

def _save_analysis(user_id, tender_id, matrix):
    db.save_analysis(tender_id, "BID", "test", matrix, {}, {}, {}, {}, [], [], user_id=user_id)

user = db.create_user("test_ui_debug@example.com", "Password123!", "UI Debug")[2]["id"]
db.save_company_profile({"company_name": "UI Co"}, user)
tid = db.save_tender("ui.pdf", "/nonexistent/ui.pdf", "Some tender text", 1, "success", user)
_save_analysis(user, tid, [_item("C-1", "Valid ISO 9001 certification", category="Certifications")])
profile_user = db.get_user_by_id(user)

at = AppTest.from_file("app.py", default_timeout=30)
at.session_state["authenticated"] = True
at.session_state["user"] = profile_user
at.session_state["current_page"] = "🏢 Company Profile"
at.run()
print("Initial run exception:", at.exception)

sel = at.selectbox(key=f"profile_tender_select_{user}")
print("Found selectbox:", sel)
sel.select(tid).run()
print("After select exception:", at.exception)
print("All text_input keys:")
for ti in at.text_input:
    print(" ", ti.key)
print("Markdown values containing ISO:")
for m in at.markdown:
    if "ISO" in m.value:
        print("  MD:", m.value[:100])
