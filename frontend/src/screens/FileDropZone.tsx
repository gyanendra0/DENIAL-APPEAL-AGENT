import { useId, useRef, useState } from "react";
import type { DragEvent } from "react";

import { formatFileSize } from "./fileSize";
import { FileIcon, UploadIcon } from "./icons";

interface FileDropZoneProps {
  label: string;
  accept: string;
  hint: string;
  file: File | null;
  onPick: (picked: File | null) => void;
}

// A large dashed area in place of the browser's "Choose file". Underneath it is still a real file
// input, hidden from sight but not from the keyboard or screen readers: Tab reaches it, Enter or
// Space opens the picker, and the area is its label, so a click anywhere on it opens the picker too.
// A file dragged onto the area goes to `onPick` like a picked one, so it gets the same checks.
export function FileDropZone({ label, accept, hint, file, onPick }: FileDropZoneProps) {
  const inputId = useId();
  const hintId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(true);
  }

  // Moving over a child of the area also fires dragleave; only leaving the area itself counts.
  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !event.currentTarget.contains(next)) {
      setDragging(false);
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped !== undefined) {
      onPick(dropped);
    }
  }

  // The input is emptied too, so picking the same file again still counts as a change.
  function onRemove() {
    if (input.current !== null) {
      input.current.value = "";
    }
    onPick(null);
  }

  const className = ["dropzone", dragging ? "is-dragging" : "", file ? "has-file" : ""].join(" ");

  return (
    <div
      className={className.trim()}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <input
        ref={input}
        id={inputId}
        className="dropzone-input"
        type="file"
        accept={accept}
        aria-label={label}
        aria-describedby={hintId}
        onChange={(event) => onPick(event.target.files?.[0] ?? null)}
      />
      <label htmlFor={inputId} className="dropzone-area">
        {file === null ? (
          <>
            <span className="dropzone-icon">
              <UploadIcon />
            </span>
            <span className="dropzone-title">
              Drag your letter here, or <span className="dropzone-browse">browse</span>
            </span>
          </>
        ) : (
          <>
            <span className="dropzone-icon">
              <FileIcon />
            </span>
            <span className="dropzone-title">{file.name}</span>
            <span className="muted">{formatFileSize(file.size)} · click to choose another</span>
          </>
        )}
        <span id={hintId} className="muted dropzone-hint">
          {hint}
        </span>
      </label>
      {file !== null && (
        <button type="button" className="secondary dropzone-remove" onClick={onRemove}>
          Remove
        </button>
      )}
    </div>
  );
}
