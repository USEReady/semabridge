import { useNavigate } from 'react-router-dom';

export default function UnauthorizedPage() {
  const navigate = useNavigate();

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        gap: 16,
        color: 'var(--text-primary)',
      }}
    >
      <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>Access Denied</h1>
      <p style={{ fontSize: 14, color: 'var(--text-secondary)', margin: 0 }}>
        You don&apos;t have permission to access this page.
      </p>
      <button
        onClick={() => navigate(-1)}
        style={{
          marginTop: 8,
          padding: '8px 20px',
          borderRadius: 6,
          border: '1px solid var(--border-default)',
          background: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
          cursor: 'pointer',
          fontSize: 13,
        }}
      >
        Go Back
      </button>
    </div>
  );
}
