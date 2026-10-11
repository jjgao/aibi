/**
 * Another site's pages, for the fetch-metadata matrix: a link, a page that goes back in its own
 * tab, a frame and a window it opens. Its own pages hold inline code: they are the attacker's.
 */
import http from "node:http";

function attribute(value: string): string {
  return value.replace(/&/gu, "&amp;").replace(/"/gu, "&quot;").replace(/</gu, "&lt;").replace(/>/gu, "&gt;");
}

function script(value: string): string {
  return JSON.stringify(value).replace(/</gu, "\\u003c");
}

export function page(path: string, to: string): string {
  switch (path) {
    case "/link":
      return `<a id="go" href="${attribute(to)}">go</a>`;
    case "/back":
      return '<p id="other">other</p><script>setTimeout(() => history.back(), 200)</script>';
    case "/frame":
      return `<iframe id="frame" src="${attribute(to)}"></iframe>`;
    case "/open":
      return `<button id="open">open</button><script>document.getElementById("open").addEventListener("click", () => window.open(${script(to)}))</script>`;
    default:
      return '<p id="other">other</p>';
  }
}

export function otherOrigin(): http.Server {
  return http.createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://other.invalid");
    response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
    response.end(`<!doctype html><title>other</title>${page(url.pathname, url.searchParams.get("to") ?? "")}`);
  });
}
