import os

from qt.core import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from calibre.utils.config import JSONConfig

# Known install locations of the Amazon Send to Kindle desktop application,
# tried in order when no explicit path has been configured.
KNOWN_EXE_PATHS = (
    r"C:\Program Files (x86)\Amazon\SendToKindle\StkSendToHandler.exe",
    r"C:\Program Files\Amazon\SendToKindle\StkSendToHandler.exe",
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Amazon', 'SendToKindle', 'StkSendToHandler.exe'),
)

DOWNLOAD_URL = 'https://www.amazon.com/sendtokindle'

prefs = JSONConfig('plugins/send_to_kindle')

prefs.defaults['exe_path'] = ''  # empty => autodetect
prefs.defaults['format_priority'] = ['EPUB', 'MOBI', 'AZW3', 'PDF', 'DOCX', 'TXT']
prefs.defaults['convert_pdf'] = True
prefs.defaults['convert_timeout'] = 900  # seconds per conversion
prefs.defaults['timeout'] = 15  # seconds per send
prefs.defaults['track_status'] = True
prefs.defaults['sent_column'] = 'stk_sent'


def candidate_exe_paths():
    'The paths autodetection looks at, in order, ignoring empty entries'
    return [p for p in KNOWN_EXE_PATHS if p and os.path.basename(p)]


def resolve_exe():
    '''
    Return the path of the Send to Kindle handler to use, or '' if none was found.

    A configured path always wins, even if it does not exist -- the caller reports
    that back to the user rather than silently falling back to another install.
    '''
    configured = (prefs['exe_path'] or '').strip()
    if configured:
        return configured
    for path in candidate_exe_paths():
        if os.path.isfile(path):
            return path
    return ''


class ConfigWidget(QWidget):

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        layout = QVBoxLayout(self)

        # --- Send to Kindle application -------------------------------------
        exe_box = QGroupBox(_('Send to Kindle application'), self)
        exe_layout = QVBoxLayout(exe_box)

        row = QHBoxLayout()
        self.exe_edit = QLineEdit(prefs['exe_path'] or '', self)
        self.exe_edit.setPlaceholderText(_('Leave empty to detect the standard install automatically'))
        self.exe_edit.textChanged.connect(self.update_exe_status)
        browse = QPushButton(_('&Browse...'), self)
        browse.clicked.connect(self.browse_for_exe)
        row.addWidget(self.exe_edit)
        row.addWidget(browse)
        exe_layout.addLayout(row)

        self.exe_status = QLabel(self)
        self.exe_status.setOpenExternalLinks(True)
        self.exe_status.setWordWrap(True)
        exe_layout.addWidget(self.exe_status)
        layout.addWidget(exe_box)

        # --- Formats --------------------------------------------------------
        fmt_box = QGroupBox(_('Formats'), self)
        fmt_layout = QFormLayout(fmt_box)

        self.formats_edit = QLineEdit(', '.join(prefs['format_priority']), self)
        self.formats_edit.setToolTip(_(
            'Comma separated list of formats, most preferred first. The first format\n'
            'in this list that a book actually has is the one that gets sent.'))
        fmt_layout.addRow(_('&Preferred formats:'), self.formats_edit)

        self.convert_pdf_box = QCheckBox(_('Convert PDF to EPUB before sending'), self)
        self.convert_pdf_box.setChecked(bool(prefs['convert_pdf']))
        self.convert_pdf_box.setToolTip(_(
            'When the file that would be sent is a PDF, convert it to EPUB first and\n'
            'add the EPUB to the book, so it does not have to be converted again.'))
        fmt_layout.addRow(self.convert_pdf_box)

        self.convert_timeout_box = QSpinBox(self)
        self.convert_timeout_box.setRange(30, 36000)
        self.convert_timeout_box.setSuffix(_(' seconds'))
        self.convert_timeout_box.setValue(int(prefs['convert_timeout']))
        fmt_layout.addRow(_('Conversion &timeout:'), self.convert_timeout_box)

        self.timeout_box = QSpinBox(self)
        self.timeout_box.setRange(5, 3600)
        self.timeout_box.setSuffix(_(' seconds'))
        self.timeout_box.setValue(int(prefs['timeout']))
        fmt_layout.addRow(_('&Send timeout:'), self.timeout_box)
        layout.addWidget(fmt_box)

        # --- Status tracking ------------------------------------------------
        track_box = QGroupBox(_('Status tracking'), self)
        track_layout = QFormLayout(track_box)

        self.track_status_box = QCheckBox(_('Record the result of each send in a custom column'), self)
        self.track_status_box.setChecked(bool(prefs['track_status']))
        self.track_status_box.toggled.connect(self.update_track_status)
        track_layout.addRow(self.track_status_box)

        self.sent_column_edit = QLineEdit(prefs['sent_column'], self)
        self.sent_column_edit.setToolTip(_('Lookup name of a Yes/No column, without the leading #'))
        track_layout.addRow(_('&Sent column:'), self.sent_column_edit)

        note = QLabel(_(
            'The column is created for you the first time you send a book. '
            'A "Yes" means the file was handed to the Send to Kindle application '
            'without an error, not that Amazon has confirmed delivery.'), self)
        note.setWordWrap(True)
        track_layout.addRow(note)
        layout.addWidget(track_box)

        layout.addStretch()

        self.update_exe_status()
        self.update_track_status(self.track_status_box.isChecked())

    # --- helpers ------------------------------------------------------------
    def browse_for_exe(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, _('Select StkSendToHandler.exe'), self.exe_edit.text() or '',
            _('Programs') + ' (*.exe);;' + _('All files') + ' (*)')
        if path:
            self.exe_edit.setText(os.path.normpath(path))

    def update_exe_status(self, *args):
        configured = self.exe_edit.text().strip()
        if configured:
            if os.path.isfile(configured):
                self.exe_status.setText(_('Found the Send to Kindle application.'))
            else:
                self.exe_status.setText(_(
                    'No file at that path. Install the Send to Kindle application from '
                    '<a href="{0}">{0}</a> or correct the path.').format(DOWNLOAD_URL))
            return
        detected = resolve_exe()
        if detected:
            self.exe_status.setText(_('Detected automatically at: {0}').format(detected))
        else:
            self.exe_status.setText(_(
                'The Send to Kindle application was not found in any of its usual locations. '
                'Download it from <a href="{0}">{0}</a> or set the path above.').format(DOWNLOAD_URL))

    def update_track_status(self, checked):
        self.sent_column_edit.setEnabled(checked)

    def validate(self):
        return True

    def save_settings(self):
        prefs['exe_path'] = self.exe_edit.text().strip()

        formats = [f.strip().upper().lstrip('.') for f in self.formats_edit.text().split(',')]
        formats = [f for f in formats if f]
        prefs['format_priority'] = formats or list(prefs.defaults['format_priority'])

        prefs['convert_pdf'] = self.convert_pdf_box.isChecked()
        prefs['convert_timeout'] = self.convert_timeout_box.value()
        prefs['timeout'] = self.timeout_box.value()

        prefs['track_status'] = self.track_status_box.isChecked()
        sent = self.sent_column_edit.text().strip().lstrip('#').lower()
        prefs['sent_column'] = sent or prefs.defaults['sent_column']
