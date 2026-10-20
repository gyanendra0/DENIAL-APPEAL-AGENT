import { useState } from "react";

import type { DraftDetail, DraftRejectReason } from "../api/types";
import { REJECT_REASON_TEXT } from "./detailText";
import { ReviewError } from "./ReviewError";
import { useApproveDraft, useRejectDraft } from "./useDraftActions";

const REJECT_REASONS = Object.keys(REJECT_REASON_TEXT) as DraftRejectReason[];

// Approve or reject a pending draft. Both are final, so each asks for a confirm first. Approve
// sends the latest version and waits while edits are unsaved; reject takes one rubric reason.
export function DraftActions({
  jobId,
  draft,
  editing,
  onReload,
}: {
  jobId: number;
  draft: DraftDetail;
  editing: boolean;
  onReload: () => void;
}) {
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState<DraftRejectReason | null>(null);
  const approve = useApproveDraft(jobId);
  const reject = useRejectDraft(jobId);
  const pending = approve.isPending || reject.isPending;

  function open(next: "approve" | "reject" | null) {
    approve.reset();
    reject.reset();
    setReason(null);
    setConfirming(next);
  }

  return (
    <section aria-label="Review actions" className="review-actions">
      {confirming === null && (
        <div className="links">
          <button type="button" disabled={editing} onClick={() => open("approve")}>
            Approve
          </button>
          <button type="button" className="secondary" onClick={() => open("reject")}>
            Reject
          </button>
        </div>
      )}
      {confirming === null && editing && (
        <p className="muted">Save or cancel your edits before approving.</p>
      )}
      {confirming === "approve" && (
        <div className="status-box">
          <p>
            Approving version {draft.latest_version} marks this letter ready for you to send.
            Nothing is sent by this system.
          </p>
          <div className="links">
            <button
              type="button"
              disabled={pending}
              onClick={() => approve.mutate(draft.latest_version)}
            >
              {approve.isPending ? "Approving…" : "Yes, approve"}
            </button>
            <button type="button" className="secondary" onClick={() => open(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {confirming === "reject" && (
        <div className="status-box">
          <fieldset>
            <legend>Why is this draft rejected?</legend>
            {REJECT_REASONS.map((value) => (
              <label key={value} className="choice">
                <input
                  type="radio"
                  name="reject-reason"
                  value={value}
                  checked={reason === value}
                  onChange={() => setReason(value)}
                />
                {REJECT_REASON_TEXT[value]}
              </label>
            ))}
          </fieldset>
          <p>Rejecting is final.</p>
          <div className="links">
            <button
              type="button"
              disabled={reason === null || pending}
              onClick={() => {
                if (reason !== null) {
                  reject.mutate(reason);
                }
              }}
            >
              {reject.isPending ? "Rejecting…" : "Reject the draft"}
            </button>
            <button type="button" className="secondary" onClick={() => open(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <ReviewError
        error={approve.error ?? reject.error}
        onReload={() => {
          open(null);
          onReload();
        }}
        staleText="Someone saved a newer version. Reload to see it before approving."
      />
    </section>
  );
}
