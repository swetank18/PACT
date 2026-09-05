/** The six places. The order is the order in the sidebar. */
export const TABS = [
  { id: "dashboard", icon: "📊", label: "Dashboard" },
  { id: "mandates", icon: "📜", label: "Mandates" },
  { id: "transactions", icon: "🔍", label: "Transactions" },
  { id: "analytics", icon: "📈", label: "Analytics" },
  { id: "agents", icon: "🤖", label: "Agents" },
  { id: "settings", icon: "⚙️", label: "Settings" },
] as const;

export type Tab = (typeof TABS)[number]["id"];

export function isTab(v: string): v is Tab {
  return TABS.some((t) => t.id === v);
}
