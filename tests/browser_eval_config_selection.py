"""Hardware-free browser regression for model/task config selection."""
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    source = (Path(__file__).resolve().parents[1] / 'frontend/evaluation.mjs').read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content('''<select id="ve-model"><option>ACT</option><option>N0</option></select>
            <select id="ve-task-filter"><option>egg_pick</option><option>chip_pick</option><option>unknown</option><option>charge_press</option><option>charge_insert</option></select>
            <select id="ve-config"></select><select id="ve-checkpoint"><option value="chosen">chosen</option></select>''')
        page.add_script_tag(content=source.replace('export class VLAEvaluation', 'window.VLAEvaluation = class VLAEvaluation'))
        page.evaluate('''() => {
            const view=Object.create(window.VLAEvaluation.prototype);
            const root='/configs';
            const profile=model=>({eval_config:root+'/legacy.yaml',eval_config_root:root,
                eval_config_catalog:['egg_pick','chip_pick','charger_press','charger_insert'].map(task=>({
                    path:`${root}/${model}/${task}/${model}_${task}.yaml`,directory:`${model}/${task}`,task,model})),
                eval_configs:['egg_pick','chip_pick','charger_press','charger_insert'].map(task=>`${root}/${model}/${task}/${model}_${task}.yaml`)});
            view.controlState={models:{ACT:profile('ACT'),N0:profile('N0')}};
            view.catalog={runs:[]};view.roundOptions=()=>{};view.previewConfig=()=>{};
            window.view=view;
        }''')
        def choose(model, task, expected):
            page.select_option('#ve-model', model)
            page.select_option('#ve-task-filter', task)
            page.evaluate('view.controlDefaults({resetCheckpoint:false})')
            assert page.input_value('#ve-config') == expected
            assert page.input_value('#ve-checkpoint') == 'chosen'
        choose('ACT','egg_pick','/configs/ACT/egg_pick/ACT_egg_pick.yaml')
        choose('ACT','chip_pick','/configs/ACT/chip_pick/ACT_chip_pick.yaml')
        choose('N0','chip_pick','/configs/N0/chip_pick/N0_chip_pick.yaml')
        assert page.locator('optgroup[label="N0 / chip_pick"]').count() == 1
        choose('N0','charge_press','/configs/N0/charger_press/N0_charger_press.yaml')
        choose('ACT','charge_insert','/configs/ACT/charger_insert/ACT_charger_insert.yaml')
        choose('N0','unknown','')
        page.evaluate("view.run={model:'N0',task:'egg_pick',eval_config:'/runs/saved.yaml'}")
        choose('N0','egg_pick','/runs/saved.yaml')
        browser.close()
    print('PASS: task/model auto-selection, directory grouping, fallback, checkpoint and saved-round preservation')


if __name__ == '__main__':
    main()
