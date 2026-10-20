import type { AgentTrace } from "../api/types";
import { RULE_OUTCOME_TEXT, RULE_REASON_TEXT, SOURCE_TEXT, formatPercent } from "./detailText";

// What the agent decided after reading the letter: the rules, the score and the evidence found.
// The trace holds no text, so the evidence is named, not quoted.
export function AnalysisSection({ trace }: { trace: AgentTrace | null }) {
  if (trace === null) {
    return (
      <section aria-label="Analysis">
        <h2>Analysis</h2>
        <p>The agent did not run on this letter.</p>
      </section>
    );
  }

  const { rules, scoring, retrieval } = trace;
  return (
    <section aria-label="Analysis">
      <h2>Analysis</h2>
      <h3>Rules</h3>
      {rules.outcome === null ? (
        <p>The rules gave no verdict.</p>
      ) : (
        <>
          <p>{RULE_OUTCOME_TEXT[rules.outcome]}</p>
          {rules.reasons.length > 0 && (
            <ul>
              {rules.reasons.map((reason) => (
                <li key={reason}>{RULE_REASON_TEXT[reason]}</li>
              ))}
            </ul>
          )}
        </>
      )}
      <h3>Win chance</h3>
      {scoring.score === null ? (
        <p>Not scored.</p>
      ) : (
        <p>
          {formatPercent(scoring.score)}
          {scoring.threshold !== null &&
            ` (a draft needs at least ${formatPercent(scoring.threshold)})`}
        </p>
      )}
      <h3>Evidence found</h3>
      {retrieval.matches.length === 0 ? (
        <p>No evidence was found.</p>
      ) : (
        <div className="table-scroll">
          <table className="worklist">
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Document</th>
                <th scope="col">Chunk</th>
                <th scope="col">Similarity</th>
              </tr>
            </thead>
            <tbody>
              {retrieval.matches.map((match) => (
                <tr key={`${match.source}-${match.source_document_id}-${match.chunk_index}`}>
                  <td>{SOURCE_TEXT[match.source]}</td>
                  <td>{match.source_document_id}</td>
                  <td>{match.chunk_index}</td>
                  <td>{match.similarity.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
