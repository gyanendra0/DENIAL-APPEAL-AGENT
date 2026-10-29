// The API keeps no name, only the email, so the sidebar shows a first name made from it: the part
// before the "@", cut at the first ".", "_", "-", "+" or digit, with a capital first letter
// ("ada.lovelace@x.org" -> "Ada"). When nothing is left ("2026@x.org"), the whole part is shown.
export function firstNameFromEmail(email: string): string {
  const local = email.split("@")[0] ?? "";
  const first = local.split(/[._+\-0-9]/)[0] ?? "";
  const name = first === "" ? local : first;
  return name.charAt(0).toUpperCase() + name.slice(1).toLowerCase();
}
