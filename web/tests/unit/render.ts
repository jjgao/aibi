import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { vi } from "vitest";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** A container in the document, React's root on it, and a way to wait for its words. */
export interface Rendered {
  readonly container: HTMLElement;
  readonly root: Root;
  words(): string;
  until(predicate: (words: string) => boolean): Promise<string>;
  unmount(): void;
}

/** The element's words: its text nodes, each trimmed, joined by one space. */
export function words(element: Element): string {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const found: string[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    const text = (node.nodeValue ?? "").replace(/\s+/gu, " ").trim();
    if (text !== "") {
      found.push(text);
    }
  }
  return found.join(" ");
}

export async function render(node: ReactNode, container?: HTMLElement): Promise<Rendered> {
  const host = container ?? document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  await act(async () => {
    root.render(node);
    await Promise.resolve();
  });
  return {
    container: host,
    root,
    words: () => words(host),
    async until(predicate) {
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const found = words(host);
        if (predicate(found)) {
          return found;
        }
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 5));
        });
      }
      throw new Error(`the words never matched; they are: ${words(host)}`);
    },
    unmount() {
      act(() => {
        root.unmount();
      });
      host.remove();
    },
  };
}

/** Stand-ins for `Response` and `Request` whose body readers are still configurable: the seal is
 * for good, and a copy of the client (a module reloaded for a test) knows only the readers it
 * installed itself (D419), so each test that loads an entry or the client afresh seals prototypes
 * of its own. `vi.unstubAllGlobals()` puts the real ones back. */
export function freshReaders(): void {
  for (const name of ["Response", "Request"]) {
    class Stand {
      json(): unknown {
        return undefined;
      }
      text(): unknown {
        return undefined;
      }
      arrayBuffer(): unknown {
        return undefined;
      }
      blob(): unknown {
        return undefined;
      }
      bytes(): unknown {
        return undefined;
      }
      formData(): unknown {
        return undefined;
      }
    }
    vi.stubGlobal(name, Stand);
  }
}
