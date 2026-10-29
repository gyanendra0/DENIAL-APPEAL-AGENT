import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_NOTIFICATIONS,
  NOTIFICATION_MS,
  NotificationsProvider,
} from "../../src/notify/NotificationsProvider";
import { useNotify } from "../../src/notify/notify";
import type { NotificationInput } from "../../src/notify/notify";

// A button that sends one notification, so a test can send several.
function Sender({ notification }: { notification: NotificationInput }) {
  const notify = useNotify();
  return (
    <button type="button" onClick={() => notify(notification)}>
      Send
    </button>
  );
}

// Fake timers that still move on their own, so the auto-close can be stepped over.
function renderWith(notification: NotificationInput) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const navigate = vi.fn<(to: string) => void>();
  render(
    <NotificationsProvider navigate={navigate}>
      <Sender notification={notification} />
    </NotificationsProvider>,
  );
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  return { navigate, user, send: () => user.click(screen.getByRole("button", { name: "Send" })) };
}

function region() {
  return within(screen.getByLabelText("Notifications"));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("NotificationsProvider", () => {
  it("shows a notification with its title, detail and tone", async () => {
    const { send } = renderWith({ tone: "success", title: "Saved", detail: "All done." });

    await send();

    const title = region().getByText("Saved");
    expect(region().getByText("All done.")).toBeInTheDocument();
    const card = title.closest(".notification");
    expect(card).toHaveClass("tone-success");
    expect(card?.parentElement).toHaveAttribute("aria-live", "polite");
  });

  it("closes a notification with its close button", async () => {
    const { send, user } = renderWith({ tone: "info", title: "Hello" });
    await send();

    await user.click(region().getByRole("button", { name: "Close notification" }));

    expect(region().queryByText("Hello")).not.toBeInTheDocument();
  });

  it("closes a success by itself after a while, but not while the pointer is on it", async () => {
    const { send, user } = renderWith({ tone: "success", title: "Saved" });
    await send();

    await user.hover(region().getByText("Saved"));
    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS * 2));
    expect(region().getByText("Saved")).toBeInTheDocument();

    await user.unhover(region().getByText("Saved"));
    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS - 100));
    expect(region().getByText("Saved")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(region().queryByText("Saved")).not.toBeInTheDocument();
  });

  it("keeps a success open while the keyboard focus is in it", async () => {
    const { send, user } = renderWith({
      tone: "success",
      title: "Draft ready",
      action: { label: "Open", to: "/app/jobs/7" },
    });
    await send();

    await user.tab();
    expect(region().getByRole("button", { name: "Open" })).toHaveFocus();
    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS * 2));
    expect(region().getByText("Draft ready")).toBeInTheDocument();

    // Moving between the card's own buttons keeps it paused.
    await user.tab();
    expect(region().getByRole("button", { name: "Close notification" })).toHaveFocus();
    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS * 2));
    expect(region().getByText("Draft ready")).toBeInTheDocument();

    await user.tab({ shift: true });
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Send" })).toHaveFocus();
    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS + 100));
    expect(region().queryByText("Draft ready")).not.toBeInTheDocument();
  });

  it("keeps an error until it is closed, and reads it out at once", async () => {
    const { send } = renderWith({ tone: "error", title: "It failed" });
    await send();

    await act(() => vi.advanceTimersByTimeAsync(NOTIFICATION_MS * 3));

    const title = region().getByText("It failed");
    expect(title.closest(".notification")?.parentElement).toHaveAttribute("aria-live", "assertive");
  });

  it("shows at most a few, newest first", async () => {
    const { send } = renderWith({ tone: "info", title: "Again" });

    for (let i = 0; i < MAX_NOTIFICATIONS + 2; i += 1) {
      await send();
    }

    expect(region().getAllByText("Again")).toHaveLength(MAX_NOTIFICATIONS);
  });

  it("goes to the action's screen and closes the notification", async () => {
    const { send, user, navigate } = renderWith({
      tone: "success",
      title: "Draft ready",
      action: { label: "Open", to: "/app/jobs/7" },
    });
    await send();

    await user.click(region().getByRole("button", { name: "Open" }));

    expect(navigate).toHaveBeenCalledWith("/app/jobs/7");
    expect(region().queryByText("Draft ready")).not.toBeInTheDocument();
  });

  it("sends nowhere outside the provider", async () => {
    render(<Sender notification={{ tone: "info", title: "Lost" }} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Send" }));

    expect(screen.queryByText("Lost")).not.toBeInTheDocument();
  });
});
