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
            expect(page.locator('#ve-load')).to_have_text('继续选中的评测')
            expect(page.locator('#ve-resume')).to_be_visible()
            expect(page.locator('#ve-setup')).to_be_visible()
            assert page.locator('#ve-resume').bounding_box()['y'] < page.locator('#ve-setup').bounding_box()['y']
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
            expect(page.locator('#ve-trial-select option')).to_have_count(2)
            page.locator('#ve-trial-select').select_option('1')
            page.locator('#ve-select-trial').click()
            expect(page.locator('#ve-current-label')).to_contain_text('第 2/2 张')
            page.locator('#ve-trial-select').select_option('0')
            page.locator('#ve-select-trial').click()
            expect(page.locator('#ve-current-label')).to_contain_text('第 1/2 张')
            expect(page.locator('#ve-alignment #comparison-panels')).to_be_visible()
            expect(page.locator('#ve-alignment #overlay-live')).to_be_visible()
            expect(page.locator('#ve-alignment #alpha')).to_be_visible()
            if mode == 'demo':
                page.wait_for_function("document.querySelector('#overlay-live').dataset.frameId")
            page.locator('#ve-manual-controls').evaluate('(el)=>el.open=true')
            expect(page.locator('#ve-next-action')).to_contain_text('终端')
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
            page.locator('#ve-setup').evaluate('(el)=>el.open=false')
            expect(page.locator('#ve-load')).to_be_visible()
            page.locator('#ve-load').click()
            page.locator('#ve-manual-controls').evaluate('(el)=>el.open=true')
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
            # Summary stays visible; per-layout results are collapsed independently.
            expect(page.locator('#ve-comparison')).to_be_visible()
            expect(page.locator('#ve-comparison > .ve-table > table')).to_contain_text('50.0%')
            details = page.locator('#ve-comparison .ve-trial-results').first
            assert not details.evaluate('(el)=>el.open')
            expect(details.locator('ol')).not_to_be_visible()
            details.locator('summary').click()
            expect(details.locator('li')).to_have_count(2)
            expect(details.locator('li').nth(0)).to_contain_text('成功')
            expect(details.locator('li').nth(1)).to_contain_text('失败')
            # Suite management remains visible when new-round setup is collapsed.
            page.locator('#ve-setup').evaluate('(el)=>el.open=false')
            expect(page.locator('#ve-suite-management')).to_be_visible()
            expect(page.locator('#ve-comparison [data-delete-suite]').first).to_be_visible()
            page.locator('#ve-comparison [data-delete-suite]').first.click()
            expect(page.locator('#ve-delete-suite-dialog')).to_be_visible()
            expect(page.locator('#ve-delete-suite-description')).to_contain_text('resume_suite')
            expect(page.locator('#ve-delete-suite-cancel')).to_be_focused()
            expect(page.locator('#ve-delete-suite-dialog #ve-emergency')).to_be_visible()
            assert len(app.evaluation.catalog()['suites']) == 1
            page.locator('#ve-delete-suite-cancel').click()
            expect(page.locator('#ve-delete-suite-dialog')).not_to_be_visible()
            assert len(app.evaluation.catalog()['suites']) == 1
            page.locator('#ve-comparison [data-delete-suite]').first.click()
            page.locator('#ve-delete-suite-confirm').click()
            expect(page.locator('#message')).to_contain_text('已删除 suite')
            expect(page.locator('#ve-suite option')).to_have_count(0)
            expect(page.locator('#ve-comparison [data-delete-suite]')).to_have_count(0)
            assert app.evaluation.catalog()['suites'] == []
            assert len(app.evaluation.catalog()['runs']) == 1
            for layout in run['trials']:
                assert app.asset(layout['url']).is_file()
            # A task with no remaining suite must still expose its saved rounds.
            page.reload()
            expect(page.locator('#ve-task-filter')).to_have_value('egg')
            page.locator('#ve-load').click()
            expect(page.locator('#ve-progress')).to_contain_text('全部判定完成')
            assert not app.control.jobs
            assert not errors, errors
            print(mode + ': suite reload/edit, confirmed deletion with history preserved and manual resume passed')
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
