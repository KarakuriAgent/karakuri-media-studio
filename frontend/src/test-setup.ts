/** vitest 共通セットアップ: jsdom に無い DOM API を最小限だけ補う。 */
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
