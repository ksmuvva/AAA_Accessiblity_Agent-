// @body
// arg 'blur': blur the focused element (the deepest, inside shadow roots) and remember it; 'focus': focus it again.
let el = window.__a11yBlurred || document.activeElement;
while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
if (!el) return;
if (arg === 'blur') {
  window.__a11yBlurred = el;
  el.blur();
} else {
  delete window.__a11yBlurred;
  el.focus({ preventScroll: true });
}
