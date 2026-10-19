// Full-resolution, same-coordinate RGB comparison. No scaling or registration.
export const MATCH_THRESHOLD = 99;
export const RGB_TOLERANCE = 10;
export const REQUIRED_FRAMES = 3;
export const STALE_MS = 2000;

export function comparePixels(live, reference, tolerance = RGB_TOLERANCE) {
  if (!live.length || live.length !== reference.length || live.length % 4) {
    throw new Error('图像像素尺寸不一致');
  }
  let matched = 0;
  const difference = new Uint8ClampedArray(live.length);
  for (let offset = 0; offset < live.length; offset += 4) {
    const error = Math.max(Math.abs(live[offset] - reference[offset]),
      Math.abs(live[offset + 1] - reference[offset + 1]),
      Math.abs(live[offset + 2] - reference[offset + 2]));
    if (error <= tolerance) matched++;
    difference[offset] = error <= tolerance ? 0 : 255;
    difference[offset + 1] = error <= tolerance ? 0 : Math.min(180, error);
    difference[offset + 2] = 0;
    difference[offset + 3] = 255;
  }
  return {score: 100 * matched / (live.length / 4), difference};
}

export class AlignmentGate {
  constructor() { this.reset(); }
  reset(reason = '等待实时画面与参考图') {
    this.key = null; this.lastId = null; this.lastAt = null;
    this.count = 0; this.score = null; this.reason = reason;
  }
  observe({score, frameId, key, now}) {
    if (this.key !== key) { this.reset(); this.key = key; }
    if (frameId === this.lastId) return this.status(now);
    // A frame arriving after a gap must build a new confirmation streak.
    if (this.lastAt !== null && now - this.lastAt > STALE_MS) this.count = 0;
    this.lastId = frameId; this.lastAt = now; this.score = score;
    this.count = Number.isFinite(score) && score >= MATCH_THRESHOLD ? this.count + 1 : 0;
    this.reason = this.count >= REQUIRED_FRAMES ? '通过' :
      this.count > 0 ? `确认中 ${this.count}/${REQUIRED_FRAMES}` : '未通过';
    return this.status(now);
  }
  status(now) {
    if (this.lastAt !== null && now - this.lastAt > STALE_MS) {
      this.count = 0; this.score = null; this.reason = '画面超时，等待相机新帧';
    }
    return {passed: this.count >= REQUIRED_FRAMES && this.score >= MATCH_THRESHOLD,
      score: this.score, reason: this.reason, count: this.count};
  }
}

