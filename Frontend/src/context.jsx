import { createContext, useContext } from 'react';
export const WorkspaceContext = createContext(null);
export const useWorkspace = () => useContext(WorkspaceContext);
export const navigate = (path) => {
  window.location.hash = path;
};
export const ACTIVE = ['PENDING_DISPATCH', 'QUEUED', 'RUNNING'];
export const number = (value) => new Intl.NumberFormat('en-US').format(value || 0);
export const date = (value) =>
  new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
