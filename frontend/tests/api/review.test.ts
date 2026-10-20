import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../src/api/client";
import { approveDraft, fetchJobDetail, rejectDraft, saveDraft } from "../../src/api/review";
import type { AppealLetter, JobDetail } from "../../src/api/types";
import { jsonResponse, sentBody, stubApi } from "../helpers";

const LETTER: AppealLetter = {
  paragraphs: [
    { kind: "other", text: "Made-up opening paragraph.", citations: [] },
    {
      kind: "policy",
      text: "Made-up policy paragraph.",
      citations: [
        {
          source: "cms_ncd",
          source_document_id: "ncd-1",
          chunk_index: 0,
          text_sha256: "a".repeat(64),
          quote: "made-up quote from the evidence",
        },
      ],
    },
  ],
};

const DETAIL: JobDetail = {
  job_id: 7,
  file_name: "letter.txt",
  file_type: "text",
  uploaded_at: "2026-10-20T10:00:00Z",
  uploaded_by: "reviewer@example.com",
  status: "done",
  failure: null,
  end_state: "needs_review",
  started_at: "2026-10-20T10:00:01Z",
  finished_at: "2026-10-20T10:00:14Z",
  cost_usd: "0.001141",
  text: "Made-up denial letter.",
  extraction: null,
  trace: null,
  draft: {
    prompt_version: "v3",
    model_name: "made-up-model",
    original: LETTER,
    latest_version: 0,
    latest: LETTER,
    review_status: "pending",
    reviewed_by: null,
    reviewed_at: null,
    reject_reason: null,
    approved_version: null,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("expected an ApiError");
}

describe("fetchJobDetail", () => {
  it("reads the detail of the job, with the cost kept as a decimal string", async () => {
    stubApi({ "GET /jobs/7/detail": () => jsonResponse(200, DETAIL) });

    const detail = await fetchJobDetail(7);

    expect(detail).toEqual(DETAIL);
    expect(detail.cost_usd).toBe("0.001141");
  });

  it("raises not_found for a job of another account", async () => {
    stubApi({ "GET /jobs/8/detail": () => jsonResponse(404, { detail: "job not found" }) });

    expect((await caught(fetchJobDetail(8))).kind).toBe("not_found");
  });
});

describe("saveDraft", () => {
  it("sends the whole letter as JSON with the version it was based on", async () => {
    const fetchStub = stubApi({
      "PUT /jobs/7/draft": () => jsonResponse(200, { job_id: 7, version: 1 }),
    });

    await expect(saveDraft(7, LETTER, 0)).resolves.toEqual({ job_id: 7, version: 1 });

    expect(sentBody(fetchStub, "PUT /jobs/7/draft")).toEqual({
      letter: LETTER,
      based_on_version: 0,
    });
    expect(fetchStub.mock.calls[0]?.[1]?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("raises conflict when someone saved a newer version", async () => {
    stubApi({
      "PUT /jobs/7/draft": () =>
        jsonResponse(409, { detail: "the draft has a newer version; reload it" }),
    });

    const error = await caught(saveDraft(7, LETTER, 0));

    expect(error.kind).toBe("conflict");
    expect(error.message).toBe("the draft has a newer version; reload it");
  });

  it("raises invalid with the letter problems of a refused letter", async () => {
    stubApi({
      "PUT /jobs/7/draft": () =>
        jsonResponse(422, {
          detail: {
            message: "the letter did not pass the checks",
            problems: [
              { kind: "policy_without_citation", paragraph_index: 1, citation_index: null },
            ],
          },
        }),
    });

    const error = await caught(saveDraft(7, LETTER, 0));

    expect(error.kind).toBe("invalid");
    expect(error.letterProblems).toEqual([
      { kind: "policy_without_citation", paragraph_index: 1, citation_index: null },
    ]);
  });
});

describe("approveDraft", () => {
  it("sends only the version approved", async () => {
    const fetchStub = stubApi({
      "POST /jobs/7/draft/approve": () =>
        jsonResponse(200, { job_id: 7, review_status: "approved" }),
    });

    await expect(approveDraft(7, 2)).resolves.toEqual({ job_id: 7, review_status: "approved" });

    expect(sentBody(fetchStub, "POST /jobs/7/draft/approve")).toEqual({ version: 2 });
  });

  it("raises conflict when the draft was already decided", async () => {
    stubApi({
      "POST /jobs/7/draft/approve": () =>
        jsonResponse(409, { detail: "the draft was already approved or rejected" }),
    });

    expect((await caught(approveDraft(7, 2))).kind).toBe("conflict");
  });
});

describe("rejectDraft", () => {
  it("sends only the rubric reason", async () => {
    const fetchStub = stubApi({
      "POST /jobs/7/draft/reject": () =>
        jsonResponse(200, { job_id: 7, review_status: "rejected" }),
    });

    await expect(rejectDraft(7, "citation_does_not_fit")).resolves.toEqual({
      job_id: 7,
      review_status: "rejected",
    });

    expect(sentBody(fetchStub, "POST /jobs/7/draft/reject")).toEqual({
      reason: "citation_does_not_fit",
    });
  });

  it("raises forbidden for a viewer", async () => {
    stubApi({
      "POST /jobs/7/draft/reject": () => jsonResponse(403, { detail: "not allowed" }),
    });

    expect((await caught(rejectDraft(7, "other"))).kind).toBe("forbidden");
  });
});
