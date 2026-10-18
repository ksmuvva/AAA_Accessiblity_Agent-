// @body
let el = document.activeElement;
while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
if (!el || el === document.body || el === document.documentElement) return null;
const props = ['outline-style', 'outline-width', 'outline-color', 'box-shadow', 'border-top-color', 'border-bottom-color',
  'border-top-width', 'border-bottom-width', 'background-color', 'color', 'text-decoration-line', 'transform', 'opacity'];
// A transparent outline (often added for forced-colours mode) shows nothing, so it is no indicator.
const clear = (c) => c === 'transparent' || /,\s*0(\.0*)?\)$/.test(c);
const snap = () => {
  const out = [];
  for (const [node, pseudo] of [[el, null], [el, '::before'], [el, '::after'], [el.parentElement, null]]) {
    if (!node) continue;
    const s = getComputedStyle(node, pseudo);
    out.push(...props.map((p) => (p.startsWith('outline') && clear(s.outlineColor) ? '' : s.getPropertyValue(p))));
  }
  return out.join('|');
};
const fs = getComputedStyle(el);
const hasOutline = fs.outlineStyle !== 'none' && parseFloat(fs.outlineWidth) > 0 && !clear(fs.outlineColor);
const focusedStyles = snap();
// Measure obscuring while focused (the browser has scrolled it into view).
const r = el.getBoundingClientRect();
const pts = [[0.5, 0.5], [0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]]
  .map(([fx, fy]) => [r.left + r.width * fx, r.top + r.height * fy])
  .filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
const ownLabel = el.labels && el.labels[0];
const isOwn = (t) => t && (t === el || el.contains(t) || t.contains(el) || (ownLabel && ownLabel.contains(t)));
const hits = pts.filter(([x, y]) => isOwn(document.elementFromPoint(x, y)));
let coveredBy = null;
if (pts.length && !hits.length) {
  const t = document.elementFromPoint(pts[0][0], pts[0][1]);
  coveredBy = t ? cssPath(t) : null;
}
// Forced-colours mode drops box-shadow and background images and replaces colours: only an outline (of any colour),
// a border width or style, an underline or a transform still shows there.
const forced = () => { const s = getComputedStyle(el); return ['outline-style', 'outline-width', 'border-top-width',
  'border-bottom-width', 'border-top-style', 'text-decoration-line', 'transform'].map((p) => s.getPropertyValue(p)).join('|'); };
const anyOutline = fs.outlineStyle !== 'none' && parseFloat(fs.outlineWidth) > 0;
const focusedForced = forced();
el.blur();
const unfocusedStyles = snap();
const unfocusedForced = forced();
el.focus({ preventScroll: true });
return {
  id: idOf(el),
  selector: cssPath(el),
  name: label(el),
  tag: el.tagName.toLowerCase(),
  tabindex: el.getAttribute('tabindex'),
  indicator: hasOutline || focusedStyles !== unfocusedStyles,
  forced_colors_lost: (hasOutline || focusedStyles !== unfocusedStyles) && !anyOutline && focusedForced === unfocusedForced,
  visible: r.width > 0 && r.height > 0 && pts.length > 0,
  obscured: !pts.length ? 'offscreen' : !hits.length ? 'full' : hits.length < pts.length ? 'partial' : 'none',
  covered_by: coveredBy,
  outline: { style: fs.outlineStyle, width: parseFloat(fs.outlineWidth) || 0, color: fs.outlineColor, offset: parseFloat(fs.outlineOffset) || 0 },
  box_shadow: fs.boxShadow,
  behind: effectiveBg(el.parentElement || el),
  size: { width: Math.round(r.width), height: Math.round(r.height) },
  text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
};
