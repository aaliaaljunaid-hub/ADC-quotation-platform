import { useEffect, useState } from "react";
import { useAccount } from "../context/AccountContext";
import { api } from "../api";
import type {
  CatalogItem, CostBuildupCalculateResponse, CostBuildupDefaults, FitOutBenchmark, RateLibraryEntry,
} from "../types";

const bhd = (v: string) => `BHD ${Number(v).toFixed(3)}`;

function CostBuildupSection({ accountId }: { accountId: string }) {
  const [defaults, setDefaults] = useState<CostBuildupDefaults | null>(null);
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [form, setForm] = useState({ material_cost_per_unit: "", labour_cost_per_unit: "", quantity: "1" });
  const [result, setResult] = useState<CostBuildupCalculateResponse | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [pushTargetId, setPushTargetId] = useState("");
  const [pushStatus, setPushStatus] = useState<string | null>(null);

  useEffect(() => {
    api.getCostBuildupDefaults(accountId).then(setDefaults);
    api.getCatalogItems(accountId).then(setCatalogItems);
  }, [accountId]);

  async function handleSaveDefaults(field: keyof CostBuildupDefaults, value: string) {
    if (!defaults) return;
    const updated = await api.updateCostBuildupDefaults(accountId, { [field]: value });
    setDefaults(updated);
  }

  async function handleCalculate(e: React.FormEvent) {
    e.preventDefault();
    setCalculating(true);
    setPushStatus(null);
    try {
      const r = await api.calculateCostBuildup(accountId, {
        material_cost_per_unit: form.material_cost_per_unit || "0",
        labour_cost_per_unit: form.labour_cost_per_unit || "0",
        quantity: form.quantity || "1",
      });
      setResult(r);
    } finally {
      setCalculating(false);
    }
  }

  async function handlePushToCatalog() {
    if (!result || !pushTargetId) return;
    setPushStatus(null);
    try {
      await api.updateCatalogItem(accountId, pushTargetId, { client_unit_price: result.proposed_unit_rate });
      const item = catalogItems.find((i) => i.id === pushTargetId);
      setPushStatus(`Set ${item?.description || "item"}'s client price to ${bhd(result.proposed_unit_rate)}.`);
    } catch {
      setPushStatus("Could not update that catalog item.");
    }
  }

  if (!defaults) return <p>Loading cost build-up...</p>;

  return (
    <section className="branding-section">
      <h3>Cost Build-Up Calculator</h3>
      <p className="muted">
        Estimate a client unit price from material + labour cost, using wastage/overhead/margin
        assumptions — mirrors the source workbook's Rate Build-Up sheet. Nothing is saved until
        you push a result to a catalog item.
      </p>

      <div className="buildup-defaults">
        <label>
          Wastage %
          <input
            type="number" step="0.1" defaultValue={defaults.wastage_pct}
            onBlur={(e) => handleSaveDefaults("wastage_pct", e.target.value)}
          />
        </label>
        <label>
          Overhead %
          <input
            type="number" step="0.1" defaultValue={defaults.overhead_pct}
            onBlur={(e) => handleSaveDefaults("overhead_pct", e.target.value)}
          />
        </label>
        <label>
          Margin %
          <input
            type="number" step="0.1" defaultValue={defaults.margin_pct}
            onBlur={(e) => handleSaveDefaults("margin_pct", e.target.value)}
          />
        </label>
      </div>

      <form className="inline-form" onSubmit={handleCalculate}>
        <input
          placeholder="Material cost / unit" type="number" step="0.001"
          value={form.material_cost_per_unit}
          onChange={(e) => setForm({ ...form, material_cost_per_unit: e.target.value })}
        />
        <input
          placeholder="Labour cost / unit" type="number" step="0.001"
          value={form.labour_cost_per_unit}
          onChange={(e) => setForm({ ...form, labour_cost_per_unit: e.target.value })}
        />
        <input
          placeholder="Quantity" type="number" step="0.001"
          value={form.quantity}
          onChange={(e) => setForm({ ...form, quantity: e.target.value })}
          style={{ width: 90 }}
        />
        <button type="submit" disabled={calculating}>{calculating ? "Calculating..." : "Calculate"}</button>
      </form>

      {result && (
        <>
          <table className="data-table">
            <tbody>
              <tr><td>Wastage</td><td>{bhd(result.wastage)}</td></tr>
              <tr><td>Prime Cost</td><td>{bhd(result.prime_cost)}</td></tr>
              <tr><td>Overhead</td><td>{bhd(result.overhead)}</td></tr>
              <tr><td>Total Cost</td><td>{bhd(result.total_cost)}</td></tr>
              <tr><td>Margin</td><td>{bhd(result.margin)}</td></tr>
              <tr><td><strong>Proposed Unit Rate</strong></td><td><strong>{bhd(result.proposed_unit_rate)}</strong></td></tr>
              <tr><td>Proposed Total (x quantity)</td><td>{bhd(result.proposed_total)}</td></tr>
            </tbody>
          </table>

          <div className="inline-form">
            <select value={pushTargetId} onChange={(e) => setPushTargetId(e.target.value)}>
              <option value="">Push proposed unit rate to catalog item...</option>
              {catalogItems.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.tag ? `${item.tag} — ` : ""}{item.description.slice(0, 60)}
                </option>
              ))}
            </select>
            <button type="button" onClick={handlePushToCatalog} disabled={!pushTargetId}>
              Set as client price
            </button>
          </div>
          {pushStatus && <p className="muted">{pushStatus}</p>}
        </>
      )}
    </section>
  );
}

