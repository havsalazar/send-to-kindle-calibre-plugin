import traceback

from qt.core import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    Qt,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from calibre.gui2 import error_dialog, question_dialog
from calibre.utils.config import JSONConfig

from calibre_plugins.send_to_kindle import auth

# Formats the Send to Kindle service accepts. MOBI and AZW3 are deliberately absent, Amazon
# stopped accepting them in 2022, so a book that only has those has to be converted first.
SUPPORTED_FORMATS = frozenset(
    'EPUB PDF DOCX DOC TXT RTF HTM HTML JPEG JPG PNG GIF BMP'.split())

prefs = JSONConfig('plugins/send_to_kindle')

prefs.defaults['format_priority'] = ['EPUB', 'PDF', 'DOCX', 'TXT']
prefs.defaults['convert_pdf'] = True
prefs.defaults['convert_timeout'] = 900  # seconds per conversion
prefs.defaults['send_timeout'] = 300  # seconds, applied to each request to Amazon
prefs.defaults['device_serials'] = []  # devices the books are sent to
prefs.defaults['device_names'] = {}  # serial -> name, cached so this dialog opens offline
prefs.defaults['track_status'] = True
prefs.defaults['sent_column'] = 'stk_sent'


class ConfigWidget(QWidget):

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.client = auth.load_client()
        layout = QVBoxLayout(self)

        # --- Amazon account -------------------------------------------------
        account_box = QGroupBox(_('Amazon account'), self)
        account_layout = QVBoxLayout(account_box)

        row = QHBoxLayout()
        self.account_status = QLabel(self)
        self.account_status.setWordWrap(True)
        self.sign_in_button = QPushButton(_('&Sign in...'), self)
        self.sign_in_button.clicked.connect(self.sign_in)
        self.sign_out_button = QPushButton(_('Sign &out'), self)
        self.sign_out_button.clicked.connect(self.sign_out)
        row.addWidget(self.account_status, stretch=1)
        row.addWidget(self.sign_in_button)
        row.addWidget(self.sign_out_button)
        account_layout.addLayout(row)

        self.devices_list = QListWidget(self)
        self.devices_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.devices_list.setToolTip(_('Books are sent to every device ticked here'))
        account_layout.addWidget(self.devices_list)

        row = QHBoxLayout()
        row.addWidget(QLabel(_('Books are sent to every device ticked above.'), self), stretch=1)
        self.refresh_button = QPushButton(_('&Refresh devices'), self)
        self.refresh_button.clicked.connect(self.refresh_devices)
        row.addWidget(self.refresh_button)
        account_layout.addLayout(row)
        layout.addWidget(account_box)

        # --- Formats --------------------------------------------------------
        fmt_box = QGroupBox(_('Formats'), self)
        fmt_layout = QFormLayout(fmt_box)

        self.formats_edit = QLineEdit(', '.join(prefs['format_priority']), self)
        self.formats_edit.setToolTip(_(
            'Comma separated list of formats, most preferred first. The first format\n'
            'in this list that a book actually has is the one that gets sent.'))
        fmt_layout.addRow(_('&Preferred formats:'), self.formats_edit)

        supported = QLabel(_(
            'Amazon accepts {}. It no longer accepts MOBI or AZW3, so a book that only has '
            'those has to be converted before it can be sent.').format(
                ', '.join(sorted(SUPPORTED_FORMATS))), self)
        supported.setWordWrap(True)
        fmt_layout.addRow(supported)

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

        self.send_timeout_box = QSpinBox(self)
        self.send_timeout_box.setRange(30, 3600)
        self.send_timeout_box.setSuffix(_(' seconds'))
        self.send_timeout_box.setValue(int(prefs['send_timeout']))
        self.send_timeout_box.setToolTip(_(
            'How long to wait for Amazon to respond. Large books take a while to upload.'))
        fmt_layout.addRow(_('&Send timeout:'), self.send_timeout_box)
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
            'The column is created for you the first time you send a book. A "Yes" means '
            'Amazon accepted the file for delivery.'), self)
        note.setWordWrap(True)
        track_layout.addRow(note)
        layout.addWidget(track_box)

        layout.addStretch()

        self.populate_devices(prefs['device_names'], set(prefs['device_serials']))
        self.update_account_status()
        self.update_track_status(self.track_status_box.isChecked())

    # --- account ------------------------------------------------------------
    def update_account_status(self):
        signed_in = self.client is not None
        if signed_in:
            name = auth.account_name(self.client)
            self.account_status.setText(
                _('Signed in as {}.').format(name) if name else _('Signed in.'))
        else:
            self.account_status.setText(_('Not signed in. Sign in to send books to your Kindle.'))
        self.sign_in_button.setText(_('&Sign in...') if not signed_in else _('Sign in a&gain...'))
        self.sign_out_button.setEnabled(signed_in)
        self.refresh_button.setEnabled(signed_in)
        self.devices_list.setEnabled(signed_in)

    def sign_in(self):
        d = auth.LoginDialog(self)
        if d.exec() != d.DialogCode.Accepted:
            return
        self.client = d.client
        self.update_account_status()
        # A fresh account has no stored selection, so default to sending to everything
        self.refresh_devices(select_all=not prefs['device_serials'])

    def sign_out(self):
        if not question_dialog(
                self, _('Sign out?'),
                _('calibre will no longer be able to send books to your Kindle until you sign '
                  'in again. Amazon will also be asked to remove this calibre registration.')):
            return
        if self.client is not None:
            try:
                self.client.logout()
            except Exception:
                # Losing the local credentials is what matters, a failed call to Amazon here
                # only leaves a stale registration behind
                traceback.print_exc()
        auth.delete_client()
        self.client = None
        self.devices_list.clear()
        self.update_account_status()

    def refresh_devices(self, checked=False, select_all=False):
        if self.client is None:
            return
        try:
            with auth.busy_cursor():
                devices = self.client.get_owned_devices()
        except Exception as err:
            traceback.print_exc()
            return error_dialog(
                self, _('Could not list your devices'),
                _('Amazon did not return your devices: {}\n\nIf you were signed out, sign in '
                  'again.').format(auth.describe_error(err)),
                det_msg=traceback.format_exc(), show=True)

        names = {d.device_serial_number: d.device_name for d in devices}
        selected = set(names) if select_all else self.selected_serials()
        self.populate_devices(names, selected)
        if not devices:
            info = _('Amazon reports no devices on this account. Open the Kindle app or your '
                     'Kindle at least once, then refresh.')
            error_dialog(self, _('No devices found'), info, show=True)

    def populate_devices(self, names, selected):
        self.devices_list.clear()
        for serial, name in sorted(names.items(), key=lambda kv: (kv[1] or '').lower()):
            item = QListWidgetItem(name or serial, self.devices_list)
            item.setData(Qt.ItemDataRole.UserRole, serial)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if serial in selected else Qt.CheckState.Unchecked)
            item.setToolTip(serial)

    def selected_serials(self):
        out = []
        for row in range(self.devices_list.count()):
            item = self.devices_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def device_names(self):
        return {self.devices_list.item(row).data(Qt.ItemDataRole.UserRole):
                self.devices_list.item(row).text()
                for row in range(self.devices_list.count())}

    # --- helpers ------------------------------------------------------------
    def update_track_status(self, checked):
        self.sent_column_edit.setEnabled(checked)

    def validate(self):
        if self.client is None:
            error_dialog(self, _('Not signed in'),
                         _('Sign in to your Amazon account before sending books.'), show=True)
            return False
        if not self.selected_serials():
            error_dialog(self, _('No device selected'),
                         _('Tick at least one device to send books to. Click "Refresh devices" '
                           'if the list is empty.'), show=True)
            return False
        return True

    def save_settings(self):
        formats = [f.strip().upper().lstrip('.') for f in self.formats_edit.text().split(',')]
        formats = [f for f in formats if f]
        prefs['format_priority'] = formats or list(prefs.defaults['format_priority'])

        prefs['convert_pdf'] = self.convert_pdf_box.isChecked()
        prefs['convert_timeout'] = self.convert_timeout_box.value()
        prefs['send_timeout'] = self.send_timeout_box.value()

        prefs['device_serials'] = self.selected_serials()
        prefs['device_names'] = self.device_names()

        prefs['track_status'] = self.track_status_box.isChecked()
        sent = self.sent_column_edit.text().strip().lstrip('#').lower()
        prefs['sent_column'] = sent or prefs.defaults['sent_column']
