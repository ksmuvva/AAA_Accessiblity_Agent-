// @body
const words = (t) => (t.match(/\S+/g) || []).length;
const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
// document.body can be null mid-navigation or on a non-HTML response (seen on a bot-wall page).
const bodyFont = (document.body && parseFloat(getComputedStyle(document.body).fontSize)) || 16;
const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
const all = [...document.querySelectorAll('body *')].slice(0, 6000);
const headingSel = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
const headingEls = [...document.querySelectorAll(headingSel)];
// The nearest heading before an element: with technique H80 it can supply a link's purpose.
const headingBefore = (el) => {
  const h = headingEls.filter((x) => x.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).pop();
  return h ? text(h).slice(0, 80) : null;
};
// The nearest ancestor that isn't inline: a <span> or <strong> wrapped around a link is not its context.
const blockOf = (el) => {
  let b = el.parentElement;
  while (b && b.parentElement && getComputedStyle(b).display.startsWith('inline')) b = b.parentElement;
  return b || el;
};
const links =[...document.querySelectorAll('a[href]'), ...shadowAll('a[href]')].filter(isVisible).slice(0, 200).map((a) => ({
  selector: cssPath(a), text: text(a).slice(0, 80), name: linkName(a), aria_label: a.getAttribute('aria-label'), title: a.getAttribute('title'),
  href: a.href, new_window: a.target === '_blank',
  warns_new_window: /new (window|tab)|opens in|external/i.test(text(a) + ' ' + (a.getAttribute('aria-label') || '') + ' ' + (a.getAttribute('title') || '')),
  context: text(a.closest('p, li, td, h1, h2, h3, h4, h5, h6') || a).slice(0, 120),
  // Nothing around it supplies its purpose: alone in its block and its paragraph or list item, no aria-describedby,
  // not in a table cell.
  bare: [blockOf(a), a.closest('p, li, h1, h2, h3, h4, h5, h6') || a].every((c) => text(c) === text(a))
    && !a.hasAttribute('aria-describedby') && !a.closest('td, th'),
  heading: headingBefore(a),
}));
const IMAGES = 'img, [role="img"], svg, input[type="image"], area';
const images = [...document.querySelectorAll(IMAGES), ...shadowAll(IMAGES)].filter((el) => el.tagName === 'AREA' || isVisible(el)).slice(0, 80).map((el) => ({
  selector: cssPath(el), tag: el.tagName.toLowerCase(), alt: el.hasAttribute('alt') ? el.getAttribute('alt') : null,
  aria_label: el.getAttribute('aria-label'), hidden: !!el.closest('[aria-hidden="true"]') || el.getAttribute('role') === 'presentation',
  src: (el.currentSrc || el.getAttribute('src') || '').split('/').pop().slice(0, 60),
  width: Math.round(el.getBoundingClientRect().width), height: Math.round(el.getBoundingClientRect().height),
}));
const bgImages = all.filter((el) => isVisible(el) && getComputedStyle(el).backgroundImage.startsWith('url(')).slice(0, 30).map(cssPath);
const fakeHeadings = all.filter((el) => {
  if (!ownText(el) || !isVisible(el) || el.closest(headingSel + ', a, button, label, th, li, nav, footer')) return false;
  if (!['P', 'DIV', 'SPAN', 'STRONG', 'B', 'FONT'].includes(el.tagName)) return false;
  const s = getComputedStyle(el), t = text(el);
  return t.length > 2 && t.length < 80 && !/[.!?:,]$/.test(t) && (parseFloat(s.fontSize) >= bodyFont * 1.25 || (parseInt(s.fontWeight, 10) >= 700 && s.display === 'block'));
}).slice(0, 20).map((el) => ({ selector: cssPath(el), text: text(el).slice(0, 60), font_px: parseFloat(getComputedStyle(el).fontSize) }));
const paragraphs = [...document.querySelectorAll('p, li, td, dd, blockquote')].filter((el) => isVisible(el) && text(el).length > 120).slice(0, 40).map((el) => {
  const s = getComputedStyle(el), fp = parseFloat(s.fontSize);
  return { selector: cssPath(el), chars: text(el).length, width_px: Math.round(el.getBoundingClientRect().width), font_px: fp,
    chars_per_line: Math.round(el.getBoundingClientRect().width / (fp * 0.5)),
    line_height: s.lineHeight === 'normal' ? 1.2 : Math.round(parseFloat(s.lineHeight) / fp * 100) / 100,
    text_align: s.textAlign, margin_bottom_ratio: Math.round(parseFloat(s.marginBottom) / fp * 100) / 100 };
});
const passages = all.filter((el) => ownText(el) && isVisible(el) && text(el).length >= 20 && !el.querySelector('p, div, li'))
  .slice(0, 200).map((el) => ({ selector: cssPath(el), text: text(el).slice(0, 200), lang: el.closest('[lang]')?.getAttribute('lang') || null }));
