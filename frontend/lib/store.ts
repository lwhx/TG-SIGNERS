import { create } from "zustand";
export type Page =
  | "overview"
  | "accounts"
  | "tasks"
  | "automation"
  | "relay"
  | "relay-plus"
  | "faults"
  | "logs"
  | "tools"
  | "settings";
interface UI {
  page: Page;
  setPage: (page: Page) => void;
  revision: number;
  refresh: () => void;
  authOpen: boolean;
  setAuthOpen: (v: boolean) => void;
}
export const useUI = create<UI>((set) => ({
  page: "overview",
  setPage: (page) => set({ page }),
  revision: 0,
  refresh: () => set((s) => ({ revision: s.revision + 1 })),
  authOpen: false,
  setAuthOpen: (authOpen) => set({ authOpen }),
}));
