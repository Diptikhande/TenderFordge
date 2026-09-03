import type { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  value: string | number;
  detail: string;
  accent?: 'blue' | 'amber' | 'green' | 'slate';
  icon?: ReactNode;
}

export function StatCard({ label, value, detail, accent = 'slate', icon }: StatCardProps) {
  return <article className={`stat-card stat-card--${accent}`}><div className="stat-card-heading"><span>{label}</span>{icon}</div><strong>{value}</strong><p>{detail}</p></article>;
}