const headings = [...document.querySelectorAll(headingSel)].filter(isVisible).length;
const main = document.querySelector('main, [role="main"]') || document.body;
// Tables (1.3.1): data tables need header cells; role=presentation/none marks a layout table and is skipped.
const bold = (cell) => parseInt(getComputedStyle(cell).fontWeight, 10) >= 700 || !!cell.querySelector('b, strong');
const tables = [...document.querySelectorAll('table')].filter((t) => isVisible(t) && !['presentation', 'none'].includes(t.getAttribute('role') || '')).slice(0, 20).map((t) => {
  const rows = [...t.rows], firstRow = rows[0] ? [...rows[0].cells] : [], firstCol = rows.map((r) => r.cells[0]).filter(Boolean);
  const cap = t.querySelector('caption');
  return { selector: cssPath(t), rows: rows.length, cols: Math.max(0, ...rows.map((r) => r.cells.length)),
    caption: cap ? text(cap).slice(0, 60) : null,
    has_headers: !!t.querySelector('th, [scope], [headers], [role="columnheader"], [role="rowheader"]'),
    first_row_bold: firstRow.length > 0 && firstRow.every((c) => bold(c) && text(c)),
    first_col_bold: firstCol.length > 2 && firstCol.every((c) => bold(c) && text(c)),
    first_row_has_links: firstRow.some((c) => c.querySelector('a, button, img')),
    nested_tables: t.querySelectorAll('table').length, in_nav: !!t.closest('nav, header, footer') };
});
// Radios sharing a name with no group label: no fieldset with a legend, no named group or radiogroup (H71, ARIA17; 1.3.1).
const radioSets = {};
for (const r of document.querySelectorAll('input[type="radio"][name]')) {
  if (!r.closest('[hidden]')) (radioSets[(r.form ? cssPath(r.form) : '') + ' ' + r.name] ||= []).push(r);
}
const groupLabel = (r) => {
  const g = r.closest('fieldset, [role="radiogroup"], [role="group"]');
  const legend = g?.tagName === 'FIELDSET' && g.querySelector(':scope > legend');
  return g && ((legend && text(legend)) || g.getAttribute('aria-label') || idrefText(g.getAttribute('aria-labelledby'), g));
};
const ungroupedRadios = Object.values(radioSets).filter((rs) => rs.length > 1 && !groupLabel(rs[0])).slice(0, 10)
  .map((rs) => ({ selector: cssPath(rs[0]), name: rs[0].name, options: rs.map((r) => (r.labels?.[0] ? text(r.labels[0]) : r.value).slice(0, 30)) }));
