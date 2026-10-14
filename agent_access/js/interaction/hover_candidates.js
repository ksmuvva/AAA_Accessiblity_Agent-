// @body
const SEL = '[aria-haspopup], [aria-describedby], [data-tooltip], [data-tip], [data-toggle="tooltip"], [data-bs-toggle="tooltip"],'
  + ' nav li, [class*="tooltip" i], [class*="dropdown" i] > a, [class*="dropdown" i] > button, [class*="has-sub" i], [class*="menu-item-has-children" i]';
const seen = new Set();
return [...document.querySelectorAll(SEL)].filter((el) => isVisible(el) && !el.matches('[role="tooltip"]'))
  .filter((el) => { const k = cssPath(el); if (seen.has(k)) return false; seen.add(k); return true; })
  .slice(0, arg).map(cssPath);
