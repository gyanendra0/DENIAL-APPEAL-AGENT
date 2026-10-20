import { ApiError } from "../api/client";
import { describeError } from "../screens/errorMessage";
import { describeLetterProblem } from "./detailText";
import { isAlreadyDecided, isStaleVersion } from "./useDraftActions";

// Why a save, approval or rejection was refused, in fixed sentences. A refused letter names its
// problems by kind and position only, so nothing typed into the letter is shown back.
export function ReviewError({
  error,
  onReload,
  staleText,
  problemsBeside = false,
}: {
  error: Error | null;
  onReload: () => void;
  staleText: string;
  // True when the editor shows each problem beside its paragraph instead of in a list here.
  problemsBeside?: boolean;
}) {
  if (error === null) {
    return null;
  }
  if (isStaleVersion(error)) {
    return (
      <div className="error" role="alert">
        <p>{staleText}</p>
        <button type="button" className="secondary" onClick={onReload}>
          Reload
        </button>
      </div>
    );
  }
  if (isAlreadyDecided(error)) {
    return (
      <p className="error" role="alert">
        This draft was already approved or rejected.
      </p>
    );
  }
  const problems = error instanceof ApiError ? error.letterProblems : [];
  if (problems.length > 0) {
    return (
      <div className="error" role="alert">
        <p>
          The letter did not pass the checks.
          {problemsBeside && " Each problem is shown beside its paragraph."}
        </p>
        {!problemsBeside && (
          <ul>
            {problems.map((problem, index) => (
              <li key={index}>{describeLetterProblem(problem)}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  return (
    <p className="error" role="alert">
      {describeError(error)}
    </p>
  );
}
