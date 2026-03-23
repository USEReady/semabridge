/**
 * Color System - Consistent color values for styled components
 * Ensures uniform colors across the application
 * 
 * Usage:
 * import { COLORS } from '../theme/colors';
 * 
 * style={{
 *   background: COLORS.bg.input,
 *   color: COLORS.text.primary,
 *   border: `1px solid ${COLORS.border.main}`
 * }}
 */

export const COLORS = {
  /* ===== BACKGROUNDS ===== */
  bg: {
    app: 'var(--bg-app)',
    main: 'var(--bg-main)',
    surface: 'var(--bg-surface)',
    surfaceHover: 'var(--bg-surface-hover)',
    surfaceRaised: 'var(--bg-surface-raised)',
    primary: 'var(--bg-primary)',
    input: 'var(--bg-input)',
    inputHover: 'var(--bg-input-hover)',
    button: 'var(--bg-button)',
    buttonHover: 'var(--bg-button-hover)',
    backdrop: 'var(--bg-backdrop)',
  },

  /* ===== TEXT ===== */
  text: {
    primary: 'var(--text-primary)',      // Main text (high contrast)
    secondary: 'var(--text-secondary)',  // Reduced emphasis
    tertiary: 'var(--text-tertiary)',    // Even more reduced
    quaternary: 'var(--text-quaternary)', // Minimal emphasis (placeholders)
    inverse: 'var(--text-inverse)',      // Inverse text on colored backgrounds
    muted: 'var(--text-muted)',          // Disabled/muted text
  },

  /* ===== BORDERS ===== */
  border: {
    color: 'var(--border-color)',
    main: 'var(--border-main)',          // Default borders
    light: 'var(--border-light)',        // Subtle borders
    focus: 'var(--border-focus)',        // Focus state borders
  },

  /* ===== ACCENT (BRAND COLOR) ===== */
  accent: {
    primary: 'var(--accent-blue)',       // Primary brand color
    hover: 'var(--accent-blue-hover)',   // Hover state
    dark: 'var(--accent-blue-dark)',     // Darker variant (light mode)
    light: 'var(--accent-blue-light)',   // Lighter variant (for light mode)
  },

  /* ===== STATUS COLORS ===== */
  status: {
    // Success - Green
    success: 'var(--color-success)',
    successMuted: 'var(--color-success-muted)',
    successBg: 'var(--color-success-bg)',

    // Warning - Yellow/Orange
    warning: 'var(--color-warning)',
    warningMuted: 'var(--color-warning-muted)',
    warningBg: 'var(--color-warning-bg)',

    // Error - Red
    error: 'var(--color-error)',
    errorMuted: 'var(--color-error-muted)',
    errorBg: 'var(--color-error-bg)',

    // Info - Blue
    info: 'var(--color-info)',
    infoMuted: 'var(--color-info-muted)',
  },

  /* ===== NEUTRAL GREYS ===== */
  grey: {
    base: 'var(--color-grey)',
    light: 'var(--color-grey-light)',
    dark: 'var(--color-grey-dark)',
  },

  /* ===== SHADOWS ===== */
  shadow: {
    sm: 'var(--shadow-sm)',
    md: 'var(--shadow-md)',
    lg: 'var(--shadow-lg)',
    focus: 'var(--shadow-focus)',
  },

  /* ===== TRANSITIONS ===== */
  transition: 'var(--transition-normal)',
};

/**
 * Common Style Objects - Reusable style combinations
 * Usage:
 * import { STYLES } from '../theme/colors';
 * 
 * <input style={STYLES.input} />
 * <button style={STYLES.buttonPrimary}>Click me</button>
 */

