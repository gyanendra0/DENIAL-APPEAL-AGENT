import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ChartIcon,
  CheckIcon,
  ChevronUpIcon,
  CloseIcon,
  EyeIcon,
  EyeOffIcon,
  FileIcon,
  GoogleIcon,
  InfoIcon,
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
  WarningIcon,
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
    ["CheckIcon", CheckIcon],
    ["InfoIcon", InfoIcon],
    ["WarningIcon", WarningIcon],
  ])("%s is decoration, hidden from screen readers", (_name, IconComponent) => {
    const { container } = render(<IconComponent />);

    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });
});
