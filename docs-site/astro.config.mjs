// @ts-check
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import react from '@astrojs/react';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import mermaid from 'astro-mermaid';
import lucode from 'lucode-starlight';
import starlightLinksValidator from 'starlight-links-validator';
import starlightOpenAPI, { createOpenAPISidebarGroup } from 'starlight-openapi';
import { REPO_URL, SITE_DIR, SITE_URL } from './scripts/doc-map.mjs';
import { mermaidConfig } from './src/config/mermaid.mjs';

const DOCS = join(SITE_DIR, 'src', 'content', 'docs');

/** A page listed in the sidebar only once its file exists, so a half-written site still builds. */
const page = (slug, label) =>
  existsSync(join(DOCS, `${slug}.mdx`)) || existsSync(join(DOCS, `${slug}.md`)) ? [{ label, slug }] : [];
const guide = (slug, label) => page(`guides/${slug}`, label);

// The generated HTTP reference (starlight-openapi, from openapi/ratchet.json) goes inside "API & reference".
const httpOperations = createOpenAPISidebarGroup();

/** Without JavaScript a Stepper shows every step as a list (also for browsers without the `scripting` query). */
const NOSCRIPT_CSS = '<style>.rb-stepper__ui{display:none!important}.rb-stepper__all{display:block!important}</style>';

