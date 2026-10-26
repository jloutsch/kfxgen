"""The JPEG marker scan in `_detect_image_dimensions` finds a picture's size
past padding, stuffed bytes and parameterless markers, and gives up on a
truncated file rather than reading past its end (#457: these branches had
no test)."""

import pytest

from kfxgen.native_generator import NativeKFXGenerator

SOF = bytes.fromhex("FFC000110800100020")  # baseline SOF: 16 high, 32 wide


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "size"),
    [
        # 0xFF fill bytes before a marker are skipped.
        (bytes.fromhex("FFD8FFFF") + SOF[1:], (32, 16)),
        # A stuffed byte (FF 00) is passed over.
        (bytes.fromhex("FFD8FF00") + SOF, (32, 16)),
        # RST and SOI markers carry no length.
        (bytes.fromhex("FFD8FFD0") + SOF, (32, 16)),
        (bytes.fromhex("FFD8FFD8") + SOF, (32, 16)),
        # EOI ends the scan: a SOF after it isn't the picture's. (Read as a
        # segment, "00 02" after it would step onto that SOF.)
        (bytes.fromhex("FFD8FFD90002") + SOF, (None, None)),
        # A SOF cut short, and a segment cut before its length.
        (bytes.fromhex("FFD8FFC00011"), (None, None)),
        (bytes.fromhex("FFD8FFE000"), (None, None)),
    ],
    ids=["padding", "stuffed", "rst", "soi", "eoi", "short-sof", "short-length"],
)
def test_the_marker_scan(data, size):
    assert NativeKFXGenerator._detect_image_dimensions(data) == size
