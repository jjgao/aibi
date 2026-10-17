/**
 * The catalogue (`#/`; D431, D311; D427's R50, R52, R53a and R54): the latest published release of
 * each dataset, as `search_catalog` gives it, the first page (`{ limit: 50 }`, D311's page and the
 * schema's maximum; nothing else is sent). One call a load, through the tab's one queue
 * (`load.ts`). `Catalogue` is the loader, `Hits` the view of an answer (the position the injection
 * sweeps render). Every text of the answer reaches the DOM through `Text`, `Ref`, `List` and the
 * shared components alone; every number through `Num`; the page computes no count.
 *
 * Each hit: its label, linked to the dataset's screen by `Link` (a `DatasetId` from `idOf`, never a
 * text); its dataset id; its release (`ReleaseRef`) and status in words; its effective disclosure;
 * its name and description (omitted when absent, as D311); its domain tags and facets' values
 * (D312's lists, `List`); its data-use terms, packs and concepts; its tables (`ServerList`, with the
 * server's `tables_left_out`), each with its id, label, grain, role and row count (`StatCount`).
 * The answer's caveats, its total, and where the next page starts are shown. The fixed link to the
 * operator entry (`/curate`) stands in every state.
 */
import "./screens.css";

import { Fragment, type ReactElement, useState, useSyncExternalStore } from "react";

import { type Answered, entriesOf, idOf, isZero, kindOf, Num, type Outcome, searchCatalog, SHAPE_MISMATCH, UNREACHABLE } from "../api";
import { Caveats } from "../layout/Caveat";
import { Disclosure } from "../layout/Disclosure";
import { keyOf } from "../layout/key";
import { Link } from "../layout/Link";
import { List, ServerList } from "../layout/List";
import { Refusals } from "../layout/Refusal";
import { ReleaseRef } from "../layout/ReleaseRef";
import { StatCount } from "../layout/StatCount";
import { Ref, Text } from "../layout/Text";
import { DEADLINE, loader } from "./load";

/** The words while the catalogue loads: fixed. */
export const PENDING = "Loading the catalogue…";

/** R54's sentence: which release is listed, and what the descriptors' text is. Fixed. */
export const LATEST = "The latest published release of each dataset is listed; text in the descriptors comes from the datasets and is shown as it is.";

/** The words of a catalogue with no hit: fixed. */
export const EMPTY = "No dataset has a published release yet.";

/** The words of a facet with no value: fixed. */
export const NO_VALUE = "No value.";

/** The words of a hit that has no table (and none left out by the server): fixed. */
export const NO_TABLE = "No table.";

/** The words over a refused catalogue: fixed. */
export const REFUSED = "The catalogue could not be listed.";

/** The words of a load that threw: fixed, never the error's message. */
export const FAILED = "This screen failed. Return to the catalogue or reload the page.";

/** The words of a hit whose dataset is not of the dataset grammar (the schema rules it out): fixed. */
export const NOT_LINKED = "(not linked: its dataset is not an id this page reads)";

/** The words after a member of a vocabulary the page does not know: fixed. */
export const UNKNOWN = "(not known to this page)";

/** The words of an absent grain or role: fixed. */
export const UNDECLARED = "undeclared";

/** The words of each release status. */
const STATUS = { published: "published", draft: "draft" } as const;

/** The words of each table role. */
const ROLE = { entity: "entity", link: "link", measurement: "measurement", event: "event", coverage: "coverage" } as const;

type Answer = Answered<"search_catalog">;
type Hit = Answer["hits"][number];
type Table = Hit["tables"][number];

/** A hit's table: its id, label, grain, role and row count. */
function TableLine({ table }: { readonly table: Table }): ReactElement {
  const role = table.role === undefined ? null : kindOf(table.role, "TableSummary.role");
  return (
    <span className="table">
      <Ref value={table.id} /> <Text value={table.label.data} />, grain {table.grain === undefined ? UNDECLARED : <Text value={table.grain.data} />}, role{" "}
      {table.role === undefined ? (
        UNDECLARED
      ) : role === null ? (
        <>
          <Text value={table.role} /> {UNKNOWN}
        </>
      ) : (
        ROLE[role]
      )}
      , rows <StatCount value={table.rows} />
    </span>
  );
}

