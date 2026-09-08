import type {
  Account, CatalogItem, Category, CostBuildupCalculateResponse, CostBuildupDefaults,
  DocTemplate, FitOutBenchmark, Project, PublicQuoteView, QuoteCalculateResponse, QuoteLineItemOut,
  QuoteOut, RateLibraryEntry, Room, SessionInfo, TermsTemplate, TokenResponse, User,
  WorkbookImportResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const TOKEN_STORAGE_KEY = "quotation_platform_token";

let authToken: string | null = localStorage.getItem(TOKEN_STORAGE_KEY);

function setAuthToken(token: string | null) {
  authToken = token;
  if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
  else localStorage.removeItem(TOKEN_STORAGE_KEY);
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    ...(init?.headers as Record<string, string> | undefined),
  };
  const resp = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new ApiError(resp.status, text || resp.statusText);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const get = <T,>(path: string) => request<T>(path);
const del = (path: string) => request<void>(path, { method: "DELETE" });

async function downloadBlob(path: string): Promise<Blob> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new ApiError(resp.status, text || resp.statusText);
  }
  return resp.blob();
}

export const api = {
  BASE_URL,

  redeemAccessCode: async (access_code: string) => {
    const result = await post<TokenResponse>("/auth/redeem", { access_code });
    setAuthToken(result.access_token);
    return result;
  },
  loginWithPassword: async (email: string, password: string) => {
    const result = await post<TokenResponse>("/auth/login", { email, password });
    setAuthToken(result.access_token);
    return result;
  },
  createAccount: (firm_name: string, access_code: string) =>
    post<Account>("/accounts", { firm_name, access_code }),
  getCurrentSession: () => get<SessionInfo>("/auth/me"),
  logout: () => setAuthToken(null),
  hasToken: () => authToken !== null,

  getUsers: (accountId: string) => get<User[]>(`/accounts/${accountId}/users`),
  createUser: (accountId: string, payload: Record<string, unknown>) =>
    post<User>(`/accounts/${accountId}/users`, payload),

  getCategories: (accountId: string) => get<Category[]>(`/accounts/${accountId}/categories`),
  createCategory: (accountId: string, payload: Omit<Category, "id" | "account_id">) =>
    post<Category>(`/accounts/${accountId}/categories`, payload),

  getCatalogItems: (accountId: string, categoryId?: string) =>
    get<CatalogItem[]>(
      `/accounts/${accountId}/catalog-items${categoryId ? `?category_id=${categoryId}` : ""}`
    ),
  createCatalogItem: (accountId: string, payload: Record<string, unknown>) =>
    post<CatalogItem>(`/accounts/${accountId}/catalog-items`, payload),
  updateCatalogItem: (accountId: string, itemId: string, payload: Record<string, unknown>) =>
    patch<CatalogItem>(`/accounts/${accountId}/catalog-items/${itemId}`, payload),
  deleteCatalogItem: (accountId: string, itemId: string) =>
    del(`/accounts/${accountId}/catalog-items/${itemId}`),

  getProjects: (accountId: string) => get<Project[]>(`/accounts/${accountId}/projects`),
  createProject: (accountId: string, payload: Record<string, unknown>) =>
    post<Project>(`/accounts/${accountId}/projects`, payload),
  getProject: (projectId: string) => get<Project>(`/projects/${projectId}`),
  importWorkbook: async (accountId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<WorkbookImportResponse>(`/accounts/${accountId}/import/workbook`, {
      method: "POST",
      body: form,
    });
  },

  getRooms: (projectId: string) => get<Room[]>(`/projects/${projectId}/rooms`),
  createRoom: (projectId: string, payload: Record<string, unknown>) =>
    post<Room>(`/projects/${projectId}/rooms`, payload),

  getDocTemplates: (accountId: string) => get<DocTemplate[]>(`/accounts/${accountId}/doc-templates`),
  updateDocTemplate: (accountId: string, templateId: string, payload: Record<string, unknown>) =>
    patch<DocTemplate>(`/accounts/${accountId}/doc-templates/${templateId}`, payload),
  uploadLogo: async (accountId: string, templateId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocTemplate>(`/accounts/${accountId}/doc-templates/${templateId}/logo`, {
      method: "POST",
      body: form,
    });
  },

  getTermsTemplates: (accountId: string) => get<TermsTemplate[]>(`/accounts/${accountId}/terms-templates`),
  createTermsTemplate: (accountId: string, payload: Record<string, unknown>) =>
    post<TermsTemplate>(`/accounts/${accountId}/terms-templates`, payload),

  getQuotesForProject: (projectId: string) => get<QuoteOut[]>(`/projects/${projectId}/quotes`),
  createQuote: (accountId: string, payload: Record<string, unknown>) =>
    post<QuoteOut>(`/accounts/${accountId}/quotes`, payload),
  getQuote: (quoteId: string) => get<QuoteOut>(`/quotes/${quoteId}`),
  calculateQuote: (quoteId: string) => get<QuoteCalculateResponse>(`/quotes/${quoteId}/calculate`),

  addLineItem: (quoteId: string, payload: Record<string, unknown>) =>
    post<QuoteLineItemOut>(`/quotes/${quoteId}/line-items`, payload),
  updateLineItem: (quoteId: string, lineItemId: string, payload: Record<string, unknown>) =>
    patch<QuoteLineItemOut>(`/quotes/${quoteId}/line-items/${lineItemId}`, payload),
  deleteLineItem: (quoteId: string, lineItemId: string) =>
    del(`/quotes/${quoteId}/line-items/${lineItemId}`),

  generateDocument: (quoteId: string) => downloadBlob(`/quotes/${quoteId}/generate-document`),
  generateSpreadsheet: (quoteId: string) => downloadBlob(`/quotes/${quoteId}/generate-spreadsheet`),

  getRateLibrary: (accountId: string, search?: string) =>
    get<RateLibraryEntry[]>(`/accounts/${accountId}/rate-library${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  createRateLibraryEntry: (accountId: string, payload: Record<string, unknown>) =>
    post<RateLibraryEntry>(`/accounts/${accountId}/rate-library`, payload),
  deleteRateLibraryEntry: (accountId: string, entryId: string) =>
    del(`/accounts/${accountId}/rate-library/${entryId}`),

  getCostBuildupDefaults: (accountId: string) => get<CostBuildupDefaults>(`/accounts/${accountId}/cost-buildup/defaults`),
  updateCostBuildupDefaults: (accountId: string, payload: Record<string, unknown>) =>
    patch<CostBuildupDefaults>(`/accounts/${accountId}/cost-buildup/defaults`, payload),
  calculateCostBuildup: (accountId: string, payload: Record<string, unknown>) =>
    post<CostBuildupCalculateResponse>(`/accounts/${accountId}/cost-buildup/calculate`, payload),

  getFitoutBenchmarks: (accountId: string, projectType?: string) =>
    get<FitOutBenchmark[]>(`/accounts/${accountId}/fitout-benchmarks${projectType ? `?project_type=${encodeURIComponent(projectType)}` : ""}`),

  // Public (client-facing) endpoints — no session token needed or sent.
  getPublicQuote: (shareToken: string) => get<PublicQuoteView>(`/public/quotes/${shareToken}`),
  acceptPublicQuote: (shareToken: string, accepted_by_name: string) =>
    post<PublicQuoteView>(`/public/quotes/${shareToken}/accept`, { accepted_by_name }),
};

export { ApiError };
