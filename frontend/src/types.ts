// Mirrors app/models.py on the backend. All money fields are strings on the
// wire (Pydantic serializes Decimal as string) — parse with Number() only
// for display math, never to round-trip back to the API.

export interface Account {
  id: string;
  firm_name: string;
  subscription_status: string;
  vat_rate_default: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  account: Account;
  role: string;
}

export interface SessionInfo {
  account: Account;
  role: string;
}

export interface User {
  id: string;
  account_id: string;
  name: string;
  email: string;
  role: "owner" | "editor";
}

export interface Category {
  id: string;
  account_id: string | null;
  code: string;
  name: string;
  two_letter_code: string;
  process_sequence: number;
}

export interface CatalogItem {
  id: string;
  account_id: string;
  category_id: string;
  sub_category: string | null;
  tag: string | null;
  description: string;
  default_unit: string;
  dimensions: string | null;
  material: string | null;
  finish: string | null;
  manufacturer: string | null;
  supplier_unit_cost: string | null;
  client_unit_price: string | null;
  delivery_timeline: string | null;
  installation_requirement: string | null;
  warranty_years: number | null;
  sustainability_cert: string | null;
  maintenance_notes: string | null;
  is_active: boolean;
  profit: string | null;
  margin_pct: string | null;
}

export interface Project {
  id: string;
  account_id: string;
  project_number: string | null;
  project_name: string;
  client_name: string;
  client_contact: string | null;
  location: string | null;
  created_at: string;
}

export interface Room {
  id: string;
  project_id: string;
  floor_name: string;
  room_name: string;
  room_tag: string | null;
  area_sqm: string | null;
  perimeter_mm: string | null;
  ceiling_type: string | null;
  wall_finish: string | null;
  floor_finish: string | null;
}

export interface PaymentTerm {
  pct: number;
  label: string;
}

export interface DocTemplate {
  id: string;
  account_id: string;
  name: string;
  layout_key: string;
  logo_asset_path: string | null;
  logo_url: string | null;
  primary_color: string;
  accent_color: string;
  show_tag_column: boolean;
  default_payment_terms: PaymentTerm[];
  is_default: boolean;
}

export interface TermsSection {
  heading?: string | null;
  body: string;
}

export interface TermsTemplate {
  id: string;
  account_id: string;
  name: string;
  sections: TermsSection[];
  is_default: boolean;
}

export interface QuoteOut {
  id: string;
  account_id: string;
  project_id: string;
  quote_number: string | null;
  client_name: string;
  status: string;
  vat_rate_snapshot: string;
  payment_terms: PaymentTerm[];
  doc_template_id: string | null;
  created_at: string;
  share_token: string | null;
  accepted_at: string | null;
  accepted_by_name: string | null;
}

export interface QuoteLineItemOut {
  id: string;
  quote_id: string;
  catalog_item_id: string | null;
  category_id: string;
  tag: string | null;
  description: string;
  unit: string;
  quantity: string;
  client_unit_price: string;
  line_total: string;
  room_id: string | null;
  sort_order: number;
}

export interface PaymentScheduleRow {
  pct: number;
  label: string;
  amount: string;
}

export interface CategoryGroupOut {
  category_id: string;
  category_name: string;
  process_sequence: number;
  items: QuoteLineItemOut[];
  category_subtotal: string;
}

export interface QuoteCalculateResponse {
  quote_id: string;
  groups: CategoryGroupOut[];
  subtotal: string;
  vat_rate: string;
  vat_amount: string;
  total: string;
  payment_schedule: PaymentScheduleRow[];
}

export interface WorkbookImportResponse {
  project: Project;
  quote: QuoteOut;
  line_items_imported: number;
  catalog_items_created: number;
  catalog_items_updated: number;
}

export interface RateLibraryEntry {
  id: string;
  account_id: string | null;
  area: string | null;
  category: string | null;
  item_description: string;
  unit: string | null;
  supplier_cost: string | null;
  client_unit_price: string | null;
  rate_basis: "unit_rate" | "lump_sum" | "tbc";
  source_note: string | null;
  imported_at: string | null;
}

export interface CostBuildupDefaults {
  account_id: string;
  wastage_pct: string;
  overhead_pct: string;
  margin_pct: string;
}

export interface CostBuildupCalculateResponse {
  wastage: string;
  prime_cost: string;
  overhead: string;
  total_cost: string;
  margin: string;
  proposed_unit_rate: string;
  proposed_total: string;
}

export interface FitOutBenchmark {
  id: string;
  account_id: string | null;
  project_type: string;
  trade_element: string;
  low_bhd_per_sqm: string;
  high_bhd_per_sqm: string;
}

export interface PublicQuoteLineItem {
  description: string;
  unit: string;
  quantity: string;
  line_total: string;
}

export interface PublicCategoryGroup {
  category_name: string;
  items: PublicQuoteLineItem[];
  category_subtotal: string;
}

export interface PublicQuoteView {
  firm_name: string;
  logo_url: string | null;
  primary_color: string;
  accent_color: string;
  project_name: string;
  client_name: string;
  quote_number: string | null;
  status: string;
  groups: PublicCategoryGroup[];
  subtotal: string;
  vat_rate: string;
  vat_amount: string;
  total: string;
  payment_schedule: PaymentScheduleRow[];
  terms_sections: TermsSection[];
  accepted_at: string | null;
  accepted_by_name: string | null;
}
