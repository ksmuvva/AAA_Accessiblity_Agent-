// @body
const PERSONAL = new RegExp(arg, 'i');
const fieldLabel = (el) => (el.labels && el.labels[0] && el.labels[0].innerText.trim())
  || el.getAttribute('aria-label') || idrefText(el.getAttribute('aria-labelledby'), el) || (el.getAttribute('title') || '').trim();
const forms = [...document.querySelectorAll('form')];
// Controls outside any <form> still form a "form" for auditing purposes.
const orphans = [...document.querySelectorAll('input, select, textarea'), ...shadowAll('input, select, textarea')]
  .filter((el) => !el.form && el.type !== 'hidden');
const groups = forms.map((f) => ({ el: f, controls: [...f.elements] }));
if (orphans.length) groups.push({ el: null, controls: orphans });
return groups.slice(0, 10).map((g, i) => {
  const controls = g.controls.filter((el) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type));
  const submit = g.el ? g.el.querySelector('button[type="submit"], input[type="submit"], input[type="image"], button:not([type])') : null;
  return {
    // Controls outside any <form> get 'body' so test_forms can re-test them (null can't be sent through the tool schema).
    form_selector: g.el ? cssPath(g.el) : 'body',
    name: g.el ? (g.el.getAttribute('aria-label') || g.el.getAttribute('name') || g.el.id || '') : '(controls outside a form)',
    method: g.el ? (g.el.getAttribute('method') || 'get').toLowerCase() : null,
    novalidate: g.el ? g.el.noValidate : null,
    submit: submit ? { selector: cssPath(submit), name: label(submit) } : null,
    captcha: !!(g.el || document).querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, [class*="captcha" i], img[alt*="captcha" i]'),
    fields: controls.slice(0, 25).map((el) => {
      const key = [el.name, el.id, el.getAttribute('autocomplete'), fieldLabel(el), el.placeholder].join(' ');
      return {
        selector: cssPath(el), tag: el.tagName.toLowerCase(), type: el.type, name: el.name || null,
        label: fieldLabel(el), placeholder: el.placeholder || null,
        visible_label: !!(el.labels && el.labels[0] && isVisible(el.labels[0])) || (el.getAttribute('aria-labelledby') || '')
          .split(/\s+/).some((id) => { const t = id && el.getRootNode().getElementById(id); return t && isVisible(t) && t.innerText.trim(); }),
        required: el.required || el.getAttribute('aria-required') === 'true',
        autocomplete: el.getAttribute('autocomplete'),
        likely_personal_without_autocomplete: PERSONAL.test(key) && !el.getAttribute('autocomplete'),
        blocks_paste: el.type === 'password' && (el.getAttribute('onpaste') || '').includes('return false'),
        change_handler: !!el.getAttribute('onchange'),
      };
    }),
  };
});
