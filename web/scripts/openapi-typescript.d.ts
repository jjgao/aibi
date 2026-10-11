/**
 * What `scripts/generate-types.mjs` uses of openapi-typescript 7.13.0, declared here for the type
 * checker (`tsconfig.node.json` maps the package's name to this file): the package's own
 * declarations reach `@redocly/openapi-core`'s, which name two packages that are its
 * development dependencies (`json-schema-to-ts`, `@types/js-yaml`) and so are never installed,
 * and the lint runs without `skipLibCheck`. At run time the real package is loaded; the gates'
 * tests run the generator, so a declaration here that the package does not honour fails them.
 */
declare module "openapi-typescript" {
  import type ts from "typescript";

  export interface TransformNodeOptions {
    readonly path?: string;
    readonly schema?: unknown;
  }

  export interface OpenAPITSOptions {
    transform?: (schemaObject: unknown, options: TransformNodeOptions) => ts.TypeNode | undefined;
    defaultNonNullable?: boolean;
    immutable?: boolean;
    silent?: boolean;
  }

  export default function openapiTS(schema: Readonly<Record<string, unknown>>, options?: OpenAPITSOptions): Promise<ts.Node[]>;

  export function astToString(ast: ts.Node | ts.Node[]): string;
}
