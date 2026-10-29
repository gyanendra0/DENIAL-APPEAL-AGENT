import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { WorklistItem } from "../../src/api/types";
import { ADMIN, VIEWER, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";

const FIRST_PAGE = "GET /worklist?limit=50";

const DRAFTED: WorklistItem = {
  job_id: 9,
  file_name: "letter-9.txt",
  uploaded_at: "2026-10-17T10:00:00Z",
  uploaded_by: "ada@example.test",
  status: "done",
  failure: null,
  end_state: "needs_review",
  review_status: "pending",
  denial_reason: "medical_necessity",
  win_probability: 0.634,
};
const FAILED: WorklistItem = {
  ...DRAFTED,
  job_id: 8,
  file_name: "letter-8.txt",
  status: "failed",
  failure: "extraction_failed",
  end_state: null,
  review_status: null,
  denial_reason: null,
  win_probability: null,
};

function page(items: WorklistItem[], nextBefore: number | null = null) {
  return () => jsonResponse(200, { items, next_before: nextBefore });
}

function rowOf(jobId: number): HTMLElement {
  const row = screen.getByText(`letter-${jobId}.txt`).closest("tr");
  if (row === null) {
    throw new Error(`no row for job ${jobId}`);
  }
  return row;
}

describe("WorklistScreen", () => {
  it("shows each job in plain words", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED, FAILED]),
    });

    renderAt("/app/worklist");

    expect(await screen.findByText("letter-9.txt")).toBeInTheDocument();
    const drafted = within(rowOf(9));
    expect(drafted.queryByRole("link", { name: "9" })).not.toBeInTheDocument();
    expect(drafted.getByRole("link", { name: "Edit letter-9.txt" })).toHaveAttribute(
      "href",
      "/app/jobs/9",
    );
    expect(drafted.getByRole("link", { name: "Edit letter-9.txt" })).toHaveTextContent("Edit");
    expect(drafted.getByRole("link", { name: "Edit letter-9.txt" })).toHaveClass(
      "button-link",
      "secondary",
    );
    expect(drafted.getByText("Draft ready for review")).toBeInTheDocument();
    expect(drafted.getByText("Waiting for review")).toBeInTheDocument();
    expect(drafted.getByText("Medical necessity")).toBeInTheDocument();
    expect(drafted.getByText("63%")).toBeInTheDocument();
    const failed = within(rowOf(8));
    expect(
      failed.getByText("Failed: the letter could not be read into fields"),
    ).toBeInTheDocument();
    expect(failed.getAllByText("–")).toHaveLength(3);
    expect(failed.getByRole("link", { name: "Edit letter-8.txt" })).toHaveAttribute(
      "href",
      "/app/jobs/8",
    );
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("shows each job's file with its number, the uploader under the date, pills and a bar", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED, FAILED]),
    });

    renderAt("/app/worklist");

    await screen.findByText("letter-9.txt");
    const drafted = rowOf(9);
    expect(within(drafted).getByText("#9")).toBeInTheDocument();
    expect(within(drafted).getByText("ada@example.test")).toHaveClass("worklist-sub");
    expect(screen.queryByRole("columnheader", { name: "Job" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "By" })).not.toBeInTheDocument();
    const ready = within(drafted).getByText("Draft ready for review");
    expect(ready).toHaveClass("pill");
    expect(ready.querySelector(".pill-dot")).toHaveClass("tone-good");
    expect(within(drafted).getByText("Waiting for review").querySelector(".pill-dot")).toHaveClass(
      "tone-waiting",
    );
    expect(within(drafted).getByText("Medical necessity")).toHaveClass("tag");
    const chance = within(drafted).getByText("63%");
    expect(chance.querySelector(".chance-fill")).toHaveClass("tone-good");
    expect(chance.querySelector(".chance-fill")).toHaveStyle({ width: "63%" });
    expect(
      within(rowOf(8))
        .getByText("Failed: the letter could not be read into fields")
        .querySelector(".pill-dot"),
    ).toHaveClass("tone-bad");
  });

  it.each([
    [0.6, "tone-good"],
    [0.59, "tone-waiting"],
    [0.3, "tone-waiting"],
    [0.29, "tone-bad"],
  ])("colours a win chance of %f as %s", async (probability, tone) => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([{ ...DRAFTED, win_probability: probability }]),
    });

    renderAt("/app/worklist");

    const chance = await screen.findByText(`${Math.round(probability * 100)}%`);
    expect(chance.querySelector(".chance-fill")).toHaveClass(tone);
  });

  it.each([
    ["queued", "tone-waiting", "Waiting to start"],
    ["running", "tone-waiting", "Reading the letter and drafting"],
  ] as const)("shows a %s job as a waiting pill", async (status, tone, text) => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([{ ...FAILED, status, failure: null }]),
    });

    renderAt("/app/worklist");

    // The filter's options hold the same words, so the pill is looked for by its class.
    const pill = await screen.findByText(text, { selector: ".pill" });
    expect(pill.querySelector(".pill-dot")).toHaveClass(tone);
  });

  it("gives a finished job with no end state a good pill", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([{ ...FAILED, status: "done", failure: null }]),
    });

    renderAt("/app/worklist");

    const pill = await screen.findByText("Finished", { selector: ".pill" });
    expect(pill.querySelector(".pill-dot")).toHaveClass("tone-good");
  });

  it("counts the jobs shown, says when more are to come, and when they are filtered", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED], 9),
      "GET /worklist?before=9&limit=50": page([FAILED]),
      "GET /worklist?limit=50&status=failed": page([FAILED]),
    });
    const user = userEvent.setup();
    renderAt("/app/worklist");

    expect(await screen.findByText("1 job so far")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("2 jobs")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Job status"), "failed");
    expect(await screen.findByText("1 job shown")).toBeInTheDocument();
  });

  it("clears both filters from the toolbar and keeps nothing else", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED, FAILED]),
      "GET /worklist?limit=50&status=failed&review_status=pending": page([FAILED]),
    });
    const user = userEvent.setup();
    const { router } = renderAt("/app/worklist?status=failed&review_status=pending");
    await screen.findByText("letter-8.txt");
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(await screen.findByText("letter-9.txt")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("offers to clear the filters when they match nothing", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED]),
      "GET /worklist?limit=50&status=queued": page([]),
    });
    const user = userEvent.setup();
    const { router } = renderAt("/app/worklist?status=queued");

    expect(await screen.findByText("No jobs match these filters.")).toBeInTheDocument();
    expect(screen.queryByText("No jobs here yet.")).not.toBeInTheDocument();
    const clears = screen.getAllByRole("button", { name: "Clear filters" });
    expect(clears).toHaveLength(2);
    await user.click(clears[1] as HTMLElement);

    expect(await screen.findByText("letter-9.txt")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
  });

  it("loads the older jobs when asked", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED], 9),
      "GET /worklist?before=9&limit=50": page([FAILED]),
    });
    const user = userEvent.setup();
    renderAt("/app/worklist");

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("letter-8.txt")).toBeInTheDocument();
    expect(screen.getByText("letter-9.txt")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("keeps the shown jobs when an older page fails", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED], 9),
      "GET /worklist?before=9&limit=50": () => jsonResponse(500, { detail: "boom" }),
    });
    const user = userEvent.setup();
    renderAt("/app/worklist");

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("letter-9.txt")).toBeInTheDocument();
  });

  it("puts a chosen filter in the address and asks the API with it", async () => {
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED, FAILED]),
      "GET /worklist?limit=50&status=failed": page([FAILED]),
      "GET /worklist?limit=50&status=failed&review_status=pending": page([]),
      "GET /worklist?limit=50&review_status=pending": page([]),
    });
    const user = userEvent.setup();
    const { router } = renderAt("/app/worklist");
    await screen.findByText("letter-9.txt");

    await user.selectOptions(screen.getByLabelText("Job status"), "failed");

    await screen.findByText("letter-8.txt");
    expect(screen.queryByText("letter-9.txt")).not.toBeInTheDocument();
    expect(router.state.location.search).toBe("?status=failed");

    await user.selectOptions(screen.getByLabelText("Review status"), "pending");

    expect(await screen.findByText("No jobs match these filters.")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?status=failed&review_status=pending");
    expect(screen.getByText(/Jobs without a draft are left out/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Job status"), "");

    expect(router.state.location.search).toBe("?review_status=pending");
    expect(fetchStub).toHaveBeenCalledWith(
      "/worklist?limit=50&review_status=pending",
      expect.anything(),
    );
  });

  it("reads the filters from the address and ignores a value it does not know", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /worklist?limit=50&review_status=approved": page([DRAFTED]),
    });

    renderAt("/app/worklist?status=lost&review_status=approved");

    expect(await screen.findByText("letter-9.txt")).toBeInTheDocument();
    expect(screen.getByLabelText("Job status")).toHaveValue("");
    expect(screen.getByLabelText("Review status")).toHaveValue("approved");
  });

  it("offers the upload at the top when there are jobs", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: page([DRAFTED]),
    });
    renderAt("/app/worklist");

    expect(await screen.findByText("letter-9.txt")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a denial letter" })).toHaveAttribute(
      "href",
      "/app/upload",
    );
  });

  it("offers the upload on an empty list", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, ADMIN), [FIRST_PAGE]: page([]) });
    renderAt("/app/worklist");

    expect(await screen.findByText(/No jobs here yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a denial letter" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload your first letter" })).toHaveAttribute(
      "href",
      "/app/upload",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("offers the upload to a viewer too", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, VIEWER), [FIRST_PAGE]: page([]) });
    renderAt("/app/worklist");

    expect(await screen.findByText(/No jobs here yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a denial letter" })).toBeInTheDocument();
  });

  it("shows an error when the first page fails", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: () => jsonResponse(500, { detail: "boom" }),
    });
    renderAt("/app/worklist");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sends the user to login when the session has ended", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: () => jsonResponse(401, { detail: "not logged in" }),
    });
    const { router } = renderAt("/app/worklist");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
  });

  it("asks for the page only once when the session has ended", async () => {
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      [FIRST_PAGE]: () => jsonResponse(401, { detail: "not logged in" }),
    });
    renderAt("/app/worklist");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    const pageCalls = fetchStub.mock.calls.filter(
      ([input]) => String(input) === FIRST_PAGE.slice(4),
    );
    expect(pageCalls).toHaveLength(1);
  });
});
