import '/app.js';
import {initTraining} from '/training.mjs';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let catalog,selectedModel='pi05';
const initial=window.initialWorkbenchHash?.slice(1)||'';
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function route(){
  const page=location.hash.slice(1)||'training';
  const consolePage=['vla','wm','idm'].includes(page);
  $('training-stage').hidden=consolePage||page==='checkpoints';
  $('checkpoints-stage').hidden=page!=='checkpoints';
  $('console-stage').hidden=!consolePage;
  for(const button of document.querySelectorAll('header nav button'))button.classList.toggle('active',(button.dataset.workspace||button.dataset.page)===page);
  if(consolePage)document.querySelector(`[data-page="${page}"]`).click();
  if(page==='checkpoints')refreshCheckpoints().catch(e=>message(e.message,true));
}
function checkpointOptions(){
  const select=$('ve-checkpoint-select');if(!select||!catalog)return;
  const model=$('ve-model').value;
  const entries=catalog.models.find(m=>m.id===model)?.checkpoints||[];
  select.replaceChildren(new Option(entries.length?'从归档中选择，或在下方填写其他路径':'此模型目录暂无检查点，可在下方填写其他路径',''));
  for(const item of entries){const option=new Option(`${item.run} / ${item.name}${item.metadata_ready?'':' · 元数据不完整'}`,item.path);option.disabled=!item.metadata_ready;select.add(option);}
  select.value=$('ve-checkpoint').value;
}
function useCheckpoint(path){
  const item=catalog.models.flatMap(m=>m.checkpoints).find(c=>c.path===path);
  if(!item)return;
  $('ve-model').value=item.model;$('ve-model').dispatchEvent(new Event('change'));
  $('ve-checkpoint').value=item.path;checkpointOptions();
  location.hash='vla';
  message(`已选择 ${item.model_label} / ${item.run} / ${item.name}。选择测试集并创建新评测轮次后，再启动服务。`);
}
function renderCheckpoints(){
  $('cp-root').textContent=catalog.root;
  $('cp-example').textContent=`${catalog.root}/\n  pi05/                 # π05\n    egg_pick_v1/        # 一次完整训练\n      config.yaml\n      dataset_statistics.json\n      paligemma_tokenizer.model\n      checkpoints/\n        steps_25000_model.safetensors\n  ACT/\n  N0/\n  FTP1/`;
  $('cp-models').innerHTML=catalog.models.map(m=>`<button class="model-card ${m.id===selectedModel?'active':''}" data-model="${m.id}"><strong>${m.label}</strong><span>${m.checkpoints.length} 个权重</span><small>${esc(m.directory)}</small></button>`).join('');
  const model=catalog.models.find(m=>m.id===selectedModel);
  $('cp-records').innerHTML=`<div class="panel"><h3>${model.label} 检查点</h3>${model.checkpoints.length?`<div class="scroll"><table><thead><tr><th>训练目录 / 权重</th><th>文件检查</th><th></th></tr></thead><tbody>${model.checkpoints.map(c=>`<tr><td>${esc(c.run)}<br><small>${esc(c.name)}</small></td><td>${c.metadata_ready?'<span class="badge">元数据齐全</span>':`<span class="badge error">需补充文件</span><p>${c.issues.map(esc).join('<br>')}</p>`}</td><td><button data-checkpoint="${esc(c.path)}" ${c.metadata_ready?'':'disabled'}>用于真机评测 →</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">还没有检查点。将一次完整训练目录放入上方模型文件夹，再点击“刷新目录”。</div>'}</div>`;
  checkpointOptions();
}
async function refreshCheckpoints(){
  const response=await fetch('/api/checkpoints',{cache:'no-store'});const result=await response.json();if(!response.ok)throw Error(result.error);
  catalog=result;renderCheckpoints();
}
for(const button of document.querySelectorAll('[data-workspace]'))button.onclick=()=>{location.hash=button.dataset.workspace;};
$('cp-refresh').onclick=()=>refreshCheckpoints().catch(e=>message(e.message,true));
$('cp-models').onclick=e=>{const button=e.target.closest('[data-model]');if(button){selectedModel=button.dataset.model;renderCheckpoints();}};
$('cp-records').onclick=e=>{const button=e.target.closest('[data-checkpoint]');if(button)useCheckpoint(button.dataset.checkpoint);};
const trainingReady=initTraining().catch(e=>message('训练配置加载失败：'+e.message,true));
try{
  await window.consoleReady;
  const label=document.createElement('label');label.textContent='已归档的检查点';
  const select=document.createElement('select');select.id='ve-checkpoint-select';label.append(select);
  $('ve-checkpoint').closest('label').before(label);
  select.onchange=()=>{if(select.value){$('ve-checkpoint').value=select.value;message('已选择检查点；如需更换已有评测轮次的权重，请新建轮次。');}};
  $('ve-model').addEventListener('change',checkpointOptions);
  $('ve-checkpoint').addEventListener('input',()=>{select.value=$('ve-checkpoint').value;});
  for(const option of $('ve-model').options)if(option.value==='pi05')option.textContent='π05';
  document.querySelector('[data-page="vla"]').addEventListener('click',()=>{if(catalog)checkpointOptions();});
  await refreshCheckpoints();
}catch(e){message('评测模块加载失败：'+e.message,true);}
const trainingCatalog=await trainingReady;
const fallback=trainingCatalog?.integration_available===false?'wm':'training';
window.addEventListener('hashchange',route);
location.hash=['training','checkpoints','vla','wm','idm'].includes(initial)?initial:fallback;
route();
