interface EmptyStateProps {
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
}

export function EmptyState({ title, description, actionLabel, onAction }: EmptyStateProps) {
  return (
    <div style={{ textAlign: 'center', padding: 60, color: '#6b7280' }}>
      <p style={{ fontSize: 18, fontWeight: 500, color: '#374151' }}>{title}</p>
      {description && <p style={{ marginTop: 8 }}>{description}</p>}
      {actionLabel && onAction && (
        <button onClick={onAction} style={{ marginTop: 16, padding: '8px 24px', cursor: 'pointer', background: '#3b82f6', color: 'white', border: 'none', borderRadius: 6 }}>
          {actionLabel}
        </button>
      )}
    </div>
  )
}
