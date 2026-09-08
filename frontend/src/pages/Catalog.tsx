import { useEffect, useState } from "react";
import { useAccount } from "../context/AccountContext";
import { api } from "../api";
import type { CatalogItem, Category } from "../types";

const money = (v: string | null) => (v === null ? "-" : `BHD ${Number(v).toFixed(3)}`);
const pct = (v: string | null) => (v === null ? "-" : `${(Number(v) * 100).toFixed(1)}%`);

export default function Catalog() {
  const { account } = useAccount();
  const [categories, setCategories] = useState<Category[]>([]);
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState({
    category_id: "",
    description: "",
    default_unit: "Item",
    tag: "",
    supplier_unit_cost: "",
    client_unit_price: "",
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!account) return;
    Promise.all([api.getCategories(account.id), api.getCatalogItems(account.id)]).then(
      ([cats, cItems]) => {
        setCategories(cats);
        setItems(cItems);
        setLoading(false);
      }
    );
  }, [account]);

  async function refreshItems() {
    if (!account) return;
    setItems(await api.getCatalogItems(account.id));
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!account || !form.category_id || !form.description) return;
    setSaving(true);
    try {
      await api.createCatalogItem(account.id, {
        category_id: form.category_id,
        description: form.description,
        default_unit: form.default_unit || "Item",
        tag: form.tag || null,
        supplier_unit_cost: form.supplier_unit_cost || null,
        client_unit_price: form.client_unit_price || null,
      });
      setForm({ ...form, description: "", tag: "", supplier_unit_cost: "", client_unit_price: "" });
      await refreshItems();
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(itemId: string) {
    if (!account) return;
    await api.deleteCatalogItem(account.id, itemId);
    await refreshItems();
  }

  if (loading) return <p>Loading catalog...</p>;

  const categoryById = new Map(categories.map((c) => [c.id, c]));
  const visibleItems = selectedCategory ? items.filter((i) => i.category_id === selectedCategory) : items;

  return (
    <div>
      <h2>Item Catalog</h2>

      <form className="inline-form" onSubmit={handleAdd}>
        <select
          value={form.category_id}
          onChange={(e) => setForm({ ...form, category_id: e.target.value })}
          required
        >
          <option value="">Category...</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.code}. {c.name}
            </option>
          ))}
        </select>
        <input
          placeholder="Description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          required
        />
        <input
          placeholder="Unit"
          value={form.default_unit}
          onChange={(e) => setForm({ ...form, default_unit: e.target.value })}
          style={{ width: 80 }}
        />
        <input
          placeholder="Tag"
          value={form.tag}
          onChange={(e) => setForm({ ...form, tag: e.target.value })}
          style={{ width: 80 }}
        />
        <input
          placeholder="Supplier cost"
          type="number"
          step="0.001"
          value={form.supplier_unit_cost}
          onChange={(e) => setForm({ ...form, supplier_unit_cost: e.target.value })}
          style={{ width: 110 }}
        />
        <input
          placeholder="Client price"
          type="number"
          step="0.001"
          value={form.client_unit_price}
          onChange={(e) => setForm({ ...form, client_unit_price: e.target.value })}
          style={{ width: 110 }}
        />
        <button type="submit" disabled={saving}>Add item</button>
      </form>

      <div className="filter-row">
        <label>
          Filter by category:{" "}
          <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)}>
            <option value="">All</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>Tag</th>
            <th>Category</th>
            <th>Description</th>
            <th>Unit</th>
            <th>Supplier Cost</th>
            <th>Client Price</th>
            <th>Profit</th>
            <th>Margin</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visibleItems.map((item) => (
            <tr key={item.id}>
              <td>{item.tag || "-"}</td>
              <td>{categoryById.get(item.category_id)?.name || "-"}</td>
              <td>{item.description}</td>
              <td>{item.default_unit}</td>
              <td>{money(item.supplier_unit_cost)}</td>
              <td>{money(item.client_unit_price)}</td>
              <td>{money(item.profit)}</td>
              <td>{pct(item.margin_pct)}</td>
              <td>
                <button className="link-button danger" onClick={() => handleDelete(item.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
          {visibleItems.length === 0 && (
            <tr>
              <td colSpan={9} className="empty-row">No catalog items yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
