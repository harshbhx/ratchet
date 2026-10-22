// The Ratchet demo as a Cloudflare Container: the same mock-mode image as deploy/shared-vm, no keys, no secrets.
// A cron trigger pings it every 5 minutes, so it stays awake and a visitor never waits for the mock warm-up.
import { Container, getContainer } from "@cloudflare/containers";

export class RatchetDemo extends Container {
  defaultPort = 8000;
  sleepAfter = "1h"; // the cron below keeps it awake; if the cron stops, it sleeps after an hour without a request
  envVars = {
    RATCHET_MODE: "mock",
    RATCHET_MOCK_WARMUP_DAYS: "4",
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
  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    await getContainer(env.RATCHET_DEMO).fetch(new Request("http://container/healthz"));
  },
};
