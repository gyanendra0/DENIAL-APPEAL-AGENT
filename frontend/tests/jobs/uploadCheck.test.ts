import { describe, expect, it } from "vitest";

import { UPLOAD_MAX_BYTES, checkUploadFile } from "../../src/jobs/uploadCheck";

function fileOf(name: string, bytes: number): File {
  return new File(["x".repeat(bytes)], name, { type: "" });
}

describe("checkUploadFile", () => {
  it.each(["letter.txt", "letter.pdf", "LETTER.PDF", "made.up.letter.Txt"])(
    "accepts %s",
    (name) => {
      expect(checkUploadFile(fileOf(name, 10))).toBeNull();
    },
  );

  it.each(["scan.png", "letter.docx", "letter", "letter.txt.exe", "pdf"])("refuses %s", (name) => {
    expect(checkUploadFile(fileOf(name, 10))).toBe("Only a .txt file or a PDF can be uploaded.");
  });

  it("refuses an empty file", () => {
    expect(checkUploadFile(fileOf("letter.txt", 0))).toBe("The file is empty.");
  });

  it("accepts a file of exactly the limit and refuses one byte more", () => {
    expect(checkUploadFile(fileOf("letter.txt", UPLOAD_MAX_BYTES))).toBeNull();
    expect(checkUploadFile(fileOf("letter.txt", UPLOAD_MAX_BYTES + 1))).toBe(
      "The file is larger than the upload limit.",
    );
  });

  it("never names the file in its sentence", () => {
    expect(checkUploadFile(fileOf("secret-name.png", 10))).not.toContain("secret-name");
  });
});
