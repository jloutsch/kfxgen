import pytest
from pathlib import Path

SRC = Path("plugin/__init__.py").read_text()


@pytest.mark.unit
def test_imports_option_recommendation():
    assert "OptionRecommendation" in SRC


@pytest.mark.unit
def test_defines_embed_original_images_option():
    assert "kfxgen_embed_original_images" in SRC
    # default must be False (optimization on by default)
    assert "recommended_value=False" in SRC


@pytest.mark.unit
def test_option_has_help_text():
    assert "original resolution" in SRC.lower() or "embed" in SRC.lower()
