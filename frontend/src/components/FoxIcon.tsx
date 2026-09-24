// ============================================================================
// FoxIcon.tsx — Fuchser mascot: geometric fox head as an SVG.
// ============================================================================
// Drawn ourselves (no license pilfering needed): silhouette with pointed
// ears, semi-transparent inner ears, eyes and nose as a cut-out shape.
// Colored via currentColor — so it automatically fits the theme.

export function FoxIcon({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="Fuchser Logo"
    >
      {/* Head silhouette with ears */}
      <path
        d="M10 4 L26 14 H38 L54 4 L56 26 L32 60 L8 26 Z"
        fill="currentColor"
      />
      {/* Inner ears (lighter) */}
      <path d="M12 8 L24 15 L18 24 Z" fill="#fff" opacity="0.4" />
      <path d="M52 8 L40 15 L46 24 Z" fill="#fff" opacity="0.4" />
      {/* Eyes */}
      <circle cx="24" cy="27" r="3" fill="#fff" opacity="0.95" />
      <circle cx="40" cy="27" r="3" fill="#fff" opacity="0.95" />
      {/* Snout/nose */}
      <path d="M32 36 L38 42 L32 50 L26 42 Z" fill="#fff" opacity="0.9" />
    </svg>
  );
}
