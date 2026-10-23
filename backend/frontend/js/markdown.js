/* ==========================================================================
   WeCi · Markdown 渲染管线

   流程：提取 LaTeX 数学公式 → marked 解析 → KaTeX 回填 → DOMPurify 净化。
   依赖 window.marked / window.DOMPurify / window.hljs / window.katex
   （全部由 frontend/vendor 下的本地脚本提供，无 CDN 运行时依赖）。
   ========================================================================== */

import { escapeHtml, icon } from './utils.js';

/* --------------------------------------------------------------------------
   LaTeX 数学公式
   -------------------------------------------------------------------------- */

const MATH_TOKEN = '@@WECIMATH';

// 依次匹配：$$...$$、\[...\]、\(...\)、$...$
// 行内 $...$ 要求首尾字符非空白，避免把价格等普通文本误判为公式
const MATH_PATTERN = new RegExp(
  [
    '\\$\\$[\\s\\S]+?\\$\\$',
    '\\\\\\[[\\s\\S]+?\\\\\\]',
    '\\\\\\([^\\n]+?\\\\\\)',
    '(?<!\\\\)\\$[^\\s$](?:[^$\\n]*[^\\s$])?(?<!\\\\)\\$',
  ].join('|'),
  'g',
);

function renderMath(tex, display) {
  if (!window.katex) return escapeHtml(tex);
  try {
    return window.katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      strict: 'ignore',
      output: 'html',
    });
  } catch (error) {
    console.warn('[WeCi] katex render failed', error);
    return escapeHtml(tex);
  }
}

function extractMath(text) {
  const segments = [];
  const replaced = text.replace(MATH_PATTERN, (match) => {
    let tex;
    let display;
    if (match.startsWith('$$')) {
      tex = match.slice(2, -2);
      display = true;
    } else if (match.startsWith('\\[')) {
      tex = match.slice(2, -2);
      display = true;
    } else if (match.startsWith('\\(')) {
      tex = match.slice(2, -2);
      display = false;
    } else {
      tex = match.slice(1, -1);
      display = false;
    }
    const index = segments.length;
    segments.push({ tex, display });
    // 块级公式用空行隔开，保证 marked 把它渲染成独立段落
    return display ? `\n\n${MATH_TOKEN}${index}@@\n\n` : `${MATH_TOKEN}${index}@@`;
  });
  return { replaced, segments };
}

function restoreMath(html, segments) {
  segments.forEach((segment, index) => {
    const token = `${MATH_TOKEN}${index}@@`;
    const rendered = renderMath(segment.tex, segment.display);
    if (segment.display) {
      const block = `<div class="math-display">${rendered}</div>`;
      // 优先替换整个段落包裹，兜底替换裸 token
      html = html.split(`<p>${token}</p>`).join(block).split(token).join(block);
    } else {
      html = html.split(token).join(`<span class="math-inline">${rendered}</span>`);
    }
  });
  return html;
}

/* --------------------------------------------------------------------------
   Markdown → 安全 HTML
   -------------------------------------------------------------------------- */

export function markdownToHtml(text) {
  const plainFallback = `<p>${escapeHtml(text).replace(/\n/g, '<br>')}</p>`;
  if (!window.marked || !window.DOMPurify) return plainFallback;
  try {
    const { replaced, segments } = extractMath(text);
    let html = window.marked.parse(replaced, { gfm: true, breaks: true });
    html = restoreMath(html, segments);
    return window.DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true, mathMl: true },
      ADD_ATTR: ['target', 'rel', 'encoding'],
    });
  } catch (error) {
    console.warn('[WeCi] markdown render failed', error);
    return plainFallback;
  }
}

export function createProse(text) {
  const prose = document.createElement('div');
  prose.className = 'prose';
  prose.innerHTML = markdownToHtml(text);
  return prose;
}

/* --------------------------------------------------------------------------
   渲染后增强：外链新窗口、代码高亮、语言标签、复制按钮
   （流式输出期间不调用，结束或完整渲染时执行一次）
   -------------------------------------------------------------------------- */

export function enhanceMarkdown(container) {
  container.querySelectorAll('a[href]').forEach((link) => {
    const href = link.getAttribute('href') || '';
    if (/^https?:\/\//i.test(href)) {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
  });

  container.querySelectorAll('pre').forEach((pre) => {
    if (pre.dataset.enhanced === 'true') return;
    pre.dataset.enhanced = 'true';

    const code = pre.querySelector('code');
    if (!code) return;

    if (window.hljs) {
      try {
        if (!code.classList.contains('hljs') && !code.dataset.highlighted) {
          window.hljs.highlightElement(code);
        }
      } catch (error) {
        console.warn('[WeCi] highlight failed', error);
      }
    }

    const languageMatch = code.className.match(/language-([\w+-]+)/);
    const language = languageMatch ? languageMatch[1].toLowerCase() : '';
    if (language && language !== 'text' && language !== 'plaintext') {
      const label = document.createElement('span');
      label.className = 'code-lang';
      label.textContent = language;
      pre.appendChild(label);
      pre.classList.add('has-code-lang');
    }

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'code-copy';
    copyBtn.dataset.codeCopy = 'true';
    copyBtn.setAttribute('aria-label', '复制代码');
    copyBtn.appendChild(icon('copy'));
    const copyLabel = document.createElement('span');
    copyLabel.textContent = '复制';
    copyBtn.appendChild(copyLabel);
    pre.appendChild(copyBtn);
  });
}

/* --------------------------------------------------------------------------
   流式状态元素
   -------------------------------------------------------------------------- */

export function appendStreamCursor(container) {
  const cursor = document.createElement('span');
  cursor.className = 'stream-cursor';
  cursor.setAttribute('aria-hidden', 'true');
  const lastBlock = container.querySelector(
    'p:last-child, li:last-child, h1:last-child, h2:last-child, h3:last-child, h4:last-child, h5:last-child, blockquote:last-child',
  );
  if (lastBlock) {
    lastBlock.appendChild(cursor);
  } else {
    container.appendChild(cursor);
  }
}

export function typingEl() {
  const wrap = document.createElement('span');
  wrap.className = 'typing';
  wrap.setAttribute('aria-label', 'WeCi 正在回复');
  wrap.innerHTML = '<span></span><span></span><span></span>';
  return wrap;
}
