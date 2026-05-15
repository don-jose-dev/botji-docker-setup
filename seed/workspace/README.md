# Botji Workspace

Put the tenant project/repo here before asking Botji to use Codex.

Default Codex boundary:
- readable/writable: this workspace
- credentials: stored outside workspace under `/opt/data/.codex`
- network: disabled in Codex workspace-write profile
- Docker socket: not mounted unless explicitly enabled in `docker-compose.yml`

Do not put unrelated tenant/customer files here.
