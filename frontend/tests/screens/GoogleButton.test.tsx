import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { jsonResponse, renderAt, stubApi } from "../helpers";

const NO_SESSION = () => jsonResponse(401, { detail: "not logged in" });

describe("GoogleButton", () => {
  it.each(["/app/login", "/app/signup"])(
    "links %s to the API's Google start when the server offers it",
    async (path) => {
      stubApi({
        "GET /me": NO_SESSION,
        "GET /auth/options": () => jsonResponse(200, { google: true }),
      });
      renderAt(path);

      const link = await screen.findByRole("link", { name: "Continue with Google" });
      expect(link).toHaveAttribute("href", "/auth/google/start");
    },
  );

  it.each([
    ["Google is off", () => jsonResponse(200, { google: false })],
    ["the options cannot be read", () => jsonResponse(500, {})],
  ])("shows only the password form when %s", async (_case, answer) => {
    const fetchStub = stubApi({ "GET /me": NO_SESSION, "GET /auth/options": answer });
    renderAt("/app/login");

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchStub.mock.calls.some(([input]) => String(input) === "/auth/options")).toBe(true),
    );
    expect(screen.queryByRole("link", { name: "Continue with Google" })).not.toBeInTheDocument();
  });
});
