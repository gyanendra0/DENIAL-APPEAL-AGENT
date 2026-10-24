import { NavLink } from "react-router";

import { useRecentDenials } from "../worklist/useRecentDenials";
import { shortStatus } from "./jobText";

// The sidebar's list of the newest denials, each a link to its split view. A failure here is a
// quiet line, not an alert: the screen beside it has its own errors to tell.
export function RecentDenials() {
  const recent = useRecentDenials();

  return (
    <section aria-label="Recent denials" className="recent">
      <h2 className="recent-title">Recent</h2>
      {recent.isError && <p className="recent-note">The recent denials could not be loaded.</p>}
      {recent.data !== undefined && recent.data.items.length === 0 && (
        <p className="recent-note">No denials yet.</p>
      )}
      {recent.data !== undefined && recent.data.items.length > 0 && (
        <ul className="recent-list">
          {recent.data.items.map((item) => (
            <li key={item.job_id}>
              <NavLink to={`/app/jobs/${item.job_id}`} title={item.file_name}>
                <span className="recent-name">{item.file_name}</span>
                <span className="recent-status">{shortStatus(item)}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
