import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuthFrame } from "../../src/screens/AuthFrame";

describe("AuthFrame", () => {
  it("shows the brand, the title, the intro, the form and the footer", () => {
    render(
      <AuthFrame title="Log in" intro="A made-up intro." footer="Footer">
        <form aria-label="Made-up form" />
      </AuthFrame>,
    );

    expect(screen.getByRole("banner")).toHaveTextContent("Denial appeal agent");
    expect(
      screen.getByRole("heading", { level: 1, name: "Appeal denials with evidence" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Log in" })).toBeInTheDocument();
    expect(screen.getByText("A made-up intro.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Made-up form" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("Footer");
    expect(screen.getByRole("complementary", { name: "About the product" })).toHaveTextContent(
      "A person reviews each draft; nothing is sent by itself.",
    );
  });
});
