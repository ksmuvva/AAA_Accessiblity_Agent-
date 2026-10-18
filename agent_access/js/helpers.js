const __ids = (window.__a11yIds = window.__a11yIds || { map: new WeakMap(), next: 1 });
const idOf = (el) => {
  if (!__ids.map.has(el)) __ids.map.set(el, __ids.next++);
  return __ids.map.get(el);
};
// The parent in the flat tree: a shadow root's top element continues at its host.
const up = (el) => el.parentElement || el.getRootNode?.().host || null;
// Elements matching `sel` inside open shadow roots, which document.querySelectorAll doesn't enter.
const shadowAll = (sel, root = document) => [...root.querySelectorAll('*')].filter((el) => el.shadowRoot)
  .flatMap((el) => [...el.shadowRoot.querySelectorAll(sel), ...shadowAll(sel, el.shadowRoot)]);
const cssPath = (el) => {
  const host = el && el.getRootNode?.().host;
  const parts = [];
  while (el && el.nodeType === 1 && parts.length < 6) {
    if (el.id) { parts.unshift('#' + CSS.escape(el.id)); break; }
    let part = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (parent) {
      const same = [...parent.children].filter((c) => c.tagName === el.tagName);
      if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
    }
    parts.unshift(part);
    el = parent;
  }
  // Inside a shadow root: Playwright's '>>' continues the search inside the host's open shadow root.
  return (host ? cssPath(host) + ' >> ' : '') + parts.join(' > ');
};
// Text of the elements an id-reference attribute points to (aria-labelledby, aria-describedby, ...).
// Ids resolve in `from`'s own tree: a shadow root's ids aren't in the document.
const idrefText = (ids, from = document) => (ids || '').split(/\s+/)
  .map((id) => id && from.getRootNode().getElementById(id)?.innerText || '').join(' ').trim();
const label = (el) => (el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText
  || el.value || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '')
  .toString().trim().replace(/\s+/g, ' ').slice(0, 60);
// A link's accessible name. Unlike label(), it includes the alt text of images inside the link, so an image link
// with alt text has a name and one without (WCAG failure F89) has none.
const linkName = (a) => (a.getAttribute('aria-label')
  || idrefText(a.getAttribute('aria-labelledby'), a)
  || (a.innerText || '').trim()
  || [...a.querySelectorAll('img[alt], input[type="image"][alt], [role="img"][aria-label], svg title')]
    .map((i) => i.getAttribute('alt') || i.getAttribute('aria-label') || i.textContent || '').join(' ').trim()
  || a.getAttribute('title') || '').toString().trim().replace(/\s+/g, ' ').slice(0, 80);
const parseRgb = (c) => {
  const m = (c || '').match(/rgba?\(([^)]+)\)/);
  if (!m) return null;
  const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
};
// The colour actually behind an element: background colours composited up the tree over white.
// Returns null when an image or gradient is involved (the caller samples pixels instead).
const effectiveBg = (el) => {
  const layers = [];
  for (let node = el; node && node.nodeType === 1; node = up(node)) {
    const s = getComputedStyle(node);
    if (s.backgroundImage && s.backgroundImage !== 'none') return null;
    const c = parseRgb(s.backgroundColor);
    if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
  }
  let out = { r: 255, g: 255, b: 255 };
  for (const c of layers.reverse()) out = { r: c.r * c.a + out.r * (1 - c.a), g: c.g * c.a + out.g * (1 - c.a), b: c.b * c.a + out.b * (1 - c.a) };
  return `rgb(${Math.round(out.r)}, ${Math.round(out.g)}, ${Math.round(out.b)})`;
};
const isVisible = (el) => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0;
};
