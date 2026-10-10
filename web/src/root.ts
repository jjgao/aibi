/** The element both entries render into: the `<div id="root">` of the document the server
 * writes (D411). */
export function rootElement(): HTMLElement {
  const found = document.getElementById("root");
  if (found === null) {
    throw new Error("The document has no root element");
  }
  return found;
}
