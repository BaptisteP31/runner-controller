# AGENTS.md — runner-controller

## 1. Rôle du projet

`runner-controller` est le service de contrôle chargé de gérer le cycle de vie des machines virtuelles temporaires utilisées comme **runners d'agents IA/OpenClaw** sur le cluster Proxmox `pve-lab`.

Le service agit comme une couche intermédiaire entre OpenClaw et Proxmox.

OpenClaw et les agents ne doivent **jamais disposer directement des identifiants ou permissions Proxmox**.

Architecture logique :

```text
OpenClaw / orchestrateur
        |
        | API HTTP
        v
runner-controller
        |
        | API Proxmox VE
        v
     pve-lab
        |
        +-- tpl-agent-runner
        |
        +-- runner-<id>
        +-- runner-<id>
        +-- ...
```

Les runners sont considérés comme des workloads **potentiellement non fiables**.

Le contrôleur, au contraire, est un composant d'infrastructure privilégié et doit rester minimal, prévisible et fortement contrôlé.

---

## 2. Objectif fonctionnel

L'API doit permettre à un orchestrateur de :

* créer un runner à partir du template `tpl-agent-runner` ;
* démarrer le runner ;
* récupérer les informations nécessaires pour l'utiliser ;
* consulter son état ;
* arrêter le runner ;
* détruire le runner lorsqu'il n'est plus nécessaire.

À terme, elle pourra également gérer :

* TTL / expiration automatique ;
* nettoyage des runners abandonnés ;
* quotas ;
* association runner ↔ tâche / agent ;
* métadonnées ;
* limites CPU / RAM ;
* journalisation des opérations.

Ne pas ajouter de fonctionnalités en dehors de ce périmètre sans nécessité explicite.

---

# 3. Infrastructure actuelle

Hyperviseur :

```text
Proxmox VE 9.x
Node : pve-lab
```

Matériel approximatif :

```text
Intel Core i7-6700
4 cœurs / 8 threads
16 Go RAM
NVMe ~500 Go
HDD ~500 Go
1 GbE
```

Cette machine est dédiée à l'environnement OpenClaw et à ses services associés.

Elle n'est pas destinée à exécuter de gros modèles LLM localement.

---

# 4. Réseau des runners

Les runners sont placés sur le réseau isolé OpenClaw :

```text
SDN zone : ai
VNet     : agentnet
```

Ils ne doivent pas obtenir un accès direct non contrôlé au LAN principal.

Le réseau dispose notamment d'un DHCP/DNS géré côté infrastructure.

Lors de la création d'une VM, utiliser le réseau prévu pour les runners et ne pas déplacer arbitrairement les VMs vers un bridge LAN tel que `vmbr0`.

La segmentation réseau est une propriété de sécurité importante.

---

# 5. Template

Les runners doivent être créés à partir du template :

```text
tpl-agent-runner
```

Ne pas réimplémenter le provisioning complet d'une VM lorsque le template peut être utilisé.

Le template doit contenir le socle logiciel commun nécessaire aux runners.

Le contrôleur est responsable du **cycle de vie**, pas de la configuration détaillée du système invité.

---

# 6. Communication avec Proxmox

Utiliser exclusivement l'API Proxmox VE.

Éviter :

```text
ssh root@pve-lab
qm ...
pvesh ...
```

depuis le code applicatif.

Ces commandes peuvent être utiles manuellement pour l'administration ou le diagnostic, mais ne constituent pas l'interface normale du service.

L'API utilise un token dédié au service, sur le principe :

```text
Authorization: PVEAPIToken=<user>@pve!<token>=<secret>
```

Le token doit disposer uniquement des permissions nécessaires.

Ne jamais proposer d'utiliser :

```text
root@pam
```

ou un token administrateur global simplement pour contourner un problème de permissions.

---

# 7. Principe du moindre privilège

Les permissions Proxmox doivent être ajoutées uniquement lorsqu'une opération concrète les nécessite.

Des permissions telles que :

```text
VM.Allocate
VM.Clone
VM.PowerMgmt
VM.Config.*
VM.Audit
Datastore.AllocateSpace
SDN.Use
```

peuvent être nécessaires selon l'opération.

Toujours préférer :

```text
ajouter une permission précise
```

à :

```text
donner Administrator au token
```

Certaines opérations Proxmox peuvent réussir puis retourner une erreur lors d'une vérification secondaire.

Exemple déjà rencontré :

```text
DELETE VM
→ suppression effective
→ vérification suivante
→ VM.Audit échoue car la VM n'existe déjà plus
```

Le code doit donc distinguer :

* l'échec réel d'une opération ;
* l'échec d'une vérification postérieure ;
* un état déjà atteint.

Les opérations doivent autant que possible être **idempotentes**.

---

# 8. Sécurité critique

Considérer toutes les données fournies par un agent comme non fiables.

Cela inclut notamment :

