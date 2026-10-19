// @body
// Siteimprove Alfa's failed outcomes (vendor/alfa_entry.mjs), each with a CSS path for its target: Alfa gives an XPath,
// and a text node's element is its parent.
const at = (path) => {
  try {
    const n = document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    const el = n && (n.nodeType === 1 ? n : n.parentElement);
    return el ? cssPath(el) : null;
  } catch (e) {
    return null;   // a path into a shadow root or a frame: the outcome still counts, without a selector
  }
};
return window.__alfa.audit().then((outcomes) => outcomes.map((o) => ({ ...o, selector: o.target ? at(o.target) : null })));
