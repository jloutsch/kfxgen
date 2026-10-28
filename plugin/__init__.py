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

from calibre.customize.conversion import OutputFormatPlugin, OptionRecommendation

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

    # Font sizes as the book writes them, as Kindle Previewer writes them:
    # calibre's default rescaling snaps 0.9em to 0.75, which put a poem's
    # verse a quarter below its notes (#241). MED, so a user's own Look &
    # Feel setting still wins.
    recommendations = {
        ("disable_font_rescaling", True, OptionRecommendation.MED),
    }

    options = {
        OptionRecommendation(
            name="kfxgen_embed_original_images",
            recommended_value=False,
            help=(
                "Embed images at their original resolution instead of "
                "downscaling/recompressing them for Kindle. Produces much "
                "larger KFX files for illustrated books."
            ),
        ),
        OptionRecommendation(
            name="kfxgen_disable_font_embedding",
            recommended_value=False,
            help=(
                "Do not embed the book's own @font-face fonts. Embedding is on "
                "by default (so the book's typography renders on-device); enable "
                "this to use the font installed/selected on the Kindle instead."
            ),
        ),
        OptionRecommendation(
            name="kfxgen_disable_native_tables",
            recommended_value=False,
            help=(
                "Write every table as one paragraph per row instead of as a "
                "native Kindle table. Native tables are on by default; enable "
                "this if a book's tables display badly on your Kindle."
            ),
        ),
        OptionRecommendation(
            name="kfxgen_open_at_cover",
            recommended_value=False,
            help=(
                "Open a new book at its cover. By default it opens where the "
                "book marks the start of its text, usually the first chapter, "
                "as store books do."
            ),
        ),
    }

    # --- Customization (Preferences -> Plugins -> Customize) ---

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.kfxgen.config import ConfigWidget

        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()

    def _apply_prefs(self, opts, log):
        """Fold the persistent Customize setting into the conversion opts.

        The Customize-dialog checkbox and the per-conversion
        `--kfxgen-disable-font-embedding` option are OR'd: either disables
        font embedding. Default (both off) embeds. The native-table setting
        works the same way: either disables native tables (#219).
        """
        try:
            from calibre_plugins.kfxgen.prefs import prefs

            if prefs["disable_font_embedding"]:
                opts.kfxgen_disable_font_embedding = True
                log.info("  Font embedding disabled by plugin setting")
            if prefs["disable_native_tables"]:
                opts.kfxgen_disable_native_tables = True
                log.info("  Native tables disabled by plugin setting")
            if prefs["open_at_cover"]:
                opts.kfxgen_open_at_cover = True
                log.info("  New books open at the cover, by plugin setting")
        except Exception as e:
            log.warn("Could not read kfxgen plugin settings ({})".format(e))

    def specialize_css_for_output(self, log, opts, item, stylizer):
        """Called by calibre on each file just before it flattens the CSS
        and renames the classes: note which elements the book itself
        classes `toc`, a printed contents listing (#329)."""
        try:
            from .kfxgen.converter import mark_source_classes

            if hasattr(item.data, "iter"):
                mark_source_classes(item.data)
        except Exception as e:
            log.warn("kfxgen: could not record the book's classes ({})".format(e))

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
        self._apply_prefs(opts, log)

        import traceback

        # Try native generation first
        try:
            from .kfxgen.converter import convert_oeb_to_kfx

            convert_oeb_to_kfx(oeb_book, output_path, opts, log)
            return
        except Exception as e:
            # Its type and traceback where a user sees them: an exception with
            # no message logged nothing after the colon (#434, #472).
            native_error = e
            log.warn("Native KFX generation failed: {}: {}".format(type(e).__name__, e))
            log.warn(traceback.format_exc())

        # Fall back to Kindle Previewer if available
        log.info("Attempting Kindle Previewer fallback...")
        try:
            from .kfxgen.kp_calibre_converter import convert_oeb_to_kfx as kp_convert

            kp_convert(oeb_book, output_path, opts, log)
        except Exception as e:
            log.error(
                "Kindle Previewer fallback also failed: {}: {}".format(
                    type(e).__name__, e
                )
            )
            log.error(traceback.format_exc())
            raise e from native_error
