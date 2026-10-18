/**
 * The residuals of `showText` (D428, D419's boundary: deliberate code, not an accident), each with
 * the probe that shows it open or closed:
 *
 * - narrowing the element's `props` by hand gives the box back, never its text (closed: the box
 *   throws on every conversion);
 * - a detached `createRoot`, a timer and the host's `textContent` read the visible form's text
 *   (open here, in a test: closed in the app outside the entries by D429, which allows
 *   `react-dom/client` in the entries alone); what it reads is the visible form, escapes and all, never
 *   the raw text;
 * - `react-dom/server` reads the same (open here, in a test: closed in the app by D429's specifier
 *   allow-list, `tests/gates/display-lint.test.ts`);
 * - calling the element's `type` inside a render (`Reflect.apply(type, undefined, [props])`) runs
 *   the door's own component and gives back the visible form's strings (open: deliberate code, which
 *   the app's lint refuses, `Reflect` being a sink name; round 1 of #117's probe 2).
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";

import { boxText, ServerText } from "../../src/api/box";
import { showText } from "../../src/api/text";
import { render } from "./render";

const RAW = "secret\u202elabel";
const VISIBLE = "secret\\u202elabel";

describe("what deliberate code recovers from showText's element", () => {
  it("narrowing its props gives the box back, never the text (closed)", () => {
    const box = boxText(RAW);
    const { props } = showText(box);
    const held = typeof props === "object" && props !== null && "text" in props ? props.text : null;
    expect(held).toBe(box);
    expect(ServerText.holds(held)).toBe(true);
    expect(() => String(held)).toThrow(TypeError);
    expect(() => JSON.stringify(props)).toThrow(TypeError);
  });

  it("a detached root and a timer read the visible form's text, never the raw text (closed in the app by D429)", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    root.render(showText(boxText(RAW)));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(host.textContent).toBe(VISIBLE);
    root.unmount();
  });

  it("react-dom/server reads the visible form's text, never the raw text (closed in the app by D429)", () => {
    expect(renderToStaticMarkup(showText(boxText(RAW)))).toBe('secret<span class="escape">\\u202e</span>label');
  });

  it("calling the element's type inside a render gives the visible form's strings, never the raw text (open: deliberate code)", async () => {
    const stolen: string[] = [];
    const walk = (node: unknown): void => {
      if (typeof node === "string") {
        stolen.push(node);
      } else if (Array.isArray(node)) {
        node.forEach(walk);
      } else if (typeof node === "object" && node !== null && "props" in node) {
        const props: unknown = node.props;
        if (typeof props === "object" && props !== null && "children" in props) {
          walk(props.children);
        }
      }
    };
    function Thief({ text }: { readonly text: ReturnType<typeof boxText> }): ReactElement {
      const element = showText(text);
      const type: unknown = element.type;
      if (typeof type === "function") {
        walk(Reflect.apply(type, undefined, [element.props]));
      }
      return <p>{stolen.length}</p>;
    }
    const shown = await render(<Thief text={boxText("javascript:alert(1)\u202e")} />);
    expect(stolen).toEqual(["javascript:alert(1)", "\\u202e"]);
    expect(stolen.join("").includes("\u202e")).toBe(false);
    shown.unmount();
  });
});
