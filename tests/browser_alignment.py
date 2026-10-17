"""Real browser/canvas comparison against controlled PNG camera frames, no hardware."""
import io
import os
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from backend.server import Application, make_handler


def png(width=100, height=100, changed=0):
    image = Image.new('RGB', (width, height), (40, 80, 120))
    for index in range(changed):
        image.putpixel((index % width, index // width), (255, 255, 255))
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = Application({'mode': 'demo', 'output_dir': directory, 'datasets': {}})
        camera = {'data': png(), 'id': 0, 'freeze': False, 'offline': False}
        def frame(camera_id):
            if camera['offline']:
                raise ValueError('测试相机已断流')
            if not camera['freeze']:
                camera['id'] += 1
            return camera['data'], 'image/png', str(camera['id'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app.camera, 'frame', side_effect=frame), sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_BIN', '/usr/bin/google-chrome'), headless=True)
                page = browser.new_page(viewport={'width': 1600, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#idm')
                page.locator('#reference-upload').set_input_files({'name': 'reference.png', 'mimeType': 'image/png', 'buffer': png()})
                expect(page.locator('#reference-image')).to_be_visible()
                page.wait_for_function("document.getElementById('live').naturalWidth===100")
                live = page.locator('#live').bounding_box()
                reference = page.locator('#reference-image').bounding_box()
                assert reference['x'] > live['x'] + live['width'], 'Images should appear side by side'
                page.locator('#validate').click()
                badge = page.locator('#comparison-result')
                expect(badge).to_have_attribute('data-state', 'passed')
                expect(page.locator('#match-score')).to_have_text('100.000%')
                for alpha in ('0', '100'):
                    page.locator('#alpha').fill(alpha)
                    expect(badge).to_have_attribute('data-state', 'passed')
                    expect(page.locator('#match-score')).to_have_text('100.000%')
                camera['data'] = png(changed=100)
                expect(page.locator('#match-score')).to_have_text('99.000%')
                expect(badge).to_have_attribute('data-state', 'passed')
                camera['data'] = png(changed=101)
                expect(page.locator('#match-score')).to_have_text('98.990%')
                expect(badge).to_have_attribute('data-state', 'pending')
                expect(badge).to_have_text('未通过')
                camera['data'] = png()
                expect(badge).to_have_attribute('data-state', 'passed')
                camera['freeze'] = True
                expect(badge).to_contain_text('画面超时', timeout=5000)
                expect(badge).to_have_attribute('data-state', 'pending')
                camera['freeze'] = False
                expect(badge).to_have_attribute('data-state', 'passed')
                camera['offline'] = True
                expect(badge).to_contain_text('断流')
                expect(badge).to_have_attribute('data-state', 'pending')
                camera['offline'] = False
                expect(badge).to_have_attribute('data-state', 'passed')
                page.locator('#reference-upload').set_input_files({'name': 'wrong-size.png', 'mimeType': 'image/png', 'buffer': png(200,100)})
                expect(badge).to_contain_text('尺寸不一致')
                expect(badge).to_have_attribute('data-state', 'pending')
                page.locator('#reference-upload').set_input_files({'name': 'restored.png', 'mimeType': 'image/png', 'buffer': png()})
                expect(badge).to_have_attribute('data-state', 'passed')
                page.locator('#camera').dispatch_event('change')
                expect(badge).to_have_attribute('data-state', 'pending')
                expect(badge).to_have_attribute('data-state', 'passed')
                page.locator('#validate').click()
                expect(badge).to_have_text('未启用验证')
                expect(badge).to_have_attribute('data-state', 'pending')
                assert not errors, errors
                browser.close()
                print('PASS: side-by-side, live pixel scoring, 100/99/98.99 threshold, alpha-independent, three fresh frames, frozen frame timeout, disconnection, size mismatch, reference/camera switch, stop validation; no JavaScript errors')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
