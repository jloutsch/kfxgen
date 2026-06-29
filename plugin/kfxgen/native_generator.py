import errno
import hashlib
import logging
import os
import posixpath
import re

from ._img_tokens import IMG_TOKEN_RE
from .inline_style import ALIGN_MAP
from .kfxlib_minimal.kfx_container import KfxContainer
from .kfxlib_minimal.standard_symbols import StandardSymbolTable
from .kfxlib_minimal.ion import IonStruct, IonDecimal, IonAnnotation, IonBLOB, IS
from .kfxlib_minimal.yj_container import (
    YJFragment,
    YJFragmentList,
    CONTAINER_FORMAT_KFX_MAIN,
)

# Windows drive-relative '..' patterns like 'C:..\foo' or 'C:..' have no
# separator before the '..', so the segment-split traversal check would
# pass them through. Match the literal prefix and reject up front.
_DRIVE_RELATIVE_TRAVERSAL_RE = re.compile(r"^[A-Za-z]:\.\.")

_security_log = logging.getLogger(__name__ + ".security")

# O_NOFOLLOW is POSIX-only; on Windows it isn't defined. Degrade to 0 (no-op
# flag bit) so the plugin still imports under Calibre on Windows. Symlink
# defense on Windows then relies on the islink() check above the open, which
# is TOCTOU-vulnerable but adequate for the single-user threat model.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _safe_write_bytes(path, data):
    """Write `data` to `path` with symlink and traversal defenses (#45).

    - Rejects paths containing '..' segments at the input boundary.
    - Refuses to overwrite if the destination is already a symlink (loud
      failure rather than silently writing through to the symlink target).
    - Writes to `path + ".tmp"` with O_NOFOLLOW where supported (so a
      symlink planted at the tmp slot fails with ELOOP on POSIX) then
      atomically replaces the destination via os.replace. On Windows
      O_NOFOLLOW is unavailable; defense relies on the islink() check.

    A TOCTOU window exists between the islink check and the replace — out
    of scope for the single-user / single-machine threat model in
    SECURITY.md.

    `path` may be str or any os.PathLike (e.g. pathlib.Path). Normalize
    once via os.fspath so the safety checks, the tmp slot, and the
    rename all operate on the same string representation (#102).
    """
    path = os.fspath(path)
    candidate = path

    # Defense in depth — three independent layers, any of which rejects.
    #
    # Layer 1 (regex): catches Windows drive-relative '..' with no separator
    #   before the dots, e.g. 'C:..\\foo' or bare 'C:..'. These have no '/'
    #   or '\\' between the drive letter and the '..', so the segment-split
    #   layers below see a single token like 'C:..' and miss the traversal.
    #   This regex is the only layer that catches that shape.
    if _DRIVE_RELATIVE_TRAVERSAL_RE.match(candidate):
        _security_log.warning("rejected drive-relative output path: %r", path)
        raise ValueError("output path is drive-relative with '..': %r" % path)

    # Layer 2 (raw segment split): substitute '\\' → '/' so Windows-style
    #   separators participate, then split and reject any literal '..'
    #   segment. Catches inputs where '..' appears verbatim as a path
    #   component, e.g. 'foo/../bar', 'foo\\..\\bar', or 'C:.\\..\\foo'
    #   (which splits into ['C:.', '..', 'foo']).
    raw_parts = candidate.replace("\\", "/").split("/")
    if any(p == ".." for p in raw_parts):
        _security_log.warning("rejected output path with traversal segment: %r", path)
        raise ValueError("output path contains '..' segment: %r" % path)

    # Layer 3 (canonical-form check): substitute '\\' → '/' first so the
    #   path uses POSIX separators, then run posixpath.normpath. Using
    #   posixpath (not os.path) makes this layer platform-independent —
    #   os.path.normpath on POSIX hosts treats '\\' as a literal character
    #   and would not collapse Windows-shaped inputs there. After our
    #   '\\' → '/' substitution, posixpath sees a uniform path.
    #
    #   This catches inputs that pass layer 2 but post-normalize to '..',
    #   e.g. 'foo/./../../bar' → '../bar' (layer 2 sees the literal '..',
    #   so this is mostly belt-and-suspenders) and any future shape where
    #   the '..' only emerges after redundant-segment collapse.
    normalized = posixpath.normpath(candidate.replace("\\", "/"))
    if any(p == ".." for p in normalized.split("/")):
        _security_log.warning(
            "rejected output path with traversal segment after normpath: %r", path
        )
        raise ValueError("output path contains '..' segment: %r" % path)

    if os.path.islink(path):
        _security_log.warning("refused to overwrite symlink at output path: %r", path)
        raise OSError(errno.ELOOP, "refusing to overwrite symlink", path)

    tmp = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW
    fd = os.open(tmp, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


class NativeKFXGenerator:
    """
    Generates KFX files from scratch using standard symbols and deterministic
    fragment structures, without requiring templates or Kindle Previewer.
    """

    def __init__(self):
        self.symtab = StandardSymbolTable()
        self.fragments = []
        self.entity_ids = {}  # Map of logical ID to entity ID
        # Start at 349 to avoid collision with fid=348 used by metadata fragments
        # ($585, $490, $538, $410, $419 all use fid=348)
        self.next_entity_id = 349
        self.field_403_counter = 10  # Global counter for Fragment 259 Field $403

    def generate_metadata_only(self, title, author, asin=None, output_path=None):
        """
        Generates a minimal KFX with metadata but no content.
        Useful for verifying container structure.
        """
        # Reset state for clean generation
        self.fragments = []
        self.symtab = StandardSymbolTable()
        self.entity_ids = {}
        self.next_entity_id = 349
        self.field_403_counter = 10

        # Generate Container ID
        # Format: CR! + 28 chars
        # For testing we can use a fixed one or random
        container_id = "CR!FEHAKUKMU05DIVSIJBZWPW44H9VH"

        if asin is None:
            asin = "ASIN_PLACEHOLDER"

        # 1. Build Content Features ($585)
        self.fragments.append(self.build_fragment_585())

        # 2. Build Book Metadata ($490)
        self.fragments.append(
            self.build_fragment_490(title, author, asin, container_id)
        )

        # 3. Create Container
        container = KfxContainer(self.symtab)
        container.fragments = self.fragments

        # Serialize
        data = container.serialize()

        if output_path:
            _safe_write_bytes(output_path, data)

        return data

    def build_fragment_585(self):
        """
        Builds Fragment $585 (Content Features)
        Standard structure for reflowable books.

        Includes capabilities matching known-good KFX files:
        - yj_table: table support
        - reflow-section-size: multi-section content support
        - reflow-style: basic reflowable styling
        - CanonicalFormat: standard format marker
        - yj_hdv: high-definition visual support
        - yj_jpg_rst_marker_present: JPEG restart marker support
        """
        self.symtab.create_local_symbol("com.amazon.yjconversion")
        self.symtab.create_local_symbol("reflow-style")
        self.symtab.create_local_symbol("reflow-section-size")
        self.symtab.create_local_symbol("yj_table")
        self.symtab.create_local_symbol("yj_hdv")
        self.symtab.create_local_symbol("yj_jpg_rst_marker_present")
        self.symtab.create_local_symbol("version")
        self.symtab.create_local_symbol("SDK.Marker")
        self.symtab.create_local_symbol("CanonicalFormat")

        def make_version(major, minor=0):
            return IonStruct(
                IS("version"), IonStruct(IS("$587"), major, IS("$588"), minor)
            )

        features_list = [
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_table",
                IS("$589"),
                make_version(1),
            ),
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "reflow-section-size",
                IS("$589"),
                make_version(9),
            ),
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "reflow-style",
                IS("$589"),
                make_version(1),
            ),
            IonStruct(
                IS("$586"),
                "SDK.Marker",
                IS("$492"),
                "CanonicalFormat",
                IS("$589"),
                make_version(1),
            ),
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_hdv",
                IS("$589"),
                make_version(1),
            ),
            IonStruct(
                IS("$586"),
                "com.amazon.yjconversion",
                IS("$492"),
                "yj_jpg_rst_marker_present",
                IS("$589"),
                make_version(1),
            ),
        ]

        value = IonStruct(IS("$590"), features_list)
        return YJFragment(fid=IS("$348"), ftype=IS("$585"), value=value)

    def build_fragment_164(
        self,
        resource_name,
        location_name,
        image_format="jpeg",
        width=None,
        height=None,
        include_mime=False,
    ):
        """
        Builds Fragment $164 (Resource Metadata) for an image resource.

        The $164 and $417 fragments MUST have different fids, linked by $165:
        - $164.fid = resource_name (e.g., "cover_img")
        - $164.$165 = location_name (e.g., "cover_img_raw") → matches $417 fid
        - $164.$175 = resource_name (self-reference, required by Kindle)
        - $164.$162 = MIME type — required for cover thumbnail extraction on
          Kindle home screen (#39, verified on Paperwhite). Optional for
          body images; reference Calibre KFX includes it on some, omits
          on others.

        Args:
            resource_name: Local symbol name for this resource (e.g., "cover_img")
            location_name: Local symbol name for the $417 raw data (e.g., "cover_img_raw")
            image_format: "jpeg" or "png"
            width: Optional image width in pixels
            height: Optional image height in pixels
            include_mime: If True, emit $162 with the MIME type. Pass True
                for the cover image to enable Kindle home-screen thumbnail
                extraction (#39).

        Returns:
            YJFragment with type $164
        """
        self.symtab.create_local_symbol(resource_name)
        self.symtab.create_local_symbol(location_name)
        # $285 = JPEG, $284 = PNG, $286 = GIF
        format_sym = "$285" if image_format == "jpeg" else "$284"

        value = IonStruct(
            IS("$175"),
            IS(resource_name),  # Self-reference (required by Kindle)
            IS("$161"),
            IS(format_sym),  # Format
            IS("$165"),
            location_name,  # Location ref — plain STRING (not symbol!)
        )
        if width and height:
            value[IS("$422")] = width
            value[IS("$423")] = height
        if include_mime:
            mime = (
                "image/jpg"
                if image_format == "jpeg"
                else ("image/png" if image_format == "png" else "image/jpeg")
            )
            value[IS("$162")] = mime

        return YJFragment(fid=IS(resource_name), ftype=IS("$164"), value=value)

    def build_fragment_417(self, location_name, image_data):
        """
        Builds Fragment $417 (Raw Binary Data) for cover image.

        $417 is a RAW_FRAGMENT_TYPE — serialized as raw bytes, not Ion.
        The fid MUST match the $165 value in the corresponding $164 fragment.

        Args:
            location_name: Local symbol name matching $164.$165
            image_data: Raw image bytes

        Returns:
            YJFragment with type $417
        """
        self.symtab.create_local_symbol(location_name)
        return YJFragment(
            fid=IS(location_name), ftype=IS("$417"), value=IonBLOB(image_data)
        )

    def build_fragment_490(
        self,
        title,
        author,
        asin,
        container_id,
        cover_image=None,
        language="en",
        issue_date=None,
        publisher="kfxgen",
    ):
        """
        Builds Fragment $490 (Book Metadata)
        """
        # Ensure symbols exist
        for sym in [
            "title",
            "author",
            "ASIN",
            "asset_id",
            "book_id",
            "content_id",
            "cover_image",
            "description",
            "is_sample",
            "issue_date",
            "language",
            "override_kindle_font",
            "publisher",
            "kindle_audit_metadata",
            "kindle_ebook_metadata",
            "kindle_title_metadata",
            "kindle_capability_metadata",
            "file_creator",
            "creator_version",
            "selection",
            "nested_span",
            "KPR",
        ]:
            self.symtab.create_local_symbol(sym)

        # 1. Audit Metadata
        audit = IonStruct(
            IS("$495"),
            "kindle_audit_metadata",
            IS("$258"),
            [
                IonStruct(IS("$492"), "file_creator", IS("$307"), "KPR"),
                IonStruct(IS("$492"), "creator_version", IS("$307"), "3.98.0"),
            ],
        )

        # 2. Ebook Metadata
        ebook = IonStruct(
            IS("$495"),
            "kindle_ebook_metadata",
            IS("$258"),
            [
                IonStruct(IS("$492"), "selection", IS("$307"), "enabled"),
                IonStruct(IS("$492"), "nested_span", IS("$307"), "enabled"),
            ],
        )

        # 3. Title Metadata
        # We need to populate this with actual data
        title_meta_list = [
            IonStruct(IS("$492"), "ASIN", IS("$307"), asin),
            IonStruct(IS("$492"), "asset_id", IS("$307"), container_id),
            IonStruct(IS("$492"), "author", IS("$307"), author),
            # Use ASIN as book_id/content_id for simplicity
            IonStruct(IS("$492"), "book_id", IS("$307"), asin),
            IonStruct(IS("$492"), "cde_content_type", IS("$307"), "PDOC"),
            IonStruct(IS("$492"), "content_id", IS("$307"), asin),
            IonStruct(IS("$492"), "description", IS("$307"), f"Book: {title}"),
            IonStruct(IS("$492"), "is_sample", IS("$307"), False),
            IonStruct(
                IS("$492"),
                "issue_date",
                IS("$307"),
                # Fallback is a stable epoch sentinel rather than today's
                # date (#89): a time-of-conversion default would leak
                # date drift into otherwise-identical re-conversions of
                # the same book, breaking byte-determinism across days.
                # Callers with a real date should pass `issue_date`
                # explicitly; this branch only fires for inputs whose
                # OPF metadata has no date, where Kindle's sort order
                # is dominated by sideload-time anyway.
                issue_date or "1970-01-01",
            ),
            IonStruct(IS("$492"), "language", IS("$307"), language),
            IonStruct(IS("$492"), "override_kindle_font", IS("$307"), False),
            IonStruct(IS("$492"), "publisher", IS("$307"), publisher),
            IonStruct(IS("$492"), "title", IS("$307"), title),
        ]

        if cover_image:
            title_meta_list.append(
                IonStruct(IS("$492"), "cover_image", IS("$307"), cover_image)
            )

        title_meta = IonStruct(
            IS("$495"), "kindle_title_metadata", IS("$258"), title_meta_list
        )

        # 4. Capability Metadata (Empty)
        capability = IonStruct(IS("$495"), "kindle_capability_metadata", IS("$258"), [])

        value = IonStruct(IS("$491"), [audit, ebook, title_meta, capability])

        return YJFragment(fid=IS("$348"), ftype=IS("$490"), value=value)

    def build_fragment_258(self, section_names):
        """
        Builds Fragment $258 (Reading Order Metadata)

        Required by the Kindle to discover the navigation structure.
        Has the same $169 structure as $538, linking $351 reading order to sections.

        Args:
            section_names: List of $260 section fids (e.g., ["c0"])
        """
        if isinstance(section_names, str):
            section_names = [section_names]

        for name in section_names:
            self.symtab.create_local_symbol(name)

        value = IonStruct(
            IS("$169"),
            [
                IonStruct(
                    IS("$178"),
                    IS("$351"),
                    IS("$170"),
                    [IS(name) for name in section_names],
                )
            ],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$258"), value=value)

    def build_fragment_538(self, section_names):
        """
        Builds Fragment $538 (Document Data / Reading Order)

        $538.$169[0].$178 = $351 (standard reading order identifier, shared with $389)
        $538.$169[0].$170 = list of $260 section fids

        Args:
            section_names: List of $260 section fids (e.g., ["c0"]) or single string.
        """
        if isinstance(section_names, str):
            section_names = [section_names]

        for name in section_names:
            self.symtab.create_local_symbol(name)

        value = IonStruct(
            IS("$560"),
            IS("$557"),
            IS("$192"),
            IS("$376"),
            IS("$16"),
            IonStruct(IS("$307"), IonDecimal(1), IS("$306"), IS("$308")),
            IS("$112"),
            IS("$383"),
            IS("$436"),
            IS("$441"),
            IS("max_id"),
            15000,
            IS("$42"),
            IonStruct(IS("$307"), IonDecimal("1.2"), IS("$306"), IS("$308")),
            IS("$477"),
            IS("$56"),
            IS("$169"),
            [
                IonStruct(
                    IS("$178"),
                    IS("$351"),
                    IS("$170"),
                    [IS(name) for name in section_names],
                )
            ],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$538"), value=value)

    def build_fragment_389_toc(self, toc_entries):
        """
        Builds Fragment $389 (Navigation) with TOC and landmarks.

        Matches the structure used in real Kindle books:
        - Value is a LIST containing one struct
        - Top-level struct has $178: $351 (reading order ref) and $392 (nav containers)
        - Three nav containers (matching known-good):
          1. TOC ($235: $212) — table of contents entries
          2. Landmarks ($235: $236) — structural landmarks (e.g., bodymatter start)

        Args:
            toc_entries: List of dicts with 'title' and 'position' keys

        Returns:
            YJFragment with type $389
        """
        heading_name = "nH"
        toc_name = "nT"
        landmarks_name = "nL"
        self.symtab.create_local_symbol(heading_name)
        self.symtab.create_local_symbol(toc_name)
        self.symtab.create_local_symbol(landmarks_name)

        # Build $798 heading nav container (required by Kindle for navigation)
        # Contains heading-based navigation mirroring the TOC structure
        heading_units = []
        for entry in toc_entries:
            position = entry["position"]
            heading_unit = IonAnnotation(
                [IS("$393")],
                IonStruct(
                    IS("$238"),
                    IS("$800"),
                    IS("$241"),
                    IonStruct(IS("$244"), "heading-nav-unit"),
                    IS("$246"),
                    IonStruct(IS("$155"), position, IS("$143"), 0),
                ),
            )
            heading_units.append(heading_unit)

        # Wrap all heading units under a single top-level $799 entry
        first_position = toc_entries[0]["position"] if toc_entries else 1800
        heading_top = IonAnnotation(
            [IS("$393")],
            IonStruct(
                IS("$238"),
                IS("$799"),
                IS("$241"),
                IonStruct(IS("$244"), "heading-nav-unit"),
                IS("$246"),
                IonStruct(IS("$155"), first_position, IS("$143"), 0),
                IS("$247"),
                heading_units,
            ),
        )

        heading_container = IonAnnotation(
            [IS("$391")],
            IonStruct(
                IS("$235"),
                IS("$798"),
                IS("$239"),
                IS(heading_name),
                IS("$247"),
                [heading_top],
            ),
        )

        # Build TOC nav_units, each annotated with $393
        nav_units = []
        for entry in toc_entries:
            title = entry["title"]
            position = entry["position"]

            unit = IonAnnotation(
                [IS("$393")],
                IonStruct(
                    IS("$241"),
                    IonStruct(IS("$244"), title),
                    IS("$246"),
                    IonStruct(IS("$155"), position, IS("$143"), 0),
                ),
            )
            nav_units.append(unit)

        # TOC container annotated with $391, type $212
        toc_container = IonAnnotation(
            [IS("$391")],
            IonStruct(
                IS("$235"), IS("$212"), IS("$239"), IS(toc_name), IS("$247"), nav_units
            ),
        )

        # Landmarks container annotated with $391, type $236
        landmarks_units = [
            IonAnnotation(
                [IS("$393")],
                IonStruct(
                    IS("$238"),
                    IS("$233"),
                    IS("$241"),
                    IonStruct(
                        IS("$244"), toc_entries[0]["title"] if toc_entries else "Start"
                    ),
                    IS("$246"),
                    IonStruct(IS("$155"), first_position, IS("$143"), 0),
                ),
            )
        ]
        landmarks_container = IonAnnotation(
            [IS("$391")],
            IonStruct(
                IS("$235"),
                IS("$236"),
                IS("$239"),
                IS(landmarks_name),
                IS("$247"),
                landmarks_units,
            ),
        )

        # Top-level struct with reading order ref and all 3 nav containers
        top_struct = IonStruct(
            IS("$178"),
            IS("$351"),
            IS("$392"),
            [heading_container, toc_container, landmarks_container],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$389"), value=[top_struct])

    def build_fragment_264(self, section_positions):
        """
        Builds Fragment $264 (Position Index)

        Maps section names to lists of position IDs that appear in each section.
        This is critical for enabling navigation through the book.

        Args:
            section_positions: Dict mapping section names to lists of position IDs
                e.g., {"c0": [1000, 1002, 1004, ...], "c1": [1200, 1202, ...]}

        Returns:
            YJFragment with type $264
        """
        entries = []
        for section_name, positions in section_positions.items():
            self.symtab.create_local_symbol(section_name)
            entry = IonStruct(IS("$181"), positions, IS("$174"), IS(section_name))
            entries.append(entry)

        return YJFragment(fid=IS("$348"), ftype=IS("$264"), value=entries)

    def build_fragment_265(self, position_ids):
        """
        Builds Fragment $265 (Position Index Table)

        Maps character offsets to position IDs. The Kindle uses this to
        translate reading locations to content positions.

        Known-good files map $184 = character offset, $185 = position ID.
        Dense position data (one entry per ~200 characters) is required
        for the Kindle to enable the navigation pane.

        Args:
            position_ids: List of (char_offset, position_id) tuples in document order
                e.g., [(0, 1000), (200, 1002), (400, 1004), ...]
                OR list of plain position IDs (legacy, auto-indexed)

        Returns:
            YJFragment with type $265
        """
        entries = []
        for item in position_ids:
            if isinstance(item, (list, tuple)):
                char_offset, position_id = item
            else:
                # Legacy: plain list of position IDs
                char_offset = len(entries)
                position_id = item
            entry = IonStruct(IS("$184"), char_offset, IS("$185"), position_id)
            entries.append(entry)

        return YJFragment(fid=IS("$348"), ftype=IS("$265"), value=entries)

    def _build_position_data(self, chapters, section_names, ch_data):
        """
        Build position data fragments ($264, $265, $550) using Z3 pattern.

        Creates interleaved position entries in $265 with both section positions
        (from $260, range 10000+) and content positions (from $259, range 1000+).
        This ensures all position IDs referenced in $265 are properly defined.

        Args:
            chapters: List of {'title': str, 'text': str} dicts
            section_names: List of section names (e.g., ["c0", "c1", ...])
            ch_data: Dict from _build_chapter_content() with chunk data

        Returns:
            dict with keys:
                'position_entries_265': List of (char_offset, position_id) for $265
                'section_positions_264': Dict of section_name -> [position_ids] for $264
                'all_position_ids': List of all position IDs for $550
        """
        section_positions = ch_data["section_positions"]
        chunk_positions = ch_data["chunk_positions"]
        chapter_chunk_ranges = ch_data["chapter_chunk_ranges"]
        all_chunks = ch_data["all_chunks"]
        # outer_positions stays in ch_data shape for future nested-$259
        # re-enabling but is unused here in the flat-revert v5.3.2 path.

        # Build $265 entries — content positions (from $259), including
        # image entries.
        # The reference Calibre KFX file maps every $259 leaf entry into
        # $265, including image entries. Excluding image positions from
        # $265 (as v5.3.4 did) breaks navigation for image-heavy chapters
        # like a diagram-only chapter — the TOC target's surrounding storyline has
        # unmapped positions and Kindle bails out to the start of the
        # book. Give image chunks a small synthetic char-offset (+1) so
        # they get their own $265 entry without colliding with adjacent
        # text chunks.
        # Section positions ($260) are intentionally excluded — including
        # them creates "boundary markers" that cause Kindle TOC nav to
        # land one page past the target.
        entries_265_raw = []
        char_offset = 0

        for ch_idx in range(len(chapters)):
            chapter_char_start = char_offset
            start, end = chapter_chunk_ranges[ch_idx]

            chunk_offset = chapter_char_start
            for chunk_idx in range(start, end):
                chunk = all_chunks[chunk_idx]
                is_image = isinstance(chunk, dict) and chunk.get("type") == "image"
                if is_image:
                    chunk_text_len = 1  # synthetic; images take one offset slot
                elif isinstance(chunk, dict):
                    chunk_text_len = len(chunk["text"])
                else:
                    chunk_text_len = len(chunk)  # legacy: bare string
                entries_265_raw.append((chunk_offset, chunk_positions[chunk_idx]))
                chunk_offset += chunk_text_len

            char_offset = chunk_offset

        entries_265_raw.sort(key=lambda x: x[0])
        # Deduplicate entries with same $184 char-offset (rare with text-
        # only entries — only fires for adjacent zero-length chunks).
        seen_offsets = set()
        deduped = []
        for entry in entries_265_raw:
            if entry[0] not in seen_offsets:
                seen_offsets.add(entry[0])
                deduped.append(entry)
        entries_265_raw = deduped
        entries_265_raw.append(
            (char_offset, 0)
        )  # sentinel (required by Kindle nav pane)

        # Build $264 — map sections to all position IDs (section + every
        # chunk in the section, text and image alike). Image positions
        # are now in $265 too, so this is no longer the source of the
        # progress regression that v5.3.4 was guarding against.
        section_positions_264 = {}
        for ch_idx, sec_name in enumerate(section_names):
            start, end = chapter_chunk_ranges[ch_idx]
            pids = [section_positions[ch_idx]] + chunk_positions[start:end]
            section_positions_264[sec_name] = pids

        # All position IDs for $550 (section + every chunk in reading order)
        all_position_ids = list(section_positions)
        all_position_ids.extend(chunk_positions)

        return {
            "position_entries_265": entries_265_raw,
            "section_positions_264": section_positions_264,
            "all_position_ids": all_position_ids,
        }

    def build_ion_symbol_table_fragment(self):
        """
        Builds Fragment $ion_symbol_table (Document Symbol Table)

        This fragment is REQUIRED by the KFX container format. It tells
        the Kindle parser how to interpret Ion symbol IDs in the fragments.

        The symbol table includes:
        - imports: List of shared symbol tables to import (e.g., YJ_Symbols)
        - symbols: List of local symbols defined in this document
        - max_id: Total number of symbols after imports

        Returns:
            YJFragment with type $ion_symbol_table
        """
        # Use the symbol table's create_import method to generate the data
        symbol_table_data = self.symtab.create_import()

        if symbol_table_data is None:
            # Create minimal symbol table if none exists
            symbol_table_data = IonStruct(
                IS("imports"),
                [
                    IonStruct(
                        IS("name"), "YJ_Symbols", IS("version"), 15, IS("max_id"), 1098
                    )
                ],
                IS("symbols"),
                self.symtab.get_local_symbols(),
                IS("max_id"),
                len(self.symtab.symbols),
            )
            symbol_table_data = IonAnnotation(
                [IS("$ion_symbol_table")], symbol_table_data
            )

        return YJFragment(symbol_table_data)

    def build_fragment_270(self, container_id, entity_map):
        """
        Builds Fragment $270 (Container Info)

        This fragment is REQUIRED by the KFX container format. It contains:
        - Container ID (CR!...)
        - Compression and DRM settings
        - Entity map ($181) listing all fragment types and their entity IDs
        - Application version info

        Args:
            container_id: String (e.g. "CR!FEHAKUKMU05DIVSIJBZWPW44H9VH")
            entity_map: List of [ftype_id, entity_id] pairs
                       e.g., [[585, 348], [490, 348], [145, 348], ...]

        Returns:
            YJFragment with type $270
        """
        value = IonStruct(
            IS("$409"),
            container_id,  # Container ID
            IS("$412"),
            4096,  # Chunk size (default)
            IS("$410"),
            0,  # Compression type (0 = none)
            IS("$411"),
            0,  # DRM scheme (0 = none)
            IS("$587"),
            "",  # Application version
            IS("$588"),
            "",  # Package version
            IS("$161"),
            CONTAINER_FORMAT_KFX_MAIN,  # Container format
            IS("version"),
            2,  # Version
            IS("$181"),
            entity_map,  # Entity map [[ftype_id, entity_id], ...]
        )

        return YJFragment(ftype=IS("$270"), value=value)

    def build_fragment_419(self, container_id, entity_names):
        """
        Builds Fragment $419 (Entity Index / Container Map)

        Args:
            container_id: String (e.g. "CR!...")
            entity_names: List of local symbol names (e.g. ["content_1", "s0", "l0", "c0"])
                         These are looked up via the symbol table to get proper IDs.
        """
        # Use IS() with plain names so get_id does proper symbol table lookup
        entity_symbols = [IS(name) for name in entity_names]

        value = IonStruct(
            IS("$252"),
            [IonStruct(IS("$155"), container_id, IS("$181"), entity_symbols)],
            IS("$253"),
            [],
        )

        return YJFragment(fid=IS("$348"), ftype=IS("$419"), value=value)

    def build_fragment_145(self, text_strings, content_name=None):
        """
        Builds Fragment $145 (Content Strings)

        Working KFX files use a unique local symbol as the fid (e.g., "content_1"),
        NOT the shared $348 entity ID. The $145 fragment's name field ($4) must
        match the fid, and $259 entries must reference this same name.

        Args:
            text_strings: List of strings (paragraphs/text chunks)
            content_name: Local symbol name for the fid and $4 field (e.g., "content_1")

        Returns:
            YJFragment with type $145
        """
        if content_name is None:
            content_name = "content_1"
        self.symtab.create_local_symbol(content_name)

        value = IonStruct(IS("$4"), IS(content_name), IS("$146"), text_strings)

        # Use content_name as fid for a unique entity ID (NOT shared $348)
        # This matches working KFX files where $145 has fid=content_1
        return YJFragment(fid=IS(content_name), ftype=IS("$145"), value=value)

    def build_fragment_157(
        self,
        entity_name=None,
        font_size=1.0,
        line_height=1.0,
        underline=False,
        bold=False,
        margin_top=None,
        is_heading=False,
        italic=False,
        align=None,
        text_indent=None,
        margin_left=None,
        margin_right=None,
    ):
        """
        Builds Fragment $157 (Style Definition).

        Creates a text style with proper font-size, line-height, text-align,
        and display type matching the structure of known-good KFX files.

        Args:
            entity_name: Local symbol name for this fragment (e.g., "s0", "s1").
                        Auto-generated if None.
            font_size: Font size in rem units (default 1.0). When 1.0, font-size
                      is omitted to let Kindle use its default (matching reference KFX).
            line_height: Line height in lh units (default 1.0, matching reference KFX)
            underline: If True, add text-decoration: underline ($23: $328)
            bold: If True, set font-weight: bold ($13: $361)
            italic: If True, add font-style: italic ($12: $382)
            is_heading: If True, omit padding-top (headings use margin-top for spacing)
            align: Text alignment override. If a key in ALIGN_MAP ("left", "right", "center"),
                  use the mapped symbol; otherwise default to "justify" ($321).
            text_indent: If a tuple (magnitude_str, unit_symbol), set text-indent ($36)
                        and suppress padding-top. Default None uses 0% indent and
                        normal padding behavior.
            margin_left: If a tuple (magnitude_str, unit_symbol), override the default
                        margin-left ($48). Default None uses 0.5% ($314).
            margin_right: If a tuple (magnitude_str, unit_symbol), emit margin-right ($50).
                         Default None omits $50.

        Returns:
            YJFragment with type $157
        """
        if entity_name is None:
            entity_name = f"s{self.next_entity_id}"
            self.next_entity_id += 1
        self.symtab.create_local_symbol(entity_name)

        # $13 = font-weight: $350=normal, $361=bold
        font_weight = IS("$361") if bold else IS("$350")

        # $34 = text-align; default justify ($321), overridden per element.
        text_align = IS(ALIGN_MAP[align]) if align in ALIGN_MAP else IS("$321")

        # $36 = text-indent; default 0%, overridden per element.
        if text_indent is not None:
            indent_struct = IonStruct(
                IS("$307"),
                IonDecimal(text_indent[0]),
                IS("$306"),
                IS(text_indent[1]),
            )
        else:
            indent_struct = IonStruct(
                IS("$307"), IonDecimal("0"), IS("$306"), IS("$314")
            )

        # $48 = margin-left; default 0.5%, overridden per element.
        if margin_left is not None:
            margin_left_struct = IonStruct(
                IS("$307"),
                IonDecimal(margin_left[0]),
                IS("$306"),
                IS(margin_left[1]),
            )
        else:
            margin_left_struct = IonStruct(
                IS("$307"), IonDecimal("0.5"), IS("$306"), IS("$314")
            )

        value = IonStruct(
            IS("$48"),
            margin_left_struct,
            IS("$34"),
            text_align,  # text-align: justify ($320=center, $321=justify, $59=left, $61=right)
            IS("$36"),
            indent_struct,  # text-indent
            IS("$42"),
            IonStruct(
                IS("$307"), IonDecimal(str(line_height)), IS("$306"), IS("$310")
            ),  # line-height (lh)
            IS("$173"),
            IS(entity_name),  # self-reference
            IS("$13"),
            font_weight,  # font-weight
        )

        # Body text gets padding-top for paragraph spacing; headings rely on margin-top.
        # A non-zero first-line indent replaces inter-paragraph spacing (print convention),
        # so suppress padding-top when indented.
        if not is_heading and text_indent is None:
            value[IS("$47")] = IonStruct(
                IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")
            )  # padding-top: 1lh

        # Only set font-size when non-default — lets Kindle use its default for normal text
        # Reference KFX files omit font-size on most styles and only set it for headings/small text
        if font_size != 1.0:
            value[IS("$16")] = IonStruct(
                IS("$307"), IonDecimal(str(font_size)), IS("$306"), IS("$505")
            )  # rem

        # $23: $328 = text-decoration: underline
        if underline:
            value[IS("$23")] = IS("$328")

        # $12 = font-style: $382 = italic (authoritative, jhowell kfxlib)
        if italic:
            value[IS("$12")] = IS("$382")

        # $46 = margin-top (for visual separation before chapter headings)
        if margin_top is not None:
            value[IS("$46")] = IonStruct(
                IS("$307"), IonDecimal(str(margin_top)), IS("$306"), IS("$310")
            )

        # $50 = margin-right; emitted only when the source specifies it.
        if margin_right is not None:
            value[IS("$50")] = IonStruct(
                IS("$307"), IonDecimal(margin_right[0]), IS("$306"), IS(margin_right[1])
            )

        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_fragment_157_image(self, entity_name, kind="inline"):
        """Builds a $157 style for inline image entries.

        Three variants observed in Calibre KFX Output (jhowell) reference
        on the maintainer's real-book test corpus:
        - "small" (s7N8 shape): tiny square chapter ornaments / dingbats.
          $56=3em, $57=3em, em-based fixed sizing.
        - "inline" (s5J shape): wide rule-style decorations.
          $56=9.626% caps height to a small viewport fraction.
        - "page" (s4R shape): full-page images like maps and diagrams.
          $56=100% lets the image take the full available height.

        Decision is driven by the caller, who knows the image dimensions.
        """
        self.symtab.create_local_symbol(entity_name)
        if kind == "page":
            value = IonStruct(
                IS("$56"),
                IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
                IS("$785"),
                IonStruct(IS("$131"), 2, IS("$132"), 2),
                IS("$546"),
                IS("$377"),
                IS("$580"),
                IS("$320"),
                IS("$173"),
                IS(entity_name),
            )
        elif kind == "small":
            # Reference s7N8 verbatim:
            #   $56: 3em (width), $57: 3em (height) — em unit ($308)
            #   $65: 100% (max-width, % unit $314) — prevents overflow
            #   $546: $377 (center alignment)
            #   $31: -9lh (top margin pull-up, lh unit $318)
            value = IonStruct(
                IS("$56"),
                IonStruct(IS("$307"), IonDecimal("3"), IS("$306"), IS("$308")),
                IS("$65"),
                IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
                IS("$57"),
                IonStruct(IS("$307"), IonDecimal("3"), IS("$306"), IS("$308")),
                IS("$546"),
                IS("$377"),
                IS("$173"),
                IS(entity_name),
                IS("$31"),
                IonStruct(IS("$307"), IonDecimal("-9"), IS("$306"), IS("$318")),
            )
        else:  # inline (wide rule)
            value = IonStruct(
                IS("$56"),
                IonStruct(IS("$307"), IonDecimal("9.626"), IS("$306"), IS("$314")),
                IS("$65"),
                IonStruct(IS("$307"), IonDecimal("100"), IS("$306"), IS("$314")),
                IS("$785"),
                IonStruct(IS("$131"), 2, IS("$132"), 2),
                IS("$546"),
                IS("$377"),
                IS("$42"),
                IonStruct(IS("$307"), IonDecimal("1"), IS("$306"), IS("$310")),
                IS("$580"),
                IS("$320"),
                IS("$173"),
                IS(entity_name),
            )
        return YJFragment(fid=IS(entity_name), ftype=IS("$157"), value=value)

    def build_fragment_266(self, anchor_name, position_id):
        """
        Builds Fragment $266 (Anchor / Bookmark)

        Creates an internal anchor that maps a name to a position in the book.
        Used by $179 link references in $259 entries to create clickable links.

        Args:
            anchor_name: Local symbol name for this anchor (e.g., "toc_anchor_0")
            position_id: Target position ID (from $259 content positions)

        Returns:
            YJFragment with type $266
        """
        self.symtab.create_local_symbol(anchor_name)
        value = IonStruct(IS("$183"), IonStruct(IS("$155"), position_id, IS("$143"), 0))
        return YJFragment(fid=IS(anchor_name), ftype=IS("$266"), value=value)

    def build_fragment_259(
        self,
        story_names,
        content_name,
        entity_name=None,
        positions=None,
        content_index_offset=0,
        link_targets=None,
        link_styles=None,
        link_text_lengths=None,
        outer_position=None,
        outer_style=None,
        chunk_kinds=None,
        image_specs=None,
        emphasis_spans=None,
    ):
        """
        Builds Fragment $259 (Storyline / Flow Map)

        Emits a FLAT $146 list — one entry per chunk, directly under the
        storyline:

            $259.value.$146 = [
              { $155: pos[0], $157: story[0], $790: 1, ..., $145: ... },
              { $155: pos[1], $157: story[1],         ..., $145: ... },
              ...
            ]

        (A nested single-outer-wrapper shape was tried during the Phase-3 work
        but reverted; the flat shape is what ships and is device-verified. The
        `outer_position` / `outer_style` params are retained for compatibility
        but are not emitted in the flat shape.)

        Args:
            story_names: List of $157 fragment local symbol names per chunk.
            content_name: Name of the $145 content fragment.
            entity_name: Local symbol name for this fragment (e.g., "l0").
                        Auto-generated if None.
            positions: List of position IDs, one per story_name (children).
            content_index_offset: Base offset for $403 indices into $145.$146.
            link_targets: Optional list of $266 anchor names (or None) per child.
                         When set, the entry gets a $142 character-span marking
                         text [0:link_text_lengths[i]] as a hyperlink to the
                         anchor. Reference uses $142 spans (not entry-level
                         $179) — Kindle treats $142 as inline hyperlinks but
                         entry-level $179 as a non-tappable structural ref.
            link_styles: Optional list of $157 style names (or None) per child,
                        used inside the $142 span as the link's visual style
                        (typically underlined). Falls back to the entry's
                        story_name when None.
            link_text_lengths: Optional list of int (or None) per child giving
                              the character length to mark as link. Defaults
                              to a large value when None (covers the whole
                              paragraph).
            outer_position: Position ID for the outer wrapping entry. Required
                           when story_names is non-empty.
            outer_style: $157 style name for the outer entry. Defaults to the
                         first story_name when None.

        Returns:
            YJFragment with type $259
        """
        if entity_name is None:
            entity_name = f"l{self.next_entity_id}"
            self.next_entity_id += 1
        self.symtab.create_local_symbol(entity_name)
        self.symtab.create_local_symbol(content_name)

        children = []
        text_index = content_index_offset
        for i, story_name in enumerate(story_names):
            position = positions[i] if positions and i < len(positions) else 1000 + i
            kind = chunk_kinds[i] if chunk_kinds and i < len(chunk_kinds) else "text"

            if (
                kind == "image"
                and image_specs
                and i < len(image_specs)
                and image_specs[i]
            ):
                spec = image_specs[i]
                resource_name = spec.get("resource")
                alt = spec.get("alt", "")
                if resource_name:
                    self.symtab.create_local_symbol(resource_name)
                entry = IonStruct(
                    IS("$155"),
                    position,
                    IS("$157"),
                    IS(story_name),
                    IS("$159"),
                    IS("$271"),
                    IS("$584"),
                    alt,
                )
                if resource_name:
                    entry[IS("$175")] = IS(resource_name)
                # Image entries don't consume a $145 $403 slot — text_index
                # is unchanged.
                if i == 0:
                    entry[IS("$790")] = 1
                children.append(entry)
                continue

            # Text entry
            entry = IonStruct(
                IS("$155"),
                position,
                IS("$157"),
                IS(story_name),
                IS("$159"),
                IS("$269"),
                IS("$145"),
                IonStruct(IS("$4"), IS(content_name), IS("$403"), text_index),
            )
            text_index += 1
            if i == 0:
                entry[IS("$790")] = 1

            if link_targets and i < len(link_targets) and link_targets[i]:
                anchor_name = link_targets[i]
                self.symtab.create_local_symbol(anchor_name)
                # Emit $142 character span (reference's mechanism for inline
                # hyperlinks). Entry-level $179 is non-tappable — span-level
                # $179 inside $142 makes the marked text region a tappable
                # hyperlink.
                span_length = (
                    link_text_lengths[i]
                    if link_text_lengths
                    and i < len(link_text_lengths)
                    and link_text_lengths[i] is not None
                    else 1000
                )
                span_style = (
                    link_styles[i]
                    if link_styles and i < len(link_styles) and link_styles[i]
                    else story_name
                )
                self.symtab.create_local_symbol(span_style)
                span = IonStruct(
                    IS("$143"),
                    0,
                    IS("$144"),
                    span_length,
                    IS("$179"),
                    IS(anchor_name),
                    IS("$157"),
                    IS(span_style),
                )
                entry[IS("$142")] = [span]

            if emphasis_spans and i < len(emphasis_spans) and emphasis_spans[i]:
                existing = entry.get(IS("$142"), [])
                for start, length, style_name in emphasis_spans[i]:
                    self.symtab.create_local_symbol(style_name)
                    existing.append(
                        IonStruct(
                            IS("$143"),
                            start,
                            IS("$144"),
                            length,
                            IS("$157"),
                            IS(style_name),
                        )
                    )
                entry[IS("$142")] = existing

            children.append(entry)

        # FLAT shape (pre-Phase-3) — used for the TOC-regression test.
        # Whether to revert nesting permanently or fix the nested shape
        # depends on the device test outcome.
        value = IonStruct(
            IS("$176"),
            IS(entity_name),
            IS("$146"),
            children,
        )

        return YJFragment(fid=IS(entity_name), ftype=IS("$259"), value=value)

    def build_fragment_260(
        self,
        storyline_name,
        section_name=None,
        field_155_value=None,
        is_first_section=False,
    ):
        """
        Builds Fragment $260 (Section)

        CRITICAL: $260.fid MUST equal $260.$174.

        All sections use inline display ($269) with page-break-before ($326).
        Block display ($270) with fixed dimensions causes text overlap and is
        only appropriate for cover image sections.

        Args:
            storyline_name: Local symbol name of the $259 fragment (e.g., "l0")
            section_name: Local symbol name for BOTH fid AND $174 (e.g., "c0").
                         Auto-generated if None.
            field_155_value: Optional integer for Field $155 (defaults to 1800)
            is_first_section: Unused (kept for API compatibility)

        Returns:
            (YJFragment, section_name): Fragment and its fid/name for use in $538
        """
        if section_name is None:
            section_name = f"c{self.next_entity_id}"
            self.next_entity_id += 1

        if field_155_value is None:
            field_155_value = 1800

        self.symtab.create_local_symbol(section_name)
        self.symtab.create_local_symbol(storyline_name)

        # All sections use inline display ($269) without explicit page-break.
        # The $790: 1 flag on heading $259 entries handles chapter breaks.
        # Explicit $156: $326 page-break causes TOC navigation to land one
        # page late (preview shows correct page but tap goes to next page).
        entry = IonStruct(
            IS("$155"),
            field_155_value,
            IS("$176"),
            IS(storyline_name),
            IS("$159"),
            IS("$269"),
        )

        value = IonStruct(IS("$174"), IS(section_name), IS("$141"), [entry])

        return YJFragment(
            fid=IS(section_name), ftype=IS("$260"), value=value
        ), section_name

    def build_fragment_389(self, toc_entries, entity_id=None):
        """
        Builds Fragment $389 (Navigation / Table of Contents) - LEGACY

        NOTE: This is the legacy TOC structure. For better Kindle compatibility,
        use build_book_navigation() which creates Fragment $410 (book_navigation).

        Based on template generator analysis, Fragment 389 has this structure:
        - Top level: LIST (not struct!)
        - First element: Struct with Field $392
        - Field $392: List of navigation containers
        - Each container: Struct with Field $235 (type, e.g., $212 for TOC) and Field $247 (nav units)
        - Each nav unit: Struct with Field $246 (target with $155 EID) and Field $241 (label with $244 text)

        Navigation chain:
        - Fragment 389 TOC entry -> EID ($246.$155) points to Fragment 259
        - Fragment 259 maps to Fragment 157s
        - Fragment 157s reference Fragment 145 content

        Args:
            toc_entries: List of dicts with 'title', 'level', and 'target_entity_id' (Fragment 259 entity ID)
            entity_id: Optional entity ID (auto-assigned if None)

        Returns:
            YJFragment with type $389
        """
        if entity_id is None:
            entity_id = self.next_entity_id
            self.next_entity_id += 1

        # Ensure symbols exist
        self.symtab.create_local_symbol(str(entity_id))
        self.symtab.create_local_symbol("$212")  # TOC container type

        # Build navigation units
        nav_units = []
        for entry in toc_entries:
            # Target should be Fragment 259 entity ID (the storyline that contains the content)
            target_eid = entry.get(
                "target_entity_id", 348
            )  # Default to metadata if not specified
            title = entry.get("title", "Untitled")

            # Navigation unit structure:
            # - Field $246: Target struct with Field $155 (entity ID pointing to Fragment 259)
            # - Field $241: Label struct with Field $244 (display text for TOC)
            # - Field $175: Alternative title field
            nav_unit = IonStruct(
                IS("$246"),
                IonStruct(
                    IS("$155"),
                    target_eid,  # Points to Fragment 259 entity ID
                ),
                IS("$241"),
                IonStruct(
                    IS("$244"),
                    title,  # Display text for TOC entry
                ),
                IS("$175"),
                title,  # Alternative title field
            )
            nav_units.append(nav_unit)

        # Navigation container structure:
        # - Field $235: Container type ($212 = TOC/NCX)
        # - Field $247: List of navigation units
        nav_container = IonStruct(
            IS("$235"),
            IS("$212"),  # TOC container type
            IS("$247"),
            nav_units,
        )

        # Top-level structure:
        # - Field $392: List of navigation containers
        nav_struct = IonStruct(IS("$392"), [nav_container])

        # CRITICAL: Fragment 389 value must be a LIST, not a struct!
        # The list contains one struct with Field $392
        value = [nav_struct]

        return YJFragment(fid=IS(f"${entity_id}"), ftype=IS("$389"), value=value)

    def build_book_navigation(self, toc_entries, landmarks=None):
        """
        Builds Fragment $410 (book_navigation) - RECOMMENDED TOC STRUCTURE

        Based on KFX reverse engineering from Kindle Previewer:

        Structure:
        $book_navigation::{
          nav_containers: [
            $nav_container::{
              nav_container_name: "toc",
              nav_type: $toc,
              entries: [
                $nav_unit::{
                  nav_unit_name: "chapter_1",
                  representation: "Chapter 1",
                  target_position: { position: <position_id> }
                },
                ...
              ]
            }
          ]
        }

        Symbol Reference:
        - $410 = book_navigation (ftype)
        - $413 = nav_containers (field)
        - $256 = nav_type (field)
        - $260 = nav_container_name (field)
        - $268 = entries (field)
        - $261 = nav_unit_name (field)
        - $262 = representation (field)
        - $267 = target_position (field)
        - $233 = toc (nav_type value)

        Uses fid=$348 (single fragment marker) to avoid FID registration issues.

        Args:
            toc_entries: List of dicts with:
                - 'title': Display text for TOC entry
                - 'position': Position ID (typically Fragment 259 entity ID or position reference)
                - 'name': Optional unique identifier (auto-generated if not provided)
                - 'children': Optional nested entries (for hierarchical TOC)
            landmarks: Optional list of landmark dicts with:
                - 'title': Display text
                - 'position': Position ID
                - 'landmark_type': Type symbol (e.g., '$bodymatter', '$cover_page')

        Returns:
            YJFragment with type $410 (book_navigation)
        """
        # Register required symbols
        self.symtab.create_local_symbol("position")
        self.symtab.create_local_symbol("toc")

        # Build TOC navigation units
        toc_nav_units = []
        for i, entry in enumerate(toc_entries):
            title = entry.get("title", f"Chapter {i + 1}")
            position = entry.get(
                "position", entry.get("target_entity_id", 1800 + i * 10)
            )
            name = entry.get("name", f"toc_entry_{i}")

            # Build nav_unit structure
            # $261 = nav_unit_name
            # $262 = representation
            # $267 = target_position
            nav_unit = IonStruct(
                IS("$261"),
                name,  # nav_unit_name
                IS("$262"),
                title,  # representation (display text)
                IS("$267"),
                IonStruct(  # target_position
                    IS("position"), position
                ),
            )

            # Handle nested children for hierarchical TOC
            children = entry.get("children", [])
            if children:
                child_units = []
                for j, child in enumerate(children):
                    child_title = child.get("title", f"Section {j + 1}")
                    child_position = child.get(
                        "position", child.get("target_entity_id", position + j + 1)
                    )
                    child_name = child.get("name", f"{name}_sub_{j}")

                    child_unit = IonStruct(
                        IS("$261"),
                        child_name,
                        IS("$262"),
                        child_title,
                        IS("$267"),
                        IonStruct(IS("position"), child_position),
                    )
                    child_units.append(child_unit)

                # $268 = entries (for nested children)
                nav_unit[IS("$268")] = child_units

            toc_nav_units.append(nav_unit)

        # Build TOC nav_container
        # $260 = nav_container_name
        # $256 = nav_type
        # $268 = entries
        # $233 = toc (symbol for nav_type value)
        toc_container = IonStruct(
            IS("$260"),
            "toc",  # nav_container_name
            IS("$256"),
            IS("$233"),  # nav_type = $toc
            IS("$268"),
            toc_nav_units,  # entries
        )

        nav_containers = [toc_container]

        # Add landmarks container if provided
        # Note: Landmarks use string values for landmark_type to avoid symbol registration issues
        if landmarks:
            landmark_units = []
            for i, lm in enumerate(landmarks):
                lm_title = lm.get("title", f"Landmark {i + 1}")
                lm_position = lm.get("position", 1000 + i)
                lm_name = lm.get("name", f"landmark_{i}")
                lm_type = lm.get("landmark_type", "bodymatter")

                # Remove $ prefix if present (use string value)
                if lm_type.startswith("$"):
                    lm_type = lm_type[1:]

                # $259 = landmark_type field (use string value)
                lm_unit = IonStruct(
                    IS("$261"),
                    lm_name,
                    IS("$262"),
                    lm_title,
                    IS("$267"),
                    IonStruct(IS("position"), lm_position),
                    IS("$259"),
                    lm_type,  # landmark_type as string
                )
                landmark_units.append(lm_unit)

            # $257 = landmarks (nav_type value)
            landmarks_container = IonStruct(
                IS("$260"),
                "landmarks",
                IS("$256"),
                IS("$257"),  # nav_type = $landmarks
                IS("$268"),
                landmark_units,
            )
            nav_containers.append(landmarks_container)

        # Build book_navigation value
        # $413 = nav_containers
        book_nav_value = IonStruct(IS("$413"), nav_containers)

        # CRITICAL: Use fid=$348 (single fragment marker) to avoid FID registration issues
        # This avoids the symbol table max_id mismatch that was causing $0 serialization
        return YJFragment(fid=IS("$348"), ftype=IS("$410"), value=book_nav_value)

    def generate_full_book(
        self,
        title,
        author,
        chapters,
        asin=None,
        output_path=None,
        cover_image=None,
        language="en",
        publisher="kfxgen",
        issue_date=None,
        images=None,
    ):
        """
        Generates a complete KFX book with metadata and content.

        Creates per-chapter sections matching known-good KFX structure:
        each chapter gets its own $259 storyline and $260 section.

        Args:
            title: Book title
            author: Book author
            chapters: Required list of chapter dicts with 'title' and 'text'
                keys (text may contain inline image tokens emitted by
                converter.extract_text_from_html). Optionally each chapter
                may carry 'font_size' and 'toc_links'. Raises ValueError
                if empty or None.
            asin: Optional ASIN (auto-generated if None)
            output_path: Optional path to write KFX file
            cover_image: Optional bytes of cover image (JPEG or PNG)
            language: Two-letter ISO language code (default 'en')
            publisher: Publisher string for $490 metadata
            issue_date: Optional ISO date string
            images: Optional dict of {href: raw bytes} for body images.
                Each gets a $164 manifest + $417 blob pair; <img> tokens
                in chapter text resolve against this dict by basename.

        Returns:
            bytes: Serialized KFX data
        """
        # Container ID and ASIN are derived deterministically from book
        # identity rather than randomly generated (#89). Two consecutive
        # conversions of the same book now produce byte-identical output,
        # which:
        #   1. Lets `tier3_strict` golden-file tests verify bit-level
        #      stability of the generator.
        #   2. Causes re-conversions of the same EPUB to overwrite (rather
        #      than duplicate) on the Kindle library, which is the
        #      expected UX for "I tweaked the plugin and re-converted."
        # SHA-256 hex digest is in [0-9a-f]; uppercased it becomes
        # [0-9A-F], all alphanumeric, matching the format the random
        # versions produced. The seed includes (title, author, language,
        # publisher, issue_date, len(chapters)) — enough to distinguish
        # most books in practice. Known limitation: two distinct editions
        # of the same book with the same metadata and same chapter count
        # but different per-chapter content will collide. Callers that
        # need stronger uniqueness should pass an explicit `asin`.
        _id_seed = "\x00".join(
            [
                str(title),
                str(author),
                str(language),
                str(publisher),
                str(issue_date or ""),
                str(len(chapters)),
            ]
        ).encode("utf-8")
        _id_digest = hashlib.sha256(_id_seed).hexdigest().upper()

        # Container ID: "CR!" prefix + 28-char alphanumeric tail. Reference
        # Calibre KFX uses this exact shape.
        container_id = "CR!" + _id_digest[:28]

        if asin is None:
            # 32-char uppercase alphanumeric (no prefix). Reference Calibre
            # KFX uses this format (e.g. 'NRHEO70SKAG5UVR2SSWENJ42365REDE2').
            # The previous `ASIN_<10>` short prefix format inhibited Kindle
            # home-screen thumbnail extraction (#39, verified on Paperwhite).
            asin = _id_digest[:32]

        # Reset state for clean generation
        self.fragments = []
        self.symtab = StandardSymbolTable()
        self.entity_ids = {}
        self.next_entity_id = 349
        self.field_403_counter = 10

        # 1. Build metadata fragments
        self.fragments.append(self.build_fragment_585())

        # Detect cover image format and build resource fragments
        # $164 (metadata) and $417 (raw data) MUST have different fids, linked by $165
        # $165 must be a plain STRING (not IonSymbol) — Kindle requires this
        cover_resource_name = None
        cover_location_name = None
        cover_dims = None
        if cover_image:
            cover_resource_name = "cover_img"
            cover_location_name = "resource/cover_img"
            if cover_image[:3] == b"\xff\xd8\xff":
                img_format = "jpeg"
            elif cover_image[:4] == b"\x89PNG":
                img_format = "png"
            else:
                # Defense in depth: extract_cover_image validates magic bytes
                # before this code is reached (#46). If we still arrive here
                # with garbage, refuse rather than mislabel as JPEG.
                _security_log.warning(
                    "rejected cover image with unrecognized magic bytes: %r",
                    cover_image[:8],
                )
                raise ValueError(
                    "cover_image bytes are neither JPEG nor PNG (magic %s)"
                    % cover_image[:4].hex()
                )

            # Detect image dimensions
            width, height = self._detect_image_dimensions(cover_image)
            cover_dims = (width, height)

            # Cover gets $162 MIME type — enables Kindle home-screen
            # thumbnail extraction (#39, verified on Paperwhite). Body
            # images stay without $162.
            self.fragments.append(
                self.build_fragment_164(
                    cover_resource_name,
                    cover_location_name,
                    img_format,
                    width=width,
                    height=height,
                    include_mime=True,
                )
            )
            self.fragments.append(
                self.build_fragment_417(cover_location_name, cover_image)
            )

        # Body images: emit one $164 + $417 pair per spine-referenced image.
        # image_resources maps href -> (resource_name, location_name) so the
        # img-rewriting step (Phase 4 step B/C) can resolve <img src="href"> to
        # the right $164 fid for $259 image entries.
        # image_resources is indexed by basename so <img src="../path/x.jpg">
        # in spine XHTML resolves against manifest href "OEBPS/path/x.jpg".
        def _img_basename(h):
            if not h:
                return ""
            h = h.split("#", 1)[0]
            return h.rsplit("/", 1)[-1]

        image_resources = {}
        self._image_dims = {}  # basename -> (width, height) for style picker
        if images:
            # Filter to recognized formats first, then enumerate so resource
            # names stay contiguous (img_0, img_1, ...) regardless of skips.
            # Unsupported formats are dropped silently here; the converter
            # is responsible for pre-filtering or logging.
            valid = []
            for href, data in images.items():
                if not data or len(data) <= 100:
                    continue
                if data[:3] == b"\xff\xd8\xff":
                    fmt = "jpeg"
                elif data[:4] == b"\x89PNG":
                    fmt = "png"
                else:
                    continue
                valid.append((href, data, fmt))

            for idx, (href, data, fmt) in enumerate(valid):
                resource_name = f"img_{idx}"
                location_name = f"resource/img_{idx}"
                width, height = self._detect_image_dimensions(data)
                self.fragments.append(
                    self.build_fragment_164(
                        resource_name, location_name, fmt, width=width, height=height
                    )
                )
                self.fragments.append(self.build_fragment_417(location_name, data))
                base = _img_basename(href)
                # Basename collision (e.g. images/foo.jpg + ext/foo.jpg): keep
                # the first entry so existing <img src> resolutions stay
                # stable. The collision is logged once below.
                if base not in image_resources:
                    image_resources[base] = (resource_name, location_name)
                    self._image_dims[base] = (width, height)
                else:
                    self._image_basename_collisions = getattr(
                        self, "_image_basename_collisions", []
                    ) + [base]

        # Cover-in-reading-flow (#32): make the cover image visible as the
        # first reading page by registering its resource under a synthetic
        # basename and prepending a cover chapter. The cover chapter has no
        # heading text, no TOC entry, and a single image entry referencing
        # the cover resource via the standard image-token mechanism.
        _cover_basename = "__kfxgen_cover__"
        if cover_resource_name and chapters:
            image_resources[_cover_basename] = (
                cover_resource_name,
                cover_location_name,
            )
            if cover_dims:
                self._image_dims[_cover_basename] = cover_dims

        # Expose to _build_chapter_content for img-token rewriting (Phase 4b).
        self.image_resources = image_resources

        self.fragments.append(
            self.build_fragment_490(
                title,
                author,
                asin,
                container_id,
                cover_image=cover_resource_name,
                language=language,
                issue_date=issue_date,
                publisher=publisher,
            )
        )

        # 2. Build content fragments ($145, $157, $259). The legacy
        # `content_text=`/`toc_entries=` API was removed in 5.3.1 — callers
        # must pass structured `chapters` (the production converter has
        # always done so via extract_chapters_from_oeb).
        if not chapters:
            raise ValueError("generate_full_book requires a non-empty chapters list")

        # Prepend synthetic cover chapter (#32). All toc_link target indices
        # in subsequent chapters shift by +1 to account for the new chapter
        # at index 0.
        if cover_resource_name:
            chapters = [
                {
                    **ch,
                    "toc_links": [
                        {**link, "target_chapter_idx": link["target_chapter_idx"] + 1}
                        for link in (ch.get("toc_links") or [])
                    ],
                }
                if ch.get("toc_links")
                else ch
                for ch in chapters
            ]
            cover_chunk = f"\x00IMG\x01{_cover_basename}\x01\x00"
            cover_chapter = {
                "title": "",
                "text": cover_chunk,
                "_omit_from_toc": True,
                "_is_cover": True,
            }
            chapters = [cover_chapter] + chapters

        ch_data = self._build_chapter_content(chapters)
        story_names = ch_data["story_names"]
        storyline_names = ch_data["storyline_names"]
        content_names = ch_data["content_names"]
        section_positions = ch_data["section_positions"]
        toc_positions = ch_data["chapter_start_positions"]

        # 3. Build one $260 section per chapter/storyline
        # Known-good: first section uses block display with dimensions,
        # subsequent sections use minimal inline display
        section_names = []
        for i, sl_name in enumerate(storyline_names):
            sec_name = f"c{i}"
            frag_260, sec_name = self.build_fragment_260(
                sl_name,
                section_name=sec_name,
                field_155_value=section_positions[i],
                is_first_section=(i == 0),
            )
            section_names.append(sec_name)
            self.fragments.append(frag_260)

        # 4. Build position data (required for Kindle nav pane)
        pos_data = self._build_position_data(chapters, section_names, ch_data)

        # 5. Build Fragment $538 (Document Data / Reading Order)
        # Lists ALL sections in reading order
        self.fragments.append(self.build_fragment_538(section_names))

        # 6. Build Fragment $389 (Navigation / TOC). Synthetic chapters
        # marked with `_omit_from_toc` (e.g. the cover-in-reading-flow
        # chapter) are excluded so they don't appear in the nav-pane.
        nav_entries = [
            {"title": ch["title"], "position": pos}
            for ch, pos in zip(chapters, toc_positions)
            if not ch.get("_omit_from_toc")
        ]
        self.fragments.append(self.build_fragment_389_toc(nav_entries))

        # 7. Build Fragment $258 (Reading Order Metadata)
        # Lists ALL sections in reading order (same as $538)
        self.fragments.append(self.build_fragment_258(section_names))

        # 8. Build Fragment $395 (empty nav units)
        self.fragments.append(
            YJFragment(
                fid=IS("$348"), ftype=IS("$395"), value=IonStruct(IS("$247"), [])
            )
        )

        # 9. Build Fragment $264 (Position Index)
        self.fragments.append(
            self.build_fragment_264(pos_data["section_positions_264"])
        )

        # 10. Build Fragment $265 (Position Index Table)
        self.fragments.append(self.build_fragment_265(pos_data["position_entries_265"]))

        # 11. Build Fragment $593 (Format Capabilities)
        # CRITICAL: Container-level fragments must NOT have fid — only ftype.
        # Using both fid+ftype creates double annotation ($348::$593::) that
        # breaks Kindle parsing of the format_capabilities blob.
        self.symtab.create_local_symbol("kfxgen.textBlock")
        self.symtab.create_local_symbol("version")
        self.fragments.append(
            YJFragment(
                ftype=IS("$593"),
                value=[IonStruct(IS("$492"), "kfxgen.textBlock", IS("version"), 1)],
            )
        )

        # 12. Build Fragment $597 (Section Annotation) - one per section
        self.symtab.create_local_symbol("IS_TARGET_SECTION")
        section_ad_names = []
        for sec_name in section_names:
            ad_name = sec_name + "-ad"
            self.symtab.create_local_symbol(ad_name)
            section_ad_names.append(ad_name)
            self.fragments.append(
                YJFragment(
                    fid=IS(ad_name),
                    ftype=IS("$597"),
                    value=IonStruct(
                        IS("$598"),
                        IS(ad_name),
                        IS("$258"),
                        [IonStruct(IS("$492"), "IS_TARGET_SECTION", IS("$307"), True)],
                    ),
                )
            )

        # 13. Build Fragment $550 (Page Break Positions)
        page_positions = [
            IonStruct(IS("$155"), p, IS("$143"), 0)
            for p in pos_data["all_position_ids"]
        ]
        self.fragments.append(
            YJFragment(
                fid=IS("$348"),
                ftype=IS("$550"),
                value=[IonStruct(IS("$182"), page_positions)],
            )
        )

        # 14. Build Fragment $419 (Entity Index)
        # Collect extra entity names from chapter content (headings, links, anchors)
        anchor_names = ch_data.get("anchor_names", [])
        extra_style_names = ch_data.get("extra_style_names", [])

        all_entity_names = (
            list(content_names)
            + storyline_names
            + section_names
            + story_names
            + extra_style_names
            + anchor_names
            + section_ad_names
        )
        if cover_resource_name:
            all_entity_names.append(cover_resource_name)
            all_entity_names.append(cover_location_name)
        for resource_name, location_name in image_resources.values():
            all_entity_names.append(resource_name)
            all_entity_names.append(location_name)
        self.fragments.append(self.build_fragment_419(container_id, all_entity_names))

        # 15. Build $270 container info fragment (REQUIRED)
        def get_id(name):
            return self.symtab.get_id(IS(name))

        entity_map = [
            [585, 348],
            [490, 348],
            [258, 348],
            [538, 348],
            [389, 348],
        ]
        for cname in content_names:
            entity_map.append([145, get_id(cname)])
        for sname in story_names:
            entity_map.append([157, get_id(sname)])
        for sname in extra_style_names:
            entity_map.append([157, get_id(sname)])
        for sl_name in storyline_names:
            entity_map.append([259, get_id(sl_name)])
        for sec_name in section_names:
            entity_map.append([260, get_id(sec_name)])
        for anchor_name in anchor_names:
            entity_map.append([266, get_id(anchor_name)])
        entity_map.extend(
            [
                [264, 348],
                [265, 348],
                [550, 348],
            ]
        )
        for ad_name in section_ad_names:
            entity_map.append([597, get_id(ad_name)])
        if cover_resource_name:
            entity_map.append([164, get_id(cover_resource_name)])
            entity_map.append([417, get_id(cover_location_name)])
        for resource_name, location_name in image_resources.values():
            entity_map.append([164, get_id(resource_name)])
            entity_map.append([417, get_id(location_name)])
        entity_map.extend(
            [
                [395, 348],
                [419, 348],
            ]
        )
        self.fragments.append(self.build_fragment_270(container_id, entity_map))

        # 16. Build $ion_symbol_table fragment (REQUIRED for Kindle)
        # Must be added last so all symbols used by other fragments are registered
        self.fragments.append(self.build_ion_symbol_table_fragment())

        # 17. Sort fragments to match PREFERED_FRAGMENT_TYPE_ORDER
        # Known-good KFX files have fragments sorted by type then by fid.
        # The Kindle may depend on this ordering.
        sorted_frags = YJFragmentList()
        for f in sorted(self.fragments, key=lambda f: f.annotations.sort_key()):
            sorted_frags.append(f)
        self.fragments = sorted_frags

        # 18. Create and serialize container
        container = KfxContainer(self.symtab)
        container.fragments = self.fragments

        data = container.serialize()

        if output_path:
            _safe_write_bytes(output_path, data)

        return data

    # Position data constants (Z3 pattern).
    #
    # Content positions ($259 outer + chunk children) start at
    # CONTENT_POS_BASE and grow by CONTENT_POS_STEP. Section positions
    # ($260) start at SECTION_POS_BASE and grow by SECTION_POS_STEP.
    #
    # Empirical position-id facts learned the hard way (v5.3.0–v5.3.2,
    # see CHANGELOG):
    #
    #   - The two ranges may arithmetically overlap. v5.2.0 produced ~71
    #     position values shared between content and section ranges on
    #     the test corpus and rendered correctly on Kindle. EIDs in $259
    #     entries and $260 sections are resolved by context (which
    #     fragment they live in), not by global uniqueness, so position
    #     duplicates don't break nav.
    #   - Despite (a), the values themselves matter. Pushing
    #     SECTION_POS_BASE up to 100000 broke Kindle's progress display
    #     ("at start of book" showed 100% complete). The 5-digit range
    #     v5.2.0 used (≤ 16000-ish for content, 10000+ for sections) is
    #     the known-good envelope. Don't widen.
    #   - There is no need to assert a content-position ceiling. An
    #     earlier attempt (#19, removed in v5.3.2) added a ValueError
    #     when content_pos_id climbed into the section range. That
    #     guard fired on a perfectly-rendering v5.2.0-style book, so it
    #     was wrong. The actual operative limit is "stay in the 5-digit
    #     range Kindle has been observed to handle"; future code that
    #     needs more positions should investigate Kindle's actual limit
    #     on a real device, not assume one based on the constants.
    CHUNK_SIZE = 2000  # chars per content chunk in $145.$146
    CONTENT_POS_BASE = 1000  # Position ID base for $259 entries
    CONTENT_POS_STEP = 2  # Position ID step for $259 entries
    SECTION_POS_BASE = 10000  # Position ID base for $260 sections (v5.2.0 known-good)
    SECTION_POS_STEP = 2  # Position ID step for $260 sections

    @staticmethod
    def _detect_image_dimensions(image_data):
        """Detect width and height from JPEG or PNG image data."""
        if image_data[:3] == b"\xff\xd8\xff":  # JPEG
            # Scan for SOF markers (0xFFC0-0xFFC3) which contain dimensions.
            # Must handle: 0xFF padding bytes, parameterless markers (RST, SOI, EOI),
            # and APP/COM segments with length fields that may contain embedded images.
            i = 2
            while i < len(image_data) - 1:
                # Skip any 0xFF padding bytes
                while (
                    i < len(image_data) - 1
                    and image_data[i] == 0xFF
                    and image_data[i + 1] == 0xFF
                ):
                    i += 1
                if i >= len(image_data) - 1 or image_data[i] != 0xFF:
                    break
                marker = image_data[i + 1]
                if marker == 0x00 or marker == 0xFF:
                    # Stuffed byte or more padding
                    i += 2
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    # SOF marker — dimensions at offset +5/+7
                    if i + 9 <= len(image_data):
                        height = (image_data[i + 5] << 8) | image_data[i + 6]
                        width = (image_data[i + 7] << 8) | image_data[i + 8]
                        return width, height
                    break
                if marker == 0xD9:
                    break  # EOI — end of image
                if 0xD0 <= marker <= 0xD8:
                    # Parameterless markers (RST0-RST7, SOI) — no length field
                    i += 2
                    continue
                # All other markers have a 2-byte length field
                if i + 4 > len(image_data):
                    break
                length = (image_data[i + 2] << 8) | image_data[i + 3]
                if length < 2:
                    break
                i += 2 + length
        elif image_data[:4] == b"\x89PNG":  # PNG
            if len(image_data) > 24:
                width = int.from_bytes(image_data[16:20], "big")
                height = int.from_bytes(image_data[20:24], "big")
                return width, height
        return None, None

    # Heading style: font size multiplier relative to chapter body font size
    # 1.2 keeps headings distinct but readable on small Kindle screens
    HEADING_FONT_SIZE = 1.2

    def _build_chapter_content(self, chapters):
        """
        Build content fragments for multi-chapter books.

        Uses the Z3 pattern for working TOC navigation:
        - Content split into ~2000 char chunks stored in $145.$146
        - Multi-entry $259 storylines (one entry per chunk, not one per chapter)
        - Separate position ID ranges: content (1000+) for $259, section (10000+) for $260
        - All position IDs properly defined in $265

        Each chapter's first chunk is its title, styled with a bold/larger heading.
        The title is stripped from the body text start to avoid duplication.

        Chapters with 'toc_links' get special handling:
        - Each link entry becomes its own chunk (not ~2000 char split)
        - Link entries use underlined style and $179 references to $266 anchors

        Args:
            chapters: List of {'title': str, 'text': str} dicts.
                     Chapters may optionally have 'toc_links': list of
                     {'text': str, 'target_chapter_idx': int} for in-book links.

        Returns:
            dict with keys:
                story_names: List of $157 body style names
                storyline_names: List of $259 names
                content_name: Name of shared $145 fragment
                section_positions: List of $260 position IDs (one per chapter)
                chapter_start_positions: List of content position IDs at chapter starts (for TOC)
                chunk_positions: List of content position IDs (one per chunk)
                chapter_chunk_ranges: List of (start_idx, end_idx) per chapter
                all_chunks: List of chunk text strings
                anchor_names: List of $266 anchor entity names (for entity map/index)
                extra_style_names: List of additional $157 style names (headings, links)
        """
        # Per-chapter $145 fragments (#2): each chapter owns its own
        # content fragment instead of all chapters sharing a singleton
        # content_1. Reference Calibre KFX emits 149 $145 fragments on
        # the test corpus; the singleton walked a 1.3MB array on every nav
        # lookup. Numbering matches reference (1-based content_1...N).
        content_names = [f"content_{i + 1}" for i in range(len(chapters))]

        # Phase 4b: chunks are typed dicts so a chapter's reading flow can
        # interleave text paragraphs and inline image entries.
        # Each chunk is either:
        #   {'type': 'text',  'text': '<paragraph>'}
        #   {'type': 'image', 'resource': 'img_<i>', 'alt': '<alt>'}
        # Token shape (IMG_TOKEN_RE) is defined once in _img_tokens and shared
        # with converter so both layers agree on what an image token is.
        image_resources = getattr(self, "image_resources", {}) or {}

        all_chunks = []
        chapter_chunk_ranges = []
        heading_chunk_indices = set()
        toc_link_chunks = {}

        def _emit_text_chunks(para_text):
            """Yield typed chunks from a paragraph that may contain image tokens."""
            chunks = []
            last = 0
            for m in IMG_TOKEN_RE.finditer(para_text):
                if m.start() > last:
                    seg = para_text[last : m.start()].strip()
                    if seg:
                        chunks.append({"type": "text", "text": seg})
                href = m.group(1)
                alt = m.group(2).replace("\x02", " ")
                # Match <img src> values (often relative) against manifest
                # hrefs by basename — see _img_basename helper above.
                basename = href.split("#", 1)[0].rsplit("/", 1)[-1] if href else ""
                resource = image_resources.get(basename)
                if resource is not None:
                    chunks.append(
                        {"type": "image", "resource": resource[0], "alt": alt}
                    )
                # Unknown href: drop the token silently
                last = m.end()
            if last < len(para_text):
                seg = para_text[last:].strip()
                if seg:
                    chunks.append({"type": "text", "text": seg})
            return chunks

        def _split_long_text(text):
            if len(text) <= self.CHUNK_SIZE:
                return [{"type": "text", "text": text}]
            chunks = []
            pos = 0
            while pos < len(text):
                chunks.append(
                    {"type": "text", "text": text[pos : pos + self.CHUNK_SIZE]}
                )
                pos += self.CHUNK_SIZE
            return chunks

        def _append_text_with_spans(chunk_text, para_text, para_spans, block_style):
            """Split chunk_text by CHUNK_SIZE and attach the slice of
            para_spans covering each piece, offsets rebased to the piece.
            The chunk_text is a (stripped) substring of para_text; find its
            offset once, then translate spans.

            The CHUNK_SIZE loop below is NOT redundant: chunk_text is a whole
            paragraph segment from _emit_text_chunks (split only on image
            tokens, not length), so a long paragraph arrives > CHUNK_SIZE and
            must be split here — this absorbs the old _split_long_text path.
            """
            # find() returns the FIRST occurrence. In the rare case of two
            # identical text segments around an inline image in one paragraph,
            # the second segment's spans could rebase to the first's offset.
            # Low impact (needs emphasis + duplicate segment text + inline img);
            # tracked in #21. Defensive base=0 means emphasis just won't apply
            # to a chunk whose text can't be located.
            base = para_text.find(chunk_text)
            if base < 0:
                base = 0
            pos = 0
            while pos < len(chunk_text):
                piece = chunk_text[pos : pos + self.CHUNK_SIZE]
                p_start = base + pos
                p_end = p_start + len(piece)
                pspans = []
                for s, length, flags in para_spans:
                    a = max(s, p_start)
                    b = min(s + length, p_end)
                    if b > a:
                        pspans.append((a - p_start, b - a, flags))
                all_chunks.append(
                    {
                        "type": "text",
                        "text": piece,
                        "spans": pspans,
                        "block_style": block_style,
                    }
                )
                pos += self.CHUNK_SIZE

        for ch_idx, chapter in enumerate(chapters):
            start_idx = len(all_chunks)
            title = chapter["title"]
            toc_links = chapter.get("toc_links")

            # Decide whether to emit the title as a heading text chunk.
            # Skip it when:
            # - `_is_cover`: synthetic cover chapter (#32), no title text.
            # - `_omit_title_heading`: caller-set flag (e.g. Title Page
            #   replaced with title+author body — heading would be
            #   redundant) (#33).
            # - The body (after title-prefix stripping) contains image
            #   tokens but no real text — for image-only chapters
            #   (map pages, diagram pages), the title heading appears
            #   as awkward standalone text on top of the image (#33).
            body_text = chapter.get("text", "") or ""
            stripped = body_text.lstrip()
            if stripped[: len(title)].lower() == title.lower():
                body_after_title = stripped[len(title) :].lstrip()
            else:
                body_after_title = stripped
            text_after_imgs = IMG_TOKEN_RE.sub("", body_after_title).strip()
            body_is_image_only = (
                bool(body_after_title.strip()) and not text_after_imgs and not toc_links
            )
            omit_heading = (
                chapter.get("_is_cover")
                or chapter.get("_omit_title_heading")
                or body_is_image_only
            )
            if not omit_heading:
                # First chunk: chapter title (heading)
                heading_chunk_indices.add(len(all_chunks))
                all_chunks.append({"type": "text", "text": title})

            if toc_links:
                for link in toc_links:
                    chunk_idx = len(all_chunks)
                    all_chunks.append({"type": "text", "text": link["text"]})
                    toc_link_chunks[chunk_idx] = link["target_chapter_idx"]
            else:
                text = chapter["text"]
                stripped = text.lstrip()
                if stripped[: len(title)].lower() == title.lower():
                    text = stripped[len(title) :].lstrip()
                if text:
                    blocks = chapter.get("blocks")
                    if blocks is not None:
                        iter_blocks = list(blocks)
                        if iter_blocks:
                            first = iter_blocks[0]
                            first_stripped = first["text"].lstrip()
                            if first_stripped[: len(title)].lower() == title.lower():
                                remainder = first_stripped[len(title) :].lstrip()
                                removed = len(first["text"]) - len(remainder)
                                rebased_spans = []
                                for s, length, flags in first.get("spans", []):
                                    new_s = s - removed
                                    new_end = s + length - removed
                                    start = max(new_s, 0)
                                    end = min(new_end, len(remainder))
                                    if end > start:
                                        rebased_spans.append(
                                            (start, end - start, flags)
                                        )
                                if remainder:
                                    # Copy-and-override so any other keys on the
                                    # block dict (e.g. block_style, future keys)
                                    # are forwarded, not silently dropped.
                                    iter_blocks[0] = {
                                        **first,
                                        "text": remainder,
                                        "spans": rebased_spans,
                                    }
                                else:
                                    iter_blocks = iter_blocks[1:]
                        para_iter = iter_blocks
                    else:
                        para_iter = [
                            {"text": p, "spans": []} for p in text.split("\n\n")
                        ]

                    for block in para_iter:
                        para = block["text"].strip()
                        if not para:
                            continue
                        para_spans = block.get("spans", [])
                        block_style = block.get("block_style")
                        for chunk in _emit_text_chunks(para):
                            if chunk["type"] == "image":
                                all_chunks.append(chunk)
                            else:
                                _append_text_with_spans(
                                    chunk["text"], para, para_spans, block_style
                                )

            # Guarantee every chapter contributes at least one chunk so it
            # owns a navigable content position and the per-chapter arrays
            # stay aligned. A chapter emits zero chunks when its only body
            # is an <img> whose href doesn't resolve to a known body
            # resource — e.g. a recovered cover.xhtml orphan whose image is
            # the separately-handled cover (#32). Without a placeholder, a
            # trailing empty chapter made chapter_start_positions index
            # chunk_positions out of range (IndexError), and a middle empty
            # chapter silently pointed its TOC entry at the next chapter.
            if len(all_chunks) == start_idx:
                all_chunks.append({"type": "text", "text": " "})

            chapter_chunk_ranges.append((start_idx, len(all_chunks)))

        # Position IDs for the nested $259 structure: each chapter gets one
        # outer position followed by N child positions (one per chunk). All
        # in the content range, all unique, no overlap with the section range.
        chunk_positions = [None] * len(all_chunks)
        outer_positions = []
        content_pos_id = self.CONTENT_POS_BASE
        for ch_idx, (start, end) in enumerate(chapter_chunk_ranges):
            outer_positions.append(content_pos_id)
            content_pos_id += self.CONTENT_POS_STEP
            for chunk_idx in range(start, end):
                chunk_positions[chunk_idx] = content_pos_id
                content_pos_id += self.CONTENT_POS_STEP

        # Section position IDs (for $260) — separate range, may arithmetically
        # collide with chunk positions for busy books (v5.2.0 had ~71 such
        # collisions on the test corpus and worked correctly).
        section_positions = [
            self.SECTION_POS_BASE + i * self.SECTION_POS_STEP
            for i in range(len(chapters))
        ]

        # Chapter start positions for TOC entries. These MUST point to a
        # leaf $259 child with a $145 content reference and (ideally) a
        # $790:1 section marker — i.e. the heading entry, which is the
        # first child of the outer wrapper. Kindle treats outer wrapper
        # positions as non-navigable (no $145 ref, no $790:1), so TOC
        # entries that target wrappers behave as no-ops on tap.
        chapter_start_positions = [
            chunk_positions[chapter_chunk_ranges[i][0]] for i in range(len(chapters))
        ]

        # Build $266 anchor fragments for TOC link entries
        anchor_names = []
        chunk_anchor_map = {}
        for chunk_idx, target_ch_idx in toc_link_chunks.items():
            anchor_name = f"toc_anchor_{len(anchor_names)}"
            if target_ch_idx < len(chapter_start_positions):
                target_pos = chapter_start_positions[target_ch_idx]
            else:
                target_pos = chapter_start_positions[0]
            self.fragments.append(self.build_fragment_266(anchor_name, target_pos))
            chunk_anchor_map[chunk_idx] = anchor_name
            anchor_names.append(anchor_name)

        # Build one $145 fragment per chapter from its chunk-range slice.
        # Image chunks are filtered out — $145 holds only text paragraphs;
        # images are referenced from $259 via $175 against $164 resources.
        for ch_idx, (start, end) in enumerate(chapter_chunk_ranges):
            chapter_text_chunks = [
                all_chunks[i]["text"]
                for i in range(start, end)
                if all_chunks[i].get("type") == "text"
            ]
            self.fragments.append(
                self.build_fragment_145(
                    chapter_text_chunks, content_name=content_names[ch_idx]
                )
            )

        # Build $157 styles. Identical attribute fingerprints share one fragment
        # across chapters; previously each chapter cloned its own body/heading
        # styles, producing 2N fragments for N chapters.
        story_names = []  # body style per chapter
        extra_style_names = []  # heading and link styles (for entity registration)
        heading_style_names = {}  # ch_idx -> heading style name
        toc_link_style_names = {}  # ch_idx -> underlined link style name

        style_cache = {}  # (kind, sorted-attrs-tuple) -> entity_name
        kind_counts = {}  # kind -> next index for that kind

        def _allocate_style(kind, **attrs):
            key = (kind, tuple(sorted(attrs.items())))
            if key in style_cache:
                return style_cache[key]
            idx = kind_counts.get(kind, 0)
            kind_counts[kind] = idx + 1
            name = f"s{idx}{kind}"
            style_cache[key] = name
            self.fragments.append(self.build_fragment_157(entity_name=name, **attrs))
            if kind == "_em" and name not in extra_style_names:
                extra_style_names.append(name)
            return name

        for i, chapter in enumerate(chapters):
            fs = chapter.get("font_size", 1.0)

            body_name = _allocate_style("", font_size=fs)
            story_names.append(body_name)

            heading_fs = round(max(fs * self.HEADING_FONT_SIZE, 1.0), 4)
            mt = 2.0 if i > 0 else None
            heading_name = _allocate_style(
                "_h", font_size=heading_fs, bold=True, margin_top=mt, is_heading=True
            )
            heading_style_names[i] = heading_name
            if heading_name not in extra_style_names:
                extra_style_names.append(heading_name)

            if chapter.get("toc_links"):
                link_name = _allocate_style("_link", font_size=fs, underline=True)
                toc_link_style_names[i] = link_name
                if link_name not in extra_style_names:
                    extra_style_names.append(link_name)

        # Image entries need their own $157 styles with image-specific
        # layout attributes. Reference uses three styles based on image
        # shape:
        # - "small" (s7N8): tiny square chapter ornaments (~222px),
        #   sized 3em x 3em.
        # - "inline" (s5J): wide rule-style decorations (e.g. 1090x92),
        #   capped at 9.626% viewport height.
        # - "page" (s4R): full-page images (maps, diagrams, ~2200px),
        #   sized 100% to fill the available height.
        # Heuristic:
        #   both dims >= 600px           -> page
        #   both dims <= 300px, ~square  -> small
        #   else                         -> inline
        def _classify(w, h):
            if not (w and h):
                return "inline"
            if w >= 600 and h >= 600:
                return "page"
            if w <= 300 and h <= 300:
                ratio = max(w, h) / float(min(w, h))
                if ratio <= 1.4:
                    return "small"
            return "inline"

        any_image_chunk = any(
            isinstance(c, dict) and c.get("type") == "image" for c in all_chunks
        )
        image_style_names = {"small": None, "inline": None, "page": None}
        resource_to_dims = {}
        if any_image_chunk:
            for base, dims in (self._image_dims or {}).items():
                resname, _ = (self.image_resources or {}).get(base, (None, None))
                if resname:
                    resource_to_dims[resname] = dims

            kinds_used = set()
            for c in all_chunks:
                if isinstance(c, dict) and c.get("type") == "image":
                    w, h = resource_to_dims.get(c.get("resource"), (None, None))
                    kinds_used.add(_classify(w, h))

            for kind in kinds_used:
                name = {"small": "s_img_sm", "inline": "s_img", "page": "s_img_page"}[
                    kind
                ]
                self.fragments.append(
                    self.build_fragment_157_image(entity_name=name, kind=kind)
                )
                extra_style_names.append(name)
                image_style_names[kind] = name

        def _image_style_for(resource_name):
            w, h = resource_to_dims.get(resource_name, (None, None))
            kind = _classify(w, h)
            return image_style_names.get(kind) or image_style_names.get("inline")

        from .inline_style import FLAG_BOLD, FLAG_ITALIC

        def _emphasis_style(flags):
            return _allocate_style(
                "_em",
                italic=FLAG_ITALIC in flags,
                bold=FLAG_BOLD in flags,
            )

        # With per-chapter $145 fragments (#2), each chapter's $259
        # entries address into their OWN content fragment, so $403
        # indices reset to 0 at the start of every chapter.
        chapter_text_offsets = [0] * len(chapters)

        # Build multi-entry $259 storylines (one entry per chunk per chapter)
        storyline_names = []
        for ch_idx in range(len(chapters)):
            sl_name = f"l{ch_idx}"
            start, end = chapter_chunk_ranges[ch_idx]

            # Build per-entry style names, link targets, kinds, image specs
            entry_styles = []
            entry_link_targets = []
            entry_link_styles = []
            entry_link_text_lengths = []
            entry_kinds = []
            entry_image_specs = []
            entry_emphasis_spans = []
            for chunk_idx in range(start, end):
                chunk = all_chunks[chunk_idx]
                if chunk.get("type") == "image":
                    entry_styles.append(
                        _image_style_for(chunk["resource"]) or story_names[ch_idx]
                    )
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                    entry_kinds.append("image")
                    entry_image_specs.append(
                        {"resource": chunk["resource"], "alt": chunk.get("alt", "")}
                    )
                    entry_emphasis_spans.append(None)
                    continue
                entry_kinds.append("text")
                entry_image_specs.append(None)
                if chunk_idx in heading_chunk_indices:
                    entry_styles.append(heading_style_names[ch_idx])
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                elif chunk_idx in chunk_anchor_map:
                    # Link entry: entry uses body style; link styling lives
                    # inside the $142 span via entry_link_styles. Reference
                    # has entry $157 = body style, span $157 = link style.
                    entry_styles.append(story_names[ch_idx])
                    entry_link_targets.append(chunk_anchor_map[chunk_idx])
                    entry_link_styles.append(toc_link_style_names[ch_idx])
                    entry_link_text_lengths.append(len(chunk["text"]))
                else:
                    bs = chunk.get("block_style") or {}
                    attrs = {"font_size": chapters[ch_idx].get("font_size", 1.0)}
                    if bs.get("align"):
                        attrs["align"] = bs["align"]
                    if bs.get("indent"):
                        attrs["text_indent"] = bs["indent"]
                    entry_styles.append(_allocate_style("", **attrs))
                    entry_link_targets.append(None)
                    entry_link_styles.append(None)
                    entry_link_text_lengths.append(None)
                chunk_spans = chunk.get("spans", [])
                entry_emphasis_spans.append(
                    [
                        (s, length, _emphasis_style(flags))
                        for (s, length, flags) in chunk_spans
                    ]
                )

            frag_259 = self.build_fragment_259(
                entry_styles,
                content_name=content_names[ch_idx],
                entity_name=sl_name,
                positions=chunk_positions[start:end],
                content_index_offset=chapter_text_offsets[ch_idx],
                link_targets=entry_link_targets,
                link_styles=entry_link_styles,
                link_text_lengths=entry_link_text_lengths,
                outer_position=outer_positions[ch_idx],
                outer_style=story_names[ch_idx],
                chunk_kinds=entry_kinds,
                image_specs=entry_image_specs,
                emphasis_spans=entry_emphasis_spans,
            )
            storyline_names.append(sl_name)
            self.fragments.append(frag_259)

        return {
            "story_names": story_names,
            "storyline_names": storyline_names,
            "content_names": content_names,
            "section_positions": section_positions,
            "chapter_start_positions": chapter_start_positions,
            "chunk_positions": chunk_positions,
            "outer_positions": outer_positions,
            "chapter_chunk_ranges": chapter_chunk_ranges,
            "all_chunks": all_chunks,
            "anchor_names": anchor_names,
            "extra_style_names": extra_style_names,
        }
