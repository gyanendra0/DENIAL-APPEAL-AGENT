// "812 B", "4 KB", "1 MB", "1.2 MB": a file's size in the units the upload limit is set in
// (1 KB = 1,000 bytes, as `UPLOAD_MAX_BYTES` = 1,000,000 is "1 MB").
const KILOBYTE = 1000;
const MEGABYTE = KILOBYTE * KILOBYTE;

export function formatFileSize(bytes: number): string {
  if (bytes < KILOBYTE) {
    return `${bytes} B`;
  }
  if (bytes < MEGABYTE) {
    return `${Math.round(bytes / KILOBYTE)} KB`;
  }
  return `${Number((bytes / MEGABYTE).toFixed(1))} MB`;
}
