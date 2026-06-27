#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kfxgen - Open-Source KFX Generation Plugin for Calibre

Converts ebooks to KFX format for Kindle devices.

v5.0.0: Native KFX generation with working TOC navigation.
        No external dependencies required.
        Falls back to Kindle Previewer if native generation fails.
"""

__license__ = "GPL v3"
__copyright__ = "2025-2026, Justin Loutsch <justin.loutsch@gmail.com>"
__docformat__ = "restructuredtext en"

from calibre.customize.conversion import OutputFormatPlugin

# Single source of truth for the package version lives in
# plugin/kfxgen/__init__.py — read it here so the wrapper and the inner
# module never drift apart.
from .kfxgen import version as _kfxgen_version


class KFXGenOutputPlugin(OutputFormatPlugin):
    """
    kfxgen Output Format Plugin

    Converts ebooks to KFX format using native generation.
    No external tools required.

    Features:
    - Working TOC navigation on Kindle devices
    - Per-chapter content structure
    - Full metadata preservation
    - Optional Kindle Previewer fallback for complex books
    """

    name = "kfxgen"
    description = "Native KFX generation, blazing fast. Builds Kindle-ready KFX ebooks in seconds — no Kindle Previewer round-trip, no external tools, just pure Python. A drop-in replacement for KFX Output, only quicker."
    supported_platforms = ["windows", "osx", "linux"]
    author = "Justin Loutsch"
    version = _kfxgen_version

    file_type = "kfx"
    commit_name = "kfxgen"
    minimum_calibre_version = (5, 0, 0)
    can_be_disabled = True

    def convert(self, oeb_book, output_path, input_plugin, opts, log):
        """
        Main conversion method called by Calibre.

        Tries native KFX generation first. Falls back to Kindle Previewer
        pipeline if native generation fails and KP is available.

        Args:
            oeb_book: Calibre's OEB book object
            output_path: Path where KFX should be written
            input_plugin: Plugin that read the input file
            opts: Conversion options
            log: Calibre's logger object
        """
        log.info("kfxgen v{}.{}.{} - Starting conversion".format(*self.version))

        # Try native generation first
        try:
            from .kfxgen.converter import convert_oeb_to_kfx

            convert_oeb_to_kfx(oeb_book, output_path, opts, log)
            return
        except Exception as e:
            log.warn("Native KFX generation failed: {}".format(str(e)))
            import traceback

            log.debug(traceback.format_exc())

        # Fall back to Kindle Previewer if available
        log.info("Attempting Kindle Previewer fallback...")
        try:
            from .kfxgen.kp_calibre_converter import convert_oeb_to_kfx as kp_convert

            kp_convert(oeb_book, output_path, opts, log)
        except Exception as e:
            log.error("Kindle Previewer fallback also failed: {}".format(str(e)))
            import traceback

            log.error(traceback.format_exc())
            raise
