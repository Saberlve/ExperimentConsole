const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={pending:'待测',running:'已标记开始',success:'成功',failure:'失败',aborted:'中止（不计入成功率）'};
export class VLAEvaluation {
  constructor(reference,status,message){
    this.reference=reference;this.status=status;this.message=message;this.draft=[];this.run=null;this.busy=false;
    $('vla-evaluation').innerHTML=`<h2>VLA 真机评测 · 布局测试集与结果</h2>
<p>每个任务保存独立布局测试集；不同模型和 checkpoint 使用同一测试集，各自新建评测轮次。可在下方启动模型服务和评测，也可沿用终端。</p>
<section id="ve-process"><h3>模型服务与评测</h3><p>使用下面轮次表单中的模型、checkpoint 和评测 YAML。先新建或加载轮次，恢复布局并标记开始，再启动评测。</p><div class="ve-actions"><button id="ve-server-start">启动模型服务</button><button id="ve-server-stop">停止模型服务</button><button id="ve-eval-start">启动评测</button><button id="ve-emergency" style="background:#b91c1c;color:white;font-weight:bold">急停 / 停止评测</button><button id="ve-unlock">已人工检查，解除启动锁定</button></div><p>软件急停发送控制器停止指令；网络或控制器异常时请使用实体急停。解除锁定不会使机器人运动。</p><p id="ve-process-status"></p><details><summary>启动日志</summary><pre id="ve-process-log" style="white-space:pre-wrap;max-height:260px;overflow:auto"></pre></details></section>
<div class="ve-actions"><button id="ve-camera-open">开启布局相机</button><button id="ve-camera-release">关闭并释放相机</button></div><p id="ve-camera-status">恢复布局时开启相机；标记开始会先释放相机，再由你在终端启动评测。</p>
<details><summary>① 准备任务的布局测试集（建议 10 张）</summary>
<label>任务名称<input id="ve-task" placeholder="例如 egg_pick"></label><label>测试集名称 / 版本<input id="ve-suite-name" placeholder="例如 固定布局 v1"></label><label>成功判定标准<textarea id="ve-criteria" placeholder="例如 成功抓起并放入目标容器，物体未掉落"></textarea></label>
<label>批量上传布局图<input id="ve-files" type="file" accept="image/png,image/jpeg" multiple></label><p>上传按文件名排序；可调整顺序。也可以在左侧拍摄或加载参考图后加入。草稿需点击保存才会持久化。</p>
<button id="ve-add-reference">将左侧已选参考图加入草稿</button><button id="ve-capture">拍摄当前视角并加入草稿</button>
<div id="ve-draft"></div><button id="ve-save-suite" class="primary">保存布局测试集</button></details>
<h3>② 选择任务与测试集，创建模型评测轮次</h3>
<div class="ve-grid"><label>任务<select id="ve-task-filter"></select></label><label>布局测试集<select id="ve-suite"></select></label></div><p id="ve-suite-info"></p>
<div class="ve-grid"><label>模型名称<select id="ve-model"><option>pi05</option><option>ACT</option><option>FTP1</option><option>N0</option></select></label><label>checkpoint 路径或唯一标识<input id="ve-checkpoint" placeholder="明确到某个训练 checkpoint"></label><label>轮次名称<input id="ve-round" placeholder="例如 round-1"></label><label>评测配置路径（可选）<input id="ve-config" placeholder="终端使用的 eval YAML"></label></div>
<button id="ve-create-run" class="primary">新建该模型的评测轮次</button>
<h3>③ 继续已有轮次</h3><label>当前任务的评测记录<select id="ve-runs"></select></label><button id="ve-load">加载 / 刷新评测记录</button>
<section id="ve-current" hidden><p id="ve-run-meta"></p><strong id="ve-progress"></strong><p id="ve-current-label"></p><img id="ve-layout" alt="当前样本的固定布局图" style="max-width:100%;max-height:420px"><p id="ve-instructions"></p><label>本样本备注 / 日志路径<textarea id="ve-note"></textarea></label>
<div class="ve-actions"><button id="ve-start">已摆好：释放相机并标记开始</button><button id="ve-success">成功</button><button id="ve-failure">失败</button><button id="ve-aborted">中止</button><button id="ve-next" class="primary">下一张布局 →</button></div>
<p><a id="ve-json" target="_blank">导出 JSON</a> · <a id="ve-csv">导出 CSV</a></p><div id="ve-trials"></div></section>
<h3>当前任务的模型结果</h3><p>成功率 = 成功 /（成功 + 失败）。中止和未测单列；只有同一测试集 ID 的记录可直接逐样本比较。DEMO 记录单独标识。</p><div id="ve-comparison"></div>`;
    this.cameraGeneration=0;
    const controlPayload=()=>({model:$('ve-model').value,checkpoint:$('ve-checkpoint').value,eval_config:$('ve-config').value,run_id:this.run?.id,revision:this.run?.revision});
    for(const [id,endpoint] of [['ve-server-start','server'],['ve-server-stop','stop-server'],['ve-eval-start','evaluation'],['ve-unlock','unlock']])this.bind(id,async()=>{await this.api('/api/evaluation/control/'+endpoint,controlPayload());await this.pollControl();});
    // Emergency remains independent of the ordinary form busy guard.
    $('ve-emergency').onclick=async()=>{try{await this.api('/api/evaluation/control/stop',{});}catch(e){this.message(e.message,true);}finally{await this.pollControl();}};
    $('ve-model').onchange=()=>this.controlDefaults();
    this.pollControl().catch(e=>this.message(e.message,true));
    this.bind('ve-camera-open',()=>this.openCamera());
    this.bind('ve-camera-release',async()=>{++this.cameraGeneration;document.dispatchEvent(new CustomEvent('console-camera-paused'));await this.api('/api/evaluation/camera/release',{});$('ve-camera-status').textContent='控制台相机已释放，可以在原终端评测。';});
    $('ve-camera-open').disabled=status.mode==='reference_only';
    this.bind('ve-save-suite',async()=>{const suite=await this.api('/api/evaluation/suites',{task:$('ve-task').value,name:$('ve-suite-name').value,criteria:$('ve-criteria').value,layouts:this.draft});this.draft=[];this.renderDraft();await this.load(suite.task,suite.id);this.message('布局测试集已保存；相同任务的其他模型可以复用这组图片。');});
    this.bind('ve-add-reference',async()=>{const option=$('references').selectedOptions[0];if(!option?.value)throw Error('请先在左侧选择参考图');this.draft.push({url:option.value,label:option.textContent});this.renderDraft();});
    this.bind('ve-capture',async()=>{const task=$('ve-task').value.trim();if(!task)throw Error('请先填写任务名称');const result=await this.api('/api/snapshot',{camera_id:$('camera').value,task_id:task});const frame=result.frames.find(f=>f.camera_id===$('camera').value)||result.frames[0];this.draft.push({url:frame.url,label:`布局 ${this.draft.length+1}`});this.renderDraft();});
    $('ve-capture').disabled=status.mode==='reference_only';
    this.bind('ve-create-run',async()=>{this.run=await this.api('/api/evaluation/runs',{suite_id:$('ve-suite').value,model:$('ve-model').value,checkpoint:$('ve-checkpoint').value,round:$('ve-round').value,eval_config:$('ve-config').value});await this.load(this.run.task,this.run.suite_id);this.selectRun();});
    this.bind('ve-load',async()=>{if(!$('ve-runs').value)throw Error('请选择评测轮次');this.run=await this.api('/api/evaluation/run?id='+encodeURIComponent($('ve-runs').value));this.selectRun();});
    for(const [id,action,result]of [['ve-start','start'],['ve-success','result','success'],['ve-failure','result','failure'],['ve-aborted','result','aborted'],['ve-next','next']])this.bind(id,async()=>{if(action==='start'){++this.cameraGeneration;document.dispatchEvent(new CustomEvent('console-camera-paused'));}this.run=await this.api('/api/evaluation/transition',{run_id:this.run.id,revision:this.run.revision,action,result,note:$('ve-note').value});await this.load(this.run.task,this.run.suite_id);this.selectRun();if(action==='start')$('ve-camera-status').textContent='控制台相机已释放。请在原终端启动本样本评测。';if(action==='next'&&status.mode!=='reference_only')await this.openCamera();});
    $('ve-task-filter').onchange=()=>{this.run=null;$('ve-current').hidden=true;this.renderTask();};
    $('ve-suite').onchange=()=>this.suiteInfo();
    $('ve-files').onchange=()=>this.guard(async()=>{const files=[...$('ve-files').files].sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));if(files.length+this.draft.length>100)throw Error('最多 100 张布局图');for(const file of files){this.draft.push({url:await reference.upload(file),label:file.name});this.renderDraft();}$('ve-files').value='';});
    $('ve-draft').onclick=e=>{if(this.busy)return;const b=e.target.closest('button');if(!b)return;const i=Number(b.dataset.index);if(b.dataset.action==='delete')this.draft.splice(i,1);else {const j=i+(b.dataset.action==='up'?-1:1);if(j>=0&&j<this.draft.length)[this.draft[i],this.draft[j]]=[this.draft[j],this.draft[i]];}this.renderDraft();};
    this.renderDraft();
  }
  async openCamera(){
    const generation=++this.cameraGeneration;
    await this.api('/api/evaluation/camera/enable',{});
    $('ve-camera-status').textContent='正在开启布局相机…';
    const poll=async()=>{try{const state=await this.api('/api/evaluation/camera');if(generation!==this.cameraGeneration)return;if(!state.enabled){$('ve-camera-status').textContent='相机预览已关闭';return;}if(state.service?.state==='error')throw Error(state.service.detail);if(state.service&&state.service.state!=='ready'){setTimeout(poll,500);return;}document.dispatchEvent(new CustomEvent('console-camera-enabled'));$('ve-camera-status').textContent='布局相机已开启，请按参考图恢复物体位置。';}catch(e){$('ve-camera-status').textContent=e.message;this.message(e.message,true);}};
    await poll();
  }
  controlDefaults(){const p=this.controlState?.models[$('ve-model').value];if(p){$('ve-checkpoint').value=p.checkpoint;$('ve-config').value=p.eval_config;} }
  async pollControl(){
    clearTimeout(this.controlTimer);
    try{const c=await this.api('/api/evaluation/control');const first=!this.controlState;this.controlState=c;if(first&&!$('ve-checkpoint').value)this.controlDefaults();
      const server=c.jobs.server,ev=c.jobs.evaluation,p=c.models[$('ve-model').value];
      $('ve-process-status').textContent=(c.enabled?'模型服务：'+(server?.running?'运行中（端口就绪后可评测）':server?'已退出 '+server.exit_code:'未启动')+'；评测：'+(ev?.running?'运行中':ev?'已退出 '+ev.exit_code:'未启动'):'未配置启动功能')+'；'+c.stop_detail+(c.stopped?'；启动已锁定':'')+(p?.unavailable?'；'+p.unavailable:'');
      $('ve-server-start').disabled=!c.enabled||c.stopped||!!server?.running;
      $('ve-eval-start').disabled=!c.enabled||c.stopped||!!ev?.running||!server?.running||!!p?.unavailable;
      $('ve-server-stop').disabled=!server?.running||!!ev?.running;
      $('ve-unlock').disabled=!c.stopped||c.stopping;
      $('ve-emergency').disabled=!c.enabled;
      $('ve-process-log').textContent='模型服务\n'+(server?.log||'')+'\n评测\n'+(ev?.log||'');
    }finally{this.controlTimer=setTimeout(()=>this.pollControl().catch(()=>{}),1500);}
  }
  async api(path,body){const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Replay-Token':this.status.replay.token},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);return data;}
  bind(id,fn){$(id).onclick=()=>this.guard(fn);}
  async guard(fn){if(this.busy)return;this.busy=true;try{await fn();}catch(e){this.message(e.message,true);}finally{this.busy=false;}}
  renderDraft(){ $('ve-draft').innerHTML=`<p>草稿：${this.draft.length} 张（建议 10 张）</p>`+this.draft.map((x,i)=>`<div class="ve-draft-item"><img src="${esc(x.url)}" alt="布局 ${i+1}"><span>${i+1}. ${esc(x.label)}</span><button data-index="${i}" data-action="up">↑</button><button data-index="${i}" data-action="down">↓</button><button data-index="${i}" data-action="delete">移除</button></div>`).join(''); }
  async load(task,suite){this.catalog=await this.api('/api/evaluation/catalog');const current=task||$('ve-task-filter').value;const tasks=[...new Set(this.catalog.suites.map(s=>s.task))].sort();$('ve-task-filter').replaceChildren(...tasks.map(t=>new Option(t,t)));if(tasks.includes(current))$('ve-task-filter').value=current;this.renderTask(suite);}
  suiteInfo(){const s=this.catalog.suites.find(s=>s.id===$('ve-suite').value);$('ve-suite-info').textContent=s?`${s.layouts.length} 张布局 · 判定：${s.criteria} · 测试集 ID：${s.id}`:'先保存一个任务的布局测试集。';}
  renderTask(suite){const task=$('ve-task-filter').value,previous=suite||$('ve-suite').value;const suites=this.catalog.suites.filter(s=>s.task===task);$('ve-suite').replaceChildren(...suites.map(s=>new Option(`${s.name} · ${s.layouts.length} 张 · ${s.id.slice(0,8)}`,s.id)));if(suites.some(s=>s.id===previous))$('ve-suite').value=previous;this.suiteInfo();const runs=this.catalog.runs.filter(r=>r.task===task);$('ve-runs').replaceChildren(...runs.map(r=>new Option(`${r.model} · ${r.round} · ${r.checkpoint} · ${r.id.slice(0,8)}`,r.id)));if(this.run&&runs.some(r=>r.id===this.run.id))$('ve-runs').value=this.run.id;
    $('ve-comparison').innerHTML='<div class="ve-table"><table><thead><tr><th>模型 / checkpoint</th><th>测试集 / 轮次</th><th>成功率</th><th>进度</th><th>模式</th></tr></thead><tbody>'+runs.map(r=>`<tr><td>${esc(r.model)}<br><small>${esc(r.checkpoint)}</small></td><td>${esc(r.suite_name)} (${r.suite_id.slice(0,8)})<br>${esc(r.round)}</td><td>${this.rate(r.metrics)} (${r.metrics.success}/${r.metrics.completed})</td><td>已判定 ${r.metrics.completed}/${r.metrics.total}；中止 ${r.metrics.aborted}</td><td>${r.mode==='demo'?'DEMO': '人工真机记录'}</td></tr>`).join('')+'</tbody></table></div>';}
  rate(m){return m.success_rate===null?'—':(m.success_rate*100).toFixed(1)+'%';}
  selectRun(){const r=this.run;$('ve-model').value=Object.keys(this.controlState?.models||{}).find(k=>k.toLowerCase()===r.model.toLowerCase())||r.model;$('ve-checkpoint').value=r.checkpoint;$('ve-config').value=r.eval_config;const t=r.trials[r.cursor],m=r.metrics;$('ve-current').hidden=false;$('ve-run-meta').textContent=`任务 ${r.task} · ${r.model} · checkpoint ${r.checkpoint} · ${r.round}${r.mode==='demo'?' · DEMO':''}`;$('ve-progress').textContent=`成功 ${m.success} / 已判定 ${m.completed} = ${this.rate(m)}；共 ${m.total} 张，待测 ${m.pending}，进行中 ${m.running}，中止 ${m.aborted}${m.all_scored?' · 全部判定完成':' · 尚非完整测试集结果'}`;$('ve-current-label').textContent=`第 ${r.cursor+1}/${r.trials.length} 张：${t.label} · ${labels[t.status]}`;$('ve-layout').src=t.url;$('ve-note').value=t.note;$('ve-note').disabled=t.status!=='running';$('ve-instructions').textContent='开启布局相机，按上图摆放；点击标记开始会先释放相机。然后在原终端执行评测，结束后人工记录结果。成功标准：'+r.criteria;$('ve-start').disabled=t.status!=='pending';for(const k of ['success','failure','aborted'])$('ve-'+k).disabled=t.status!=='running';$('ve-next').disabled=!['success','failure','aborted'].includes(t.status)||r.cursor+1===r.trials.length;$('ve-json').href='/api/evaluation/run?id='+r.id;$('ve-csv').href='/api/evaluation/export?id='+r.id;$('ve-trials').textContent=r.trials.map((t,i)=>`${i+1}: ${labels[t.status]}`).join(' · ');this.reference.add(t.url,`${r.task} / ${r.suite_name} / ${t.label}`);}
}
