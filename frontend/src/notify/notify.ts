import { createContext, useContext } from "react";

// A short message at the top of the page about something that just happened: an upload
// accepted, a draft approved, a password changed. Its words are fixed sentences; a notification
// never shows a file name, what the user typed or what a letter says.
export type NotificationTone = "success" | "info" | "error";

export interface NotificationInput {
  tone: NotificationTone;
  title: string;
  detail?: string;
  // A link to the screen the message is about ("Open" the finished job).
  action?: { label: string; to: string };
}

export type Notify = (notification: NotificationInput) => void;

// Outside the provider (a screen tested on its own) a notification goes nowhere.
export const NotifyContext = createContext<Notify>(() => undefined);

export function useNotify(): Notify {
  return useContext(NotifyContext);
}
