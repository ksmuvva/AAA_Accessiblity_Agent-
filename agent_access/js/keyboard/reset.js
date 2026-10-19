(start) => {
  document.activeElement && document.activeElement.blur();
  if (start) {
    // Blurring leaves Chromium's Tab starting point where focus was (a clicked button, in a labelled state), and Tab
    // would go on from there. Start from a throwaway stop first in the page (or in an open modal dialog: the rest is
    // inert) and first in tab order (tabindex 1), so Tab goes where it goes on a fresh load, positive tabindex
    // included. focus_stop removes it after the first Tab.
    const host = document.querySelector('dialog:modal') || document.body;
    if (host) {
      const first = document.createElement('span');
      first.id = '__a11yTabStart';
      first.tabIndex = 1;
      host.prepend(first);
      first.focus({ preventScroll: true });
      first.blur();
    }
  }
  window.scrollTo(0, 0);
}
