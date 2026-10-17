import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../src/App";

describe("App", () => {
  it("shows the product name and that nothing is sent to an insurer", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Denial appeal agent" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing is sent to an insurer/)).toBeInTheDocument();
  });
});
