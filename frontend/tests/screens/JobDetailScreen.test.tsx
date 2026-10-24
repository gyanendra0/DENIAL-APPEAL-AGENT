import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JobDetail, JobResponse } from "../../src/api/types";
import { JOB_POLL_MS } from "../../src/jobs/useJob";
import { DETAIL, DRAFT, EDITED, TRACE } from "../detail/fixtures";
import { VIEWER, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";
import type { Answer } from "../helpers";

const DONE_JOB: JobResponse = {
  job_id: 9,
  status: "done",
  failure: null,
  end_state: "needs_review",
  created_at: "2026-10-17T10:00:00Z",
  started_at: "2026-10-17T10:00:01Z",
  finished_at: "2026-10-17T10:00:20Z",
};
const RUNNING_JOB: JobResponse = {
  ...DONE_JOB,
  status: "running",
  end_state: null,
  finished_at: null,
};

function answer(body: unknown, status = 200): Answer {
  return () => jsonResponse(status, body);
}

// Opens the detail of job 9 as a viewer (any role may read it), with the given detail.
function openDetail(detail: JobDetail, job: JobResponse = DONE_JOB) {
  const fetchStub = stubApi({
    ...NO_RECENT,
    "GET /me": answer(VIEWER),
    "GET /jobs/9": answer(job),
    "GET /jobs/9/detail": answer(detail),
  });
  renderAt("/app/jobs/9");
  return fetchStub;
}

function section(name: string): ReturnType<typeof within> {
  return within(screen.getByRole("region", { name }));
}

function rowOf(label: string): ReturnType<typeof within> {
  const row = screen.getByRole("rowheader", { name: label }).closest("tr");
  if (row === null) {
    throw new Error(`no row for ${label}`);
  }
  return within(row);
}

afterEach(() => {
  vi.useRealTimers();
});

describe("JobDetailScreen", () => {
  it("shows the letter beside the fields, the analysis and the job facts", async () => {
    openDetail(DETAIL);

    expect(await screen.findByRole("heading", { name: "Job 9: letter-9.txt" })).toBeInTheDocument();
    expect(section("Letter").getByText(/The claim MADE-UP-1001 is denied\./)).toBeInTheDocument();
    expect(section("Letter").queryByText("Text read from the PDF.")).not.toBeInTheDocument();

    const job = section("Job");
    expect(job.getByText("Draft ready for review")).toBeInTheDocument();
    expect(job.getByText("$0.001141")).toBeInTheDocument();
    expect(job.getByText(/by ada@example\.test/)).toBeInTheDocument();

    expect(rowOf("Claim number").getByText("MADE-UP-1001")).toBeInTheDocument();
    expect(rowOf("Claim number").getByText("97%")).toBeInTheDocument();
    expect(rowOf("Claim number").queryByText("Check this")).not.toBeInTheDocument();
    expect(rowOf("Member ID").getByText("Not found")).toBeInTheDocument();
    expect(rowOf("Member ID").getByText("Check this")).toBeInTheDocument();
    expect(rowOf("Payer").getByText("Check this")).toBeInTheDocument();
    expect(section("Extracted fields").getAllByText("Check this")).toHaveLength(2);

    const analysis = section("Analysis");
    expect(analysis.getByText("Passed the rules")).toBeInTheDocument();
    expect(analysis.getByText("63% (a draft needs at least 50%)")).toBeInTheDocument();
    expect(analysis.getByRole("cell", { name: "CMS coverage determination" })).toBeInTheDocument();
    expect(analysis.getByRole("cell", { name: "NCD-0.1" })).toBeInTheDocument();
    expect(analysis.getByRole("cell", { name: "0.81" })).toBeInTheDocument();
  });

  it("says when the text was read from a PDF", async () => {
    openDetail({ ...DETAIL, file_type: "pdf" });

    expect(await screen.findByText("Text read from the PDF.")).toBeInTheDocument();
  });

  it("shows why the rules stopped a letter, and no score", async () => {
    openDetail({
      ...DETAIL,
      end_state: "stopped_by_rules",
      trace: {
        ...TRACE,
        end_state: "stopped_by_rules",
        rules: { ...TRACE.rules, outcome: "hard_fail", reasons: ["deadline_passed"] },
        scoring: { ...TRACE.scoring, status: "stopped", score: null },
        retrieval: { ...TRACE.retrieval, status: "not_run", matches: [] },
      },
      draft: null,
    });

    const analysis = within(await screen.findByRole("region", { name: "Analysis" }));
    expect(analysis.getByText("Stopped by the rules")).toBeInTheDocument();
    expect(analysis.getByText("The appeal deadline has passed.")).toBeInTheDocument();
    expect(analysis.getByText("Not scored.")).toBeInTheDocument();
    expect(analysis.getByText("No evidence was found.")).toBeInTheDocument();
    expect(
      section("Draft").getByText(
        "No draft was written. Stopped by the rules (deadline or amount); no draft was written.",
      ),
    ).toBeInTheDocument();
  });

  it("says when the rules gave no verdict", async () => {
    openDetail({
      ...DETAIL,
      end_state: "needs_review_no_draft",
      trace: {
        ...TRACE,
        rules: { ...TRACE.rules, status: "failed", outcome: null },
        scoring: { ...TRACE.scoring, score: 0.7, threshold: null },
      },
      draft: null,
    });

    const analysis = within(await screen.findByRole("region", { name: "Analysis" }));
    expect(analysis.getByText("The rules gave no verdict.")).toBeInTheDocument();
    expect(analysis.getByText("70%")).toBeInTheDocument();
  });

  it("shows a job that failed before the agent ran", async () => {
    openDetail(
      {
        ...DETAIL,
        status: "failed",
        failure: "extraction_failed",
        end_state: null,
        extraction: null,
        trace: null,
        draft: null,
      },
      { ...DONE_JOB, status: "failed", failure: "extraction_failed", end_state: null },
    );

    expect(
      await screen.findByText("Failed: the letter could not be read into fields"),
    ).toBeInTheDocument();
    expect(screen.getByText("The letter was not read into fields.")).toBeInTheDocument();
    expect(screen.getByText("The agent did not run on this letter.")).toBeInTheDocument();
    expect(section("Draft").getByText("No draft was written.")).toBeInTheDocument();
  });

  it("shows the draft and opens a citation's quote on click", async () => {
    openDetail(DETAIL);
    const user = userEvent.setup();

    const draft = within(await screen.findByRole("region", { name: "Draft" }));
    expect(draft.getByText("Waiting for review")).toBeInTheDocument();
    expect(draft.getByText(/Version 0, not edited\./)).toBeInTheDocument();
    expect(draft.getByText("Policy argument")).toBeInTheDocument();
    expect(draft.getByText("We ask you to look again at this made-up claim.")).toBeInTheDocument();
    // An unedited draft is the model's draft, so there is nothing to toggle.
    expect(draft.queryByRole("button", { name: "Show the model's draft" })).not.toBeInTheDocument();

    const quote = draft.getByText("made-up quote that covers the service");
    expect(quote).not.toBeVisible();
    await user.click(draft.getByText("CMS coverage determination NCD-0.1, chunk 3"));
    expect(quote).toBeVisible();
  });

  it("shows the latest version and the model's draft on request", async () => {
    openDetail({ ...DETAIL, draft: { ...DRAFT, latest_version: 1, latest: EDITED } });
    const user = userEvent.setup();

    const draft = within(await screen.findByRole("region", { name: "Draft" }));
    expect(draft.getByText(/Version 1, edited by a person\./)).toBeInTheDocument();
    expect(draft.getByText("Please review this made-up claim again.")).toBeInTheDocument();

    await user.click(draft.getByRole("button", { name: "Show the model's draft" }));
    expect(draft.getByText(/The model's draft \(made-up-model, prompt v3\)\./)).toBeInTheDocument();
    expect(draft.getByText("We ask you to look again at this made-up claim.")).toBeInTheDocument();
    expect(draft.queryByText("Please review this made-up claim again.")).not.toBeInTheDocument();

    await user.click(draft.getByRole("button", { name: "Show the latest version" }));
    expect(draft.getByText("Please review this made-up claim again.")).toBeInTheDocument();
  });

  it("shows who approved a draft and which version", async () => {
    openDetail({
      ...DETAIL,
      draft: {
        ...DRAFT,
        review_status: "approved",
        reviewed_by: "rev@example.test",
        reviewed_at: "2026-10-18T09:00:00Z",
        approved_version: 0,
      },
    });

    const draft = within(await screen.findByRole("region", { name: "Draft" }));
    expect(draft.getByText("Approved")).toBeInTheDocument();
    expect(draft.getByText("rev@example.test")).toBeInTheDocument();
    expect(draft.getByText("Approved version")).toBeInTheDocument();
    expect(draft.queryByText("Reason")).not.toBeInTheDocument();
  });

  it("shows why a draft was rejected", async () => {
    openDetail({
      ...DETAIL,
      draft: {
        ...DRAFT,
        review_status: "rejected",
        reviewed_by: "rev@example.test",
        reviewed_at: "2026-10-18T09:00:00Z",
        reject_reason: "citation_does_not_fit",
      },
    });

    const draft = within(await screen.findByRole("region", { name: "Draft" }));
    expect(draft.getByText("Rejected")).toBeInTheDocument();
    expect(draft.getByText("A cited quote does not support its paragraph")).toBeInTheDocument();
  });

  it("polls a running job and reads its detail once it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let jobCalls = 0;
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": answer(VIEWER),
      "GET /jobs/9": () => jsonResponse(200, jobCalls++ === 0 ? RUNNING_JOB : DONE_JOB),
      "GET /jobs/9/detail": answer(DETAIL),
    });
    renderAt("/app/jobs/9");

    expect(await screen.findByRole("status")).toHaveTextContent("Reading the letter and drafting");
    expect(fetchStub.mock.calls.some(([path]) => path === "/jobs/9/detail")).toBe(false);

    await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));
    expect(await screen.findByRole("heading", { name: "Job 9: letter-9.txt" })).toBeInTheDocument();
  });

  it("answers a job of another account as not found", async () => {
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": answer(VIEWER),
      "GET /jobs/9": answer({ detail: "not found" }, 404),
    });
    renderAt("/app/jobs/9");

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(fetchStub.mock.calls.some(([path]) => path === "/jobs/9/detail")).toBe(false);
  });

  it("answers an id that is not a job number as not found, without asking the API", async () => {
    const fetchStub = stubApi({ ...NO_RECENT, "GET /me": answer(VIEWER) });
    renderAt("/app/jobs/abc");

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(fetchStub.mock.calls.some(([path]) => String(path).startsWith("/jobs"))).toBe(false);
  });

  it("shows a server error on the detail in plain words", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": answer(VIEWER),
      "GET /jobs/9": answer(DONE_JOB),
      "GET /jobs/9/detail": answer({ detail: "boom" }, 500),
    });
    renderAt("/app/jobs/9");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server. Try again later.",
    );
  });
});
