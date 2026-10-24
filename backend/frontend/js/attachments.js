/* ==========================================================================
   WeCi · 附件处理（图片 + 文件）

   职责：把用户选择的文件转换为可发送的附件对象。
   - 图片：校验格式与大小 → 必要时缩放压缩 → base64 data URL
   - 文件：校验扩展名与大小 → 原始字节的 base64（文本 / PDF 由后端提取）

   本模块只处理“附件”本身，不关心消息结构、请求格式或 Agent：
   消息持久化见 store.js，请求封装见 api.js，DOM 见 ui.js。
   ========================================================================== */

import { uid } from './utils.js';

export const MAX_IMAGES = 4;
export const MAX_ASSET_BYTES = 5 * 1024 * 1024; // 压缩后单张上限（与后端默认一致）
export const MAX_FILE_BYTES = 20 * 1024 * 1024; // 图片原始文件上限
export const MAX_EDGE = 1568; // 压缩后的最长边
export const KEEP_ORIGINAL_BYTES = 900 * 1024; // 小于此值且尺寸合适时保留原图

// 文件附件（第一阶段）
export const MAX_FILES = 4;
export const MAX_DOC_BYTES = 5 * 1024 * 1024; // 与后端 WECI_MAX_FILE_BYTES 默认一致
export const FILE_EXTENSIONS = [
  '.txt',
  '.md',
  '.json',
  '.csv',
  '.py',
  '.js',
  '.ts',
  '.html',
  '.css',
  '.pdf',
];

const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

export class AttachmentError extends Error {}

function dataUrlBytes(dataUrl) {
  const base64 = String(dataUrl).split(',')[1] || '';
  const padding = (base64.match(/=*$/) || [''])[0].length;
  return Math.floor((base64.length * 3) / 4) - padding;
}

function readAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new AttachmentError('读取图片失败，请重试。'));
    reader.readAsDataURL(blob);
  });
}

async function loadImage(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file);
    } catch {
      /* 退回 <img> 解码 */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new AttachmentError('图片解码失败，请换一张图片。'));
      image.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function shrinkImage(file, source) {
  const width = source.width || 0;
  const height = source.height || 0;
  if (!width || !height) {
    throw new AttachmentError('无法读取图片尺寸，请换一张图片。');
  }

  const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));

  const context = canvas.getContext('2d');
  if (!context) throw new AttachmentError('当前浏览器不支持图片压缩。');
  context.drawImage(source, 0, 0, canvas.width, canvas.height);

  // PNG 保留无损与透明通道，其余格式转 JPEG 以显著减小体积
  const type = file.type === 'image/png' ? 'image/png' : 'image/jpeg';
  const blob = await new Promise((resolve) => {
    canvas.toBlob(resolve, type, 0.86);
  });
  if (!blob) throw new AttachmentError('图片压缩失败，请换一张图片。');
  return blob;
}

/**
 * 把一个 File 转换为附件对象。
 * 失败时抛出 AttachmentError（message 可直接展示给用户）。
 */
export async function createAttachment(file) {
  if (!file) throw new AttachmentError('没有可添加的图片。');
  if (!ACCEPTED_TYPES.includes(file.type)) {
    throw new AttachmentError('仅支持 PNG / JPEG / WebP / GIF 图片。');
  }
  if (file.size > MAX_FILE_BYTES) {
    throw new AttachmentError('图片文件过大（超过 20 MB），请换一张。');
  }

  const source = await loadImage(file);
  const maxEdge = Math.max(source.width || 0, source.height || 0);
  const keepOriginal =
    file.size <= KEEP_ORIGINAL_BYTES && (!maxEdge || maxEdge <= MAX_EDGE);

  const blob = keepOriginal ? file : await shrinkImage(file, source);
  if (typeof source.close === 'function') source.close();

  const dataUrl = await readAsDataUrl(blob);
  const size = dataUrlBytes(dataUrl);
  if (size > MAX_ASSET_BYTES) {
    throw new AttachmentError('图片压缩后仍然过大，请换一张分辨率更低的图片。');
  }

  return {
    id: uid(),
    name: file.name || 'image',
    mime: blob.type || file.type,
    dataUrl,
    size,
  };
}

/** 把附件对象转换为后端需要的请求字段。 */
export function toApiAttachment(attachment) {
  return {
    data: attachment.dataUrl,
    mime_type: attachment.mime,
    name: attachment.name,
  };
}

/* --------------------------------------------------------------------------
   文件附件
   -------------------------------------------------------------------------- */

export function fileExtension(name) {
  const match = /(\.[A-Za-z0-9]+)$/.exec(String(name || '').trim());
  return match ? match[1].toLowerCase() : '';
}

/**
 * 把一个 File 转换为文件附件对象。
 * 失败时抛出 AttachmentError（message 可直接展示给用户）。
 */
export async function createFileAttachment(file) {
  if (!file) throw new AttachmentError('没有可添加的文件。');

  const name = file.name || 'file';
  const ext = fileExtension(name);
  if (!FILE_EXTENSIONS.includes(ext)) {
    throw new AttachmentError(
      `不支持的文件类型 ${ext || '（无扩展名）'}，仅支持 ${FILE_EXTENSIONS.join(' / ')}。`,
    );
  }
  if (!file.size) throw new AttachmentError('文件内容为空。');
  if (file.size > MAX_DOC_BYTES) {
    throw new AttachmentError('文件过大（超过 5 MB），请换一个小一些的文件。');
  }

  const dataUrl = await readAsDataUrl(file);
  const data = String(dataUrl).split(',')[1] || '';
  if (!data) throw new AttachmentError('读取文件失败，请重试。');

  return {
    id: uid(),
    name,
    ext,
    mime: file.type || 'application/octet-stream',
    size: file.size,
    data,
  };
}

/** 把文件附件转换为后端需要的请求字段。 */
export function toApiFile(attachment) {
  return {
    data: attachment.data,
    name: attachment.name,
    mime_type: attachment.mime,
  };
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
