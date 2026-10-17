// The checks the browser makes before a file is sent, so a wrong file is refused at once. The API
// checks the file again and decides; it reads the type from the content, the browser from the name.

// The default of `UPLOAD_MAX_BYTES` in `src/config/settings.py`; a server with a lower limit still
// answers a larger file with a 413.
export const UPLOAD_MAX_BYTES = 1_000_000;

const UPLOAD_EXTENSIONS = [".txt", ".pdf"] as const;

// A fixed sentence when the file cannot be sent, or null when it can. The sentence never names the
// file.
export function checkUploadFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!UPLOAD_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return "Only a .txt file or a PDF can be uploaded.";
  }
  if (file.size === 0) {
    return "The file is empty.";
  }
  if (file.size > UPLOAD_MAX_BYTES) {
    return "The file is larger than the upload limit.";
  }
  return null;
}
