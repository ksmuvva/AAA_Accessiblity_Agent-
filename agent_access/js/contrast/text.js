// @body
// The text and background colours as painted, down the chain from the root over white: opacity fades a whole group,
// its background and text alike. null when an image or gradient is in the chain (the caller samples pixels).
const composite = (el, textColor) => {
  const chain = [];
  for (let e = el; e; e = up(e)) chain.unshift(getComputedStyle(e));
  if (chain.some((s) => s.backgroundImage !== 'none')) return null;
  const over = (css, b) => { const c = parseRgb(css); return c ? [c.r, c.g, c.b].map((v, i) => v * c.a + b[i] * (1 - c.a)) : b; };
  const paint = (k, b, last) => {
    if (k === chain.length) return last(b);
    const inner = paint(k + 1, over(chain[k].backgroundColor, b), last), o = parseFloat(chain[k].opacity);
    return inner.map((v, i) => v * o + b[i] * (1 - o));
  };
  const css = (c) => `rgb(${c.map(Math.round).join(', ')})`;
  return [css(paint(0, [255, 255, 255], (b) => over(textColor, b))), css(paint(0, [255, 255, 255], (b) => b))];
};
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
  // Faded over an image: no composite, so the pixels are sampled instead (they show the fading).
  const [color, background] = opacity < 1 ? composite(el, s.color) || [s.color, null] : [s.color, effectiveBg(el)];
  out.push({
    selector: cssPath(el),
    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60),
    color,
    background,
    font_px: parseFloat(s.fontSize),
    weight: parseInt(s.fontWeight, 10) || 400,
    opacity,
    rect: { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height },
  });
}
return out;
