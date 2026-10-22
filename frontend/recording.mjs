const $ = id => document.getElementById(id);

export class RecordingPanel {
  constructor(mode) {
    this.unavailable=mode==='reference_only';
    this.loaded=false;
    try {$('recording-name').value=localStorage.getItem('idm-recording-class')||'录像';}catch{}
    $('recording-name').addEventListener('input',()=>{
      try {localStorage.setItem('idm-recording-class',$('recording-name').value);}catch{}
    });
    this.poll();
  }
  async request(path) {
    const response=await fetch('/api/recording/'+path,{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'录制请求失败');
    return data;
  }
  render(data) {
    if(!this.loaded){
      if(data.active)$('recording-name').value=data.name;
      this.loaded=true;
    }
    $('recording-directory').textContent=data.directory;
    $('recording-name').disabled=data.active;
    $('recording-status').dataset.state=data.state;
    $('recording-status').textContent=data.error?'录制异常：'+data.error:
      data.state==='saving'?'正在结束录制并保存…':
      data.active?`正在录制 · ${data.duration_seconds.toFixed(1)} 秒 · ${data.frames} 帧`:
      data.state==='saved'?`已保存：${data.last?.name||''}`:
      this.unavailable?'请使用相机模式启动服务后自动录制。':'等待执行 CSV：执行时自动开始录制，结束或停止后自动保存。';
    $('recording-videos').replaceChildren(...data.videos.map(video=>{
      const row=document.createElement('li');
      const link=document.createElement('a');
      link.href=video.url;link.download=video.name;link.textContent=video.name;
      row.append(link);return row;
    }));
  }
  async poll() {
    try {
      const data=await this.request('status');
      this.render(data);
    } catch(error) {
      $('recording-status').textContent='无法读取录制状态：'+error.message;
    } finally {setTimeout(()=>this.poll(),1500);}
  }
}
