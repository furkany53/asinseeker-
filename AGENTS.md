# AGENTS.md

## Project overview

This workspace contains a lightweight static landing page for a digital agency / web studio.

- `index.html` contains the page structure and content.
- `styles.css` contains all layout, color, spacing, and responsive styling.
- `script.js` handles a small runtime task such as setting the current year.
- The project does not use a framework or build step; it is plain HTML/CSS/JS.

## Working conventions

- Prefer small, targeted edits in the existing files rather than adding new frameworks or tooling.
- Maintain a clean, semantic HTML structure with headings, sections, and navigation.
- Keep the design responsive and mobile-friendly.
- Preserve the existing brand tone: modern, minimal, and conversion-focused.
- Keep content in Turkish unless the user explicitly requests another language.

## Local preview

From the project root, preview the site with:

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Validation guidance

Before considering a UI change complete, verify:

- the page loads without console errors
- layout remains readable on narrow screens
- links and buttons still work as expected
- the styling remains consistent with the current visual system

## Editing guidance

- Update existing sections in `index.html` rather than rewriting the entire page for small changes.
- Add CSS in `styles.css` using the current design tokens and spacing patterns.
- Keep JavaScript minimal and focused on behavior; avoid unnecessary dependencies.
- Favor clarity and maintainability over clever abstractions.
