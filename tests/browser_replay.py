"""Real UI upload/execute/stop with explicit fake hardware; no robot connection."""
import base64
import io
import os
import secrets
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from backend.server import Application, ROOT, make_handler
from tests.replay_fixtures import action_csv, FakeReplayAdapter


def main():
    with tempfile.TemporaryDirectory() as directory:
        config={'mode':'legacy_http','camera_autostart':False,'output_dir':directory,
                'datasets':{},'replay':{'enabled':True,'robot_ip':'192.168.1.245'}}
        app=Application(config)
        adapter=FakeReplayAdapter(ROOT,config)
        app.replay.adapter=adapter
        # Fake camera, real upload/prepare/dry-run/API and frontend; never use XArmAPI.
        output=io.BytesIO();Image.new('RGB',(100,100),'navy').save(output,'PNG')
        app.camera.cameras=lambda:[{'serial':serial,'label':label,'connected':True}
                                  for serial,label in [('fixture-overview','TEST 全景'),('fixture-wrist','TEST 腕部')]]
        app.camera.frame=lambda _: (output.getvalue(),'image/png',str(time.monotonic_ns()))
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app))
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('CHROME_BIN','/usr/bin/google-chrome'),headless=True)
                page=browser.new_page(viewport={'width':1360,'height':1100})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#idm')
                expect(page.locator('#action-replay-panel')).to_be_visible()
                expect(page.locator('#replay-start')).to_be_disabled()
                expect(page.locator('#replay-record-video')).not_to_be_checked()
                expect(page.locator('#replay-recording-directory')).to_contain_text('idm_recordings')
                # A server restart rotates the token while an existing tab keeps
                # its initial capabilities. Upload must refresh authorization.
                app.replay.token=secrets.token_urlsafe(32)
                page.locator('#action-drop').evaluate("""(element,encoded)=>{
                    const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
                    const transfer=new DataTransfer();
                    transfer.items.add(new File([bytes],'raw.csv',{type:'text/csv'}));
                    element.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
                }""",base64.b64encode(action_csv()).decode())
                expect(page.locator('#action-summary')).to_contain_text('15.000 Hz')
                expect(page.locator('#action-summary')).to_contain_text('离线校验通过')
                expect(page.locator('#action-download')).to_be_visible()
                assert not adapter.launches
                page.locator('#recording-name').fill('CSV自动录制')
                expect(page.locator('#replay-start')).to_be_disabled()
                page.locator('#replay-record-video').check()
                page.locator('#replay-confirm').check()
                adapter.approach_release=threading.Event()
                page.locator('#replay-start').click()
                expect(page.locator('#replay-recording-status')).to_have_attribute('data-state','waiting')
                expect(page.locator('#replay-recording-status')).to_contain_text('尚未开始录制')
                assert all(view['frames']==0 for view in app.replay.status()['recording']['views'])
                adapter.approach_release.set()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','running')
                expect(page.locator('#replay-log')).to_contain_text('MOCK hardware')
                expect(page.locator('#replay-start')).to_be_disabled()
                expect(page.locator('#action-csv')).to_be_disabled()
                expect(page.locator('#replay-record-video')).to_be_disabled()
                expect(page.locator('#replay-recording-status')).to_contain_text('正在同时录制两个视角')
                assert len(adapter.launches)==1
                page.locator('#replay-stop').click()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','stopped')
                expect(page.locator('#replay-recording-status')).to_contain_text('双视角视频已保存')
                expect(page.locator('#replay-recording-files a')).to_have_count(2)
                first_recording=app.replay.status()['recording']['directory']
                video_url=page.locator('#replay-recording-files a').first.get_attribute('href')
                response=page.request.get(f'http://127.0.0.1:{server.server_port}'+video_url)
                assert response.ok and len(response.body())>100
                expect(page.locator('#recording-videos a')).to_have_text(['CSV自动录制.mp4'])
                assert len(adapter.stops)==1
                # Start a second explicit run and let the fake command stream complete.
                page.locator('#replay-confirm').check()
                page.locator('#replay-start').click()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','running')
                assert len(adapter.launches)==2
                adapter.launches[-1].done.set()
                expect(page.locator('#replay-status')).to_have_attribute('data-state','completed')
                expect(page.locator('#replay-status')).to_contain_text('未确认最终到位')
                expect(page.locator('#replay-recording-status')).to_contain_text('双视角视频已保存')
                assert app.replay.status()['recording']['directory']!=first_recording
                page.reload()
                expect(page.locator('#replay-record-video')).to_be_checked()
                expect(page.locator('#replay-recording-files a')).to_have_count(2)
                page.locator('#replay-record-video').uncheck()
                expect(page.locator('#recording-videos a')).to_have_text(['CSV自动录制.mp4','CSV自动录制_1.mp4'])
                page.locator('#action-csv').set_input_files({'name':'bad.csv','mimeType':'text/csv','buffer':b'bad\n1\n2\n'})
                expect(page.locator('#action-summary')).to_contain_text('转换失败')
                expect(page.locator('#replay-start')).to_be_disabled()
                # Authorization failures must block upload and clear the old job.
                uploads=[]
                page.on('request',lambda request:uploads.append(request.url) if request.url.endswith('/api/replay/upload') else None)
                page.route('**/api/status',lambda route:route.fulfill(status=403,json={'error':'仅允许配置的本机地址访问'}))
                page.locator('#action-csv').set_input_files({'name':'blocked.csv','mimeType':'text/csv','buffer':action_csv()})
                expect(page.locator('#action-summary')).to_contain_text('转换失败：仅允许配置的本机地址访问')
                expect(page.locator('#action-download')).to_be_hidden()
                expect(page.locator('#replay-start')).to_be_disabled()
                assert not uploads
                page.unroute('**/api/status')
                # Refresh execution capabilities too, rather than keeping a stale
                # enabled flag when the replacement server disables real replay.
                app.replay.token=secrets.token_urlsafe(32)
                app.replay.enabled=False
                page.locator('#action-csv').set_input_files({'name':'refreshed.csv','mimeType':'text/csv','buffer':action_csv()})
                expect(page.locator('#action-summary')).to_contain_text('离线校验通过')
                expect(page.locator('#replay-enabled')).to_contain_text('真实动作禁用')
                page.locator('#replay-confirm').check()
                expect(page.locator('#replay-start')).to_be_disabled()
                assert len(adapter.launches)==2
                assert not errors,errors
                browser.close()
                print('PASS: HTML5 CSV drop, token rotation, authorization rejection, refreshed capabilities, real conversion/dry-run, summary/download, explicit execute, single-run locking, fake logs/stop/completion, invalid CSV; no robot connected or JavaScript errors')
        finally:
            app.replay.close();server.shutdown();server.server_close();thread.join()
            if app.replay.thread:app.replay.thread.join(2)


if __name__=='__main__':
    main()
