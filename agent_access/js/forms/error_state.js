// @body
const root = arg ? document.querySelector(arg) : document;
if (!root) return null;
const controls = [...root.querySelectorAll('input, select, textarea')].filter((el) => el.type !== 'hidden');
const form = root.tagName === 'FORM' ? root : null;
const alerts = heardLiveText();   // before the validity checks below, which fire invalid events
return {
  native_invalid: form && form.noValidate ? [] : controls.filter((el) => el.willValidate && !el.checkValidity())
    .map((el) => ({ selector: cssPath(el), message: el.validationMessage })),
  aria_invalid: controls.filter((el) => el.getAttribute('aria-invalid') === 'true').map((el) => ({
    selector: cssPath(el),
    described_by_text: (idrefText(el.getAttribute('aria-describedby')) + ' ' + idrefText(el.getAttribute('aria-errormessage'))).trim(),
  })),
  alerts,
  focus: document.activeElement && document.activeElement !== document.body ? cssPath(document.activeElement) : null,
  focus_in_form: !!(form && form.contains(document.activeElement)),
};
