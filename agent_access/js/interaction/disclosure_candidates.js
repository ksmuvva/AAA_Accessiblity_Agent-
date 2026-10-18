// @body
const SEL = 'button[aria-expanded], [role="button"][aria-expanded], [aria-haspopup]:not(a[href]:not([href^="#"])),'
  + ' summary, button[aria-controls], [data-toggle], [data-bs-toggle], button[popovertarget]';
const named = [...document.querySelectorAll('button, [role="button"]')].filter((el) => /menu|more|open|show|expand|filter|options|settings|account|details|info/i.test(label(el)));
// Custom controls (not native ones) with a click handler: what a click opens, Enter or Space must open too (2.1.1).
const NATIVE = 'a[href], button, input, select, textarea, summary, label';
const clickers = (window.__a11yInstr?.listeners || []).filter((l) => l.type === 'click' && l.target?.nodeType === 1).map((l) => l.target);
const custom = [...document.querySelectorAll('[role="button"], [role="tab"], [role="menuitem"], [onclick]'), ...clickers]
  .filter((el) => el !== document.body && !el.closest(NATIVE) && !el.querySelector(NATIVE));
const seen = new Set();
return [...document.querySelectorAll(SEL), ...named, ...custom].filter(isVisible)
  .filter((el) => { const k = cssPath(el); if (seen.has(k)) return false; seen.add(k); return true; })
  .slice(0, arg).map((el) => ({ selector: cssPath(el), name: label(el) }));
