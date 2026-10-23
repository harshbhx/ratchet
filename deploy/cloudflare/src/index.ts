// The Ratchet demo as a Cloudflare Container: the repo's mock-mode image, no keys, no secrets.
// On demand: nothing calls it in the background. The first visitor starts it (about a minute while the mock warm-up runs) and it
// goes back to sleep 20 minutes after the last request, so it costs nothing while nobody is looking.
import { Container, getContainer } from "@cloudflare/containers";

export class RatchetDemo extends Container {
  defaultPort = 8000;
  sleepAfter = "20m"; // sleeps after 20 minutes without a request
  envVars = {
    RATCHET_MODE: "mock",
    RATCHET_MOCK_WARMUP_DAYS: "2",
    RATCHET_DEMO_RPM: "30",
    RATCHET_DEMO_MAX_DAYS: "30",
    RATCHET_DEMO_MAX_REQUESTS: "200000",
    RATCHET_DEMO_IDLE_RESET_S: "7200",
    RATCHET_IP_RPM: "60",
    RATCHET_MAX_INFLIGHT: "8",
    RATCHET_STATS_TTL_S: "2",
  };
}

interface Env {
  RATCHET_DEMO: DurableObjectNamespace<RatchetDemo>;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return getContainer(env.RATCHET_DEMO).fetch(request);
  },
};
