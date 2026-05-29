export function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div style={{ padding: 16, background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, color: '#991b1b' }}>
      <p style={{ margin: 0 }}>{message}</p>
      {onRetry && (
        <button onClick={onRetry} style={{ marginTop: 8, color: '#991b1b', background: 'none', border: '1px solid #fca5a5', borderRadius: 4, cursor: 'pointer', padding: '4px 12px' }}>
          Retry
        </button>
      )}
    </div>
  )
}

export function PageError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div style={{ textAlign: 'center', padding: 60 }}>
      <h2 style={{ color: '#991b1b' }}>Something went wrong</h2>
      <p style={{ color: '#6b7280' }}>{message}</p>
      {onRetry && <button onClick={onRetry} style={{ marginTop: 16, padding: '8px 24px', cursor: 'pointer' }}>Retry</button>}
    </div>
  )
}