export const STYLES = {
  /* ===== INPUTS ===== */
  input: {
    display: 'block',
    width: '100%',
    background: COLORS.bg.input,
    border: `1px solid ${COLORS.border.main}`,
    borderRadius: 6,
    color: COLORS.text.primary,
    padding: '8px 12px',
    fontSize: 13,
    outline: 'none',
    fontFamily: 'inherit',
    boxSizing: 'border-box',
    transition: `all ${COLORS.transition}`,
  },

  inputFocused: {
    borderColor: COLORS.accent.primary,
    backgroundColor: COLORS.bg.inputHover,
    boxShadow: COLORS.shadow.focus,
  },

  /* ===== BUTTONS ===== */
  buttonBase: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    border: 'none',
    transition: `all ${COLORS.transition}`,
    textAlign: 'center',
  },

  buttonPrimary: {
    background: COLORS.accent.primary,
    color: '#FFFFFF',
    fontWeight: 600,
    padding: '10px 18px',
    fontSize: 13,
  },

  buttonPrimaryHover: {
    background: COLORS.accent.hover,
    opacity: 1,
  },

  buttonSecondary: {
    background: COLORS.bg.button,
    color: COLORS.text.primary,
    border: `1px solid ${COLORS.border.main}`,
  },

  buttonSecondaryHover: {
    background: COLORS.bg.buttonHover,
    borderColor: COLORS.border.main,
  },

  buttonDanger: {
    background: COLORS.status.error,
    color: '#FFFFFF',
    fontWeight: 600,
  },

  buttonDangerHover: {
    opacity: 0.9,
  },

  buttonDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },

  /* ===== BADGES ===== */
  badgeSuccess: {
    background: COLORS.status.successBg,
    color: COLORS.status.success,
    border: `1px solid ${COLORS.status.success}`,
    padding: '3px 10px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase',
  },

  badgeWarning: {
    background: COLORS.status.warningBg,
    color: COLORS.status.warning,
    border: `1px solid ${COLORS.status.warning}`,
    padding: '3px 10px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase',
  },

  badgeError: {
    background: COLORS.status.errorBg,
    color: COLORS.status.error,
    border: `1px solid ${COLORS.status.error}`,
    padding: '3px 10px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase',
  },

  badgeInfo: {
    background: 'var(--color-accent-faint)',
    color: COLORS.accent.primary,
    border: `1px solid ${COLORS.accent.primary}`,
    padding: '3px 10px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase',
  },

  /* ===== LABELS ===== */
  label: {
    display: 'block',
    fontSize: 12,
    fontWeight: 600,
    color: COLORS.text.secondary,
    marginBottom: 6,
  },

  /* ===== CARDS ===== */
  card: {
    background: COLORS.bg.surface,
    border: `1px solid ${COLORS.border.main}`,
    borderRadius: 8,
    padding: 16,
    transition: `all ${COLORS.transition}`,
  },

  cardHover: {
    borderColor: COLORS.accent.primary,
    boxShadow: '0 4px 16px rgba(99, 102, 241, 0.12)',
  },

  /* ===== HELPER TEXT ===== */
  helperTextSuccess: {
    fontSize: 11,
    color: COLORS.status.success,
    marginTop: 4,
  },

  helperTextError: {
    fontSize: 11,
    color: COLORS.status.error,
    marginTop: 4,
  },

  helperTextWarning: {
    fontSize: 11,
    color: COLORS.status.warning,
    marginTop: 4,
  },

  helperTextMuted: {
    fontSize: 11,
    color: COLORS.text.quaternary,
    marginTop: 4,
  },

  /* ===== SEPARATOR ===== */
  divider: {
    height: 1,
    background: COLORS.border.main,
    margin: '12px 0',
  },
};

/**
 * Color Recommendations:
 * 
 * Text Hierarchy:
 * - Primary: Main content, important information
 * - Secondary: Labels, descriptions, secondary actions
 * - Tertiary: Less important information, metadata
 * - Quaternary: Placeholders, disabled text, hints
 * 
 * Status Indicators:
 * - Success (Green): Positive outcomes, completions
 * - Warning (Yellow): Cautions, requires attention
 * - Error (Red): Failures, destructive actions
 * - Info (Blue): Informational messages
 * 
 * Background Usage:
 * - bg-surface: Main content containers, cards
 * - bg-input: Form fields, search bars
 * - bg-button: Secondary buttons, less important actions
 * - bg-surface-raised: Highlighted/selected items
 * 
 * Always test contrast ratios (should be >= 4.5:1 for WCAG AA compliance)
 */
