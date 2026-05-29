export function SkeletonCard() {
  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: 16, marginBottom: 12 }}>
      <div style={{ height: 20, width: '60%', background: '#f3f4f6', borderRadius: 4, marginBottom: 8 }} />
      <div style={{ height: 14, width: '40%', background: '#f3f4f6', borderRadius: 4, marginBottom: 6 }} />
      <div style={{ height: 14, width: '30%', background: '#f3f4f6', borderRadius: 4 }} />
    </div>
  )
}

export function SkeletonRow() {
  return (
    <tr>
      {[40, 120, 80, 80, 100].map((w, i) => (
        <td key={i} style={{ padding: '10px 12px' }}>
          <div style={{ height: 14, width: w, background: '#f3f4f6', borderRadius: 4 }} />
        </td>
      ))}
    </tr>
  )
}

export function Spinner() {
  return <div style={{ textAlign: 'center', padding: 40, color: '#6b7280' }}>Loading...</div>
}
