// Resolves once the page has settled after an action: no DOM change for `quiet` ms, no CSS transition running and no
// pending short timer (a tooltip shown after a delay), or after `cap` ms on a page that never settles. Returns the ms.
async ([quiet, cap]) => {
  const start = performance.now();
  let last = start;
  const watch = new MutationObserver(() => { last = performance.now(); });
  watch.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));   // not setTimeout: it is counted
  try {
    while (performance.now() - start < cap) {
      await frame();
      const busy = (window.__a11yInstr?.short?.size || 0) > 0
        || document.getAnimations().some((a) => a instanceof CSSTransition && a.playState === 'running');
      if (!busy && performance.now() - last >= quiet) break;
    }
  } finally {
    watch.disconnect();
  }
  return Math.round(performance.now() - start);
}
