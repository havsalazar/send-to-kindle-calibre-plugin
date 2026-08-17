import os
import subprocess
import traceback

from qt.core import QMenu

from calibre import prepare_string_for_xml
from calibre.gui2 import Dispatcher, error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.ptempfile import PersistentTemporaryDirectory
from calibre.utils.filenames import ascii_filename

from calibre_plugins.send_to_kindle.config import (
    DOWNLOAD_URL,
    candidate_exe_paths,
    prefs,
    resolve_exe,
)


def decode_output(raw):
    'Turn the tail of a subprocess output stream into a short one line message'
    if not raw:
        return ''
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', 'replace')
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    return ' '.join(lines[-3:])[:300]


class SendToKindleAction(InterfaceAction):
    name = 'Send to Kindle'
    action_spec = ('Send to Kindle', None, 'Send the selected books to Kindle', None)
    action_type = 'current'

    def genesis(self):
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.create_menu_action(
            self.menu, 'send_to_kindle_send', _('Send selected books to Kindle'),
            description=_('Send the selected books to the Send to Kindle application'),
            triggered=self.send_to_kindle)
        self.menu.addSeparator()
        self.create_menu_action(
            self.menu, 'send_to_kindle_configure', _('Configure...'),
            description=_('Configure the Send to Kindle plugin'),
            triggered=self.show_configuration)
        self.qaction.triggered.connect(self.send_to_kindle)

    def location_selected(self, loc):
        self.qaction.setEnabled(loc == 'library')

    def show_configuration(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    # --- entry point --------------------------------------------------------
    def send_to_kindle(self):
        exe = resolve_exe()
        if not exe or not os.path.isfile(exe):
            return self.report_missing_exe(exe)

        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(
                self.gui, _('No books selected'),
                _('Select the books you want to send to your Kindle first.'), show=True)
        book_ids = [self.gui.library_view.model().id(row) for row in rows]

        items, results = self.prepare_items(book_ids)
        if not items and not results:
            return

        columns = self.ensure_columns()
        opts = {
            'timeout': int(prefs['timeout']),
            'convert_timeout': int(prefs['convert_timeout']),
        }
        desc = ngettext('Send one book to Kindle',
                        'Send {} books to Kindle', len(book_ids)).format(len(book_ids))
        # The callback is run by the worker thread, so it has to be dispatched
        # onto the GUI thread before it touches the database or shows a dialog
        job = ThreadedJob('send_to_kindle', desc, self.run_batch,
                          (exe, items, opts, results), {}, Dispatcher(self.batch_done))
        job.stk_columns = columns
        self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(desc, 3000)

    # --- preparation (GUI thread, needs the db) -----------------------------
    def prepare_items(self, book_ids):
        '''
        Work out what file to send for each book. Returns (items, results) where
        items still have to be processed by the job and results are the books that
        already failed (no format, missing file).
        '''
        db = self.gui.current_db
        new_api = db.new_api
        fmt_priority = [f.upper() for f in prefs['format_priority']]
        convert_pdf = bool(prefs['convert_pdf'])

        items, results = [], []
        tdir = None
        for book_id in book_ids:
            title = new_api.field_for('title', book_id) or _('Unknown')
            fmts = [f.upper() for f in (new_api.formats(book_id) or ())]
            if not fmts:
                results.append(self.result(book_id, title, False, _('The book has no formats')))
                continue
            fmt = next((f for f in fmt_priority if f in fmts), fmts[0])
            if fmt == 'PDF' and convert_pdf and 'EPUB' in fmts:
                # No point converting, the book already has the EPUB we would produce
                fmt = 'EPUB'
            path = new_api.format_abspath(book_id, fmt)
            if not path:
                results.append(self.result(
                    book_id, title, False,
                    _('The {} file is missing from the library').format(fmt)))
                continue

            item = {'book_id': book_id, 'title': title, 'fmt': fmt, 'path': path,
                    'convert': False, 'out_path': None, 'recs': None}
            if fmt == 'PDF' and convert_pdf:
                if tdir is None:
                    tdir = PersistentTemporaryDirectory('_send_to_kindle')
                try:
                    item['recs'] = self.conversion_recommendations(db, book_id)
                    item['out_path'] = self.conversion_output_path(tdir, new_api, book_id, title)
                    item['convert'] = True
                except Exception:
                    traceback.print_exc()
                    results.append(self.result(
                        book_id, title, False,
                        _('Could not prepare the PDF for conversion, see the calibre log')))
                    continue
            items.append(item)
        return items, results

    def conversion_recommendations(self, db, book_id):
        'Give the conversion the library metadata, the way calibre\'s own Convert does'
        from calibre.customize.conversion import OptionRecommendation
        from calibre.gui2.convert.metadata import create_cover_file, create_opf_file

        # These helpers take the legacy database object, not new_api
        unused_mi, opf_file = create_opf_file(db, book_id)
        recs = [('read_metadata_from_opf', opf_file.name, OptionRecommendation.HIGH)]
        cover_file = create_cover_file(db, book_id)
        if cover_file is not None:
            recs.append(('cover', cover_file.name, OptionRecommendation.HIGH))
        return recs

    def conversion_output_path(self, tdir, new_api, book_id, title):
        '''
        Send to Kindle shows the file name on the device, so give the converted
        EPUB a proper "Title - Author" name instead of a temporary one. Each book
        gets its own directory so two books cannot collide.
        '''
        authors = new_api.field_for('authors', book_id) or ()
        name = title
        if authors:
            name = '{} - {}'.format(title, ' & '.join(authors))
        name = ascii_filename(name).strip() or 'book'
        book_dir = os.path.join(tdir, str(book_id))
        os.makedirs(book_dir)
        return os.path.join(book_dir, name[:120] + '.epub')

    def ensure_columns(self):
        '''
        Return (sent_column, error_column) lookup names to write the result to, or
        (None, None) when the result must not be recorded.
        '''
        if not prefs['track_status']:
            return None, None
        sent, error = '#' + prefs['sent_column'], '#' + prefs['error_column']
        db = self.gui.current_db
        existing = set(db.new_api.field_metadata.custom_field_keys())
        missing = [c for c in (sent, error) if c not in existing]
        if not missing:
            return sent, error

        if not question_dialog(
                self.gui, _('Create tracking columns?'),
                _('To keep track of what was sent to your Kindle, this plugin needs the '
                  'custom columns {0} (Yes/No) and {1} (text) in this library.'
                  '\n\nCreate them now?').format(sent, error)):
            return None, None
        try:
            if sent in missing:
                db.new_api.create_custom_column(
                    prefs['sent_column'], _('Sent to Kindle'), 'bool', False)
            if error in missing:
                db.new_api.create_custom_column(
                    prefs['error_column'], _('Send to Kindle error'), 'text', False)
        except Exception as err:
            traceback.print_exc()
            error_dialog(self.gui, _('Could not create the columns'),
                         _('The tracking columns could not be created: {}').format(err),
                         det_msg=traceback.format_exc(), show=True)
            return None, None

        info_dialog(
            self.gui, _('Restart calibre'),
            _('The columns {0} and {1} were created. Restart calibre for them to appear. '
              'The books you are sending now will be sent, but their result will only be '
              'recorded after the restart.').format(sent, error), show=True)
        return None, None

    # --- worker (background thread) -----------------------------------------
    def run_batch(self, exe, items, opts, results, abort=None, log=None, notifications=None):
        from calibre.utils.ipc.simple_worker import WorkerError, fork_job

        results = list(results)
        total = len(items) or 1
        for i, item in enumerate(items):
            if abort is not None and abort.is_set():
                break
            title, path, epub_path = item['title'], item['path'], None

            if item['convert']:
                self.notify(notifications, i / total, _('Converting {} to EPUB').format(title))
                if log is not None:
                    log('Converting', path, 'to', item['out_path'])
                try:
                    fork_job('calibre.gui2.convert.gui_conversion', 'gui_convert_override',
                             args=[path, item['out_path'], item['recs']],
                             timeout=opts['convert_timeout'])
                except WorkerError as err:
                    if log is not None:
                        log.error(getattr(err, 'orig_tb', '') or str(err))
                    results.append(self.result(
                        item['book_id'], title, False,
                        _('Conversion to EPUB failed: {}').format(
                            decode_output(getattr(err, 'orig_tb', '')) or str(err))))
                    continue
                except Exception as err:
                    if log is not None:
                        log.error(traceback.format_exc())
                    results.append(self.result(
                        item['book_id'], title, False,
                        _('Conversion to EPUB failed: {}').format(err)))
                    continue
                path = epub_path = item['out_path']

            self.notify(notifications, (i + 0.5) / total, _('Sending {}').format(title))
            if log is not None:
                log('Sending', path)
            ok, error = self.send_file(exe, path, opts['timeout'])
            results.append(self.result(item['book_id'], title, ok, error, epub_path))
        self.notify(notifications, 1, _('Finished'))
        return results

    def send_file(self, exe, path, timeout):
        try:
            proc = subprocess.run(
                [exe, path], capture_output=True, timeout=timeout,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            return False, _('The Send to Kindle application did not finish within {} seconds').format(timeout)
        except FileNotFoundError:
            return False, _('The Send to Kindle application was not found at: {}').format(exe)
        except OSError as err:
            return False, _('Could not run the Send to Kindle application: {}').format(err)

        if proc.returncode == 0:
            return True, ''
        detail = decode_output(proc.stderr) or decode_output(proc.stdout)
        msg = _('Exit code {}').format(proc.returncode)
        if detail:
            msg = '{}: {}'.format(msg, detail)
        return False, msg

    # --- completion (GUI thread) --------------------------------------------
    def batch_done(self, job):
        if job.failed:
            return self.gui.job_exception(job, dialog_title=_('Send to Kindle failed'))

        results = job.result or []
        if not results:
            return
        db = self.gui.current_db
        new_api = db.new_api

        # Keep the converted EPUBs, so the next send does not convert again
        for r in results:
            if not r.get('epub_path') or not os.path.exists(r['epub_path']):
                continue
            try:
                new_api.add_format(r['book_id'], 'EPUB', r['epub_path'],
                                   replace=False, run_hooks=False)
            except Exception:
                traceback.print_exc()
                job.log.error('Failed to add the converted EPUB for', r['title'])

        sent_column, error_column = getattr(job, 'stk_columns', (None, None))
        if sent_column and error_column:
            try:
                new_api.set_field(sent_column, {r['book_id']: bool(r['ok']) for r in results})
                new_api.set_field(error_column, {r['book_id']: r['error'] or '' for r in results})
            except Exception:
                traceback.print_exc()
                error_dialog(self.gui, _('Could not record the result'),
                             _('The books were processed but the result could not be written '
                               'to the tracking columns.'),
                             det_msg=traceback.format_exc(), show=True)

        book_ids = [r['book_id'] for r in results]
        self.gui.library_view.model().refresh_ids(book_ids)
        self.gui.tags_view.recount()

        failed = [r for r in results if not r['ok']]
        sent = len(results) - len(failed)
        self.gui.status_bar.show_message(
            _('Sent {0} of {1} books to Kindle').format(sent, len(results)), 5000)
        if failed:
            error_dialog(
                self.gui, _('Some books were not sent'),
                _('{0} of {1} books could not be sent to your Kindle.').format(
                    len(failed), len(results)),
                det_msg='\n'.join('{}: {}'.format(r['title'], r['error']) for r in failed),
                show=True)

    # --- helpers ------------------------------------------------------------
    def result(self, book_id, title, ok, error='', epub_path=None):
        return {'book_id': book_id, 'title': title, 'ok': ok, 'error': error,
                'epub_path': epub_path}

    def notify(self, notifications, frac, msg):
        if notifications is not None:
            notifications.put((frac, msg))

    def report_missing_exe(self, configured):
        checked = [configured] if configured else candidate_exe_paths()
        msg = '<p>' + _(
            'The Send to Kindle application could not be found, so nothing was sent.')
        msg += '<p>' + _('Looked for it here:') + '<ul>{}</ul>'.format(
            ''.join('<li>{}</li>'.format(prepare_string_for_xml(p)) for p in checked))
        msg += '<p>' + _(
            'Download and install it from <a href="{0}">{0}</a>, or set the path to it in '
            'Preferences &rarr; Plugins &rarr; Send to Kindle &rarr; Customize.').format(DOWNLOAD_URL)
        error_dialog(self.gui, _('Send to Kindle not found'), msg, show=True)
        if question_dialog(self.gui, _('Set the path now?'),
                           _('Do you want to open the plugin configuration and set the path '
                             'to the Send to Kindle application?')):
            self.show_configuration()
