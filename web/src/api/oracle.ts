/**
 * The run-time oracle's counter (D419): how many bodies the decoder was given since the page
 * loaded, which the end-to-end matrix compares with the responses the page received. It is the
 * end-to-end harness's alone (`src/harness/`): the index does not export it, the lint lets no
 * other module import this one, and the build's gate (`plugins/gate.ts`) holds that nothing but
 * the harness imports it, so a production build holds it nowhere.
 */
export { decodedCount } from "./decode";
