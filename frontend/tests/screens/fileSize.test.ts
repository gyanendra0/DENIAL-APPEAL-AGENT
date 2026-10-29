import { describe, expect, it } from "vitest";

import { formatFileSize } from "../../src/screens/fileSize";

describe("formatFileSize", () => {
  it.each([
    [0, "0 B"],
    [812, "812 B"],
    [1000, "1 KB"],
    [4_400, "4 KB"],
    [999_499, "999 KB"],
    [1_000_000, "1 MB"],
    [1_250_000, "1.3 MB"],
  ])("shows %i bytes as %s", (bytes, text) => {
    expect(formatFileSize(bytes)).toBe(text);
  });
});
