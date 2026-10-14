// @body
const cw = document.documentElement.clientWidth;
const sw = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0);
if (sw <= cw + 1) return { client_width: cw, scroll_width: sw, horizontal_scroll: false, offenders: [] };
const EXCEPT = 'table, pre, code, img, video, canvas, svg, iframe, map, [role="grid"], [role="img"]';
const wide = (el) => el.getBoundingClientRect().right > cw + 1;
const offenders = [];
for (const el of [...document.querySelectorAll('body *')].slice(0, 5000)) {
  if (!isVisible(el) || !wide(el)) continue;
  // Report outermost offenders only; skip content inside its own horizontal scroller.
  if (el.parentElement && el.parentElement !== document.body && wide(el.parentElement)) continue;
  let p = el.parentElement, scrolls = false;
  while (p && p !== document.body) {
    if (['auto', 'scroll'].includes(getComputedStyle(p).overflowX)) { scrolls = true; break; }
    p = p.parentElement;
  }
  if (scrolls) continue;
  offenders.push({ selector: cssPath(el), width: Math.round(el.getBoundingClientRect().width), likely_exception: !!el.closest(EXCEPT) || !!el.querySelector(EXCEPT) && el.children.length === 1 });
}
return { client_width: cw, scroll_width: sw, horizontal_scroll: true, offenders: offenders.slice(0, 30) };
