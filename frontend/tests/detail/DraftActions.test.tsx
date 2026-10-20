import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { JobDetail } from "../../src/api/types";
import { ADMIN, VIEWER, jsonResponse, sentBody } from "../helpers";
import { DETAIL, DRAFT, EDITED } from "./fixtures";
import { detailReads, findDraft, openDetail } from "./reviewHelpers";

const APPROVED: JobDetail = {
  ...DETAIL,
  draft: {
    ...DRAFT,
    latest_version: 1,
    latest: EDITED,
    review_status: "approved",
    reviewed_by: "rev@example.test",
    reviewed_at: "2026-10-18T09:00:00Z",
    approved_version: 1,
  },
};

const REJECTED: JobDetail = {
  ...DETAIL,
  draft: {
    ...DRAFT,
    review_status: "rejected",
    reviewed_by: "rev@example.test",
    reviewed_at: "2026-10-18T09:00:00Z",
    reject_reason: "wrong_fact",
  },
};

describe("DraftActions", () => {
  it("approves the latest version after a confirm that says nothing is sent", async () => {
    const edited: JobDetail = { ...DETAIL, draft: { ...DRAFT, latest_version: 1, latest: EDITED } };
    const fetchStub = openDetail([edited, APPROVED], {
      "POST /jobs/9/draft/approve": () =>
        jsonResponse(200, { job_id: 9, review_status: "approved" }),
    });
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Approve" }));
    expect(
      draft.getByText(
        "Approving version 1 marks this letter ready for you to send. Nothing is sent by this system.",
      ),
    ).toBeInTheDocument();
    await user.click(draft.getByRole("button", { name: "Yes, approve" }));

    expect(await screen.findByText("Approved version")).toBeInTheDocument();
    expect(sentBody(fetchStub, "POST /jobs/9/draft/approve")).toEqual({ version: 1 });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("sends nothing when a confirm is cancelled", async () => {
    const fetchStub = openDetail([DETAIL]);
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Approve" }));
    await user.click(draft.getByRole("button", { name: "Cancel" }));
    await user.click(draft.getByRole("button", { name: "Reject" }));
    await user.click(draft.getByRole("radio", { name: "Another reason" }));
    await user.click(draft.getByRole("button", { name: "Cancel" }));

    expect(draft.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("does not approve while edits are unsaved", async () => {
    openDetail([DETAIL]);
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Edit" }));

    expect(draft.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(draft.getByText("Save or cancel your edits before approving.")).toBeInTheDocument();
  });

  it("rejects with the one rubric reason chosen", async () => {
    const fetchStub = openDetail([DETAIL, REJECTED], {
      "POST /jobs/9/draft/reject": () =>
        jsonResponse(200, { job_id: 9, review_status: "rejected" }),
    });
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Reject" }));
    const confirm = draft.getByRole("button", { name: "Reject the draft" });
    expect(confirm).toBeDisabled();
    expect(draft.getAllByRole("radio")).toHaveLength(5);
    await user.click(draft.getByRole("radio", { name: "A fact differs from the denial letter" }));
    await user.click(confirm);

    expect(await screen.findByText("Rejected")).toBeInTheDocument();
    expect(sentBody(fetchStub, "POST /jobs/9/draft/reject")).toEqual({ reason: "wrong_fact" });
    expect(detailReads(fetchStub)).toBe(2);
  });

  it("lists the problems of a letter that no longer passes the checks", async () => {
    openDetail([DETAIL], {
      "POST /jobs/9/draft/approve": () =>
        jsonResponse(422, {
          detail: {
            message: "the letter did not pass the checks",
            problems: [{ kind: "chunk_not_found", paragraph_index: 1, citation_index: 0 }],
          },
        }),
    });
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Approve" }));
    await user.click(draft.getByRole("button", { name: "Yes, approve" }));

    const alert = within(await screen.findByRole("alert"));
    expect(alert.getByText("The letter did not pass the checks.")).toBeInTheDocument();
    expect(
      alert.getByText(
        "Paragraph 2, citation 1: the cited evidence no longer exists or has changed.",
      ),
    ).toBeInTheDocument();
  });

  it("offers a reload when the approved version is no longer the latest", async () => {
    const edited: JobDetail = { ...DETAIL, draft: { ...DRAFT, latest_version: 1, latest: EDITED } };
    const fetchStub = openDetail([DETAIL, edited], {
      "POST /jobs/9/draft/approve": () =>
        jsonResponse(409, { detail: "the draft has a newer version; reload it" }),
    });
    const user = userEvent.setup();
    const draft = await findDraft();

    await user.click(draft.getByRole("button", { name: "Approve" }));
    await user.click(draft.getByRole("button", { name: "Yes, approve" }));
    const alert = within(await screen.findByRole("alert"));
    expect(
      alert.getByText("Someone saved a newer version. Reload to see it before approving."),
    ).toBeInTheDocument();
    await user.click(alert.getByRole("button", { name: "Reload" }));

    expect(await screen.findByText(/Version 1, edited by a person\./)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(detailReads(fetchStub)).toBe(2);
  });

  it("shows no controls to a viewer", async () => {
    openDetail([DETAIL], {}, VIEWER);
    const draft = await findDraft();

    expect(draft.getByText("Waiting for review")).toBeInTheDocument();
    for (const name of ["Edit", "Approve", "Reject"]) {
      expect(draft.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("shows the controls to an admin", async () => {
    openDetail([DETAIL], {}, ADMIN);
    const draft = await findDraft();

    expect(draft.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("shows no controls on a draft already rejected", async () => {
    openDetail([REJECTED]);
    const draft = await findDraft();

    expect(draft.getByText("Rejected")).toBeInTheDocument();
    expect(draft.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});
