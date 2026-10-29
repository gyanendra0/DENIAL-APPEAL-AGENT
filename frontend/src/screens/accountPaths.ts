// The account screen opens one of its forms straight away when the address asks for it
// (`/app/account?open=password`); the account menu in the sidebar links there.
export const ACCOUNT_OPEN_PARAM = "open";

export type AccountForm = "password" | "delete";

const ACCOUNT_FORMS: readonly AccountForm[] = ["password", "delete"];

export function accountFormPath(form: AccountForm): string {
  return `/app/account?${ACCOUNT_OPEN_PARAM}=${form}`;
}

// A value that is not one of the forms opens nothing, as if it were not there.
export function readAccountForm(value: string | null): AccountForm | null {
  return ACCOUNT_FORMS.find((form) => form === value) ?? null;
}