function RateLibrarySection({ accountId }: { accountId: string }) {
  const [entries, setEntries] = useState<RateLibraryEntry[]>([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({
    area: "", category: "", item_description: "", unit: "", supplier_cost: "", client_unit_price: "",
  });
  const [saving, setSaving] = useState(false);

  async function refresh(searchTerm?: string) {
    setEntries(await api.getRateLibrary(accountId, searchTerm));
  }

  useEffect(() => { refresh(); }, [accountId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    await refresh(search || undefined);
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!form.item_description) return;
    setSaving(true);
    try {
      await api.createRateLibraryEntry(accountId, {
        area: form.area || null,
        category: form.category || null,
        item_description: form.item_description,
        unit: form.unit || null,
        supplier_cost: form.supplier_cost || null,
        client_unit_price: form.client_unit_price || null,
      });
      setForm({ area: "", category: "", item_description: "", unit: "", supplier_cost: "", client_unit_price: "" });
      await refresh(search || undefined);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    await api.deleteRateLibraryEntry(accountId, id);
    await refresh(search || undefined);
  }

  return (
    <section className="branding-section">
      <h3>Rate Library</h3>
      <p className="muted">
        Frozen historical reference rates — nothing here links live to a catalog item; copy a
        number across and adjust it yourself.
      </p>

      <form className="inline-form" onSubmit={handleSearch}>
        <input placeholder="Search descriptions..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <button type="submit">Search</button>
      </form>

      <form className="inline-form" onSubmit={handleAdd}>
        <input placeholder="Area" value={form.area} onChange={(e) => setForm({ ...form, area: e.target.value })} style={{ width: 100 }} />
        <input placeholder="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} style={{ width: 120 }} />
        <input placeholder="Description" value={form.item_description} onChange={(e) => setForm({ ...form, item_description: e.target.value })} required />
        <input placeholder="Unit" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} style={{ width: 70 }} />
        <input placeholder="Supplier cost" type="number" step="0.001" value={form.supplier_cost} onChange={(e) => setForm({ ...form, supplier_cost: e.target.value })} style={{ width: 110 }} />
        <input placeholder="Client price" type="number" step="0.001" value={form.client_unit_price} onChange={(e) => setForm({ ...form, client_unit_price: e.target.value })} style={{ width: 110 }} />
        <button type="submit" disabled={saving}>Add entry</button>
      </form>

      <table className="data-table">
        <thead>
          <tr><th>Area</th><th>Category</th><th>Description</th><th>Unit</th><th>Supplier Cost</th><th>Client Price</th><th></th></tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id}>
              <td>{entry.area || "-"}</td>
              <td>{entry.category || "-"}</td>
              <td>{entry.item_description}</td>
              <td>{entry.unit || "-"}</td>
              <td>{entry.supplier_cost ? bhd(entry.supplier_cost) : "-"}</td>
              <td>{entry.client_unit_price ? bhd(entry.client_unit_price) : "-"}</td>
              <td><button className="link-button danger" onClick={() => handleDelete(entry.id)}>Delete</button></td>
            </tr>
          ))}
          {entries.length === 0 && <tr><td colSpan={7} className="empty-row">No rate library entries yet.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

function FitOutBenchmarksSection({ accountId }: { accountId: string }) {
  const [benchmarks, setBenchmarks] = useState<FitOutBenchmark[]>([]);
  const [projectType, setProjectType] = useState("");

  useEffect(() => {
    api.getFitoutBenchmarks(accountId, projectType || undefined).then(setBenchmarks);
  }, [accountId, projectType]);

  const projectTypes = Array.from(new Set(benchmarks.map((b) => b.project_type))).sort();

  return (
    <section className="branding-section">
      <h3>Fit-Out Benchmarks</h3>
      <p className="muted">
        Indicative BHD/m² bands by project type and trade — a sense-check only, never wired
        into pricing.
      </p>
      <div className="filter-row">
        <label>
          Project type:{" "}
          <select value={projectType} onChange={(e) => setProjectType(e.target.value)}>
            <option value="">All</option>
            {(projectTypes.length ? projectTypes : ["Office", "Retail", "Restaurant"]).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
      </div>
      <table className="data-table">
        <thead>
          <tr><th>Project Type</th><th>Trade Element</th><th>Low (BHD/m²)</th><th>High (BHD/m²)</th></tr>
        </thead>
        <tbody>
          {benchmarks.map((b) => (
            <tr key={b.id}>
              <td>{b.project_type}</td>
              <td>{b.trade_element}</td>
              <td>{Number(b.low_bhd_per_sqm).toFixed(2)}</td>
              <td>{Number(b.high_bhd_per_sqm).toFixed(2)}</td>
            </tr>
          ))}
          {benchmarks.length === 0 && <tr><td colSpan={4} className="empty-row">No benchmarks found.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

export default function EstimationAids() {
  const { account } = useAccount();
  if (!account) return null;

  return (
    <div>
      <h2>Estimation Aids</h2>
      <p className="muted">
        Reference tools, not part of the quote-building critical path — nothing here writes to
        a quote automatically.
      </p>
      <CostBuildupSection accountId={account.id} />
      <RateLibrarySection accountId={account.id} />
      <FitOutBenchmarksSection accountId={account.id} />
    </div>
  );
}
