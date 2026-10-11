import "./not-found.css";

/** The catalogue's not-found screen: fixed words. It is loaded lazily, with its stylesheet, so
 * that the shipped build itself has a lazy chunk and a stylesheet it loads (the policy matrix
 * loads both under the documents' policy). */
export function NotFound() {
  return (
    <main className="not-found">
      <h1>aibi</h1>
      <p>Nothing is here.</p>
      <p>
        <a href="#/">The catalogue</a>
      </p>
    </main>
  );
}
