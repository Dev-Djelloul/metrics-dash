import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  METRICS_DASH_CONTAINER: DurableObjectNamespace<MetricsDashContainer>;
  STRIPE_SECRET_KEY: string;
  STRIPE_CONNECT_CLIENT_ID: string;
  SESSION_SECRET: string;
  CF_ACCOUNT_ID: string;
  CF_API_TOKEN: string;
  CF_D1_DATABASE_ID: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  SHOPIFY_API_KEY: string;
  SHOPIFY_API_SECRET: string;
}

// Le Worker ne fait que router les requêtes vers le conteneur Docker
// (main.py / FastAPI). Toute la logique métier reste dans le Python existant.
export class MetricsDashContainer extends Container<Env> {
  defaultPort = 8420;
  sleepAfter = "10m";

  envVars = {
    STRIPE_SECRET_KEY: this.env.STRIPE_SECRET_KEY ?? "",
    STRIPE_CONNECT_CLIENT_ID: this.env.STRIPE_CONNECT_CLIENT_ID ?? "",
    SESSION_SECRET: this.env.SESSION_SECRET ?? "",
    CF_ACCOUNT_ID: this.env.CF_ACCOUNT_ID ?? "",
    CF_API_TOKEN: this.env.CF_API_TOKEN ?? "",
    CF_D1_DATABASE_ID: this.env.CF_D1_DATABASE_ID ?? "",
    GOOGLE_CLIENT_ID: this.env.GOOGLE_CLIENT_ID ?? "",
    GOOGLE_CLIENT_SECRET: this.env.GOOGLE_CLIENT_SECRET ?? "",
    SHOPIFY_API_KEY: this.env.SHOPIFY_API_KEY ?? "",
    SHOPIFY_API_SECRET: this.env.SHOPIFY_API_SECRET ?? "",
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Instance unique : le dashboard est un usage perso/démo, pas besoin
    // de répartir sur plusieurs conteneurs.
    const container = getContainer(env.METRICS_DASH_CONTAINER);
    return container.fetch(request);
  },
};
