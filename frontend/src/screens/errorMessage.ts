// Turns an error into one sentence for the screen. A 422 names the fields the API refused, never
// what was typed into them; any other API answer uses its kind, or its `detail` when given.

import { ApiError } from "../api/client";
import type { FieldProblem } from "../api/client";

const MESSAGE_BY_KIND: Readonly<Record<ApiError["kind"], string>> = {
  unauthorized: "You are not logged in.",
  forbidden: "Your role cannot do this.",
  not_found: "Not found.",
  conflict: "This was changed by someone else.",
  length_required: "The upload had no size; try again.",
  too_large: "The file is too large.",
  invalid: "Some fields are not valid.",
  server: "Something went wrong on the server. Try again later.",
  network: "The API could not be reached. Check your connection and try again.",
};

// The name of the field a 422 problem points at: the last text part of its `loc`.
function fieldName(problem: FieldProblem): string | null {
  const last = problem.loc.at(-1);
  return typeof last === "string" && problem.loc.length > 1 ? last.replaceAll("_", " ") : null;
}

export function describeError(error: unknown, messages: Partial<Record<number, string>> = {}) {
  if (!(error instanceof ApiError)) {
    return MESSAGE_BY_KIND.server;
  }
  const own = error.status === null ? undefined : messages[error.status];
  if (own !== undefined) {
    return own;
  }
  if (error.kind === "invalid") {
    const names = [...new Set(error.problems.map(fieldName).filter((name) => name !== null))];
    return names.length > 0 ? `Check these fields: ${names.join(", ")}.` : MESSAGE_BY_KIND.invalid;
  }
  return MESSAGE_BY_KIND[error.kind];
}
