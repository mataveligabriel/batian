import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

const TerminalContext = createContext(null);

export function TerminalProvider({ children }) {
  const [tabs, setTabs] = useState([]);
  const [active, setActive] = useState(null);

  const openTab = useCallback((device) => {
    setTabs(prev => prev.find(t => t.id === device.id) ? prev : [...prev, device]);
    setActive(device.id);
  }, []);

  const closeTab = useCallback((id) => {
    setTabs(prev => {
      const next = prev.filter(t => t.id !== id);
      setActive(cur => (cur === id ? (next[next.length - 1]?.id ?? null) : cur));
      return next;
    });
  }, []);

  const value = useMemo(() => ({ tabs, active, setActive, openTab, closeTab }), [tabs, active, openTab, closeTab]);
  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export const useTerminal = () => useContext(TerminalContext);
