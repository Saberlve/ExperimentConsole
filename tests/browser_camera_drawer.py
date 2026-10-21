"""Exercise the expandable VLA camera sidebar using synthetic cameras only."""
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

from backend.server import Application
from backend.workbench import make_workbench_handler
from tests.test_training_overlay import make_dataset, fingerprints


def main():
    with tempfile.TemporaryDirectory() as directory:
        dataset = Path(directory) / 'dataset'
        make_dataset(dataset)
        before = fingerprints(dataset)
        app = Application(dict(mode='demo', output_dir=str(Path(directory) / 'output'), datasets={},
                               camera_preview_on_start=False))
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome',
                                            headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1050})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#vla')
                page.wait_for_function('window.consoleReady')
                expect(page.locator('#message')).to_contain_text('相机已释放')
                controls = page.locator('.controls')
                initial = controls.bounding_box()['width']
                page.locator('#refresh').click()
                expect(page.locator('#refresh')).to_have_attribute('aria-expanded', 'true')
                expect(page.locator('#vla-camera-drawer')).to_have_attribute('data-state', 'ready')
                page.wait_for_function("document.querySelector('.controls').getBoundingClientRect().width>700")
                assert controls.bounding_box()['width'] > initial + 300
                expect(page.locator('#vla-evaluation')).not_to_be_visible()
                page.wait_for_function("document.querySelector('.controls').getBoundingClientRect().width>innerWidth*.85")
                expect(page.locator('#camera-drawer-close')).to_have_text('返回 VLA 评测')
                expect(page.locator('#camera-drawer-view #live')).to_be_visible()
                assert page.locator('#live').count() == 1
                page.locator('#training-overlay-root').fill(str(dataset))
                page.locator('#training-overlay-generate').click()
                expect(page.locator('#training-overlay-status')).to_contain_text('已生成 3 张')
                expect(page.locator('#training-overlay-image')).to_be_visible()
                expect(page.locator('#training-overlay-caption')).to_contain_text('3 个 episode')
                static_url = page.locator('#training-overlay-image').get_attribute('src')
                assert fingerprints(dataset) == before
                page.locator('#refresh').click()
                expect(page.locator('#vla-camera-drawer')).to_have_attribute('data-state', 'ready')
                assert page.locator('#training-overlay-image').get_attribute('src') == static_url
                page.locator('#training-overlay-root').fill(str(dataset / 'missing'))
                page.locator('#training-overlay-generate').click()
                expect(page.locator('#training-overlay-status')).to_contain_text('生成失败')
                assert page.locator('#training-overlay-image').get_attribute('src') == static_url
                page.locator('#training-overlay-root').fill(str(dataset))
                page.screenshot(path='/tmp/vla-camera-drawer-desktop.png')
                options = page.locator('#camera option').evaluate_all('(els)=>els.map(e=>e.value)')
                if len(options) > 1:
                    page.locator('#camera').select_option(options[-1])
                    expect(page.locator('#camera-drawer-status')).to_contain_text(
                        page.locator('#camera option:checked').inner_text())
                page.locator('#ve-task').fill('drawer-test')
                page.locator('#ve-suite-name').fill('drawer-test')
                page.locator('#snapshot').click()
                expect(page.locator('.ve-draft-item')).to_have_count(1)
                page.locator('#camera-drawer-close').click()
                expect(page.locator('#refresh')).to_have_attribute('aria-expanded', 'false')
                expect(page.locator('#comparison-panels #live')).to_be_visible()
                expect(page.locator('#vla-evaluation')).to_be_visible()
                assert app.preview_enabled, 'Collapsing must preserve the camera connection'
                page.locator('#refresh').click()
                expect(page.locator('#vla-camera-drawer')).to_have_attribute('data-state', 'ready')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_function("document.querySelector('#vla-camera-drawer').getBoundingClientRect().height>100")
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.locator('#vla-camera-drawer').scroll_into_view_if_needed()
                page.screenshot(path='/tmp/vla-camera-drawer-mobile.png')
                page.locator('#release-camera').click()
                expect(page.locator('#refresh')).to_have_attribute('aria-expanded', 'false')
                expect(page.locator('#message')).to_contain_text('相机已释放')
                assert page.locator('#training-overlay-image').get_attribute('src') == static_url
                assert fingerprints(dataset) == before
                assert not app.preview_enabled
                page.locator('[data-page=wm]').click()
                expect(page.locator('#comparison-panels #live')).to_be_visible()
                assert not errors, errors
                browser.close()
            print('Camera drawer: expansion, live view, camera selection, capture, collapse, mobile and release PASS')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
