"""Shared pytest configuration.

The suite currently has zero tests (the project is a skeleton). pytest exits with
code 5 ("no tests collected"), which CI treats as a failure. Until real tests land,
remap that one case to success so `uv run pytest` is green on an empty suite. Any
actual collection — or any failure — keeps its normal exit code.
"""

from __future__ import annotations

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK
