/** The catalogue's error element: fixed words alone, never the error or its message, which
 * may hold text from data (§14, A6). */
export function Failure() {
  return (
    <main>
      <h1>aibi</h1>
      <p>This screen failed to load. Reload the page to try again.</p>
    </main>
  );
}
