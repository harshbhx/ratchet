// Lets Node's test runner import the app's TypeScript modules as Vite does: `@/x` is `src/x.ts`, and a relative
// import without an extension from a .ts file is that .ts file. Node strips the types (--experimental-strip-types).
import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));

export async function resolve(specifier, context, next) {
  if (specifier.startsWith("@/")) return next(pathToFileURL(SRC + specifier.slice(2) + ".ts").href, context);
  if (specifier.startsWith(".") && !/\.[cm]?[jt]sx?$/.test(specifier) && context.parentURL?.endsWith(".ts")) {
    const url = new URL(specifier + ".ts", context.parentURL);
    if (existsSync(fileURLToPath(url))) return next(url.href, context);
  }
  return next(specifier, context);
}
