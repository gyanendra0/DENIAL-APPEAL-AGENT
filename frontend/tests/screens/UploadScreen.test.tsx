import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JobResponse } from "../../src/api/types";
import { UPLOAD_MAX_BYTES } from "../../src/jobs/uploadCheck";
import { JOB_POLL_MS } from "../../src/jobs/useJob";
import {
  ADMIN,
  INDIVIDUAL,
  REVIEWER,
  VIEWER,
  jsonResponse,
  notifications,
  renderAt,
  stubApi,
  NO_RECENT,
} from "../helpers";
import type { Answer } from "../helpers";

const QUEUED: JobResponse = {
  job_id: 7,
  status: "queued",
  failure: null,
  end_state: null,
  created_at: "2026-10-17T10:00:00Z",
  started_at: null,
  finished_at: null,
};
const RUNNING: JobResponse = { ...QUEUED, status: "running", started_at: "2026-10-17T10:00:01Z" };
const DONE: JobResponse = {
  ...RUNNING,
  status: "done",
  end_state: "needs_review",
  finished_at: "2026-10-17T10:00:20Z",
};

// Answers each call with the next job in the list, and the last one from then on.
function inTurn(jobs: JobResponse[]): Answer {
  let call = 0;
  return () => jsonResponse(200, jobs[Math.min(call++, jobs.length - 1)]);
}

// The checkbox every upload needs: the letter holds no real personal details.
const CONFIRMATION =
  "I confirm this letter holds no real names, member ids, claim numbers or dates of birth";

function letter(): File {
  return new File(["Made-up denial letter"], "letter.txt", { type: "text/plain" });
}

function jobCalls(fetchStub: ReturnType<typeof stubApi>): number {
  return fetchStub.mock.calls.filter(([path]) => path === "/jobs/7").length;
}

async function nextPoll() {
  await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));
}

afterEach(() => {
  vi.useRealTimers();
});

function dropZone(): HTMLElement {
  const zone = screen.getByLabelText("Denial letter").closest(".dropzone");
  if (!(zone instanceof HTMLElement)) {
    throw new Error("no drop zone");
  }
  return zone;
}

