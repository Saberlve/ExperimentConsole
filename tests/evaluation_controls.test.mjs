import test from 'node:test';
import assert from 'node:assert/strict';
import {VLAEvaluation} from '../frontend/evaluation.mjs';

test('finishing the last trial clears the session and does not reopen alignment cameras',async()=>{
  const previousDocument=globalThis.document,previousEvent=globalThis.CustomEvent;
  try{
    for(const exited of [false,true]){
      const elements=new Map(),element=id=>{
        if(!elements.has(id))elements.set(id,{value:id==='ve-trial-result'?'failure':'final note'});
        return elements.get(id);
      };
      globalThis.document={getElementById:element,dispatchEvent(){}};
      globalThis.CustomEvent=class{constructor(type){this.type=type;}};
      const view=Object.create(VLAEvaluation.prototype),calls=[],messages=[];
      const record={id:'run',revision:4,task:'egg',suite_id:'suite',cursor:0,
        trials:[{status:'running'},...(!exited?[{status:'pending'}]:[])],
        metrics:{pending:exited?0:1},result_files:{json:'results.json',csv:'results.csv'}};
      Object.assign(view,{run:record,hadModel:true,cameraGeneration:0,status:{mode:'legacy_http'},
        async api(path,payload){calls.push([path,payload]);return {...record,evaluation_exited:exited};},
        async load(){},selectRun(){calls.push('select');},async openCamera(){calls.push('camera');},
        clearSession(){calls.push('clear');},async pollControl(){},message(text){messages.push(text);}});
      await view.finishRun(false);
      assert.equal(calls[0][0],'/api/evaluation/finish-trial');
      assert.equal(calls[0][1].result,'failure');
      assert.equal(calls[0][1].note,'final note');
      if(exited){
        assert.equal(view.run,null);
        assert.equal(view.hadModel,false);
        assert.equal(element('ve-setup').open,true);
        assert.equal(element('ve-current').hidden,true);
        assert.ok(calls.includes('clear'));
        assert.ok(!calls.includes('camera'));
        assert.ok(!calls.includes('select'));
        assert.match(messages.at(-1),/已退出评测.*results.json.*results.csv/);
      }else{
        assert.ok(view.run);
        assert.ok(calls.includes('camera'));
        assert.ok(calls.includes('select'));
        assert.ok(!calls.includes('clear'));
      }
    }
  }finally{globalThis.document=previousDocument;globalThis.CustomEvent=previousEvent;}
});

test('the final remaining layout is determined by results rather than its index',()=>{
  const view=Object.create(VLAEvaluation.prototype);
  view.run={cursor:1,trials:[{status:'pending'},{status:'running'}]};
  assert.equal(view.isFinalTrial(),false);
  view.run={cursor:0,trials:[{status:'running'},{status:'aborted'}]};
  assert.equal(view.isFinalTrial(),true);
  view.run=null;
  assert.equal(view.isFinalTrial(),false);
});

test('nested model task YAMLs appear in directory groups alongside saved rounds',()=>{
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:'',options:[],
    replaceChildren(...options){this.children=options;this.options=options.flatMap(option=>option.options||[option]);},add(option){this.options.push(option);}});return elements.get(id);};
  const previousDocument=globalThis.document,previousOption=globalThis.Option;
  globalThis.document={getElementById:element,createElement(){return {options:[],append(option){this.options.push(option);}};}};
  globalThis.Option=class{
    constructor(text,value){this.text=text;this.value=value;this.classList={add(){}};}
  };
  try{
    const root='/repo/config/eval/FTP1',egg=root+'/egg_pick/FTP1_egg_pick.yaml',
      charger=root+'/charger_press/FTP1_charger_press.yaml',saved='/output/runs/round.yaml';
    element('ve-model').value='FTP1';
    const view=Object.create(VLAEvaluation.prototype);
    Object.assign(view,{controlState:{models:{FTP1:{eval_config:egg,eval_config_root:root,eval_configs:[charger,egg]}}},
      catalog:{runs:[{model:'FTP1',eval_config:saved}]},roundOptions(){},previewConfig(){}});
    view.controlDefaults();
    const groups=element('ve-config').children;
    const options=element('ve-config').options;
    assert.ok(groups.some(group=>group.label==='charger_press'));
    assert.ok(groups.some(group=>group.label==='egg_pick'));
    assert.ok(groups.some(group=>group.label==='已保存配置'));
    assert.equal(options.find(o=>o.value===charger).text,'FTP1_charger_press.yaml');
    assert.equal(options.find(o=>o.value===egg).text,'FTP1_egg_pick.yaml');
    assert.equal(options.find(o=>o.value===saved).text,'round.yaml');
    assert.equal(options.filter(o=>o.value===egg).length,1);
    assert.equal(element('ve-config').value,egg);
  }finally{globalThis.document=previousDocument;globalThis.Option=previousOption;}
});

test('edited running trial can restart, while stop/reset and other jobs still block it',()=>{
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:''});return elements.get(id);};
  const previous=globalThis.document;
  globalThis.document={getElementById:element,querySelectorAll:()=>[]};
  try{
    const view=Object.create(VLAEvaluation.prototype);
    Object.assign(view,{placeConfigEditor(){},renderResultEditor(){},renderTrialLog(){},
      run:{id:'run',cursor:0,eval_config:'old.yaml',metrics:{},trials:[{status:'running'}]},
      controlState:{enabled:true,jobs:{server:{running:true,ready:true},
        evaluation:{running:true,run_id:'run',trial_index:0}}},configDirty:false});
    view.updateTrialControls();
    assert.equal(element('ve-eval-start').disabled,true);
    view.configDirty=true;
    view.updateTrialControls();
    assert.equal(element('ve-eval-start').disabled,false);
    assert.equal(element('ve-eval-start').textContent,'应用修改并重启本条评测');
    view.controlState.stopped=true;
    view.updateTrialControls();
    assert.equal(element('ve-eval-start').disabled,true);
    view.controlState.stopped=false;
    view.controlState.jobs.reset={running:true};
    view.updateTrialControls();
    assert.equal(element('ve-eval-start').disabled,true);
    view.controlState.jobs.reset.running=false;
    view.controlState.jobs.evaluation.run_id='other';
    view.updateTrialControls();
    assert.equal(element('ve-eval-start').disabled,true);
  }finally{globalThis.document=previous;}
});


test('video switch applies to one trial without editing YAML or carrying into the next trial',()=>{
  const oldDocument=globalThis.document,elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false});return elements.get(id);};
  globalThis.document={getElementById:element};
  try{
    const view=Object.create(VLAEvaluation.prototype);
    view.run={id:'run',cursor:0,trials:[{status:'pending'},{status:'pending'}]};
    const yaml='fps: 15\n';element('ve-config-content').value=yaml;
    let edits=0;element('ve-config-content').oninput=()=>{edits++;};
    view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,false);
    view.setVideoRecording(true);
    assert.equal(element('ve-record-video').checked,true);
    assert.equal(element('ve-config-content').value,yaml);assert.equal(edits,0);
    view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,true);
    view.run.cursor=1;view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,false);
    // A configured default can also be overridden for a single trial.
    view.configVideoDefault=true;view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,true);
    view.setVideoRecording(false);assert.equal(element('ve-record-video').checked,false);
    view.run.cursor=0;view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,true);
    // Refreshing a running trial reflects its actual launch setting.
    view.run.trials[0]={status:'running',record_video:false};
    view.syncVideoSwitch();assert.equal(element('ve-record-video').checked,false);
  }finally{globalThis.document=oldDocument;}
});
