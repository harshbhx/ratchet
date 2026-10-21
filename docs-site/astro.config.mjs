// @ts-check
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import mermaid from 'astro-mermaid';
import lucode from 'lucode-starlight';
import starlightLinksValidator from 'starlight-links-validator';
import starlightOpenAPI, { createOpenAPISidebarGroup } from 'starlight-openapi';
import { REPO_URL, SITE_DIR, SITE_URL } from './scripts/doc-map.mjs';

const DOCS = join(SITE_DIR, 'src', 'content', 'docs');

/** A page listed in the sidebar only once its file exists, so a half-written site still builds. */
const page = (slug, label) =>
  existsSync(join(DOCS, `${slug}.mdx`)) || existsSync(join(DOCS, `${slug}.md`)) ? [{ label, slug }] : [];
const guide = (slug, label) => page(`guides/${slug}`, label);

// The generated HTTP reference (starlight-openapi, from openapi/ratchet.json) goes inside "API & reference".
const httpOperations = createOpenAPISidebarGroup();

export default defineConfig({
  site: SITE_URL,
  trailingSlash: 'always',
  integrations: [
    // Before Starlight, so ```mermaid blocks become diagrams instead of code.
    mermaid({
      autoTheme: true,
      enableLog: false,
      mermaidConfig: {
        fontFamily: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
        // Natural size, scrolling sideways when wider than the column, instead of shrinking the text.
        flowchart: { curve: 'basis', htmlLabels: true, padding: 12, useMaxWidth: false },
        sequence: { mirrorActors: false, showSequenceNumbers: false, useMaxWidth: false },
        state: { useMaxWidth: false },
        er: { useMaxWidth: false },
      },
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
      customCss: [
        '@fontsource-variable/inter',
        '@fontsource-variable/jetbrains-mono',
        './src/styles/theme.css',
        './src/styles/diagrams.css',
      ],
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#fbfaf7', media: '(prefers-color-scheme: light)' } },
        { tag: 'meta', attrs: { name: 'theme-color', content: '#0b0b0c', media: '(prefers-color-scheme: dark)' } },
      ],
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      expressiveCode: {
        styleOverrides: {
          codeFontFamily: '"JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace',
          uiFontFamily: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
        },
      },
      plugins: [
        lucode({
          navLinks: [
            { label: 'Docs', link: '/guides/what-is-ratchet/' },
            { label: 'Guides', link: '/guides/big-picture/' },
            { label: 'API', link: '/api/http/' },
            { label: 'GitHub', link: REPO_URL, attrs: { target: '_blank', rel: 'noopener' } },
            { label: 'Live site', link: 'https://ratchet.dualmindlab.tech', attrs: { target: '_blank', rel: 'noopener' } },
          ],
          footerText:
            'Ratchet is open source under Apache-2.0 ([GitHub](https://github.com/harshbhx/ratchet)). These pages are built from the repository’s `docs/` folder. Simulation numbers come from mock models and are labelled. Theme: [Lucode Starlight](https://github.com/lucas-labs/lucode-starlight-theme).',
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
  ],
});