```text
nom du runner
VMID
CPU
RAM
disk size
variables
metadata
commandes
identifiants de tâche
```

Toute donnée reçue doit être validée.

Ne jamais permettre à l'appelant de fournir directement :

* un endpoint arbitraire de l'API Proxmox ;
* un node arbitraire ;
* un storage arbitraire ;
* un bridge arbitraire ;
* un template arbitraire ;
* un chemin fichier arbitraire ;
* une commande shell arbitraire.

Préférer des valeurs contrôlées côté serveur.

Exemple :

```python
PROXMOX_NODE = "pve-lab"
RUNNER_TEMPLATE = 900
RUNNER_VNET = "agentnet"
```

plutôt qu'un équivalent fourni dans chaque requête cliente.

---

# 9. Interdiction d'exécution shell

L'API ne doit pas devenir une API générique d'exécution distante.

Ne jamais créer des endpoints comme :

```text
POST /shell
POST /exec
POST /command
POST /ssh
```

qui accepteraient une commande arbitraire.

Le contrôleur doit exposer des opérations métier explicites :

```text
POST /runners
GET  /runners/{id}
POST /runners/{id}/start
POST /runners/{id}/stop
DELETE /runners/{id}
```

Une nouvelle fonctionnalité sensible doit être implémentée sous forme d'une opération définie et validée, pas sous forme d'un shell générique.

---

# 10. API souhaitée

L'API doit rester simple et REST.

Structure cible indicative :

```text
GET /health

POST /runners
GET /runners
GET /runners/{id}

POST /runners/{id}/start
POST /runners/{id}/stop

DELETE /runners/{id}
```

Une création pourrait accepter :

```json
{
  "name": "agent-a1b2c3",
  "cpu": 2,
  "memory_mb": 2048,
  "ttl_seconds": 3600
}
```

mais les valeurs doivent être bornées côté serveur.

Exemple de limites raisonnables :

```text
CPU        : 1–4
RAM        : 512–4096 MiB
TTL        : borné
nom        : format strict
```

Ces valeurs ne constituent pas nécessairement les limites définitives : conserver leur configuration centralisée.

---

# 11. Identité des runners

Ne pas laisser les agents choisir librement un VMID Proxmox.

Le VMID doit être :

* attribué par Proxmox si possible ;
* ou attribué par le contrôleur depuis une plage réservée.

Le nom du runner doit être généré ou fortement validé.

Format recommandé :

```text
runner-<identifiant>
```

Exemple :

```text
runner-a83f27c1
```

Les noms ne doivent pas contenir de caractères permettant une injection ou une ambiguïté.

---

# 12. Gestion des tâches Proxmox

De nombreuses opérations Proxmox sont asynchrones et retournent un UPID.

Exemple :

```text
UPID:pve-lab:...:qmclone:...
```

Ne pas considérer la simple réception d'un UPID comme la réussite finale de l'opération.

Le contrôleur doit pouvoir :

1. lancer l'opération ;
2. récupérer l'UPID ;
3. attendre ou vérifier son état ;
4. vérifier le résultat final.

Prévoir une abstraction dédiée aux tâches Proxmox plutôt que de dupliquer cette logique dans tous les endpoints.

---

# 13. États et erreurs

Utiliser des erreurs API structurées.

Exemple :

```json
{
  "error": {
    "code": "RUNNER_NOT_FOUND",
    "message": "Runner not found"
  }
}
```

Ne pas exposer directement :

* le token Proxmox ;
* les secrets ;
* les headers Authorization ;
* les traces contenant des credentials.

Les logs peuvent conserver :

```text
request_id
runner_id
vmid
action
UPID
durée
résultat
```

mais jamais les secrets.

---

# 14. Destruction des runners

La destruction est une opération normale.

`DELETE /runners/{id}` doit tendre vers un comportement idempotent.

Si le runner n'existe déjà plus :

```text
DELETE /runners/{id}
```

doit pouvoir être considéré comme un état final valide.

Ne pas transformer systématiquement cette situation en erreur serveur.

La priorité est de garantir :

```text
runner absent après DELETE
```

---

# 15. TTL et garbage collection

Les runners sont temporaires.

Toute création doit à terme pouvoir être associée à :

```text
created_at
expires_at
owner/task id
```

Un mécanisme de garbage collection pourra supprimer les VMs ayant dépassé leur TTL.

Ne jamais supposer qu'un agent supprimera proprement son runner.

La sécurité et la maîtrise des ressources reposent sur le contrôleur.

---

# 16. Concurrence

Plusieurs agents peuvent demander un runner simultanément.

Éviter les algorithmes du type :

```text
chercher VMID libre
puis créer VM
```

sans protection contre les races.

La création doit être robuste aux appels concurrents.

De même, deux suppressions concurrentes d'un même runner ne doivent pas entraîner une erreur critique.

---

# 17. Résilience

Les erreurs Proxmox suivantes doivent être considérées comme normales et gérées proprement :

