# Ratchet demo on a shared VM

The demo runs in two small containers with hard limits, so the other services on the machine keep their memory and CPU.
It is the mock-mode simulation: no keys, nothing secret, and the numbers it shows are simulation, labelled as such.
For the live gateway (Token Factory) use [`deploy/vm`](../vm).

| Container | RAM limit | CPU limit | Notes |
|---|---|---|---|
| `ratchet-demo` | 1.5 GiB (`MEM_GATEWAY`) | 1.5 vCPU | listens on `127.0.0.1:18000` only |
| `tunnel` (optional) | 256 MiB | 0.25 vCPU | a Cloudflare Tunnel, so no public port is opened |

Both have no swap, a cap of 200 and 100 processes, no extra Linux capabilities, and `oom_score_adj` 500: if the host runs out of
memory, the kernel stops these first.

```bash
# the VM needs only docker-compose.yml and .env (no source code, no build):
mkdir -p ~/ratchet-demo && cd ~/ratchet-demo
curl -fsSLO https://raw.githubusercontent.com/harshbhx/ratchet/main/deploy/shared-vm/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/harshbhx/ratchet/main/deploy/shared-vm/.env.example -o .env
docker compose pull               # the image is ghcr.io/harshbhx/ratchet-gateway:shared
docker compose up -d              # the first start runs the mock warm-up: about a minute or two of CPU
curl -s http://127.0.0.1:18000/healthz
docker stats --no-stream          # check the memory and CPU the first day
```

Public HTTPS address without opening a port: create a tunnel in the Cloudflare dashboard (Zero Trust, Networks, Tunnels), add a
public hostname with the service `http://ratchet-demo:8000`, put the tunnel token in `.env` as `TUNNEL_TOKEN`, then run
`docker compose --profile tunnel up -d`. Never commit `.env`.

The package is private after its first publish: make it public once (GitHub, Packages, `ratchet-gateway`, Package settings, Change visibility), or log the VM in with `docker login ghcr.io` and a token that has only `read:packages`.

To build from source instead: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.

Stop and remove it without touching anything else: `docker compose down`.

Not verified: how it behaves next to your other services under judging traffic. Watch `docker stats` and raise `MEM_GATEWAY` or
lower the demo's request limits if needed.
