import { useEffect, useState } from "react";
import { useAccount } from "../context/AccountContext";
import { api } from "../api";
import type { DocTemplate } from "../types";

export default function Branding() {
  const { account } = useAccount();
  const [template, setTemplate] = useState<DocTemplate | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!account) return;
    api.getDocTemplates(account.id).then((templates) => {
      setTemplate(templates.find((t) => t.is_default) || templates[0] || null);
      setLoading(false);
    });
  }, [account]);

  async function handleSave(updates: Record<string, unknown>) {
    if (!account || !template) return;
    setSaving(true);
    try {
      const updated = await api.updateDocTemplate(account.id, template.id, updates);
      setTemplate(updated);
    } finally {
      setSaving(false);
    }
  }

  async function handleLogoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !account || !template) return;
    setUploading(true);
    try {
      const updated = await api.uploadLogo(account.id, template.id, file);
      setTemplate(updated);
    } finally {
      setUploading(false);
    }
  }

  if (loading) return <p>Loading branding...</p>;
  if (!template) return <p>No branding template found for this account.</p>;

  return (
    <div>
      <h2>Branding</h2>

      <section className="branding-section">
        <h3>Logo</h3>
        {template.logo_url && (
          <img
            src={`${api.BASE_URL}${template.logo_url}`}
            alt="Firm logo"
            className="logo-preview"
          />
        )}
        <input type="file" accept="image/png,image/jpeg,image/svg+xml" onChange={handleLogoUpload} disabled={uploading} />
      </section>

      <section className="branding-section">
        <h3>Colors</h3>
        <label>
          Primary color
          <input
            type="color"
            value={template.primary_color}
            onChange={(e) => handleSave({ primary_color: e.target.value })}
          />
          <span className="swatch-value">{template.primary_color}</span>
        </label>
        <label>
          Accent color
          <input
            type="color"
            value={template.accent_color}
            onChange={(e) => handleSave({ accent_color: e.target.value })}
          />
          <span className="swatch-value">{template.accent_color}</span>
        </label>
      </section>

      <section className="branding-section">
        <h3>Layout</h3>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={template.show_tag_column}
            onChange={(e) => handleSave({ show_tag_column: e.target.checked })}
          />
          Show item Tag column in the generated document
        </label>
      </section>

      <section className="branding-section">
        <h3>Default payment schedule</h3>
        {template.default_payment_terms.map((term, i) => (
          <div key={i} className="payment-term-row">
            <input
              type="number"
              value={term.pct}
              style={{ width: 70 }}
              onChange={(e) => {
                const terms = [...template.default_payment_terms];
                terms[i] = { ...terms[i], pct: Number(e.target.value) };
                handleSave({ default_payment_terms: terms });
              }}
            />
            %
            <input
              value={term.label}
              onChange={(e) => {
                const terms = [...template.default_payment_terms];
                terms[i] = { ...terms[i], label: e.target.value };
                handleSave({ default_payment_terms: terms });
              }}
            />
          </div>
        ))}
      </section>

      {saving && <p className="muted">Saving...</p>}
    </div>
  );
}
