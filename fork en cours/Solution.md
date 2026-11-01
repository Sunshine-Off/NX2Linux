# NX2Linux — Solution globale

## Delivery correction and integration gates

The numbered global milestones below describe the target architecture, not
completed deliveries. They are distinct from the S1–S8 and T1–T7 work tracks.
The authoritative current status is in [Swann.md](Swann.md) and the
[remediation report](Jalons-remediation.md).

The original S1 delivery only added frontend/XDG support. S1a now supplies a
native ELF fixture host independent of graphics. S1b remains the integration
with Téo's actual guest runtime, not something proven by native TLS tests.

The original S3/S4 deliveries were a synthetic triangle and demo lifecycle.
S4b still requires the game graphics implementation. Do not treat guest shaders,
textures, buffers, descriptors, NVN command translation or guest GPU synchronization
as existing merely because a Vulkan window works.

S7 artifact inspection and headless integration preparation can begin when Téo's
code and reports become available. S7 cannot be accepted as a working game until
the native/runtime contract, AOT conformance and required graphics path have
concrete tests. Working CLI and Qt controls do not waive these gates. No complete
static Switch game recompilation has been demonstrated by the Swann host tests.

## Objectif

Transformer NX2Apple en un fork Linux x86_64 capable de préparer et lancer des jeux
Switch avec une recompilation statique ARM64 vers x86_64 et un JIT de secours.

Le projet ne doit pas devenir un émulateur généraliste. Le code du jeu doit être
recompilé autant que possible avant l’exécution. Le JIT intervient seulement pour
les fonctions ou blocs impossibles à résoudre statiquement.

## Répartition générale

### Partie Téo — Moteur et recompilation

Téo prend en charge le cœur technique : analyse NSO, recompilation ARM64 vers
x86_64, runtime mémoire, services Horizon, loader ELF et compatibilité du code jeu.

### Partie Swann — Plateforme et intégration

Swann prend en charge le host Linux, Vulkan, le frontend Qt, le build system, la CLI,
les tests, le packaging et la distribution.

Les deux parties doivent converger autour de contrats stables : manifest, mémoire
invitée, appels SVC, événements de build, logs et artefacts.

## Jalons

### Jalon 0 — Préparation commune

- créer le fork et conserver la licence
- conserver `engine/` comme source de référence locale
- séparer les patches communs, Apple et Linux
- définir l’architecture des artefacts Linux
- créer une CI Linux x86_64 minimale
- ne jamais versionner de clés, dumps ou données de jeu

Livrable : dépôt NX2Linux documenté et reproductible.

### Jalon 1 — Réutilisation de la chaîne de décompilation

- utiliser `nx_extract` pour NSP/XCI/NSZ
- réutiliser `nso.py`, `nso_code.py`, `nso_asm.py` et `nso2elf.py`
- conserver `patch_nso.py` et les profils de jeux
- vérifier Capstone 5
- produire des fixtures NSO synthétiques

Livrable : extraction et réécriture ARM64 fonctionnelles sous Linux, sans exécution.

### Jalon 2 — Preuve de recompilation ARM64 vers x86_64

- analyser une fixture ARM64
- lever le code vers LLVM IR avec une brique existante comme Remill
- générer du x86_64 avec LLVM
- gérer registres, NZCV, branches, mémoire et atomiques
- utiliser un JIT de secours pour les blocs dynamiques
- comparer les résultats avec une référence ARM64

Livrable : une fonction ARM64 recompilée et exécutée correctement sur x86_64.

### Jalon 3 — Runtime et loader ELF

- définir le manifest des modules
- remplacer Mach-O par ELF
- implémenter mémoire invitée, TLS et threads
- gérer appels indirects et pointeurs de fonctions
- intercepter les SVC
- charger les modules recompilés avec `dlopen` ou un chargeur dédié

Livrable : un module recompilé se charge dans un processus Linux contrôlé.

### Jalon 4 — Host Linux POSIX

- porter les services Horizon nécessaires
- ajouter fichiers, horloge, mmap, threads et synchronisation
- ajouter clavier, manette et audio
- remplacer les APIs Objective-C et Apple
- ajouter logs, crash reports et chemins XDG

Livrable : host ELF Linux démarrant en mode headless.

### Jalon 5 — Renderer Linux

- remplacer Metal par Vulkan Linux
- conserver les invariants graphiques NVN
- porter textures, buffers, descriptors, pipelines et synchronisation
- produire et charger du SPIR-V
- tester avec Mesa lavapipe

Livrable : fenêtre Vulkan et frame de test correcte.

### Jalon 6 — Premier jeu

- choisir un jeu simple avec profil existant
- extraire, réécrire, recompiler et charger ses modules
- atteindre la boucle de jeu
- valider input, rendu, audio et sauvegardes
- mesurer le ratio AOT/JIT

Livrable : premier jeu Linux x86_64 documenté et reproductible.

### Jalon 7 — Frontend et CLI

- ajouter `LinuxBackend` à `frontend-qt`
- créer `nx2linux doctor`
- créer `nx2linux build`, `run` et `verify`
- afficher les étapes AOT, JIT et host
- stocker logs et réglages sous XDG

Livrable : parcours utilisateur Linux complet sans Xcode ni Metal.

### Jalon 8 — Distribution

- produire un tarball ou une AppImage x86_64
- publier un manifest signé
- intégrer un `engine-id` Linux
- ajouter CI Debug, Release et lavapipe
- documenter les GPU Intel, AMD et NVIDIA

Livrable : release Linux installable et vérifiable.

## Architecture cible

```text
NSO ARM64
  -> analyse NX2Apple
  -> LLVM IR
  -> code x86_64 AOT
  -> JIT des blocs dynamiques
  -> runtime mémoire et services Horizon
  -> host Linux POSIX
  -> Vulkan
```

## Décision technique

La recompilation statique hybride est retenue. Une émulation complète ne constitue
que l’oracle de validation ou le dernier fallback. Le port Metal existant fournit
les invariants de comportement, mais Linux devra utiliser une implémentation Vulkan.
