import test from 'node:test';
import assert from 'node:assert/strict';
import {VLAEvaluation} from '../frontend/evaluation.mjs';

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
