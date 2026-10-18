/**
 * Lists from data (D430; D427's R03, D312, §8.4). `List` shows one of **D312's lists** of a catalogue
 * hit (its domain tags, or one facet's values, named by its key): its first `LISTED` members (64, the
 * visible form's export, which the server's page cuts at too), each through `Text`, then how many
 * more (`listOf`, the position-typed door, which gives both from the one list: the page counts only
 * what the server sent, every member of which is disclosed, and only for these lists). `ServerList` shows a list **the server
 * cut** (a hit's `tables`, with `tables_left_out`): every member it sent, then the server's own
 * number of the rest through `Num`, never one the page counted, so no count §8.4 governs is made
 * on the page.
 */
import { Fragment, type ReactElement } from "react";

import { isZero, type Listed, type Lists, listOf, Num, type ServerNumber } from "../api";
import { type Key, keyOf } from "./key";
import { Text } from "./Text";

/** One of D312's lists of a hit: its first `LISTED` members, then how many more (`listOf` finds the
 * one list both come from). */
export function List({ hit, at }: { readonly hit: Lists; readonly at: Listed }): ReactElement {
  const { members, more } = listOf(hit, at);
  return (
    <span role="list" className="list">
      {members.map((member, index) => (
        <Fragment key={keyOf(index)}>
          {index === 0 ? null : ", "}
          <span role="listitem">
            <Text value={member.data} />
          </span>
        </Fragment>
      ))}
      {more === null ? null : <>, {more}</>}
    </span>
  );
}

/** A list the server cut: every member it sent, then the server's number of the rest. */
export function ServerList<T>({ members, each, leftOut }: { readonly members: readonly T[]; readonly each: (member: T, at: Key) => ReactElement; readonly leftOut: ServerNumber }): ReactElement {
  return (
    <span role="list" className="list">
      {members.map((member, index) => (
        <Fragment key={keyOf(index)}>
          {index === 0 ? null : ", "}
          <span role="listitem">{each(member, keyOf(index))}</span>
        </Fragment>
      ))}
      {isZero(leftOut) ? null : (
        <>
          ,{" "}
          <span className="more">
            and <Num value={leftOut} /> more, left out by the server
          </span>
        </>
      )}
    </span>
  );
}
