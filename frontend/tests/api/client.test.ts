import { describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "../../src/api/client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchStub = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("expected an ApiError");
}

describe("apiRequest", () => {
  it("returns the parsed body of a 2xx answer", async () => {
    stubFetch(jsonResponse(200, { job_id: 7, status: "queued" }));

    await expect(apiRequest("/jobs/7")).resolves.toEqual({ job_id: 7, status: "queued" });
  });

  it("returns undefined for a 204 answer", async () => {
    stubFetch(new Response(null, { status: 204 }));

    await expect(apiRequest("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("sends the same-origin cookie and keeps the caller's options", async () => {
    const fetchStub = stubFetch(jsonResponse(200, {}));

    await apiRequest("/auth/login", { method: "POST", body: "{}" });

    expect(fetchStub).toHaveBeenCalledWith("/auth/login", {
      method: "POST",
      body: "{}",
      credentials: "same-origin",
    });
  });

  it("does not let the caller change the cookie mode", async () => {
    const fetchStub = stubFetch(jsonResponse(200, {}));

    await apiRequest("/me", { credentials: "include" });

    expect(fetchStub).toHaveBeenCalledWith("/me", { credentials: "same-origin" });
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [411, "length_required"],
    [413, "too_large"],
    [429, "too_many"],
  ] as const)("maps a %i answer to the kind %s with the server's detail", async (status, kind) => {
    stubFetch(jsonResponse(status, { detail: "job not found" }));

    const error = await caught(apiRequest("/jobs/1"));

    expect(error.status).toBe(status);
    expect(error.kind).toBe(kind);
    expect(error.message).toBe("job not found");
    expect(error.problems).toEqual([]);
  });

  it("maps an unlisted status to the kind server", async () => {
    stubFetch(jsonResponse(500, { detail: "Internal Server Error" }));

    const error = await caught(apiRequest("/worklist"));

    expect(error.status).toBe(500);
    expect(error.kind).toBe("server");
  });

  it("keeps only the positions and types of a 422, never the message or the input", async () => {
    stubFetch(
      jsonResponse(422, {
        detail: [
          {
            loc: ["body", "email"],
            type: "value_error",
            msg: "not an email: secret@example",
            input: "secret@example",
          },
          { loc: ["body", "items", 0], type: "missing", msg: "Field required" },
        ],
      }),
    );

    const error = await caught(apiRequest("/auth/signup", { method: "POST" }));

    expect(error.kind).toBe("invalid");
    expect(error.problems).toEqual([
      { loc: ["body", "email"], type: "value_error" },
      { loc: ["body", "items", 0], type: "missing" },
    ]);
    expect(JSON.stringify(error.problems)).not.toContain("secret");
    expect(error.message).not.toContain("secret");
  });

  it("drops 422 problems that do not have the expected shape", async () => {
    stubFetch(
      jsonResponse(422, {
        detail: [{ loc: "body", type: "missing" }, "text", { loc: ["body", null], type: "x" }],
      }),
    );

    const error = await caught(apiRequest("/uploads", { method: "POST" }));

    expect(error.problems).toEqual([{ loc: ["body"], type: "x" }]);
  });

  it("keeps the API's fixed sentence of a 422 whose detail is text", async () => {
    stubFetch(jsonResponse(422, { detail: "the file is empty" }));

    const error = await caught(apiRequest("/uploads", { method: "POST" }));

    expect(error.kind).toBe("invalid");
    expect(error.message).toBe("the file is empty");
    expect(error.problems).toEqual([]);
  });

  it("gives no problems for a 422 whose detail is neither a list nor text", async () => {
    stubFetch(jsonResponse(422, { detail: { nested: true } }));

    const error = await caught(apiRequest("/uploads", { method: "POST" }));

    expect(error.kind).toBe("invalid");
    expect(error.message).toBe("the request was not valid");
    expect(error.problems).toEqual([]);
  });

  it("keeps only the kinds and positions of a refused letter, never its text", async () => {
    stubFetch(
      jsonResponse(422, {
        detail: {
          message: "the letter did not pass the checks",
          problems: [
            { kind: "policy_without_citation", paragraph_index: 1, citation_index: null },
            {
              kind: "quote_not_in_evidence",
              paragraph_index: 2,
              citation_index: 0,
              text: "Made-up secret letter text",
            },
          ],
        },
      }),
    );

    const error = await caught(apiRequest("/jobs/7/draft", { method: "PUT" }));

    expect(error.kind).toBe("invalid");
    expect(error.message).toBe("the letter did not pass the checks");
    expect(error.problems).toEqual([]);
    expect(error.letterProblems).toEqual([
      { kind: "policy_without_citation", paragraph_index: 1, citation_index: null },
      { kind: "quote_not_in_evidence", paragraph_index: 2, citation_index: 0 },
    ]);
    expect(JSON.stringify(error.letterProblems)).not.toContain("secret");
  });

  it("drops refused-letter problems that do not have the expected shape", async () => {
    stubFetch(
      jsonResponse(422, {
        detail: {
          message: "the letter did not pass the checks",
          problems: [
            "text",
            { kind: "not_a_kind", paragraph_index: 0 },
            { kind: "placeholder_text", paragraph_index: -1 },
            { kind: "placeholder_text", paragraph_index: "0" },
            { kind: "edit_not_allowed", paragraph_index: 3, citation_index: 1.5 },
          ],
        },
      }),
    );

    const error = await caught(apiRequest("/jobs/7/draft/approve", { method: "POST" }));

    expect(error.letterProblems).toEqual([
      { kind: "edit_not_allowed", paragraph_index: 3, citation_index: null },
    ]);
  });

  it("gives no problems of either kind for a 422 without a detail", async () => {
    stubFetch(new Response("not JSON", { status: 422 }));

    const error = await caught(apiRequest("/jobs/7/draft", { method: "PUT" }));

    expect(error.kind).toBe("invalid");
    expect(error.message).toBe("the request was not valid");
    expect(error.problems).toEqual([]);
    expect(error.letterProblems).toEqual([]);
  });

  it("gives no letter problems for a 422 with a list of field problems", async () => {
    stubFetch(jsonResponse(422, { detail: [{ loc: ["body", "version"], type: "missing" }] }));

    const error = await caught(apiRequest("/jobs/7/draft/approve", { method: "POST" }));

    expect(error.problems).toEqual([{ loc: ["body", "version"], type: "missing" }]);
    expect(error.letterProblems).toEqual([]);
  });

  it("gives a plain message when the error body is not JSON", async () => {
    stubFetch(new Response("<html>bad gateway</html>", { status: 502 }));

    const error = await caught(apiRequest("/me"));

    expect(error.kind).toBe("server");
    expect(error.message).toBe("the API answered 502");
  });

  it("gives a plain message when the error detail is not text", async () => {
    stubFetch(jsonResponse(404, { detail: { nested: true } }));

    const error = await caught(apiRequest("/jobs/1"));

    expect(error.kind).toBe("not_found");
    expect(error.message).toBe("the API answered 404");
  });

  it("raises a server error when a 2xx body is not JSON", async () => {
    stubFetch(new Response("not json", { status: 200 }));

    const error = await caught(apiRequest("/me"));

    expect(error.status).toBe(200);
    expect(error.kind).toBe("server");
  });

  it("raises a network error when no answer comes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await caught(apiRequest("/me"));

    expect(error.status).toBeNull();
    expect(error.kind).toBe("network");
    expect(error.name).toBe("ApiError");
  });
});
