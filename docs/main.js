/* ─────────────────────────────────────────────────────────────────
   LaunchAI · landing · terminal demo
   Prints a plausible "手动装 → 报错 → LaunchAI 接管" sequence.
   Respects prefers-reduced-motion.
   ───────────────────────────────────────────────────────────────── */

(() => {
  const term = document.getElementById('term');
  const toggle = document.getElementById('termToggle');
  if (!term) return;

  const prefersReduced =
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ── Script content ──
  // Each item:  [kind, text, opts?]
  //   kind: 'cmd' | 'out' | 'err' | 'comment' | 'divider' | 'la' | 'la-body' | 'la-mirror' | 'la-check' | 'blank'
  //   opts: { pauseAfter?: ms, typeSpeed?: ms/char }
  const script = [
    // ── PART 1 · 手动装 ──
    ['cmd', '$ pip install demucs'],
    ['out', 'Looking in indexes: https://pypi.org/simple'],
    ['out', 'Collecting demucs'],
    ['out', '  Downloading demucs-4.0.1.tar.gz (1.2 MB)'],
    ['out', 'Collecting torch>=2.0'],
    ['out', '  Downloading torch-2.5.1-cp310-cp310-win_amd64.whl (203 MB)'],
    ['out', '    ▓▓▓░░░░░░░░░░░░░░░░░  8%  [ 340 KB/s · ETA 08:34 ]'],
    ['comment', '    (…真的等,不快进,你此刻在切换标签页…)', { pauseAfter: 900 }],
    ['out', '    ▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░ 58%  [ 210 KB/s · ETA 06:52 ]'],
    ['err', '    ✕ ReadTimeoutError: files.pythonhosted.org'],
    ['err', 'ERROR: Could not install packages due to an OSError'],
    ['comment', '$ # 半小时后,你去 pip.conf 换镜像', { pauseAfter: 700 }],
    ['cmd', '$ pip install torch --index-url https://mirrors.aliyun.com/pypi/simple'],
    ['out', 'Successfully installed torch-2.5.1'],
    ['comment', '$ # ??? 装的是 CPU 版,你的 4070 没生效', { pauseAfter: 600 }],
    ['cmd', '$ pip install demucs'],
    ['out', 'Successfully installed basicsr-1.4.2'],
    ['cmd', '$ python -c "from demucs import pretrained"'],
    ['err', 'ImportError: cannot import name \'rgb_to_grayscale\''],
    ['err', '  from \'torchvision.transforms.functional_tensor\''],
    ['err', '  (site-packages\\basicsr\\data\\degradations.py:8)'],
    ['comment', '$ # 你打开 GitHub issue 页,看到 42 条评论、9 个 workaround…', { pauseAfter: 1100 }],

    // ── Divider ──
    ['blank', ''],
    ['divider', '───────────────  换成 LaunchAI 之后 :  ───────────────'],
    ['blank', ''],

    // ── PART 2 · LaunchAI ──
    ['cmd', '$ ./LaunchAI.exe', { pauseAfter: 400 }],
    ['la', '[LaunchAI]', ' detecting CUDA          → 12.1 (RTX 4070 · 12 GB)'],
    ['la-mirror', '[LaunchAI]', ' rerouting torch         → mirrors.aliyun.com/pytorch-wheels'],
    ['la', '[LaunchAI]', '   torch-2.5.1+cu121-cp310-cp310-win_amd64.whl'],
    ['la-check', '[LaunchAI]', '   ✔ torch 2.5.1+cu121 (2.3 GB · 24s)'],
    ['la-mirror', '[LaunchAI]', ' cloning demucs          → via ghproxy.link (23 ms)'],
    ['la', '[LaunchAI]', '   filtered `torch*` from requirements_minimal.txt'],
    ['la-check', '[LaunchAI]', '   ✔ demucs 4.0.1 ready'],
    ['la-mirror', '[LaunchAI]', ' cloning Real-ESRGAN     → via github.moeyy.xyz (41 ms)'],
    ['la', '[LaunchAI]', '   pip: tb-nightly → basicsr → facexlib → gfpgan'],
    ['la', '[LaunchAI]', '   patched site-packages/basicsr/data/degradations.py:8'],
    ['la-check', '[LaunchAI]', '   ✔ Real-ESRGAN ready'],
    ['la', '[LaunchAI]', ' + whisper · yolo · rvc · gpt-sovits · audiocraft · iopaint'],
    ['blank', ''],
    ['la-check', '[LaunchAI]', ' 8 tools ready · 3m 42s · 你没碰过 pip', { pauseAfter: 3200 }],
  ];

  // ── Rendering ──
  const kindClass = {
    cmd: 't-cmd',
    out: 't-out',
    err: 't-err',
    comment: 't-comment',
    divider: 't-divider',
    la: 't-la-body',
    'la-body': 't-la-body',
    'la-mirror': 't-la-body',
    'la-check': 't-check',
    blank: 't-out',
  };

  function appendLine(item) {
    const [kind, a, b, opts] = item;
    const span = document.createElement('span');
    span.className = kindClass[kind] || 't-out';

    if (kind === 'la' || kind === 'la-body' || kind === 'la-mirror' || kind === 'la-check') {
      // [LaunchAI] tag + body
      const tag = document.createElement('span');
      tag.className = 't-la-tag';
      tag.textContent = a;
      const body = document.createElement('span');
      if (kind === 'la-mirror') {
        // highlight the mirror host name after the arrow
        const m = b.match(/(.*→\s+)(\S+)(.*)/);
        if (m) {
          body.className = 't-la-body';
          body.append(document.createTextNode(m[1]));
          const host = document.createElement('span');
          host.className = 't-la-mirror';
          host.textContent = m[2];
          body.append(host);
          body.append(document.createTextNode(m[3]));
        } else {
          body.textContent = b;
        }
      } else {
        body.textContent = b;
      }
      span.append(tag, body);
    } else {
      span.textContent = a;
    }

    term.appendChild(span);
    term.appendChild(document.createTextNode('\n'));
    term.scrollTop = term.scrollHeight;
    return { span, opts: opts || (typeof b === 'object' ? b : {}) };
  }

  // Type a text node char by char inside a span.
  function typeLine(item) {
    return new Promise((resolve) => {
      const [kind] = item;
      const { span } = appendLine(item);

      if (prefersReduced) {
        resolve();
        return;
      }

      // Blank / divider land instantly.
      if (kind === 'blank' || kind === 'divider') {
        setTimeout(resolve, 80);
        return;
      }

      // For LA-prefixed lines, keep the [LaunchAI] tag instant, type the body only.
      let bodyNode = null;
      if (kind === 'la' || kind === 'la-body' || kind === 'la-mirror' || kind === 'la-check') {
        bodyNode = span.children[1];
      }

      if (bodyNode) {
        const original = bodyNode.cloneNode(true);
        const text = bodyNode.textContent;
        // Simplify: type into the body span as plain text; drop the highlight
        // subspan (mirror color) during typing, then swap back to original when done.
        bodyNode.textContent = '';
        let i = 0;
        const speed = 10 + Math.random() * 10;
        const tick = () => {
          if (state.paused) { setTimeout(tick, 120); return; }
          bodyNode.textContent = text.slice(0, ++i);
          term.scrollTop = term.scrollHeight;
          if (i < text.length) setTimeout(tick, speed + (Math.random() * 8 - 4));
          else {
            // Restore original (brings back mirror-color highlight)
            span.replaceChild(original, bodyNode);
            resolve();
          }
        };
        tick();
      } else {
        const text = span.textContent;
        span.textContent = '';
        let i = 0;
        const speed = kind === 'cmd' ? 22 :
          kind === 'err' ? 14 :
            kind === 'comment' ? 20 :
              12;
        const tick = () => {
          if (state.paused) { setTimeout(tick, 120); return; }
          span.textContent = text.slice(0, ++i);
          term.scrollTop = term.scrollHeight;
          if (i < text.length) setTimeout(tick, speed + (Math.random() * 6 - 3));
          else resolve();
        };
        tick();
      }
    });
  }

  // ── Runner ──
  const state = { paused: false, running: false };

  async function run() {
    state.running = true;
    term.textContent = '';

    if (prefersReduced) {
      // Dump everything at once as static content.
      for (const item of script) appendLine(item);
      toggle.style.display = 'none';
      return;
    }

    for (const item of script) {
      if (!state.running) return;
      await typeLine(item);
      const opts = item[3] || (typeof item[2] === 'object' ? item[2] : null);
      const gap = (opts && opts.pauseAfter) || 260;
      await sleep(gap);
    }

    // add a blinking cursor at the end and loop after a beat
    const cursor = document.createElement('span');
    cursor.className = 't-cursor';
    term.appendChild(cursor);

    await sleep(5000);
    if (state.running) run();
  }

  function sleep(ms) {
    return new Promise((r) => {
      const start = Date.now();
      const tick = () => {
        if (!state.running) return r();
        if (state.paused) return setTimeout(tick, 120);
        const left = ms - (Date.now() - start);
        if (left <= 0) r();
        else setTimeout(tick, Math.min(left, 60));
      };
      tick();
    });
  }

  // ── Pause control ──
  toggle.addEventListener('click', () => {
    state.paused = !state.paused;
    toggle.textContent = state.paused ? '继续' : '暂停';
  });

  // Stop when tab is hidden (saves CPU + keeps state coherent)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) state.paused = true;
    else if (toggle.textContent === '暂停') state.paused = false;
  });

  run();
})();


