import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Overview } from "../../src/api/types";
import { VIEWER, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";

const OVERVIEW: Overview = {
  jobs_by_status: { queued: 0, running: 1, done: 3, failed: 2 },
  jobs_by_end_state: {
    needs_review: 2,
    needs_review_no_draft: 0,
    stopped_by_rules: 1,
    below_threshold: 0,
    draft_failed: 0,
  },
  drafts_by_review_status: { pending: 1, approved: 1, rejected: 0 },
  cost_usd_this_month: "0.001141",
  cost_usd_total: "0.948021",
  average_seconds: 13.64,
};

function showOverview(answer: () => Response | Promise<Response>) {
  return stubApi({
    ...NO_RECENT,
    "GET /me": () => jsonResponse(200, VIEWER),
    "GET /overview": answer,
  });
}

// The count beside a row's label in the table under `title`.
function countIn(title: string, label: string): string | null {
  const section = screen.getByRole("heading", { name: title }).closest("section");
  if (section === null) {
    throw new Error(`no section for ${title}`);
  }
  const row = within(section).getByRole("rowheader", { name: label }).closest("tr");
  return row === null ? null : within(row).getByRole("cell").textContent;
}

describe("OverviewScreen", () => {
  it("shows every count with its sentence, zeros included", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    expect(await screen.findByRole("heading", { name: "Jobs by status" })).toBeInTheDocument();
    expect(countIn("Jobs by status", "Finished")).toBe("3");
    expect(countIn("Jobs by status", "Waiting to start")).toBe("0");
    expect(countIn("Finished jobs by end state", "Draft ready for review")).toBe("2");
    expect(
      countIn(
        "Finished jobs by end state",
        "Stopped by the rules (deadline or amount); no draft was written",
      ),
    ).toBe("1");
    expect(countIn("Drafts by review", "Approved")).toBe("1");
    expect(countIn("Drafts by review", "Rejected")).toBe("0");
  });

  it("shows the costs exactly as the API sent them and the average time", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    expect(await screen.findByText("$0.001141")).toBeInTheDocument();
    expect(screen.getByText("$0.948021")).toBeInTheDocument();
    expect(screen.getByText("13.6 seconds")).toBeInTheDocument();
  });

  it("says when no job has finished yet", async () => {
    showOverview(() => jsonResponse(200, { ...OVERVIEW, average_seconds: null }));

    renderAt("/app/overview");

    expect(await screen.findByText("No finished job yet.")).toBeInTheDocument();
  });

  it("shows a loading line while the overview is asked for", async () => {
    showOverview(() => new Promise<Response>(() => undefined));

    renderAt("/app/overview");

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error when the API cannot be reached", async () => {
    showOverview(() => Promise.reject(new TypeError("offline")));

    renderAt("/app/overview");

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
  });
});
