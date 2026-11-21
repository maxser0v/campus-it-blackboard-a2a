---
name: Campus IT Incident Investigator
description: Navy investigation workspace with readable evidence and native controls.
colors:
  primary: "#a8caff"
  primary-hover: "#c2dbff"
  primary-ink: "#0c1830"
  canvas: "#0b1020"
  surface: "#111e33"
  field: "#142039"
  text: "#e6edf8"
  copy: "#bdcee5"
  muted: "#95a9c7"
  border: "#30425e"
  focus: "#b6d6ff"
  tab-track: "#101a2c"
  tab-active: "#253956"
  tab-text: "#d4e5ff"
  secondary: "#18263e"
  secondary-text: "#dce8fa"
  pill: "#1b2b44"
  pill-text: "#bcd3f4"
  cyan: "#7eddec"
  cyan-ground: "#13313b"
  violet: "#c7b1ff"
  violet-ground: "#2b2341"
  amber: "#ffd493"
  amber-ground: "#342d20"
  mint: "#83e5c3"
  mint-ground: "#15312e"
  rose: "#ffa9ba"
  rose-ground: "#342132"
  evidence-agent: "#63d9ee"
  diagnosis-agent: "#b29aff"
  review-agent: "#ffce83"
  response-agent: "#77e3ba"
typography:
  display:
    fontFamily: "sans-serif"
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.15
    letterSpacing: "-0.6px"
  headline:
    fontFamily: "sans-serif"
    fontSize: "22px"
    fontWeight: 600
    letterSpacing: "-0.5px"
  title:
    fontFamily: "sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.6
  body:
    fontFamily: "sans-serif"
    fontSize: "14px"
    lineHeight: 1.85
  label:
    fontFamily: "sans-serif"
    fontSize: "12px"
  tab-label:
    fontFamily: "sans-serif"
    fontSize: "13px"
    fontWeight: 600
rounded:
  inset: "7px"
  compact: "8px"
  secondary: "9px"
  control: "10px"
  disclosure: "11px"
  panel: "12px"
  empty: "14px"
  setup: "15px"
  pill: "20px"
spacing:
  compact: "6px"
  small: "12px"
  medium: "14px"
  wide: "18px"
  section: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.control}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-text}"
    rounded: "{rounded.secondary}"
  record-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.copy}"
    rounded: "{rounded.panel}"
    padding: "19px 21px"
  pill:
    backgroundColor: "{colors.pill}"
    textColor: "{colors.pill-text}"
    rounded: "{rounded.pill}"
    padding: "4px 9px"
  tab-active:
    backgroundColor: "{colors.tab-active}"
    textColor: "{colors.tab-text}"
    rounded: "{rounded.compact}"
    padding: "10px 16px"
---
# Design System: Campus IT Incident Investigator

## Overview

**Creative North Star: "The Shared Investigation Board"**

A dark, evidence-led workspace for following synthetic campus IT investigations. Navy surfaces, pale blue actions, and restrained specialist colors extend the existing Agent theatre identity. Findings remain readable before source identifiers and raw records are disclosed.

The application uses native Streamlit controls for starting runs, choosing cases, navigating tabs, and disclosing details. Agent theatre remains its own embedded interactive surface; its smaller type and motion vocabulary are inherited rather than a template for the reading views.

**Key Characteristics:**
- Navy grounds with pale blue controls.
- Readable findings, revision-aware reviews, and progressively disclosed source metadata.
- Full-border evidence panels and a compact run summary.
- Native controls alongside the existing Agent theatre.

## Colors

The palette uses a deep navy ground and pale blue actions, with restrained specialist and outcome colors. The frontmatter records reusable implemented values; the stylesheet remains the source for local text and border variations.

### Primary
- **Pale blue:** Primary action, with a lighter hover and dark ink for readable button text.
- **Focus blue:** A visible keyboard outline for buttons, tabs, and disclosure summaries.

### Secondary
- **Cyan / violet / amber / mint:** Evidence, Diagnosis, Review, and Response respectively. The `*-agent` colors belong to the inherited theatre; the gentler named colors belong to readable pills.
- **Mint / amber / rose outcomes:** Completed or accepted; unresolved or awaiting evidence; blocked or rejected. Each pill also spells out its meaning. Cyan also labels Offline demo, and mint labels Live model.

### Neutral
- **Canvas:** Overall application background.
- **Surface / field / tab track:** Quiet layers for records, controls, and navigation.
- **Text / copy / muted:** Base text, reading copy, and secondary metadata.
- **Border:** Full panel boundaries; local variants provide control and table separation.

**The Shared Role Rule.** Use cyan for Evidence, violet for Diagnosis, amber for Review, and mint for Response; retain written role and status labels alongside color.

## Typography

**Display and Body Font:** Streamlit's configured sans serif; no custom typeface is loaded. Agent theatre explicitly uses `system-ui, sans-serif` inside its iframe.

### Hierarchy
- **Display:** Product title; reduced to (24px) at the narrow breakpoint.
- **Headline:** Investigation heading. Section headings use (20px), weight (600), with (-0.4px) tracking.
- **Title:** Finding titles; timeline titles use (15px), and response action titles use (16px).
- **Body:** Reading copy uses the recorded body role, with a maximum measure of (76ch). Hero copy uses a shorter line height (1.7) and maximum width (690px).
- **Label:** Metadata and pills. Pills add weight (600), line height (1.6), and tracking (0.2px). Current React Aria tab labels use the tab-label role; older Base Web tabs retain (12px).

