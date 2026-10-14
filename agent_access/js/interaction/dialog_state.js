// @body
const fresh = [...document.querySelectorAll('body *')].filter((el) => arg.includes(__ids.map.get(el)));
const dialogSel = '[role="dialog"], [role="alertdialog"], dialog[open], [aria-modal="true"]';
let dialog = null;
for (const el of fresh) { dialog = el.matches(dialogSel) ? el : el.querySelector(dialogSel); if (dialog) break; }
if (!dialog) dialog = [...document.querySelectorAll('dialog[open]')].find(isVisible) || null;
const content = dialog || fresh[0] || null;
const focusables = content ? [...content.querySelectorAll('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(isVisible) : [];
return {
  is_dialog: !!dialog,
  dialog_selector: dialog ? cssPath(dialog) : null,
  modal: !!dialog && (dialog.getAttribute('aria-modal') === 'true' || (dialog.tagName === 'DIALOG' && dialog.matches(':modal'))),
  dialog_name: dialog ? (dialog.getAttribute('aria-label') || (dialog.getAttribute('aria-labelledby') || '').split(' ')
    .map((id) => document.getElementById(id)?.innerText || '').join(' ').trim()) : null,
  focus_inside: !!content && content.contains(document.activeElement),
  focusable_count: focusables.length,
  close_button: focusables.some((b) => /close|dismiss|cancel|×|✕/i.test(label(b))),
};
