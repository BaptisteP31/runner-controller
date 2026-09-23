import logging
import os
import threading
import time

from .config import Config
from .httpd import serve
from .proxmox import ProxmoxClient
from .service import RunnerService
from .store import Store


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    store = Store(config.database_path)
    service = RunnerService(config, store, ProxmoxClient(config))

    def cleanup_loop():
        while True:
            service.cleanup_expired()
            time.sleep(config.gc_interval_seconds)

    threading.Thread(target=cleanup_loop, name="ttl-cleanup", daemon=True).start()
    serve(service, config.bind_host, config.bind_port)


if __name__ == "__main__":
    main()
