"""Headless Chrome interactions. Uses DEMO camera and explicit dataset fixture."""
import io
import base64
import os
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from backend.server import Application, make_handler


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        app = Application({'mode': 'demo', 'output_dir': str(root),
                           'datasets': {'browser-fixture': {}}})
        output = io.BytesIO()
        Image.new('RGB', (640, 480), '#b08040').save(output, 'PNG')
        data = output.getvalue()
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app.dataset, 'describe', return_value={'episodes': [3], 'views': ['front']}), \
                 patch.object(app.dataset, 'first_frame', return_value=data), sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_BIN', '/usr/bin/google-chrome'), headless=True)
                page = browser.new_page(viewport={'width': 1360, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                expect(page.locator('#mode')).to_contain_text('DEMO')
                page.wait_for_function("document.getElementById('live').naturalWidth === 640")
                expect(page.locator('#alignment')).to_be_hidden()
                page.locator('#snapshot').click()
                expect(page.locator('#snapshots img')).to_have_count(1)
                expect(page.locator('#message')).to_contain_text('已保存 DEMO')
                page.get_by_role('button', name='VLA Test', exact=True).click()
                expect(page.locator('#alignment')).to_be_visible()
                page.locator('#reference-upload').set_input_files({'name':'ref.png','mimeType':'image/png','buffer':data})
                expect(page.locator('#overlay')).to_be_visible()
                page.locator('#alpha').fill('75')
                expect(page.locator('#overlay')).to_have_css('opacity','0.75')
                for value, css in [('0','0'),('100','1'),('50','0.5')]:
                    page.locator('#alpha').fill(value)
                    expect(page.locator('#overlay')).to_have_css('opacity',css)
                page.locator('#show-reference').uncheck()
                expect(page.locator('#overlay')).to_have_css('opacity','0')
                page.locator('#show-reference').check()
                page.locator('#dataset').select_option('browser-fixture')
                expect(page.locator('#episode')).to_have_value('3')
                page.locator('#load-reference').click()
                expect(page.locator('#references option')).to_have_count(3)
                expect(page.locator('#dataset-status')).to_contain_text('Episode 3')
                page.locator('#prepare').click()
                expect(page.locator('#message')).to_contain_text('未调用')
                page.get_by_role('button', name='IDM Test', exact=True).click()
                # Exercise actual HTML5 drag/drop, not just the file chooser.
                reference_count = page.locator('#references option').count()
                page.locator('#reference-drop').evaluate("""(element, encoded) => {
                    const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
                    const transfer=new DataTransfer();
                    transfer.items.add(new File([bytes],'dropped.png',{type:'image/png'}));
                    element.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
                }""", base64.b64encode(data).decode())
                expect(page.locator('#references option')).to_have_count(reference_count+1)
                expect(page.locator('#overlay')).to_be_visible()
                import cv2
                import numpy as np
                video_path=root/'episode.avi'
                writer=cv2.VideoWriter(str(video_path),cv2.VideoWriter_fourcc(*'MJPG'),10,(32,24))
                assert writer.isOpened()
                writer.write(np.full((24,32,3),(0,0,240),dtype=np.uint8))
                writer.write(np.full((24,32,3),(240,0,0),dtype=np.uint8))
                writer.release()
                page.locator('#episode-video').set_input_files(str(video_path))
                expect(page.locator('#video-status')).to_contain_text('已作为参考图')
                expect(page.locator('#references option')).to_have_count(reference_count+2)
                expect(page.locator('#overlay')).to_be_visible()

                expect(page.locator('#idm-panel')).to_be_visible()
                expect(page.locator('#overlay')).to_have_css('opacity','0.5')
                page.locator('#infer').click()
                expect(page.locator('#result')).to_contain_text('测试失败')
                for kind in ('current','target'):
                    page.locator('#'+kind+'-upload').set_input_files({'name':kind+'.png','mimeType':'image/png','buffer':data})
                    expect(page.locator('#'+kind+'-preview')).to_be_visible()
                page.locator('#infer').click()
                expect(page.locator('#result')).to_contain_text('"mock": true')
                expect(page.locator('#result')).to_contain_text('predicted_action')
                page.locator('#history').click()
                expect(page.locator('#result')).to_contain_text('"status": "error"')
                page.get_by_role('button', name='WM First Frame', exact=True).click()
                expect(page.locator('#overlay')).to_be_hidden()
                expect(page.locator('#alignment')).to_be_hidden()
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                print('PASS: Chrome navigation, live image, snapshot, upload, overlay 0/50/75/100%, toggle, dataset fixture, VLA preparation, IDM validation/Mock/history, mobile layout, HTML5 image drop, actual episode video extraction; no JavaScript errors')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
