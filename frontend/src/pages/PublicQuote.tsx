import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { PublicQuoteView } from "../types";

const bhd = (v: string) => `BHD ${Number(v).toFixed(3)}`;
const qty = (v: string) => Number(v).toString();

export default function PublicQuote() {
  const { shareToken } = useParams<{ shareToken: string }>();
  const [view, setView] = useState<PublicQuoteView | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const [name, setName] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!shareToken) return;
    api
      .getPublicQuote(shareToken)
      .then(setView)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) setNotFound(true);
        else setError("Could not load this quote.");
      })
      .finally(() => setLoading(false));
  }, [shareToken]);

  async function handleAccept(e: React.FormEvent) {
    e.preventDefault();
    if (!shareToken || !name.trim()) return;
    setAccepting(true);
    setError(null);
    try {
      const updated = await api.acceptPublicQuote(shareToken, name.trim());
      setView(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not record your acceptance.");
    } finally {
      setAccepting(false);
    }
  }

  if (loading) return <div style={styles.centered}>Loading quotation...</div>;
  if (notFound || !view) return <div style={styles.centered}>This quotation link isn't valid.</div>;

  const primary = view.primary_color;
  const accent = view.accent_color;

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        {view.logo_url && <img src={`${api.BASE_URL}${view.logo_url}`} alt={view.firm_name} style={styles.logo} />}
        <h1 style={{ ...styles.firmName, color: primary }}>{view.firm_name}</h1>
        <div style={{ ...styles.subtitle, color: accent }}>PROJECT QUOTATION</div>

        <div style={styles.infoBlock}>
          <div><strong>To:</strong> {view.client_name}</div>
          <div><strong>Project:</strong> {view.project_name}</div>
          {view.quote_number && <div><strong>Quotation No.:</strong> {view.quote_number}</div>}
        </div>

        {view.groups.map((group) => (
          <div key={group.category_name} style={{ marginBottom: "1.25rem" }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={{ ...styles.th, background: primary }} colSpan={3}>{group.category_name}</th>
                </tr>
                <tr>
                  <th style={styles.thLight}>Description</th>
                  <th style={styles.thLight}>Unit / Qty</th>
                  <th style={{ ...styles.thLight, textAlign: "right" }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {group.items.map((item, i) => (
                  <tr key={i}>
                    <td style={styles.td}>{item.description}</td>
                    <td style={styles.td}>{qty(item.quantity)} {item.unit}</td>
                    <td style={{ ...styles.td, textAlign: "right" }}>{bhd(item.line_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

        <div style={styles.totalsBox}>
          <div style={styles.totalsRow}><span>Subtotal</span><span>{bhd(view.subtotal)}</span></div>
          <div style={styles.totalsRow}><span>VAT ({Number(view.vat_rate).toFixed(1)}%)</span><span>{bhd(view.vat_amount)}</span></div>
          <div style={{ ...styles.totalsRow, fontWeight: 700, fontSize: "1.15rem", color: accent, borderTop: "1px solid #ddd", paddingTop: "0.5rem" }}>
            <span>Grand Total</span><span>{bhd(view.total)}</span>
          </div>
        </div>

        {view.payment_schedule.length > 0 && (
          <div style={{ marginBottom: "1.5rem" }}>
            <h3>Terms Of Payment</h3>
            {view.payment_schedule.map((row, i) => (
              <div key={i} style={styles.totalsRow}>
                <span>{row.pct}% — {row.label}</span>
                <span>{bhd(row.amount)}</span>
              </div>
            ))}
          </div>
        )}

        {view.terms_sections.length > 0 && (
          <div style={{ marginBottom: "1.5rem" }}>
            <h3>Terms &amp; Conditions</h3>
            {view.terms_sections.map((section, i) => (
              <div key={i} style={{ marginBottom: "0.75rem" }}>
                {section.heading && <div style={{ fontWeight: 600 }}>{section.heading}</div>}
                <div style={{ whiteSpace: "pre-wrap" }}>{section.body}</div>
              </div>
            ))}
          </div>
        )}

        {view.status === "accepted" ? (
          <div style={{ ...styles.acceptedBanner, borderColor: accent }}>
            <strong>Accepted</strong> by {view.accepted_by_name} on{" "}
            {view.accepted_at && new Date(view.accepted_at).toLocaleString()}.
          </div>
        ) : (
          <form onSubmit={handleAccept} style={styles.acceptForm}>
            <h3>Accept this quotation</h3>
            <p style={{ color: "#666", fontSize: "0.9rem" }}>
              Typing your name below and clicking Accept confirms you approve this quotation.
            </p>
            <input
              placeholder="Your full name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={styles.nameInput}
              required
            />
            {error && <p style={{ color: "#b3261e" }}>{error}</p>}
            <button type="submit" disabled={accepting} style={{ ...styles.acceptButton, background: primary }}>
              {accepting ? "Recording..." : "Accept Quotation"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { minHeight: "100vh", background: "#f5f5f5", padding: "2rem 1rem", fontFamily: "system-ui, sans-serif" },
  centered: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "system-ui, sans-serif" },
  card: { maxWidth: 720, margin: "0 auto", background: "white", borderRadius: 8, padding: "2rem", boxShadow: "0 1px 4px rgba(0,0,0,0.1)" },
  logo: { maxHeight: 70, marginBottom: "1rem" },
  firmName: { margin: 0, fontSize: "1.5rem" },
  subtitle: { fontWeight: 700, fontSize: "0.9rem", marginBottom: "1.5rem" },
  infoBlock: { marginBottom: "1.5rem", lineHeight: 1.8 },
  table: { width: "100%", borderCollapse: "collapse", marginBottom: "0.5rem" },
  th: { color: "white", padding: "0.5rem 0.75rem", textAlign: "left" },
  thLight: { background: "#f0f0f0", padding: "0.4rem 0.75rem", textAlign: "left", fontSize: "0.85rem" },
  td: { padding: "0.5rem 0.75rem", borderBottom: "1px solid #eee", fontSize: "0.9rem" },
  totalsBox: { maxWidth: 320, marginLeft: "auto", marginBottom: "1.5rem" },
  totalsRow: { display: "flex", justifyContent: "space-between", padding: "0.25rem 0" },
  acceptedBanner: { border: "2px solid", borderRadius: 6, padding: "1rem", textAlign: "center" },
  acceptForm: { borderTop: "1px solid #eee", paddingTop: "1.5rem" },
  nameInput: { width: "100%", padding: "0.6rem", marginBottom: "0.75rem", border: "1px solid #ccc", borderRadius: 4, fontSize: "1rem" },
  acceptButton: { color: "white", border: "none", padding: "0.7rem 1.5rem", borderRadius: 4, fontSize: "1rem", cursor: "pointer" },
};
