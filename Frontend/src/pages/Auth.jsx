import { useState } from 'react';
import { ArrowRight, Check, Eye, EyeOff, LoaderCircle, ShieldCheck } from 'lucide-react';
import { apiRequest } from '../api.js';
import { Button, ErrorBox } from '../components/UI.jsx';

export function Auth({ onLogin, onDemo, notice }) {
  const [register, setRegister] = useState(false),
    [verifying, setVerifying] = useState(false),
    [code, setCode] = useState(''),
    [email, setEmail] = useState(''),
    [password, setPassword] = useState(''),
    [visible, setVisible] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(null),
    [message, setMessage] = useState(notice);
  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (verifying) {
        const result = await apiRequest('/auth/verify-email', {
          method: 'POST', body: { email: email.trim(), code },
        });
        setVerifying(false); setRegister(false); setCode('');
        setMessage(result.message);
      } else if (register) {
        const result = await apiRequest('/auth/register', {
          method: 'POST',
          body: { email: email.trim(), password },
        });
        setRegister(false);
        setPassword('');
        setVerifying(result.email_verification_required);
        setMessage(result.email_verification_required
          ? result.verification_delivery === 'unavailable'
            ? 'Account created, but the verification email could not be delivered. Contact the project team to approve your demo email address, then request a new code.'
            : 'Account created. Check your inbox for a six-digit code. Codes expire in 15 minutes. If it does not arrive, use Send a new code.'
          : 'Account created. Sign in to open your workspace.');
      } else {
        const session = await apiRequest('/auth/login', {
          method: 'POST',
          body: { email: email.trim(), password },
        });
        const user = await apiRequest('/auth/me', { token: session.access_token });
        onLogin(session.access_token, user);
      }
    } catch (e) {
      if (e.code === 'EMAIL_NOT_VERIFIED') {
        setVerifying(true); setPassword('');
      }
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function resend() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const result = await apiRequest('/auth/resend-verification', {
        method: 'POST', body: { email: email.trim() },
      });
      setMessage(result.message);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }
  return (
    <div className="auth-page">
      <aside className="auth-story">
        <a className="brand" href="#/">
          <span className="brand-mark">
            <ShieldCheck size={25} />
          </span>
          DataGuard<span className="brand-period">.</span>
        </a>
        <div>
          <span className="eyebrow">CONFIDENCE IN EVERY ROW</span>
          <h1>
            Better data.
            <br />
            Clearer decisions.
          </h1>
          <p>
            A thoughtful little workspace for your data’s
            <br />
            biggest questions.
          </p>
          <div className="auth-checks">
            {[
              'Catch the gaps before they grow',
              'Understand every rejected row',
              'Keep your original data intact',
            ].map((text) => (
              <span key={text}>
                <Check size={17} />
                {text}
              </span>
            ))}
          </div>
        </div>
        <small>Built for clarity. Designed for trust.</small>
      </aside>
      <main className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          <span className="auth-mini-mark">
            <ShieldCheck size={27} />
          </span>
          <h2>{verifying ? 'Verify your email.' : register ? 'Start with confidence.' : 'Welcome back.'}</h2>
          <p>
            {verifying ? 'Enter the code sent to your inbox to activate your workspace.' : register
              ? 'Create an account for your DataGuard workspace.'
              : 'Sign in to your connected DataGuard workspace.'}
          </p>
          {message && (
            <div className="success-box" role="status">
              {message}
            </div>
          )}
          {error && <ErrorBox error={error} />}
          <label className="field">
            Email address
            <input
              required
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          {verifying ? <label className="field">
            Verification code
            <input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}"
              maxLength={6} placeholder="Six-digit code" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
          </label> : <label className="field">
            Password
            <div className="password-input">
              <input
                required
                minLength={register ? 8 : undefined}
                type={visible ? 'text' : 'password'}
                autoComplete={register ? 'new-password' : 'current-password'}
                placeholder={register ? 'At least 8 characters' : 'Enter your password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                aria-label={visible ? 'Hide password' : 'Show password'}
                onClick={() => setVisible(!visible)}
              >
                {visible ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </label>}
          <Button className="full-width" disabled={busy} type="submit">
            {busy ? <LoaderCircle size={17} className="spin" /> : <ArrowRight size={17} />}{' '}
            {busy ? 'Please wait…' : verifying ? 'Verify email' : register ? 'Create account' : 'Sign in'}
          </Button>
          {verifying && <Button type="button" variant="secondary" disabled={busy || !email.trim()} onClick={resend}>
            Send a new code
          </Button>}
          <p className="auth-switch">
            {register ? 'Already have an account?' : 'New to DataGuard?'}{' '}
            <button
              type="button"
              className="text-button"
              onClick={() => {
                if (verifying) { setVerifying(false); setRegister(false); }
                else setRegister(!register);
                setError(null);
                setMessage(null);
              }}
            >
              {verifying || register ? 'Sign in' : 'Create an account'}
            </button>
          </p>
          <div className="auth-divider">
            <span />
            or take a look around
            <span />
          </div>
          <Button className="full-width" type="button" variant="secondary" onClick={onDemo}>
            Explore the demo
            <ArrowRight size={16} />
          </Button>
          <p className="auth-note">
            Live sign-in requires the FastAPI backend.
            <br />
            The demo runs locally with synthetic data.
          </p>
        </form>
      </main>
    </div>
  );
}
