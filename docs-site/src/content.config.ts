import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { ExtendDocsSchema } from 'lucode-starlight/schema';

export const collections = {
  // ExtendDocsSchema adds the Lucode theme's splash-page fields (hero layout, announcement, button variants).
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema({ extend: ExtendDocsSchema }) }),
};
