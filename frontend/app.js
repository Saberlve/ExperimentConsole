import {VLAEvaluation} from '/evaluation.mjs';
import {ActionReplay} from '/replay.mjs';
import {RealtimeComparison} from '/comparison.mjs';
import {RecordingPanel} from '/recording.mjs';
const $ = id => document.getElementById(id);

class CameraDrawer {
  constructor() {
    this.tile=$('live').closest('.comparison-tile');
    this.home=document.createComment('live camera home');
    this.tile.before(this.home);
    $('camera-drawer-close').onclick=()=>this.close();
    document.addEventListener('suite-editor-open',()=>this.open());
    $('vla-camera-drawer').addEventListener('keydown',event=>{
      if(event.key==='Escape'){this.close();$('refresh').focus();}
    });
    document.addEventListener('live-reset',()=>this.status('等待相机连接…'));
    document.addEventListener('live-frame',()=>{
      this.status('实时预览 · '+($('camera').selectedOptions[0]?.textContent||'当前相机'));
      $('vla-camera-drawer').dataset.state='ready';
    });
    document.addEventListener('live-error',event=>this.status(event.detail,'error'));
    document.addEventListener('console-camera-paused',()=>this.close());
    document.addEventListener('page-changed',()=>{
      if(location.hash!=='#vla')this.close();
    });
    window.addEventListener('hashchange',()=>{
      if(location.hash!=='#vla')this.close();
    });
  }
  status(text,state='loading') {
    $('camera-drawer-status').textContent=text;
    $('vla-camera-drawer').dataset.state=state;
  }
  open(connecting=false) {
    if(location.hash!=='#vla')return;
    if(connecting)this.status('正在连接相机…');
    else if(!$('live').getAttribute('src'))this.status('相机未开启，点击左侧“开启 / 刷新相机”查看当前场景。','idle');
    $('camera-drawer-view').append(this.tile);
    $('vla-camera-drawer').inert=false;
    $('console-stage').classList.add('camera-drawer-open');
    $('refresh').setAttribute('aria-expanded','true');
    $('camera-drawer-close').textContent='返回 VLA 评测';
    $('camera-drawer-close').setAttribute('aria-label','返回 VLA 评测');
  }
  close() {
    if($('vla-camera-drawer').contains(document.activeElement))$('refresh').focus();
    $('console-stage').classList.remove('camera-drawer-open');
    $('refresh').setAttribute('aria-expanded','false');
    $('vla-camera-drawer').inert=true;
    this.home.after(this.tile);
  }
}

class TrainingOverlayPanel {
  constructor(token) {
    this.token=token;
    $('training-overlay-root').addEventListener('input',()=>{
      $('training-overlay-view').replaceChildren(new Option('自动选择全景视角','auto'));
    });
    action('training-overlay-generate',()=>this.generate());
  }
  async generate() {
    const root=$('training-overlay-root').value.trim();
    if(!root){$('training-overlay-status').textContent='请填写服务器上的 LeRobot v3 训练集路径';return;}
    const status=$('training-overlay-status');
    const previous=!$('training-overlay-result').hidden?'（下方保留上次生成的图片）':'';
    const fields=[$('training-overlay-root'),$('training-overlay-view')];
    fields.forEach(field=>field.disabled=true);
    status.classList.remove('error');
    status.textContent='正在读取训练集元数据…'+previous;
    try {
      let job=await api('/api/evaluation/training-overlay',{
        root,view:$('training-overlay-view').value
      },{'X-Replay-Token':this.token});
      while(job.state==='running'){
        status.textContent=(job.total?`正在提取首帧 ${job.completed} / ${job.total}…`:'正在读取训练集元数据…')+previous;
        await new Promise(resolve=>setTimeout(resolve,500));
        job=await api('/api/evaluation/training-overlay?id='+encodeURIComponent(job.id));
      }
      if(job.views){
        $('training-overlay-view').replaceChildren(...job.views.map(view=>new Option(view,view)));
        $('training-overlay-view').value=job.view;
      }
      if(job.state!=='done')throw Error(job.error||'首帧叠加生成失败');
      // This is an independent static image, never a live-camera overlay.
      const image=new Image();image.src=job.url;await image.decode();
      $('training-overlay-image').src=job.url;
      $('training-overlay-link').href=job.url;
      $('training-overlay-caption').textContent=`${job.root} · ${job.view} · ${job.total} 个 episode · 等权叠加 · ${job.width} × ${job.height}`;
      $('training-overlay-result').hidden=false;
      status.textContent=`已生成 ${job.total} 张首帧的静态叠加图。可点击图片放大；切换视角后点击按钮重新生成。`;
    } catch(error) {
      status.textContent='生成失败：'+error.message+previous;
      status.classList.add('error');
    } finally { fields.forEach(field=>field.disabled=false); }
  }
}

