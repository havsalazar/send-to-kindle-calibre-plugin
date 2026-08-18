import os
import traceback
import urllib.error
import urllib.parse
from contextlib import contextmanager

from qt.core import (
    QApplication,
    QCursor,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QUrl,
    QVBoxLayout,
)

from calibre.constants import config_dir
from calibre.gui2 import error_dialog, open_url

from calibre_plugins.send_to_kindle import stkclient

# The serialized client holds the device private key and the authentication token, so it is kept
# out of the plugin preferences (which are world readable JSON) and in a file of its own. That
# also makes signing out a plain delete.
CLIENT_FILENAME = 'send_to_kindle_client.json'

# The last leg of the sign in redirects here with the authorization code in the query string
REDIRECT_PREFIX = 'https://www.amazon.com/gp/sendtokindle'
CODE_PARAM = 'openid.oa2.authorization_code'


def client_path():
    return os.path.join(config_dir, 'plugins', CLIENT_FILENAME)


def load_client():
    'Return the stored Client, or None when there is no usable one'
    try:
        with open(client_path()) as f:
            return stkclient.Client.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        # A truncated or older file must not break the plugin, the user can just sign in again
        traceback.print_exc()
        return None


def save_client(client):
    '''
    Write the client out readable only by its owner.

    The mode is honoured on Linux and macOS. Windows ignores it, there the file is protected
    only by the permissions on the calibre configuration directory itself.
    '''
    path = client_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = client.dumps()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(data)


def delete_client():
    try:
        os.remove(client_path())
    except FileNotFoundError:
        pass


def has_client():
    return os.path.exists(client_path())


def account_name(client):
    'The name Amazon has for the signed in account, or an empty string'
    try:
        info = client._device_info
        return info.given_name or info.name or ''
    except Exception:
        return ''


@contextmanager
def busy_cursor():
    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def describe_error(err):
    'Turn an exception from the Amazon API into something worth showing a user'
    if isinstance(err, stkclient.APIError):
        return str(err)
    if isinstance(err, urllib.error.URLError):
        return _('Could not reach Amazon: {}').format(getattr(err, 'reason', err))
    return str(err) or err.__class__.__name__


class LoginDialog(QDialog):
    '''
    Runs Amazon's OAuth2 sign in.

    The single OAuth2 instance has to live for as long as the dialog does: it holds the PKCE
    verifier that the sign in URL was generated from, and the authorization code can only be
    exchanged with that same verifier.
    '''

    def __init__(self, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('Sign in to Amazon'))
        self.client = None
        self.auth = stkclient.OAuth2()

        layout = QVBoxLayout(self)

        intro = QLabel(_(
            '<p>calibre needs permission to send books to your Kindle.'
            '<ol>'
            '<li>Open the address below and sign in to Amazon.</li>'
            '<li>When the page finishes loading you end up on a Send to Kindle page. '
            'Copy the whole address out of your browser\'s address bar.</li>'
            '<li>Paste it below and click OK.</li>'
            '</ol>'), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.url_edit = QLineEdit(self.auth.get_signin_url(), self)
        self.url_edit.setReadOnly(True)
        self.url_edit.setCursorPosition(0)
        open_button = QPushButton(_('&Open in browser'), self)
        open_button.clicked.connect(self.open_in_browser)
        row.addWidget(self.url_edit)
        row.addWidget(open_button)
        layout.addLayout(row)

        redirect_label = QLabel(_('&Paste the address you were sent to:'), self)
        self.redirect_edit = QLineEdit(self)
        self.redirect_edit.setPlaceholderText(REDIRECT_PREFIX + '?...')
        redirect_label.setBuddy(self.redirect_edit)
        layout.addWidget(redirect_label)
        layout.addWidget(self.redirect_edit)

        self.bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        layout.addWidget(self.bb)

        self.resize(self.sizeHint())
        self.redirect_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def open_in_browser(self):
        open_url(QUrl(self.url_edit.text()))

    def accept(self):
        url = self.redirect_edit.text().strip()
        if not url:
            return error_dialog(
                self, _('Nothing pasted'),
                _('Paste the address you were redirected to after signing in.'), show=True)
        # Checked here so a wrong address is a clear message rather than a KeyError from the
        # authorization code lookup
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if not query.get(CODE_PARAM):
            return error_dialog(
                self, _('That is not the right address'),
                _('The address has no authorization code in it, so it is not the page you were '
                  'sent to at the end of signing in. It should start with {0} and contain '
                  '{1}.').format(REDIRECT_PREFIX, CODE_PARAM), show=True)

        try:
            with busy_cursor():
                client = self.auth.create_client(url)
        except Exception as err:
            traceback.print_exc()
            return error_dialog(
                self, _('Could not sign in'),
                _('Amazon did not accept the sign in: {}').format(describe_error(err)),
                det_msg=traceback.format_exc(), show=True)

        try:
            save_client(client)
        except OSError as err:
            traceback.print_exc()
            return error_dialog(
                self, _('Could not save the sign in'),
                _('Signed in successfully, but the credentials could not be written to {0}: '
                  '{1}').format(client_path(), err),
                det_msg=traceback.format_exc(), show=True)

        self.client = client
        QDialog.accept(self)
