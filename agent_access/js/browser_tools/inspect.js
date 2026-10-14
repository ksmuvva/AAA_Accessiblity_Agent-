(els) => els.slice(0, 3).map((el) => {
  const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const attrs = {};
  for (const a of el.attributes) attrs[a.name] = a.value.slice(0, 200);
  const landmark = el.closest('main, nav, header, footer, aside, form, [role]');
  return {
    tag: el.tagName.toLowerCase(),
    attributes: attrs,
    inherited_lang: el.closest('[lang]')?.getAttribute('lang') ?? null,
    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 300),
    outer_html: el.outerHTML.slice(0, 1500),
    visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
    rect: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) },
    styles: {
      display: s.display, visibility: s.visibility, opacity: s.opacity, color: s.color,
      background_color: s.backgroundColor, font_size: s.fontSize, font_weight: s.fontWeight,
      outline: `${s.outlineStyle} ${s.outlineWidth} ${s.outlineColor}`, overflow: `${s.overflowX} ${s.overflowY}`,
      position: s.position,
    },
    inside: landmark && landmark !== el ? landmark.tagName.toLowerCase() + (landmark.getAttribute('role') ? `[role=${landmark.getAttribute('role')}]` : '') : null,
  };
})
