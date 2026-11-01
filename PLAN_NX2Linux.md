# Plan d’exécution NX2Linux x86_64

## Objectif

Obtenir un fork Linux x86_64 capable de préparer et lancer au moins un jeu validé,
avec un frontend Qt, un host ELF natif, une fenêtre Vulkan et une distribution
reproductible. La compatibilité complète n’est pas présumée : elle sera mesurée par
jeu, version, profil et GPU.

## Règles de portage

1. Le moteur de réécriture ARM64 est conservé autant que possible.
2. Le code invité ARM64 ne devient pas automatiquement du x86_64.
3. Toute dépendance Apple reçoit une implémentation Linux séparée.
4. Les patches communs restent partageables avec upstream. Les patches Linux sont
   séparés des patches Apple.
5. Aucun fichier de jeu, clé ou donnée extraite n’entre dans Git.
6. Chaque jalon doit être testable sans attendre le rendu complet.

## Phase 0 — Baseline et découpage

Livrables :

- branche de travail NX2Linux
- `docs/linux/architecture.md`
- liste des patches communs, Apple-only et candidats Linux
- inventaire des APIs Apple dans `dm/host`, `dm/gfx` et `dm/ios`
- CI Linux minimale

Validation :

- `python3 tools/engine.py check`
- tests Qt et tools verts
- `python3 -m compileall` sur `engine/dm/tools`, `engine/dm/games` et `engine/scripts`

## Phase 1 — Réécriture et extraction Linux

Objectif : faire fonctionner la partie « décompilateur » au sens pratique, sans
fenêtre et sans host.

Travail :

- ajouter une cible Linux pour `dm/tools/nx_extract` avec .NET
- vérifier `nso.py`, `nso_code.py`, `nso_asm.py`, `nso2elf.py`
- exécuter `patch_nso.py` avec Capstone 5
- porter les chemins temporaires, permissions et hard links
- remplacer la détection Mac dans `build_ipa_from_nsp.py` par un dispatcher de cible
- produire un manifeste Linux et un répertoire de build

Critères de sortie :

- extraction d’une fixture synthétique NSP/XCI
- réécriture déterministe d’une fixture NSO
- tests d’échec pour mauvais Capstone, architecture non supportée et profil absent
- aucun appel à Xcode, Metal ou `xcrun` dans le chemin headless Linux

Cette phase ne résout pas encore l’exécution sur x86_64. Elle démontre seulement que
les artefacts ARM64 peuvent être extraits et réécrits sous Linux.

## Phase 2 — Loader ELF et exécution de module

Objectif : charger les artefacts Linux sans Mach-O.

Travail :

- définir un manifest de modules, profils, tables et hashes
- créer `loader_linux.c` ou une implémentation POSIX équivalente
- remplacer `mach_header`, sections Mach-O, `dladdr` Apple et frameworks
- utiliser ELF, `dlopen`, `dlsym`, `$ORIGIN` et RPATH
- commencer par des modules externes du bundle, sans sections ELF custom
- tester tailles, alignement, relocation, symboles et erreurs de chargement

Critères de sortie :

- un module ELF synthétique se charge et se décharge
- les erreurs sont déterministes et diagnostiquées
- tests ASan et UBSan du loader
- aucune dépendance à Objective-C ou framework Apple

Le loader ELF ne résout pas à lui seul la différence ARM64/x86_64. Pour une cible
x86_64, cette phase doit produire une frontière claire entre le host x86_64 et un
runtime ARM64 : traducteur dynamique, émulateur ou autre solution validée.

## Phase 2 bis — Preuve de recompilation ARM64 vers x86_64

Cette preuve est bloquante avant le port complet du host et du renderer.

Prototype minimal :

- analyser une fixture ARM64 contrôlée
- traduire statiquement ses fonctions vers une IR puis x86_64
- exécuter les blocs non résolus avec un JIT de secours
- gérer mémoire invitée, appels indirects, TLS, threads et exceptions
- intercepter les appels SVC et les rediriger vers le host
- mesurer le ratio AOT/JIT, les transitions et les invalidations de code

La cible est une recompilation statique réelle, avec JIT uniquement pour le code
dynamique ou non prouvable. Le jeu contient du code généré, des pointeurs de
fonctions, des hypothèses d’adresses et des appels système. Le prototype doit donc
prouver les registres, NZCV, mémoire virtuelle, atomiques, branches indirectes et
transitions SVC avant toute généralisation. En parallèle, une cible Linux ARM64 peut
servir de plateforme de validation du host, mais elle ne remplace pas la cible finale.

### Réutilisation des briques

Le prototype ne doit pas créer un compilateur ARM64 complet depuis zéro :

- Remill peut fournir la levée AArch64 vers LLVM IR
- LLVM peut générer le code x86_64
- Capstone et les analyseurs NSO du moteur fournissent les frontières et règles Switch
- QEMU TCG peut servir d’oracle d’exécution et de fallback de comparaison
- NX2Linux fournit les profils, l’état mémoire, les callouts Horizon et la logique NVN

