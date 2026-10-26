import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AuthField, PasswordField } from "../../src/screens/AuthField";

describe("AuthField", () => {
  it("is named by its label, which is also the placeholder", () => {
    render(<AuthField label="Email" icon={null} type="email" />);

    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("placeholder", "Email");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("reads the hint and the error with the input and marks it wrong", () => {
    render(
      <AuthField label="Email" icon={null} hint="A made-up hint." error="Enter your email." />,
    );

    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("A made-up hint. Enter your email.");
  });
});

describe("PasswordField", () => {
  it("shows and hides what was typed", async () => {
    const user = userEvent.setup();
    render(<PasswordField />);
    const input = screen.getByLabelText("Password");
    const toggle = screen.getByRole("button", { name: "Show password" });

    expect(input).toHaveAttribute("type", "password");
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");

    await user.click(toggle);
    expect(input).toHaveAttribute("type", "password");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("names the input and its show button after a given label", () => {
    render(<PasswordField label="New password" />);

    expect(screen.getByLabelText("New password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Show new password" })).toBeInTheDocument();
  });
});
