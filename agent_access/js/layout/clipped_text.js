// @body
const out = [];
for (const el of [...document.querySelectorAll('body *')].slice(0, 5000)) {
  const s = getComputedStyle(el);
  const clipsX = ['hidden', 'clip'].includes(s.overflowX), clipsY = ['hidden', 'clip'].includes(s.overflowY);
  if ((!clipsX && !clipsY) || !(el.innerText || '').trim()) continue;
  if ((clipsY && el.scrollHeight > el.clientHeight + 2) || (clipsX && el.scrollWidth > el.clientWidth + 2)) {
    out.push({ id: idOf(el), selector: cssPath(el), text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60) });
  }
}
return out;
