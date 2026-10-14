() => {
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120);
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')]
    .slice(0, 60)
    .map((h) => ({
      level: h.getAttribute('aria-level') || h.tagName.replace(/^H/, '') || null,
      text: text(h),
    }));
  const landmarkSelectors = {
    banner: 'header, [role="banner"]',
    navigation: 'nav, [role="navigation"]',
    main: 'main, [role="main"]',
    contentinfo: 'footer, [role="contentinfo"]',
    complementary: 'aside, [role="complementary"]',
    search: 'search, [role="search"]',
    form: 'form[aria-label], form[aria-labelledby], [role="form"]',
  };
  const landmarks = {};
  for (const [name, sel] of Object.entries(landmarkSelectors)) {
    landmarks[name] = document.querySelectorAll(sel).length;
  }
  const imgs = [...document.querySelectorAll('img')];
  // Only outermost <svg> elements; nested <svg> belong to the same graphic.
  const svgs = [...document.querySelectorAll('svg')].filter((s) => !s.parentElement?.closest('svg'));
  // Passages that declare their own language (3.1.2). The accessibility tree does not show these.
  const pageLang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
  const languageParts = [...document.body.querySelectorAll('[lang]')]
    .filter((el) => el.getAttribute('lang').toLowerCase() !== pageLang && text(el))
    .slice(0, 30)
    .map((el) => ({ tag: el.tagName.toLowerCase(), lang: el.getAttribute('lang'), text: text(el).slice(0, 60) }));
  return {
    title: document.title,
    lang: document.documentElement.getAttribute('lang'),
    language_parts: languageParts,
    headings,
    landmarks,
    counts: {
      images: imgs.length,
      images_without_alt_attribute: imgs.filter((i) => !i.hasAttribute('alt')).length,
      svgs: svgs.length,
      // Exposed to assistive tech (not aria-hidden) but with no accessible name: likely 1.1.1 issues.
      svgs_exposed_without_name: svgs.filter((s) => !s.closest('[aria-hidden="true"]')
        && !s.getAttribute('aria-label') && !s.getAttribute('aria-labelledby') && !s.querySelector('title')).length,
      role_img_elements: document.querySelectorAll('[role="img"]').length,
      links: document.querySelectorAll('a[href]').length,
      buttons: document.querySelectorAll('button, [role="button"]').length,
      form_controls: document.querySelectorAll('input:not([type="hidden"]), select, textarea').length,
      iframes: document.querySelectorAll('iframe').length,
      videos: document.querySelectorAll('video').length,
      audio: document.querySelectorAll('audio').length,
    },
    skip_link_present: [...document.querySelectorAll('a[href^="#"]')]
      .slice(0, 5)
      .some((a) => /skip|main content/i.test(text(a))),
  };
}
