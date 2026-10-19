"""Refresh recovery and visible launch failures/output, with no real processes."""
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from backend.server import Application
from backend.workbench import make_workbench_handler
from tests.test_console import png


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = Application(dict(mode='demo', output_dir=directory, datasets={}, camera_preview_on_start=False))
        image = app.upload(png())
        suite = app.evaluation.create_suite(dict(task='egg', name='saved', layouts=[dict(url=image['url'], label='one')]))
        run = app.evaluation.create_run(dict(suite_id=suite['id'], model='ACT', checkpoint='fixture', round='resume', eval_config_content='fps: 15\n'))
        control = dict(enabled=True, models={}, jobs={'server': dict(running=True, ready=True, log='server listening on 127.0.0.1:10093', model='ACT', checkpoint='fixture')}, stopped=False, stopping=False, stop_detail='test')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.route('**/api/evaluation/control', lambda route: route.fulfill(json=control))
                launches = []
                def launch(route):
                    launches.append(route.request.post_data_json)
                    route.fulfill(status=400, json={'error': 'fixture: config validation failed'})
                page.route('**/api/evaluation/start-trial', launch)
                page.goto(f'http://127.0.0.1:{server.server_port}/#vla')
                page.locator('#ve-load').click()
                expect(page.locator('#ve-current')).to_be_visible()
                expect(page.locator('#ve-resume-config-host #ve-config-editor')).to_be_visible()
                expect(page.locator('#ve-config-editor-title')).to_have_text('编辑当前轮次配置')
                assert not page.locator('#ve-config-editor').evaluate('(editor)=>editor.open')
                expect(page.locator('#ve-config-content')).not_to_be_visible()
                control['jobs']['server']['ready'] = False
                expect(page.locator('#ve-eval-start')).to_be_disabled()
                expect(page.locator('#ve-trial-status')).to_contain_text('模型正在加载')
                control['jobs']['server']['ready'] = True
                expect(page.locator('#ve-eval-start')).to_be_enabled()
                expect(page.locator('#ve-config-content')).to_be_enabled()
                page.locator('#ve-eval-start').click()
                expect(page.locator('#ve-trial-status')).to_contain_text('启动失败')
                expect(page.locator('#ve-trial-log')).to_contain_text('fixture: config validation failed')
                page.evaluate("sessionStorage.removeItem('vla-evaluation-session')")
                page.reload()
                expect(page.locator('#ve-current')).to_be_visible()
                expect(page.locator('#ve-round')).to_have_value('resume')
                page.reload()
                expect(page.locator('#ve-current')).to_be_visible()
                expect(page.locator('#ve-current-label')).to_contain_text('第 1/1 张')
                expect(page.locator('#ve-round')).to_have_value('resume')
                expect(page.locator('#ve-trial-log')).to_contain_text('fixture: config validation failed')
                page.locator('#ve-manual-controls').evaluate('(el)=>el.open=true')
                page.locator('#ve-manual-start').click()
                expect(page.locator('#ve-manual-result')).to_be_enabled()
                page.locator('#ve-note').fill('unfinished note survives refresh')
                page.locator('#ve-trial-result').select_option('success')
                page.reload()
                expect(page.locator('#ve-current')).to_be_visible()
                expect(page.locator('#ve-manual-controls')).to_have_attribute('open', '')
                expect(page.locator('#ve-note')).to_have_value('unfinished note survives refresh')
                expect(page.locator('#ve-trial-result')).to_have_value('success')
                # Recreate owned client state in the temporary fixture; never start a process.
                record = app.evaluation.read('runs', run['id'])
                record['trials'][0]['log_path'] = '/tmp/fixture-client.log'
                app.evaluation.write('runs', record)
                control['jobs']['evaluation'] = dict(run_id=run['id'], running=True, exit_code=None, log='phase: reset complete\nphase: connecting policy')
                page.reload()
                expect(page.locator('#ve-trial-log')).to_contain_text('phase: connecting policy')
                # A previous launch failure must not hide a subsequently active client.
                expect(page.locator('#ve-trial-status')).to_contain_text('正在运行')
                control['jobs']['evaluation'].update(running=False, exit_code=7, log='client failed: fixture')
                expect(page.locator('#ve-trial-status')).to_contain_text('退出码：7')
                expect(page.locator('#ve-trial-log')).to_contain_text('client failed: fixture')
                # Background recovery must update an already-open page and
                # restore alignment without a manual reload or a new suite.
                record = app.evaluation.read('runs', run['id'])
                trial = record['trials'][0]
                trial.update(status='pending', started_at=None, finished_at=None,
                    start_error='评测客户端异常退出；请重试', failed_attempts=[dict(
                        exit_code=7, log_path='/tmp/fixture-client.log', log_tail='client failed: fixture')])
                trial.pop('log_path', None)
                record['revision'] += 1
                app.evaluation.write('runs', record)
                control['jobs']['evaluation'].update(recovered=True, log_path='/tmp/fixture-client.log', trial_index=0)
                expect(page.locator('#ve-eval-start')).to_have_text('重试本条评测（自动释放相机）')
                expect(page.locator('#ve-eval-start')).to_be_enabled()
                expect(page.locator('#ve-current-label')).to_contain_text('待测')
                expect(page.locator('#ve-trial-end')).to_be_enabled()
                expect(page.locator('#ve-trial-result')).to_be_enabled()
                expect(page.locator('#ve-note')).to_be_enabled()
                page.wait_for_function("document.querySelector('#live').naturalWidth>0")
                expect(page.locator('#ve-trial-log')).to_contain_text('client failed: fixture')
                page.locator('#ve-config-content').evaluate('editor=>{for(let node=editor.parentElement;node;node=node.parentElement)if(node.tagName==="DETAILS")node.open=true;}')
                page.locator('#ve-config-content').fill('fps: 12\n')
                page.locator('#ve-eval-start').click()
                expect(page.locator('#ve-trial-status')).to_contain_text('fixture: config validation failed')
                assert launches[-1]['eval_config_content'] == 'fps: 12\n'
                # A recovered failed trial can be ended and scored without
                # re-running the client or touching a robot.
                finishes = []
                def finish(route):
                    payload = route.request.post_data_json
                    finishes.append(payload)
                    saved = app.evaluation.transition(dict(payload, action='result'))
                    route.fulfill(json=app.evaluation.save_results(saved['id']))
                page.route('**/api/evaluation/finish-trial', finish)
                page.locator('#ve-trial-result').select_option('failure')
                page.locator('#ve-note').fill('record failure after inference error')
                page.reload()
                expect(page.locator('#ve-trial-end')).to_be_enabled()
                expect(page.locator('#ve-trial-result')).to_have_value('failure')
                expect(page.locator('#ve-note')).to_have_value('record failure after inference error')
                page.locator('#ve-trial-end').click()
                expect(page.locator('#ve-progress')).to_contain_text('已判定 1')
                assert finishes[-1]['result'] == 'failure'
                assert app.evaluation.read('runs', run['id'])['trials'][0]['status'] == 'failure'
                # Correct the saved outcome in place, without another finish or
                # launch request, and keep the selected layout unchanged.
                page.locator('#ve-result-editor').evaluate('(el)=>el.open=true')
                expect(page.locator('#ve-corrected-result')).to_have_value('failure')
                page.locator('#ve-corrected-result').select_option('success')
                page.locator('#ve-corrected-note').fill('corrected accidental failure label')
                page.locator('#ve-save-result').click()
                expect(page.locator('#ve-progress')).to_contain_text('成功 1 / 已判定 1')
                expect(page.locator('#ve-current-label')).to_contain_text('成功')
                updated = app.evaluation.read('runs', run['id'])
                assert updated['cursor'] == 0
                assert updated['trials'][0]['status'] == 'success'
                assert updated['trials'][0]['note'] == 'corrected accidental failure label'
                assert len(finishes) == 1
                assert len(launches) == 2
                page.reload()
                expect(page.locator('#ve-progress')).to_contain_text('成功 1 / 已判定 1')
                assert not app.control.jobs
                assert not errors, errors
                browser.close()
                print('PASS: launch errors/logs, run and note refresh recovery, client output and exit status; no robot commands')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            app.control.close()


if __name__ == '__main__':
    main()
