() => {
  const text = (document.body.innerText || '').replace(/\s+/g, ' ').trim();
  // A CSS orientation lock turns the page 90 degrees in one orientation (a transform on html, body or a top block).
  const angle = (el) => {
    const m = getComputedStyle(el).transform.match(/^matrix\(([^,]+),\s*([^,]+)/);
    return m ? Math.round(Math.abs(Math.atan2(+m[2], +m[1]) * 180 / Math.PI)) : 0;
  };
  return {
    text_length: text.length,
    rotate_message: /(rotate|turn) (your|the) (device|phone|screen)|(landscape|portrait) (mode|orientation) (only|required)/i.test(text),
    rotation: Math.max(0, ...[document.documentElement, document.body, ...document.body.children].map(angle)),
  };
}
