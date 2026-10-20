import { useState } from "react";

import { ApiError } from "../api/client";
import type { DraftDetail, LetterParagraph } from "../api/types";
import { PARAGRAPH_KIND_TEXT, SOURCE_TEXT, describeLetterProblem } from "./detailText";
import { ReviewError } from "./ReviewError";
import { useSaveDraft } from "./useDraftActions";

// A paragraph being edited, with a key that stays the same when an earlier one is removed.
interface EditedParagraph {
  key: number;
  paragraph: LetterParagraph;
}

// Edits the latest version: change a paragraph's text, remove a paragraph or a citation. Kinds and
// quotes are read-only; the API refuses any other change too. Save sends the whole letter.
export function DraftEditor({
  jobId,
  draft,
  onClose,
  onReload,
}: {
  jobId: number;
  draft: DraftDetail;
  onClose: () => void;
  onReload: () => void;
}) {
  // The version the edit started from. A read in the background (when the window gets focus, say)
  // may bring a newer one; Save still names this one, so the API answers 409 instead of letting
  // these edits overwrite the newer version.
  const [base] = useState(() => ({
    version: draft.latest_version,
    paragraphs: draft.latest.paragraphs,
  }));
  const [items, setItems] = useState<EditedParagraph[]>(() =>
    base.paragraphs.map((paragraph, key) => ({ key, paragraph })),
  );
  const save = useSaveDraft(jobId);

  const paragraphs = items.map((item) => item.paragraph);
  const changed = JSON.stringify(paragraphs) !== JSON.stringify(base.paragraphs);
  // The problems name positions in the letter as sent; any change moves them, so they go.
  const problems = save.error instanceof ApiError ? save.error.letterProblems : [];

  function change(next: EditedParagraph[]) {
    save.reset();
    setItems(next);
  }

  function setText(index: number, text: string) {
    change(
      items.map((item, i) =>
        i === index ? { ...item, paragraph: { ...item.paragraph, text } } : item,
      ),
    );
  }

  function removeParagraph(index: number) {
    change(items.filter((_, i) => i !== index));
  }

  function removeCitation(index: number, citationIndex: number) {
    change(
      items.map((item, i) =>
        i === index
          ? {
              ...item,
              paragraph: {
                ...item.paragraph,
                citations: item.paragraph.citations.filter((_, c) => c !== citationIndex),
              },
            }
          : item,
      ),
    );
  }

  function submit() {
    save.mutate({ letter: { paragraphs }, basedOnVersion: base.version }, { onSuccess: onClose });
  }

  return (
    <form
      className="draft-editor"
      aria-label="Edit the draft"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <p className="muted">
        Editing version {base.version}. Change the text, or remove a paragraph or a citation.
      </p>
      <ol className="letter">
        {items.map(({ key, paragraph }, index) => {
          const number = index + 1;
          return (
            <li key={key}>
              <p className="muted">{PARAGRAPH_KIND_TEXT[paragraph.kind]}</p>
              <textarea
                aria-label={`Text of paragraph ${number}`}
                value={paragraph.text}
                rows={5}
                onChange={(event) => setText(index, event.target.value)}
              />
              {paragraph.citations.length > 0 && (
                <ul className="citations">
                  {paragraph.citations.map((citation, citationIndex) => (
                    <li key={citationIndex}>
                      {SOURCE_TEXT[citation.source]} {citation.source_document_id}, chunk{" "}
                      {citation.chunk_index}{" "}
                      <button
                        type="button"
                        className="secondary"
                        aria-label={`Remove citation ${citationIndex + 1} of paragraph ${number}`}
                        onClick={() => removeCitation(index, citationIndex)}
                      >
                        Remove citation
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <button
                type="button"
                className="secondary"
                aria-label={`Remove paragraph ${number}`}
                disabled={items.length === 1}
                onClick={() => removeParagraph(index)}
              >
                Remove paragraph
              </button>
              {problems
                .filter((problem) => problem.paragraph_index === index)
                .map((problem, problemIndex) => (
                  <p key={problemIndex} className="check-this">
                    {describeLetterProblem(problem)}
                  </p>
                ))}
            </li>
          );
        })}
      </ol>
      <ReviewError
        error={save.error}
        onReload={onReload}
        staleText="Someone saved a newer version. Reload to see it; your changes here will be lost."
        problemsBeside
      />
      <div className="links">
        <button type="submit" disabled={!changed || save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
