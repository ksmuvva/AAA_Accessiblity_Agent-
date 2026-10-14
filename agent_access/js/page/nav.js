// @body
const links = (root) => root ? [...root.querySelectorAll('a[href]')].filter(isVisible)
  .map((a) => ({ text: linkName(a), href: a.href })).filter((l) => l.text).slice(0, 40) : [];
const navs = [...document.querySelectorAll('nav, [role="navigation"]')].filter(isVisible);
const header = document.querySelector('header, [role="banner"]');
const footer = document.querySelector('footer, [role="contentinfo"]');
const all = [...document.querySelectorAll('a[href], button')].filter(isVisible);
const HELP = /\b(help|contact|support|faq|chat|customer service|call us)\b/i;
// Pages without nav landmarks: the first (outermost) block that looks like navigation - a list, table, or
// nav/menu-named element with 3+ links whose text is mostly link text.
const squash = (t) => (t || '').replace(/\s+/g, '').length;
const groups = [...document.querySelectorAll('ul, ol, table, [id*="nav" i], [class*="nav" i], [id*="menu" i], [class*="menu" i]')]
  .filter(isVisible).map((el) => ({ el, links: links(el) }))
  .filter((g) => g.links.length >= 3 && g.links.reduce((n, l) => n + squash(l.text), 0) >= 0.6 * squash(g.el.innerText));
const navGroup = groups.find((g) => !groups.some((o) => o !== g && o.el.contains(g.el)));
// Only the menu's top-level items: a submenu shown under the current page's item would otherwise look like reordering.
const topList = navGroup && (navGroup.el.matches('ul, ol') ? navGroup.el : navGroup.el.querySelector('ul, ol'));
const topLinks = topList ? [...topList.querySelectorAll(':scope > li > a[href]')].filter(isVisible)
  .map((a) => ({ text: linkName(a), href: a.href })).filter((l) => l.text) : [];
return {
  nav_landmarks: navs.map((n) => ({ name: n.getAttribute('aria-label') || '', links: links(n) })),
  header_links: links(header),
  footer_links: links(footer),
  nav_group: topLinks.length >= 3 ? topLinks : navGroup ? navGroup.links : [],
  // Every visible link to another page, nameless ones included, so 3.2.4 can compare repeated links on pages without
  // landmarks. Links to this page (skip links, #anchors) aren't navigation to a destination, so they're left out.
  all_links: [...document.querySelectorAll('a[href]')].filter((a) => isVisible(a) && a.href.split('#')[0] !== location.href.split('#')[0])
    .slice(0, 200).map((a) => ({ text: linkName(a), href: a.href })),
  search: !!document.querySelector('input[type="search"], [role="search"], search, form[action*="search" i], input[name="q"], input[name*="search" i]'),
  sitemap_link: all.some((a) => /site ?map/i.test(label(a))),
  breadcrumb: !!document.querySelector('[aria-label*="breadcrumb" i], .breadcrumb, [class*="breadcrumb" i]'),
  // Help mechanisms in document order, for 3.2.6.
  help: all.filter((a) => HELP.test(label(a))).map((a) => ({ text: label(a), href: a.href || null })).slice(0, 10),
};
