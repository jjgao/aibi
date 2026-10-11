/** The catalogue entry's home screen: fixed words and the one link to the operator entry. */
export function Home() {
  return (
    <main>
      <h1>aibi</h1>
      <p>The catalogue opens here.</p>
      <p>
        {/* The fixed path, never composed from the URL or from data (D412). */}
        <a href="/curate" rel="noopener noreferrer">
          Curate
        </a>
      </p>
    </main>
  );
}