export default defineConfig({
  site: SITE_URL,
  trailingSlash: 'always',
  integrations: [
    // Before Starlight, so ```mermaid blocks become diagrams instead of code.
    // autoTheme off: the base theme stays, and src/config/mermaid.mjs colours diagrams with the site's CSS variables.
    mermaid({
      autoTheme: false,
      enableLog: false,
      mermaidConfig,
    }),
    starlight({
      title: 'Ratchet docs',
      description:
        'Ratchet is an OpenAI-compatible gateway that distills open teacher models into a small student, with a gated nightly release. Rent intelligence today, own it tomorrow.',
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
      },
      favicon: '/favicon.svg',
      editLink: { baseUrl: `${REPO_URL}/edit/main/docs-site/` },
      lastUpdated: false,
      credits: false,
      // Ratchet's own page chrome (src/components/overrides); Lucode keeps every other override.
      components: {
        Hero: './src/components/overrides/Hero.astro',
        PageTitle: './src/components/overrides/PageTitle.astro',
        Footer: './src/components/overrides/Footer.astro',
      },
      customCss: [
        '@fontsource-variable/inter',
        '@fontsource-variable/jetbrains-mono',
        './src/styles/theme.css',
        './src/styles/chrome.css',
        './src/styles/content.css',
        './src/styles/print.css',
        './src/styles/diagrams.css',
      ],
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#fbfaf7', media: '(prefers-color-scheme: light)' } },
        { tag: 'meta', attrs: { name: 'theme-color', content: '#0b0b0c', media: '(prefers-color-scheme: dark)' } },
        { tag: 'meta', attrs: { name: 'color-scheme', content: 'dark light' } },
        { tag: 'noscript', content: NOSCRIPT_CSS },
      ],
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      expressiveCode: {
        styleOverrides: {
          codeFontFamily: '"JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace',
          uiFontFamily: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
          codeFontSize: '0.85rem',
          codeLineHeight: '1.7',
          codePaddingBlock: '1rem',
          codePaddingInline: '1.15rem',
          borderRadius: '0.85rem',
          borderWidth: '1px',
          borderColor: 'var(--rt-line)',
          codeBackground: 'var(--rt-code-bg)',
          scrollbarThumbColor: 'var(--rt-line-strong)',
          scrollbarThumbHoverColor: 'var(--rt-muted)',
          frames: {
            editorBackground: 'var(--rt-code-bg)',
            editorTabBarBackground: 'var(--rt-surface-2)',
            editorTabBarBorderColor: 'var(--rt-line)',
            editorTabBarBorderBottomColor: 'var(--rt-line)',
            editorActiveTabBackground: 'var(--rt-code-bg)',
            editorActiveTabForeground: 'var(--rt-ink)',
            editorActiveTabIndicatorTopColor: 'var(--rt-owned)',
            editorActiveTabIndicatorBottomColor: 'transparent',
            editorActiveTabBorderColor: 'var(--rt-line)',
            terminalBackground: 'var(--rt-code-bg)',
            terminalTitlebarBackground: 'var(--rt-surface-2)',
            terminalTitlebarBorderBottomColor: 'var(--rt-line)',
            terminalTitlebarForeground: 'var(--rt-muted)',
            terminalTitlebarDotsForeground: 'var(--rt-line-strong)',
            terminalTitlebarDotsOpacity: '1',
            inlineButtonBackground: 'var(--rt-surface)',
            inlineButtonBorder: 'var(--rt-line-strong)',
            inlineButtonForeground: 'var(--rt-ink-2)',
            tooltipSuccessBackground: 'var(--rt-good)',
            tooltipSuccessForeground: 'var(--rt-bg)',
            frameBoxShadowCssValue: 'none',
            shadowColor: 'transparent',
          },
        },
      },
      plugins: [
        lucode({
          // Hero, PageTitle and Footer are Ratchet's own (see `components` above); that is on purpose.
          warnOverrides: false,
          // No `attrs` here: Lucode's NavBar spreads a shared attrs object and grows a duplicate class attribute
          // on every page it renders. Links to other sites carry an arrow (src/styles/chrome.css) and open in place.
          navLinks: [
            { label: 'Docs', link: '/guides/what-is-ratchet/' },
            { label: 'Guides', link: '/guides/big-picture/' },
            { label: 'API', link: '/api/http/' },
            { label: 'GitHub', link: REPO_URL },
            { label: 'Live site', link: 'https://ratchet.dualmindlab.tech' },
          ],
          footerText:
            'Ratchet is open source under Apache-2.0 ([GitHub](https://github.com/harshbhx/ratchet)). These pages are built from the repository’s `docs/` folder. Simulation numbers come from mock models and are labelled. Built with [Starlight](https://starlight.astro.build), the [Lucode](https://github.com/lucas-labs/lucode-starlight-theme) theme and [React Bits](https://reactbits.dev) components (MIT + Commons Clause, kept in `docs-site/src/components/rb/`).',
        }),
        starlightOpenAPI([
          {
            base: 'api/http',
            schema: './openapi/ratchet.json',
            sidebar: {
              label: 'HTTP API (generated)',
              collapsed: true,
              group: httpOperations,
              operations: { badges: true, labels: 'path', sort: 'document' },
            },
          },
        ]),
        starlightLinksValidator({
          errorOnRelativeLinks: false,
          errorOnLocalLinks: true,
          // The generated OpenAPI pages are routes, not content entries, so the validator cannot see them.
          exclude: ['/api/http/', '/api/http/**'],
        }),
      ],
      sidebar: [
        {
          label: 'Start here',
          items: [
            ...guide('what-is-ratchet', 'What is Ratchet'),
            ...guide('business', 'The business case'),
            ...guide('quickstart', 'Quickstart'),
            ...guide('for-judges', 'For judges'),
          ],
        },
        {
          label: 'How it works',
          items: [
            ...guide('big-picture', 'The big picture'),
            ...guide('request-journey', "One request's journey"),
            ...guide('confidence', 'Confidence'),
            ...guide('escalation-and-audits', 'Escalation and audits'),
            ...guide('ledger', 'The ledger'),
            ...guide('night-school', 'Night School'),
            ...guide('release-gate', 'The release gate'),
            ...guide('training-on-your-data', 'Training on your data'),
            ...guide('licenses', 'Licenses'),
            ...guide('cost-and-savings', 'Cost and savings'),
            ...guide('evidence', 'Evidence you can check'),
          ],
        },
        {
          label: 'Scout',
          items: [...guide('scout', 'Scout guide'), ...page('reference/scout', 'Scout reference')],
        },
        {
          label: 'Run it',
          items: [
            ...guide('host-on-nebius', 'Host on Nebius'),
            ...page('reference/deployment', 'Deployment'),
            ...page('reference/deploy-nebius-vm', 'Demo VM on Nebius'),
            ...page('reference/runbook', 'Runbook'),
          ],
        },
        {
          label: 'Security',
          items: [...guide('security', 'Security and safety'), ...page('reference/threat-model', 'Threat model')],
        },
        {
          label: 'API & reference',
          items: [
            httpOperations,
            ...page('reference/api', 'HTTP API guide'),
            ...page('api/environment', 'Environment variables'),
            ...page('api/cli', 'Command line'),
            ...page('api/python', 'Python API'),
            ...page('reference/architecture', 'Architecture'),
            ...page('reference/testing', 'Testing'),
          ],
        },
        {
          label: 'Help',
          items: [...guide('glossary', 'Glossary'), ...guide('faq', 'FAQ')],
        },
      ].filter((group) => group.items.length > 0),
    }),
    // React islands for the React Bits components (src/components/rb, wrapped in src/components/ui).
    react(),
  ],
});
