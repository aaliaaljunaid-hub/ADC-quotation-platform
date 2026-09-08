import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Account } from "../types";
import { api } from "../api";

interface AccountContextValue {
  account: Account | null;
  role: string | null;
  isOwner: boolean;
  loading: boolean;
  login: (accessCode: string) => Promise<void>;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AccountContext = createContext<AccountContextValue | undefined>(undefined);

export function AccountProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!api.hasToken()) {
      setLoading(false);
      return;
    }
    api
      .getCurrentSession()
      .then((session) => {
        setAccount(session.account);
        setRole(session.role);
      })
      .catch(() => api.logout())
      .finally(() => setLoading(false));
  }, []);

  async function login(accessCode: string) {
    const result = await api.redeemAccessCode(accessCode);
    setAccount(result.account);
    setRole(result.role);
  }

  async function loginWithPassword(email: string, password: string) {
    const result = await api.loginWithPassword(email, password);
    setAccount(result.account);
    setRole(result.role);
  }

  function logout() {
    api.logout();
    setAccount(null);
    setRole(null);
  }

  return (
    <AccountContext.Provider
      value={{ account, role, isOwner: role === "owner", loading, login, loginWithPassword, logout }}
    >
      {children}
    </AccountContext.Provider>
  );
}

export function useAccount() {
  const ctx = useContext(AccountContext);
  if (!ctx) throw new Error("useAccount must be used within AccountProvider");
  return ctx;
}
