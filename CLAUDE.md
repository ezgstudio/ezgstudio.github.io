# CLAUDE.md — EZG Studio hub

Public static site for EZG Studio LLC, served by **GitHub Pages at the repo root**
(`https://ezgstudio.github.io/`). No build step — plain HTML/CSS.

## Structure
- `index.html` — the studio hub homepage (self-contained; inline CSS).
- `folio/` — Folio's landing page + legal pages (`folio/privacy.html`, `folio/terms.html`).
- `assets/` — hub images, favicons, logo SVGs.
- Future app pages go in their own folder: `situ/`, `fora/`, etc.

## Deploy
Push to `main` → GitHub Pages rebuilds automatically (live in ~1 min). That's it.

## Conventions
- Pages are **self-contained HTML with inline `<style>`** (no framework, no bundler).
- Brand: graphite `#1A1B18`, paper/cream, gold `#B58A2B` / `#D6A63A`;
  Schibsted Grotesk + JetBrains Mono for the hub. Product pages (e.g. Folio) carry
  their own product palette. Canonical brand tokens live in the private
  `ezg-assets` repo (`brand/tokens.css`).
- Keep layouts responsive (mobile-first gutters, no horizontal scroll).

## Guardrails
- **Do not change the URLs of `folio/privacy.html` or `folio/terms.html`.** They are
  referenced by App Store Connect and inside the Folio app. The old `folio-legal`
  repo now only redirects here.
- New app landing pages should be built from the **app-landing template** in the
  `ezg-assets` repo, then committed here as a new folder.
- Contact for the studio is ezgforge@gmail.com (already public on the site).