**The Reading First Rule.** Present findings and response checks before raw source metadata; keep longer reading copy within the implemented measure.

## Layout

The wide Streamlit canvas has a maximum width of (1480px), top padding (2.8rem), and bottom padding (3rem). Case selection and the start action use native columns in a (3:1) ratio. Response evidence and guidance use (1.4:1). Native column stacking and popup placement remain Streamlit-owned.

Custom content uses compact gaps with larger section breaks. The run summary wraps its facts. At (800px) and below, main padding becomes (2rem 1.2rem), the mode bar stacks, the four-column introduction becomes two columns, timeline timestamps move above records, record padding becomes (15px), and the run summary gap becomes (14px). At (600px), the evidence-table scroll hint appears. Tables retain a minimum width of (560px) inside a local overflow region so values and units remain intact.

The inherited theatre stacks its stage and inspector at (980px); at (600px) it reduces internal spacing and stage typography. Its iframe height is (850px), with scrolling enabled in the legacy embedding fallback.

## Elevation & Depth

The reading workspace uses tonal layering and one-pixel borders. Primary buttons explicitly remove shadows. The inherited theatre uses a soft specialist-color activity glow and a subtle hub glow; these are activity cues specific to that surface.

### Shadow Vocabulary
- **Theatre active specialist:** `0 0 25px color-mix(in srgb,var(--accent) 20%,transparent)`.
- **Theatre hub:** `0 0 45px #5599ff15`.

**The Quiet Surface Rule.** Use tonal surfaces and full borders for the reading workspace; reserve the inherited activity glow for Agent theatre.

## Shapes

Gently rounded rectangles organize records, controls, navigation, and disclosure panels using the frontmatter radius scale. Pills use fully rounded short capsules. Empty states use a dashed border; record cards use a complete solid border. The theatre retains its own larger shell radius (18px) and selected-robot outline.

## Components

### Buttons
Pale blue primary actions use dark text, weight (650), a one-pixel light border, and minimum height (42px). Hover lightens the fill and border. Secondary controls use a darker filled surface. Native Streamlit owns button padding and disabled behavior; do not invent alternate interaction semantics.

### Inputs / Fields
Case selection and activity filters are native labelled selectboxes. The outer Base Web select surface uses the field color and control radius. The nested combobox is transparent in the final override; do not add a second filled rectangle. Keyboard and expanded-list behavior remain native.

### Navigation
Native tabs place compact labels inside a bordered, padded track. The active tab uses a filled blue surface and pale text. The default underline is hidden for both older Base Web and current React Aria implementations. Labels stay on one line and the native tablist handles horizontal overflow. Preserve `role`, selection state, and arrow-key behavior.

### Chips
Static role and status pills pair written labels with colored text, tinted fill, and a one-pixel border. These are labels, not filter buttons. Completion/acceptance is mint; unavailable/open/proposed is amber; failure/rejection is rose; other outcomes are muted.

### Cards / Containers
Record panels contain a role/status header, readable finding, optional source table, and metadata footer. Longer copy wraps anywhere when needed. Source details and raw records live in native expanders. Empty panels explain the missing state and next expected step rather than imitating completed findings.

### Evidence Tables
Semantic column headers use `scope="col"`. The overflow wrapper is a focusable, named region. Numeric values use tabular numerals and do not wrap; column widths and the local scroll region protect units and measurements. The table is descriptive evidence, not an editable grid.

### Review and Response
Review notes use a subdued violet ground and text. Response actions use a numbered mint block with a separately tinted expected check. Review matching uses the exact target revision; the interface retains that relationship and the recommendation-only explanation.

### Agent Theatre
The existing embedded surface provides robot selection, replay controls, a task inspector, and a work log. Keep its role colors consistent with the reading views. Its animated routes, robot bob/blink, and arrival cues are disabled for reduced motion; packet visuals are hidden. The surrounding dashboard adds no animation and disables smooth scrolling under reduced motion.

### Accessibility
Keep written status and mode labels, native control semantics, and visible focus outlines (2px with 3px offset) for buttons, tabs, and summaries. The stylesheet does not replace native selectbox focus behavior. This records implemented behavior, not a claim of a completed accessibility audit. The table wrapper retains native browser focus treatment.

## Do's and Don'ts

### Do:
- **Do** preserve native Streamlit control semantics, labels, keyboard operation, and visible focus.
- **Do** keep numeric values and units intact and allow evidence tables to scroll locally.
- **Do** retain explicit Offline/Live model mode and recommendation-only language.
- **Do** keep review verdicts attached to the exact finding or response revision.

### Don't:
- **Don't** use role or status color as the only explanation.
- **Don't** replace readable evidence with raw JSON as the first presentation.
- **Don't** propagate Agent theatre’s compact inspector type into the reading workspace.

Documented from `src/campus_blackboard/dashboard.css`, `dashboard_ui.py`, `dashboard.py`, `.streamlit/config.toml`, and the inherited `agent_theatre.html`. Product context comes from README.md and is now recorded in PRODUCT.md. The descriptive North Star names the implemented system, rather than claiming a separately approved brand identity. The inherited theatre's eyebrow and compact micro-labels are not canonized for new surfaces.
