"""GUI config widget for kfxgen (Preferences -> Plugins -> Customize).

Imported only in the GUI (from `config_widget()`), so Qt is imported here rather
than in the conversion worker. The settings are the global font-embedding and
native-table defaults; the per-conversion CLI flags can still disable them.
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

        self.disable_tables = QCheckBox("Write tables as one paragraph per row", self)
        self.disable_tables.setToolTip(
            "Use this if a book's tables display badly on your Kindle. By default "
            "kfxgen writes native Kindle tables. The per-conversion "
            "--kfxgen-disable-native-tables option can also disable them."
        )
        self.disable_tables.setChecked(bool(prefs["disable_native_tables"]))
        layout.addWidget(self.disable_tables)
        layout.addStretch(1)

    def save_settings(self):
        prefs["disable_font_embedding"] = self.disable_fonts.isChecked()
        prefs["disable_native_tables"] = self.disable_tables.isChecked()
