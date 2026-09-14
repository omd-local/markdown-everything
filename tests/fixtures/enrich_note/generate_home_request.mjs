// Run with a Node version supporting TypeScript stripping and an OMD Home checkout.
// The input is synthetic; this does not inspect a vault or call a model.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

if (!process.argv[2]) throw new Error("Usage: generate_home_request.mjs OMD_HOME_ROOT");
const { buildEnrichmentRequest } = await import(pathToFileURL(
  resolve(process.argv[2], "src/enrichment/request-builder.ts"),
).href);
const input = JSON.parse(readFileSync(new URL("home-multiline-input.json", import.meta.url), "utf8"));
const { request } = buildEnrichmentRequest(input);
process.stdout.write(`${JSON.stringify(request, null, 2)}\n`);
