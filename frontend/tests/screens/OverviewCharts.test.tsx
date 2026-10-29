import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Overview } from "../../src/api/types";
import { OverviewCharts } from "../../src/screens/OverviewCharts";

const OVERVIEW: Overview = {
  jobs_by_status: { queued: 0, running: 1, done: 3, failed: 16 },
  jobs_by_end_state: {
    needs_review: 2,
    needs_review_no_draft: 0,
    stopped_by_rules: 1,
    below_threshold: 0,
    draft_failed: 0,
  },
  drafts_by_review_status: { pending: 1, approved: 3, rejected: 1 },
  cost_usd_this_month: null,
  cost_usd_total: null,
  average_seconds: 13.64,
};

// The chart (a figure) whose caption starts with `title`.
function chart(title: string): HTMLElement {
  const caption = screen.getByText(title, { selector: "figcaption", exact: false });
  const figure = caption.closest("figure");
  if (figure === null) {
    throw new Error(`no chart ${title}`);
  }
  return figure;
}

describe("OverviewCharts", () => {
  it("splits each bar into the values above 0, sized by count and named for the tooltip", () => {
    render(<OverviewCharts overview={OVERVIEW} />);

    const jobs = within(chart("Jobs by status"));
    const segments = jobs.getAllByRole("img");
    expect(segments.map((segment) => segment.getAttribute("aria-label"))).toEqual([
      "Reading the letter and drafting: 1 of 20 (5%)",
      "Finished: 3 of 20 (15%)",
      "Failed: 16 of 20 (80%)",
    ]);
    const failed = jobs.getByRole("img", { name: "Failed: 16 of 20 (80%)" });
    expect(failed).toHaveAttribute("data-tip", "Failed: 16 of 20 (80%)");
    expect(failed).toHaveClass("tone-bad");
    expect(failed).toHaveStyle({ flexGrow: "16" });
    expect(screen.getByText("20 in all", { exact: false })).toBeInTheDocument();
  });

  it("writes the count inside a segment only when it has room", () => {
    render(<OverviewCharts overview={OVERVIEW} />);

    const jobs = within(chart("Jobs by status"));
    expect(jobs.getByRole("img", { name: /^Failed/ })).toHaveTextContent("16");
    expect(jobs.getByRole("img", { name: /^Finished/ })).toHaveTextContent("3");
    expect(jobs.getByRole("img", { name: /^Reading/ })).toHaveTextContent("");
  });

  it("lets the keyboard reach each segment", async () => {
    render(<OverviewCharts overview={OVERVIEW} />);

    await userEvent.setup().tab();

    expect(screen.getByRole("img", { name: /^Reading the letter/ })).toHaveFocus();
  });

  it("lists every value in the legend, zeros included", () => {
    render(<OverviewCharts overview={OVERVIEW} />);

    const legend = within(chart("Jobs by status")).getAllByRole("listitem");
    expect(legend.map((item) => item.textContent)).toEqual([
      "Waiting to start 0",
      "Reading the letter and drafting 1",
      "Finished 3",
      "Failed 16",
    ]);
    expect(legend[0]).toHaveClass("is-zero");
    expect(legend[2]).not.toHaveClass("is-zero");
  });

  it("says a bar with nothing in it is empty instead of drawing it", () => {
    render(
      <OverviewCharts
        overview={{
          ...OVERVIEW,
          drafts_by_review_status: { pending: 0, approved: 0, rejected: 0 },
        }}
      />,
    );

    const drafts = within(chart("Drafts by review"));
    expect(drafts.getByText("Nothing here yet.")).toBeInTheDocument();
    expect(drafts.queryByRole("img")).not.toBeInTheDocument();
  });

  it("shows the approved share of the decided drafts, leaving the waiting ones out", () => {
    render(<OverviewCharts overview={OVERVIEW} />);

    const meter = screen.getByRole("meter", { name: "Approval rate" });
    expect(meter).toHaveAttribute("aria-valuenow", "75");
    expect(meter).toHaveAttribute("aria-valuetext", "75% approved: 3 of 4 decided drafts");
    const approval = within(chart("Approval rate"));
    expect(approval.getByText("75% approved")).toBeInTheDocument();
    expect(approval.getByText("3 approved · 1 rejected")).toBeInTheDocument();
    expect(approval.getByText("4 decided")).toBeInTheDocument();
  });

  it("says no draft is decided yet instead of a rate of nothing", () => {
    render(
      <OverviewCharts
        overview={{
          ...OVERVIEW,
          drafts_by_review_status: { pending: 2, approved: 0, rejected: 0 },
        }}
      />,
    );

    expect(screen.getByText("No draft decided yet.")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });
});
