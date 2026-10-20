// The one way the screens call the API. The login token is an HttpOnly cookie on the same site,
// so every request sends it (`credentials: "same-origin"`) and no code ever reads it.

import type { LetterProblem, LetterProblemKind } from "./types";

export type ApiErrorKind =
  | "unauthorized" // 401: not logged in, or the session ended
  | "forbidden" // 403: the role cannot do this
  | "not_found" // 404: missing, or another account's row
  | "conflict" // 409: changed by someone else, or already final
  | "length_required" // 411: the upload had no plain Content-Length
  | "too_large" // 413: the upload is over the size limit
  | "invalid" // 422: the request did not pass validation
  | "server" // any other answer, or a body that is not the API's JSON
  | "network"; // no answer at all

// One field the API refused in a 422: where it is and the kind of problem, never its value.
export interface FieldProblem {
  loc: (string | number)[];
  type: string;
}

// The letter check's kinds, to keep only a kind the API can send.
const LETTER_PROBLEM_KINDS: ReadonlySet<string> = new Set<LetterProblemKind>([
  "policy_without_citation",
  "chunk_not_found",
  "quote_too_short",
  "quote_not_in_evidence",
  "placeholder_text",
  "amount_not_a_denied_line",
  "other_as_reason",
  "edit_not_allowed",
]);

// The message of a 422 that carries a list of field problems.
export const INVALID_REQUEST = "the request was not valid";

const KIND_BY_STATUS: Readonly<Record<number, ApiErrorKind>> = {
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  411: "length_required",
  413: "too_large",
  422: "invalid",
};

export class ApiError extends Error {
  readonly status: number | null;
  readonly kind: ApiErrorKind;
  readonly problems: readonly FieldProblem[];
  // The problems of a refused letter (a draft edit or approval): kinds and positions only.
  readonly letterProblems: readonly LetterProblem[];

  constructor(
    status: number | null,
    kind: ApiErrorKind,
    message: string,
    problems: readonly FieldProblem[] = [],
    letterProblems: readonly LetterProblem[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.problems = problems;
    this.letterProblems = letterProblems;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Keeps only `loc` and `type` of each 422 problem. `msg` and any `input` are dropped, so text the
// user typed never reaches the screen or a log through an error.
function readProblems(detail: unknown): FieldProblem[] {
  if (!Array.isArray(detail)) {
    return [];
  }
  return detail.flatMap((item: unknown) => {
    if (!isRecord(item) || !Array.isArray(item.loc) || typeof item.type !== "string") {
      return [];
    }
    const loc = item.loc.filter(
      (part: unknown): part is string | number =>
        typeof part === "string" || typeof part === "number",
    );
    return [{ loc, type: item.type }];
  });
}

function isIndex(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

// Keeps only `kind`, `paragraph_index` and `citation_index` of each problem of a refused letter;
// an entry of another shape is dropped.
function readLetterProblems(problems: unknown): LetterProblem[] {
  if (!Array.isArray(problems)) {
    return [];
  }
  return problems.flatMap((item: unknown) => {
    if (
      !isRecord(item) ||
      typeof item.kind !== "string" ||
      !LETTER_PROBLEM_KINDS.has(item.kind) ||
      !isIndex(item.paragraph_index)
    ) {
      return [];
    }
    const citationIndex = isIndex(item.citation_index) ? item.citation_index : null;
    return [
      {
        kind: item.kind as LetterProblemKind,
        paragraph_index: item.paragraph_index,
        citation_index: citationIndex,
      },
    ];
  });
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const kind = KIND_BY_STATUS[response.status] ?? "server";
  const body = await readJson(response);
  const detail = isRecord(body) ? body.detail : undefined;
  // A 422 whose detail is text is one of the API's fixed sentences (the upload route's, such as
  // "the file is empty"), never input, so it is kept. A list keeps only `loc` and `type`.
  // A 422 whose detail is an object is a refused letter: its `message` is a fixed sentence and
  // its `problems` hold kinds and positions only.
  if (kind === "invalid" && isRecord(detail)) {
    const message = typeof detail.message === "string" ? detail.message : INVALID_REQUEST;
    return new ApiError(response.status, kind, message, [], readLetterProblems(detail.problems));
  }
  if (kind === "invalid" && typeof detail !== "string") {
    return new ApiError(response.status, kind, INVALID_REQUEST, readProblems(detail));
  }
  const message = typeof detail === "string" ? detail : `the API answered ${response.status}`;
  return new ApiError(response.status, kind, message);
}

// The options of a request with a JSON body.
export function jsonRequest(method: "POST" | "PUT", body: object): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// Sends one request and returns the parsed JSON body (undefined for 204). Any other answer than
// 2xx raises an ApiError; so does no answer at all or a 2xx body that is not JSON.
export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, credentials: "same-origin" });
  } catch {
    throw new ApiError(null, "network", "the API could not be reached");
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "server", "the API answer was not JSON");
  }
}
