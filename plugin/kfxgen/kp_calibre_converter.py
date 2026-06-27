"""
Kindle Previewer-based KFX Converter for Calibre

Uses Amazon's Kindle Previewer CLI to convert EPUB to KPF,
then kfxlib to create a single KFX file.

This approach produces correct formatting, TOC navigation, and cover thumbnails
because it uses Amazon's own conversion engine.
"""

import os
import sys
import tempfile
import subprocess
import uuid
from pathlib import Path


class KPCalibreConverter:
    """
    Convert Calibre OEB book to KFX using Kindle Previewer + kfxlib.

    This is the recommended approach as it uses Amazon's official
    conversion engine for maximum compatibility.
    """

    # Default Kindle Previewer locations (standard install paths)
    KP_PATHS = [
        # macOS
        "/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3",
        os.path.expanduser(
            "~/Applications/Kindle Previewer 3.app/Contents/MacOS/Kindle Previewer 3"
        ),
        # Windows
        "C:\\Program Files\\Kindle Previewer 3\\Kindle Previewer 3.exe",
        "C:\\Program Files (x86)\\Kindle Previewer 3\\Kindle Previewer 3.exe",
        os.path.expanduser(
            "~\\AppData\\Local\\Amazon\\Kindle Previewer 3\\Kindle Previewer 3.exe"
        ),
    ]

    def __init__(self, kindle_previewer_path=None, log=None):
        """
        Initialize converter.

        Args:
            kindle_previewer_path: Path to Kindle Previewer executable.
                                   Auto-detected if not provided.
            log: Calibre logger (or None for print-based logging)
        """
        self.kindle_previewer_path = (
            kindle_previewer_path or self._find_kindle_previewer()
        )
        self.log = log
        self._kfxlib_path = None
        self._setup_kfxlib()

    def _log_info(self, msg):
        if self.log:
            self.log.info(msg)
        else:
            print(msg)

    def _log_error(self, msg):
        if self.log:
            self.log.error(msg)
        else:
            print(f"ERROR: {msg}")

    def _find_kindle_previewer(self):
        """Find Kindle Previewer installation."""
        for path in self.KP_PATHS:
            if os.path.isfile(path):
                return path
        return None

    def _setup_kfxlib(self):
        """Set up kfxlib import paths."""
        module_dir = os.path.dirname(os.path.abspath(__file__))

        candidates = [
            os.path.join(module_dir, "kfxlib"),
            os.path.join(module_dir, "..", "..", "research", "kfxlib_extracted"),
        ]

        for path in candidates:
            if os.path.isdir(path):
                if path not in sys.path:
                    sys.path.insert(0, path)
                self._kfxlib_path = path
                break

    def _fix_cover_metadata(self, book):
        """
        Fix cover metadata to ensure thumbnail displays on Kindle.

        Adds the $162 (mime type) field if missing.
        """
        try:
            from kfxlib.ion import IonSymbol as IS

            cover_id = book.get_metadata_value("cover_image")
            if not cover_id:
                return

            cover_frag = book.fragments.get(ftype="$164", fid=cover_id)
            if not cover_frag:
                return

            if IS("$162") not in cover_frag.value:
                fmt_symbol = cover_frag.value.get(IS("$161"))
                if fmt_symbol:
                    fmt_str = str(fmt_symbol)
                    if "285" in fmt_str or "jpg" in fmt_str.lower():
                        mime_type = "image/jpg"
                    elif "png" in fmt_str.lower():
                        mime_type = "image/png"
                    else:
                        mime_type = "image/jpeg"

                    cover_frag.value[IS("$162")] = mime_type
                    self._log_info(
                        f"  Fixed cover metadata: added mime type {mime_type}"
                    )

        except Exception as e:
            self._log_error(f"Could not fix cover metadata: {e}")

    def _fix_title_metadata(self, book):
        """
        Add required metadata fields for Kindle thumbnail display.

        Adds ASIN, asset_id, and cde_content_type if missing.
        """
        try:
            from kfxlib.ion import IonSymbol as IS, IonStruct

            for frag in book.fragments:
                if str(frag.ftype) == "$490":
                    if IS("$491") not in frag.value:
                        continue

                    for section in frag.value[IS("$491")]:
                        if not hasattr(section, "get"):
                            continue

                        section_name = section.get(IS("$495"), "")
                        if section_name != "kindle_title_metadata":
                            continue

                        if IS("$258") not in section:
                            continue

                        fields = section[IS("$258")]
                        existing_keys = set()
                        for field in fields:
                            if hasattr(field, "get"):
                                existing_keys.add(field.get(IS("$492"), ""))

                        # Generate unique IDs
                        book_uuid = str(uuid.uuid4()).replace("-", "").upper()

                        if "cde_content_type" not in existing_keys:
                            fields.append(
                                IonStruct(
                                    {IS("$492"): "cde_content_type", IS("$307"): "PDOC"}
                                )
                            )
                            self._log_info("  Added cde_content_type: PDOC")

                        if "ASIN" not in existing_keys:
                            asin = book_uuid[:32]
                            fields.append(
                                IonStruct({IS("$492"): "ASIN", IS("$307"): asin})
                            )
                            self._log_info(f"  Added ASIN: {asin}")

                        if "asset_id" not in existing_keys:
                            asset_id = f"CR!{book_uuid[:28]}"
                            fields.append(
                                IonStruct(
                                    {IS("$492"): "asset_id", IS("$307"): asset_id}
                                )
                            )
                            self._log_info(f"  Added asset_id: {asset_id}")

                        return

        except Exception as e:
            self._log_error(f"Could not fix title metadata: {e}")

    def convert_oeb(self, oeb_book, output_path, opts=None):
        """
        Convert Calibre OEB book to KFX.

        Args:
            oeb_book: Calibre OEB book object
            output_path: Path for output KFX file
            opts: Conversion options (optional)

        Returns:
            bool: True if successful
        """
        if not self.kindle_previewer_path:
            self._log_error("Kindle Previewer not found")
            self._log_error("Please install Kindle Previewer 3 from Amazon")
            return False

        self._log_info("=" * 70)
        self._log_info("kfxgen - Kindle Previewer KFX Converter")
        self._log_info("=" * 70)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Step 1: Save OEB as EPUB
            self._log_info("Step 1: Saving as EPUB...")
            epub_path = os.path.join(temp_dir, "book.epub")

            try:
                self._save_oeb_as_epub(oeb_book, epub_path)
                self._log_info(f"  Saved EPUB: {os.path.getsize(epub_path):,} bytes")
            except Exception as e:
                self._log_error(f"Failed to save EPUB: {e}")
                return False

            # Step 2: Run Kindle Previewer
            self._log_info("Step 2: Kindle Previewer (EPUB -> KPF)...")
            kpf_dir = os.path.join(temp_dir, "kpf_output")
            os.makedirs(kpf_dir, exist_ok=True)

            try:
                result = subprocess.run(
                    [
                        self.kindle_previewer_path,
                        epub_path,
                        "-convert",
                        "-output",
                        kpf_dir,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10 minute timeout
                )

                if (
                    result.returncode != 0
                    or "converted successfully" not in result.stdout.lower()
                ):
                    self._log_error("Kindle Previewer conversion failed")
                    self._log_error(f"stdout: {result.stdout}")
                    self._log_error(f"stderr: {result.stderr}")
                    return False

                self._log_info("  KPF created successfully")

            except subprocess.TimeoutExpired:
                self._log_error("Kindle Previewer timed out after 10 minutes")
                return False
            except Exception as e:
                self._log_error(f"Failed to run Kindle Previewer: {e}")
                return False

            # Find the KPF file
            kpf_subdir = os.path.join(kpf_dir, "KPF")
            kpf_files = (
                list(Path(kpf_subdir).glob("*.kpf"))
                if os.path.isdir(kpf_subdir)
                else []
            )

            if not kpf_files:
                self._log_error("No KPF file generated")
                return False

            kpf_path = str(kpf_files[0])

            # Step 3: Convert KPF to KFX using kfxlib
            self._log_info("Step 3: kfxlib (KPF -> KFX)...")

            try:
                from kfxlib import YJ_Book
                from kfxlib.utilities import file_write_binary

                book = YJ_Book(kpf_path)
                book.decode_book()

                # Fix metadata for cover thumbnail
                self._fix_cover_metadata(book)
                self._fix_title_metadata(book)

                kfx_data = book.convert_to_single_kfx()

                if not kfx_data:
                    self._log_error("Failed to convert KPF to KFX")
                    return False

                file_write_binary(output_path, kfx_data)
                self._log_info("  KFX created successfully")

            except ImportError as e:
                self._log_error(f"Failed to import kfxlib: {e}")
                return False
            except Exception as e:
                self._log_error(f"kfxlib conversion failed: {e}")
                import traceback

                traceback.print_exc()
                return False

        # Verify output
        if os.path.isfile(output_path):
            size = os.path.getsize(output_path)
            self._log_info("")
            self._log_info("=" * 70)
            self._log_info(f"Success! Generated: {output_path}")
            self._log_info(f"  Size: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
            return True
        else:
            self._log_error("Output file was not created")
            return False

    def _save_oeb_as_epub(self, oeb_book, output_path):
        """
        Save Calibre OEB book as EPUB file.

        Args:
            oeb_book: Calibre OEB book object
            output_path: Path for EPUB output
        """
        from calibre.ebooks.oeb.writer import OEBWriter

        # OEBWriter can write OEB container to EPUB format
        writer = OEBWriter()
        writer(oeb_book, output_path)


def convert_oeb_to_kfx(oeb_book, output_path, opts, log):
    """
    Convert Calibre OEB book to KFX format using Kindle Previewer.

    This is the main entry point called by the Calibre plugin.

    Args:
        oeb_book: Calibre OEB book object
        output_path: Path to write KFX file
        opts: Conversion options
        log: Calibre logger

    Returns:
        None (writes to output_path)

    Raises:
        Exception on conversion failure
    """
    converter = KPCalibreConverter(log=log)
    success = converter.convert_oeb(oeb_book, output_path, opts)

    if not success:
        raise Exception("KFX conversion failed")
