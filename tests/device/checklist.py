"""The tier-4 checklist: what a human performs on hardware, and the record it
leaves behind (#109).

Tier 4 is the only oracle in this repo that proves a KFX *renders*. It was also
the only tier with nothing mechanical behind it — the `device` marker was
declared, documented, and carried by no test, so `pytest -m device` collected
nothing (exit 5, "no tests collected") and said nothing about what should have
been checked. This module gives the tier something to collect, and gives a
device pass a shape that survives being asked about later.

Two things are recorded, because either alone is unreadable a year on:

* **What was checked** — a named procedure with the issues it stands for, so
  "device-verified" means a specific thing rather than a feeling.
* **What it ran on** — model, generation and firmware. #109's own argument is
  that "a physical Kindle" is not a record: "Paperwhite" spans several
  revisions whose behaviour differs (the CHANGELOG thumbnail table has a Voyage
  failing where a Paperwhite succeeds), so the generation decides the outcome.

The pair of devices matters as much as either one. The Oasis is on terminal
firmware and can never move, so it stands for every reader that will never
update — a permanent population, and for some changes the *stricter* test
(#126 removed `$31` with no fallback, which would have degraded silently
there). The Paperwhite is on current firmware and is the only one of the two
that can observe render-side drift as Amazon changes things. A pass on one is
worth recording; it just is not the same claim as a pass on both.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Environment variable naming the sign-off file. Absent means "not verified",
#: which is a failure for a release gate rather than something to skip past.
SIGNOFF_ENV = "KFXGEN_DEVICE_SIGNOFF"

VALID_OUTCOMES = ("pass", "fail")


@dataclass(frozen=True)
class Device:
    """One piece of tier-4 hardware.

    `terminal` means the model no longer receives firmware updates, so its
    firmware is a property of the device rather than of the run and is pinned
    here. A non-terminal device's firmware can move under us, so it has to be
    recorded per run.
    """

    id: str
    model: str
    generation: str
    firmware: str | None
    terminal: bool

    @property
    def label(self) -> str:
        fw = self.firmware or "<firmware recorded per run>"
        return f"{self.model} {self.generation}, firmware {fw}"


#: The repo's tier-4 hardware, pinned in #109 after the model generations were
#: read off Settings -> Device Options -> Device Info.
DEVICES: dict[str, Device] = {
    "oasis-10": Device(
        id="oasis-10",
        model="Oasis",
        generation="10th gen (2019)",
        firmware="5.18.2",
        terminal=True,
    ),
    "paperwhite-11": Device(
        id="paperwhite-11",
        model="Paperwhite",
        generation="11th gen (2021)",
        firmware=None,
        terminal=False,
    ),
}


@dataclass(frozen=True)
class Check:
    """One thing a human does on the device, and why it is worth doing."""

    id: str
    title: str
    issues: tuple[str, ...]
    procedure: str
    fails_like: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


#: Each entry earns its place by standing for a defect that actually shipped.
#: `tags` mark the change classes CONTRIBUTING calls out as needing tier 4
#: before merge, so a PR touching them can name the checks it needs.
CHECKS: tuple[Check, ...] = (
    Check(
        id="toc_navigation",
        title="Navigation pane entries land on their chapter",
        issues=("#51", "#62"),
        procedure=(
            "Open Go To -> Table of Contents. Tap three entries spread through "
            "the book, including the last one. Each must land at the start of "
            "the chapter it names."
        ),
        fails_like=(
            "An entry does nothing, or lands at the top of the book. Every "
            "link kfxgen emitted once resolved against nothing (#51) and "
            "structural tests could not see it."
        ),
        tags=("nav",),
    ),
    Check(
        id="note_round_trip",
        title="A note marker and its return link both land on the marker",
        issues=("#52", "#79"),
        procedure=(
            "Find a superscript note marker in the body. Tap it: it must land "
            "on the note. Tap the note's return link: it must land back on the "
            "marker, not at the top of the paragraph holding it."
        ),
        fails_like=(
            "The return link lands a paragraph early. Anchors are "
            "paragraph-granular, so landing more than a page off means a "
            "stale sideload or a genuine regression (#79)."
        ),
        tags=("nav",),
    ),
    Check(
        id="body_images_render",
        title="Every image the source displays is drawn",
        issues=("#102", "#116"),
        procedure=(
            "Page through a chapter with captioned figures. Every figure must "
            "draw, not merely occupy space. Check a figure that sits between "
            "two paragraphs and one directly under a heading."
        ),
        fails_like=(
            "Images are present in the container but nothing is drawn — #102 "
            "shipped with every image in the file and none on screen, which "
            "counting resources cannot distinguish."
        ),
        tags=("$164", "$417", "images"),
    ),
    Check(
        id="raised_text",
        title="Superscript and subscript render raised and smaller",
        issues=("#115", "#123", "#126"),
        procedure=(
            "Find a superscript note marker and a subscript (a chemical "
            "formula or footnote index). Both must sit off the baseline and "
            "render smaller than surrounding text."
        ),
        fails_like=(
            "Text renders at full size on the baseline. #126 removed `$31` "
            "with no fallback, so a reader ignoring `$44` degrades silently — "
            "the terminal-firmware device is the stricter test here."
        ),
        tags=("style",),
    ),
    Check(
        id="contents_page",
        title="The contents page is generated, singular, and free of raw tokens",
        issues=("#132", "#133", "#135"),
        procedure=(
            "Open the generated contents page. It must list real chapter "
            "titles, with no image entry and no stray control characters. The "
            "book's own listing must not also appear in the body. Then page "
            "through the opening pages of the book from the very start."
        ),
        fails_like=(
            "An unresolved image token reaches the container as raw control "
            "bytes. On a Paperwhite this crashed the reader while paging the "
            "opening pages, so this check is a crash gate, not a cosmetic one "
            "(#133)."
        ),
        tags=("contents", "$164"),
    ),
    Check(
        id="cover_thumbnail",
        title="The cover appears as the home-screen thumbnail",
        issues=("#39",),
        procedure=(
            "Return to the home screen. The book must show its cover, not a "
            "generic placeholder or the title on a grey tile."
        ),
        fails_like=(
            "A short ASIN prefix inhibited thumbnail extraction (#39); the "
            "container looks correct either way."
        ),
        tags=("cover", "$164"),
    ),
)


def check_by_id(check_id: str) -> Check | None:
    for check in CHECKS:
        if check.id == check_id:
            return check
    return None


def validate_result(result: dict) -> list[str]:
    """Problems with one sign-off entry. Empty list means it counts as evidence.

    Rejecting rather than ignoring a malformed entry is the point: a record
    that quietly drops what it cannot parse is back to being unanswerable.
    """
    problems: list[str] = []

    check_id = result.get("check")
    if check_by_id(check_id) is None:
        problems.append(f"unknown check {check_id!r}")

    device_id = result.get("device")
    device = DEVICES.get(device_id)
    if device is None:
        problems.append(f"unknown device {device_id!r}")

    firmware = (result.get("firmware") or "").strip()
    if not firmware:
        problems.append("firmware not recorded")
    elif device is not None and device.terminal and firmware != device.firmware:
        problems.append(
            f"{device.id} is on terminal firmware {device.firmware}; "
            f"sign-off claims {firmware!r}"
        )

    outcome = result.get("outcome")
    if outcome not in VALID_OUTCOMES:
        problems.append(f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}")

    return problems


def results_for(signoff: dict, check_id: str) -> list[dict]:
    """Entries in a loaded sign-off that refer to `check_id`."""
    return [r for r in (signoff.get("results") or []) if r.get("check") == check_id]


def signoff_path() -> str | None:
    return os.environ.get(SIGNOFF_ENV) or None


def procedure_text(check: Check) -> str:
    """The checklist entry as a human reads it in a failure message."""
    lines = [
        f"{check.title}  [{check.id}]",
        f"  stands for: {', '.join(check.issues)}",
        f"  do: {check.procedure}",
    ]
    if check.fails_like:
        lines.append(f"  fails like: {check.fails_like}")
    return "\n".join(lines)
