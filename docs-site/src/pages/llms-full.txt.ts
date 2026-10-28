import type { APIRoute } from 'astro';
import { llmsFull } from '../lib/llms';

export const GET: APIRoute = async ({ site }) =>
  new Response(await llmsFull(site), { headers: { 'content-type': 'text/plain; charset=utf-8' } });
