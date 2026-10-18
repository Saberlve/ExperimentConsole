"""Browser integration using isolated fixture datasets; never loads hardware."""
from pathlib import Path
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from backend.workbench import Application, CONSOLE, load_config, make_workbench_handler

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('training_fixture', repo / 'tests/test_training.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
fixture = fixtures.ExperimentManagerTest()
fixture.setUp()
fixture.configure_base_fixture()
root = Path(fixture.tmp.name)
archived = root / 'results/Checkpoints/ACT/browser_fixture'
(archived / 'checkpoints').mkdir(parents=True)
(archived / 'checkpoints/steps_25_model.safetensors').write_bytes(b'fixture; never loaded')
(archived / 'config.yaml').write_text('framework:\n  name: ACT\n')
(archived / 'dataset_statistics.json').write_text(json.dumps({'fixture': {'action': {'mean': [0]}}}))
config = load_config(CONSOLE / 'config/default.json', str(root / 'evaluation'))
config['camera_preview_on_start'] = False
app = Application(config)
server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(app, fixture.manager))
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440, 'height':1050})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:' + str(server.server_port), wait_until='networkidle')
        page.locator('#training-stage').wait_for(state='visible')
        page.wait_for_function('document.querySelectorAll("#tr-recipe option").length>0')
        page.locator('#tr-horizon').fill('15')
        page.locator('#tr-preview').click()
        page.locator('#tr-config').wait_for(state='visible')
        assert 'results/Checkpoints/ACT' in page.locator('#tr-config').inner_text()
        assert 'chunk_size: 15' in page.locator('#tr-config').inner_text()
        assert 'action_horizon: 15' in page.locator('#tr-config').inner_text()
        page.locator('#tr-eid').fill('browser_fixture')
        page.locator('#tr-save').click()
        page.wait_for_function('document.querySelector("#tr-configState").textContent==="已保存快照"')
        assert (fixture.root / 'saved/browser_fixture/config.yaml').is_file()
        assert '--nproc_per_node=1' in page.locator('#tr-command').inner_text()
        page.locator('#tr-horizon').fill('20')
        assert 'chunk_size: 15' in (fixture.root / 'saved/browser_fixture/config.yaml').read_text()
        page.locator('#tr-tabSaved').click()
        page.locator('#tr-saved').wait_for(state='visible')
        page.get_by_role('button', name='查看', exact=True).click()
        page.locator('#tr-config').wait_for(state='visible')
        page.locator('#tr-tabSources').click()
        page.locator('#tr-sources').wait_for(state='visible')
        page.locator('[data-workspace=checkpoints]').click()
        page.locator('#checkpoints-stage').wait_for(state='visible')
        page.wait_for_function('document.querySelectorAll(".model-card").length===4')
        page.locator('[data-model=N0]').click()
        assert 'N0' in page.locator('#cp-records').inner_text()
        page.locator('[data-model=ACT]').click()
        page.get_by_role('button', name='用于真机评测 →', exact=True).click()
        page.locator('#vla-evaluation').wait_for(state='visible')
        assert page.locator('#ve-checkpoint').input_value() == str(archived / 'checkpoints/steps_25_model.safetensors')
        assert page.locator('#ve-model').input_value() == 'ACT'
        page.locator('[data-page=vla]').click()
        page.locator('#vla-evaluation').wait_for(state='visible')
        page.locator('#ve-checkpoint-select').wait_for(state='visible')
        page.locator('[data-page=wm]').click()
        page.locator('#vla-evaluation').wait_for(state='hidden')
        page.locator('[data-page=idm]').click()
        page.locator('#idm-panel').wait_for(state='visible')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('[data-workspace=training]').click()
        page.locator('#tr-tabEditor').click()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors, errors
        assert page.evaluate('getComputedStyle(document.documentElement).colorScheme') == 'light'
        browser.close()
    print('Browser preview/save/reload, checkpoint handoff, five sections, light theme and mobile layout: PASS')
finally:
    server.shutdown(); server.server_close(); thread.join(); fixture.tmp.cleanup()
