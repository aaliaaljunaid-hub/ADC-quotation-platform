import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAccount } from "../context/AccountContext";
import { api, ApiError } from "../api";
import type { Project } from "../types";

export default function Projects() {
  const { account } = useAccount();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ project_name: "", project_number: "", client_name: "", location: "" });
  const [saving, setSaving] = useState(false);

  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  useEffect(() => {
    if (!account) return;
    api.getProjects(account.id).then((p) => {
      setProjects(p);
      setLoading(false);
    });
  }, [account]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!account || !form.project_name || !form.client_name) return;
    setSaving(true);
    try {
      const project = await api.createProject(account.id, {
        project_name: form.project_name,
        project_number: form.project_number || null,
        client_name: form.client_name,
        location: form.location || null,
      });
      setProjects([...projects, project]);
      setForm({ project_name: "", project_number: "", client_name: "", location: "" });
    } finally {
      setSaving(false);
    }
  }

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file next time
    if (!file || !account) return;
    setImporting(true);
    setImportError(null);
    try {
      const result = await api.importWorkbook(account.id, file);
      navigate(`/quotes/${result.quote.id}`);
    } catch (err) {
      setImportError(
        err instanceof ApiError
          ? err.message
          : "Could not import that workbook."
      );
    } finally {
      setImporting(false);
    }
  }

  if (loading) return <p>Loading projects...</p>;

  return (
    <div>
      <h2>Projects</h2>

      <div className="import-box">
        <label className="import-label">
          {importing ? "Importing..." : "Import from ADC-BOQ workbook (.xlsx)"}
          <input type="file" accept=".xlsx" onChange={handleImport} disabled={importing} hidden />
        </label>
        <p className="muted">
          Reads the '3.Selected' sheet plus the client/project/quote number shown on the
          'ADC' sheet, and creates a matching project, quote and catalog items in one go.
        </p>
        {importError && <p className="error">{importError}</p>}
      </div>

      <form className="inline-form" onSubmit={handleCreate}>
        <input
          placeholder="Project name"
          value={form.project_name}
          onChange={(e) => setForm({ ...form, project_name: e.target.value })}
          required
        />
        <input
          placeholder="Project no."
          value={form.project_number}
          onChange={(e) => setForm({ ...form, project_number: e.target.value })}
          style={{ width: 100 }}
        />
        <input
          placeholder="Client name"
          value={form.client_name}
          onChange={(e) => setForm({ ...form, client_name: e.target.value })}
          required
        />
        <input
          placeholder="Location"
          value={form.location}
          onChange={(e) => setForm({ ...form, location: e.target.value })}
        />
        <button type="submit" disabled={saving}>Create project</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            <th>Project</th>
            <th>No.</th>
            <th>Client</th>
            <th>Location</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id}>
              <td><Link to={`/projects/${p.id}`}>{p.project_name}</Link></td>
              <td>{p.project_number || "-"}</td>
              <td>{p.client_name}</td>
              <td>{p.location || "-"}</td>
            </tr>
          ))}
          {projects.length === 0 && (
            <tr><td colSpan={4} className="empty-row">No projects yet.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
