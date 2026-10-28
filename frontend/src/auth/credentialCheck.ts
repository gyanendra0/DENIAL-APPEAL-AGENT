// The checks the browser makes before a login or sign-up is sent, so a missing or malformed field
// is shown under that field at once. The API checks everything again and decides.

// The same limits as `src/api/passwords.py`.
export const MIN_PASSWORD_CHARACTERS = 12;
export const MAX_PASSWORD_CHARACTERS = 128;

// As loose as the browser's own email check: something, an @, something. "ada@made-up" passes and
// the API answers it.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;

export type LoginField = "email" | "password";
export type SignupField = "email" | "password";
export type PasswordChangeField = "currentPassword" | "newPassword";
export type ResetAskField = "email";
export type ResetConfirmField = "code" | "newPassword";
export type CodeField = "code";

// One fixed sentence per wrong field, in the order the fields are on the screen. A sentence never
// repeats what was typed.
export type FieldErrors<Field extends string> = Partial<Record<Field, string>>;

function checkEmail(email: string): string | undefined {
  if (email.trim() === "") {
    return "Enter your email.";
  }
  if (!EMAIL_SHAPE.test(email.trim())) {
    return "Enter an email like name@example.com.";
  }
  return undefined;
}

// The same shape as the API's: exactly six digits, leading zeros kept.
const CODE_SHAPE = /^[0-9]{6}$/;

function checkCodeShape(code: string): string | undefined {
  if (code.trim() === "") {
    return "Enter the code from the email.";
  }
  return CODE_SHAPE.test(code.trim()) ? undefined : "The code is 6 digits.";
}

// Python's `len` counts code points, so the length is counted the same way here.
function passwordLength(password: string): number {
  return [...password].length;
}

// A new password must have the API's length; the sentence names the limits.
function checkNewPassword(password: string): string | undefined {
  const length = passwordLength(password);
  return length < MIN_PASSWORD_CHARACTERS || length > MAX_PASSWORD_CHARACTERS
    ? `Use ${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`
    : undefined;
}

function withoutEmpty<Field extends string>(
  errors: Record<Field, string | undefined>,
): FieldErrors<Field> {
  const found: FieldErrors<Field> = {};
  for (const field of Object.keys(errors) as Field[]) {
    if (errors[field] !== undefined) {
      found[field] = errors[field];
    }
  }
  return found;
}

// The login does not check the password length: the API answers a wrong length like a wrong
// password (03-architecture.md, "Login, step by step").
export function checkLogin(values: { email: string; password: string }): FieldErrors<LoginField> {
  return withoutEmpty<LoginField>({
    email: checkEmail(values.email),
    password: values.password === "" ? "Enter your password." : undefined,
  });
}

export function checkSignup(values: { email: string; password: string }): FieldErrors<SignupField> {
  return withoutEmpty<SignupField>({
    email: checkEmail(values.email),
    password: checkNewPassword(values.password),
  });
}

// The current password's length is not checked, as at login: the API answers a wrong length like
// a wrong password.
export function checkPasswordChange(values: {
  currentPassword: string;
  newPassword: string;
}): FieldErrors<PasswordChangeField> {
  return withoutEmpty<PasswordChangeField>({
    currentPassword: values.currentPassword === "" ? "Enter your current password." : undefined,
    newPassword: checkNewPassword(values.newPassword),
  });
}

export function checkResetAsk(values: { email: string }): FieldErrors<ResetAskField> {
  return withoutEmpty<ResetAskField>({ email: checkEmail(values.email) });
}

export function checkResetConfirm(values: {
  code: string;
  newPassword: string;
}): FieldErrors<ResetConfirmField> {
  return withoutEmpty<ResetConfirmField>({
    code: checkCodeShape(values.code),
    newPassword: checkNewPassword(values.newPassword),
  });
}

export function checkCode(values: { code: string }): FieldErrors<CodeField> {
  return withoutEmpty<CodeField>({ code: checkCodeShape(values.code) });
}

// Moves the focus to the first wrong field (each input's `name` is its field) and says whether
// there was one.
export function focusFirstError(form: HTMLFormElement, errors: FieldErrors<string>): boolean {
  const first = Object.keys(errors)[0];
  if (first === undefined) {
    return false;
  }
  const input = form.elements.namedItem(first);
  if (input instanceof HTMLInputElement) {
    input.focus();
  }
  return true;
}
