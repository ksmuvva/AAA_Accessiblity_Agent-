// @body
const cw = document.documentElement.clientWidth;
const sw = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0);
// Text cut off by a container that hides its overflow: no scrollbar, so nothing reaches it (a 1px screen-reader-only
// box is not such a container).
const clipped = [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => {
  if (el.children.length || !(el.innerText || '').trim() || !isVisible(el)) return false;
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowX, box = p.getBoundingClientRect();
    if (['auto', 'scroll'].includes(overflow)) return false;
    if (['hidden', 'clip'].includes(overflow)) return box.width > 2 && box.height > 2 && el.getBoundingClientRect().right > box.right + 1;
  }
  return false;
}).slice(0, 10).map((el) => ({ selector: cssPath(el), text: el.innerText.trim().slice(0, 50) }));
if (sw <= cw + 1) return { client_width: cw, scroll_width: sw, horizontal_scroll: false, offenders: [], clipped };
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
return { client_width: cw, scroll_width: sw, horizontal_scroll: true, offenders: offenders.slice(0, 30), clipped };
