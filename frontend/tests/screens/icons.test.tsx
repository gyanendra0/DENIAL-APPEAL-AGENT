import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ChartIcon,
  ChevronUpIcon,
  CloseIcon,
  EyeIcon,
  EyeOffIcon,
  FileIcon,
  GoogleIcon,
  ListIcon,
  LockIcon,
  LogOutIcon,
  LogoIcon,
  MailIcon,
  MenuIcon,
  PlusIcon,
  SidebarIcon,
  TrashIcon,
  UploadIcon,
} from "../../src/screens/icons";

describe("icons", () => {
  it.each([
    ["LogoIcon", LogoIcon],
    ["MailIcon", MailIcon],
    ["LockIcon", LockIcon],
    ["EyeIcon", EyeIcon],
    ["EyeOffIcon", EyeOffIcon],
    ["MenuIcon", MenuIcon],
    ["CloseIcon", CloseIcon],
    ["SidebarIcon", SidebarIcon],
    ["PlusIcon", PlusIcon],
    ["ListIcon", ListIcon],
    ["ChartIcon", ChartIcon],
    ["LogOutIcon", LogOutIcon],
    ["GoogleIcon", GoogleIcon],
    ["TrashIcon", TrashIcon],
    ["ChevronUpIcon", ChevronUpIcon],
    ["UploadIcon", UploadIcon],
    ["FileIcon", FileIcon],
  ])("%s is decoration, hidden from screen readers", (_name, IconComponent) => {
    const { container } = render(<IconComponent />);

    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });
});
