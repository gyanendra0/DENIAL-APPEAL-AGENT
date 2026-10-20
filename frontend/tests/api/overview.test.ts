import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchOverview } from "../../src/api/overview";
import type { Overview } from "../../src/api/types";
import { jsonResponse, stubApi } from "../helpers";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchOverview", () => {
  it("reads the overview, with the costs kept as decimal strings", async () => {
    const overview: Overview = {
      jobs_by_status: { queued: 0, running: 0, done: 2, failed: 1 },
      jobs_by_end_state: {
        needs_review: 1,
        needs_review_no_draft: 0,
        stopped_by_rules: 1,
        below_threshold: 0,
        draft_failed: 0,
      },
      drafts_by_review_status: { pending: 0, approved: 1, rejected: 0 },
      cost_usd_this_month: "0.001141",
      cost_usd_total: "0.948021",
      average_seconds: 13.6,
    };
    stubApi({ "GET /overview": () => jsonResponse(200, overview) });

    const answer = await fetchOverview();

    expect(answer).toEqual(overview);
    expect(answer.cost_usd_total).toBe("0.948021");
  });

  it("keeps a null average when no job has finished", async () => {
    stubApi({
      "GET /overview": () =>
        jsonResponse(200, {
          jobs_by_status: { queued: 0, running: 0, done: 0, failed: 0 },
          jobs_by_end_state: {},
          drafts_by_review_status: { pending: 0, approved: 0, rejected: 0 },
          cost_usd_this_month: "0",
          cost_usd_total: "0",
          average_seconds: null,
        }),
    });

    expect((await fetchOverview()).average_seconds).toBeNull();
  });
});
