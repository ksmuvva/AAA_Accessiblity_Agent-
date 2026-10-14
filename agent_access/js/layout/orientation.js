() => {
  const text = (document.body.innerText || '').replace(/\s+/g, ' ').trim();
  return { text_length: text.length, rotate_message: /(rotate|turn) (your|the) (device|phone|screen)|(landscape|portrait) (mode|orientation) (only|required)/i.test(text) };
}
