import { useState } from "react";

import type { AppealLetter, Citation, DraftDetail, EndState } from "../api/types";
import { END_STATE_TEXT, REVIEW_STATUS_TEXT } from "../screens/jobText";
import { PARAGRAPH_KIND_TEXT, REJECT_REASON_TEXT, SOURCE_TEXT } from "./detailText";

const REVIEWED_AT = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

// The draft of the appeal letter: the latest version by default, the model's own draft on
// request (it is kept unchanged), and where its review stands.
export function DraftView({
  draft,
  endState,
}: {
  draft: DraftDetail | null;
  endState: EndState | null;
}) {
  const [showOriginal, setShowOriginal] = useState(false);

  if (draft === null) {
    return (
      <section aria-label="Draft">
        <h2>Draft</h2>
        <p>No draft was written.{endState !== null && ` ${END_STATE_TEXT[endState]}.`}</p>
      </section>
    );
  }

  const edited = draft.latest_version > 1;
  return (
    <section aria-label="Draft">
      <h2>Draft</h2>
      <ReviewStatus draft={draft} />
      <p className="muted">
        {showOriginal
          ? `The model's draft (${draft.model_name}, prompt ${draft.prompt_version}).`
          : `Version ${draft.latest_version}${edited ? ", edited by a person" : ", not edited"}.`}{" "}
        Nothing is sent to an insurer.
      </p>
      {edited && (
        <button type="button" className="secondary" onClick={() => setShowOriginal(!showOriginal)}>
          {showOriginal ? "Show the latest version" : "Show the model's draft"}
        </button>
      )}
      <LetterView letter={showOriginal ? draft.original : draft.latest} />
    </section>
  );
}

function ReviewStatus({ draft }: { draft: DraftDetail }) {
  return (
    <dl className="facts">
      <dt>Review</dt>
      <dd>{REVIEW_STATUS_TEXT[draft.review_status]}</dd>
      {draft.reviewed_by !== null && (
        <>
          <dt>By</dt>
          <dd>{draft.reviewed_by}</dd>
        </>
      )}
      {draft.reviewed_at !== null && (
        <>
          <dt>On</dt>
          <dd>{REVIEWED_AT.format(new Date(draft.reviewed_at))}</dd>
        </>
      )}
      {draft.reject_reason !== null && (
        <>
          <dt>Reason</dt>
          <dd>{REJECT_REASON_TEXT[draft.reject_reason]}</dd>
        </>
      )}
      {draft.approved_version !== null && (
        <>
          <dt>Approved version</dt>
          <dd>{draft.approved_version}</dd>
        </>
      )}
    </dl>
  );
}

export function LetterView({ letter }: { letter: AppealLetter }) {
  return (
    <ol className="letter">
      {letter.paragraphs.map((paragraph, index) => (
        <li key={index}>
          <p className="muted">{PARAGRAPH_KIND_TEXT[paragraph.kind]}</p>
          <p className="pre-wrap">{paragraph.text}</p>
          {paragraph.citations.length > 0 && (
            <ul className="citations">
              {paragraph.citations.map((citation, citationIndex) => (
                <li key={citationIndex}>
                  <CitationView citation={citation} />
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

// A citation's label; its exact quote opens in place on click.
function CitationView({ citation }: { citation: Citation }) {
  return (
    <details>
      <summary>
        {SOURCE_TEXT[citation.source]} {citation.source_document_id}, chunk {citation.chunk_index}
      </summary>
      <blockquote className="pre-wrap">{citation.quote}</blockquote>
    </details>
  );
}
