import os
import traceback
import urllib.error
from pathlib import Path

from qt.core import QMenu

from calibre.gui2 import Dispatcher, error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.ptempfile import PersistentTemporaryDirectory
from calibre.utils.filenames import ascii_filename

from calibre_plugins.send_to_kindle import auth, stkclient
from calibre_plugins.send_to_kindle.config import SUPPORTED_FORMATS, prefs


def decode_output(raw):
    'Turn the tail of a conversion worker traceback into a short one line message'
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
            description=_('Send the selected books to your Kindle devices'),
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
        client = auth.load_client()
        if client is None:
            return self.report_not_configured(
                _('You are not signed in to Amazon, so nothing was sent.'))
        serials = list(prefs['device_serials'])
        if not serials:
            return self.report_not_configured(
                _('No Kindle device is selected, so nothing was sent.'))

        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(
                self.gui, _('No books selected'),
                _('Select the books you want to send to your Kindle first.'), show=True)
        book_ids = [self.gui.library_view.model().id(row) for row in rows]

        items, results = self.prepare_items(book_ids)
        if not items and not results:
            return

        sent_column = self.ensure_column()
        opts = {
            'send_timeout': int(prefs['send_timeout']),
            'convert_timeout': int(prefs['convert_timeout']),
        }
        desc = ngettext('Send one book to Kindle',
                        'Send {} books to Kindle', len(book_ids)).format(len(book_ids))
        # The callback is run by the worker thread, so it has to be dispatched
        # onto the GUI thread before it touches the database or shows a dialog
        job = ThreadedJob('send_to_kindle', desc, self.run_batch,
                          (client, serials, items, opts, results), {}, Dispatcher(self.batch_done))
        job.stk_column = sent_column
        self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(desc, 3000)

    # --- preparation (GUI thread, needs the db) -----------------------------
    def prepare_items(self, book_ids):
        '''
        Work out what file to send for each book. Returns (items, results) where
        items still have to be processed by the job and results are the books that
        already failed (no format, unsupported format, missing file).
        '''
        db = self.gui.current_db
        new_api = db.new_api
        fmt_priority = [f.upper() for f in prefs['format_priority']]
        convert_pdf = bool(prefs['convert_pdf'])

        items, results = [], []
        tdir = None
        for book_id in book_ids:
            title = new_api.field_for('title', book_id) or _('Unknown')
            authors = new_api.field_for('authors', book_id) or ()
            author = ' & '.join(authors) or _('Unknown')
            all_fmts = [f.upper() for f in (new_api.formats(book_id) or ())]
            if not all_fmts:
                results.append(self.result(book_id, title, False, _('The book has no formats')))
                continue
            fmts = [f for f in all_fmts if f in SUPPORTED_FORMATS]
            if not fmts:
                # Amazon rejects MOBI and AZW3 outright, so say so instead of uploading in vain
                results.append(self.result(
                    book_id, title, False,
                    _('Amazon does not accept {0}. Convert the book to EPUB first.').format(
                        ', '.join(all_fmts))))
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

            item = {'book_id': book_id, 'title': title, 'author': author, 'fmt': fmt,
                    'path': path, 'convert': False, 'out_path': None, 'recs': None}
            if fmt == 'PDF' and convert_pdf:
                if tdir is None:
                    tdir = PersistentTemporaryDirectory('_send_to_kindle')
                try:
                    item['recs'] = self.conversion_recommendations(db, book_id)
                    item['out_path'] = self.conversion_output_path(tdir, book_id, title, authors)
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

    def conversion_output_path(self, tdir, book_id, title, authors):
        '''
        Give the converted EPUB a proper "Title - Author" name instead of a temporary one.
        Each book gets its own directory so two books cannot collide.
        '''
        name = title
        if authors:
            name = '{} - {}'.format(title, ' & '.join(authors))
        name = ascii_filename(name).strip() or 'book'
        book_dir = os.path.join(tdir, str(book_id))
        os.makedirs(book_dir)
        return os.path.join(book_dir, name[:120] + '.epub')

    def ensure_column(self):
        '''
        Return the lookup name of the column to write the result to, or None when the
        result must not be recorded.
        '''
        if not prefs['track_status']:
            return None
        sent = '#' + prefs['sent_column']
        db = self.gui.current_db
        if sent in set(db.new_api.field_metadata.custom_field_keys()):
            return sent

        if not question_dialog(
                self.gui, _('Create the tracking column?'),
                _('To keep track of what was sent to your Kindle, this plugin needs the '
                  'custom column {0} (Yes/No) in this library.'
                  '\n\nCreate it now?').format(sent)):
            return None
        try:
            db.new_api.create_custom_column(
                prefs['sent_column'], _('Sent to Kindle'), 'bool', False)
        except Exception as err:
            traceback.print_exc()
            error_dialog(self.gui, _('Could not create the column'),
                         _('The tracking column could not be created: {}').format(err),
                         det_msg=traceback.format_exc(), show=True)
            return None

        info_dialog(
            self.gui, _('Restart calibre'),
            _('The column {0} was created. Restart calibre for it to appear. '
              'The books you are sending now will be sent, but their result will only be '
              'recorded after the restart.').format(sent), show=True)
        return None

    # --- worker (background thread) -----------------------------------------
    def run_batch(self, client, serials, items, opts, results,
                  abort=None, log=None, notifications=None):
        from calibre.utils.ipc.simple_worker import WorkerError, fork_job

        # Applies to every request this batch makes to Amazon
        stkclient.api.TIMEOUT = opts['send_timeout']

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
            ok, error = self.send_file(client, serials, item, path, log)
            results.append(self.result(item['book_id'], title, ok, error, epub_path))
        self.notify(notifications, 1, _('Finished'))
        return results

    def send_file(self, client, serials, item, path, log=None):
        'Upload one file to Amazon and have it delivered. Returns (ok, error message).'
        # Taken from the file actually being sent, so a converted PDF is reported as an EPUB
        fmt = os.path.splitext(path)[1].lstrip('.').lower()
        try:
            sku = client.send_file(Path(path), serials, author=item['author'],
                                   title=item['title'], format=fmt)
        except stkclient.APIError as err:
            if log is not None:
                log.error(traceback.format_exc())
            return False, _('Amazon rejected the file: {}').format(err)
        except urllib.error.URLError as err:
            if log is not None:
                log.error(traceback.format_exc())
            return False, _('Could not reach Amazon: {}').format(getattr(err, 'reason', err))
        except Exception as err:
            if log is not None:
                log.error(traceback.format_exc())
            return False, _('Could not send the file: {}').format(err)
        if log is not None and sku:
            log('Amazon accepted', os.path.basename(path), 'as', sku)
        return True, ''

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
                job.log.error('Failed to add the converted EPUB for {}'.format(r['title']))

        sent_column = getattr(job, 'stk_column', None)
        if sent_column:
            try:
                new_api.set_field(sent_column, {r['book_id']: bool(r['ok']) for r in results})
            except Exception:
                traceback.print_exc()
                error_dialog(self.gui, _('Could not record the result'),
                             _('The books were processed but the result could not be written '
                               'to the tracking column.'),
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

    def report_not_configured(self, msg):
        error_dialog(self.gui, _('Send to Kindle is not set up'), msg, show=True)
        if question_dialog(self.gui, _('Set it up now?'),
                           _('Do you want to open the plugin configuration?')):
            self.show_configuration()
