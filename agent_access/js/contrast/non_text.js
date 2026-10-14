// @body
const out = [];
// Form controls: their boundary must reach 3:1 against what's around them (unless the browser styles them).
for (const el of [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]):not([type=image]), select, textarea')].filter(isVisible).slice(0, 60)) {
  const s = getComputedStyle(el);
  const around = effectiveBg(el.parentElement || el);
  const width = parseFloat(s.borderTopWidth) || parseFloat(s.borderBottomWidth) || 0;
  out.push({ kind: 'control boundary', selector: cssPath(el), name: label(el), around,
    border: width > 0 && s.borderStyle !== 'none' ? (parseFloat(s.borderBottomWidth) >= parseFloat(s.borderTopWidth) ? s.borderBottomColor : s.borderTopColor) : null,
    fill: s.backgroundColor });
}
// Icons that carry meaning: SVGs inside links/buttons without text, or role=img.
// An aria-hidden icon is still the only visible content of a text-less control (the usual icon-button pattern), so it counts.
for (const svg of [...document.querySelectorAll('svg')].filter(isVisible).slice(0, 60)) {
  const control = svg.closest('a, button, [role="button"], [role="link"]');
  const hidden = !!svg.closest('[aria-hidden="true"]');
  const meaningful = (svg.getAttribute('role') === 'img' && !hidden) || (control && !(control.innerText || '').trim());
  if (!meaningful) continue;
  const shape = svg.querySelector('path, circle, rect, polygon, line, polyline') || svg;
  const s = getComputedStyle(shape);
  const paint = s.fill && s.fill !== 'none' ? s.fill : s.stroke;
  out.push({ kind: 'icon', selector: cssPath(control || svg), name: control ? label(control) : (svg.getAttribute('aria-label') || ''),
    around: effectiveBg(svg), color: paint && paint.startsWith('rgb') ? paint : getComputedStyle(svg).color });
}
return out;
