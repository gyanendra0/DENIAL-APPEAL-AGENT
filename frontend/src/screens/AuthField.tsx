import { useId, useState } from "react";
import type { InputHTMLAttributes, ReactNode } from "react";

import { EyeIcon, EyeOffIcon, LockIcon } from "./icons";

type AuthFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "placeholder"> & {
  label: string;
  icon: ReactNode;
  hint?: string;
  error?: string;
  trailing?: ReactNode;
};

// One input of the login or sign-up form. The label is read by screen readers and shown as the
// placeholder; a hint and an error sit under the input and are read with it.
export function AuthField({ label, icon, hint, error, trailing, ...inputProps }: AuthFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint && hintId, error && errorId].filter(Boolean).join(" ");

  return (
    <div className="auth-field">
      <label htmlFor={id} className="visually-hidden">
        {label}
      </label>
      <div className="auth-input">
        <span className="auth-input-icon">{icon}</span>
        <input
          id={id}
          placeholder={label}
          aria-invalid={error !== undefined ? true : undefined}
          aria-describedby={describedBy === "" ? undefined : describedBy}
          {...inputProps}
        />
        {trailing}
      </div>
      {hint !== undefined && (
        <p id={hintId} className="auth-hint">
          {hint}
        </p>
      )}
      {error !== undefined && (
        <p id={errorId} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

type PasswordFieldProps = Omit<AuthFieldProps, "label" | "icon" | "type" | "trailing">;

// The password input with a button that shows or hides what was typed. The button keeps one
// name and says its state with aria-pressed.
export function PasswordField(props: PasswordFieldProps) {
  const [shown, setShown] = useState(false);
  return (
    <AuthField
      {...props}
      label="Password"
      icon={<LockIcon />}
      type={shown ? "text" : "password"}
      trailing={
        <button
          type="button"
          className="auth-reveal"
          aria-label="Show password"
          aria-pressed={shown}
          onClick={() => setShown(!shown)}
        >
          {shown ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      }
    />
  );
}
