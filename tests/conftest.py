"""Shared test setup.

Env vars must exist before app.py / shift_app.py are imported (they read
config at import time), so they are set here at conftest import.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="cfa-shift-tests-")
os.environ.setdefault("SHIFT_ADMIN_PIN", "9999")
os.environ.setdefault("SHIFT_DB_PATH", os.path.join(_TMP, "boot.db"))
os.environ.setdefault("ENV", "test")
os.environ.setdefault("SCHEDULE_SECRET", "test-schedule-secret")
