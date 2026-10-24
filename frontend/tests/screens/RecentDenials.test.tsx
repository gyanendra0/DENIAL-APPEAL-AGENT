import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorklistItem } from "../../src/api/types";
import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, stubApi } from "../helpers";

const RECENT = "GET /worklist?limit=10";

const ITEM: WorklistItem = {
  job_id: 7,
  file_name: "made-up-letter.txt",
  uploaded_at: "2026-10-20T04:00:49Z",
  uploaded_by: ADMIN.email,
  status: "done",
  failure: null,
  end_state: "needs_review",
  review_status: "pending",
  denial_reason: "noncovered",
  win_probability: 0.72,
};

function recentList() {
  return within(screen.getByRole("region", { name: "Recent denials" }));
}

describe("RecentDenials", () => {
  it("lists the newest denials, each a link to its split view", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      [RECENT]: () =>
        jsonResponse(200, {
          items: [ITEM, { ...ITEM, job_id: 6, file_name: "older.pdf", review_status: null }],
          next_before: null,
        }),
    });
    renderAt("/app/worklist");

    const link = await screen.findByRole("link", { name: /made-up-letter\.txt/ });
    expect(link).toHaveAttribute("href", "/app/jobs/7");
    expect(link).toHaveTextContent("Waiting for review");
    expect(recentList().getByRole("link", { name: /older\.pdf/ })).toHaveTextContent("Finished");
  });

  it("says when there are no denials yet", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      [RECENT]: () => jsonResponse(200, { items: [], next_before: null }),
    });
    renderAt("/app/worklist");

    expect(await screen.findByText("No denials yet.")).toBeInTheDocument();
  });

  it("fails quietly, without an alert", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      [RECENT]: () => jsonResponse(500, { detail: "made-up failure" }),
    });
    renderAt("/app/worklist");

    expect(await screen.findByText("The recent denials could not be loaded.")).toBeInTheDocument();
    expect(recentList().queryByRole("alert")).not.toBeInTheDocument();
  });
});