describe("UploadScreen", () => {
  it("says when the watched job fails, and Open goes to its screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed: JobResponse = { ...RUNNING, status: "failed", failure: "extraction_failed" };
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "GET /jobs/7": inTurn([RUNNING, failed]),
      "GET /jobs/7/detail": () => jsonResponse(404, { detail: "job not found" }),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { router } = renderAt("/app/upload?job=7");
    await screen.findByRole("status");

    await nextPoll();

    expect(notifications().getByText("The letter could not be processed")).toBeInTheDocument();
    expect(
      notifications().getByText("Failed: the letter could not be read into fields"),
    ).toBeInTheDocument();
    await user.click(notifications().getByRole("button", { name: "Open" }));
    expect(router.state.location.pathname).toBe("/app/jobs/7");
    expect(
      notifications().queryByText("The letter could not be processed"),
    ).not.toBeInTheDocument();
  });

  it("says a watched job finished another way, with its end in words", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const stopped: JobResponse = { ...DONE, end_state: "stopped_by_rules" };
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "GET /jobs/7": inTurn([RUNNING, stopped]),
    });
    renderAt("/app/upload?job=7");
    await screen.findByRole("status");

    await nextPoll();

    expect(notifications().getByText("Job 7 finished")).toBeInTheDocument();
    expect(
      notifications().getByText("Stopped by the rules (deadline or amount); no draft was written"),
    ).toBeInTheDocument();
  });

  it("gives no notification for a job already finished when the screen opens", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "GET /jobs/7": () => jsonResponse(200, DONE),
    });

    renderAt("/app/upload?job=7");

    expect(await screen.findByRole("status")).toHaveTextContent("Draft ready for review");
    expect(notifications().queryByText("Draft ready for review")).not.toBeInTheDocument();
  });

  it("takes a dropped letter like a picked one, and refuses a dropped wrong file", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, REVIEWER) });
    const user = userEvent.setup();
    renderAt("/app/upload");
    await screen.findByLabelText("Denial letter");
    await user.click(screen.getByLabelText(CONFIRMATION));

    const image = new File(["made-up"], "scan.png", { type: "image/png" });
    fireEvent.drop(dropZone(), { dataTransfer: { files: [image] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Only a .txt file or a PDF can be uploaded.",
    );
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();

    fireEvent.drop(dropZone(), { dataTransfer: { files: [letter()] } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("letter.txt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("turns Upload off again when the chosen letter is removed", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, REVIEWER) });
    const user = userEvent.setup();
    renderAt("/app/upload");
    await user.upload(await screen.findByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(screen.queryByText("letter.txt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("says the limit in the hint under the drop zone", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, REVIEWER) });
    renderAt("/app/upload");

    expect(await screen.findByLabelText("Denial letter")).toHaveAccessibleDescription(
      ".txt or .pdf with text, up to 1 MB",
    );
  });

  it("uploads a letter, polls the job until it ends and then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "POST /uploads": () => jsonResponse(202, { job_id: 7, status: "queued" }),
      "GET /jobs/7": inTurn([QUEUED, RUNNING, DONE]),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { router } = renderAt("/app/upload");

    const button = await screen.findByRole("button", { name: "Upload" });
    expect(button).toBeDisabled();
    await user.upload(screen.getByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    expect(router.state.location.search).toBe("?job=7");
    expect(notifications().getByText("Letter uploaded")).toBeInTheDocument();
    expect(notifications().getByText("Job 7 is being read.")).toBeInTheDocument();
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Reading the letter and drafting");
    expect(notifications().queryByText("Draft ready for review")).not.toBeInTheDocument();
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Draft ready for review");
    expect(notifications().getByText("Draft ready for review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the worklist" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the denial" })).toHaveAttribute(
      "href",
      "/app/jobs/7",
    );

    await nextPoll();
    await nextPoll();
    expect(jobCalls(fetchStub)).toBe(3);
  });

  it("refreshes the sidebar's Recent list after the upload and once the job ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "POST /uploads": () => jsonResponse(202, { job_id: 7, status: "queued" }),
      "GET /jobs/7": inTurn([QUEUED, DONE]),
    });
    const recentCalls = () =>
      fetchStub.mock.calls.filter(([path]) => path === "/worklist?limit=10").length;
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderAt("/app/upload");

    const button = await screen.findByRole("button", { name: "Upload" });
    await waitFor(() => expect(recentCalls()).toBe(1));
    await user.upload(screen.getByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    await waitFor(() => expect(recentCalls()).toBe(2));
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Draft ready for review");
    await waitFor(() => expect(recentCalls()).toBe(3));

    await nextPoll();
    expect(recentCalls()).toBe(3);
  });

  it("shows the job at once, without waiting for the Recent list to be read again", async () => {
    let recentCalls = 0;
    stubApi({
      "GET /worklist?limit=10": () =>
        recentCalls++ === 0
          ? jsonResponse(200, { items: [], next_before: null })
          : new Promise<Response>(() => undefined),
      "GET /me": () => jsonResponse(200, REVIEWER),
      "POST /uploads": () => jsonResponse(202, { job_id: 7, status: "queued" }),
      "GET /jobs/7": () => jsonResponse(200, QUEUED),
    });
    const user = userEvent.setup();
    renderAt("/app/upload");

    await user.upload(await screen.findByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    expect(recentCalls).toBe(2);
  });

  it("shows why a job failed", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /jobs/7": () =>
        jsonResponse(200, { ...RUNNING, status: "failed", failure: "budget_exceeded" }),
    });

    renderAt("/app/upload?job=7");

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Failed: the monthly model budget is used up",
    );
  });

  it("shows the job named in the address after a reload", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /jobs/7": () => jsonResponse(200, DONE),
    });

    renderAt("/app/upload?job=7");

    expect(await screen.findByRole("status")).toHaveTextContent("Draft ready for review");
    expect(screen.getByRole("heading", { name: "Job 7" })).toBeInTheDocument();
  });

  it("ignores a job id in the address that is not a number", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, ADMIN) });

    renderAt("/app/upload?job=7x");

    expect(await screen.findByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Job status" })).not.toBeInTheDocument();
  });

  it("says not found for a job of another account", async () => {
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /jobs/7": () => jsonResponse(404, { detail: "job not found" }),
    });

    renderAt("/app/upload?job=7");

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(jobCalls(fetchStub)).toBe(1);
  });

  it.each([
    [413, { detail: "the file is larger than the upload limit" }, "larger than the upload limit"],
    [422, { detail: "the file is empty" }, "The file is empty."],
    [403, { detail: "role not allowed" }, "Your role cannot do this."],
  ])("shows the refusal of an upload answered %i", async (status, body, message) => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "POST /uploads": () => jsonResponse(status, body),
    });
    const user = userEvent.setup();
    renderAt("/app/upload");

    await user.upload(await screen.findByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it.each([
    ["a wrong type", new File(["made-up"], "scan.png", { type: "image/png" }), "Only a .txt"],
    [
      "a file over the limit",
      new File(["x".repeat(UPLOAD_MAX_BYTES + 1)], "letter.txt", { type: "text/plain" }),
      "larger than the upload limit",
    ],
  ])("refuses %s before sending it", async (_label, file, message) => {
    const fetchStub = stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, ADMIN) });
    // As if "All files" were chosen in the file picker, past the `accept` hint.
    const user = userEvent.setup({ applyAccept: false });
    renderAt("/app/upload");

    await user.upload(await screen.findByLabelText("Denial letter"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
    expect(fetchStub.mock.calls.filter(([path]) => path === "/uploads")).toHaveLength(0);
  });

  it("clears the refusal when a good file is picked after a wrong one", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, ADMIN) });
    const user = userEvent.setup({ applyAccept: false });
    renderAt("/app/upload");
    const input = await screen.findByLabelText("Denial letter");

    await user.upload(input, new File(["made-up"], "scan.png", { type: "image/png" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.upload(input, letter());
    await user.click(screen.getByLabelText(CONFIRMATION));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("goes to login when the session ends while polling", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /jobs/7": () => jsonResponse(401, { detail: "not logged in" }),
    });

    const { router } = renderAt("/app/upload?job=7");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
  });

  it("keeps Upload off until the no-personal-data box is ticked, then sends the confirmation", async () => {
    const fetchStub = stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, REVIEWER),
      "POST /uploads": () => jsonResponse(202, { job_id: 7, status: "queued" }),
      "GET /jobs/7": () => jsonResponse(200, QUEUED),
    });
    const user = userEvent.setup();
    renderAt("/app/upload");

    expect(await screen.findByLabelText(CONFIRMATION)).not.toBeChecked();
    await user.upload(screen.getByLabelText("Denial letter"), letter());
    const button = screen.getByRole("button", { name: "Upload" });
    expect(button).toBeDisabled();
    await user.click(screen.getByLabelText(CONFIRMATION));
    expect(button).toBeEnabled();
    await user.click(screen.getByLabelText(CONFIRMATION));
    expect(button).toBeDisabled();
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    const upload = fetchStub.mock.calls.find(([path]) => path === "/uploads");
    expect((upload?.[1]?.body as FormData).get("no_personal_data")).toBe("true");
  });

  it.each([
    [
      "this account has used its uploads for this month; try again next month",
      "This account has used its uploads for this month; try again next month.",
    ],
    [
      "today's uploads for personal accounts are used up; try again tomorrow",
      "Today's uploads for personal accounts are used up; try again tomorrow.",
    ],
  ])("shows the API's sentence when a cap refuses the upload: %s", async (detail, message) => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, INDIVIDUAL),
      "POST /uploads": () =>
        new Response(JSON.stringify({ detail }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "3600" },
        }),
    });
    const user = userEvent.setup();
    renderAt("/app/upload");

    await user.upload(await screen.findByLabelText("Denial letter"), letter());
    await user.click(screen.getByLabelText(CONFIRMATION));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("region", { name: "Job status" })).not.toBeInTheDocument();
  });

  it("lets a viewer upload too", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, VIEWER) });

    renderAt("/app/upload");

    expect(await screen.findByLabelText("Denial letter")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
