import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import { BackendHealthSchema } from "../../../apps/web/lib/api/health";
import type { components } from "../src/generated/openapi";

type GeneratedHealthResponse = components["schemas"]["HealthResponse"];
type FrontendHealthResponse = z.infer<typeof BackendHealthSchema>;

const generatedMatchesFrontend: FrontendHealthResponse = {} as GeneratedHealthResponse;
const frontendMatchesGenerated: GeneratedHealthResponse = {} as FrontendHealthResponse;
void generatedMatchesFrontend;
void frontendMatchesGenerated;

type OpenApiSchema = {
  $ref?: string;
  const?: unknown;
  enum?: unknown[];
  type?: string;
  properties?: Record<string, OpenApiSchema>;
  required?: string[];
};

type OpenApiDocument = {
  paths: {
    "/health": {
      get: {
        responses: {
          "200": {
            content: {
              "application/json": {
                schema: OpenApiSchema;
              };
            };
          };
        };
      };
    };
  };
  components: {
    schemas: {
      HealthResponse: OpenApiSchema;
    };
  };
};

const requiredHealthFields = ["status", "service", "stage", "environment", "database"] as const;

function readOpenApi(): OpenApiDocument {
  return JSON.parse(readFileSync(new URL("../openapi.json", import.meta.url), "utf8")) as OpenApiDocument;
}

function literalValues(schema: OpenApiSchema): unknown[] {
  if ("const" in schema) {
    return [schema.const];
  }
  return schema.enum ?? [];
}

describe("health contract", () => {
  it("keeps the backend OpenAPI health response aligned with frontend expectations", () => {
    const openapi = readOpenApi();
    const responseSchema = openapi.paths["/health"].get.responses["200"].content["application/json"].schema;

    expect(responseSchema).toEqual({ $ref: "#/components/schemas/HealthResponse" });

    const healthSchema = openapi.components.schemas.HealthResponse;
    expect(new Set(healthSchema.required)).toEqual(new Set(requiredHealthFields));
    expect(Object.keys(healthSchema.properties ?? {}).sort()).toEqual([...requiredHealthFields].sort());

    expect(literalValues(healthSchema.properties?.status ?? {})).toEqual(["ok"]);
    expect(literalValues(healthSchema.properties?.service ?? {})).toEqual(["api"]);
    expect(literalValues(healthSchema.properties?.database ?? {})).toEqual(["configured"]);
    expect(healthSchema.properties?.environment?.type).toBe("string");

    const backendSample = {
      status: "ok",
      service: "api",
      stage: "phase-1-stage-1.3",
      environment: "test",
      database: "configured",
    };

    expect(BackendHealthSchema.parse(backendSample)).toEqual(backendSample);
  });

  it.each(requiredHealthFields)("rejects health payloads missing %s", (field) => {
    const payload = {
      status: "ok",
      service: "api",
      stage: "phase-1-stage-1.3",
      environment: "test",
      database: "configured",
    };
    delete payload[field];

    expect(BackendHealthSchema.safeParse(payload).success).toBe(false);
  });
});
