import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAccount } from "../context/AccountContext";
import { api, ApiError } from "../api";
import type { CatalogItem, Category, QuoteCalculateResponse, QuoteOut } from "../types";

const bhd = (v: string) => `BHD ${Number(v).toFixed(3)}`;

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.status === 402) {
    return "Your subscription needs to be renewed to continue building quotes.";
  }
  return fallback;
}

export default function QuoteBuilder() {
  const { account } = useAccount();
  const { quoteId } = useParams<{ quoteId: string }>();

  const [quote, setQuote] = useState<QuoteOut | null>(null);
  const [calc, setCalc] = useState<QuoteCalculateResponse | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pickedCatalogItemId, setPickedCatalogItemId] = useState("");
  const [qtyToAdd, setQtyToAdd] = useState("1");
  const [customLine, setCustomLine] = useState({ category_id: "", description: "", unit: "Item", quantity: "1", client_unit_price: "" });
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (!quoteId) return;
    const [q, c] = await Promise.all([api.getQuote(quoteId), api.calculateQuote(quoteId)]);
    setQuote(q);
    setCalc(c);
  }, [quoteId]);

  useEffect(() => {
    if (!quoteId || !account) return;
    Promise.all([api.getCategories(account.id), api.getCatalogItems(account.id)]).then(([cats, items]) => {
      setCategories(cats);
      setCatalogItems(items);
    });
    refresh()
      .catch((err) => setError(describeError(err, "Could not load this quote.")))
      .finally(() => setLoading(false));
  }, [quoteId, account, refresh]);

  async function handleAddFromCatalog(e: React.FormEvent) {
    e.preventDefault();
    if (!quoteId || !pickedCatalogItemId) return;
    setError(null);
    try {
      await api.addLineItem(quoteId, { catalog_item_id: pickedCatalogItemId, quantity: qtyToAdd || "1" });
      setPickedCatalogItemId("");
      setQtyToAdd("1");
      await refresh();
    } catch (err) {
      setError(describeError(err, "Could not add that item."));
    }
  }

  async function handleAddCustom(e: React.FormEvent) {
    e.preventDefault();
    if (!quoteId || !customLine.category_id || !customLine.description || !customLine.client_unit_price) return;
    setError(null);
    try {
      await api.addLineItem(quoteId, customLine);
      setCustomLine({ category_id: "", description: "", unit: "Item", quantity: "1", client_unit_price: "" });
      setShowCustomForm(false);
      await refresh();
    } catch (err) {
      setError(describeError(err, "Could not add that line item."));
    }
  }

  async function handleQtyChange(lineItemId: string, quantity: string) {
    if (!quoteId) return;
    setError(null);
    try {
      await api.updateLineItem(quoteId, lineItemId, { quantity });
      await refresh();
    } catch (err) {
      setError(describeError(err, "Could not update that line item."));
    }
  }

  async function handleDelete(lineItemId: string) {
    if (!quoteId) return;
    setError(null);
    try {
      await api.deleteLineItem(quoteId, lineItemId);
      await refresh();
    } catch (err) {
      setError(describeError(err, "Could not remove that line item."));
    }
  }

  async function handleGenerate(format: "docx" | "xlsx") {
    if (!quoteId) return;
    setGenerating(true);
    setError(null);
    try {
      const blob = format === "docx" ? await api.generateDocument(quoteId) : await api.generateSpreadsheet(quoteId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${quote?.quote_number || "quote"}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(describeError(err, "Could not generate the document."));
    } finally {
      setGenerating(false);
    }
  }

  if (loading) return <p>Loading quote...</p>;
  if (!quote || !calc) return <p className="error">{error || "Could not load this quote."}</p>;

  const isAccepted = quote.status === "accepted";
  const shareUrl = quote.share_token ? `${window.location.origin}/quote/${quote.share_token}` : null;

  async function handleCopyLink() {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }

  return (
    <div>
      <p><Link to={`/projects/${quote.project_id}`}>&larr; Back to project</Link></p>
      <h2>Quote {quote.quote_number}</h2>
      <p className="muted">Client: {quote.client_name} · Status: {quote.status}</p>

      {isAccepted ? (
        <div className="accepted-banner">
          <strong>Accepted</strong> by {quote.accepted_by_name} on{" "}
          {quote.accepted_at && new Date(quote.accepted_at).toLocaleString()}. This quote can no
          longer be edited.
        </div>
      ) : shareUrl && (
        <div className="share-link-box">
          <span className="muted">Client link:</span>
          <code>{shareUrl}</code>
          <button type="button" className="link-button" onClick={handleCopyLink}>
            {linkCopied ? "Copied!" : "Copy"}
          </button>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {!isAccepted && (
      <>
      <form className="inline-form" onSubmit={handleAddFromCatalog}>
        <select value={pickedCatalogItemId} onChange={(e) => setPickedCatalogItemId(e.target.value)} required>
          <option value="">Add catalog item...</option>
          {catalogItems.map((item) => (
            <option key={item.id} value={item.id}>
              {item.tag ? `${item.tag} — ` : ""}{item.description.slice(0, 60)}
            </option>
          ))}
        </select>
        <input
          type="number" step="0.001" value={qtyToAdd}
          onChange={(e) => setQtyToAdd(e.target.value)}
          style={{ width: 80 }}
        />
        <button type="submit">Add</button>
        <button type="button" className="link-button" onClick={() => setShowCustomForm(!showCustomForm)}>
          {showCustomForm ? "Cancel custom line" : "+ Custom line item"}
        </button>
      </form>

      {showCustomForm && (
        <form className="inline-form" onSubmit={handleAddCustom}>
          <select
            value={customLine.category_id}
            onChange={(e) => setCustomLine({ ...customLine, category_id: e.target.value })}
            required
          >
            <option value="">Category...</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <input
            placeholder="Description"
            value={customLine.description}
            onChange={(e) => setCustomLine({ ...customLine, description: e.target.value })}
            required
          />
          <input
            placeholder="Unit"
            value={customLine.unit}
            onChange={(e) => setCustomLine({ ...customLine, unit: e.target.value })}
            style={{ width: 80 }}
          />
          <input
            type="number" step="0.001" placeholder="Qty"
            value={customLine.quantity}
            onChange={(e) => setCustomLine({ ...customLine, quantity: e.target.value })}
            style={{ width: 80 }}
          />
          <input
            type="number" step="0.001" placeholder="Unit price"
            value={customLine.client_unit_price}
            onChange={(e) => setCustomLine({ ...customLine, client_unit_price: e.target.value })}
            style={{ width: 110 }}
            required
          />
          <button type="submit">Add custom line</button>
        </form>
      )}
      </>
      )}

      {calc.groups.map((group) => (
        <div key={group.category_id} className="category-group">
          <h3>{group.category_name}</h3>
          <table className="data-table">
            <thead>
              <tr><th>Description</th><th>Unit</th><th>Qty</th><th>Unit Price</th><th>Total</th><th></th></tr>
            </thead>
            <tbody>
              {group.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.description}</td>
                  <td>{item.unit}</td>
                  <td>
                    {isAccepted ? item.quantity : (
                      <input
                        type="number" step="0.001" defaultValue={item.quantity}
                        style={{ width: 70 }}
                        onBlur={(e) => e.target.value !== item.quantity && handleQtyChange(item.id, e.target.value)}
                      />
                    )}
                  </td>
                  <td>{bhd(item.client_unit_price)}</td>
                  <td>{bhd(item.line_total)}</td>
                  <td>
                    {!isAccepted && (
                      <button className="link-button danger" onClick={() => handleDelete(item.id)}>Remove</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr><td colSpan={4} className="subtotal-label">Category subtotal</td><td colSpan={2}>{bhd(group.category_subtotal)}</td></tr>
            </tfoot>
          </table>
        </div>
      ))}

      {calc.groups.length === 0 && <p className="empty-row">No line items yet — add one above.</p>}

      <div className="totals-box">
        <div><span>Subtotal</span><span>{bhd(calc.subtotal)}</span></div>
        <div><span>VAT ({Number(calc.vat_rate).toFixed(1)}%)</span><span>{bhd(calc.vat_amount)}</span></div>
        <div className="grand-total"><span>Grand Total</span><span>{bhd(calc.total)}</span></div>
      </div>

      {calc.payment_schedule.length > 0 && (
        <div className="payment-schedule">
          <h3>Payment Schedule</h3>
          {calc.payment_schedule.map((row, i) => (
            <div key={i}><span>{row.pct}% — {row.label}</span><span>{bhd(row.amount)}</span></div>
          ))}
        </div>
      )}

      <div className="generate-actions">
        <button onClick={() => handleGenerate("xlsx")} disabled={generating || calc.groups.length === 0} className="secondary">
          {generating ? "Generating..." : "Generate Excel"}
        </button>
        <button onClick={() => handleGenerate("docx")} disabled={generating || calc.groups.length === 0} className="primary">
          {generating ? "Generating..." : "Generate Word document"}
        </button>
      </div>
    </div>
  );
}
