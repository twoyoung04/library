import { BrowserMultiFormatOneDReader } from '@zxing/browser';
import { createWorker } from 'tesseract.js';

const isbnField = document.getElementById('id_isbn');
const statusEl = document.getElementById('scan-status');
const cameraButton = document.getElementById('camera-button');
const photoInput = document.getElementById('photo-input');
const preview = document.getElementById('camera-preview');
const duplicateNotice = document.getElementById('duplicate-notice');
const reader = new BrowserMultiFormatOneDReader();
let cameraControls = null;
let cameraStream = null;
let cameraStarting = false;
let cameraRun = 0;
let busy = false;

function message(text, kind = '') {
  statusEl.textContent = text;
  statusEl.className = `scan-status ${kind}`;
}

function checkDigit(stem) {
  let sum = 0;
  for (let i = 0; i < stem.length; i++) sum += Number(stem[i]) * (i % 2 ? 3 : 1);
  return String((10 - sum % 10) % 10);
}

function normalize(raw) {
  let digits = String(raw || '').replace(/[^0-9Xx]/g, '').toUpperCase();
  if (digits.length === 10) {
    let sum = 0;
    for (let i = 0; i < 9; i++) sum += Number(digits[i]) * (10 - i);
    sum += digits[9] === 'X' ? 10 : Number(digits[9]);
    if (!Number.isFinite(sum) || sum % 11) return null;
    digits = '978' + digits.slice(0, 9);
    digits += checkDigit(digits);
  }
  if (digits.length !== 13 || !/^97[89]\d{10}$/.test(digits)) return null;
  return checkDigit(digits.slice(0, 12)) === digits[12] ? digits : null;
}

async function lookup(raw) {
  const isbn = normalize(raw);
  if (!isbn) {
    message('未识别到有效的 ISBN，请核对数字或手动输入。', 'error');
    return;
  }
  isbnField.value = isbn;
  message('正在查询图书资料…');
  try {
    const response = await fetch(`/api/lookup/?isbn=${encodeURIComponent(isbn)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '查询失败');
    for (const name of ['title', 'author', 'publisher', 'published_year', 'cover_url']) {
      const field = document.getElementById(`id_${name}`);
      if (field && data[name] && (!field.value || data.existing)) field.value = data[name];
    }
    if (data.existing) {
      duplicateNotice.hidden = false;
      duplicateNotice.textContent = `已收录《${data.title}》${data.copy_count} 册。保存后将为这本书新增一册，不会覆盖原有图书资料。`;
      message('找到已有图书，可继续填写这册书的位置和状态。', 'success');
    } else if (data.title) {
      duplicateNotice.hidden = true;
      message(`已从 ${data.source} 填入资料，请核对后保存。`, 'success');
    } else {
      duplicateNotice.hidden = true;
      message('没有查到书目资料，请手动填写书名。');
    }
  } catch (error) {
    message(`查询暂不可用：${error.message}。仍可手动填写并保存。`, 'error');
  }
}

function stopCamera() {
  cameraRun += 1;
  if (cameraControls) cameraControls.stop();
  cameraControls = null;
  if (cameraStream) cameraStream.getTracks().forEach(track => track.stop());
  cameraStream = null;
  preview.hidden = true;
  cameraButton.textContent = '打开摄像头';
}

async function applyMobileZoom(stream) {
  if (!window.matchMedia?.('(pointer: coarse)').matches) return null;
  const track = stream.getVideoTracks()[0];
  let range;
  try { range = track?.getCapabilities?.().zoom; } catch (_) { return null; }
  if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max) || range.max <= 1) return null;

  let zoom = Math.min(2, range.max);
  zoom = Math.max(range.min, zoom);
  if (Number.isFinite(range.step) && range.step > 0) {
    zoom = range.min + Math.round((zoom - range.min) / range.step) * range.step;
    zoom = Math.min(range.max, Math.max(range.min, zoom));
  }
  if (zoom <= 1) return null;

  try {
    await track.applyConstraints({ advanced: [{ zoom }] });
    const actual = track.getSettings?.().zoom;
    return Number.isFinite(actual) ? actual : zoom;
  } catch (_) {
    return null;
  }
}

cameraButton.addEventListener('click', async () => {
  if (cameraControls) { stopCamera(); return; }
  if (cameraStarting) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    message('当前页面无法打开摄像头。手机访问需要 HTTPS；也可选择“拍照 / 上传图片”。', 'error');
    return;
  }
  cameraStarting = true;
  cameraButton.disabled = true;
  const run = ++cameraRun;
  try {
    message('正在打开后置摄像头…');
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });
    if (run !== cameraRun) { stream.getTracks().forEach(track => track.stop()); return; }
    cameraStream = stream;
    const zoom = await applyMobileZoom(stream);
    if (run !== cameraRun) return;
    message(zoom && zoom > 1
      ? `已启用约 ${zoom.toFixed(1)} 倍变焦，请将书背条码放入画面中…`
      : '请将书背条码放入画面中…');
    preview.hidden = false;
    const controls = await reader.decodeFromStream(stream, preview, (result, error, controls) => {
      if (!result || busy) return;
      const isbn = normalize(result.getText());
      if (!isbn) return;
      busy = true;
      cameraControls = controls;
      stopCamera();
      lookup(isbn).finally(() => { busy = false; });
    });
    if (run !== cameraRun) { controls.stop(); return; }
    cameraControls = controls;
    cameraButton.textContent = '关闭摄像头';
  } catch (error) {
    stopCamera();
    message('摄像头未能启动，请检查权限，或改用拍照上传。', 'error');
  } finally {
    cameraStarting = false;
    cameraButton.disabled = false;
  }
});

async function ocrIsbn(file) {
  const root = '/static/catalog/vendor';
  const worker = await createWorker('eng', 1, {
    workerPath: `${root}/worker.min.js`,
    corePath: `${root}/core`,
    langPath: `${root}/lang/`,
  });
  try {
    await worker.setParameters({ tessedit_char_whitelist: '0123456789XxISBNisbn- ' });
    const { data } = await worker.recognize(file);
    const text = data.text || '';
    const patterns = [/(?:97[89](?:[\s-]?\d){10})/g, /(?:\d(?:[\s-]?\d){8}[\s-]?[\dXx])/g];
    for (const pattern of patterns) {
      for (const match of text.matchAll(pattern)) {
        const isbn = normalize(match[0]);
        if (isbn) return isbn;
      }
    }
    return null;
  } finally {
    await worker.terminate();
  }
}

photoInput.addEventListener('change', async () => {
  const file = photoInput.files?.[0];
  if (!file || busy) return;
  busy = true;
  stopCamera();
  const url = URL.createObjectURL(file);
  try {
    message('正在识别图片中的条码…');
    let isbn = null;
    try { isbn = normalize((await reader.decodeFromImageUrl(url)).getText()); } catch (_) { /* Try printed ISBN text next. */ }
    if (!isbn) {
      message('未找到条码，正在识别印刷的 ISBN 数字…');
      isbn = await ocrIsbn(file);
    }
    if (isbn) await lookup(isbn);
    else message('没有识别到有效 ISBN。请拍清楚条码或数字，也可以手动输入。', 'error');
  } catch (error) {
    message('图片识别暂不可用，请手动输入 ISBN。', 'error');
  } finally {
    URL.revokeObjectURL(url);
    photoInput.value = '';
    busy = false;
  }
});

document.getElementById('lookup-button').addEventListener('click', () => lookup(isbnField.value));
window.addEventListener('pagehide', stopCamera);
if (isbnField.value) lookup(isbnField.value);