export class RealtimeComparison {
  constructor() {
    this.gate = new AlignmentGate();
    this.enabled = false;
    this.frame = null;
    this.liveCanvas = document.createElement('canvas');
    this.referenceCanvas = document.createElement('canvas');
    this.liveContext = this.liveCanvas.getContext('2d', {willReadFrequently: true});
    this.referenceContext = this.referenceCanvas.getContext('2d', {willReadFrequently: true});
    this.referencePixels = null;
    this.referenceLoading = false;
    document.addEventListener('live-frame', event => { this.frame = event.detail; this.update(); });
    const clearLive=reason=>{
      this.frame=null;
      const canvas=document.getElementById('overlay-live');
      canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);
      this.panelStatus('overlay',reason);this.panelStatus('difference',reason);
      this.reset(reason);
    };
    document.addEventListener('live-reset', () => clearLive('等待相机新帧'));
    document.addEventListener('live-error', event => clearLive(event.detail));
    document.addEventListener('reference-reset', () => {
      this.referenceLoading = true;
      this.referencePixels = null; this.reset('参考图加载中，请重新验证');
      this.panelStatus('difference','参考图加载中');
    });
    document.getElementById('reference-image').addEventListener('load', () => {
      this.referenceLoading = false;this.referencePixels = null; this.update();
    });
    document.getElementById('reference-image').addEventListener('error', () => {
      this.referenceLoading=true;this.reset('参考图加载失败');
      this.panelStatus('difference','参考图加载失败，请重新上传');
    });
    document.addEventListener('overlay-changed',()=>this.update());
    document.getElementById('validate').onclick = () => {
      this.enabled = !this.enabled;
      document.getElementById('validate').textContent = this.enabled ? '停止实时验证' : '开始实时验证';
      this.reset(this.enabled ? '等待新帧验证' : '未启用验证');
      if (this.enabled) this.update();
    };
    document.addEventListener('page-changed', () => {
      if (location.hash === '#wm') this.reset('WM 页面不执行对齐验证');
    });
    setInterval(() => this.render(), 250);
    this.render();
  }
  reset(reason) {
    this.gate.reset(reason);
    document.getElementById('difference').getContext('2d').clearRect(0, 0,
      document.getElementById('difference').width, document.getElementById('difference').height);
    this.render();
  }
  panelStatus(panel,text){
    const label=document.getElementById(panel+'-status');
    if(label)label.textContent=text;
  }
  update() {
    const live = document.getElementById('live');
    const reference = document.getElementById('reference-image');
    if (!this.frame) return;
    const source=this.frame.image||live;
    if(!this.frame.image && (!live.complete || !live.naturalWidth))return;
    const width = this.frame.image?.width||live.naturalWidth;
    const height = this.frame.image?.height||live.naturalHeight;
    const referenceReady=!this.referenceLoading && reference.getAttribute('src') && reference.complete && reference.naturalWidth;
    const blend = document.getElementById('overlay-live');
    if (blend.width !== width || blend.height !== height) { blend.width = width; blend.height = height; }
    const blendContext=blend.getContext('2d');
    blendContext.globalAlpha=1;
    blendContext.drawImage(source, 0, 0);
    if(referenceReady && document.getElementById('show-reference').checked){
      blendContext.globalAlpha=Math.min(100,Math.max(0,Number(document.getElementById('alpha').value)))/100;
      blendContext.drawImage(reference,0,0,width,height);
      blendContext.globalAlpha=1;
    }
    blend.dataset.frameId=this.frame.frameId;
    this.panelStatus('overlay',`实时更新 · 帧 ${this.frame.frameId}${referenceReady?'':' · 请加载参考图'}`);
    if (location.hash === '#wm') return;
    if (!referenceReady) {
      if(this.gate.reason!=='请加载一张参考图')this.reset('请加载一张参考图');
      this.panelStatus('difference',this.referenceLoading?'参考图加载中或加载失败，请重新选择':'请加载一张参考图');
      return;
    }
    if (width !== reference.naturalWidth || height !== reference.naturalHeight) {
      this.reset(`尺寸不一致：相机 ${width}×${height}，参考图 ${reference.naturalWidth}×${reference.naturalHeight}`);
      this.panelStatus('difference',this.gate.reason);
      return;
    }
    try {
      if (this.liveCanvas.width !== width || this.liveCanvas.height !== height) {
        this.liveCanvas.width = width; this.liveCanvas.height = height;
      }
      if (!this.referencePixels) {
        this.referenceCanvas.width = width; this.referenceCanvas.height = height;
        this.referenceContext.drawImage(reference, 0, 0);
        this.referencePixels = this.referenceContext.getImageData(0, 0, width, height).data;
      }
      this.liveContext.drawImage(source, 0, 0);
      const result = comparePixels(this.liveContext.getImageData(0, 0, width, height).data, this.referencePixels);
      if(this.enabled)this.gate.observe({score: result.score, frameId: this.frame.frameId,
        key: this.frame.cameraId + '|' + reference.src, now: this.frame.receivedAt});
      const difference = document.getElementById('difference');
      if(difference.width!==width || difference.height!==height){
        difference.width = width; difference.height = height;
      }
      difference.getContext('2d').putImageData(new ImageData(result.difference, width, height), 0, 0);
      difference.dataset.frameId=this.frame.frameId;
      this.panelStatus('difference',`实时更新 · 帧 ${this.frame.frameId}`);
      this.render();
    } catch (error) { this.reset('对比失败：' + error.message);this.panelStatus('difference',this.gate.reason); }
  }
  render() {
    const result = this.gate.status(performance.now());
    if(this.frame && performance.now()-this.frame.receivedAt>STALE_MS){
      this.panelStatus('overlay','画面已停止更新，等待相机新帧');
      this.panelStatus('difference','画面已停止更新，等待相机新帧');
    }
    const badge = document.getElementById('comparison-result');
    const active = this.enabled && location.hash !== '#wm';
    badge.textContent = active ? result.reason : this.enabled ? 'WM 页面不执行对齐验证' : '未启用验证';
    badge.dataset.state = active && result.passed ? 'passed' : 'pending';
    document.getElementById('match-score').textContent = active && result.score !== null ?
      (Math.floor(result.score * 1000) / 1000).toFixed(3) + '%' : '—';
  }
}
