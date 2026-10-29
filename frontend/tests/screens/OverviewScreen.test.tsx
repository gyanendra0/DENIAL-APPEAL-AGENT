import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Overview } from "../../src/api/types";
import type { UserResponse } from "../../src/api/types";
import { ADMIN, REVIEWER, VIEWER, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";

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

const NOTHING_YET: Overview = {
  jobs_by_status: { queued: 0, running: 0, done: 0, failed: 0 },
  jobs_by_end_state: {
    needs_review: 0,
    needs_review_no_draft: 0,
    stopped_by_rules: 0,
    below_threshold: 0,
    draft_failed: 0,
  },
  drafts_by_review_status: { pending: 0, approved: 0, rejected: 0 },
  cost_usd_this_month: null,
  cost_usd_total: null,
  average_seconds: null,
};

function showOverview(answer: () => Response | Promise<Response>, user: UserResponse = VIEWER) {
  return stubApi({
    ...NO_RECENT,
    "GET /me": () => jsonResponse(200, user),
    "GET /overview": answer,
  });
}

// The row of a value in the card under `title`.
function rowIn(title: string, label: string): HTMLElement {
  const section = screen.getByRole("heading", { name: new RegExp(`^${title}`) }).closest("section");
  if (section === null) {
    throw new Error(`no section for ${title}`);
  }
  const row = within(section).getByRole("rowheader", { name: label }).closest("tr");
  if (row === null) {
    throw new Error(`no row for ${label}`);
  }
  return row;
}

// The count beside a row's label (its only cell; the tables show no percent).
function countIn(title: string, label: string): string | null {
  return within(rowIn(title, label)).getByRole("cell").textContent;
}

// The value (and the note, if any) of the headline tile named `label`.
function tile(label: string): string {
  const term = screen.getByText(label, { selector: "dt" });
  return Array.from(term.parentElement?.querySelectorAll("dd") ?? [])
    .map((dd) => dd.textContent)
    .join(" | ");
}

describe("OverviewScreen", () => {
  it("shows every count with its sentence, zeros included", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    expect(await screen.findByRole("heading", { name: /^Jobs by status/ })).toBeInTheDocument();
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
    expect(screen.queryByText(/%$/, { selector: "td" })).not.toBeInTheDocument();
  });

  it("shows the charts to an admin above the tables", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW), ADMIN);

    renderAt("/app/overview");

    const charts = await screen.findByRole("region", { name: "Charts" });
    expect(within(charts).getByRole("img", { name: "Finished: 3 of 6 (50%)" })).toBeInTheDocument();
    expect(within(charts).getByRole("meter", { name: "Approval rate" })).toBeInTheDocument();
  });

  it.each([
    ["a viewer", VIEWER],
    ["a reviewer", REVIEWER],
  ])("leaves the charts out for %s", async (_who, user) => {
    showOverview(() => jsonResponse(200, OVERVIEW), user);

    renderAt("/app/overview");

    expect(await screen.findByRole("heading", { name: /^Jobs by status/ })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Charts" })).not.toBeInTheDocument();
  });

  it("puts each card's total beside its title", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    expect(await screen.findByRole("heading", { name: "Jobs by status 6" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Finished jobs by end state 3" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Drafts by review 2" })).toBeInTheDocument();
  });

  it("draws each bar as its share, coloured by what the value means, and greys a zero", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    await screen.findByRole("heading", { name: /^Jobs by status/ });
    const finished = rowIn("Jobs by status", "Finished");
    const fill = finished.querySelector(".count-fill");
    expect(fill).toHaveClass("tone-good");
    expect(fill).toHaveStyle({ width: "50%" });
    expect(finished.querySelector(".count-track")).toHaveAttribute("aria-hidden", "true");
    expect(rowIn("Jobs by status", "Failed").querySelector(".count-fill")).toHaveClass("tone-bad");
    expect(
      rowIn("Jobs by status", "Reading the letter and drafting").querySelector(".count-fill"),
    ).toHaveClass("tone-waiting");
    expect(rowIn("Jobs by status", "Waiting to start")).toHaveClass("is-zero");
    expect(rowIn("Jobs by status", "Finished")).not.toHaveClass("is-zero");
  });

  it("leads with the headline numbers", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    await screen.findByRole("heading", { name: /^Jobs by status/ });
    expect(tile("Letters uploaded")).toBe("6");
    expect(tile("Drafts waiting for review")).toBe("1");
    expect(tile("Approved")).toBe("1 | 100% of decided");
    expect(tile("Average time to finish")).toBe("13.6 seconds | From upload to the end");
  });

  it("says no draft is decided yet instead of a percent of nothing", async () => {
    showOverview(() =>
      jsonResponse(200, {
        ...OVERVIEW,
        drafts_by_review_status: { pending: 2, approved: 0, rejected: 0 },
      }),
    );

    renderAt("/app/overview");

    await screen.findByRole("heading", { name: /^Jobs by status/ });
    expect(tile("Approved")).toBe("0 | None decided yet");
  });

  it("says nothing is uploaded yet instead of a page of zeros, and offers the upload", async () => {
    showOverview(() => jsonResponse(200, NOTHING_YET), ADMIN);

    renderAt("/app/overview");

    expect(await screen.findByText(/Nothing uploaded yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a denial letter" })).toHaveAttribute(
      "href",
      "/app/upload",
    );
    expect(screen.queryByRole("heading", { name: /^Jobs by status/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Letters uploaded")).not.toBeInTheDocument();
  });

  it("shows the costs exactly as the API sent them and the average time", async () => {
    showOverview(() => jsonResponse(200, OVERVIEW));

    renderAt("/app/overview");

    expect(await screen.findByText("$0.001141")).toBeInTheDocument();
    expect(tile("Model cost this month")).toBe("$0.001141");
    expect(tile("Model cost in all")).toBe("$0.948021");
    expect(screen.getByText("13.6 seconds")).toBeInTheDocument();
  });

  it("leaves the cost rows out when the API sends no cost", async () => {
    showOverview(() =>
      jsonResponse(200, { ...OVERVIEW, cost_usd_this_month: null, cost_usd_total: null }),
    );

    renderAt("/app/overview");

    expect(await screen.findByText("Letters uploaded")).toBeInTheDocument();
    expect(screen.queryByText(/Model cost/)).not.toBeInTheDocument();
    expect(screen.getByText("13.6 seconds")).toBeInTheDocument();
  });

  it("says when no job has finished yet", async () => {
    showOverview(() => jsonResponse(200, { ...OVERVIEW, average_seconds: null }));

    renderAt("/app/overview");

    expect(await screen.findByText("No finished job yet.")).toBeInTheDocument();
    expect(tile("Average time to finish")).toBe("– | No finished job yet.");
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
