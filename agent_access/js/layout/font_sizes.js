// @body
const out = {};
for (const el of [...document.querySelectorAll('body *')].slice(0, 4000)) {
  const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  if (own && isVisible(el)) out[idOf(el)] = { size: parseFloat(getComputedStyle(el).fontSize), selector: cssPath(el),
    text: (el.innerText || '').trim().slice(0, 50) };
}
return out;
