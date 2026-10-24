import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JobResponse } from "../../src/api/types";
import { UPLOAD_MAX_BYTES } from "../../src/jobs/uploadCheck";
import { JOB_POLL_MS } from "../../src/jobs/useJob";
import { ADMIN, REVIEWER, VIEWER, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";
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

describe("UploadScreen", () => {
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
    await user.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    expect(router.state.location.search).toBe("?job=7");
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Reading the letter and drafting");
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Draft ready for review");
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
    await user.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting to start");
    await waitFor(() => expect(recentCalls()).toBe(2));
    await nextPoll();
    expect(screen.getByRole("status")).toHaveTextContent("Draft ready for review");
    await waitFor(() => expect(recentCalls()).toBe(3));

    await nextPoll();
    expect(recentCalls()).toBe(3);
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

  it("shows a viewer that the role cannot upload, without the form", async () => {
    stubApi({ ...NO_RECENT, "GET /me": () => jsonResponse(200, VIEWER) });

    renderAt("/app/upload");

    expect(await screen.findByRole("alert")).toHaveTextContent("Your role cannot do this.");
    expect(screen.queryByLabelText("Denial letter")).not.toBeInTheDocument();
  });
});