/** One hit of the catalogue. */
function OneHit({ hit }: { readonly hit: Hit }): ReactElement {
  const id = idOf(hit.dataset);
  const status = kindOf(hit.release.status, "ReleaseOut.status");
  return (
    <section className="hit">
      <h2>
        {id === null ? (
          <>
            <Text value={hit.label.data} /> {NOT_LINKED}
          </>
        ) : (
          <Link to={id}>
            <Text value={hit.label.data} />
          </Link>
        )}
      </h2>
      <p className="meta">
        Dataset <Ref value={hit.dataset} />, release <ReleaseRef value={hit.release} />, status{" "}
        {status === null ? (
          <>
            <Text value={hit.release.status} /> {UNKNOWN}
          </>
        ) : (
          STATUS[status]
        )}
      </p>
      <Disclosure value={hit.disclosure} />
      {hit.name === undefined ? null : (
        <p>
          <Text value={hit.name.data} />
        </p>
      )}
      {hit.description === undefined ? null : (
        <p>
          <Text value={hit.description.data} />
        </p>
      )}
      {hit.domain_tags.length === 0 ? null : (
        <p>
          Domain tags: <List hit={hit} at="domain_tags" />
        </p>
      )}
      {hit.data_use.length === 0 ? null : (
        <p>
          Data use:{" "}
          {hit.data_use.map((term, index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <Text value={term.label.data} /> (<Text value={term.system.data} />:<Text value={term.code.data} />)
            </Fragment>
          ))}
        </p>
      )}
      {hit.packs.length === 0 ? null : (
        <p>
          Packs:{" "}
          {hit.packs.map((pack, index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <Ref value={pack} />
            </Fragment>
          ))}
        </p>
      )}
      {hit.concepts.length === 0 ? null : (
        <p>
          Concepts:{" "}
          {hit.concepts.map((concept, index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <Ref value={concept} />
            </Fragment>
          ))}
        </p>
      )}
      {entriesOf(hit.facets).map(([facet, values], index) => (
        <p key={keyOf(index)}>
          Facet <Ref value={facet} />: {values.length === 0 ? NO_VALUE : <List hit={hit} at={{ facet }} />}
        </p>
      ))}
      <div className="tables">
        Tables:{" "}
        {hit.tables.length === 0 && isZero(hit.tables_left_out) ? (
          NO_TABLE
        ) : (
          <ServerList members={hit.tables} leftOut={hit.tables_left_out} each={(table) => <TableLine table={table} />} />
        )}
      </div>
    </section>
  );
}

/** An answer of `search_catalog`: R54's sentence, the total, the caveats, every hit, and where the
 * next page starts. */
export function Hits({ answer }: { readonly answer: Answer }): ReactElement {
  return (
    <>
      <p className="meta">{LATEST}</p>
      <p>
        Datasets: <Num value={answer.total} />
      </p>
      <Caveats caveats={answer.caveats} />
      {answer.hits.length === 0 ? <p className="state">{EMPTY}</p> : null}
      {answer.hits.map((hit, index) => (
        <OneHit key={keyOf(index)} hit={hit} />
      ))}
      {answer.next_offset === undefined ? null : (
        <p>
          More datasets follow from position <Num value={answer.next_offset} />; this page shows the first page.
        </p>
      )}
    </>
  );
}

/** What a load of the catalogue shows, in each state. */
function State({ outcome }: { readonly outcome: Outcome<Answer> }): ReactElement {
  if (outcome.kind === "refused") {
    return (
      <>
        <p className="state">{REFUSED}</p>
        <Refusals refused={outcome} />
      </>
    );
  }
  if (outcome.kind === "invalid") {
    return <p className="state">{outcome.cause === "shape" ? SHAPE_MISMATCH : UNREACHABLE}</p>;
  }
  return <Hits answer={outcome.value} />;
}

/** The catalogue's screen: one `search_catalog` a mount, its outcome in each state, and the fixed
 * link to the operator entry in every state. */
export function Catalogue(): ReactElement {
  const [store] = useState(() => loader(() => searchCatalog({ limit: 50 })));
  const loaded = useSyncExternalStore(store.subscribe, store.snapshot);
  return (
    <main className="catalogue">
      <h1>Catalogue</h1>
      {loaded.kind === "pending" ? (
        <p className="state">{PENDING}</p>
      ) : loaded.kind === "failed" ? (
        <p className="state">{FAILED}</p>
      ) : loaded.kind === "deadline" ? (
        <p className="state">{DEADLINE}</p>
      ) : (
        <State outcome={loaded.value} />
      )}
      <p>
        {/* The fixed path, never composed from the URL or from data (D412). */}
        <a href="/curate" rel="noopener noreferrer">
          Curate
        </a>
      </p>
    </main>
  );
}
