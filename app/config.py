from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    pve_host: str
    pve_node: str
    pve_token_id: str
    pve_token_secret: str
    bind_host: str = "10.20.30.30"
    bind_port: int = 8080
    database_path: str = "/var/lib/runner-controller/runners.sqlite3"
    vmid_min: int = 920
    vmid_max: int = 949
    max_runners: int = 2
    template_id: int = 900
    pool: str = "agent-runners"
    storage: str = "local-lvm"
    vnet: str = "agentnet"
    cpu: int = 2
    memory_default_mb: int = 4096
    memory_min_mb: int = 512
    memory_max_mb: int = 6144
    ttl_default_seconds: int = 21600
    ttl_min_seconds: int = 300
    ttl_max_seconds: int = 86400
    task_timeout_seconds: int = 300
    guest_agent_timeout_seconds: int = 180
    request_timeout_seconds: int = 15
    gc_interval_seconds: int = 60

    @classmethod
    def from_env(cls):
        host = os.environ["PVE_HOST"].rstrip("/")
        if not host.startswith(("https://", "http://")):
            host = "https://" + host
        return cls(
            pve_host=host,
            pve_node=os.environ.get("PVE_NODE", "pve-lab"),
            pve_token_id=os.environ["PVE_TOKEN_ID"],
            pve_token_secret=os.environ["PVE_TOKEN_SECRET"],
            bind_host="10.20.30.30",
            bind_port=int(os.environ.get("RUNNER_BIND_PORT", "8080")),
            database_path=os.environ.get("RUNNER_DATABASE", "/var/lib/runner-controller/runners.sqlite3"),
            ttl_default_seconds=int(os.environ.get("RUNNER_TTL_DEFAULT", "21600")),
        )