```text
timeout
VM inexistante
VM déjà démarrée
VM déjà arrêtée
VM supprimée entre deux requêtes
task Proxmox échouée
permission refusée
ressources insuffisantes
VMID déjà utilisé
erreur réseau
```

Ne jamais contourner automatiquement une erreur de permission en élargissant les droits.

Une erreur de permissions doit être remontée explicitement.

---

# 18. Organisation du code

Favoriser une séparation claire :

```text
API / routes
    ↓
services métier
    ↓
client Proxmox
    ↓
API Proxmox
```

Exemple conceptuel :

```text
app/
  api/
  services/
    runner_service.*
  proxmox/
    client.*
    tasks.*
  models/
  config/
```

La logique Proxmox ne doit pas être dispersée dans les routes HTTP.

Les routes doivent rester fines.

---

# 19. Configuration

Toute configuration dépendante de l'environnement doit passer par configuration ou variables d'environnement.

Exemples :

```text
PROXMOX_URL
PROXMOX_TOKEN_ID
PROXMOX_TOKEN_SECRET

PROXMOX_NODE
RUNNER_TEMPLATE_ID
RUNNER_STORAGE
RUNNER_VNET

RUNNER_CPU_DEFAULT
RUNNER_MEMORY_DEFAULT
RUNNER_TTL_DEFAULT
```

Les secrets ne doivent jamais être commités.

Prévoir un :

```text
.env.example
```

sans valeurs sensibles.

---

# 20. Dépendances

Limiter les dépendances.

Avant d'ajouter une librairie :

1. vérifier si elle est réellement nécessaire ;
2. privilégier une librairie maintenue ;
3. éviter les frameworks lourds pour une fonctionnalité triviale.

Ce service fait partie de l'infrastructure critique : la simplicité est préférable à l'abstraction excessive.

---

# 21. Tests

Toute logique importante doit être testable sans disposer d'un vrai serveur Proxmox.

Prévoir une abstraction du client Proxmox permettant de le mocker.

Tester en priorité :

```text
création réussie
échec du clone
timeout d'une tâche
VM inexistante
start déjà effectué
stop déjà effectué
double DELETE
validation CPU/RAM
validation des noms
permissions Proxmox refusées
```

Ne jamais faire pointer les tests automatisés vers le serveur `pve-lab` par défaut.

Les tests d'intégration réels doivent être explicitement activés.

---

# 22. Modifications de l'infrastructure

Ne jamais modifier automatiquement :

```text
configuration SDN Proxmox
firewall Proxmox
dnsmasq
bridges réseau
storage Proxmox
permissions utilisateurs/tokens
configuration du template
```

depuis ce projet.

Si une modification infrastructure est nécessaire pour implémenter une fonctionnalité :

1. identifier précisément le besoin ;
2. expliquer la modification ;
3. fournir éventuellement la commande ou la procédure ;
4. laisser l'administrateur décider de l'appliquer.

Le code applicatif ne doit pas reconfigurer son environnement d'hébergement.

---

# 23. Travail avec Codex / agents de développement

Lorsqu'un agent travaille dans ce dépôt :

1. lire ce fichier avant toute modification ;
2. inspecter le code existant ;
3. modifier le minimum nécessaire ;
4. conserver l'architecture existante lorsque celle-ci est raisonnable ;
5. ne pas réécrire tout le projet pour une modification locale ;
6. exécuter les tests disponibles ;
7. ajouter des tests lorsque le comportement change ;
8. documenter les nouvelles variables d'environnement ;
9. signaler explicitement toute nouvelle permission Proxmox nécessaire.

Avant de terminer une tâche, vérifier :

```text
[ ] aucun secret ajouté au dépôt
[ ] aucune exécution shell arbitraire introduite
[ ] aucune permission Proxmox inutile supposée
[ ] validation des entrées présente
[ ] erreurs Proxmox correctement gérées
[ ] opérations destructives limitées aux runners
[ ] comportement concurrent raisonnable
[ ] tests exécutés ou impossibilité expliquée
```

---

# 24. Règle fondamentale

La frontière de sécurité recherchée est :

```text
Agent IA non fiable
        |
        v
API runner-controller fortement limitée
        |
        v
Proxmox privilégié
```

Toute évolution qui transforme cette architecture en :

```text
Agent IA
   |
   v
accès Proxmox générique
```

est contraire à l'objectif du projet.

En cas de choix entre :

```text
plus de flexibilité pour l'agent
```

et :

```text
une API plus limitée mais contrôlable
```

préférer l'API limitée.

---

# 25. Priorités techniques

Ordre général de priorité pour ce projet :

1. **Isolation et sécurité**
2. **Prévisibilité du cycle de vie des runners**
3. **Absence de fuite de ressources**
4. **Simplicité**
5. **Observabilité**
6. **Performance**
7. **Confort développeur**

Quelques millisecondes supplémentaires sont acceptables si elles rendent les opérations plus sûres et vérifiables.

