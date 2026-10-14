() => {
  const running = document.getAnimations ? document.getAnimations().filter((a) => a.playState === 'running').length : 0;
  let reducedRules = 0;
  for (const sheet of [...document.styleSheets]) {
    let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const r of rules || []) if (r.conditionText && r.conditionText.includes('prefers-reduced-motion')) reducedRules++;
  }
  return { running, reduced_motion_rules: reducedRules };
}
