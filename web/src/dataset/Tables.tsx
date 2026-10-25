/**
 * The tables of a dataset (D431, D440; D427's R66a; §5.3, §5.4, D311): each headed `Table <id>
 * (<label>)`, its id (grammar-bound) through `Ref` and its label (data, shown as sent even when it
 * equals the id) through `Text`, then its row count with its `stat:` reference (`StatCount`; the
 * reference, `stat:<manifest>/<table>/n_rows`, is never cut, and a suppressed count is never 0), and its
 * columns, each with its id (`<table>.<column>`), its label, its datatype in words (absent:
 * undeclared) and `identifier` when it is one (a column not shown as one says nothing: the flag false
 * may be undeclared). A table's grain and role and the coverage and endpoint descriptors are
 * descriptor members (R66b, M5.2-rest): not shown here. A table with no column on this page says so
 * (`NO_COLUMN`): the columns' paging may hold them on another.
 */
import type { ReactElement } from "react";

import { type Answered, kindOf } from "../api";
import { keyOf } from "../layout/key";
import { StatCount } from "../layout/StatCount";
import { Ref, Text } from "../layout/Text";

/** The words of a dataset with no table: fixed. */
export const NO_TABLES = "No tables.";

/** The words of a table with no column on this page: fixed (the columns' paging may hold them on
 * another, so the words never say the table has none). */
export const NO_COLUMN = "No column on this page.";

/** The words of an absent datatype: fixed. */
export const UNDECLARED = "datatype undeclared";

/** The words after a datatype the page does not know: fixed. */
export const UNKNOWN_DATATYPE = "(a datatype not known to this page)";

/** The words of a column that is an identifier: fixed. A column that is not shown as one says nothing
 * of it: the descriptor may not have declared it, and D311 shows the positive alone. */
export const IDENTIFIER = "identifier";

/** The words of each datatype. */
const DATATYPE = {
  number: "number",
  integer: "integer",
  string: "string",
  boolean: "boolean",
  category: "category",
  "list<category>": "list of categories",
  date: "date",
  datetime: "date and time",
  time_offset: "time offset",
} as const;

/** The tables of an answer of `describe_dataset`. */
export type TablesOut = Answered<"describe_dataset">["tables"];

type Column = TablesOut[number]["columns"][number];

/** A column: its id, label, datatype and, when it is one, `identifier`. */
function ColumnLine({ column }: { readonly column: Column }): ReactElement {
  const datatype = column.datatype === undefined ? null : kindOf(column.datatype, "ColumnBrief.datatype");
  return (
    <p role="listitem">
      <Ref value={column.id} /> <Text value={column.label.data} />,{" "}
      {column.datatype === undefined ? (
        UNDECLARED
      ) : datatype === null ? (
        <>
          <Text value={column.datatype} /> {UNKNOWN_DATATYPE}
        </>
      ) : (
        DATATYPE[datatype]
      )}
      {column.identifier ? `, ${IDENTIFIER}` : null}
    </p>
  );
}

/** Every table: headed by its id and label, then its row count with its reference, then its columns. */
export function Tables({ tables }: { readonly tables: TablesOut }): ReactElement {
  return (
    <section className="tables">
      <h2>Tables</h2>
      {tables.length === 0 ? <p className="state">{NO_TABLES}</p> : null}
      {tables.map((table, index) => (
        <section key={keyOf(index)} className="table">
          <h3>
            Table <Ref value={table.id} /> (<Text value={table.label.data} />)
          </h3>
          <p>
            Rows: <StatCount value={table.rows} />
          </p>
          {table.columns.length === 0 ? (
            <p className="state">{NO_COLUMN}</p>
          ) : (
            <div role="list" aria-label="columns">
              {table.columns.map((column, at) => (
                <ColumnLine key={keyOf(at)} column={column} />
              ))}
            </div>
          )}
        </section>
      ))}
    </section>
  );
}
