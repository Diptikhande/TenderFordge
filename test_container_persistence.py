"""
Container Restart Persistence Verification for TenderForge:
1. Creates a test account and company profile.
2. Asserts initial persistence.
"""
import sys
from database import init_db, create_user, authenticate_user, get_company_profile, save_company_profile, get_connection

init_db()

email = "persist_test@tenderforge-audit.internal"
pw = "Persist#Test2026!Secret"
name = "Persist Tester"

# Check if phase 2 (verification after restart)
if len(sys.argv) > 1 and sys.argv[1] == "verify":
    print("Executing Phase 2: Verifying persistence after container restart...")
    ok, msg, user = authenticate_user(email, pw)
    assert ok and user is not None, f"Failed to authenticate after restart: {msg}"
    print(f" -> Authentication succeeded for {user['email']} (ID={user['id']})")
    
    profile = get_company_profile(user["id"])
    assert profile is not None, "Profile was lost after restart!"
    assert profile["company_name"] == "Persistent Dynamics Corporation", "Profile content altered after restart!"
    print(f" -> Company Profile verified: {profile['company_name']}")

    # Clean up
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM company_profile WHERE user_id = ?", (user["id"],))
    c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    print(" -> Phase 2 Cleanup completed successfully.")
    print("RESTART PERSISTENCE VERIFIED SUCCESSFULLY!")
    sys.exit(0)

# Phase 1: Creation before restart
print("Executing Phase 1: Creating account and profile before container restart...")
# Clean prior if exists
conn = get_connection()
c = conn.cursor()
c.execute("SELECT id FROM users WHERE email = ?", (email,))
existing = c.fetchone()
if existing:
    c.execute("DELETE FROM company_profile WHERE user_id = ?", (existing[0],))
    c.execute("DELETE FROM users WHERE id = ?", (existing[0],))
    conn.commit()
conn.close()

ok, msg, user = create_user(email, pw, name)
assert ok and user is not None, f"Registration failed: {msg}"

profile_data = {
    "company_name": "Persistent Dynamics Corporation",
    "industry": "Aerospace & Defense Robotics",
    "years_of_experience": "12 years",
    "annual_turnover": "$40,000,000 USD",
    "certifications": "ISO 9001, AS9100D",
    "technical_capabilities": "Autonomous Aerial Systems",
    "past_projects": "Surveillance Drone Fleet ($18M)",
    "financial_details": "Triple-A rated debt-free",
    "contact_name": name,
    "contact_email": email,
    "contact_phone": "+1 555-4321",
    "contact_address": "88 Orbital Way, Houston, TX"
}
save_company_profile(profile_data, user_id=user["id"])
print(f" -> Account {email} and company profile created before restart.")
