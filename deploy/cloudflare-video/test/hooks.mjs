// Lets Node's test runner import the Worker: `cloudflare:email` (a module only the Workers runtime has) becomes a stub, and a
// relative import without an extension from a .ts file is that .ts file. Node strips the types (--experimental-strip-types).
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const STUB = new URL("./stub-email.mjs", import.meta.url).href;

export async function resolve(specifier, context, next) {
  if (specifier === "cloudflare:email") return { url: STUB, shortCircuit: true };
  if (specifier.startsWith(".") && !/\.[cm]?[jt]sx?$/.test(specifier) && context.parentURL?.endsWith(".ts")) {
    const url = new URL(specifier + ".ts", context.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, context);
  }
  return next(specifier, context);
}
