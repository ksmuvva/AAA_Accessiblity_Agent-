// @body
const SEL = 'button[aria-expanded], [role="button"][aria-expanded], [aria-haspopup]:not(a[href]:not([href^="#"])),'
  + ' summary, button[aria-controls], [data-toggle], [data-bs-toggle], button[popovertarget]';
const named = [...document.querySelectorAll('button, [role="button"]')].filter((el) => /menu|more|open|show|expand|filter|options|settings|account|details|info/i.test(label(el)));
const seen = new Set();
return [...document.querySelectorAll(SEL), ...named].filter(isVisible)
  .filter((el) => { const k = cssPath(el); if (seen.has(k)) return false; seen.add(k); return true; })
  .slice(0, arg).map((el) => ({ selector: cssPath(el), name: label(el) }));
