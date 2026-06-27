"""Shared definition of the inline-image placeholder token.

`extract_text_from_html` (converter.py) emits each `<img>` as a control-char
token; the converter later strips tokens to decide whether a page is
image-only, and `native_generator._build_chapter_content` parses them back
into image chunks. Both layers must agree on the token's exact shape.

This lived in two places (a #132 review NOTE, #128-class duplication). It
can't live in converter.py because converter imports native_generator — the
reverse import would be circular — so the canonical definition lives here and
both import it.

Format: ``\\x00IMG\\x01<href>\\x01<alt-with-x02-spaces>\\x00``
"""

import re

#: Token field delimiters. All non-whitespace ASCII control chars, so
#: ``str.split()`` keeps a whole token intact through whitespace normalization.
IMG_TOKEN_DELIM = "\x00"
IMG_TOKEN_FIELD = "\x01"
IMG_TOKEN_SPACE = "\x02"

#: Matches one whole IMG token, capturing ``(href, alt)``. converter uses it
#: to strip tokens (the capture groups are ignored by ``.sub``);
#: native_generator uses the groups to rebuild image chunks.
IMG_TOKEN_RE = re.compile(r"\x00IMG\x01([^\x01]*)\x01([^\x00]*)\x00")
