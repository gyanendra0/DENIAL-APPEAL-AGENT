import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchWorklist, worklistPath } from "../../src/api/worklist";
import { jsonResponse, stubApi } from "../helpers";

const FIRST = { before: null, limit: 50, status: null, reviewStatus: null };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("worklistPath", () => {
  it("sends only the limit when nothing else is set", () => {
    expect(worklistPath(FIRST)).toBe("/worklist?limit=50");
  });

  it("sends the page start and both filters", () => {
    expect(worklistPath({ before: 12, limit: 50, status: "done", reviewStatus: "pending" })).toBe(
      "/worklist?before=12&limit=50&status=done&review_status=pending",
    );
  });
});

describe("fetchWorklist", () => {
  it("returns the page the API answers", async () => {
    const page = { items: [], next_before: null };
    stubApi({ "GET /worklist?limit=50&status=failed": () => jsonResponse(200, page) });

    await expect(fetchWorklist({ ...FIRST, status: "failed" })).resolves.toEqual(page);
  });
});
