// @body
// Content pushed past the right edge when the page can't scroll horizontally is lost.
const de = document.documentElement, b = document.body;
const clipsPage = [getComputedStyle(de).overflowX, getComputedStyle(b).overflowX].some((v) => ['hidden', 'clip'].includes(v));
if (!clipsPage) return [];
const cw = de.clientWidth;
return [...document.querySelectorAll('body *')].slice(0, 4000)
  .filter((el) => isVisible(el) && (el.innerText || '').trim() && el.children.length === 0 && el.getBoundingClientRect().left >= cw)
  .slice(0, 20).map((el) => ({ selector: cssPath(el), text: (el.innerText || '').trim().slice(0, 50) }));
