import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { JobDetail } from "../../src/api/types";
import { jsonResponse, sentBody } from "../helpers";
import { DETAIL, DRAFT, EDITED, ORIGINAL } from "./fixtures";
import { detailReads, findDraft, openDetail } from "./reviewHelpers";

const AFTER_SAVE: JobDetail = { ...DETAIL, draft: { ...DRAFT, latest_version: 2, latest: EDITED } };

async function startEditing() {
  const user = userEvent.setup();
  const draft = await findDraft();
  await user.click(draft.getByRole("button", { name: "Edit" }));
  return { user, editor: within(screen.getByRole("form", { name: "Edit the draft" })) };
}

describe("DraftEditor", () => {
  it("saves the whole edited letter with the version it started from", async () => {
    const fetchStub = openDetail([DETAIL, AFTER_SAVE], {
      "PUT /jobs/9/draft": () => jsonResponse(200, { job_id: 9, version: 2 }),
    });
    const { user, editor } = await startEditing();

    const save = editor.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    const text = editor.getByRole("textbox", { name: "Text of paragraph 1" });
    await user.clear(text);
    await user.type(text, "Please review this made-up claim again.");
    await user.click(save);

    expect(await screen.findByText(/Version 2, edited by a person\./)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Edit the draft" })).not.toBeInTheDocument();
    expect(sentBody(fetchStub, "PUT /jobs/9/draft")).toEqual({
      letter: EDITED,
      based_on_version: 1,
    });
    expect(detailReads(fetchStub)).toBe(2);
  });

  it("removes a paragraph and a citation before saving", async () => {
    const fetchStub = openDetail([DETAIL], {
      "PUT /jobs/9/draft": () => jsonResponse(200, { job_id: 9, version: 2 }),
    });
    const { user, editor } = await startEditing();

    await user.click(editor.getByRole("button", { name: "Remove citation 1 of paragraph 2" }));
    await user.click(editor.getByRole("button", { name: "Remove paragraph 1" }));
    // The last paragraph cannot go: a letter has at least one.
    expect(editor.getByRole("button", { name: "Remove paragraph 1" })).toBeDisabled();
    await user.click(editor.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(sentBody(fetchStub, "PUT /jobs/9/draft")).toEqual({
        letter: { paragraphs: [{ ...ORIGINAL.paragraphs[1], citations: [] }] },
        based_on_version: 1,
      });
    });
  });

  it("drops the changes on cancel", async () => {
    const fetchStub = openDetail([DETAIL]);
    const { user, editor } = await startEditing();

    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 1" }), " typed");
    await user.click(editor.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("form", { name: "Edit the draft" })).not.toBeInTheDocument();
    expect(screen.getByText("We ask you to look again at this made-up claim.")).toBeInTheDocument();
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("shows a refused letter's problems beside their paragraph, never the typed text", async () => {
    openDetail([DETAIL], {
      "PUT /jobs/9/draft": () =>
        jsonResponse(422, {
          detail: {
            message: "the letter did not pass the checks",
            problems: [
              { kind: "policy_without_citation", paragraph_index: 1, citation_index: null },
            ],
          },
        }),
    });
    const { user, editor } = await startEditing();

    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 2" }), " SECRET-TYPED");
    await user.click(editor.getByRole("button", { name: "Remove citation 1 of paragraph 2" }));
    await user.click(editor.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The letter did not pass the checks. Each problem is shown beside its paragraph.",
    );
    const problem = editor.getByText(
      "Paragraph 2: a policy paragraph needs at least one citation.",
    );
    expect(problem.closest("li")).toContainElement(
      editor.getByRole("textbox", { name: "Text of paragraph 2" }),
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("SECRET-TYPED");

    // Any change moves the positions, so the problems go.
    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 1" }), "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(editor.queryByText(/Paragraph 2: a policy paragraph/)).not.toBeInTheDocument();
  });

  it("offers a reload when someone saved a newer version", async () => {
    const fetchStub = openDetail([DETAIL, AFTER_SAVE], {
      "PUT /jobs/9/draft": () =>
        jsonResponse(409, { detail: "the draft has a newer version; reload it" }),
    });
    const { user, editor } = await startEditing();

    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 1" }), " more");
    await user.click(editor.getByRole("button", { name: "Save" }));

    const alert = within(await screen.findByRole("alert"));
    expect(
      alert.getByText(
        "Someone saved a newer version. Reload to see it; your changes here will be lost.",
      ),
    ).toBeInTheDocument();
    expect(detailReads(fetchStub)).toBe(1);
    await user.click(alert.getByRole("button", { name: "Reload" }));

    expect(await screen.findByText(/Version 2, edited by a person\./)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Edit the draft" })).not.toBeInTheDocument();
  });

  it("reads the draft again when it was already approved or rejected", async () => {
    const approved: JobDetail = {
      ...DETAIL,
      draft: {
        ...DRAFT,
        review_status: "approved",
        reviewed_by: "rev@example.test",
        reviewed_at: "2026-10-18T09:00:00Z",
        approved_version: 1,
      },
    };
    const fetchStub = openDetail([DETAIL, approved], {
      "PUT /jobs/9/draft": () =>
        jsonResponse(409, { detail: "the draft was already approved or rejected" }),
    });
    const { user, editor } = await startEditing();

    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 1" }), " more");
    await user.click(editor.getByRole("button", { name: "Save" }));

    // The refetched draft is final, so the editor and the actions go.
    expect(await screen.findByText("Approved")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Edit the draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(detailReads(fetchStub)).toBe(2);
  });

  it("shows a 403 in plain words", async () => {
    openDetail([DETAIL], {
      "PUT /jobs/9/draft": () => jsonResponse(403, { detail: "forbidden" }),
    });
    const { user, editor } = await startEditing();

    await user.type(editor.getByRole("textbox", { name: "Text of paragraph 1" }), " more");
    await user.click(editor.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Your role cannot do this.");
  });
});
