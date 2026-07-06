"""Persistent plugin settings for kfxgen (Preferences -> Plugins -> Customize).

Qt-free so it is safe to import from the conversion worker (which reads the
setting) as well as the GUI config widget (which edits it). Stored at
``plugins/kfxgen.json`` in the Calibre config directory.
"""

from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/kfxgen")

# Global default for the "Do not embed fonts" toggle. False -> embed the book's
# @font-face fonts (the default). The per-conversion CLI option
# `--kfxgen-disable-font-embedding` can also disable embedding; the two are OR'd.
prefs.defaults["disable_font_embedding"] = False