export async function api(path, body, headers={}) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json',...headers}, body:JSON.stringify(body)});
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
  constructor(mode) { this.mode=mode; this.timer=null; this.generation=0; this.url=null; this.controller=null; this.bitmap=null; }
  select(camera) {
    const generation=++this.generation;
    clearTimeout(this.timer); this.controller?.abort();
    $('live').removeAttribute('src');
    if(this.url){URL.revokeObjectURL(this.url);this.url=null;}
    document.dispatchEvent(new CustomEvent('live-reset'));
    this.bitmap?.close();this.bitmap=null;
    if (!camera) return;
    const update=async()=>{
      const started=performance.now();
      let url=null,bitmap=null,requestTimer=null,failed=false;
      try {
        const controller=new AbortController();
        this.controller=controller;
        requestTimer=setTimeout(()=>controller.abort(),2000);
        const response=await fetch('/api/frame?camera='+encodeURIComponent(camera),
          {cache:'no-store',signal:controller.signal});
        if(!response.ok){const error=await response.json();throw new Error(error.error);}
        const frameId=response.headers.get('X-Frame-Id');
        if(!frameId)throw new Error('相机未提供帧序号，无法实时验证');
        const blob=await response.blob();
        if(generation!==this.generation)return;
        bitmap=await createImageBitmap(blob);
        if(generation!==this.generation){bitmap.close();return;}
        url=URL.createObjectURL(blob);
        $('live').src=url;
        await $('live').decode();
        if(generation!==this.generation){URL.revokeObjectURL(url);bitmap.close();return;}
        const old=this.url;this.url=url;if(old)URL.revokeObjectURL(old);
        const oldBitmap=this.bitmap;this.bitmap=bitmap;
        document.dispatchEvent(new CustomEvent('live-frame',{detail:{
          cameraId:camera,frameId,receivedAt:performance.now(),image:bitmap}}));
        if(this.failed){message('相机画面已恢复');this.failed=false;}
        oldBitmap?.close();
      } catch(e) {
        failed=true;
        if(url && url!==this.url)URL.revokeObjectURL(url);
        if(bitmap && bitmap!==this.bitmap)bitmap.close();
        if(generation!==this.generation)return;
        this.failed=true;
        document.dispatchEvent(new CustomEvent('live-error',{detail:'相机画面不可用：'+e.message}));
        message('相机画面不可用：'+e.message,true);
      } finally {
        clearTimeout(requestTimer);
        // Budget acquisition, decoding and comparison together; never queue old frames.
        if(generation===this.generation)this.timer=setTimeout(update,
          failed?500:Math.max(0,1000/30-(performance.now()-started)));
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
    $('overlay').hidden=true;
    $('alpha-value').value=$('alpha').value+'%';
    const a=$('live'),b=$('overlay');
    $('dimension-warning').textContent=b.naturalWidth && a.naturalWidth && Math.abs(a.naturalWidth/a.naturalHeight-b.naturalWidth/b.naturalHeight)>.01 ? '注意：参考图宽高比与相机不同，当前按相机画幅拉伸显示。' : '';
    document.dispatchEvent(new CustomEvent('overlay-changed'));
  }
  set(url) { document.dispatchEvent(new CustomEvent('reference-reset')); $('overlay').onload=()=>this.render();$('overlay').src=url; $('overlay').hidden=true; $('reference-image').src=url; $('reference-preview').hidden=!url; this.render(); }
}
export class ReferenceImageSelector {
  constructor(overlay) { this.overlay=overlay; $('references').onchange=()=>overlay.set($('references').value); }
  add(url,label) { let option=[...$('references').options].find(o=>o.value===url);if(!option){option=new Option(label,url);$('references').add(option);}option.textContent=label;option.selected=true;this.overlay.set(url); }
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
  constructor(captureSuite) { action('snapshot',async()=>{
    if(location.hash==='#vla'){await captureSuite();return;}
    const data=await api('/api/snapshot',{camera_id:$('camera').value,task_id:$('task').value});
    $('snapshots').replaceChildren();
    for(const frame of data.frames){ const item=document.createElement('div'),image=document.createElement('img'),link=document.createElement('a'),metadata=document.createElement('a'); image.src=frame.url;image.alt=frame.camera_id;link.href=frame.url;link.textContent='原始 PNG';link.download='';metadata.href=frame.url.replace(/\.png$/,'.json');metadata.textContent='元数据 JSON';metadata.target='_blank';item.append(image,document.createElement('br'),link,metadata);$('snapshots').append(item); }
    message(data.pair_capture?'已通过旧服务保存双相机原图和元数据':'已保存 DEMO 合成图和元数据');
  }); }
}
async function main(){
  const status=await api('/api/status');$('mode').textContent=`${status.mode==='demo'?'DEMO · 合成相机，无真实硬件':(status.mode==='reference_only'?'布局记录模式 · 相机由终端评测使用':'真实相机 · 复用原首帧 HTTP 服务')} | 机械臂回放：${status.robot_execution?'手动执行已启用':'禁用'}`;
  $('capture-note').textContent=status.mode==='demo'?'当前保存的是合成图。':'旧接口固定保存双相机；选择器控制预览。真实采集时间、设备时间域及内参不可获取时标记 null。';
  const drawer=new CameraDrawer();
  new TrainingOverlayPanel(status.replay.token);
  const camera=new CameraViewer(status.mode),overlay=new ImageOverlay(),reference=new ReferenceImageSelector(overlay);
  new RealtimeComparison();new DatasetSelector(reference);
  new ActionReplay(status.replay);
  const evaluation=new VLAEvaluation(reference,status,message);
  const alignmentParts=['alignment','comparison-panels','comparison-tools'].map(id=>{
    const element=$(id),home=document.createComment(id+' home');element.before(home);
    return {element,home};
  });
  const placeAlignment=()=>{
    for(const {element,home} of alignmentParts){
      if(location.hash==='#vla'&&evaluation.run)$('ve-alignment').append(element);
      else home.after(element);
    }
  };
  document.addEventListener('evaluation-layout-ready',placeAlignment);
  document.addEventListener('page-changed',placeAlignment);
  new SnapshotButton(()=>{drawer.open();return evaluation.guard(()=>evaluation.captureLayout());});
  await evaluation.load();
  if(status.mode==='reference_only'){ $('snapshot').disabled=true; $('refresh').disabled=true; $('capture-note').textContent='上传提前拍好的布局图；本模式不打开相机，不影响终端评测。'; }
  const cameras=async(isCurrent=()=>true)=>{const selected=$('reference-image').dataset.cameraId||$('camera').value;const data=await api('/api/cameras');if(!isCurrent())return;$('camera').replaceChildren(...data.map(d=>{const o=new Option(`${d.label||d.name||d.serial} · ${d.serial}${d.error?' · '+d.error:''}`,d.serial);o.disabled=!d.connected||!!d.error;return o;}));if(data.some(d=>d.serial===selected))$('camera').value=selected;camera.select($('camera').value);message('相机列表已刷新');};
  let connectionGeneration=0;
  let cameraRelease=Promise.resolve();
  evaluation.waitForCameraRelease=()=>cameraRelease.catch(()=>{});
  let releasingCamera=false;
  const releaseCamera=()=>{
    if(releasingCamera)return cameraRelease;
    releasingCamera=true;
    $('idm-camera-status').textContent='正在断开后台相机连接…';
    document.dispatchEvent(new CustomEvent('console-camera-paused'));
    const release=cameraRelease.catch(()=>{}).then(async()=>{
      await api('/api/evaluation/camera/release',{}, {'X-Replay-Token':status.replay.token});
      $('idm-camera-status').textContent='相机已断开，设备已释放，可启动 VLA 或其他程序。';
      message('相机已释放，可以启动 VLA；需要预览时点击“开启 / 刷新相机”');
    });
    cameraRelease=release.catch(e=>{$('idm-camera-status').textContent='断开失败：'+e.message;throw e;}).finally(()=>{releasingCamera=false;});
    return cameraRelease;
  };
  action('release-camera',releaseCamera);
  action('idm-camera-disconnect',releaseCamera);
  const connect=async(enable=false)=>{
    const generation=++connectionGeneration;
    let attempts=0;
    const poll=async()=>{try{
      if(generation!==connectionGeneration)return;
      await cameras(()=>generation===connectionGeneration);
    }catch(e){
      if(generation!==connectionGeneration)return;
      const current=await api('/api/status').catch(()=>({}));
      if(generation!==connectionGeneration)return;
      message(current.camera_service?.detail||e.message,true);
      document.dispatchEvent(new CustomEvent('live-error',{detail:current.camera_service?.detail||e.message}));
      if(current.preview_enabled && current.camera_service?.state==='connecting' && attempts++<20)
        setTimeout(poll,1500);
    }};
    try {
      await cameraRelease.catch(()=>{});
      if(generation!==connectionGeneration)return;
      const current=await api('/api/status');
      if(generation!==connectionGeneration)return;
      if(!current.preview_enabled){
        if(!enable)return;
        await api('/api/evaluation/camera/enable',{}, {'X-Replay-Token':current.replay.token});
      }
      $('idm-camera-status').textContent='网页相机预览已开启；使用 VLA 前请点击“断开相机连接”。';
      await poll();
    }catch(e){document.dispatchEvent(new CustomEvent('live-error',{detail:e.message}));message(e.message,true);}
  };
  document.addEventListener('console-camera-paused',()=>{++connectionGeneration;++evaluation.cameraGeneration;camera.select('');message('相机预览已关闭');});
  document.addEventListener('console-camera-enabled',()=>connect());
  $('camera').onchange=()=>camera.select($('camera').value);action('refresh',()=>{drawer.open(true);return connect(true);});
  let pageInitialized=false;
  for(const button of document.querySelectorAll('[data-page]'))button.onclick=()=>{
    const page=button.dataset.page;
    const isVla=page==='vla';
    $('console-stage').dataset.page=page;
    $('reference-controls').hidden=isVla;
    $('comparison-panels').hidden=false;
    $('idm-camera-controls').hidden=page!=='idm';
    $('recording-panel').hidden=page!=='idm';
    $('comparison-validation').hidden=isVla;
    if(isVla){$('alpha').value=50;$('show-reference').checked=true;$('alpha').dispatchEvent(new Event('input'));}

    for(const element of document.querySelectorAll('[data-reference-import]'))element.hidden=isVla;
    $('reference-tools-title').textContent=isVla?'布局对齐':'参考图与数据集';
    $('capture-note').hidden=isVla;$('snapshots-title').hidden=isVla;$('snapshots').hidden=isVla;
    $('reference-preview-help').textContent=isVla?'点击测试集中的布局图片，加载为对齐参考图。':'上传照片、拖入首帧或从 Episode 提取，参考图固定显示。';
    $('alignment-help').textContent=isVla?'调整透明度，对照参考图恢复物体布局。':'叠加仅用于显示；保存和推理使用原图。不同宽高比将提示，请使用同视角、同分辨率参考图。';
    if(isVla)$('ve-builder-fields').insertBefore($('snapshot'),$('ve-save-suite'));else $('snapshot-home').append($('snapshot'));
    $('vla-suite-builder').hidden=page!=='vla';$('task').closest('label').hidden=page==='vla';$('snapshot').textContent=page==='vla'?'拍摄当前视角，加入 suite':'拍摄并保存原图 / 重新拍摄'; $('title').textContent=button.textContent;$('alignment').hidden=page==='wm';$('overlay').hidden=true;$('reference-preview').hidden=false;$('comparison-tools').hidden=page==='wm';$('comparison-panels').classList.toggle('wm',page==='wm');$('idm-panel').hidden=page!=='idm';$('action-replay-panel').hidden=page!=='idm';$('prepare').hidden=true;$('vla-evaluation').hidden=page!=='vla';$('vla-results').hidden=page!=='vla';
    for(const b of document.querySelectorAll('[data-page]'))b.classList.toggle('active',b===button);
    location.hash=page;
    document.dispatchEvent(new CustomEvent('page-changed'));
    if(pageInitialized && status.mode!=='reference_only' && (page==='wm'||(isVla&&evaluation.run?.trials[evaluation.run.cursor].status==='pending')))connect(true);
    if(isVla&&!evaluation.run)releaseCamera().catch(e=>message('相机释放失败：'+e.message,true));
  };
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
  const loadVideo=async file=>{
    if(!file||$('episode-video').disabled)return;
    if(!/\.(mp4|mov|avi|mkv)$/i.test(file.name)){message('请选择或拖入 MP4 / MOV / AVI / MKV 视频',true);return;}
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
  $('episode-video').onchange=()=>loadVideo($('episode-video').files[0]);
  const videoDrop=$('video-drop');
  videoDrop.onclick=()=>$('episode-video').click();
  videoDrop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('episode-video').click();}};
  for(const event of ['dragenter','dragover'])videoDrop.addEventListener(event,e=>{e.preventDefault();videoDrop.classList.add('dragging');});
  for(const event of ['dragleave','drop'])videoDrop.addEventListener(event,e=>{e.preventDefault();videoDrop.classList.remove('dragging');});
  videoDrop.addEventListener('drop',e=>{
    if(e.dataTransfer.files.length!==1){message('请一次拖入一个视频',true);return;}
    loadVideo(e.dataTransfer.files[0]);
  });
  new RecordingPanel(status.mode);
  const inputs={};
  for(const kind of ['current','target'])$(kind+'-upload').onchange=async()=>{delete inputs[kind];$(kind+'-preview').hidden=true;try{inputs[kind]=await reference.upload($(kind+'-upload').files[0]);$(kind+'-preview').src=inputs[kind];$(kind+'-preview').hidden=false;message(kind+' 观测已上传');}catch(e){message(e.message,true);}};
  action('infer',async()=>{ $('result').textContent='运行中 · MOCK';try{const data=await api('/api/idm',{mode:'mock',current_image:inputs.current,target_image:inputs.target,state:JSON.parse($('state').value)});$('result').textContent=JSON.stringify(data,null,2);message('Mock 测试完成；未执行机器人动作');}catch(e){$('result').textContent='测试失败：'+e.message;throw e;}});
  action('history',async()=>{$('result').textContent=JSON.stringify(await api('/api/records'),null,2);});
  action('prepare',async()=>{const data=await api('/api/vla/prepare',{task_id:$('task').value,camera_id:$('camera').value});message(data.detail);});
  evaluation.startSessionTracking();
  const datasets=await api('/api/datasets');for(const name of datasets)$('dataset').add(new Option(name,name));
  const initialHash=window.initialWorkbenchHash??location.hash;
  const initialPage=['wm','vla','idm'].includes(initialHash.slice(1))?initialHash.slice(1):'wm';
  document.querySelector(`[data-page="${initialPage}"]`).click();
  pageInitialized=true;
  if(status.mode!=='reference_only'){
    if(initialHash==='#wm')await connect(true);
    else if(initialPage==='vla'&&evaluation.run){
      await evaluation.pollControl();
      if(evaluation.run.trials[evaluation.run.cursor].status==='pending'&&!evaluation.controlState?.jobs.evaluation?.running&&!evaluation.controlState?.jobs.reset?.running)await evaluation.openCamera();
    }
    else if(initialPage!=='vla'&&status.preview_enabled)await connect();
  }
}
window.consoleReady=main().catch(e=>{message(e.message,true);throw e;});
