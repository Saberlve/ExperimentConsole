import {VLAEvaluation} from '/evaluation.mjs';
import {ActionReplay} from '/replay.mjs';
import {RealtimeComparison} from '/comparison.mjs';
const $ = id => document.getElementById(id);
export async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
function message(text, error=false) { $('message').textContent=text; $('message').classList.toggle('error',error); }
function action(id, fn) { $(id).addEventListener('click', async () => {
  $(id).disabled=true;
  try { await fn(); } catch(e) { message(e.message,true); }
  finally { $(id).disabled=false; }
}); }
export function overlayOpacity(alpha, visible) { return visible ? Math.min(100,Math.max(0,Number(alpha)))/100 : 0; }
export class CameraViewer {
  constructor(mode) { this.mode=mode; this.timer=null; this.generation=0; this.url=null; this.controller=null; }
  select(camera) {
    const generation=++this.generation;
    clearTimeout(this.timer); this.controller?.abort();
    $('live').removeAttribute('src');
    if(this.url){URL.revokeObjectURL(this.url);this.url=null;}
    document.dispatchEvent(new CustomEvent('live-reset'));
    if (!camera) return;
    const update=async()=>{
      let url=null;
      try {
        this.controller=new AbortController();
        const response=await fetch('/api/frame?camera='+encodeURIComponent(camera),
          {cache:'no-store',signal:this.controller.signal});
        if(!response.ok){const error=await response.json();throw new Error(error.error);}
        const frameId=response.headers.get('X-Frame-Id');
        if(!frameId)throw new Error('相机未提供帧序号，无法实时验证');
        const blob=await response.blob();
        if(generation!==this.generation)return;
        url=URL.createObjectURL(blob);
        $('live').src=url;
        await $('live').decode();
        if(generation!==this.generation){URL.revokeObjectURL(url);return;}
        const old=this.url;this.url=url;if(old)URL.revokeObjectURL(old);
        document.dispatchEvent(new CustomEvent('live-frame',{detail:{
          cameraId:camera,frameId,receivedAt:performance.now()}}));
      } catch(e) {
        if(url && url!==this.url)URL.revokeObjectURL(url);
        if(generation!==this.generation)return;
        document.dispatchEvent(new CustomEvent('live-error',{detail:'相机画面不可用：'+e.message}));
        message('相机画面不可用：'+e.message,true);
      } finally {
        if(generation===this.generation)this.timer=setTimeout(update,300);
      }
    };
    update();
  }
}
export class ImageOverlay {
  constructor() { for(const id of ['alpha','show-reference']) $(id).addEventListener('input',()=>this.render()); }
  render() {
    const alpha=overlayOpacity($('alpha').value,$('show-reference').checked);
    $('overlay').style.opacity=alpha;
    $('alpha-value').value=$('alpha').value+'%';
    const a=$('live'),b=$('overlay');
    $('dimension-warning').textContent=b.naturalWidth && a.naturalWidth && Math.abs(a.naturalWidth/a.naturalHeight-b.naturalWidth/b.naturalHeight)>.01 ? '注意：参考图宽高比与相机不同，当前按相机画幅拉伸显示。' : '';
  }
  set(url) { document.dispatchEvent(new CustomEvent('reference-reset')); $('overlay').src=url; $('overlay').hidden=!url||location.hash==='#wm'; $('reference-image').src=url; $('reference-preview').hidden=!url; $('overlay').onload=()=>this.render(); this.render(); }
}
export class ReferenceImageSelector {
  constructor(overlay) { this.overlay=overlay; $('references').onchange=()=>overlay.set($('references').value); }
  add(url,label) { const option=new Option(label,url,true,true); $('references').add(option); this.overlay.set(url); }
  async upload(file) {
    if(!file) throw new Error('请选择图片');
    const response=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':file.type},body:file});
    const data=await response.json(); if(!response.ok) throw new Error(data.error); return data.url;
  }
}
function fill(id, values) { $(id).replaceChildren(...values.map(value=>new Option(value,value))); }
export class DatasetSelector {
  constructor(reference) {
    $('dataset').onchange=async()=>{
      fill('episode',[]); fill('view',[]);
      $('dataset-status').textContent='未加载 Episode';
      if(!$('dataset').value)return;
      try { const data=await api('/api/dataset?key='+encodeURIComponent($('dataset').value)); fill('episode',data.episodes);fill('view',data.views);this.status(); }
      catch(e){message(e.message,true);}
    };
    for(const id of ['episode','view']) $(id).onchange=()=>this.status();
    action('load-reference',async()=>{
      const query=new URLSearchParams({key:$('dataset').value,episode:$('episode').value,view:$('view').value});
      const data=await api('/api/reference?'+query);reference.add(data.url,`${$('dataset').value} / Episode ${$('episode').value} / ${$('view').value}`);message('数据集首帧已加载');
    });
  }
  status(){ $('dataset-status').textContent=`${$('dataset').value} · Episode ${$('episode').value} · ${$('view').value}`; }
}
export class SnapshotButton {
  constructor() { action('snapshot',async()=>{
    const data=await api('/api/snapshot',{camera_id:$('camera').value,task_id:$('task').value});
    $('snapshots').replaceChildren();
    for(const frame of data.frames){ const item=document.createElement('div'),image=document.createElement('img'),link=document.createElement('a'),metadata=document.createElement('a'); image.src=frame.url;image.alt=frame.camera_id;link.href=frame.url;link.textContent='原始 PNG';link.download='';metadata.href=frame.url.replace(/\.png$/,'.json');metadata.textContent='元数据 JSON';metadata.target='_blank';item.append(image,document.createElement('br'),link,metadata);$('snapshots').append(item); }
    message(data.pair_capture?'已通过旧服务保存双相机原图和元数据':'已保存 DEMO 合成图和元数据');
  }); }
}
async function main(){
  const status=await api('/api/status');$('mode').textContent=`${status.mode==='demo'?'DEMO · 合成相机，无真实硬件':(status.mode==='reference_only'?'布局记录模式 · 相机由终端评测使用':'真实相机 · 复用原首帧 HTTP 服务')} | 机械臂回放：${status.robot_execution?'手动执行已启用':'禁用'}`;
  $('capture-note').textContent=status.mode==='demo'?'当前保存的是合成图。':'旧接口固定保存双相机；选择器控制预览。真实采集时间、设备时间域及内参不可获取时标记 null。';
  const camera=new CameraViewer(status.mode),overlay=new ImageOverlay(),reference=new ReferenceImageSelector(overlay);
  new RealtimeComparison();new DatasetSelector(reference);new SnapshotButton();
  new ActionReplay(status.replay);
  new VLAEvaluation(reference, status, message).load().catch(e=>message(e.message,true));
  if(status.mode==='reference_only'){ $('snapshot').disabled=true; $('refresh').disabled=true; $('capture-note').textContent='上传提前拍好的布局图；本模式不打开相机，不影响终端评测。'; }
  const cameras=async()=>{const selected=$('camera').value;const data=await api('/api/cameras');$('camera').replaceChildren(...data.map(d=>{const o=new Option(`${d.label||d.name||d.serial} · ${d.serial}${d.error?' · '+d.error:''}`,d.serial);o.disabled=!d.connected||!!d.error;return o;}));if(data.some(d=>d.serial===selected))$('camera').value=selected;camera.select($('camera').value);message('相机列表已刷新');};
  document.addEventListener('console-camera-paused',()=>camera.select(''));
  document.addEventListener('console-camera-enabled',()=>cameras().catch(e=>message(e.message,true)));
  $('camera').onchange=()=>camera.select($('camera').value);action('refresh',cameras);
  for(const button of document.querySelectorAll('[data-page]'))button.onclick=()=>{
    const page=button.dataset.page; $('title').textContent=button.textContent;$('alignment').hidden=page==='wm';$('overlay').hidden=page==='wm'||!$('references').value;$('reference-preview').hidden=false;$('comparison-tools').hidden=page==='wm';$('comparison-panels').classList.toggle('wm',page==='wm');$('idm-panel').hidden=page!=='idm';$('action-replay-panel').hidden=page!=='idm';$('prepare').hidden=true;$('vla-evaluation').hidden=page!=='vla';
    for(const b of document.querySelectorAll('[data-page]'))b.classList.toggle('active',b===button);
    location.hash=page;
    document.dispatchEvent(new CustomEvent('page-changed'));
  };
  document.querySelector(`[data-page="${['wm','vla','idm'].includes(location.hash.slice(1))?location.hash.slice(1):'wm'}"]`).click();
  const loadPhoto=async file=>{
    try {
      if(!file || !/\.(png|jpe?g)$/i.test(file.name)) throw new Error('请选择或拖入一张 PNG/JPG 首帧照片');
      reference.add(await reference.upload(file),file.name);message('首帧参考图已加载，可调节透明度进行对齐');
    } catch(e){message(e.message,true);}
  };
  $('reference-upload').onchange=()=>loadPhoto($('reference-upload').files[0]);
  const drop=$('reference-drop');
  drop.onclick=()=>$('reference-upload').click();
  drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('reference-upload').click();}};
  for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('dragging');});
  for(const event of ['dragleave','drop'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('dragging');});
  drop.addEventListener('drop',e=>{
    if(e.dataTransfer.files.length!==1){message('请一次拖入一张首帧照片',true);return;}
    loadPhoto(e.dataTransfer.files[0]);
  });
  $('episode-video').onchange=async()=>{
    const file=$('episode-video').files[0];if(!file)return;
    if(file.size>256*1024*1024){message('Episode 视频超过 256 MiB，请使用数据集读取',true);return;}
    $('episode-video').disabled=true;$('video-status').textContent='正在上传 Episode 并提取视频首帧…';
    try{
      const response=await fetch('/api/video-first-frame',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
      const data=await response.json();if(!response.ok)throw new Error(data.error);
      reference.add(data.url,file.name+' / 首帧');
      $('video-status').textContent=`${file.name} · 首帧 ${data.width} × ${data.height} · 已作为参考图`;
      message('Episode 视频首帧已提取；可调节透明度，不自动作为 IDM 目标观测');
    }catch(e){$('video-status').textContent='首帧提取失败：'+e.message;message(e.message,true);}
    finally{$('episode-video').disabled=false;}
  };
  const inputs={};
  for(const kind of ['current','target'])$(kind+'-upload').onchange=async()=>{delete inputs[kind];$(kind+'-preview').hidden=true;try{inputs[kind]=await reference.upload($(kind+'-upload').files[0]);$(kind+'-preview').src=inputs[kind];$(kind+'-preview').hidden=false;message(kind+' 观测已上传');}catch(e){message(e.message,true);}};
  action('infer',async()=>{ $('result').textContent='运行中 · MOCK';try{const data=await api('/api/idm',{mode:'mock',current_image:inputs.current,target_image:inputs.target,state:JSON.parse($('state').value)});$('result').textContent=JSON.stringify(data,null,2);message('Mock 测试完成；未执行机器人动作');}catch(e){$('result').textContent='测试失败：'+e.message;throw e;}});
  action('history',async()=>{$('result').textContent=JSON.stringify(await api('/api/records'),null,2);});
  action('prepare',async()=>{const data=await api('/api/vla/prepare',{task_id:$('task').value,camera_id:$('camera').value});message(data.detail);});
  const datasets=await api('/api/datasets');for(const name of datasets)$('dataset').add(new Option(name,name));
  // Give the automatically started dual-camera service time to connect. UI stays usable.
  let attempts=0;
  const connect=async()=>{try{await cameras();}catch(e){
    const service=(await api('/api/status').catch(()=>({}))).camera_service;
    message(service?.detail||e.message,true);
    if(service?.state==='connecting' && attempts++<20)setTimeout(connect,1500);
  }};
  if(status.mode!=='reference_only' && status.preview_enabled) await connect();
}
main().catch(e=>message(e.message,true));
