# runner-controller

Petit service HTTP Python qui fournit à OpenClaw une API limitée de cycle de vie pour les VM runner Proxmox. Il utilise uniquement l’API REST Proxmox, stocke son registre et un journal d’actions dans SQLite, et écoute sur `10.20.30.30:8080`.

## API

| Méthode | Endpoint | Comportement |
| --- | --- | --- |
| `GET` | `/health` | État du processus |
| `POST` | `/runners` | Clone le template 900, configure `agentnet`, démarre, attend le Guest Agent et retourne l’IPv4 |
| `GET` | `/runners` | Liste les runners connus |
| `GET` | `/runners/{id}` | État d’un runner |
| `POST` | `/runners/{id}/shutdown` | Arrêt propre |
| `DELETE` | `/runners/{id}` | Arrêt si nécessaire puis suppression (idempotent) |

Création : `{"memory_mb":4096,"ttl_seconds":21600}`. Les deux champs sont facultatifs. La mémoire est bornée à 512–6144 MiB, le TTL à 300–86400 secondes. Le CPU est fixé à 2, la capacité à deux runners et le TTL par défaut à six heures. Template, pool, stockage, réseau, plage VMID et IP d’écoute sont définis côté serveur.

Les erreurs ont le format `{"error":{"code":"...","message":"..."}}`. Aucun endpoint ne permet de fournir un VMID, un nœud, un template, un stockage, un réseau ou une commande Proxmox.

## Journal SQLite

La table `actions` conserve chaque action de cycle de vie avec son horodatage, action, runner, VMID, résultat, UPID éventuel et métadonnées sans secret. Les corps de réponse Proxmox, en-têtes d’authentification et exceptions brutes ne sont jamais stockés. La base par défaut est `/var/lib/runner-controller/runners.sqlite3`.

## Installation sur le LXC

Depuis une copie du dépôt dans `/opt/runner-controller` :

1. Installer Python 3.11 ou plus récent (aucune dépendance Python tierce).
2. Créer l’utilisateur `runnerctl` s’il n’existe pas, ainsi que les répertoires :

   ```sh
   install -d -o root -g runnerctl -m 0750 /etc/runner-controller
   install -d -o runnerctl -g runnerctl -m 0750 /var/lib/runner-controller
   ```

3. Créer `/etc/runner-controller/pve.env` à partir de `.env.example`, puis protéger le fichier :

   ```sh
   chown root:runnerctl /etc/runner-controller/pve.env
   chmod 0640 /etc/runner-controller/pve.env
   ```

4. Copier `deploy/runner-controller.service` dans `/etc/systemd/system/`, puis activer le service :

   ```sh
   systemctl daemon-reload
   systemctl enable --now runner-controller
   systemctl status runner-controller
   ```

Le socket est lié en dur à `10.20.30.30`; si cette adresse n’est pas présente au démarrage, le service échoue plutôt que d’écouter sur toutes les interfaces. Configurez séparément le firewall Proxmox pour autoriser l’accès à ce port uniquement depuis `10.20.30.10`.

Le certificat HTTPS du PVE doit être approuvé par le magasin de certificats du LXC. Ne désactivez pas la vérification TLS. Les permissions du token doivent être limitées aux opérations nécessaires sur le template et le pool `agent-runners`, `local-lvm`, et `agentnet`; aucun privilège administrateur global n’est requis ni configuré automatiquement. Vérifier notamment les permissions Proxmox de lecture des VMID afin de détecter les collisions dans la plage réservée.

Les tests unitaires utilisent uniquement SQLite temporaire et un faux client Proxmox : `python3 -m unittest discover -s tests -v`.
