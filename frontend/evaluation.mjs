const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Highlight YAML without changing the editable source text.
function yamlMarkup(source){
  let blockIndent=null;
  return source.split('\n').map(line=>{
    const indent=line.match(/^ */)[0].length;
    if(blockIndent!==null){
      if(!line.trim()||indent>blockIndent)return `<span class="yaml-string">${esc(line)}</span>`;
      blockIndent=null;
    }
    const tokens=/("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|#[^\n]*|[^\s:{}\[\],#'" ]+(?=\s*:(?:\s|$))|\b(?:true|false|null|yes|no|on|off)\b|(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![\w.])|[{}\[\],:|>&*!-])/g;
    let html='',cursor=0,match;
    while((match=tokens.exec(line))){
      html+=esc(line.slice(cursor,match.index));
      const token=match[0],tail=line.slice(tokens.lastIndex);
      const kind=token.startsWith('#')?'comment':/^\s*:/.test(tail)?'key':/^["']/.test(token)?'string':/^(true|false|null|yes|no|on|off)$/.test(token)?'literal':/^[-+]?(?:\d|\.\d)/.test(token)?'number':'punctuation';
      html+=`<span class="yaml-${kind}">${esc(token)}</span>`;cursor=tokens.lastIndex;
    }
    if(/:\s*[|>][-+0-9]*\s*(?:#.*)?$/.test(line))blockIndent=indent;
    return html+esc(line.slice(cursor));
  }).join('\n');
}
const labels={pending:'待测',running:'已标记开始',success:'成功',failure:'失败',aborted:'中止（不计入成功率）'};
export class VLAEvaluation {
  constructor(reference,status,message){
    this.reference=reference;this.status=status;this.message=message;this.draft=[];this.run=null;this.busy=false;
    $('vla-evaluation').innerHTML=`<h2>VLA 真机评测</h2>
<div class="ve-workflow"><ol aria-label="评测操作顺序"><li data-eval-step="1">1 选择测试集与模型</li><li data-eval-step="2">2 启动模型服务</li><li data-eval-step="3">3 对齐布局并评测</li><li data-eval-step="4">4 保存结果，下一张</li></ol><p id="ve-next-action" role="status">继续上次评测：在下方选择任务和已有轮次，点击“继续选中的评测”。新模型或新 checkpoint 请新建轮次。</p></div>
<p id="ve-session-status" role="status">正在连接评测会话…</p><p>每个任务保存独立布局测试集；不同模型和 checkpoint 使用同一测试集，各自新建评测轮次。可在下方启动模型服务和评测，也可沿用终端。</p>

<section id="ve-resume" class="ve-step"><h3>1 · 继续已有评测</h3><p>选择上次的轮次，恢复已保存的进度、模型和配置。继续按钮不会启动机械臂。</p><div class="ve-grid"><label>任务<select id="ve-task-filter"></select></label><label>已有评测轮次<select id="ve-runs"></select></label></div><button id="ve-load" class="primary">继续选中的评测</button></section>
<details id="ve-setup" class="ve-step" open><summary>或：新建评测轮次（新模型 / 新 checkpoint）</summary><p>已有测试集直接选择；首次使用可在左侧拍摄并保存布局测试集。选好模型、检查点和 YAML 后，点击“保存配置并新建评测轮次”。</p>
<label>布局测试集<select id="ve-suite"></select></label><p id="ve-suite-info"></p><button id="ve-edit-suite">加载此 suite 补拍 / 编辑</button><details id="ve-suite-images" hidden><summary id="ve-suite-images-title">布局图片</summary><div id="ve-suite-preview"></div></details>
<p id="ve-checkpoint-root"></p><button id="ve-checkpoint-refresh">刷新检查点列表</button>
<div class="ve-grid"><label>模型名称<select id="ve-model"><option value="pi05">π05</option><option value="ACT">ACT</option><option value="FTP1">FTP1</option><option value="N0">N0</option></select></label><label>检查点<select id="ve-checkpoint"><option value="">请选择检查点</option></select></label><label>轮次名称<input id="ve-round" list="ve-round-options" placeholder="输入轮次名称，例如 round-1" maxlength="256"><datalist id="ve-round-options"></datalist></label><label>评测配置<select id="ve-config"><option value="">请选择评测 YAML</option></select></label></div>
<label>手动填写 checkpoint（终端评测 / 离线记录）<input id="ve-manual-checkpoint" placeholder="填写 checkpoint 路径或标识"></label>
<details><summary>查看 / 修改评测 YAML（可选）</summary><p id="ve-config-path"></p><div class="ve-code-editor"><div class="ve-code-gutter" aria-hidden="true"><pre id="ve-config-lines">1</pre></div><div class="ve-code-body"><pre id="ve-config-highlight" aria-hidden="true"></pre><textarea id="ve-config-content" aria-label="评测 YAML 编辑器" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off" disabled></textarea></div></div><p id="ve-config-state">请选择评测配置。</p><p>创建轮次时，编辑内容自动保存到该轮结果目录并用于评测，原文件保持不变。</p></details>
<button id="ve-create-run" class="primary">保存配置并新建评测轮次</button>
</details><section id="ve-process"><h3>2 · 启动模型服务</h3><p>完成第 1 步后启动服务；然后在第 3 步对齐布局并开始评测。</p><div class="ve-actions"><button id="ve-server-start">启动模型服务</button><button id="ve-server-stop">停止模型服务</button><button id="ve-emergency" style="background:#b91c1c;color:white;font-weight:bold">急停 / 停止评测</button><button id="ve-unlock">已人工检查，解除启动锁定</button></div><p>软件急停发送控制器停止指令；网络或控制器异常时请使用实体急停。解除锁定不会使机器人运动。</p><p id="ve-process-status"></p><details><summary>启动日志</summary><pre id="ve-process-log" style="white-space:pre-wrap;max-height:260px;overflow:auto"></pre></details></section><section id="ve-current" class="ve-step" hidden><h3>3 · 对齐布局，启动本条评测</h3><p id="ve-run-meta"></p><strong id="ve-progress"></strong><p id="ve-current-label"></p><img id="ve-layout" alt="当前样本的固定布局图" style="max-width:100%;max-height:420px"><p id="ve-instructions"></p><p>点击左侧“开启 / 刷新相机”，按参考图摆好物体。“启动本条评测”会自动释放预览相机。</p><div id="ve-alignment"></div><button id="ve-eval-start" class="primary">启动本条评测（自动释放相机）</button><section class="ve-trial-console"><h3>本条评测 · 状态与日志</h3><p id="ve-trial-status" role="status">尚未启动评测。</p><pre id="ve-trial-log" tabindex="0" aria-label="本条评测日志">等待启动。</pre></section><h3>4 · 结束后记录结果</h3><label>本样本备注 / 日志路径<textarea id="ve-note"></textarea></label>
<label>本条结果<select id="ve-trial-result"><option value="success">成功</option><option value="failure">失败</option><option value="aborted" selected>中止</option></select></label><div class="ve-actions"><button id="ve-trial-end">结束本条（复位并下一张）</button><button id="ve-run-exit">退出并保存评测结果</button></div>
<details id="ve-manual-controls"><summary>在终端运行 VLA / 仅手动记录结果</summary><p>先点击左侧“释放相机供 VLA 使用”，再在终端运行评测。手动标记开始 → 仅保存本条结果 → 继续下一张。手动记录不会停止或复位机械臂；请在终端完成控制操作。</p><div class="ve-actions"><button id="ve-manual-start">手动标记开始</button><button id="ve-manual-result">仅保存本条结果</button><button id="ve-next">继续下一张（不复位）</button></div></details>
<p><a id="ve-json" target="_blank">导出 JSON</a> · <a id="ve-csv">导出 CSV</a></p><div id="ve-trials"></div></section>
`;
    $('ve-suite-name').addEventListener('input',()=>this.builderProgress());
    $('ve-target-count').addEventListener('input',()=>this.builderProgress());
    this.bind('ve-new-suite',()=>{this.editingSuite=null;this.draft=[];$('ve-suite-name').value='';$('ve-target-count').value=15;this.renderDraft();});
    this.bind('ve-edit-suite',()=>this.editSuite());
    $('ve-manual-checkpoint').onchange=()=>{this.setChoice('ve-checkpoint',$('ve-manual-checkpoint').value.trim());this.roundOptions();};
    this.bind('ve-manual-start',()=>this.manualTransition('start'));
    this.bind('ve-manual-result',()=>this.manualTransition('result'));
    this.bind('ve-next',()=>this.manualTransition('next'));
    this.cameraGeneration=0;
    const controlPayload=()=>({model:$('ve-model').value,checkpoint:$('ve-checkpoint').value,eval_config:$('ve-config').value,run_id:this.run?.id,revision:this.run?.revision});
    for(const [id,endpoint] of [['ve-server-start','server'],['ve-server-stop','stop-server'],['ve-unlock','unlock']])this.bind(id,async()=>{await this.api('/api/evaluation/control/'+endpoint,controlPayload());await this.pollControl();});
    // Emergency remains independent of the ordinary form busy guard.
    $('ve-emergency').onclick=async()=>{try{await this.api('/api/evaluation/control/stop',{});}catch(e){this.message(e.message,true);}finally{await this.pollControl();}};
    $('ve-model').onchange=()=>this.controlDefaults();
    $('ve-config').addEventListener('change',()=>this.previewConfig());
    $('ve-config-content').addEventListener('scroll',()=>this.syncConfigScroll());
    $('ve-config-content').oninput=()=>{this.renderConfigCode();this.configDirty=$('ve-config-content').value!==this.configOriginal;$('ve-config-state').textContent=this.configDirty?'已修改，将随新轮次保存':'';};
    window.addEventListener('beforeunload',event=>{if(this.configDirty||Object.keys(this.configDrafts||{}).length){event.preventDefault();event.returnValue='';}});
    $('ve-checkpoint').addEventListener('change',()=>this.roundOptions());
    this.pollControl().catch(e=>this.message(e.message,true));
    this.bind('ve-save-suite',async()=>{
      const payload={task:$('ve-task').value,name:$('ve-suite-name').value,target_count:Number($('ve-target-count').value),layouts:this.draft};
      if(this.editingSuite)Object.assign(payload,{suite_id:this.editingSuite.id,revision:this.editingSuite.revision});else payload.directory_name=payload.name;
      const suite=await this.api('/api/evaluation/suites'+(this.editingSuite?'/update':''),payload);
      this.editingSuite={id:suite.id,revision:suite.revision||0};this.draft=suite.layouts.map(t=>({...t}));this.renderDraft();
      await this.load(suite.task,suite.id);this.message(`suite 已保存：${suite.name}，${suite.layouts.length}/${suite.target_count||15} 张。`);
    });
    this.bind('ve-create-run',async()=>{if($('ve-config').value&&($('ve-config-content').disabled||this.configEditorPath!==$('ve-config').value))throw Error('请等待配置加载完成');this.run=await this.api('/api/evaluation/runs',{suite_id:$('ve-suite').value,model:$('ve-model').value,checkpoint:$('ve-checkpoint').value,round:$('ve-round').value,eval_config:$('ve-config').value,...($('ve-config').value?{eval_config_content:$('ve-config-content').value}:{})});this.configDirty=false;delete (this.configDrafts||{})[this.configEditorPath];await this.load(this.run.task,this.run.suite_id);this.selectRun();if(status.mode!=='reference_only')await this.openCamera();});
    this.bind('ve-load',async()=>{if(!$('ve-runs').value)throw Error('请选择评测轮次');this.run=await this.api('/api/evaluation/run?id='+encodeURIComponent($('ve-runs').value));this.selectRun();if(status.mode!=='reference_only'&&this.run.trials[this.run.cursor].status==='pending')await this.openCamera();});
    this.bind('ve-eval-start',async()=>{
      this.launchPending=true;this.launchStarted=Date.now();this.launchError='';
      this.trialEvent('收到启动操作，检查当前轮次和配置。');this.renderTrialLog();this.updateTrialControls();
      const ticker=setInterval(()=>this.renderTrialLog(),1000);
      try{
        this.requireSavedConfig();if(!this.run)throw Error('请先新建或加载评测轮次');
        ++this.cameraGeneration;document.dispatchEvent(new CustomEvent('console-camera-paused'));
        this.trialEvent('已暂停浏览器预览；向服务端请求校验配置、释放相机并启动评测客户端。');
        this.message('正在请求启动评测，请查看按钮下方的状态与日志…');
        this.run=await this.api('/api/evaluation/start-trial',{run_id:this.run.id,revision:this.run.revision});
        this.trialEvent('服务端已接受启动，正在读取客户端状态和输出。');
        await this.load(this.run.task,this.run.suite_id);this.selectRun();
      }catch(e){
        this.launchError=e.message;this.trialEvent('启动失败：'+e.message);
        try{await this.reloadRun();}catch(reloadError){this.trialEvent('刷新评测记录失败：'+reloadError.message);}
        throw e;
      }finally{clearInterval(ticker);this.launchPending=false;this.renderTrialLog();this.updateTrialControls();await this.pollControl();}
    });
    this.bind('ve-trial-end',()=>this.finishRun(false));
    this.bind('ve-run-exit',()=>this.finishRun(true));
    $('ve-task-filter').onchange=()=>{$('ve-setup').open=true;this.run=null;this.clearSession();$('ve-current').hidden=true;this.renderTask();this.roundOptions();};
    $('ve-suite').onchange=()=>this.suiteInfo();
    $('ve-suite-preview').onclick=e=>{const button=e.target.closest('[data-layout-index]');if(!button)return;const suite=this.catalog.suites.find(s=>s.id===$('ve-suite').value),layout=suite?.layouts[Number(button.dataset.layoutIndex)];if(layout)this.useLayoutReference(suite,layout);};
    $('ve-draft').onclick=e=>{if(this.busy)return;const b=e.target.closest('button');if(!b)return;const i=Number(b.dataset.index);if(b.dataset.action==='delete')this.draft.splice(i,1);else {const j=i+(b.dataset.action==='up'?-1:1);if(j>=0&&j<this.draft.length)[this.draft[i],this.draft[j]]=[this.draft[j],this.draft[i]];}this.renderDraft();};
    $('ve-manual-controls').addEventListener('toggle',()=>this.saveSession());
    $('ve-note').addEventListener('input',()=>this.saveSession());
    $('ve-trial-result').addEventListener('change',()=>this.saveSession());
    this.renderDraft();
  }
  async editSuite(){
    const id=$('ve-suite').value;
    const catalog=await this.api('/api/evaluation/catalog');
    const suite=catalog.suites.find(s=>s.id===id);
    if(!suite)throw Error('请选择已有 suite');
    if(catalog.runs.some(r=>r.suite_id===id))throw Error('已有评测轮次使用此 suite，请新建 suite，以保留原评测布局');
    this.editingSuite={id:suite.id,revision:suite.revision||0};
    this.draft=suite.layouts.map(t=>({...t}));
    $('ve-task').value=suite.task;$('ve-suite-name').value=suite.name;
    $('ve-target-count').value=suite.target_count||Math.max(15,suite.layouts.length);
    this.renderDraft();this.message('已加载 suite，可继续补拍或调整布局后保存。');
  }
  async manualTransition(action){
    if(!this.run)throw Error('请先加载评测轮次');
    if(action==='start'){++this.cameraGeneration;document.dispatchEvent(new CustomEvent('console-camera-paused'));}
    try{
      this.run=await this.api('/api/evaluation/transition',{run_id:this.run.id,revision:this.run.revision,action,result:$('ve-trial-result').value,note:$('ve-note').value});
      await this.load(this.run.task,this.run.suite_id);this.selectRun();
      this.message(action==='next'?'已切换下一张，请确认机械臂状态并摆好布局。':'手动记录已保存。');
    }catch(e){await this.reloadRun();throw e;}finally{await this.pollControl();}
  }
  async reloadRun(){if(this.run){this.run=await this.api('/api/evaluation/run?id='+encodeURIComponent(this.run.id));await this.load(this.run.task,this.run.suite_id);this.selectRun();}}
  async finishRun(exit){
    if(!this.run)throw Error('请先加载评测轮次');
    ++this.cameraGeneration;document.dispatchEvent(new CustomEvent('console-camera-paused'));
    this.message(exit?'正在停止并保存本轮评测结果…':'正在结束本条并保存结果，复位后自动打开下一张布局和相机…');
    try{
      this.run=await this.api('/api/evaluation/'+(exit?'exit-run':'finish-trial'),{run_id:this.run.id,revision:this.run.revision,result:$('ve-trial-result').value,note:$('ve-note').value});
      await this.load(this.run.task,this.run.suite_id);
      if(exit){$('ve-setup').open=true;const files=this.run.result_files;this.run=null;this.clearSession();$('ve-current').hidden=true;this.message('评测结果已保存：'+files.json+' · '+files.csv);}
      else{this.selectRun();if(this.status.mode!=='reference_only')await this.openCamera();this.message(this.run.metrics.pending?'本条结果已保存，已切换下一张布局并连接相机。':'本轮布局已完成，结果已保存。');}
    }catch(e){await this.reloadRun();throw e;}finally{await this.pollControl();}
  }
  serverReady(server){return !!server?.running&&(server.ready??/\blistening on\b/i.test(server.log||''));}
  updateTrialControls(){
    const c=this.controlState,t=this.run?.trials[this.run.cursor],reset=!!c?.jobs.reset?.running,ev=!!c?.jobs.evaluation?.running,server=c?.jobs.server;
    $('ve-eval-start').disabled=this.launchPending||!c?.enabled||c.stopped||reset||ev||!this.serverReady(server)||!t||t.status!=='pending';
    $('ve-eval-start').textContent=this.launchPending?'正在启动，请查看下方日志…':'启动本条评测（自动释放相机）';
    this.renderTrialLog();
    $('ve-trial-end').disabled=!c?.enabled||!t?.log_path||reset||!(t.status==='running'||t.reset_error);
    $('ve-trial-end').textContent=t?.reset_error?'重试复位并下一张':'结束本条（复位并下一张）';
    $('ve-manual-start').disabled=!t||reset||ev||t.status!=='pending';
    $('ve-manual-result').disabled=!t||reset||ev||t.status!=='running';
    $('ve-next').disabled=!t||reset||ev||!['success','failure','aborted'].includes(t.status)||this.run.cursor+1>=this.run.trials.length;
    $('ve-trial-result').disabled=!t||t.status!=='running'||reset;
    $('ve-run-exit').disabled=!this.run||reset;
    let step=1,next='继续上次评测：选择任务和已有轮次，点击“继续选中的评测”。新模型或新 checkpoint 请新建轮次。';
    if(this.run){
      if(c?.stopped){step=2;next='启动已锁定。请先人工检查机器人状态，再解除启动锁定。';}
      else if(reset){step=4;next='正在复位，请等待完成后再布置下一张。';}
      else if(t?.status==='running'||ev){step=4;next='本条正在评测。结束后选择成功 / 失败 / 中止，再点击“结束本条”；需要立即停止时点击急停。';}
      else if(this.run.metrics.all_scored){step=4;next='本轮已完成，可以导出结果，或退出并保存本轮评测。';}
      else if(t&&['success','failure','aborted'].includes(t.status)){step=4;next='本条结果已保存。终端评测请展开手动记录，点击“继续下一张”；也可退出本轮。';}
      else if(!c?.enabled){step=3;next='本轮已加载。对齐布局并释放相机后，在终端运行 VLA；展开手动记录保存本条结果。';}
      else if(!server?.running){step=2;next='本轮已加载。点击“启动模型服务”，等待服务就绪。';}
      else if(!this.serverReady(server)){step=2;next='模型正在加载，加载完成后即可启动评测。';}
      else{step=3;next='开启相机并按参考图摆好物体，然后点击“启动本条评测”；相机会自动释放。';}
    }
    $('ve-next-action').textContent=next;
    for(const item of document.querySelectorAll('[data-eval-step]')){
      const active=Number(item.dataset.evalStep)===step;
      item.classList.toggle('current',active);
      if(active)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');
    }
  }
  builderProgress(){
    const count=Number($('ve-target-count').value)||15,name=this.editingSuite?.id||$('ve-suite-name').value.trim()||'<suite名称>';
    $('ve-save-path').textContent=`保存目录：${this.suitesRoot||'results/real_robot_eval/vla_evaluations/suites'}/${name}`;
    $('ve-builder-progress').textContent=`已拍 ${this.draft.length} / ${count} 张`;
    $('ve-new-suite').hidden=!this.editingSuite;
    $('ve-save-suite').textContent=this.editingSuite?'保存 suite 修改':'保存新 suite';
  }
  async captureLayout(){
    const task=$('ve-task').value.trim(),count=Number($('ve-target-count').value);
    if(!task)throw Error('请先在左侧填写任务名称');
    if(!$('ve-suite-name').value.trim())throw Error('请先在左侧填写 suite 名称');
    if(!Number.isInteger(count)||count<1||count>100)throw Error('目标数量必须为 1～100 的整数');
    if(this.draft.length>=count)throw Error('已达到目标数量，请先增加目标数量或移除图片');
    if(!$('camera').value)throw Error('请先开启并选择布局相机');
    const result=await this.api('/api/snapshot',{camera_id:$('camera').value,task_id:task});
    const frame=result.frames.find(f=>f.camera_id===$('camera').value)||result.frames[0];
    if(!frame)throw Error('未获得布局图');
    this.draft.push({url:frame.url,label:`布局 ${this.draft.length+1}`});this.renderDraft();
    this.message(`已拍摄第 ${this.draft.length}/${count} 张，点击“保存”写入 suite。`);
  }
  async openCamera(){
    const generation=++this.cameraGeneration;
    await this.waitForCameraRelease?.();
    if(generation!==this.cameraGeneration)return;
    await this.api('/api/evaluation/camera/enable',{});
    this.message('正在开启相机…');
    const poll=async()=>{try{const state=await this.api('/api/evaluation/camera');if(generation!==this.cameraGeneration)return;if(!state.enabled){this.message('相机预览已关闭');return;}if(state.service?.state==='error')throw Error(state.service.detail);if(state.service&&state.service.state!=='ready'){setTimeout(poll,500);return;}document.dispatchEvent(new CustomEvent('console-camera-enabled'));this.message('相机已开启，请按参考图恢复物体位置。');}catch(e){this.message(e.message,true);}};
    await poll();
  }
  syncConfigScroll(){
    const editor=$('ve-config-content'),highlight=$('ve-config-highlight');
    highlight.scrollTop=editor.scrollTop;highlight.scrollLeft=editor.scrollLeft;
    $('ve-config-lines').style.transform=`translateY(${-editor.scrollTop}px)`;
  }
  renderConfigCode(){
    const value=$('ve-config-content').value;
    $('ve-config-highlight').innerHTML=yamlMarkup(value)+'\n';
    $('ve-config-lines').textContent=Array.from({length:value.split('\n').length},(_,i)=>i+1).join('\n');
    this.syncConfigScroll();
  }
  requireSavedConfig(){if(this.configDirty)throw Error('配置已修改，请新建评测轮次，保存本轮配置后再启动评测。');}
  async previewConfig(){
    this.configDrafts||={};
    if(this.configEditorPath){if(this.configDirty)this.configDrafts[this.configEditorPath]=$('ve-config-content').value;else delete this.configDrafts[this.configEditorPath];}
    const path=$('ve-config').value.trim(),generation=(this.configGeneration||0)+1;this.configGeneration=generation;
    const editor=$('ve-config-content');editor.disabled=true;editor.value='';this.renderConfigCode();this.configDirty=false;this.configEditorPath=null;
    $('ve-config-path').textContent=path;
    $('ve-config-state').textContent=path?'正在读取配置…':'请选择评测配置。';
    if(!path)return;
    try{const result=await this.api('/api/evaluation/config?path='+encodeURIComponent(path));if(this.configGeneration===generation){
      this.configEditorPath=path;this.configOriginal=result.content;editor.value=this.configDrafts[path]??result.content;this.renderConfigCode();
      this.configDirty=editor.value!==result.content;editor.disabled=false;
      $('ve-config-path').textContent=result.path;$('ve-config-state').textContent=this.configDirty?'已修改，将随新轮次保存':'';
    }}catch(e){if(this.configGeneration===generation)$('ve-config-state').textContent='无法读取配置：'+e.message;}
  }
  setChoice(id,value){const select=$(id);if(value&&![...select.options].some(o=>o.value===value))select.add(new Option(value,value));select.value=value||'';}
  roundOptions(){
    const current=$('ve-round').value;
    const runs=(this.catalog?.runs||[]).filter(r=>r.task===$('ve-task-filter').value&&r.model===$('ve-model').value&&r.checkpoint===$('ve-checkpoint').value);
    const names=[...new Set(runs.map(r=>r.round))];let next=1;while(names.includes('round-'+next))next++;
    const fresh='round-'+next;
    $('ve-round-options').replaceChildren(new Option(fresh+'（新轮次）',fresh),...names.map(n=>new Option(n+'（已有名称）',n)));
    if(!current.trim())$('ve-round').value=fresh;
  }
  controlDefaults(){
    const p=this.controlState?.models[$('ve-model').value];
    const paths=[...new Set([p?.eval_config,...(p?.eval_configs||[]),...(this.catalog?.runs||[]).filter(r=>r.model===$('ve-model').value).map(r=>r.eval_config)].filter(Boolean))];
    $('ve-config').replaceChildren(new Option('请选择评测 YAML',''),...paths.map(path=>new Option(path.split('/').pop(),path)));
    if(p){this.setChoice('ve-checkpoint',p.checkpoint);this.setChoice('ve-config',p.eval_config);}
    this.roundOptions();this.previewConfig();
  }
  async pollControl(){
    clearTimeout(this.controlTimer);
    try{const c=await this.api('/api/evaluation/control');const first=!this.controlState;this.controlState=c;if(first&&!$('ve-checkpoint').value)this.controlDefaults();
      const server=c.jobs.server,ev=c.jobs.evaluation,reset=c.jobs.reset,p=c.models[$('ve-model').value];
      if(this.hadModel&&!server?.running&&!ev?.running&&!reset?.running){
        this.run=null;this.clearSession();$('ve-current').hidden=true;$('ve-setup').open=true;
        this.message('模型服务已停止，会话已结束；保存的评测记录仍可继续。');
      }
      this.hadModel=!!server?.running;
      $('ve-process-status').textContent=(c.enabled?'模型服务：'+(server?.running?(this.serverReady(server)?'已就绪':'加载中'):server?'已退出 '+server.exit_code:'未启动')+'；评测：'+(ev?.running?'运行中':ev?'已退出 '+ev.exit_code:'未启动'):'未配置启动功能')+(reset?.running?'；正在复位':'')+'；'+c.stop_detail+(c.stopped?'；启动已锁定':'')+(p?.unavailable?'；'+p.unavailable:'');
      $('ve-process-status').dataset.state=server?.running?(this.serverReady(server)?'ready':'loading'):server?'exited':'idle';
      $('ve-server-start').disabled=!c.enabled||c.stopped||!!server?.running;
      this.updateTrialControls();
      $('ve-server-stop').disabled=!server?.running||!!ev?.running||!!reset?.running;
      $('ve-unlock').disabled=!c.stopped||c.stopping;
      $('ve-emergency').disabled=!c.enabled;
      this.renderTrialLog();
      $('ve-process-log').textContent='模型服务\n'+(server?.log||'')+'\n评测\n'+(ev?.log||'')+'\n复位\n'+(reset?.log||'');
    }finally{this.controlTimer=setTimeout(()=>this.pollControl().catch(()=>{}),1500);}
  }
  startSessionTracking(){
    try{this.clientId=crypto.randomUUID();}
    catch{this.clientId=Date.now()+'-'+Math.random();}
    const heartbeat=()=>this.sessionHeartbeat().catch(e=>{
      $('ve-session-status').textContent='会话连接异常：'+e.message+'；请保持页面打开并检查网页服务。';
    });
    heartbeat();this.sessionTimer=setInterval(heartbeat,5000);
    window.addEventListener('pagehide',()=>{
      clearInterval(this.sessionTimer);this.saveSession();
      fetch('/api/evaluation/session/close',{method:'POST',keepalive:true,
        headers:{'Content-Type':'application/json','X-Replay-Token':this.status.replay.token},
        body:JSON.stringify(this.sessionPayload())}).catch(()=>{});
    });
    window.addEventListener('pageshow',event=>{if(event.persisted){clearInterval(this.sessionTimer);heartbeat();this.sessionTimer=setInterval(heartbeat,5000);}});
  }
  sessionPayload(){return {client_id:this.clientId,run_id:this.run?.id,cursor:this.run?.cursor,note:$('ve-note').value};}
  async sessionHeartbeat(){
    let state;
    try{state=await this.api('/api/evaluation/session/heartbeat',this.sessionPayload());}
    catch(e){if(e.message==='Not found'){$('ve-session-status').textContent='当前后端尚未更新：刷新恢复和日志已可用；关闭网页自动释放显存需要重启网页后端。';return;}throw e;}
    $('ve-session-status').textContent=state.enabled?
      '会话已连接 · 刷新会自动重连；最后一个页面关闭 '+state.close_grace_seconds+' 秒后保存记录、停止模型服务并结束会话。':
      '当前为手动记录模式，刷新会恢复本页进度。';
    if(state.error)$('ve-session-status').textContent='会话关闭未完成：'+state.error;
  }
  saveSession(){
    if(!this.run)return;
    try{sessionStorage.setItem('vla-evaluation-session',JSON.stringify({run_id:this.run.id,
      manual_open:$('ve-manual-controls').open,event_key:this.trialEventKey,
      events:this.trialEvents||[],launch_error:this.launchError||'',cursor:this.run.cursor,
      note:$('ve-note').value,result:$('ve-trial-result').value}));}catch{}
  }
  clearSession(){try{sessionStorage.removeItem('vla-evaluation-session');}catch{}}
  trialEvent(text){
    this.syncTrialEvents();
    this.trialEvents.push('['+new Date().toLocaleTimeString()+'] '+text);
    this.trialEvents=this.trialEvents.slice(-80);this.saveSession();this.renderTrialLog();
  }
  syncTrialEvents(){
    const key=this.run?this.run.id+':'+this.run.cursor:null;
    if(key!==this.trialEventKey){this.trialEventKey=key;this.trialEvents=[];this.launchError='';}
    this.trialEvents||=[];
  }
  renderTrialLog(){
    this.syncTrialEvents();
    const c=this.controlState,t=this.run?.trials[this.run.cursor];
    const current=kind=>{const job=c?.jobs[kind];return job&&job.run_id===this.run?.id&&(kind==='evaluation'?t?.log_path:t?.reset_log_path)?job:null;};
    const ev=current('evaluation'),reset=current('reset');
    let status='尚未启动评测。';
    if(this.launchPending)status='启动请求处理中，已等待 '+Math.floor((Date.now()-this.launchStarted)/1000)+' 秒；正在等待服务端返回，尚未确认客户端启动。';
    else if(reset?.running)status='正在复位，请查看复位输出。';
    else if(ev)status=ev.running?'评测客户端正在运行。':'评测客户端已退出，退出码：'+ev.exit_code+'。';
    else if(this.launchError||t?.start_error)status='启动失败：'+(this.launchError||t.start_error);
    else if(!c)status='正在读取服务端状态…';
    else if(!c.enabled)status='当前未配置网页启动，请使用下方终端手动记录。';
    else if(c.stopped)status='启动已锁定，请先人工检查并解除锁定。';
    else if(!c.jobs.server?.running)status='尚未启动模型服务，请先完成第 2 步。';
    else if(!this.serverReady(c.jobs.server))status='模型正在加载，加载完成后即可启动评测。';
    else if(t?.status==='pending')status='模型服务已就绪。可以启动本条评测。';
    else if(t?.status==='running')status='当前样本已标记开始；未发现该轮次的网页评测进程，请检查终端或进程状态。';
    else if(t)status='本条结果已保存：'+labels[t.status];
    $('ve-trial-status').textContent=status;
    const parts=[...(this.trialEvents||[])];
    if(t?.start_error&&!parts.some(line=>line.includes(t.start_error)))parts.push('服务端保存的启动错误：'+t.start_error);
    if(t?.log_path)parts.push('客户端日志文件：'+t.log_path);
    if(ev)parts.push('── 评测客户端输出 ──\n'+(ev.log||'进程尚未输出日志。'));
    if(reset)parts.push('── 复位输出 ──\n'+(reset.log||'进程尚未输出日志。'));
    const log=$('ve-trial-log'),follow=log.scrollHeight-log.scrollTop-log.clientHeight<40;
    const text=parts.join('\n')||'等待启动。客户端输出每 1.5 秒刷新。';
    if(log.textContent!==text){log.textContent=text;if(follow)log.scrollTop=log.scrollHeight;}
  }
  async api(path,body){const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Replay-Token':this.status.replay.token},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);return data;}
  bind(id,fn){$(id).onclick=()=>this.guard(fn);}
  async guard(fn){if(this.busy){this.message('正在处理上一项操作，请稍候。',true);return;}this.busy=true;try{await fn();}catch(e){this.message(e.message,true);}finally{this.busy=false;}}
  renderDraft(){ this.builderProgress();$('ve-draft').hidden=this.draft.length===0;$('ve-draft').innerHTML=this.draft.map((x,i)=>`<div class="ve-draft-item"><img src="${esc(x.url)}" alt="布局 ${i+1}"><span>${i+1}. ${esc(x.label)}</span><button data-index="${i}" data-action="up">↑</button><button data-index="${i}" data-action="down">↓</button><button data-index="${i}" data-action="delete">移除</button></div>`).join(''); }
  async load(task,suite){this.catalog=await this.api('/api/evaluation/catalog');
    if(!this.sessionRestored){
      this.sessionRestored=true;
      let saved;try{saved=JSON.parse(sessionStorage.getItem('vla-evaluation-session')||'null');}catch{}
      const control=await this.api('/api/evaluation/control');this.controlState=control;
      const server=control.jobs.server;
      let live;try{live=await this.api('/api/evaluation/session');}catch{}
      const matches=r=>r.model.toLowerCase()===server?.model?.toLowerCase()&&r.checkpoint===server?.checkpoint;
      let runId=control.jobs.evaluation?.running?control.jobs.evaluation.run_id:null;
      if(server?.running){
        runId ||= live?.run_id;
        runId ||= this.catalog.runs.find(r=>r.id===saved?.run_id&&matches(r))?.id;
        runId ||= this.catalog.runs.find(r=>matches(r)&&!r.metrics.all_scored)?.id;
        runId ||= this.catalog.runs.find(matches)?.id;
      }else if(!control.enabled)runId ||= saved?.run_id;
      if(runId&&this.catalog.runs.some(r=>r.id===runId)){
        this.run=await this.api('/api/evaluation/run?id='+encodeURIComponent(runId));
        task=this.run.task;suite=this.run.suite_id;
        if(saved?.run_id===runId){
          this.trialEvents=saved.events||[];this.trialEventKey=saved.event_key;
          this.launchError=saved.launch_error||'';
          $('ve-manual-controls').open=!!saved.manual_open;
        }
        this.selectRun();
        if(saved?.run_id===runId&&saved.cursor===this.run.cursor&&this.run.trials[this.run.cursor].status==='running'){
          $('ve-note').value=saved.note??$('ve-note').value;
          if(['success','failure','aborted'].includes(saved.result))$('ve-trial-result').value=saved.result;
          this.saveSession();
        }
        this.message('已连接评测会话：'+this.run.round+' · 第 '+(this.run.cursor+1)+' 张；不会重新启动机器人。');
      }else if(saved?.run_id)this.clearSession();
    }
    this.suitesRoot=this.catalog.suites_root;this.builderProgress();const current=task||$('ve-task-filter').value;const tasks=[...new Set(this.catalog.suites.map(s=>s.task))].sort();$('ve-task-filter').replaceChildren(...tasks.map(t=>new Option(t,t)));if(tasks.includes(current))$('ve-task-filter').value=current;this.renderTask(suite);this.roundOptions();}
  useLayoutReference(suite,layout){
    if(!layout)return;
    const name=suite.name||suite.suite_name||'',view=`${name} ${layout.label}`;
    const cameraId=/腕部|wrist/i.test(view)?'242622070583':/第三人称|全景|panorama/i.test(view)?'148522072685':'';
    $('reference-image').dataset.cameraId=cameraId;
    this.reference.add(layout.url,`${name} / ${layout.label}`);
    if(this.run&&cameraId&&[...$('camera').options].some(o=>o.value===cameraId)&&$('camera').value!==cameraId){$('camera').value=cameraId;$('camera').dispatchEvent(new Event('change'));}
  }
  suiteInfo(){
    const s=this.catalog.suites.find(s=>s.id===$('ve-suite').value);
    $('ve-suite-info').textContent=s?`${s.layouts.length}/${s.target_count||15} 张布局 · 测试集 ID：${s.id}`:'先保存一个任务的布局测试集。';
    if(s&&this.referenceSuiteId!==s.id){this.referenceSuiteId=s.id;this.useLayoutReference(s,this.run?.suite_id===s.id?this.run.trials[this.run.cursor]:s.layouts[0]);}
    $('ve-edit-suite').disabled=!s;
    $('ve-suite-images').hidden=!s;
    $('ve-suite-images-title').textContent=s?`布局图片（${s.layouts.length} 张）`:'布局图片';
    $('ve-suite-preview').innerHTML=s?'<p>已保存的布局图片，点击可加载为相机对齐参考图。</p><div class="ve-layout-gallery">'+s.layouts.map((t,i)=>`<button type="button" data-layout-index="${i}"><img loading="lazy" src="${esc(t.url)}" alt="${esc(t.label)}"><span>${i+1}. ${esc(t.label)}</span></button>`).join('')+'</div>':'';
  }
  renderTask(suite){const task=$('ve-task-filter').value,previous=suite||$('ve-suite').value;const suites=this.catalog.suites.filter(s=>s.task===task);$('ve-suite').replaceChildren(...suites.map(s=>new Option(`${s.name} · ${s.layouts.length} 张 · ${s.id.slice(0,8)}`,s.id)));if(suites.some(s=>s.id===previous))$('ve-suite').value=previous;else{const primary=suites.find(s=>s.id===task||s.name===task);if(primary)$('ve-suite').value=primary.id;}this.suiteInfo();const runs=this.catalog.runs.filter(r=>r.task===task);$('ve-runs').replaceChildren(...runs.map(r=>new Option(`${r.model} · ${r.suite_name} · ${r.round} · 已判定 ${r.metrics.completed}/${r.metrics.total} · 待测 ${r.metrics.pending} · ${r.checkpoint.split('/').pop()}`,r.id)));if(this.run&&runs.some(r=>r.id===this.run.id))$('ve-runs').value=this.run.id;
    $('ve-load').disabled=!runs.length;if(!runs.length)$('ve-runs').add(new Option('此任务暂无评测轮次，请在下方新建',''));
    $('ve-comparison').innerHTML='<div class="ve-table"><table><thead><tr><th>模型 / checkpoint</th><th>测试集 / 轮次</th><th>成功率</th><th>进度</th><th>模式</th></tr></thead><tbody>'+runs.map(r=>`<tr><td>${esc(r.model)}<br><small>${esc(r.checkpoint)}</small></td><td>${esc(r.suite_name)} (${r.suite_id.slice(0,8)})<br>${esc(r.round)}</td><td>${this.rate(r.metrics)} (${r.metrics.success}/${r.metrics.completed})</td><td>已判定 ${r.metrics.completed}/${r.metrics.total}；中止 ${r.metrics.aborted}</td><td>${r.mode==='demo'?'DEMO': '人工真机记录'}</td></tr>`).join('')+'</tbody></table></div>';}
  rate(m){return m.success_rate===null?'—':(m.success_rate*100).toFixed(1)+'%';}
  selectRun(){this.saveSession();$('ve-setup').open=false;const r=this.run;$('ve-model').value=Object.keys(this.controlState?.models||{}).find(k=>k.toLowerCase()===r.model.toLowerCase())||r.model;$('ve-model').dispatchEvent(new Event('change'));this.setChoice('ve-checkpoint',r.checkpoint);this.setChoice('ve-config',r.eval_config);$('ve-round').value=r.round;this.previewConfig();const t=r.trials[r.cursor],m=r.metrics;if(t.status!=='pending')this.launchError='';$('ve-current').hidden=false;$('ve-run-meta').textContent=`任务 ${r.task} · ${r.model} · checkpoint ${r.checkpoint} · ${r.round}${r.mode==='demo'?' · DEMO':''}`;$('ve-progress').textContent=`成功 ${m.success} / 已判定 ${m.completed} = ${this.rate(m)}；共 ${m.total} 张，待测 ${m.pending}，进行中 ${m.running}，中止 ${m.aborted}${m.all_scored?' · 全部判定完成':' · 尚非完整测试集结果'}`;$('ve-current-label').textContent=`第 ${r.cursor+1}/${r.trials.length} 张：${t.label} · ${labels[t.status]}`;$('ve-layout').src=t.url;$('ve-note').value=t.note;$('ve-note').disabled=t.status!=='running';$('ve-instructions').textContent='按图摆好后启动本条评测；结束本条会保存结果、复位并切换下一张，自动连接相机。';$('ve-trial-result').value=t.status==='success'||t.status==='failure'?t.status:'aborted';this.updateTrialControls();$('ve-json').href='/api/evaluation/run?id='+r.id;$('ve-csv').href='/api/evaluation/export?id='+r.id;$('ve-trials').textContent=r.trials.map((t,i)=>`${i+1}: ${labels[t.status]}`).join(' · ');this.useLayoutReference(r,t);document.dispatchEvent(new CustomEvent('evaluation-layout-ready'));}
}
