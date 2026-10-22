import test from 'node:test';
import assert from 'node:assert/strict';
import {VLAEvaluation} from '../frontend/evaluation.mjs';

test('nested model task YAMLs appear as base templates alongside saved rounds',()=>{
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:'',options:[],
    replaceChildren(...options){this.options=options;},add(option){this.options.push(option);}});return elements.get(id);};
  const previousDocument=globalThis.document,previousOption=globalThis.Option;
  globalThis.document={getElementById:element};
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
    const options=element('ve-config').options;
    assert.equal(options.find(o=>o.value===charger).text,'BASE · charger_press/FTP1_charger_press.yaml');
    assert.equal(options.find(o=>o.value===egg).text,'BASE · egg_pick/FTP1_egg_pick.yaml');
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