// Letters separated by spaces to space them out visually ("W E L C O M E"): screen readers spell them out (F32, 1.3.2).
const SPACED = /\b(?:[A-Za-z] ){4,}[A-Za-z]\b/;
const spacedLetters = all.filter((el) => ownText(el) && isVisible(el) && !el.closest('pre, code, kbd, samp')
  && SPACED.test([...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').replace(/\s+/g, ' ')))
  .slice(0, 10).map((el) => ({ selector: cssPath(el), text: (text(el).match(SPACED) || [''])[0].slice(0, 60) }));
// Lists made with line breaks and bullet characters instead of <ul>/<ol> (1.3.1).
const BULLET = /^\s*(?:[•·▪◦●‣\-–—*]|\d{1,3}[.)])\s+\S/;
const bulletLines = (el) => (el.innerText || '').split('\n').filter((l) => BULLET.test(l)).length;
const fakeLists = [...document.querySelectorAll('p, div, td, dd, span')].filter((el) => isVisible(el) && el.querySelector('br')
  && !el.closest('li, ul, ol, pre') && !el.querySelector('p, div, td, ul, ol') && bulletLines(el) >= 3)
  .slice(0, 10).map((el) => ({ selector: cssPath(el), lines: bulletLines(el) }));
// Preformatted text that uses runs of spaces to lay out columns (F33/F34, 1.3.1).
const preColumns = [...document.querySelectorAll('pre')].filter((el) => isVisible(el) && !el.querySelector('code')
  && (el.textContent || '').split('\n').filter((l) => /\S {3,}\S/.test(l)).length >= 3).slice(0, 5).map(cssPath);
// Links inside running text with no cue other than colour (no underline, border, weight, style or background change) (F73, 1.4.1).
const textLinks = [...document.querySelectorAll('p a[href], li a[href], td a[href], dd a[href], blockquote a[href]')].filter((a) => {
  if (!isVisible(a) || !text(a) || a.querySelector('img, svg')) return false;
  const parent = a.parentElement;
  const around = [...parent.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ');
  if (words(around) < 2) return false;
  const sa = getComputedStyle(a), sp = getComputedStyle(parent);
  return !(/underline|overline|line-through/.test(sa.textDecorationLine) || (parseFloat(sa.borderBottomWidth) > 0 && sa.borderBottomStyle !== 'none')
    || sa.fontWeight !== sp.fontWeight || sa.fontStyle !== sp.fontStyle || sa.backgroundColor !== sp.backgroundColor
    || (parseFloat(sa.outlineWidth) > 0 && sa.outlineStyle !== 'none'));
}).slice(0, 15).map((a) => ({ selector: cssPath(a), text: text(a).slice(0, 60), link_color: rgbaText(getComputedStyle(a).color),
  text_color: rgbaText(getComputedStyle(a.parentElement).color) }));
return {
  tables, spaced_letters: spacedLetters, fake_lists: fakeLists, pre_columns: preColumns, undecorated_text_links: textLinks,
  ungrouped_radios: ungroupedRadios,
  title: document.title,
  page_lang: document.documentElement.getAttribute('lang'),
  main_text: text(main).slice(0, 30000),
  body_text: text(document.body).slice(0, 30000),
  word_count: words(text(main)),
  links, images, background_images: bgImages, fake_headings: fakeHeadings, paragraphs, passages,
  heading_count: headings,
  landmarks: { main: !!document.querySelector('main, [role="main"]'), nav: !!document.querySelector('nav, [role="navigation"]'),
    banner: !!document.querySelector('header, [role="banner"]'), contentinfo: !!document.querySelector('footer, [role="contentinfo"]') },
  abbr_elements: document.querySelectorAll('abbr[title]').length,
  dfn_elements: document.querySelectorAll('dfn').length,
  ruby_elements: document.querySelectorAll('ruby').length,
  breadcrumb: !!document.querySelector('[aria-label*="breadcrumb" i], .breadcrumb, [class*="breadcrumb" i], nav ol'),
  aria_current: [...document.querySelectorAll('[aria-current]')].map((el) => text(el).slice(0, 40)).slice(0, 5),
  carousels: all.filter((el) => isVisible(el) && (/(carousel|slider|slideshow|swiper|marquee)/i.test(el.className && el.className.baseVal === undefined ? el.className : '') || /carousel|slide/i.test(el.getAttribute('aria-roledescription') || ''))).slice(0, 8).map(cssPath),
  sliders: [...document.querySelectorAll('[role="slider"], input[type="range"]')].filter(isVisible).slice(0, 6).map(cssPath),
  draggables: [...document.querySelectorAll('[draggable="true"]')].filter(isVisible).slice(0, 10).map(cssPath),
  live_regions: [...document.querySelectorAll('[aria-live], [role="status"], [role="alert"], [role="log"], [role="timer"], [role="marquee"]')].map((el) => ({ selector: cssPath(el), politeness: el.getAttribute('aria-live') || el.getAttribute('role') })).slice(0, 20),
  open_dialogs: [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], dialog[open], [aria-modal="true"]')].filter(isVisible).map(cssPath),
  meta_refresh: document.querySelector('meta[http-equiv="refresh" i]')?.getAttribute('content') || null,
  password_fields: [...document.querySelectorAll('input[type="password"]')].map((el) => ({ selector: cssPath(el), autocomplete: el.getAttribute('autocomplete'),
    // A paste handler that cancels the event (onpaste="return false" or a script listener) blocks pasting.
    blocks_paste: !el.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true })) })),
  captcha: !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, [class*="captcha" i], img[alt*="captcha" i]'),
  skip_link: (() => { const a = [...document.querySelectorAll('a[href^="#"]')].slice(0, 5).find((x) => /skip|main content|jump to/i.test(text(x)));
    return a ? { selector: cssPath(a), text: text(a), target: a.getAttribute('href') } : null; })(),
  css_reorder: all.filter((el) => { const s = getComputedStyle(el); return isVisible(el) && (s.order !== '0' || /reverse/.test(s.flexDirection) || /dense/.test(s.gridAutoFlow)); }).slice(0, 10).map(cssPath),
};
