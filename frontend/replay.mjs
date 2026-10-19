const $ = id => document.getElementById(id);
const ACTIVE = ['checking', 'starting', 'running', 'stopping'];
const LABELS = {idle:'未运行', checking:'正在启动回放程序', starting:'正在初始化夹爪并移动到首点',
  running:'正在下发动作', completed:'指令下发完成（未确认最终到位）', stopped:'已停止',
  stopping:'正在请求停止', stop_error:'停止未确认，请检查机械臂', error:'执行失败'};

export class ActionReplay {
  constructor(capabilities) {
    this.capabilities=capabilities;this.job=null;this.busy=false;this.state='idle';this.polling=false;
    this.showCapabilities();
    $('replay-confirm').onchange=()=>this.buttons();
    $('action-csv').onchange=()=>this.upload($('action-csv').files);
    const drop=$('action-drop');
    drop.onclick=()=>$('action-csv').click();
    drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('action-csv').click();}};
    for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('dragging');});
    for(const event of ['dragleave','drop'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('dragging');});
    drop.addEventListener('drop',e=>this.upload(e.dataTransfer.files));
    $('replay-start').onclick=async()=>{
      this.busy=true;this.buttons();
      try {
        const result=await this.request('/api/replay/start',{job_id:this.job.id,confirmed:$('replay-confirm').checked});
        $('replay-confirm').checked=false;this.showStatus(result);
      } catch(error){$('replay-status').textContent=error.message;}
      finally{this.busy=false;this.buttons();}
    };
    $('replay-stop').onclick=async()=>{
      $('replay-stop').disabled=true;
      try{this.showStatus(await this.request('/api/replay/stop',{}));}
      catch(error){$('replay-status').textContent='停止请求失败：'+error.message+'；请使用机械臂实体停止装置';}
      finally{this.buttons();}
    };
    this.buttons();this.poll();setInterval(()=>this.poll(),750);
  }
  showCapabilities(){
    $('replay-ip').textContent=this.capabilities.robot_ip;
    $('replay-enabled').textContent=this.capabilities.enabled ? '真实回放已启用，只有点击执行才发送动作' : 'DEMO：支持 CSV 转换与离线检查，真实动作禁用';
  }
  async refreshAuthorization(){
    // An open tab can outlive the server process and its authorization token.
    const response=await fetch('/api/status',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'无法更新控制台授权，请刷新页面');
    if(!data.replay?.token)throw new Error('无法获取控制台授权，请刷新页面');
    Object.assign(this.capabilities,data.replay);
    this.showCapabilities();this.buttons();
  }
  async request(path,body,raw=false){
    const response=await fetch(path,{method:'POST',headers:{'X-Replay-Token':this.capabilities.token,
      'Content-Type':raw?'text/csv':'application/json'},body:raw?body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.error);return data;
  }
  buttons(){
    const active=ACTIVE.includes(this.state)||this.state==='stop_error';
    $('action-csv').disabled=this.busy||active;
    $('replay-start').disabled=this.busy||active||!this.job||!this.capabilities.enabled||!$('replay-confirm').checked;
    $('replay-stop').disabled=this.state==='idle'||this.state==='stopped'||this.state==='stopping';
  }
  async upload(files){
    if(this.busy||ACTIVE.includes(this.state)||this.state==='stop_error')return;
    this.job=null;$('replay-confirm').checked=false;$('action-download').hidden=true;$('action-summary').textContent='';
    if(files.length!==1||!files[0].name.toLowerCase().endsWith('.csv')){
      $('action-summary').textContent='请一次选择或拖入一个 CSV 动作文件';this.buttons();return;
    }
    this.busy=true;this.buttons();$('action-summary').textContent='正在自动转换并运行离线校验，不连接机械臂…';
    try{
      await this.refreshAuthorization();
      const result=await this.request('/api/replay/upload',files[0],true);this.job=result;
      const vector=values=>values.map(value=>value.toFixed(2)).join(', ');
      $('action-summary').textContent=[`文件：${files[0].name}`,`格式：${result.format} · ${result.frames} 帧 · ${result.fps.toFixed(3)} Hz · ${result.duration_s.toFixed(2)} 秒`,
        `起点 XYZ（mm）：[${vector(result.start_xyz_mm)}]`,`终点 XYZ（mm）：[${vector(result.end_xyz_mm)}]`,
        `XYZ 最小值（mm）：[${vector(result.xyz_min_mm)}]`,`XYZ 最大值（mm）：[${vector(result.xyz_max_mm)}]`,
        `最大相邻位移：${result.max_step_mm.toFixed(2)} mm`,`夹爪开口：${vector(result.gripper_opening_mm)} mm`,
        '离线校验通过；未验证环境碰撞、实际 TCP 标定与坐标系。'].join('\n');
      $('action-download').href=result.action_url;$('action-download').hidden=false;
      $('action-dry-log').textContent=result.dry_run_log;
    }catch(error){$('action-summary').textContent='转换失败：'+error.message;$('action-dry-log').textContent='';}
    finally{this.busy=false;this.buttons();}
  }
  showStatus(data){
    this.state=data.status;
    $('replay-status').textContent=(LABELS[data.status]||data.status)+(data.error?'：'+data.error:'')+(data.stop_detail?' · '+data.stop_detail:'');
    $('replay-status').dataset.state=data.status;
    $('replay-log').textContent=(data.logs||[]).join('\n');
    this.buttons();
  }
  async poll(){
    if(this.polling)return;this.polling=true;
    try{const response=await fetch('/api/replay/status',{cache:'no-store'});
      if(!response.ok)throw new Error('状态接口不可用');this.showStatus(await response.json());
    }catch(error){$('replay-status').textContent='无法获取回放状态：'+error.message+'；执行期间请检查机械臂';}
    finally{this.polling=false;}
  }
}
