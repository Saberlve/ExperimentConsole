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
    document.addEventListener('live-frame', event => { this.frame = event.detail; this.update(); });
    document.addEventListener('live-reset', () => { this.frame = null; this.reset('等待相机新帧'); });
    document.addEventListener('live-error', event => { this.frame = null; this.reset(event.detail); });
    document.addEventListener('reference-reset', () => {
      this.referencePixels = null; this.reset('参考图加载中，请重新验证');
    });
    document.getElementById('reference-image').addEventListener('load', () => {
      this.referencePixels = null; this.update();
    });
    document.getElementById('reference-image').addEventListener('error', () => this.reset('参考图加载失败'));
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
  update() {
    const live = document.getElementById('live');
    const reference = document.getElementById('reference-image');
    if (!this.frame || !live.complete || !live.naturalWidth) return;
    const width = live.naturalWidth, height = live.naturalHeight;
    const blend = document.getElementById('overlay-live');
    if (blend.width !== width || blend.height !== height) { blend.width = width; blend.height = height; }
    blend.getContext('2d').drawImage(live, 0, 0);
    if (!this.enabled || location.hash === '#wm') return;
    if (!reference.getAttribute('src') || !reference.complete || !reference.naturalWidth) {
      this.reset('请加载一张参考图'); return;
    }
    if (width !== reference.naturalWidth || height !== reference.naturalHeight) {
      this.reset(`尺寸不一致：相机 ${width}×${height}，参考图 ${reference.naturalWidth}×${reference.naturalHeight}`);
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
      this.liveContext.drawImage(live, 0, 0);
      const result = comparePixels(this.liveContext.getImageData(0, 0, width, height).data, this.referencePixels);
      this.gate.observe({score: result.score, frameId: this.frame.frameId,
        key: this.frame.cameraId + '|' + reference.src, now: this.frame.receivedAt});
      const difference = document.getElementById('difference');
      difference.width = width; difference.height = height;
      difference.getContext('2d').putImageData(new ImageData(result.difference, width, height), 0, 0);
      this.render();
    } catch (error) { this.reset('对比失败：' + error.message); }
  }
  render() {
    const result = this.gate.status(performance.now());
    const badge = document.getElementById('comparison-result');
    const active = this.enabled && location.hash !== '#wm';
    badge.textContent = active ? result.reason : this.enabled ? 'WM 页面不执行对齐验证' : '未启用验证';
    badge.dataset.state = active && result.passed ? 'passed' : 'pending';
    document.getElementById('match-score').textContent = active && result.score !== null ?
      (Math.floor(result.score * 1000) / 1000).toFixed(3) + '%' : '—';
  }
}
