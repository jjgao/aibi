/**
 * The table graph (D431; D427's R65a; §6.1, D311), as a list: the tables of the graph (the server
 * leaves coverage tables out of it, and the heading says so in fixed words) and each relationship
 * with its child and parent tables and its cardinality in words. Its child and parent columns are a
 * descriptor's members (R65b, M5.2-rest): not shown here.
 */
import { Fragment, type ReactElement } from "react";

import { type Answered, kindOf } from "../api";
import { keyOf } from "../layout/key";
import { Ref, Text } from "../layout/Text";

/** The heading of the graph: fixed, and says what the graph leaves out. */
export const GRAPH_HEADING = "Table graph (coverage tables are not in it; they are listed under Tables)";

/** The words of a graph with no table and of one with no relationship: fixed (an empty list of the
 * server is said, never shown as blank). */
export const NO_TABLE = "No table.";
export const NO_RELATIONSHIP = "No relationship.";

/** The words after a cardinality the page does not know: fixed. */
export const UNKNOWN_CARDINALITY = "(a cardinality not known to this page)";

/** The words of each cardinality. */
const CARDINALITY = { "many-to-one": "many to one", "one-to-one": "one to one" } as const;

/** The table graph of an answer of `describe_dataset`. */
export type TableGraph = Answered<"describe_dataset">["graph"];

/** The table graph: its tables, then each relationship, child to parent, with its cardinality. */
export function Graph({ graph }: { readonly graph: TableGraph }): ReactElement {
  return (
    <section className="graph">
      <h2>{GRAPH_HEADING}</h2>
      <p>
        Tables:{" "}
        {graph.tables.length === 0
          ? NO_TABLE
          : graph.tables.map((table, index) => (
              <Fragment key={keyOf(index)}>
                {index === 0 ? null : ", "}
                <Ref value={table} />
              </Fragment>
            ))}
      </p>
      {graph.edges.length === 0 ? (
        <p className="state">{NO_RELATIONSHIP}</p>
      ) : (
        <div role="list" aria-label="relationships">
          {graph.edges.map((edge, index) => {
            const cardinality = kindOf(edge.cardinality, "GraphEdge.cardinality");
            return (
              <p role="listitem" key={keyOf(index)}>
                Relationship <Ref value={edge.relationship} />: <Ref value={edge.child} /> to <Ref value={edge.parent} />,{" "}
                {cardinality === null ? (
                  <>
                    <Text value={edge.cardinality} /> {UNKNOWN_CARDINALITY}
                  </>
                ) : (
                  CARDINALITY[cardinality]
                )}
              </p>
            );
          })}
        </div>
      )}
    </section>
  );
}
