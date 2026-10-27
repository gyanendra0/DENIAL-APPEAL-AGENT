// Turns an error into one sentence for the screen. A 422 names the fields the API refused, never
// what was typed into them, and a 422 with a fixed sentence from the API shows that sentence. Any
// other answer uses its kind, unless the caller gives its own line for the status.

import { ApiError, INVALID_REQUEST } from "../api/client";
import type { FieldProblem } from "../api/client";

const MESSAGE_BY_KIND: Readonly<Record<ApiError["kind"], string>> = {
  unauthorized: "You are not logged in.",
  forbidden: "Your role cannot do this.",
  not_found: "Not found.",
  conflict: "This was changed by someone else.",
  length_required: "The upload had no size; try again.",
  too_large: "The file is too large.",
  invalid: "Some fields are not valid.",
  too_many: "Too many tries. Try again in a few minutes.",
  server: "Something went wrong on the server. Try again later.",
  network: "The API could not be reached. Check your connection and try again.",
};

// One of the API's fixed sentences, as the screen shows it: "the email is taken" becomes
// "The email is taken.". Only for answers whose text the API wrote, never what the user typed.
export function asSentence(message: string): string {
  return `${message.charAt(0).toUpperCase()}${message.slice(1)}.`;
}

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
    if (names.length > 0) {
      return `Check these fields: ${names.join(", ")}.`;
    }
    if (error.message !== INVALID_REQUEST) {
      return asSentence(error.message);
    }
    return MESSAGE_BY_KIND.invalid;
  }
  return MESSAGE_BY_KIND[error.kind];
}
