"""Camera handoff through real browser navigation; no hardware is opened."""
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
from backend.server import Application
from backend.workbench import make_workbench_handler


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = Application(dict(mode='demo', output_dir=directory, datasets={}))
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#idm')
                page.wait_for_function("document.querySelector('#live').naturalWidth>0")
                with patch.object(app, 'release_preview', wraps=app.release_preview) as release:
                    expect(page.locator('#idm-camera-disconnect')).to_be_visible()
                    page.locator('#idm-camera-disconnect').click()
                    expect(page.locator('#message')).to_contain_text('相机已释放')
                    expect(page.locator('#idm-camera-status')).to_contain_text('相机已断开')
                    assert not app.preview_enabled
                    release.assert_called_once()
                    page.reload()
                    page.wait_for_function('window.consoleReady')
                    assert not app.preview_enabled, 'Reload must not reacquire a released camera'
                    page.locator('#refresh').click()
                    page.wait_for_function("document.querySelector('#live').naturalWidth>0")
                    assert app.preview_enabled
                    with page.expect_response(lambda r: '/api/evaluation/camera/release' in r.url and r.request.method == 'POST'):
                        page.locator('[data-page=vla]').click()
                    expect(page.locator('#message')).to_contain_text('相机已释放')
                    assert not app.preview_enabled
                    assert release.call_count >= 2
                    expect(page.locator('[aria-current=step]')).to_have_text('1 选择测试集与模型')
                page.locator('[data-page=idm]').click()
                with patch.object(app, 'release_preview', side_effect=ValueError('外部相机服务仍在运行')):
                    page.locator('#release-camera').click()
                    expect(page.locator('#message')).to_contain_text('外部相机服务仍在运行')
                page.locator('#refresh').click()
                page.wait_for_function("document.querySelector('#live').naturalWidth>0")
                assert app.preview_enabled, 'A failed release must not permanently disable refresh'
                assert not errors, errors
                browser.close()
                print('PASS: manual release, reload, VLA navigation, release error and preview recovery')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            app.control.close()


if __name__ == '__main__':
    main()
