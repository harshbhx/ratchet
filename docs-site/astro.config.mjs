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

/**
 * Two accessibility fixes to Lucode's markup, run once the page is parsed: its mobile-menu group titles
 * are <h4> elements that come before the page's <h1> and after its <h2>s, so they are given level 2 to keep the
 * heading outline in order; and the mobile menu's two close controls get a name.
 */
const A11Y_FIXES = `document.addEventListener('DOMContentLoaded',function(){document.querySelectorAll('h4.entry-title').forEach(function(h){h.setAttribute('aria-level','2')});document.querySelectorAll('#drawer .sheet-overlay,#drawer .drag-icon').forEach(function(b){b.setAttribute('aria-label','Close the menu')})});`;

/**
 * The theme button's reveal (after beUI's Theme Toggle, MIT; styles in src/styles/motion.css): a click is caught on
 * the way down, and Lucode's own handler runs inside a same-document view transition, so the new theme opens in a
 * circle from the button. Skipped without the API or under reduced motion; the theme switch itself is Lucode's.
 */
const THEME_REVEAL = `document.addEventListener('click',function(e){var b=e.target instanceof Element&&e.target.closest('starlight-theme-lucode-select button');if(!b||b.dataset.rtPass||!document.startViewTransition||matchMedia('(prefers-reduced-motion: reduce)').matches)return;e.stopPropagation();e.preventDefault();var r=b.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,h=document.documentElement;h.style.setProperty('--rt-vt-x',x+'px');h.style.setProperty('--rt-vt-y',y+'px');h.style.setProperty('--rt-vt-r',Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y))+'px');h.dataset.rtVt='theme';var t=document.startViewTransition(function(){b.dataset.rtPass='1';b.click();delete b.dataset.rtPass});t.ready.catch(function(){});t.finished.finally(function(){delete h.dataset.rtVt})},true);`;

/** Chromium prerenders a same-site page once the pointer rests on its link, so the next page is ready on click. */
const SPECULATION = JSON.stringify({ prerender: [{ where: { href_matches: '/*' }, eagerness: 'moderate' }] });

export default defineConfig({
  site: SITE_URL,
  trailingSlash: 'always',
  // Other browsers fetch a page when its link is hovered (Chromium prerenders it, see SPECULATION).
  prefetch: { prefetchAll: true, defaultStrategy: 'hover' },
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
        'Ratchet is an OpenAI-compatible gateway that distills open teacher models into a small student. You end up with an adapter you own, learned only from open teachers, with a gated nightly release.',
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
        Sidebar: './src/components/overrides/Sidebar.astro',
      },
      customCss: [
        '@fontsource-variable/inter',
        '@fontsource-variable/jetbrains-mono',
        './src/styles/theme.css',
        './src/styles/chrome.css',
        './src/styles/content.css',
        './src/styles/print.css',
        './src/styles/diagrams.css',
        './src/styles/motion.css',
      ],
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#fbfaf7', media: '(prefers-color-scheme: light)' } },
        { tag: 'meta', attrs: { name: 'theme-color', content: '#0b0b0c', media: '(prefers-color-scheme: dark)' } },
        { tag: 'meta', attrs: { name: 'color-scheme', content: 'dark light' } },
        { tag: 'meta', attrs: { property: 'og:image', content: 'https://docs.ratchet.dualmindlab.tech/social-preview.png' } },
        { tag: 'meta', attrs: { property: 'og:image:width', content: '1280' } },
        { tag: 'meta', attrs: { property: 'og:image:height', content: '640' } },
        { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
        { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://docs.ratchet.dualmindlab.tech/social-preview.png' } },
        { tag: 'noscript', content: NOSCRIPT_CSS },
        { tag: 'script', content: A11Y_FIXES },
        { tag: 'script', content: THEME_REVEAL },
        { tag: 'script', attrs: { type: 'speculationrules' }, content: SPECULATION },
      ],
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      expressiveCode: {
        // Shiki has no grammar for crontab lines (the runbook uses ```cron); show them as shell.
        shiki: { langAlias: { cron: 'bash' } },
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
            { label: 'Live demo', link: 'https://live.ratchet.dualmindlab.tech' },
            { label: 'Story site', link: 'https://ratchet.dualmindlab.tech' },
          ],
          footerText:
            'Ratchet is open source under Apache-2.0 ([GitHub](https://github.com/harshbhx/ratchet)). The reference pages are built from the repository’s `docs/` folder, and the guides are written for this site. Simulation numbers come from mock models and are labelled. Built with [Starlight](https://starlight.astro.build), the [Lucode](https://github.com/lucas-labs/lucode-starlight-theme) theme and [React Bits](https://reactbits.dev) components (MIT + Commons Clause, kept in `docs-site/src/components/rb/`).',
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
            ...guide('why-it-matters', 'Why it matters'),
            ...guide('business', 'The business case'),
            ...guide('text-to-sql', 'Text-to-SQL adapter'),
            ...guide('results', 'Results'),
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
            ...guide('proof', 'Proof you can run'),
            ...page('reference/verify', 'Verify: the commands'),
          ],
        },
        {
          label: 'Scout',
          items: [...guide('scout', 'Scout guide'), ...page('reference/scout', 'Scout reference')],
        },
        {
          label: 'Run it',
          items: [
            ...page('reference/where-it-runs', 'Where everything runs'),
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
            ...page('api/endpoints', 'Endpoint map'),
            ...page('api/environment', 'Environment variables'),
            ...page('reference/configuration', 'Configuration guide'),
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
