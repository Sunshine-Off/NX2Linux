# NX2Linux — Partie Swann

## Corrected delivery status

This section supersedes earlier claims that all of S1–S6 were complete.
See [remediation and validation](Jalons-remediation.md).

| Milestone | Delivered scope | Remaining gate |
|---|---|---|
| S1a | Native headless ELF fixture host, guarded scratch memory, clock and native thread/TLS tests | Not a Switch runtime |
| S1b | Explicit native/runtime ownership boundary | Agree and test the AOT runtime adapter with Téo |
| S2 | SDL window, keyboard and opened gamepads with virtual-device tests | Physical controller and desktop acceptance |
| S3 | Synthetic Vulkan triangle with corrected command pool | Strict Khronos and lavapipe checks on a working installation |
| S4a | Demo swapchain lifecycle and corrected presentation semaphores | Same strict Vulkan gate as S3 |
| S4b | Not implemented | Game resources, guest shaders, NVN commands and synchronization |
| S5 | Native/headless and graphics build checks through CLI | Game-artifact workflow depends on S1b/S4b |
| S6 | Qt controls for the synthetic host demo | Game-oriented progress and launch await integration |
| S7 | T7 branch/report inspection started, see [handoff](S7-integration.md) | Headless execution needs S1b, game graphics need S4b |
| S8 | Not started | Packaging and distribution acceptance |

S1a and S4a are subdivisions, not replacements for the original S1/S4 objectives.
A passing native fixture or triangle does not close S1b/S4b.

## Partie lisible par les humains

### Responsabilité

Swann est responsable de la plateforme Linux, du host POSIX, du rendu Vulkan, du
frontend Qt, de la CLI, des tests d’intégration et de la distribution.

### Travail à réaliser

1. Créer un backend Linux indépendant du backend Apple.
2. Porter le host vers POSIX et ELF.
3. Remplacer les appels Objective-C, AppKit, Metal et frameworks Apple.
4. Ajouter SDL pour fenêtre, clavier et manettes.
5. Implémenter le backend Vulkan Linux.
6. Ajouter les chemins XDG pour logs, cache et configuration.
7. Adapter `frontend-qt` avec une cible `linux-x86_64`.
8. Ajouter les commandes `doctor`, `build`, `run` et `verify`.
9. Ajouter la CI Linux et le packaging tarball/AppImage.
10. Tester le premier jeu livré par Téo.

### Ordre des livraisons

- Jalon S1 : host Linux headless
- Jalon S2 : fenêtre SDL et input
- Jalon S3 : triangle Vulkan avec lavapipe
- Jalon S4 : backend Vulkan connecté au host
- Jalon S5 : CLI Linux
- Jalon S6 : frontend Qt Linux
- Jalon S7 : premier jeu intégré
- Jalon S8 : packaging et release

### S6 implementation status

The Qt interface now drives the S5 Linux host demo workflow. See
[S6 validation](S6-validation.md) for scope, test results and reproduction commands.
Game-oriented AOT/runtime and graphics integration remains pending for S7.

### Critères de réussite

- compilation sans Xcode, Metal ou Objective-C
- exécutable ELF x86_64 avec RPATH propre
- fenêtre Vulkan fonctionnelle
- clavier et manette détectés
- logs exportables et diagnostics complets
- arrêt propre du processus et reprise des builds
- installation depuis un environnement Linux vierge

## Partie destinée à l’IA

### Instructions de travail

- ne pas ajouter de conditions Linux dans les fichiers Apple quand un backend séparé
  est possible
- conserver le contrat de logs et de progression du frontend Qt
- vérifier les extensions Vulkan au démarrage
- tester d’abord avec Mesa lavapipe
- utiliser `$XDG_STATE_HOME`, `$XDG_CACHE_HOME` et `$XDG_CONFIG_HOME` avec fallback
- ne pas supposer la présence de X11, préférer SDL et supporter Wayland
- ne pas considérer `dm/gfx/vk` comme un backend Vulkan Linux existant
- le code Metal actuel sert de référence comportementale uniquement
- ne jamais embarquer de données de jeu dans les tests ou les packages

### Fichiers prioritaires

```text
engine/dm/host/Makefile
engine/dm/host/main.c
engine/dm/host/front_mac.m
engine/dm/host/loader.c
engine/dm/host/thread.c
engine/dm/host/hle_*.c
engine/dm/gfx/nvn/
engine/dm/gfx/vk/
frontend-qt/nspb_qt/model.py
frontend-qt/nspb_qt/platform_util.py
frontend-qt/nspb_qt/engine.py
frontend-qt/nspb_qt/ui.py
```

### Tests attendus

- build CMake ou Meson Debug et Release
- tests POSIX du host
- tests ELF et `dlopen`
- tests SDL avec backend virtuel
- triangle Vulkan sous lavapipe
- tests shaders SPIR-V
- tests de fenêtre, input et resize
- tests CLI et chemins XDG
- smoke test complet avec l’artefact AOT de Téo
- packaging reproductible x86_64

### Contrat avec Téo

Swann fournit au runtime :

- native platform facilities, window/input and the graphics backend
- an adapter to the agreed runtime callbacks and lifecycle
- process logs and integration tests

Téo fournit à Swann :

- un artefact AOT x86_64
- les blocs JIT non résolus
- les symboles ou tables nécessaires au loader
- les exigences de mémoire et TLS
- les tests de conformité AOT/JIT
- the versioned artifact manifest and entry/callout contract
- guest memory, CPU state, SVC and exception semantics

### Règle de décision

Ne pas commencer par une interface graphique complète. Valider d’abord host headless,
loader, Vulkan minimal et CLI. Le frontend Qt ne doit intégrer le runtime qu’après
stabilisation des contrats d’artefacts et de logs.
