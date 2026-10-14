// @body
const I = window.__a11yInstr || { timers: [], listeners: [], opens: 0 };
const where = (t) => t === window ? 'window' : t === document ? 'document' : (t && t.nodeType === 1 ? cssPath(t) : 'other');
const byType = {};
for (const { type, target } of I.listeners) {
  (byType[type] = byType[type] || new Set()).add(where(target));
}
for (const el of [...document.querySelectorAll('body *')].slice(0, 5000)) {
  for (const type of ['click', 'mousedown', 'pointerdown', 'touchstart', 'keydown']) {
    if (el.getAttribute('on' + type) || typeof el['on' + type] === 'function') (byType[type] = byType[type] || new Set()).add(cssPath(el));
  }
}
const listeners = {};
for (const [type, set] of Object.entries(byType)) listeners[type] = [...set].slice(0, 40);
return { listeners, timers: I.timers.slice(0, 100), window_open_calls: I.opens, installed: !!window.__a11yInstr };
