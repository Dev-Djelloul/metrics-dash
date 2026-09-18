import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  METRICS_DASH_CONTAINER: DurableObjectNamespace<MetricsDashContainer>;
  STRIPE_SECRET_KEY: string;
}

// Le Worker ne fait que router les requêtes vers le conteneur Docker
// (main.py / FastAPI). Toute la logique métier reste dans le Python existant.
export class MetricsDashContainer extends Container<Env> {
  defaultPort = 8420;
  sleepAfter = "10m";

  envVars = {
    STRIPE_SECRET_KEY: this.env.STRIPE_SECRET_KEY ?? "",
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
