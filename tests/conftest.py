"""
Pytest Configuration

Shared fixtures and configuration for all tests.
"""

import os
import sys

import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin"))


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def kfx_dir(fixtures_dir):
    """Path to KFX fixtures"""
    return fixtures_dir / "kfx"


@pytest.fixture
def load_kfx_fragments():
    """Load and deserialize a generated KFX file, returning its fragment list.

    Thin wrapper around tests._kfx_introspect.load_fragments — kept as a
    fixture for tests that prefer the dependency-injection style. The
    underlying implementation lives in the shared helper. (#82)
    """
    from tests._kfx_introspect import load_fragments

    return load_fragments


# Markers are registered in pytest.ini's [pytest] section.
