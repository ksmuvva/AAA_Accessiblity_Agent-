// @body
const el = document.querySelector(arg);
if (!el) return null;
return { expanded: el.getAttribute('aria-expanded'), focused: document.activeElement === el,
  open_attr: el.tagName === 'SUMMARY' ? el.parentElement.open : null };
