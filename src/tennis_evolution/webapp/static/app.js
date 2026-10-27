/*
 * 网球进化 · Web 体验版前端
 * vanilla ES2020，无框架、无构建、无外部请求。
 *
 * 接口契约见 docs/webapp.md §2；本文件按契约字段渲染，不做乐观假设。
 */
(function () {
  'use strict';

  /* ============================ 常量 ============================ */

  const SAMPLE_HIGHLIGHTS = [
    '0:00 - 0:06  Quick exchange (3 hits)',
    '0:09 - 0:16  Rally (5 hits)',
    '1:04 - 1:20  Rally (11 hits)',
    '2:15 - 2:52  Long rally (24 hits)',
    '6:06 - 6:30  Long rally (15 hits)',
    '14:50 - 15:26  Long rally (24 hits)',
    '21:04 - 21:09  Rally (14 hits)',
    '27:14 - 28:08  Long rally (40 hits)',
    '34:28 - 35:02  Long rally (27 hits)',
    '38:49 - 39:38  Long rally (36 hits)',
  ].join('\n');

  const TERMINAL = new Set(['done', 'failed', 'cancelled']);

  const STATUS_MAP = {
    queued: { label: '排队中', cls: '' },
    running: { label: '运行中', cls: 'pill-lime' },
    done: { label: '已完成', cls: 'pill-ok' },
    failed: { label: '失败', cls: 'pill-err' },
    cancelled: { label: '已取消', cls: '' },
  };

  const STAGES = [
    { key: 'probe', label: '探测片源' },
    { key: 'keyframes', label: '扫描关键帧' },
    { key: 'cut', label: '无损切片' },
    { key: 'labels', label: '烧录角标', opt: 'labels' },
    { key: 'render', label: '编码合集' },
    { key: 'render_plain', label: '无角标版', opt: 'plain_reel' },
    { key: 'preview', label: '浏览器预览', opt: 'browser_preview' },
    { key: 'verify', label: '自检' },
  ];

  const STAGE_LABELS = {
    probe: '探测片源',
    keyframes: '扫描关键帧',
    cut: '无损切片',
    labels: '烧录角标',
    render: '编码合集',
    render_plain: '无角标版',
    preview: '浏览器预览',
    verify: '自检',
  };

  const KIND_LABEL = { clip: '片段', reel: '合集', label: '角标', manifest: '清单', preview: '预览' };

  /* 本地生成的播放器封面（无外部资源）：球场线 + 播放按钮，视频元数据就绪前占位 */
  const POSTER_SVG = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920">',
    '<rect width="1080" height="1920" fill="#0b1411"/>',
    '<rect x="90" y="330" width="900" height="1260" fill="none" stroke="#1b3128" stroke-width="3"/>',
    '<path d="M540 330V1590M90 960H990M90 640H990M90 1280H990" stroke="#1b3128" stroke-width="2"/>',
    '<circle cx="540" cy="960" r="86" fill="rgba(201,242,75,0.10)" stroke="#c9f24b" stroke-width="3"/>',
    '<path d="M516 918L590 960L516 1002Z" fill="#c9f24b"/>',
    '<text x="540" y="1120" text-anchor="middle" fill="#93a19a" font-family="PingFang SC, Hiragino Sans GB, sans-serif" font-size="38">点击播放</text>',
    '</svg>',
  ].join('');
  const POSTER_URI = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(POSTER_SVG)}`;

  /* ============================ 基础工具 ============================ */

  const $ = (id) => document.getElementById(id);

  function h(tag, props, ...kids) {
    const node = document.createElement(tag);
    if (props && typeof props === 'object') {
      for (const key of Object.keys(props)) {
        const value = props[key];
        if (value == null || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else if (key.startsWith('on') && typeof value === 'function') {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (key in node) node[key] = value;
        else node.setAttribute(key, value === true ? '' : String(value));
      }
    }
    for (const kid of kids) {
      if (kid == null || kid === false) continue;
      if (Array.isArray(kid)) kid.forEach((k) => k != null && node.append(k));
      else node.append(kid);
    }
    return node;
  }

  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function pad2(n) {
    const s = String(n);
    return s.length >= 2 ? s : '0' + s;
  }

  function fmtHms(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const hh = Math.floor(total / 3600);
    const mm = Math.floor((total % 3600) / 60);
    const ss = total % 60;
    if (hh > 0) return `${hh}:${pad2(mm)}:${pad2(ss)}`;
    return `${mm}:${pad2(ss)}`;
  }

  function fmtElapsed(seconds) {
    return fmtHms(seconds);
  }

  function fmtSize(bytes) {
    const n = Number(bytes);
    if (!n) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = n;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    const text = value >= 100 || unit === 0 ? String(Math.round(value)) : value.toFixed(1);
    return `${text} ${units[unit]}`;
  }

  function basename(p) {
    if (!p) return '';
    const s = String(p).replace(/\\/g, '/');
    const parts = s.split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : s;
  }

  function fmtDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    try {
      const parts = new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).formatToParts(d);
      const get = (type) => (parts.find((p) => p.type === type) || {}).value || '';
      return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
    } catch (_) {
      return String(iso);
    }
  }

  function fmtLogTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    try {
      const parts = new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).formatToParts(d);
      const get = (type) => (parts.find((p) => p.type === type) || {}).value || '';
      return `${get('hour')}:${get('minute')}:${get('second')}`;
    } catch (_) {
      return '';
    }
  }

  function sortLabel(sort) {
    return { hits: '按拍数', duration: '按时长', start: '按片源顺序' }[sort] || sort || '—';
  }

  function qualityLabel(quality) {
    if (quality === 'high') return 'high';
    if (quality === 'preview') return 'preview';
    return quality || '—';
  }

  function truncate(text, max) {
    const s = String(text || '');
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
  }

  function assetUrl(jobId, path) {
    if (!path) return '';
    const s = String(path);
    if (/^(https?:)?\/\//.test(s)) return s;
    if (s.startsWith('/files/') || s.startsWith('/demo-files/') || s.startsWith('/static/')) return s;
    const clean = s.replace(/\\/g, '/');
    const sub = clean.match(/\/(clips|labels)\/([^/]+)$/);
    const rel = sub ? `${sub[1]}/${sub[2]}` : basename(clean);
    if (!jobId) return clean.startsWith('/') ? clean : '/' + clean;
    return `/files/${encodeURIComponent(jobId)}/${rel
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`;
  }

  function assetOf(value, ctx) {
    ctx = ctx || {};
    if (!value) return null;
    if (typeof value === 'string') {
      return { url: assetUrl(ctx.jobId, value), name: basename(value), poster: ctx.poster || '', previewUrl: '' };
    }
    const url = value.url || (value.path ? assetUrl(ctx.jobId, value.path) : '');
    if (!url) return null;
    const name = value.name || basename(value.path || url);
    const poster = value.poster || ctx.poster || '';
    const previewUrl = value.preview_url ? assetUrl(ctx.jobId, value.preview_url) : '';
    return Object.assign({}, value, { url, name, poster, previewUrl });
  }

  function statNode(value, label, bad) {
    return h(
      'div',
      { class: 'stat' },
      h('div', { class: 'stat-num' + (bad ? ' bad' : '') }, String(value == null ? '—' : value)),
      h('div', { class: 'stat-label' }, label)
    );
  }

  function verificationStats(verification) {
    const entries = Object.entries(verification || {});
    let checks = 0;
    let failed = 0;
    entries.forEach(([, list]) => {
      if (!Array.isArray(list)) return;
      checks += list.length;
      failed += list.filter((c) => !c || !c.ok).length;
    });
    return { files: entries.length, checks, failed };
  }

  class ApiError extends Error {
    constructor(message, code, status) {
      super(message);
      this.name = 'ApiError';
      this.code = code || 'error';
      this.status = status || 0;
    }
  }

  /* ============================ 状态 ============================ */

  const state = {
    health: null,
    demo: null,
    jobs: [],
    jobsLoaded: false,
    job: null,
    probe: null,
    lastProbePath: '',
    hl: null,
    hlTimer: null,
    hlSeq: 0,
    demoPlayer: null,
    demoSelection: { kind: 'reel' },
    demoPreviewTimer: null,
    tab: 'demo',
    renderedJobId: null,
    logRendered: 0,
    pollTimer: null,
    elapsedTimer: null,
    pollId: null,
    startingJob: false,
  };

  /* ============================ 请求层 ============================ */

  async function request(path, options) {
    const opts = options || {};
    const method = (opts.method || 'GET').toUpperCase();
    const body = opts.body == null ? null : opts.body;

    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = setTimeout(() => controller && controller.abort(), opts.timeout || 120000);
    try {
      const response = await fetch(path, {
        method,
        headers: body == null ? {} : { 'Content-Type': 'application/json' },
        body: body == null ? undefined : JSON.stringify(body),
        signal: controller ? controller.signal : undefined,
        cache: 'no-store',
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (_) {
        data = null;
      }
      if (!response.ok || (data && data.ok === false)) {
        const error = (data && data.error) || {};
        throw new ApiError(
          error.message || `请求失败（HTTP ${response.status}）`,
          error.code || 'http_error',
          response.status
        );
      }
      if (data == null) throw new ApiError('响应不是合法 JSON', 'bad_response', response.status);
      return data;
    } catch (err) {
      if (err && err.name === 'AbortError') throw new ApiError('请求超时，请重试', 'timeout', 0);
      if (err instanceof ApiError) throw err;
      throw new ApiError(`网络请求失败：${err && err.message ? err.message : '未知错误'}`, 'network_error', 0);
    } finally {
      clearTimeout(timer);
    }
  }

  const api = {
    health: () => request('/api/health'),
    demo: () => request('/api/demo'),
    probe: (path) => request('/api/probe', { method: 'POST', body: { path } }),
    highlights: (body) => request('/api/highlights', { method: 'POST', body }),
    createJob: (options) => request('/api/jobs', { method: 'POST', body: options }),
    listJobs: () => request('/api/jobs'),
    getJob: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
    cancelJob: (id) => request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    deleteJob: (id) => request(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  };

  function uploadFile(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', `/api/uploads/${encodeURIComponent(file.name)}`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && typeof onProgress === 'function') {
          onProgress(event.loaded / event.total, event.loaded, event.total);
        }
      };
      xhr.onload = () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (_) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.ok !== false && data.path) {
          resolve(data);
          return;
        }
        const error = (data && data.error) || {};
        reject(new ApiError(error.message || `上传失败（HTTP ${xhr.status}）`, error.code || 'upload_failed', xhr.status));
      };
      xhr.onerror = () => reject(new ApiError('上传失败：网络连接中断', 'network_error', 0));
      xhr.onabort = () => reject(new ApiError('上传已取消', 'aborted', 0));
      xhr.send(file);
    });
  }

  /* ============================ Toast ============================ */

  function toast(message, kind) {
    const box = $('toasts');
    if (!box) return;
    while (box.children.length >= 4) box.removeChild(box.firstChild);
    const mark = kind === 'error' ? '!' : kind === 'ok' ? '✓' : 'i';
    const node = h(
      'div',
      { class: 'toast' + (kind ? ` is-${kind}` : '') },
      h('span', { class: 'toast-mark' }, mark),
      h('span', { class: 'toast-text' }, String(message))
    );
    box.append(node);
    setTimeout(() => {
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 200);
    }, 4200);
  }

  /* ============================ 播放器 ============================ */

  function createPlayer(video, cover, options) {
    options = options || {};
    const coverText = cover.querySelector('.player-cover-text') || cover;
    let current = null;
    let triedFallback = false;

    function showUnplayable() {
      const message = options.unplayableText || '浏览器不支持该编码，请下载后用本地播放器观看';
      if (!video.poster) {
        cover.hidden = false;
        cover.classList.remove('is-hidden');
        coverText.textContent = message;
      }
      if (typeof options.onUnplayable === 'function') options.onUnplayable(message);
    }

    function tryFallback() {
      if (!current) return false;
      const fallback = current.previewUrl;
      if (fallback && fallback !== current.url && !triedFallback) {
        triedFallback = true;
        cover.hidden = false;
        cover.classList.remove('is-hidden');
        coverText.textContent = '正在切换浏览器预览…';
        video.src = fallback;
        try {
          video.load();
        } catch (_) {
          /* noop */
        }
        return true;
      }
      showUnplayable();
      return false;
    }

    video.addEventListener('loadedmetadata', () => {
      if (video.videoWidth === 0) {
        tryFallback();
        return;
      }
      cover.classList.add('is-hidden');
    });
    video.addEventListener('error', () => {
      if (current) tryFallback();
    });

    return function play(asset) {
      if (!asset || !(asset.previewUrl || asset.url)) return;
      current = asset;
      triedFallback = false;
      cover.hidden = false;
      coverText.textContent = '加载预览…';
      video.poster = asset.poster || POSTER_URI;
      cover.classList.add('is-hidden');
      video.src = asset.previewUrl || asset.url;
      try {
        video.load();
      } catch (_) {
        /* noop */
      }
    };
  }

  function setDemoCaption(badge, title, meta) {
    $('demo-now-badge').textContent = badge;
    $('demo-now-title').textContent = title;
    $('demo-now-meta').textContent = meta;
  }

  function setActiveRow(list, row) {
    if (!list) return;
    for (const child of list.children) child.classList.toggle('is-active', child === row);
  }

  /* ============================ 体验成品渲染 ============================ */

  function renderDemoSettings(demo) {
    const settings = demo.settings || {};
    const source = demo.source || {};
    const items = [
      ['保留条数', settings.top === 0 ? '全部' : `${settings.top == null ? '—' : settings.top} 条`],
      ['排序', sortLabel(settings.sort)],
      [
        '片头 / 片尾',
        `${(settings.lead == null ? 1 : settings.lead).toFixed(1)}s / ${(settings.tail == null ? 1.5 : settings.tail).toFixed(1)}s`,
      ],
      ['地点', settings.place || '—'],
      ['时区', settings.timezone || '—'],
      ['源片', source.name ? `${source.name} · ${source.duration_hms || fmtHms(source.duration)}` : '—'],
    ];
    const dl = $('demo-settings');
    clear(dl);
    items.forEach(([label, value]) => {
      dl.append(
        h('div', { class: 'param-kv' }, h('dt', {}, label), h('dd', { title: String(value) }, String(value)))
      );
    });
    $('demo-settings-pill').textContent = 'demo_run';
  }

  function renderDemoClips(demo) {
    const list = $('demo-clips');
    clear(list);
    const clips = Array.isArray(demo.clips) ? demo.clips : [];
    $('demo-clips-note').textContent = `${clips.length} 条`;
    clips.forEach((clip, index) => {
      const row = buildClipRow(clip, {
        index,
        poster: clip.poster || demo.poster,
        onPick(asset, info, li) {
          state.demoSelection = { kind: 'clip', index: clip.index == null ? index + 1 : clip.index };
          playDemoAsset(asset, info, li, demo);
        },
      });
      list.append(row);
    });
  }

  function buildClipRow(clip, opts) {
    opts = opts || {};
    const ctx = { jobId: opts.jobId, poster: opts.poster || '' };
    const asset = assetOf(clip, ctx);
    const index = (clip && clip.index) || (opts.index != null ? opts.index + 1 : 1);
    const hits = clip && clip.hits;
    const from = (clip && clip.start_hms) || '—';
    const to = (clip && clip.end_hms) || '—';
    const duration = (clip && (clip.duration_hms || fmtHms(clip.duration))) || '—';
    const size = clip && clip.size ? fmtSize(clip.size) : '';

    const row = h('li', { class: 'clip-row' });
    const pick = h(
      'button',
      { class: 'clip-pick', type: 'button' },
      h('span', { class: 'clip-hit' }, hits == null ? '—' : String(hits), h('small', {}, '拍')),
      h(
        'span',
        { class: 'clip-main' },
        h('span', { class: 'clip-title' }, `${pad2(index)} · ${from}–${to}`),
        h('span', { class: 'clip-meta' }, `${duration}${size ? ' · ' + size : ''}`)
      )
    );
    pick.addEventListener('click', () => {
      if (!asset || typeof opts.onPick !== 'function') return;
      opts.onPick(
        asset,
        {
          badge: `${hits == null ? '—' : hits} 拍`,
          title: `${pad2(index)} 号回合 · ${from}–${to}`,
          meta: `片段 · ${duration}${size ? ' · ' + size : ''}`,
        },
        row
      );
    });
    row.append(pick);
    if (asset) {
      row.append(
        h('a', { class: 'clip-dl', href: asset.url, download: asset.name || '', title: '下载该片段', 'aria-label': '下载片段' }, '↓')
      );
    }
    return row;
  }

  /* ---------- 体验成品播放器：preview_url 优先 / preview_pending 轮询 ---------- */

  function setDemoNote(text) {
    const note = $('demo-player-note');
    if (!note) return;
    note.textContent = text || '';
    note.hidden = !text;
  }

  function hasBrowserPreview(asset) {
    return !!(asset && (asset.previewUrl || asset.isPreview));
  }

  function updateDemoNote(asset, demo) {
    if (hasBrowserPreview(asset)) {
      setDemoNote('');
      return;
    }
    setDemoNote(
      demo && demo.preview_pending ? '浏览器预览生成中，当前播放 HDR 原片' : '浏览器不支持 HEVC，可下载后用本地播放器观看'
    );
  }

  function playDemoAsset(asset, info, row, demo) {
    state.demoPlayer(asset);
    setDemoCaption(info.badge, info.title, info.meta);
    setActiveRow($('demo-clips'), row || null);
    updateDemoNote(asset, demo);
  }

  function stopDemoPreviewPoll() {
    if (state.demoPreviewTimer) {
      clearTimeout(state.demoPreviewTimer);
      state.demoPreviewTimer = null;
    }
  }

  function startDemoPreviewPoll() {
    stopDemoPreviewPoll();
    state.demoPreviewTimer = setTimeout(async () => {
      state.demoPreviewTimer = null;
      if (!state.demo || !state.demo.preview_pending) return;
      try {
        const demo = await api.demo();
        if (demo && demo.available !== false && !$('demo-content').hidden) {
          renderDemo(demo, { preserve: true });
          if (!demo.preview_pending) return;
        }
      } catch (_) {
        /* 预览还在生成，静默重试 */
      }
      if (state.demo && state.demo.preview_pending) startDemoPreviewPoll();
    }, 2000);
  }

  function renderDemoPlayback(demo) {
    const clips = Array.isArray(demo.clips) ? demo.clips : [];
    const list = $('demo-clips');
    const reels = demo.reels || {};
    const previewReel = assetOf(reels.preview, { poster: demo.poster });
    if (previewReel) previewReel.isPreview = true;
    const labeled = assetOf(reels.labeled, { poster: demo.poster });

    if (state.demoSelection && state.demoSelection.kind === 'clip') {
      const index = clips.findIndex((clip, i) => (clip.index == null ? i + 1 : clip.index) === state.demoSelection.index);
      if (index >= 0) {
        const clip = clips[index];
        const asset = assetOf(clip, { poster: clip.poster || demo.poster });
        playDemoAsset(
          asset,
          {
            badge: `${clip.hits == null ? '—' : clip.hits} 拍`,
            title: `${pad2(clip.index == null ? index + 1 : clip.index)} 号回合 · ${clip.start_hms || '—'}–${clip.end_hms || '—'}`,
            meta: `片段 · ${clip.duration_hms || fmtHms(clip.duration)}${clip.size ? ' · ' + fmtSize(clip.size) : ''}`,
          },
          list.children[index] || null,
          demo
        );
        return;
      }
    }

    const asset = previewReel || labeled;
    if (!asset) {
      $('demo-player-cover').hidden = false;
      $('demo-player-cover').classList.remove('is-hidden');
      $('demo-player-cover').querySelector('.player-cover-text').textContent = '没有可播放的合集文件';
      setDemoNote('没有可播放的合集文件，请下载查看');
      return;
    }
    const count = demo.settings && demo.settings.top === 0 ? '全部' : (demo.settings && demo.settings.top) || 5;
    playDemoAsset(
      asset,
      {
        badge: `${count} 回合`,
        title: demo.title || asset.name,
        meta: `${asset.isPreview ? '浏览器预览' : '带角标合集'} · ${fmtHms(asset.duration)}${asset.size ? ' · ' + fmtSize(asset.size) : ''}`,
      },
      null,
      demo
    );
    state.demoSelection = { kind: 'reel' };
  }

  function renderDemo(demo, opts) {
    opts = opts || {};
    state.demo = demo;
    renderDemoSettings(demo);
    renderDemoClips(demo);
    renderDemoVerdict(demo);
    renderDemoLabels(demo);
    renderDemoDownloads(demo);
    renderVerification($('demo-verify'), demo.verification);
    renderDemoPlayback(demo);
    if (demo.preview_pending) startDemoPreviewPoll();
    else stopDemoPreviewPoll();
  }

  function renderDemoVerdict(demo) {
    const verdict = demo.verdict || {};
    const box = $('demo-verdict');
    clear(box);
    const ok = verdict.ok !== false;
    box.append(
      h(
        'div',
        { class: 'verdict-status' },
        h('span', { class: `pill ${ok ? 'pill-lime' : 'pill-err'}` }, ok ? 'PASS' : 'FAIL'),
        h(
          'div',
          {},
          h('h2', {}, ok ? '自检通过 · 可以交付' : '自检未通过'),
          h('p', {}, '解码 / 起点 / 时长 / 亮帧 / 音量，逐文件断言')
        )
      ),
      statNode(verdict.files, '文件'),
      statNode(verdict.checks, '校验项'),
      statNode(verdict.failed, '失败', !ok)
    );
  }

  function renderDemoLabels(demo) {
    const box = $('demo-labels');
    clear(box);
    const labels = Array.isArray(demo.labels) ? demo.labels : [];
    if (!labels.length) {
      box.append(h('p', { class: 'hint' }, '暂无角标图层'));
      return;
    }
    labels.forEach((label) => {
      box.append(
        h(
          'a',
          { class: 'label-thumb', href: label.url, target: '_blank', rel: 'noopener', title: '查看角标 PNG' },
          h('img', { src: label.url, alt: `角标 ${label.index}`, loading: 'lazy' }),
          h('span', {}, `label_${pad2(label.index)}`)
        )
      );
    });
  }

  function downloadRow(item) {
    return h(
      'a',
      { class: 'dl', href: item.url, download: item.name || '', title: `下载 ${item.name || ''}` },
      h('span', { class: 'dl-kind' }, item.kind || '文件'),
      h('span', { class: 'dl-name' }, item.name || '下载'),
      item.size ? h('span', { class: 'dl-size' }, fmtSize(item.size)) : null,
      h('span', { class: 'dl-arrow' }, '↓')
    );
  }

  function renderDemoDownloads(demo) {
    const box = $('demo-downloads');
    clear(box);
    const items = [];
    const reels = demo.reels || {};
    const labeled = assetOf(reels.labeled);
    const plain = assetOf(reels.plain);
    if (labeled) items.push({ kind: '合集', name: labeled.name, url: labeled.url, size: labeled.size });
    if (plain) items.push({ kind: '无角标', name: plain.name, url: plain.url, size: plain.size });
    if (demo.manifest_url) items.push({ kind: '清单', name: 'manifest.json', url: demo.manifest_url });
    if (!items.length) {
      box.append(h('p', { class: 'hint' }, '暂无可下载文件'));
      return;
    }
    $('demo-downloads-note').textContent = `${items.length} 个文件`;
    items.forEach((item) => box.append(downloadRow(item)));
  }

  function renderVerification(container, verification) {
    clear(container);
    const entries = Object.entries(verification || {});
    if (!entries.length) {
      container.append(h('p', { class: 'hint' }, '暂无自检数据'));
      return;
    }
    entries.forEach(([file, list], index) => {
      const checks = Array.isArray(list) ? list : [];
      const failed = checks.filter((c) => !c || !c.ok).length;
      const details = h('details', { class: 'verify-file' });
      if (index === 0) details.open = true;
      details.append(
        h(
          'summary',
          {},
          h('span', { class: 'verify-count' + (failed ? ' bad' : '') }, `${failed ? '✗' : '✓'} ${checks.length - failed}/${checks.length}`),
          h('span', { class: 'verify-file-name', title: file }, file)
        )
      );
      const rows = h('div', { class: 'verify-rows' });
      checks.forEach((check) => {
        const ok = check && check.ok;
        rows.append(
          h(
            'div',
            { class: 'verify-row' },
            h('span', { class: 'verify-mark' + (ok ? '' : ' bad') }, ok ? '✓' : '✗'),
            h('span', { class: 'verify-name' }, (check && check.name) || ''),
            h('span', { class: 'verify-detail' }, (check && check.detail) || '')
          )
        );
      });
      details.append(rows);
      container.append(details);
    });
  }

  function showDemoState(which) {
    $('demo-loading').hidden = which !== 'loading';
    $('demo-error').hidden = which !== 'error';
    $('demo-content').hidden = which !== 'content';
  }

  async function loadDemo() {
    showDemoState('loading');
    try {
      const demo = await api.demo();
      state.demo = demo;
      if (!demo.available) {
        updateDemoPill(false);
        showDemoState('error');
        const box = $('demo-error');
        clear(box);
        box.append(
          h('h2', {}, '未找到 demo 素材'),
          h('p', {}, '先跑一次命令行生成，把产物放到 demo_run/，再刷新页面。'),
          h('p', {}, h('code', {}, 'tennis-evolution all RAW.MOV --highlights highlights.txt --outdir demo_run'))
        );
        return;
      }
      updateDemoPill(true);
      renderDemo(demo);
      showDemoState('content');
    } catch (err) {
      stopDemoPreviewPoll();
      showDemoState('error');
      const box = $('demo-error');
      clear(box);
      box.append(
        h('h2', {}, '体验成品加载失败'),
        h('p', {}, err && err.message ? err.message : '未知错误'),
        h(
          'button',
          { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => loadDemo() },
          '重试'
        )
      );
      toast(`体验成品加载失败：${err.message}`, 'error');
    }
  }

  /* ============================ 生成页渲染 ============================ */

  function setProbePill(text, kind) {
    const pill = $('probe-pill');
    pill.className = 'pill ' + (kind === 'ok' ? 'pill-lime' : kind === 'warn' ? 'pill-clay' : kind === 'error' ? 'pill-err' : '');
    pill.textContent = text;
  }

  function renderProbe(data) {
    const source = data.source || {};
    const info = $('probe-info');
    info.hidden = false;
    clear(info);
    const primary = `${source.duration_hms || fmtHms(source.duration)} · ${source.width || '—'}×${source.height || '—'}${
      source.hdr ? ' · HDR' : ''
    }${source.recorded_at_local ? ` · 录制于 ${source.recorded_at_local}` : ''}`;
    info.append(h('p', { class: 'probe-primary' }, primary));
    const bits = [
      source.size_human,
      source.video_codec,
      source.fps ? `${source.fps} fps` : '',
      source.bit_depth ? `${source.bit_depth}-bit` : '',
      source.has_audio ? 'AAC 立体声' : '无音轨',
      source.name,
    ].filter(Boolean);
    info.append(h('p', { class: 'probe-secondary' }, bits.map((text) => h('span', {}, text))));
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    if (warnings.length) {
      const ul = h('ul', { class: 'probe-warnings' });
      warnings.forEach((warning) => ul.append(h('li', {}, warning)));
      info.append(ul);
      setProbePill('可剪辑 · 有提醒', 'warn');
    } else {
      setProbePill('可剪辑', 'ok');
    }
  }

  async function probeSource(force) {
    const path = $('source-path').value.trim();
    if (!path) {
      state.lastProbePath = '';
      state.probe = null;
      setProbePill('未探测');
      $('probe-info').hidden = true;
      return;
    }
    if (!force && path === state.lastProbePath && state.probe) return;
    setProbePill('探测中…');
    try {
      const data = await api.probe(path);
      state.probe = data.source || null;
      state.lastProbePath = path;
      renderProbe(data);
    } catch (err) {
      state.probe = null;
      state.lastProbePath = '';
      setProbePill('探测失败', 'error');
      const info = $('probe-info');
      info.hidden = false;
      clear(info);
      info.append(h('p', { class: 'probe-primary' }, '无法读取该文件'), h('p', { class: 'probe-secondary' }, err.message));
      toast(`探测失败：${err.message}`, 'error');
    }
  }

  function setHlHint(text, kind) {
    const hint = $('hl-hint');
    hint.className = 'hint' + (kind === 'error' ? ' warn' : '');
    hint.textContent = text;
  }

  function renderHlList(container, items, selected) {
    clear(container);
    if (!items.length) {
      container.append(h('li', { class: 'hl-empty' }, selected ? '还没有选中的回合' : '没有落选的回合'));
      return;
    }
    items.forEach((seg) => {
      container.append(
        h(
          'li',
          { class: 'hl-row' + (selected ? ' is-selected' : '') },
          h('span', { class: 'hl-badge' }, seg.hits == null ? '—' : String(seg.hits)),
          h(
            'span',
            { class: 'hl-main' },
            h('span', { class: 'hl-range' }, `${seg.start_hms || '—'} – ${seg.end_hms || '—'}`),
            h('span', { class: 'hl-sub' }, `${seg.duration_hms || fmtHms(seg.duration)} · ${seg.label || '回合'}`)
          )
        )
      );
    });
  }

  function setPillCount(id, count) {
    const node = $(id);
    if (node) node.textContent = String(count);
  }

  function renderHighlights(data) {
    const selected = Array.isArray(data.selected) ? data.selected : [];
    const dropped = Array.isArray(data.dropped) ? data.dropped : [];
    const summary = data.summary || {};
    setPillCount('hl-selected-count', selected.length);
    setPillCount('hl-dropped-count', dropped.length);
    renderHlList($('hl-selected'), selected, true);
    renderHlList($('hl-dropped'), dropped, false);
    const box = $('hl-summary');
    clear(box);
    box.append(
      h('span', {}, '共 ', h('strong', {}, String(data.total == null ? selected.length + dropped.length : data.total)), ' 段'),
      h('span', {}, '已选 ', h('strong', {}, String(selected.length)), ' 条'),
      h('span', {}, '候选 ', h('strong', {}, summary.candidate_duration_hms || '—')),
      h('span', {}, '含片头片尾 ', h('strong', {}, summary.with_padding_hms || '—')),
      h('span', {}, '预计成片 ', h('strong', {}, summary.estimated_output_hms || '—'))
    );
    setHlHint('解析成功。改保留条数 / 排序会重新选片。');
  }

  function renderHighlightsEmpty(message) {
    setPillCount('hl-selected-count', 0);
    setPillCount('hl-dropped-count', 0);
    renderHlList($('hl-selected'), [], true);
    renderHlList($('hl-dropped'), [], false);
    clear($('hl-summary'));
    if (message) setHlHint(message);
  }

  async function parseHighlights() {
    const text = $('highlights').value.trim();
    const seq = ++state.hlSeq;
    if (!text) {
      state.hl = null;
      renderHighlightsEmpty('粘贴高光列表后显示解析预览；没有列表可以点「用示例列表」。');
      return;
    }
    setHlHint('解析中…');
    const top = getChecked('top', 5);
    const sort = String(getChecked('sort', 'hits'));
    try {
      const data = await api.highlights({ text, top: Number(top), sort });
      if (seq !== state.hlSeq) return;
      state.hl = data;
      renderHighlights(data);
    } catch (err) {
      if (seq !== state.hlSeq) return;
      state.hl = null;
      renderHighlightsEmpty('解析失败，请检查格式。');
      setHlHint(`解析失败：${err.message}`, 'error');
      toast(`高光解析失败：${err.message}`, 'error');
    }
  }

  function scheduleHighlights(delayMs) {
    clearTimeout(state.hlTimer);
    state.hlTimer = setTimeout(parseHighlights, delayMs == null ? 500 : delayMs);
  }

  function getChecked(name, fallback) {
    const node = document.querySelector(`input[name="${name}"]:checked`);
    return node ? node.value : fallback;
  }

  /* ============================ 运行面板 ============================ */

  function setProgress(progress, label) {
    const pct = Math.max(0, Math.min(1, Number(progress) || 0));
    $('run-progress-bar').style.width = `${(pct * 100).toFixed(1)}%`;
    $('run-progress-pct').textContent = `${Math.round(pct * 100)}%`;
    if (label) $('run-stage-label').textContent = label;
  }

  function resetElapsedTicker() {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
  }

  function syncElapsed(job) {
    resetElapsedTicker();
    const base = Number(job.elapsed || 0);
    const at = Date.now();
    const paint = () => {
      $('run-elapsed').textContent = fmtElapsed(base + (Date.now() - at) / 1000);
    };
    paint();
    if (!TERMINAL.has(job.status)) state.elapsedTimer = setInterval(paint, 1000);
  }

  function renderStages(job) {
    const list = $('run-stages');
    clear(list);
    const options = job.options || {};
    let keys;
    if (Array.isArray(job.stages) && job.stages.length) {
      keys = job.stages.map((key) => key);
    } else {
      keys = STAGES.filter((stage) => !stage.opt || !options || options[stage.opt]).map((stage) => stage.key);
    }
    keys = keys.filter((key, index) => keys.indexOf(key) === index);
    const currentIndex = keys.indexOf(job.stage);
    keys.forEach((key, index) => {
      const cls = ['stage'];
      let mark = '';
      if (job.status === 'done') {
        cls.push('is-done');
        mark = '✓';
      } else if (job.status === 'failed' || job.status === 'cancelled') {
        if (index <= currentIndex) {
          cls.push('is-done');
          mark = '✓';
        }
      } else if (index < currentIndex) {
        cls.push('is-done');
        mark = '✓';
      } else if (index === currentIndex) {
        cls.push('is-active');
      }
      list.append(
        h(
          'li',
          { class: cls.join(' ') },
          h('span', { class: 'stage-dot' }, mark),
          h('span', { class: 'stage-label' }, STAGE_LABELS[key] || key)
        )
      );
    });
  }

  function appendLogs(logs) {
    const box = $('run-log');
    const list = Array.isArray(logs) ? logs : [];
    if (state.logRendered > list.length) {
      clear(box);
      state.logRendered = 0;
    }
    for (let i = state.logRendered; i < list.length; i += 1) {
      const line = list[i] || {};
      box.append(
        h(
          'div',
          { class: `log-line${line.level === 'error' ? ' error' : line.level === 'warn' ? ' warn' : ''}` },
          h('span', { class: 't' }, fmtLogTime(line.t)),
          document.createTextNode(String(line.text == null ? '' : line.text))
        )
      );
    }
    state.logRendered = list.length;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
    if (nearBottom || state.logRendered === list.length) box.scrollTop = box.scrollHeight;
    $('run-log-count').textContent = `${list.length} 行`;
  }

  function setStatusPill(pill, status) {
    const info = STATUS_MAP[status] || { label: status || '—', cls: '' };
    pill.className = 'pill ' + (info.cls || '');
    clear(pill);
    pill.append(h('span', { class: 'dot' }), document.createTextNode(info.label));
  }

  function renderFailed(job) {
    const box = $('run-failed');
    box.hidden = false;
    clear(box);
    const failed = job.status === 'failed';
    const logs = (job.logs || []).slice(-30);
    box.append(
      h('h3', {}, failed ? '生成失败' : '任务已取消'),
      h('p', {}, job.error || (failed ? '后端未返回错误信息，请查看日志。' : '任务在完成前被取消。')),
      h(
        'details',
        {},
        h('summary', {}, `查看最后 ${logs.length} 行日志`),
        h('pre', {}, logs.map((line) => `[${line.level || 'info'}] ${line.text || ''}`).join('\n') || '无日志')
      ),
      h(
        'div',
        { class: 'result-actions' },
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => resetRunPanel() }, '新建任务')
      )
    );
  }

  function renderJobResult(job) {
    const result = job.result || {};
    const box = $('run-result');
    clear(box);
    box.hidden = false;

    const reels = result.reels || {};
    const previewReel = assetOf(reels.preview, { jobId: job.id });
    if (previewReel) previewReel.isPreview = true;
    const labeled = assetOf(reels.labeled, { jobId: job.id });
    const playable = previewReel || labeled;
    const hasPreview = hasBrowserPreview(previewReel) || hasBrowserPreview(labeled);

    box.append(
      h(
        'div',
        { class: 'result-head' },
        h('h3', {}, '生成完成'),
        h('span', { class: 'mono' }, `${job.id} · 用时 ${fmtElapsed(job.elapsed || 0)}`)
      )
    );

    const video = h('video', { class: 'player', controls: true, playsinline: true, preload: 'metadata' });
    const cover = h('div', { class: 'player-cover' }, h('span', { class: 'player-cover-text' }, '加载预览…'));
    const note = h('div', { class: 'player-note', hidden: true });
    const setNote = (text) => {
      note.textContent = text || '';
      note.hidden = !text;
    };
    const play = createPlayer(video, cover, {
      onUnplayable() {
        setNote('浏览器不支持 HEVC，可下载后用本地播放器观看');
      },
    });
    const badge = h('span', { class: 'pill pill-lime' }, '预览');
    const title = h('span', { class: 'player-title' }, playable ? playable.name : '无预览文件');
    const meta = h('div', { class: 'player-caption-meta' }, 'HDR 源不做 tonemap，预览颜色略灰属正常');

    const clipList = h('ol', { class: 'clip-list' });
    const clips = Array.isArray(result.clips) && result.clips.length ? result.clips : Array.isArray(job.selection) ? job.selection : [];
    clips.forEach((clip, index) => {
      clipList.append(
        buildClipRow(clip, {
          index,
          jobId: job.id,
          poster: clip.poster || '',
          onPick(asset, info, row) {
            play(asset);
            badge.textContent = info.badge;
            title.textContent = info.title;
            meta.textContent = info.meta + (hasBrowserPreview(asset) ? '' : ' · 可下载观看');
            setNote(hasBrowserPreview(asset) ? '' : '浏览器不支持 HEVC，可下载后用本地播放器观看');
            setActiveRow(clipList, row);
          },
        })
      );
    });

    const playerCol = h(
      'div',
      { class: 'result-player' },
      h('div', { class: 'player-wrap' }, video, cover),
      h('div', { class: 'player-caption' }, h('div', { class: 'player-caption-main' }, badge, title), meta, note)
    );

    const side = h(
      'div',
      { class: 'result-side' },
      h('div', { class: 'sub-head' }, '片段', h('span', { class: 'muted' }, `${clips.length} 条`)),
      clipList
    );

    box.append(h('div', { class: 'result-grid' }, playerCol, side));

    if (playable) {
      play(playable);
      badge.textContent =
        playable.isPreview || playable.previewUrl
          ? '浏览器预览'
          : playable.browser_playable
            ? '浏览器可播'
            : 'HEVC 原片';
      title.textContent = playable.name || '合集';
      meta.textContent = `合集 · ${fmtHms(playable.duration)}${playable.size ? ' · ' + fmtSize(playable.size) : ''}`;
      setNote(
        hasPreview || playable.browser_playable
          ? ''
          : '浏览器不支持 HEVC，可下载后用本地播放器观看'
      );
      setActiveRow(clipList, null);
    } else {
      cover.hidden = false;
      cover.classList.remove('is-hidden');
      cover.querySelector('.player-cover-text').textContent = '没有可播放的预览文件，请用下方下载';
      setNote('没有可播放的预览文件，请用下方下载');
    }

    const stats = verificationStats(result.verification);
    const ok = result.ok !== false && stats.failed === 0;
    box.append(
      h(
        'div',
        { class: 'result-verdict' },
        h('span', { class: `pill ${ok ? 'pill-lime' : 'pill-err'}` }, ok ? 'PASS' : 'FAIL'),
        statNode(stats.files, '文件'),
        statNode(stats.checks, '校验项'),
        statNode(stats.failed, '失败', !ok),
        h('span', { class: 'muted' }, ok ? '自检通过 · 可以交付' : '自检发现问题，请看明细')
      )
    );

    const verifyBox = h('div', { class: 'result-verify' });
    renderVerification(verifyBox, result.verification);
    box.append(h('div', {}, h('div', { class: 'sub-head' }, '自检明细'), verifyBox));

    const downloadBox = h('div', { class: 'result-downloads' });
    renderResultDownloads(job, downloadBox, previewReel, labeled);
    box.append(h('div', {}, h('div', { class: 'sub-head' }, '下载', h('span', { class: 'muted' }, '本地文件')), downloadBox));

    box.append(
      h(
        'div',
        { class: 'result-actions' },
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => resetRunPanel() }, '新建任务'),
        result.manifest_url
          ? h(
              'a',
              { class: 'btn btn-ghost btn-sm', href: assetUrl(job.id, result.manifest_url), download: 'manifest.json' },
              '下载 manifest'
            )
          : null
      )
    );
  }

  function renderResultDownloads(job, container, preview, labeled) {
    clear(container);
    const result = job.result || {};
    const items = [];
    const outputs = Array.isArray(result.outputs) ? result.outputs : [];
    const pushItem = (item) => {
      if (!item || !item.url) return;
      if (items.some((existing) => existing.url === item.url)) return;
      items.push(item);
    };
    if (outputs.length) {
      outputs.forEach((output) => {
        if (!output) return;
        pushItem({
          kind: KIND_LABEL[output.kind] || '文件',
          name: output.name || basename(output.url),
          url: assetUrl(job.id, output.url),
          size: output.size,
        });
      });
    } else {
      if (preview) pushItem({ kind: '预览', name: preview.name, url: preview.url, size: preview.size });
      if (labeled) pushItem({ kind: '合集', name: labeled.name, url: labeled.url, size: labeled.size });
      (Array.isArray(result.clips) ? result.clips : []).forEach((clip) => {
        const asset = assetOf(clip, { jobId: job.id });
        if (asset) pushItem({ kind: '片段', name: asset.name, url: asset.url, size: clip.size });
      });
    }
    if (result.manifest_url) {
      pushItem({ kind: '清单', name: 'manifest.json', url: assetUrl(job.id, result.manifest_url) });
    }
    if (!items.length) {
      container.append(h('p', { class: 'hint' }, '暂无可下载文件'));
      return;
    }
    items.forEach((item) => container.append(downloadRow(item)));
  }

  function enterRunView() {
    $('run-empty').hidden = true;
    $('run-body').hidden = false;
    const body = $('run-body');
    ['progress-block', 'stage-list', 'log-block'].forEach((cls) => {
      const node = body.querySelector('.' + cls);
      if (node) node.hidden = false;
    });
    $('run-failed').hidden = true;
    clear($('run-failed'));
    $('run-result').hidden = true;
    clear($('run-result'));
    const log = $('run-log');
    clear(log);
    $('run-log-count').textContent = '0 行';
    state.logRendered = 0;
    state.renderedJobId = null;
    $('run-elapsed').textContent = '0:00';
    $('run-id').textContent = '—';
    $('run-cancel').hidden = true;
    $('run-progress-bar').classList.remove('is-running');
    setProgress(0, '准备中…');
    clear($('run-stages'));
    setStatusPill($('run-status-pill'), 'queued');
  }

  function renderJob(job) {
    state.job = job;
    if ($('run-empty').hidden === false) $('run-empty').hidden = true;
    const body = $('run-body');
    body.hidden = false;

    if (state.renderedJobId !== job.id) {
      state.renderedJobId = job.id;
      state.logRendered = 0;
      clear($('run-log'));
      $('run-log-count').textContent = '0 行';
    }

    const status = job.status || 'queued';
    setStatusPill($('run-status-pill'), status);
    $('run-id').textContent = job.id || '—';
    syncElapsed(job);
    setProgress(job.progress || 0, job.stage_label || (STATUS_MAP[status] || {}).label || '');
    if (status === 'queued') {
      const position = Number(job.queue_position);
      $('run-stage-label').textContent =
        position > 0 ? `排队中，前面还有 ${position} 个任务` : `${job.stage_label || '排队中'} · 等待调度`;
    }
    renderStages(job);
    appendLogs(job.logs || []);

    const running = status === 'running' || status === 'queued';
    $('run-cancel').hidden = !running;
    $('run-progress-bar').classList.toggle('is-running', running);

    const showResult = status === 'done' && !!job.result;
    ['progress-block', 'stage-list', 'log-block'].forEach((cls) => {
      const node = body.querySelector('.' + cls);
      if (node) node.hidden = showResult;
    });
    $('run-result').hidden = !showResult;
    if (showResult) {
      renderJobResult(job);
    } else {
      clear($('run-result'));
    }
    if (status === 'failed' || status === 'cancelled') renderFailed(job);
    else {
      $('run-failed').hidden = true;
      clear($('run-failed'));
    }
  }

  function resetRunPanel() {
    stopPolling();
    resetElapsedTicker();
    state.job = null;
    state.renderedJobId = null;
    state.logRendered = 0;
    $('run-body').hidden = true;
    $('run-result').hidden = true;
    clear($('run-result'));
    $('run-failed').hidden = true;
    clear($('run-failed'));
    $('run-empty').hidden = false;
    clear($('run-log'));
    $('run-log-count').textContent = '0 行';
    $('run-elapsed').textContent = '0:00';
  }

  /* ============================ 轮询 ============================ */

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    state.pollId = null;
  }

  function startPolling(jobId) {
    stopPolling();
    state.pollId = jobId;
    const tick = async () => {
      if (state.pollId !== jobId) return;
      try {
        const data = await api.getJob(jobId);
        if (state.pollId !== jobId) return;
        const job = data.job && data.job.id ? data.job : data;
        renderJob(job);
        if (TERMINAL.has(job.status)) {
          state.pollId = null;
          resetElapsedTicker();
          refreshHistory();
        } else {
          state.pollTimer = setTimeout(tick, 1000);
        }
      } catch (err) {
        if (state.pollId !== jobId) return;
        toast(`任务状态刷新失败：${err.message}`, 'error');
        state.pollTimer = setTimeout(tick, 2500);
      }
    };
    tick();
  }

  /* ============================ 表单动作 ============================ */

  function readFormOptions(highlights) {
    return {
      source: $('source-path').value.trim(),
      highlights,
      top: Number(getChecked('top', 5)),
      sort: String(getChecked('sort', 'hits')),
      lead: 1.0,
      tail: 1.5,
      place: $('place').value.trim() || null,
      timezone: $('timezone').value,
      unit: $('unit').value.trim() || '拍',
      source_label: $('source-label').value.trim() || '片源',
      labels: $('labels').checked,
      keep_clips: $('keep-clips').checked,
      plain_reel: $('plain-reel').checked,
      browser_preview: $('browser-preview').checked,
      quality: String(getChecked('quality', 'preview')),
      note: '',
    };
  }

  async function startJob() {
    if (state.startingJob) return;
    const source = $('source-path').value.trim();
    const highlights = $('highlights').value.trim();
    if (!source) {
      toast('请先填写片源路径，或拖入视频文件', 'error');
      $('source-path').focus();
      return;
    }
    if (!highlights) {
      toast('请粘贴高光列表，或点「用示例列表」', 'error');
      $('highlights').focus();
      return;
    }
    state.startingJob = true;
    const button = $('start-job');
    const label = button.textContent;
    button.disabled = true;
    button.textContent = '创建任务…';
    try {
      const data = await api.createJob(readFormOptions(highlights));
      const job = data.job || {};
      if (!job.id) throw new ApiError('后端没有返回任务 ID', 'bad_response');
      toast('任务已创建，开始处理', 'ok');
      enterRunView();
      startPolling(job.id);
      refreshHistory();
    } catch (err) {
      toast(`创建任务失败：${err.message}`, 'error');
    } finally {
      state.startingJob = false;
      button.disabled = false;
      button.textContent = label;
    }
  }

  async function cancelCurrentJob() {
    if (!state.job || !state.job.id) return;
    const button = $('run-cancel');
    button.disabled = true;
    button.textContent = '取消中…';
    try {
      await api.cancelJob(state.job.id);
      toast('已请求取消，当前步骤结束后停止', 'ok');
    } catch (err) {
      toast(`取消失败：${err.message}`, 'error');
    } finally {
      button.disabled = false;
      button.textContent = '取消任务';
    }
  }

  async function uploadAndProbe(file) {
    if (!file) return;
    const isVideo = /^video\//.test(file.type || '') || /\.(mov|mp4|m4v|avi|mkv)$/i.test(file.name || '');
    if (!isVideo) {
      toast('只支持视频文件', 'error');
      return;
    }
    const limit = state.health && state.health.limits && state.health.limits.max_upload_bytes;
    if (limit && file.size > limit) {
      toast(`文件 ${fmtSize(file.size)} 超过上传上限 ${fmtSize(limit)}`, 'error');
      return;
    }
    const meter = $('upload-meter');
    const bar = $('upload-meter-bar');
    const text = $('upload-meter-text');
    meter.hidden = false;
    bar.style.width = '0%';
    text.textContent = `上传 ${file.name} · 0%`;
    try {
      const data = await uploadFile(file, (ratio, loaded, total) => {
        bar.style.width = `${(ratio * 100).toFixed(1)}%`;
        text.textContent = `上传 ${file.name} · ${Math.round(ratio * 100)}% · ${fmtSize(loaded)} / ${fmtSize(total)}`;
      });
      bar.style.width = '100%';
      text.textContent = `上传完成 · ${fmtSize(data.size)} · 正在探测…`;
      $('source-path').value = data.path;
      state.lastProbePath = '';
      await probeSource(true);
      setTimeout(() => {
        meter.hidden = true;
      }, 1600);
    } catch (err) {
      text.textContent = `上传失败：${err.message}`;
      toast(`上传失败：${err.message}`, 'error');
    }
  }

  /* ============================ 历史任务 ============================ */

  function updateDemoPill(available) {
    const pill = $('pill-demo');
    pill.hidden = false;
    pill.className = 'pill ' + (available ? '' : 'pill-clay');
    pill.textContent = available ? '样例就绪' : '样例缺失';
  }

  function renderRunningPill() {
    const pill = $('pill-running');
    const jobs = state.jobs || [];
    const running = jobs.filter((job) => job.status === 'running').length;
    const queued = jobs.filter((job) => job.status === 'queued').length;
    if (!running && !queued) {
      pill.hidden = true;
      return;
    }
    pill.hidden = false;
    clear(pill);
    pill.append(h('span', { class: 'dot' }), document.createTextNode(running ? `运行中 ${running}` : `排队 ${queued}`));
  }

  async function loadJobs() {
    const loading = $('history-loading');
    const empty = $('history-empty');
    if (!state.jobsLoaded) loading.hidden = false;
    try {
      const data = await api.listJobs();
      let jobs = [];
      if (Array.isArray(data.jobs)) jobs = data.jobs;
      else if (Array.isArray(data.items)) jobs = data.items;
      else if (Array.isArray(data)) jobs = data;
      state.jobs = jobs;
      state.jobsLoaded = true;
      loading.hidden = true;
      empty.hidden = jobs.length > 0;
      if (!jobs.length) empty.textContent = '还没有历史任务，生成一个试试。';
      renderJobs(jobs);
      renderRunningPill();
    } catch (err) {
      loading.hidden = true;
      empty.hidden = false;
      empty.textContent = `历史任务加载失败：${err.message}`;
      toast(`历史任务加载失败：${err.message}`, 'error');
    }
  }

  function refreshHistory() {
    if (state.tab !== 'generate' && state.jobsLoaded) return;
    return loadJobs();
  }

  async function removeJob(jobId, retry) {
    try {
      await api.deleteJob(jobId);
      toast('已删除任务与产物', 'ok');
      if (state.job && state.job.id === jobId) resetRunPanel();
      await loadJobs();
      return true;
    } catch (err) {
      if (err.code === 'job_running' && retry < 3) {
        toast('任务仍在收尾，1 秒后重试删除', 'info');
        await new Promise((resolve) => setTimeout(resolve, 1000));
        return removeJob(jobId, retry + 1);
      }
      toast(`删除失败：${err.message}`, 'error');
      return false;
    }
  }

  function renderJobs(jobs) {
    const list = $('history-list');
    clear(list);
    jobs.slice(0, 8).forEach((job) => {
      const info = STATUS_MAP[job.status] || { label: job.status || '未知', cls: '' };
      const active = state.job && state.job.id === job.id;
      const row = h(
        'div',
        { class: 'history-row' + (active ? ' is-active' : ''), role: 'button', tabindex: '0', title: '点击回看该任务' },
        h('span', { class: 'history-status' }, h('span', { class: `pill ${info.cls || ''}` }, h('span', { class: 'dot' }), info.label)),
        h(
          'div',
          { class: 'history-main' },
          h(
            'div',
            { class: 'history-title' },
            `${job.source_name || '未知片源'} · ${job.top === 0 ? '全部' : `${job.top == null ? '—' : job.top} 条`} · ${qualityLabel(job.quality)}`
          ),
          h(
            'div',
            { class: 'history-meta' },
            h('span', {}, job.id || ''),
            h('span', { class: 'dim' }, fmtDateTime(job.created_at)),
            job.error ? h('span', { class: 'dim', title: job.error }, truncate(job.error, 42)) : null
          )
        ),
        h('span', { class: 'history-elapsed' }, fmtElapsed(job.elapsed || 0)),
        h(
          'div',
          { class: 'history-actions' },
          h('button', { class: 'icon-btn', type: 'button' }, '回看'),
          h('button', { class: 'icon-btn', type: 'button' }, '删除')
        )
      );
      const [reviewBtn, deleteBtn] = row.querySelectorAll('.history-actions .icon-btn');
      row.addEventListener('click', () => openJob(job.id));
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openJob(job.id);
        }
      });
      reviewBtn.addEventListener('click', (event) => {
        event.stopPropagation();
        openJob(job.id);
      });
      let armed = false;
      let armTimer = null;
      const resetArm = () => {
        armed = false;
        deleteBtn.textContent = '删除';
        deleteBtn.classList.remove('is-danger');
      };
      deleteBtn.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!armed) {
          armed = true;
          deleteBtn.textContent = '确认删除';
          deleteBtn.classList.add('is-danger');
          armTimer = setTimeout(resetArm, 3200);
          return;
        }
        clearTimeout(armTimer);
        deleteBtn.disabled = true;
        deleteBtn.textContent = '删除中…';
        const removed = await removeJob(job.id, 0);
        if (!removed) {
          resetArm();
          deleteBtn.disabled = false;
        }
      });
      list.append(row);
    });
  }

  async function openJob(jobId) {
    if (!jobId) return;
    switchTab('generate', true);
    enterRunView();
    stopPolling();
    try {
      const data = await api.getJob(jobId);
      const job = data.job && data.job.id ? data.job : data;
      renderJob(job);
      if (!TERMINAL.has(job.status)) startPolling(jobId);
      else refreshHistory();
    } catch (err) {
      toast(`打开任务失败：${err.message}`, 'error');
    }
  }

  /* ============================ 健康检查 / 初始化 ============================ */

  function renderHealth(data) {
    const tools = data.tools || {};
    const ok = tools.ok !== false && !!tools.ffmpeg;
    const pill = $('pill-tools');
    pill.className = 'pill ' + (ok ? 'pill-lime' : 'pill-clay');
    clear(pill);
    pill.append(h('span', { class: 'dot' }), document.createTextNode(ok ? 'ffmpeg 就绪' : 'ffmpeg 缺失'));
    if (data.demo) updateDemoPill(!!data.demo.available);
  }

  function applyDefaults(defaults) {
    if (!defaults) return;
    let filledSource = false;
    if (!$('source-path').value && defaults.source) {
      $('source-path').value = defaults.source;
      filledSource = true;
    }
    if (!$('place').value && defaults.place) $('place').value = defaults.place;
    if (defaults.timezone) {
      const exists = Array.from($('timezone').options).some((option) => option.value === defaults.timezone);
      if (exists) $('timezone').value = defaults.timezone;
    }
    if (defaults.top != null) {
      const radio = document.querySelector(`input[name="top"][value="${defaults.top}"]`);
      if (radio) radio.checked = true;
    }
    if (defaults.quality) {
      const radio = document.querySelector(`input[name="quality"][value="${defaults.quality}"]`);
      if (radio) radio.checked = true;
    }
    if (filledSource) probeSource();
  }

  async function loadHealth() {
    try {
      const data = await api.health();
      state.health = data;
      renderHealth(data);
      applyDefaults(data.defaults || {});
    } catch (err) {
      state.health = null;
      const pill = $('pill-tools');
      pill.className = 'pill pill-err';
      clear(pill);
      pill.append(h('span', { class: 'dot' }), document.createTextNode('后端未连接'));
      $('pill-demo').hidden = true;
      toast(`无法连接后端：${err.message}`, 'error');
    }
  }

  function switchTab(name, silent) {
    const isDemo = name === 'demo';
    state.tab = isDemo ? 'demo' : 'generate';
    $('tab-demo').classList.toggle('is-active', isDemo);
    $('tab-demo').setAttribute('aria-selected', String(isDemo));
    $('tab-generate').classList.toggle('is-active', !isDemo);
    $('tab-generate').setAttribute('aria-selected', String(!isDemo));
    $('panel-demo').hidden = !isDemo;
    $('panel-generate').hidden = isDemo;
    if (!silent && !isDemo && !state.jobsLoaded) loadJobs();
    if (!silent && !isDemo) renderRunningPill();
  }

  function bindShell() {
    $('tab-demo').addEventListener('click', () => switchTab('demo'));
    $('tab-generate').addEventListener('click', () => switchTab('generate'));
    $('hero-cta').addEventListener('click', () => {
      switchTab('generate');
      const form = $('job-form');
      if (form) form.scrollIntoView({ block: 'start' });
    });
    $('refresh-history').addEventListener('click', () => loadJobs());
    $('run-cancel').addEventListener('click', cancelCurrentJob);
  }

  function bindForm() {
    const sourceInput = $('source-path');
    const fileInput = $('file-input');
    const dropzone = $('dropzone');
    sourceInput.addEventListener('blur', () => probeSource());
    sourceInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        probeSource(true);
      }
    });

    ['dragenter', 'dragover'].forEach((type) => {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add('is-over');
      });
    });
    ['dragleave', 'dragend'].forEach((type) => {
      dropzone.addEventListener(type, () => dropzone.classList.remove('is-over'));
    });
    dropzone.addEventListener('drop', (event) => {
      event.preventDefault();
      dropzone.classList.remove('is-over');
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) uploadAndProbe(file);
    });
    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        fileInput.click();
      }
    });
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      if (file) uploadAndProbe(file);
      fileInput.value = '';
    });

    $('highlights').addEventListener('input', () => scheduleHighlights());
    $('use-sample').addEventListener('click', () => {
      $('highlights').value = SAMPLE_HIGHLIGHTS;
      scheduleHighlights(0);
    });
    document.querySelectorAll('input[name="top"], input[name="sort"]').forEach((input) => {
      input.addEventListener('change', () => {
        if ($('highlights').value.trim()) scheduleHighlights();
      });
    });
    $('job-form').addEventListener('submit', (event) => {
      event.preventDefault();
      startJob();
    });
  }

  function applyDeepLink() {
    const params = new URLSearchParams(location.search);
    const jobId = params.get('job');
    if (jobId) {
      switchTab('generate', true);
      openJob(jobId);
      return;
    }
    if (params.get('tab') === 'generate') switchTab('generate', true);
  }

  async function boot() {
    state.demoPlayer = createPlayer($('demo-video'), $('demo-player-cover'), {
      onUnplayable() {
        setDemoNote('浏览器不支持 HEVC，可下载后用本地播放器观看');
      },
    });
    bindShell();
    bindForm();
    renderHighlightsEmpty('粘贴高光列表后显示解析预览；没有列表可以点「用示例列表」。');
    const tasks = [loadHealth(), loadDemo(), loadJobs()];
    await Promise.allSettled(tasks);
    applyDeepLink();
  }

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    toast(`请求异常：${reason && reason.message ? reason.message : String(reason)}`, 'error');
    event.preventDefault();
  });
  window.addEventListener('error', (event) => {
    if (event && event.message) toast(`页面脚本错误：${event.message}`, 'error');
  });

  boot();
})();
