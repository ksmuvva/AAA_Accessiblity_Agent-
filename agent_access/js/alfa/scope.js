// What Alfa would take in (Native.fromNode follows open shadow roots and same-origin frames): its elements, and the
// stylesheets it can't read (another origin's, loaded without CORS), which it would treat as empty.
() => {
  let elements = 0, unreadable = 0;
  const walk = (root) => {
    for (const sheet of root.styleSheets || []) {
      try { sheet.cssRules; } catch (e) { unreadable++; }
    }
    for (const el of root.querySelectorAll('*')) {
      elements++;
      if (el.shadowRoot) walk(el.shadowRoot);
      if (el.tagName === 'IFRAME') {
        try { if (el.contentDocument) walk(el.contentDocument); } catch (e) { /* another origin's frame: Alfa skips it too */ }
      }
    }
  };
  walk(document);
  return { elements, unreadable };
}
