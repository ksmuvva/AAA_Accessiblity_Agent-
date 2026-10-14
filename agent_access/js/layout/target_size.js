// @body
const SEL = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"],'
  + '[role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], [role="switch"], [role="option"], [onclick],'
  + '[tabindex]:not([tabindex="-1"])';
const MIN = (arg && arg.min) || 24, SPACING = !arg || arg.spacing !== false;
const all = [...document.querySelectorAll(SEL)].filter(isVisible);
// Ignore targets nested inside another target: the outer one is what the pointer hits.
const targets = all.filter((el) => !all.some((o) => o !== el && o.contains(el))).map((el) => {
  const r = el.getBoundingClientRect();
  return { el, r, cx: r.left + r.width / 2, cy: r.top + r.height / 2, under: r.width < MIN - 0.5 || r.height < MIN - 0.5 };
});
const isInline = (el) => {
  if (el.tagName !== 'A' || getComputedStyle(el).display !== 'inline') return false;
  const parentText = (el.parentElement?.innerText || '').trim();
  return parentText.length > (el.innerText || '').trim().length + 15;
};
const distToRect = (x, y, r) => Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));
const failures = [], uaReview = [];
let inlineSkipped = 0;
for (const t of targets.filter((t) => t.under)) {
  if (isInline(t.el)) { inlineSkipped++; continue; }
  // Spacing exception: a 24px circle centred on the target must not intersect another target
  // or another undersized target's circle.
  const conflict = !SPACING || targets.some((o) => o !== t &&
    (o.under ? Math.hypot(o.cx - t.cx, o.cy - t.cy) < MIN : distToRect(t.cx, t.cy, o.r) < MIN / 2));
  if (!conflict) continue;
  const item = { selector: cssPath(t.el), name: label(t.el), width: Math.round(t.r.width), height: Math.round(t.r.height) };
  const native = t.el.tagName === 'INPUT' && ['checkbox', 'radio'].includes(t.el.type) && getComputedStyle(t.el).appearance !== 'none';
  (native ? uaReview : failures).push(item);
}
return { total_targets: targets.length, undersized: targets.filter((t) => t.under).length, inline_links_skipped: inlineSkipped, failures, ua_review: uaReview };
