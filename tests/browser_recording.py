"""Chrome regression for IDM video drag/drop and camera recording, using DEMO only."""
import base64
import io
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer

import cv2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright, expect

from backend.server import Application, ROOT, make_handler
from tests.replay_fixtures import action_csv, FakeReplayAdapter


def drop(page, selector, data, name, mime):
    page.locator(selector).evaluate("""(element, file) => {
        const bytes=Uint8Array.from(atob(file.data),c=>c.charCodeAt(0));
        const transfer=new DataTransfer();
        transfer.items.add(new File([bytes],file.name,{type:file.mime}));
        element.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
    }""", dict(data=base64.b64encode(data).decode(), name=name, mime=mime))


def main():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        app=Application(dict(mode='demo', output_dir=directory, datasets={}))
        # Explicit fake replay process; only the camera frames and MP4 encoder run.
        app.replay.enabled=True
        adapter=FakeReplayAdapter(ROOT,app.config)
        app.replay.adapter=adapter
        server=ThreadingHTTPServer(('127.0.0.1',0), make_handler(app))
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            video=root/'episode.avi'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(32,24))
            assert writer.isOpened()
            writer.write(np.full((24,32,3),(0,0,240),dtype=np.uint8))
            writer.write(np.full((24,32,3),(240,0,0),dtype=np.uint8))
            writer.release()
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('CHROME_BIN','/usr/bin/google-chrome'),headless=True)
                page=browser.new_page(viewport=dict(width=1440,height=1100))
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#idm')
                expect(page.locator('#recording-panel')).to_be_visible()
                page.wait_for_function("document.getElementById('live').naturalWidth===640")
                expect(page.locator('#video-drop')).to_be_visible()
                image=io.BytesIO()
                Image.new('RGB',(32,24),'red').save(image,'PNG')
                drop(page,'#reference-drop',image.getvalue(),'reference.png','image/png')
                expect(page.locator('#references option')).to_have_count(2)
                drop(page,'#video-drop',video.read_bytes(),'dropped.avi','video/x-msvideo')
                expect(page.locator('#video-status')).to_contain_text('dropped.avi · 首帧 32 × 24 · 已作为参考图')
                expect(page.locator('#references option')).to_have_count(3)
                page.locator('#episode-video').set_input_files(str(video))
                expect(page.locator('#video-status')).to_contain_text('episode.avi · 首帧')
                expect(page.locator('#references option')).to_have_count(4)
                expect(page.locator('#recording-start')).to_have_count(0)
                expect(page.locator('#recording-stop')).to_have_count(0)
                page.locator('#recording-name').fill('抓取鸡蛋')
                csv_file=dict(name='raw.csv',mimeType='text/csv',buffer=action_csv())
                page.locator('#action-csv').set_input_files(csv_file)
                expect(page.locator('#action-summary')).to_contain_text('离线校验通过')
                page.locator('#replay-confirm').check()
                page.locator('#replay-start').click()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','running')
                expect(page.locator('#recording-status')).to_contain_text('正在录制')
                page.wait_for_function("async()=> (await (await fetch('/api/recording/status')).json()).frames>=3")
                folder=page.locator('#recording-directory').inner_text()
                page.reload()
                expect(page.locator('#replay-stop')).to_be_enabled()
                expect(page.locator('#recording-name')).to_have_value('抓取鸡蛋')
                expect(page.locator('#recording-name')).to_be_disabled()
                expect(page.locator('#recording-directory')).to_have_text(folder)
                page.locator('#replay-stop').click()
                expect(page.locator('#recording-videos a')).to_have_text(['抓取鸡蛋.mp4'])
                expect(page.locator('#recording-name')).to_be_enabled()
                page.locator('#action-csv').set_input_files(csv_file)
                expect(page.locator('#action-summary')).to_contain_text('离线校验通过')
                page.locator('#replay-confirm').check()
                page.locator('#replay-start').click()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','running')
                expect(page.locator('#recording-status')).to_contain_text('正在录制')
                adapter.launches[-1].done.set()
                expect(page.locator('#recording-videos a')).to_have_text(['抓取鸡蛋.mp4','抓取鸡蛋_1.mp4'])
                for item in app.recording.status()['videos']:
                    assert page.request.get(f'http://127.0.0.1:{server.server_port}'+item['url']).ok
                    capture=cv2.VideoCapture(str(app.asset(item['url'])))
                    assert capture.isOpened() and capture.read()[0]
                    capture.release()
                page.locator('#replay-confirm').check()
                page.locator('#replay-start').click()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','running')
                expect(page.locator('#recording-status')).to_contain_text('正在录制')
                adapter.launches[-1].returncode=3
                adapter.launches[-1].done.set()
                expect(page.locator('#recording-videos a')).to_have_count(3)
                expect(page.locator('#recording-status')).to_contain_text('已保存')
                expect(page.locator('#recording-name')).to_be_enabled()
                page.screenshot(path='/tmp/console-idm-recording.png',full_page=True)
                page.set_viewport_size(dict(width=390,height=844))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('[data-page="wm"]').click()
                expect(page.locator('#recording-panel')).to_be_hidden()
                assert not errors,errors
                browser.close()
                print('PASS: IDM image/video drop, video chooser, CSV auto-record/auto-save on stop/completion/failure, Chinese class name, no-overwrite suffix, download/decode, refresh recovery, mobile layout; no JS errors or real robot actions')
        finally:
            app.replay.close()
            app.recording.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':
    main()
