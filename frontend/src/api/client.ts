// The one way the screens call the API. The login token is an HttpOnly cookie on the same site,
// so every request sends it (`credentials: "same-origin"`) and no code ever reads it.

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

  constructor(
    status: number | null,
    kind: ApiErrorKind,
    message: string,
    problems: readonly FieldProblem[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.problems = problems;
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
  if (kind === "invalid") {
    return new ApiError(response.status, kind, "the request was not valid", readProblems(detail));
  }
  const message = typeof detail === "string" ? detail : `the API answered ${response.status}`;
  return new ApiError(response.status, kind, message);
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
