import type { DraftReviewStatus, EndState, JobStatus, Overview } from "../api/types";
import { END_STATE_TEXT, REVIEW_STATUS_TEXT, STATUS_TEXT } from "./jobText";
import { END_STATE_TONE, REVIEW_TONE, STATUS_TONE, percent, total } from "./overviewData";
import type { Tone } from "./overviewData";

// A segment narrower than this share has no room for its count inside; its tooltip still has it.
const MIN_LABELLED_SHARE = 8;

// The admin's charts: the same counts as the tables under them, drawn as one stacked bar each,
// and the share of decided drafts that were approved. Shown only to admins (a choice of layout:
// everyone sees the same numbers in the tables).
export function OverviewCharts({ overview }: { overview: Overview }) {
  return (
    <section className="overview-charts" aria-label="Charts">
      <h2>Charts</h2>
      <div className="chart-grid">
        <StackedBar<JobStatus>
          title="Jobs by status"
          counts={overview.jobs_by_status}
          labels={STATUS_TEXT}
          tones={STATUS_TONE}
        />
        <StackedBar<EndState>
          title="Finished jobs by end state"
          counts={overview.jobs_by_end_state}
          labels={END_STATE_TEXT}
          tones={END_STATE_TONE}
        />
        <StackedBar<DraftReviewStatus>
          title="Drafts by review"
          counts={overview.drafts_by_review_status}
          labels={REVIEW_STATUS_TEXT}
          tones={REVIEW_TONE}
        />
        <ApprovalMeter
          approved={overview.drafts_by_review_status.approved}
          rejected={overview.drafts_by_review_status.rejected}
        />
      </div>
    </section>
  );
}

interface StackedBarProps<T extends string> {
  title: string;
  counts: Readonly<Record<T, number>>;
  labels: Readonly<Record<T, string>>;
  tones: Readonly<Record<T, Tone>>;
}

// One bar split into a segment per value with a count above 0, in the order of `labels`. Each
// segment can take the focus and names itself ("Finished: 3 of 6 (50%)"), which is also its
// tooltip; the legend under the bar lists every value, so the colour is never the only sign.
function StackedBar<T extends string>({ title, counts, labels, tones }: StackedBarProps<T>) {
  const values = Object.keys(labels) as T[];
  const whole = total(counts);
  const shown = values.filter((value) => counts[value] > 0);

  return (
    <figure className="chart">
      <figcaption className="chart-title">
        {title} <span className="muted">{whole} in all</span>
      </figcaption>
      {whole === 0 ? (
        <p className="muted chart-empty">Nothing here yet.</p>
      ) : (
        <div className="stacked-bar">
          {shown.map((value) => {
            const share = percent(counts[value], whole);
            const tip = `${labels[value]}: ${counts[value]} of ${whole} (${share}%)`;
            return (
              <span
                key={value}
                className={`stacked-segment tone-${tones[value]}`}
                style={{ flexGrow: counts[value] }}
                role="img"
                tabIndex={0}
                aria-label={tip}
                data-tip={tip}
              >
                {share >= MIN_LABELLED_SHARE && <span aria-hidden="true">{counts[value]}</span>}
              </span>
            );
          })}
        </div>
      )}
      <ul className="chart-legend">
        {values.map((value) => (
          <li key={value} className={counts[value] === 0 ? "is-zero" : undefined}>
            <span className={`legend-swatch tone-${tones[value]}`} aria-hidden="true" />
            {labels[value]} <strong>{counts[value]}</strong>
          </li>
        ))}
      </ul>
    </figure>
  );
}

// The approved share of the drafts a person has decided (approved or rejected); waiting drafts do
// not count. The fill is the approved part, the track the rejected part.
function ApprovalMeter({ approved, rejected }: { approved: number; rejected: number }) {
  const decided = approved + rejected;
  const share = percent(approved, decided);

  return (
    <figure className="chart">
      <figcaption className="chart-title">
        Approval rate <span className="muted">{decided} decided</span>
      </figcaption>
      {decided === 0 ? (
        <p className="muted chart-empty">No draft decided yet.</p>
      ) : (
        <>
          <p className="meter-value">{share}% approved</p>
          <div
            className="meter"
            role="meter"
            aria-label="Approval rate"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={share}
            aria-valuetext={`${share}% approved: ${approved} of ${decided} decided drafts`}
          >
            <span className="meter-fill tone-good" style={{ width: `${share}%` }} />
          </div>
          <p className="muted meter-note">
            {approved} approved · {rejected} rejected
          </p>
        </>
      )}
    </figure>
  );
}
