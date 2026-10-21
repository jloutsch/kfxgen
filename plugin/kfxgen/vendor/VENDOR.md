# Vendored packages (#235)

kfxgen draws MathML equations with ziamath. calibre ships none of these
packages, so they are copied here unchanged from their PyPI wheels.
`kfxgen/_vendor.py` puts this folder on `sys.path` when an equation is
first drawn.

| Package | Version | Licence | Wheel SHA-256 |
|---|---|---|---|
| ziamath | 0.13 | MIT | 67a9b9714101fc90c2d6d0328e578f3ac311fb007644f2bbb9314ba2bc1c1459 |
| ziafont | 0.11 | MIT | b62132be3da9667a80d048ab5498f107e49761722b69965788c2c747a38e7373 |
| latex2mathml | 3.81.1 | MIT | c337668441b71c819b6733905a8058ba9a9d767bae11a0c5fdacb3aff31361bd |

ziamath includes the STIX Two Math font, version 2.11 b159, under the SIL
Open Font License 1.1. The licence texts are in `LICENSES/`.

Left out of the wheels:

- `ziafont/fonts/DejaVuSans.ttf` (757 KB): ziafont's default text font.
  ziamath sets text in STIX Two Math, and leaving it out changes no
  drawing.
- `__pycache__` folders, and each `*.dist-info` folder except
  latex2mathml's `METADATA`, which it reads for its version when it loads.

latex2mathml is ziamath's LaTeX input. kfxgen gives it only MathML, but
ziamath imports it when it loads.

To update: install the new versions into a scratch virtual environment
with pinned versions, copy the three package folders here with the same
exclusions, update this table from PyPI's wheel hashes, and run the MathML
tests.
