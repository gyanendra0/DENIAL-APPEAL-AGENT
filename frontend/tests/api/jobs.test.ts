import { describe, expect, it } from "vitest";

import { fetchJob, uploadLetter } from "../../src/api/jobs";
import { jsonResponse, stubApi } from "../helpers";

describe("uploadLetter", () => {
  it("sends the file as the multipart field `file` and lets the browser set the headers", async () => {
    const fetchStub = stubApi({
      "POST /uploads": () => jsonResponse(202, { job_id: 7, status: "queued" }),
    });
    const file = new File(["Made-up denial letter"], "letter.txt", { type: "text/plain" });

    await expect(uploadLetter(file)).resolves.toEqual({ job_id: 7, status: "queued" });

    const init = fetchStub.mock.calls[0]?.[1];
    expect(init?.headers).toBeUndefined();
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBe(file);
  });
});

describe("fetchJob", () => {
  it("reads the job by its id", async () => {
    const job = {
      job_id: 7,
      status: "running",
      failure: null,
      end_state: null,
      created_at: "2026-10-17T10:00:00Z",
      started_at: "2026-10-17T10:00:01Z",
      finished_at: null,
    };
    stubApi({ "GET /jobs/7": () => jsonResponse(200, job) });

    await expect(fetchJob(7)).resolves.toEqual(job);
  });
});
