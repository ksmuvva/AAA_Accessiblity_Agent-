// @body
const log = window.__a11yFocusLog || [];
const el = log[log.length - 1];
if (!el || !el.isConnected || el === document.body || el === document.documentElement) return null;
return { id: idOf(el), selector: cssPath(el), name: label(el), tag: el.tagName.toLowerCase() };
