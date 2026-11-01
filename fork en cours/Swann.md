# NX2Linux — Partie Swann

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

- le manifest de modules
- l’adresse d’entrée recompilée
- l’état mémoire invité
- les callouts SVC
- les événements de chargement
- les logs de traduction et de fallback

Téo fournit à Swann :

- un artefact AOT x86_64
- les blocs JIT non résolus
- les symboles ou tables nécessaires au loader
- les exigences de mémoire et TLS
- les tests de conformité AOT/JIT

### Règle de décision

Ne pas commencer par une interface graphique complète. Valider d’abord host headless,
loader, Vulkan minimal et CLI. Le frontend Qt ne doit intégrer le runtime qu’après
stabilisation des contrats d’artefacts et de logs.
