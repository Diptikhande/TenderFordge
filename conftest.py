"""pytest configuration: isolate every test run from the production database."""

import tf_test_env  # noqa: F401  (must be imported before any project module)


def pytest_sessionfinish(session, exitstatus):
    tf_test_env.assert_production_db_untouched()
    print(f"\n[tenderforge] Production DB checksum unchanged: {tf_test_env.PRODUCTION_DB_MD5_AT_START}")
    print(f"[tenderforge] Tests used isolated data dir: {tf_test_env.TEST_DATA_DIR}")
