"""GUI config widget for kfxgen (Preferences -> Plugins -> Customize).

Imported only in the GUI (from `config_widget()`), so Qt is imported here rather
than in the conversion worker. The single setting is the global font-embedding
default; the per-conversion CLI flag still overrides it.
"""

from qt.core import QCheckBox, QVBoxLayout, QWidget

from calibre_plugins.kfxgen.prefs import prefs


class ConfigWidget(QWidget):
    def __init__(self):
        QWidget.__init__(self)
        layout = QVBoxLayout(self)

        self.disable_fonts = QCheckBox("Do not embed fonts", self)
        self.disable_fonts.setToolTip(
            "Embedding is on by default, so a book's own @font-face fonts render "
            "on-device. Check this to skip embedding and use the font "
            "installed/selected on the Kindle instead. The per-conversion "
            "--kfxgen-disable-font-embedding option can also disable it."
        )
        self.disable_fonts.setChecked(bool(prefs["disable_font_embedding"]))
        layout.addWidget(self.disable_fonts)
        layout.addStretch(1)

    def save_settings(self):
        prefs["disable_font_embedding"] = self.disable_fonts.isChecked()
