# ZYRO ERP — Enterprise Design System Specification

This document defines the visual architecture, tokens, components, and patterns that govern the ZYRO ERP user interface.

## 1. Brand Identity & Color Palette

The ZYRO visual language expresses enterprise financial precision, reliability, and modern responsiveness.

| Token Name | Hex Value | Semantic Purpose |
|---|---|---|
| `--zyro-deep-navy` | `#142B3A` | Primary brand color, headers, navigation shell, dark text |
| `--zyro-dashboard-navy` | `#1B3547` | Accent surface for executive cards and secondary headers |
| `--zyro-electric-teal` | `#20B8A9` | Primary interactive color, action buttons, active states, branding accents |
| `--zyro-aqua` | `#A5F3E8` | Highlight gradients, watermarks, subtle callouts |
| `--zyro-clean-ivory` | `#F7FAF9` | Application background canvas |
| `--zyro-slate-blue` | `#60758A` | Muted text, borders, secondary labels |

### Status Colors (Pills & Badges)
- **Success / Posted / Paid:** Background `#DDEFE8`, Text `#1d6e66`
- **Warning / Draft / Pending:** Background `#fdf1df`, Text `#b5750a`
- **Danger / Suspended / Overdue:** Background `#fbe8e6`, Text `#c4432f`
- **Info / Processing:** Background `#e2eaf2`, Text `#2f4c6e`

---

## 2. Typography & Numbers
- **Font Family:** `'Inter', -apple-system, BlinkMacSystemFont, sans-serif`
- **Weights:** Regular (400), Medium (500), Semi-bold (600), Bold (700)
- **Financial Figures:** Tabular numerals enabled for monetary amounts, ensuring clean vertical alignment in all ledgers, invoices, and financial reports.

---

## 3. Layout Architecture
- **Global Shell:** Flexbox-based layout featuring a sticky top navigation bar with dropdown menus for Sales, Purchases, Accounting, and Setup & Admin, along with an active organization selector and user badge.
- **Background Watermark:** Subtle, animated ZYRO infinity watermark (`#20B8A9` to `#A5F3E8` gradient) positioned behind the workspace canvas.
- **Card Containers (`.lp-card`):** Clean white background, 1px border (`--lp-border`), subtle box shadow (`--lp-shadow-panel`), and generous inner padding.

---

## 4. Reusable Component Patterns
- **Buttons (`.lp-btn`):** Primary buttons in Electric Teal (`#20B8A9`) with dark navy text; secondary buttons with neutral borders; sm, md, lg sizing.
- **Tables (`.lp-table`):** Clean headers, alternating row hover effects, right-aligned monetary and numeric columns, status pill badges.
- **Form Controls:** Clean input borders, focus rings with teal accent shadows, structured field grouping for financial transactions (header, line items, tax/WHT calculations, totals).
- **Executive KPI Cards:** Top-level metric displays with large tabular numbers and trend indicators.

---

## 5. Non-Negotiable Technical Constraints
- No changes to backend route handlers, blueprints, or endpoint names (`url_for()` compatibility preserved 100%).
- No modifications to database schemas, primary keys, or foreign key structures.
- Strict preservation of accounting calculation services (`accounting/services.py::create_journal_entry()`), WHT manual selection rules, and organization isolation (`organization_id`).
