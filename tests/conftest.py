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


#: `KFXGEN_*` variables that select what the suite runs, rather than telling
#: the generator how to convert. The sweep below must not touch these: they are
#: how a maintainer points the corpus tests at a directory of books and at a
#: recorded baseline, and clearing them turns the test that reads them into a
#: silent skip (#162).
#:
#: `KFXGEN_CORPUS_DIR` survived the original sweep by accident — it is read at
#: collection time, before any fixture runs, so the parametrized invariant
#: tests kept working. The two baseline variables are read inside the test
#: body, after the fixture, so `test_corpus_metrics_match_baseline` skipped in
#: every configuration from the day the sweep was added. The check the module
#: docstring calls "the check that catches *silent* damage" had never once run.
HARNESS_ENV_VARS = frozenset(
    {
        "KFXGEN_CORPUS_DIR",
        "KFXGEN_CORPUS_BASELINE",
        "KFXGEN_CORPUS_WRITE_BASELINE",
    }
)


@pytest.fixture(autouse=True)
def _isolate_kfxgen_env(monkeypatch):
    """Clear `KFXGEN_*` conversion overrides so tests see documented defaults.

    These variables are meant to be exported for a conversion — that is the
    documented way to correct a device that renders superscripts or images
    wrongly (`docs/kfx-generation-explained.md`). A maintainer who has done so
    and then runs the suite would otherwise get failures unrelated to their
    change, in tests that look like real regressions.

    Autouse and repo-wide because the leak is not specific to the tests that
    exposed it: any test asserting a default is exposed to whatever the shell
    happens to carry. `KFXGEN_MAX_DECODE_SIZE` is read at import time rather
    than per call, so clearing it here has no effect on that one — it is left
    in the sweep anyway so the rule stays "no KFXGEN_* reaches a test".

    `HARNESS_ENV_VARS` is the exception, and the distinction is what the
    original sweep missed: those variables do not change how a book converts,
    they choose which books the suite converts at all.
    """
    for name in [k for k in os.environ if k.startswith("KFXGEN_")]:
        if name in HARNESS_ENV_VARS:
            continue
        monkeypatch.delenv(name, raising=False)
