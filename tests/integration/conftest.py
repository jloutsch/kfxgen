"""Session fixture: one tmp dir to hold all corpus EPUBs and KFX outputs."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("epub_corpus")
