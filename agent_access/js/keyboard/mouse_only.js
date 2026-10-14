// @body
const FOCUSABLE = 'a[href], button, input, select, textarea, summary, iframe, [tabindex], [contenteditable="true"], audio[controls], video[controls]';
const out = [];
const els = [...document.querySelectorAll('body *')].slice(0, 5000);
for (const el of els) {
  const clickable = el.hasAttribute('onclick') || getComputedStyle(el).cursor === 'pointer';
  if (!clickable || !isVisible(el) || el.closest(FOCUSABLE) || el.querySelector(FOCUSABLE)) continue;
  // Only report the outermost element of a pointer-cursor region.
  if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer' && !el.parentElement.closest(FOCUSABLE)) continue;
  out.push({ selector: cssPath(el), name: label(el), has_onclick: el.hasAttribute('onclick') });
}
return out;
