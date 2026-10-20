import type { DenialLetterExtraction } from "../api/types";
import {
  FIELD_LABELS,
  FIELD_NAMES,
  formatFieldValue,
  formatPercent,
  needsCheck,
} from "./detailText";

// The fields read from the letter, each with its confidence. A field to check says so in words,
// not only by colour.
export function ExtractionTable({ extraction }: { extraction: DenialLetterExtraction | null }) {
  if (extraction === null) {
    return (
      <section aria-label="Extracted fields">
        <h2>Fields read from the letter</h2>
        <p>The letter was not read into fields.</p>
      </section>
    );
  }

  return (
    <section aria-label="Extracted fields">
      <h2>Fields read from the letter</h2>
      <div className="table-scroll">
        <table className="worklist">
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Value</th>
              <th scope="col">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {FIELD_NAMES.map((name) => {
              const field = extraction[name];
              return (
                <tr key={name}>
                  <th scope="row">{FIELD_LABELS[name]}</th>
                  <td className="pre-wrap">{formatFieldValue(name, extraction)}</td>
                  <td>
                    {formatPercent(field.confidence)}
                    {needsCheck(field) && <span className="check-this"> Check this</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
