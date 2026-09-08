import { useState } from "react";
import { useAccount } from "../context/AccountContext";
import { api, ApiError } from "../api";

type Mode = "access" | "user" | "create";

export default function Login() {
  const { login, loginWithPassword } = useAccount();
  const [mode, setMode] = useState<Mode>("access");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [accessCode, setAccessCode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firmName, setFirmName] = useState("");
  const [newCode, setNewCode] = useState("");

  async function handleAccessCodeLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(accessCode);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? "Invalid access code." : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUserLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await loginWithPassword(email, password);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? "Invalid email or password." : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.createAccount(firmName, newCode);
      await login(newCode);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered">
      <div className="login-card">
        <h1>Quotation Platform</h1>

        {mode === "access" && (
          <form onSubmit={handleAccessCodeLogin}>
            <label>
              Access code
              <input
                type="password"
                value={accessCode}
                onChange={(e) => setAccessCode(e.target.value)}
                autoFocus
                required
              />
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy}>
              {busy ? "Checking..." : "Enter"}
            </button>
            <p className="hint">
              Team member?{" "}
              <button type="button" className="link-button" onClick={() => { setMode("user"); setError(null); }}>
                Log in with email
              </button>
            </p>
            <p className="hint">
              New firm?{" "}
              <button type="button" className="link-button" onClick={() => { setMode("create"); setError(null); }}>
                Create an account
              </button>
            </p>
          </form>
        )}

        {mode === "user" && (
          <form onSubmit={handleUserLogin}>
            <label>
              Email
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy}>
              {busy ? "Checking..." : "Log in"}
            </button>
            <p className="hint">
              <button type="button" className="link-button" onClick={() => { setMode("access"); setError(null); }}>
                Use firm access code instead
              </button>
            </p>
          </form>
        )}

        {mode === "create" && (
          <form onSubmit={handleCreate}>
            <label>
              Firm name
              <input value={firmName} onChange={(e) => setFirmName(e.target.value)} required />
            </label>
            <label>
              Choose an access code
              <input value={newCode} onChange={(e) => setNewCode(e.target.value)} required />
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy}>
              {busy ? "Creating..." : "Create account"}
            </button>
            <p className="hint">
              <button type="button" className="link-button" onClick={() => { setMode("access"); setError(null); }}>
                Back to login
              </button>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
