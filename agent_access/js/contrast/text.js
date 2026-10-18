// @body
const out = [];
for (const el of [...document.querySelectorAll('body *'), ...shadowAll('*')].slice(0, 6000)) {
  if (out.length >= arg) break;
  const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  if (!own || !isVisible(el)) continue;
  if (el.closest('[disabled], [aria-disabled="true"]') || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION'].includes(el.tagName)) continue;
  const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  let opacity = 1;   // a faded ancestor fades the text too
  for (let e = el; e; e = up(e)) opacity *= parseFloat(getComputedStyle(e).opacity);
  out.push({
    selector: cssPath(el),
    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60),
    color: s.color,
    background: effectiveBg(el),
    font_px: parseFloat(s.fontSize),
    weight: parseInt(s.fontWeight, 10) || 400,
    opacity,
    rect: { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height },
  });
}
return out;
