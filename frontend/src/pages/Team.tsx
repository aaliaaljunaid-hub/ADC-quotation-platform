import { useEffect, useState } from "react";
import { useAccount } from "../context/AccountContext";
import { api, ApiError } from "../api";
import type { User } from "../types";

export default function Team() {
  const { account, isOwner } = useAccount();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "editor" as "owner" | "editor" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!account) return;
    api.getUsers(account.id).then((u) => {
      setUsers(u);
      setLoading(false);
    });
  }, [account]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!account || !form.name || !form.email || !form.password) return;
    setSaving(true);
    setError(null);
    try {
      const user = await api.createUser(account.id, form);
      setUsers([...users, user]);
      setForm({ name: "", email: "", password: "", role: "editor" });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add that team member.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p>Loading team...</p>;

  return (
    <div>
      <h2>Team</h2>
      <p className="muted">
        Team members log in with their own email and password (via "Log in with email" on the
        login screen) instead of sharing the firm's access code.
      </p>

      {isOwner ? (
        <form className="inline-form" onSubmit={handleAdd}>
          <input
            placeholder="Name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
          />
          <input
            placeholder="Email"
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            required
          />
          <input
            placeholder="Password"
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "owner" | "editor" })}>
            <option value="editor">Editor</option>
            <option value="owner">Owner</option>
          </select>
          <button type="submit" disabled={saving}>Add team member</button>
        </form>
      ) : (
        <p className="muted">Only an account owner can add team members.</p>
      )}
      {error && <p className="error">{error}</p>}

      <table className="data-table">
        <thead>
          <tr><th>Name</th><th>Email</th><th>Role</th></tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.name}</td>
              <td>{u.email}</td>
              <td>{u.role}</td>
            </tr>
          ))}
          {users.length === 0 && <tr><td colSpan={3} className="empty-row">No team members yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
