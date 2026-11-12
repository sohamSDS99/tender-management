"""Runs before conftest.py and before any app module is imported.

The repository's .env may hold a real BREVO_API_KEY, and Settings reads that
file, so any code path that calls get_settings() would otherwise be able to send
a real email from the test suite. An environment variable outranks the .env
file, and subprocesses the suite spawns inherit it. D43.
"""

import os

os.environ["BREVO_API_KEY"] = ""
