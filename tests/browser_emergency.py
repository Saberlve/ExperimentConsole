"""Emergency control stays visible and bypasses busy forms; mocked stop endpoint."""
import tempfile
import threading
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from backend.server import Application
from backend.workbench import make_workbench_handler


def main():
    with tempfile.TemporaryDirectory() as directory:
        app=Application(dict(mode='demo', output_dir=directory, datasets={}, camera_preview_on_start=False))
        server=ThreadingHTTPServer(('127.0.0.1',0),make_workbench_handler(app));server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        control=dict(enabled=True,models={},jobs={},stopped=False,stopping=False,stop_detail='test')
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1280,height=720))
                errors=[];stops=[];pending=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/evaluation/control',lambda route:route.fulfill(json=control))
                page.route('**/api/evaluation/control/server',lambda route:pending.append(route))
                def stop(route):
                    stops.append(route.request.post_data_json)
                    route.fulfill(json=control)
                page.route('**/api/evaluation/control/stop',stop)
                page.goto(f'http://127.0.0.1:{server.server_port}/#vla')
                button=page.locator('#ve-emergency')
                for target in ['idm','wm','vla','checkpoints','training']:
                    page.evaluate('(hash)=>location.hash=hash',target)
                    expect(button).to_be_visible()
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    box=button.bounding_box()
                    assert 0<=box['y'] and box['y']+box['height']<=720
                page.set_viewport_size(dict(width=390,height=780))
                box=button.bounding_box()
                assert box['x']>=0 and box['x']+box['width']<=390
                page.evaluate("location.hash='vla'")
                page.locator('#ve-server-start').click()
                page.wait_for_function("document.querySelector('#ve-server-start')!==null")
                with page.expect_response(lambda response: response.url.endswith('/api/evaluation/control/stop')):
                    button.click()
                expect(button).to_be_enabled()
                assert len(stops)==1, 'Emergency request must bypass a pending ordinary operation'
                for route in pending:route.fulfill(json=control)
                page.evaluate("location.hash='training'")
                expect(page.locator('#training-stage')).to_be_visible()
                page.evaluate("document.querySelector('#tr-sourceDialog').showModal()")
                expect(page.locator('#tr-sourceDialog #ve-emergency')).to_be_visible()
                with page.expect_response(lambda response: response.url.endswith('/api/evaluation/control/stop')):
                    button.click()
                assert len(stops)==2
                page.evaluate("document.querySelector('#tr-sourceDialog').close()")
                expect(page.locator('body > #global-safety-bar #ve-emergency')).to_be_visible()
                assert not errors,errors
                assert not app.control.jobs
                browser.close()
                print('PASS: all pages, scroll, mobile visibility and emergency while another request is pending; no hardware')
        finally:
            server.shutdown();server.server_close();thread.join();app.control.close()


if __name__=='__main__':main()
