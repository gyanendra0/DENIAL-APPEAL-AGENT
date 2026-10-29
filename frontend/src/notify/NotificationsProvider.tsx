import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { CheckIcon, CloseIcon, InfoIcon, WarningIcon } from "../screens/icons";
import { NotifyContext } from "./notify";
import type { NotificationInput, NotificationTone } from "./notify";

// How long a success or info notification stays; an error stays until it is closed.
export const NOTIFICATION_MS = 5000;
// The most shown at once; a new one pushes the oldest out.
export const MAX_NOTIFICATIONS = 3;

interface Shown extends NotificationInput {
  id: number;
}

const TONE_ICON: Readonly<Record<NotificationTone, () => ReactNode>> = {
  success: CheckIcon,
  info: InfoIcon,
  error: WarningIcon,
};

interface NotificationsProviderProps {
  // How an action link moves to its screen: the router's own navigate, so the page is not
  // loaded again. The provider sits above the router, so the messages outlive a change of screen.
  navigate: (to: string) => void;
  children: ReactNode;
}

export function NotificationsProvider({ navigate, children }: NotificationsProviderProps) {
  const [shown, setShown] = useState<Shown[]>([]);
  const nextId = useRef(1);

  const notify = useCallback((notification: NotificationInput) => {
    const id = nextId.current;
    nextId.current += 1;
    setShown((current) => [{ ...notification, id }, ...current].slice(0, MAX_NOTIFICATIONS));
  }, []);

  const dismiss = useCallback((id: number) => {
    setShown((current) => current.filter((item) => item.id !== id));
  }, []);

  // Errors are read out at once; the rest wait for a pause. Each region holds its own messages.
  const polite = shown.filter((item) => item.tone !== "error");
  const urgent = shown.filter((item) => item.tone === "error");

  return (
    <NotifyContext.Provider value={notify}>
      {children}
      <div className="notifications" role="region" aria-label="Notifications">
        <div aria-live="assertive">
          {urgent.map((item) => (
            <NotificationCard key={item.id} item={item} navigate={navigate} onClose={dismiss} />
          ))}
        </div>
        <div aria-live="polite">
          {polite.map((item) => (
            <NotificationCard key={item.id} item={item} navigate={navigate} onClose={dismiss} />
          ))}
        </div>
      </div>
    </NotifyContext.Provider>
  );
}

interface NotificationCardProps {
  item: Shown;
  navigate: (to: string) => void;
  onClose: (id: number) => void;
}

// Closes itself after NOTIFICATION_MS unless it is an error; the time stops while the pointer is
// on it or the focus is in it (a keyboard user on its link), and starts again in full when both
// have left.
function NotificationCard({ item, navigate, onClose }: NotificationCardProps) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const paused = hovered || focused;
  const Icon = TONE_ICON[item.tone];

  useEffect(() => {
    if (item.tone === "error" || paused) {
      return undefined;
    }
    const timer = window.setTimeout(() => onClose(item.id), NOTIFICATION_MS);
    return () => window.clearTimeout(timer);
  }, [item.id, item.tone, paused, onClose]);

  return (
    <div
      className={`notification tone-${item.tone}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (!(next instanceof Node) || !event.currentTarget.contains(next)) {
          setFocused(false);
        }
      }}
    >
      <span className="notification-icon">
        <Icon />
      </span>
      <div className="notification-body">
        <p className="notification-title">{item.title}</p>
        {item.detail !== undefined && <p className="notification-detail">{item.detail}</p>}
        {item.action !== undefined && (
          <button
            type="button"
            className="link-button notification-action"
            onClick={() => {
              onClose(item.id);
              if (item.action !== undefined) {
                navigate(item.action.to);
              }
            }}
          >
            {item.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        className="icon-button notification-close"
        aria-label="Close notification"
        onClick={() => onClose(item.id)}
      >
        <CloseIcon />
      </button>
    </div>
  );
}
