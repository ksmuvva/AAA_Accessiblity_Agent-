// @body
const root = arg ? document.querySelector(arg) : document;
if (!root) return null;
const controls = [...root.querySelectorAll('input, select, textarea')].filter((el) => el.type !== 'hidden');
const form = root.tagName === 'FORM' ? root : null;
// A screen reader announces changes to live regions it already knew and, of regions inserted with their text, only
// role="alert"; hidden ones never. The first call (before a submission) marks the regions that are there, and
// watches for regions inserted still empty (filled in a later task), which are known before their text arrives.
const LIVE = '[role="alert"]:not([aria-live="off"]), [aria-live="assertive"], [aria-live="polite"], [role="status"]:not([aria-live="off"])';
const first = !document.__a11yLiveMarked;
document.__a11yLiveMarked = true;
if (first) {
  new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
    if (n.nodeType === 1) [n, ...n.querySelectorAll(LIVE)].filter((el) => el.matches(LIVE) && !el.textContent.trim())
      .forEach((el) => { el.__a11yLive = true; });
  }))).observe(document, { childList: true, subtree: true });
}
// display: contents gives a region no box of its own; its content shows wherever its parent's does.
const shown = (el) => (getComputedStyle(el).display === 'contents'
  ? getComputedStyle(el).visibility !== 'hidden' && (!el.parentElement || shown(el.parentElement))
  : el.checkVisibility({ visibilityProperty: true }));
const regions = [...document.querySelectorAll(LIVE)];
const heard = regions.filter((el) => (first || el.__a11yLive || el.getAttribute('role') === 'alert')
  && shown(el) && !el.closest('[aria-hidden="true"]'));
regions.forEach((el) => { el.__a11yLive = true; });
return {
  native_invalid: form && form.noValidate ? [] : controls.filter((el) => el.willValidate && !el.checkValidity())
    .map((el) => ({ selector: cssPath(el), message: el.validationMessage })),
  aria_invalid: controls.filter((el) => el.getAttribute('aria-invalid') === 'true').map((el) => ({
    selector: cssPath(el),
    described_by_text: (idrefText(el.getAttribute('aria-describedby')) + ' ' + idrefText(el.getAttribute('aria-errormessage'))).trim(),
  })),
  alerts: heard.map((el) => (el.innerText || '').trim()).filter(Boolean).map((t) => t.slice(0, 160)),
  focus: document.activeElement && document.activeElement !== document.body ? cssPath(document.activeElement) : null,
  focus_in_form: !!(form && form.contains(document.activeElement)),
};
