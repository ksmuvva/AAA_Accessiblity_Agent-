// @body
const before = new Set(arg);
const fresh = [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => isVisible(el) && !before.has(idOf(el)));
const freshSet = new Set(fresh);
return fresh.filter((el) => !freshSet.has(el.parentElement)).slice(0, 5).map((el) => {
  const r = el.getBoundingClientRect();
  // Does it cover other content? Sample its centre and inset corners for a non-ancestor element with text.
  const points = [[0.5, 0.5], [0.15, 0.2], [0.85, 0.2], [0.15, 0.8], [0.85, 0.8]];
  const obscures = points.some(([fx, fy]) => document.elementsFromPoint(r.left + r.width * fx, r.top + r.height * fy)
    .some((u) => u !== el && !el.contains(u) && !u.contains(el) && u !== document.body && u !== document.documentElement
      && [...u.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())));
  return { id: idOf(el), selector: cssPath(el), text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
    x: r.left + r.width / 2, y: r.top + r.height / 2, obscures };
});
