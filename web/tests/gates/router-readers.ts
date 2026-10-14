/**
 * The corpus of readers of the page's address (D428, round 1 of #117): each a function that reads
 * the address in the way an app component could, and the text of a file of `src/` that does so. The
 * unit corpus (`tests/unit/readers.test.tsx`) renders each through the REAL hash router, at an
 * address with a right-to-left override, and holds that it puts the address in the DOM raw; the gate
 * (`router.test.ts`) holds that the app may use none of them. A reader that is in neither is a
 * hole the corpus would name.
 */
import { useContext } from "react";
import { UNSAFE_LocationContext, useHref, useLocation, useMatch, useMatches, useParams, useResolvedPath, useSearchParams } from "react-router";

export interface Reader {
  /** What it is called. */
  readonly label: string;
  /** What it reads, as a hook or a getter, called in a component under `datasets/:id`. */
  readonly read: () => unknown;
  /** A file of `src/` that uses it. */
  readonly source: string;
  /** The address it is loaded at, where it is not `ADDRESS` (a path reader reads the path). */
  readonly address?: string;
}

const router = (name: string): string => `import { ${name} } from "react-router";\nexport const f = ${name};\n`;

/** The address the corpus loads: a right-to-left override in the id and in the query. */
export const ADDRESS = "/#/datasets/%E2%80%AEtxt.exe?q=%E2%80%AEq";

/** The path form of it, which the server serves the bundle at (D410): what a path reader reads. */
const PATH = "/datasets/%E2%80%AEtxt.exe?q=%E2%80%AEq";

export const READERS: readonly Reader[] = [
  { label: "useParams", read: () => useParams()["id"], source: router("useParams") },
  { label: "useLocation", read: () => useLocation().pathname, source: router("useLocation") },
  { label: "useSearchParams", read: () => useSearchParams()[0].get("q"), source: router("useSearchParams") },
  { label: "useMatches", read: () => useMatches().at(-1)?.pathname, source: router("useMatches") },
  { label: "useMatch", read: () => useMatch("/datasets/:id")?.params.id, source: router("useMatch") },
  { label: "useResolvedPath", read: () => useResolvedPath(".").pathname, source: router("useResolvedPath") },
  { label: "useHref", read: () => useHref("."), source: router("useHref") },
  { label: "UNSAFE_LocationContext", read: () => useContext(UNSAFE_LocationContext).location.pathname, source: router("UNSAFE_LocationContext") },
  { label: "location.pathname", read: () => location.pathname, source: "export const f = () => location.pathname;\n", address: PATH },
  { label: "location.hash", read: () => location.hash, source: "export const f = () => location.hash;\n" },
  { label: "window.location.href", read: () => window.location.href, source: "export const f = () => window.location.href;\n" },
  { label: "document.URL", read: () => document.URL, source: "export const f = () => document.URL;\n" },
  { label: "document.documentURI", read: () => document.documentURI, source: "export const f = () => document.documentURI;\n" },
  { label: "document.baseURI", read: () => document.baseURI, source: "export const f = () => document.baseURI;\n" },
];
