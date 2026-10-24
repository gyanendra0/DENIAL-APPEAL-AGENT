import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BuildingIcon,
  ChartIcon,
  CloseIcon,
  EyeIcon,
  EyeOffIcon,
  ListIcon,
  LockIcon,
  LogOutIcon,
  LogoIcon,
  MailIcon,
  MenuIcon,
  PlusIcon,
  SidebarIcon,
} from "../../src/screens/icons";

describe("icons", () => {
  it.each([
    ["LogoIcon", LogoIcon],
    ["MailIcon", MailIcon],
    ["LockIcon", LockIcon],
    ["BuildingIcon", BuildingIcon],
    ["EyeIcon", EyeIcon],
    ["EyeOffIcon", EyeOffIcon],
    ["MenuIcon", MenuIcon],
    ["CloseIcon", CloseIcon],
    ["SidebarIcon", SidebarIcon],
    ["PlusIcon", PlusIcon],
    ["ListIcon", ListIcon],
    ["ChartIcon", ChartIcon],
    ["LogOutIcon", LogOutIcon],
  ])("%s is decoration, hidden from screen readers", (_name, IconComponent) => {
    const { container } = render(<IconComponent />);

    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });
});