/* ─────────────────────────────────────────────────────────────────
   Floating TOC · active-section highlighting via IntersectionObserver
   ───────────────────────────────────────────────────────────────── */
(() => {
  const toc = document.getElementById('sideToc');
  if (!toc) return;

  const links = toc.querySelectorAll('.toc-link');
  const linkByTarget = new Map();
  links.forEach((l) => linkByTarget.set(l.dataset.target, l));

  const sectionIds = ['top', 'overview', 'tools', 'modes', 'patches', 'notices'];
  const sections = sectionIds
    .map((id) => document.getElementById(id))
    .filter(Boolean);

  if ('IntersectionObserver' in window && sections.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            links.forEach((l) => l.classList.remove('active'));
            const link = linkByTarget.get(entry.target.id);
            if (link) link.classList.add('active');
          }
        });
      },
      { rootMargin: '-30% 0px -60% 0px' }
    );
    sections.forEach((s) => observer.observe(s));
  }
})();


/* ─────────────────────────────────────────────────────────────────
   Lightbox · 点 mode-shot 缩略图放大查看
   Esc / 点遮罩 / 点关闭按钮 均可关闭;焦点复位到触发元素
   ───────────────────────────────────────────────────────────────── */
(() => {
  const box = document.getElementById('lightbox');
  if (!box) return;

  const bigImg = box.querySelector('.lightbox-img');
  const bigCap = box.querySelector('.lightbox-cap');
  const closeBtn = document.getElementById('lightboxClose');

  let lastFocused = null;
  let prevBodyOverflow = '';

  function open(src, alt, caption, trigger) {
    bigImg.src = src;
    bigImg.alt = alt || '';
    bigCap.textContent = caption || '';
    lastFocused = trigger || document.activeElement;
    prevBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    box.hidden = false;
    closeBtn.focus();
  }

  function close() {
    if (box.hidden) return;
    box.hidden = true;
    bigImg.removeAttribute('src');
    bigCap.textContent = '';
    document.body.style.overflow = prevBodyOverflow;
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    }
  }

  // 事件委托:点缩略图 img 就打开
  document.addEventListener('click', (e) => {
    const img = e.target.closest('.mode-shot-frame > img');
    if (!img) return;
    const fig = img.closest('figure');
    const cap = fig ? (fig.querySelector('figcaption')?.textContent || '') : '';
    open(img.currentSrc || img.src, img.alt, cap.trim(), img);
  });

  // 遮罩点击关闭,但点到图片本身不关(让人细看)
  box.addEventListener('click', close);
  bigImg.addEventListener('click', (e) => e.stopPropagation());
  closeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    close();
  });

  // 键盘:Esc 关闭
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !box.hidden) close();
  });
})();


/* ─────────────────────────────────────────────────────────────────
   Back-to-top button
   ───────────────────────────────────────────────────────────────── */
(() => {
  const btn = document.getElementById('backTop');
  if (!btn) return;

  const prefersReduced =
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const behavior = prefersReduced ? 'auto' : 'smooth';

  btn.hidden = false;

  const threshold = 500;
  let ticking = false;
  const update = () => {
    if (window.scrollY > threshold) btn.classList.add('visible');
    else btn.classList.remove('visible');
    ticking = false;
  };
  window.addEventListener(
    'scroll',
    () => {
      if (!ticking) {
        window.requestAnimationFrame(update);
        ticking = true;
      }
    },
    { passive: true }
  );
  update();

  btn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior });
  });
})();
