"""Exercise manual recording, resume and suite editing without robot hardware.

Run from the console root: python -m tests.browser_evaluation
"""
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from backend.server import Application
from backend.workbench import make_workbench_handler
from tests.test_console import png


def check_mode(browser, mode):
    with tempfile.TemporaryDirectory() as directory:
        app = Application(dict(mode=mode, output_dir=directory, datasets={}, camera_preview_on_start=False))
        image = app.upload(png())
        suite = app.evaluation.create_suite(dict(task='egg', name='resume_suite', directory_name='resume_suite',
            layouts=[dict(url=image['url'], label='one'), dict(url=image['url'], label='two')]))
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(f'http://127.0.0.1:{server.server_port}/#vla')
            expect(page.locator('#ve-edit-suite')).to_be_enabled()
            # A fresh page must restore the persisted draft and revision for editing.
            page.reload()
            page.locator('#ve-edit-suite').click()
            expect(page.locator('#ve-suite-name')).to_have_value('resume_suite')
            expect(page.locator('.ve-draft-item')).to_have_count(2)
            page.locator('#ve-target-count').fill('20')
            page.locator('#ve-draft [data-action=down]').first.click()
            page.locator('#ve-save-suite').click()
            page.wait_for_function("document.querySelector('#message').textContent.includes('suite 已保存')")
            saved = app.evaluation.read('suites', suite['id'])
            assert saved['revision'] == 1 and saved['target_count'] == 20
            assert [item['label'] for item in saved['layouts']] == ['two', 'one']
            page.reload()
            page.locator('#ve-edit-suite').click()
            expect(page.locator('#ve-target-count')).to_have_value('20')
            page.locator('#ve-save-suite').click()
            page.wait_for_function("document.querySelector('#message').textContent.includes('suite 已保存')")
            assert app.evaluation.read('suites', suite['id'])['revision'] == 2

            # No process profiles or checkpoint catalog are needed for manual recording.
            page.locator('#ve-manual-checkpoint').fill('terminal-checkpoint')
            page.locator('#ve-round').fill('manual-round')
            page.locator('#ve-create-run').click()
            expect(page.locator('#ve-manual-start')).to_be_enabled()
            page.locator('#ve-manual-start').click()
            expect(page.locator('#ve-manual-result')).to_be_enabled()
            expect(page.locator('#ve-trial-end')).to_be_disabled()
            page.locator('#ve-trial-result').select_option('success')
            page.locator('#ve-note').fill('manual result')
            page.locator('#ve-manual-result').click()
            expect(page.locator('#ve-next')).to_be_enabled()
            # Exit on a completed sample, then reload and continue the remaining sample.
            page.locator('#ve-run-exit').click()
            expect(page.locator('#ve-current')).to_be_hidden()
            page.reload()
            page.locator('#ve-load').click()
            expect(page.locator('#ve-next')).to_be_enabled()
            page.locator('#ve-next').click()
            expect(page.locator('#ve-manual-start')).to_be_enabled()
            page.locator('#ve-manual-start').click()
            expect(page.locator('#ve-manual-result')).to_be_enabled()
            page.locator('#ve-trial-result').select_option('failure')
            page.locator('#ve-manual-result').click()
            expect(page.locator('#ve-progress')).to_contain_text('全部判定完成')
            run = app.evaluation.catalog()['runs'][0]
            assert run['checkpoint'] == 'terminal-checkpoint'
            assert [t['status'] for t in run['trials']] == ['success', 'failure']
            assert run['trials'][0]['note'] == 'manual result'
            assert Path(run['result_files']['json']).is_file()
            assert not app.control.jobs
            assert not errors, errors
            print(mode + ': suite reload/edit and manual resume passed')
        finally:
            page.close()
            server.shutdown()
            server.server_close()
            thread.join()
            app.control.close()


if __name__ == '__main__':
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
        try:
            for mode in ('demo', 'reference_only'):
                check_mode(browser, mode)
        finally:
            browser.close()
