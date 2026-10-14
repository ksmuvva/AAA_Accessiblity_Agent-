async (tags) => {
  const r = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations', 'incomplete'] });
  return { violations: r.violations, incomplete: r.incomplete, passes: r.passes, version: axe.version };
}
