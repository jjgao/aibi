/** The operator entry's screen (D412, D423): the token's shell, in fixed words. Its screens are
 * M5.5's; the client it drives is `src/api/operator.ts`'s. */
import { Shell } from "./Shell";

export function Operator() {
  return (
    <main>
      <h1>aibi operator</h1>
      <Shell />
      <p>The operator screens are not built yet.</p>
      <p>
        <a href="/" rel="noopener noreferrer">
          The catalogue
        </a>
      </p>
    </main>
  );
}