Remill et QEMU ne comprennent pas automatiquement les NSO, les SVC Horizon, les
modules dynamiques ou les profils de jeux. Ils sont donc des composants de traduction,
pas le runtime complet. Le contrat à mesurer est le ratio de fonctions AOT, le ratio
de blocs JIT et le nombre de transitions vers les callouts host.

## Phase 3 — Host POSIX minimal

Objectif : obtenir un processus Linux stable avant le rendu réel.

Travail :

- créer `dm/host/linux/`
- remplacer `front_mac.m` par SDL3 ou SDL2
- porter threads, horloge, mmap, fichiers, signaux et crash reports
- porter clavier, manette, haptics et audio avec des adaptateurs distincts
- déplacer les logs vers `$XDG_STATE_HOME/nx2linux/logs`
- définir les limites Linux au lieu de simuler les entitlements iOS

Critères de sortie :

- host ELF qui démarre en mode headless
- fenêtre SDL facultative
- clavier et manette détectés
- arrêt propre et log complet
- tests unitaires POSIX séparés des tests Apple

## Phase 4 — Renderer Linux

Objectif : conserver les invariants du renderer Metal tout en fournissant une
implémentation Linux. Metal est Apple-only et ne peut pas être utilisé directement
sous Linux x86_64.

Travail :

- créer un backend Vulkan Linux explicite, inspiré du comportement Metal existant
- définir textures, buffers, descriptors, passes et synchronisation
- utiliser Vulkan 1.3 et vérifier les extensions au démarrage
- remplacer la compilation MSL par SPIR-V
- porter pipeline cache, timestamps, frame pacing et readback
- garder OpenGL comme expérimentation secondaire, pas comme cible principale

Critères de sortie :

- test triangle Vulkan sous Mesa lavapipe
- tests de formats, layouts, barriers et descriptors
- compilation des shaders sans Xcode ni Metal
- capture et comparaison d’une frame de test

## Phase 5 — Premier jeu validé

Choisir un jeu simple déjà couvert par un profil et sans dépendance graphique
exceptionnelle. Ne pas choisir le jeu le plus complexe pour le premier jalon.

Travail :

- extraction et profil
- réécriture et loader
- host, rendu, audio et input
- saves et chargement RomFS
- comparaison des logs et performances

Critères de sortie :

- démarrage reproductible trois fois
- menu et boucle de jeu atteints
- clavier et manette utilisables
- aucune `[RED]` non expliquée
- résultat documenté avec title ID, version, profil, GPU et pilote

## Phase 6 — Frontend Qt Linux

Objectif : rendre le workflow accessible sans casser les composants portables.

Travail :

- ajouter `LinuxBackend` au modèle Qt
- cible `linux-x86_64`
- commande `doctor` pour Python, .NET, Clang, Vulkan et SDL
- configuration et logs XDG
- build, run, stop, export log et reprise de build
- bundle et fichier `.desktop` facultatif

Critères de sortie :

- installation depuis un tarball propre
- aucun bouton qui propose Xcode ou Metal sur Linux
- test UI du parcours dépendances, choix de jeu, build et lancement

## Phase 7 — Distribution et CI

Livrables :

- tarball Linux x86_64 ou AppImage
- manifest signé et `engine-id` Linux
- CI Debug et Release
- test lavapipe headless
- vérification de reproductibilité
- documentation installation et diagnostic

Matrice ultérieure : Mesa llvmpipe, Intel, AMD et NVIDIA. Les tests matériels réels
doivent rester externes aux fixtures du dépôt.

## Découpage technique recommandé

```text
common/
  extraction, NSO, profils, manifest, logs
platform/linux/
  POSIX, ELF, SDL, Vulkan, XDG
platform/apple/
  Mach-O, Metal, AppKit, iOS
frontend-qt/
  modèle et UI partagés, adaptateurs de backend
```

## Risques et décisions bloquantes

| Risque | Impact | Réponse |
| --- | --- | --- |
| code invité ARM64 sur machine x86_64 | bloquant pour l’exécution | prototype DBT/émulation, avec Linux ARM64 comme voie de validation |
| loader Mach-O | bloquant | implémenter ELF avec manifest externe d’abord |
| shaders Metal | bloquant graphique | SPIR-V/Vulkan, tests lavapipe avant jeux |
| host Objective-C | bloquant build | host Linux séparé, ne pas surcharger les fichiers Mac |
| profils Apple-only | compatibilité variable | matrice par jeu et profil Linux |
| dépendances SDK | distribution | doctor, versions minimales et bundle contrôlé |

## Point de décision après la Phase 1

Si l’extraction et la réécriture passent sous Linux mais que l’exécution ARM64 ne
peut pas être assurée proprement sur x86_64, le projet doit choisir entre :

- ajouter un traducteur ARM64 utilisateur
- supporter une cible Linux ARM64 en plus de x86_64
- limiter NX2Linux à la préparation des jeux

Cette décision doit être prise avant d’investir dans le port complet du renderer.
