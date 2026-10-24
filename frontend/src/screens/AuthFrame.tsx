import type { ReactNode } from "react";

import { LogoIcon } from "./icons";

type AuthFrameProps = {
  title: string;
  intro: string;
  footer: ReactNode;
  children: ReactNode;
};

// The frame of the login and sign-up screens, in two columns: on the left the product's headline
// and the form in a card, on the right a framed panel with what the product promises. Under 900
// pixels the panel goes below the form; on phones it is left out.
export function AuthFrame({ title, intro, footer, children }: AuthFrameProps) {
  return (
    <div className="auth-page">
      <header className="auth-brand">
        <span className="logo-tile">
          <LogoIcon />
        </span>
        <span className="auth-brand-name">Denial appeal agent</span>
      </header>
      <main className="auth-main">
        <div className="auth-column">
          <h1 className="auth-headline">Appeal denials with evidence</h1>
          <p className="auth-tagline">Read the denial, check the policy, review a cited draft.</p>
          <div className="auth-card">
            <h2>{title}</h2>
            <p className="auth-intro">{intro}</p>
            {children}
          </div>
          <p className="auth-switch">{footer}</p>
        </div>
        <aside className="auth-panel" aria-label="About the product">
          <p className="auth-panel-text">Every appeal cites the policy it relies on.</p>
          <p className="auth-panel-note">A person reviews each draft; nothing is sent by itself.</p>
        </aside>
      </main>
    </div>
  );
}
