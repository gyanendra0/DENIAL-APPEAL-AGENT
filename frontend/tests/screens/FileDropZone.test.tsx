import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FileDropZone } from "../../src/screens/FileDropZone";

function letter(): File {
  return new File(["Made-up denial letter"], "letter.txt", { type: "text/plain" });
}

function renderZone(file: File | null = null) {
  const onPick = vi.fn<(picked: File | null) => void>();
  render(
    <FileDropZone
      label="Denial letter"
      accept=".txt,.pdf"
      hint=".txt or .pdf with text, up to 1 MB"
      file={file}
      onPick={onPick}
    />,
  );
  const zone = screen.getByLabelText("Denial letter").closest(".dropzone");
  if (!(zone instanceof HTMLElement)) {
    throw new Error("no drop zone");
  }
  return { onPick, zone };
}

// jsdom has no DragEvent, so the `relatedTarget` (where the pointer went) is set by hand.
function dragLeave(zone: HTMLElement, to: Element | null) {
  const event = createEvent.dragLeave(zone);
  Object.defineProperty(event, "relatedTarget", { value: to });
  fireEvent(zone, event);
}

function drop(zone: HTMLElement, files: File[]) {
  fireEvent.drop(zone, { dataTransfer: { files } });
}

describe("FileDropZone", () => {
  it("is a real file input named by its label and described by the hint", () => {
    renderZone();

    const input = screen.getByLabelText("Denial letter");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAccessibleDescription(".txt or .pdf with text, up to 1 MB");
    expect(screen.getByText(/Drag your letter here, or/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("hands a picked file on", async () => {
    const { onPick } = renderZone();

    await userEvent.setup().upload(screen.getByLabelText("Denial letter"), letter());

    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ name: "letter.txt" }));
  });

  it("hands a dropped file on and stops the browser from opening it", () => {
    const { onPick, zone } = renderZone();
    const event = createEvent.drop(zone, { dataTransfer: { files: [letter()] } });

    fireEvent(zone, event);

    expect(event.defaultPrevented).toBe(true);
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ name: "letter.txt" }));
  });

  it("ignores a drop that holds no file", () => {
    const { onPick, zone } = renderZone();

    drop(zone, []);

    expect(onPick).not.toHaveBeenCalled();
  });

  it("lights up while a file is dragged over it, and not after it leaves or drops", () => {
    const { zone } = renderZone();

    fireEvent.dragOver(zone);
    expect(zone).toHaveClass("is-dragging");
    dragLeave(zone, zone.querySelector(".dropzone-area"));
    expect(zone).toHaveClass("is-dragging");
    dragLeave(zone, document.body);
    expect(zone).not.toHaveClass("is-dragging");

    fireEvent.dragOver(zone);
    drop(zone, []);
    expect(zone).not.toHaveClass("is-dragging");
  });

  it("shows the chosen file's name and size, and Remove clears it", async () => {
    const { onPick, zone } = renderZone(letter());
    const input = screen.getByLabelText("Denial letter");

    expect(zone).toHaveClass("has-file");
    expect(screen.getByText("letter.txt")).toBeInTheDocument();
    expect(screen.getByText("21 B · click to choose another")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Remove" }));

    expect(onPick).toHaveBeenCalledWith(null);
    expect(input).toHaveValue("");
  });
});
