# Solutions pour NX2Linux x86_64

> **Status note (2026-11-04).** This is the plan written before the work. The decisions taken since are in `engine/docs/linux/architecture.md`: the host stays Python with C libraries (not a C `dm/host/linux/`), `tools/setup_toolchain.sh` is the official toolchain route, and `engine/` is synchronised, not tracked.

## Décision recommandée

Retenir une architecture à frontend Qt partagé et backend Linux natif. Le frontend
actuel est réutilisable, la réécriture ARM64 Python est partiellement réutilisable,
mais le host actuel doit être porté vers POSIX, ELF et Vulkan.

## Point critique : architecture du code du jeu

Le pipeline actuel ne « décompile » pas le jeu en source C/C++ puis ne le recompile
pas pour Apple. Il extrait les modules NSO, les analyse et applique des réécritures
ponctuelles sur du code machine ARM64. Le host et les couches système sont ensuite
compilés pour Apple Silicon, qui exécute naturellement ce code ARM64.

Sur Linux x86_64, le code du jeu resterait ARM64. Compiler le host en x86_64 ne
suffit donc pas : le processus contiendrait un host x86_64 et du code invité ARM64.
La cible Linux x86_64 exige une couche d’exécution supplémentaire.

Les solutions sont :

| Solution | Faisabilité | Commentaire |
| --- | --- | --- |
| Traduction dynamique ARM64 vers x86_64 | réaliste mais très complexe | JIT ou DBT, gestion du code généré, mémoire, exceptions et synchronisation |
| Recompilation statique complète | très risquée | pointeurs de fonctions, code généré, self-modifying code, exceptions et appels système rendent le résultat fragile |
| Émulation complète ARM64 | réaliste mais coûteuse | plus simple conceptuellement, probablement plus lente |
| Cible Linux ARM64 | la plus simple | le code réécrit peut rester ARM64 et le host est compilé pour la même ISA |

Pour Linux x86_64, il faut donc décider entre intégrer un traducteur dynamique ARM64
existant, en développer un, ou utiliser une émulation. Ce choix doit précéder le
port complet du renderer Vulkan. Si l’objectif x86_64 est impératif, la première
étape est un prototype qui exécute une petite fixture ARM64 et mesure les appels
SVC, les accès mémoire, les threads et les transitions vers le host.

La cible Linux ARM64 doit être ajoutée comme alternative stratégique. Elle permettrait
de valider extraction, réécriture, loader ELF, host POSIX et Vulkan sans résoudre
d’abord la traduction d’ISA. Elle ne remplace toutefois pas l’objectif x86_64.

### Architecture recommandée : recompilation statique avec JIT de secours

Pour l’objectif de performance, la meilleure cible n’est pas un émulateur ARM64
généraliste. Il faut produire un binaire ou une image de code x86_64 à partir des
modules ARM64, avec une traduction dynamique limitée aux cas que l’analyse statique
ne peut pas prouver.

```text
NSO ARM64
  -> analyse ELF/NSO et découpage des fonctions
  -> traduction AOT ARM64 -> IR -> x86_64
  -> résolution des branches et appels connus
  -> code x86_64 natif dans le bundle
  -> JIT fallback pour blocs dynamiques ou inconnus
  -> callouts host pour SVC et services Horizon
```

La recompilation doit conserver un état invité explicite : registres ARM64,
NZCV, mémoire virtuelle, TLS, conventions d’appel et accès aux périphériques
simulés. Le host x86_64 ne doit pas être confondu avec un émulateur instruction par
instruction : les services Horizon et NVN sont implémentés nativement par le host,
et seules les portions de code invité non traduites passent par le JIT.

Le JIT de secours est nécessaire pour les fonctions générées, les pointeurs de
fonctions non résolus, les trampolines, le code auto-modifiant et les chemins que le
désassembleur ne peut pas borner. Il doit être instrumenté afin de convertir ensuite
les chemins chauds en code AOT et de mesurer le taux réel de fallback.

### Preuve de faisabilité obligatoire

Avant de porter tout le renderer, construire un prototype AOT/JIT avec :

- une fixture ARM64 contenant arithmétique, branches, appels, accès mémoire et
  opérations atomiques
- traduction vers une IR simple puis x86_64 via LLVM ou un backend maison minimal
- comparaison instruction par instruction avec un oracle ARM64 sur des traces courtes
- support des branches indirectes et d’un bloc généré dynamiquement via JIT
- interception d’un appel SVC synthétique vers un service host
- mesure du ratio AOT/JIT et du coût des transitions

La preuve est positive si la fixture conserve les résultats, les exceptions et les
effets mémoire, avec un fallback borné et mesurable. Sans cette preuve, une réécriture
complète du host serait prématurée.

### Réutiliser les briques existantes

Le prototype ne doit pas créer un compilateur ARM64 complet depuis zéro. Remill peut
lever du code AArch64 vers LLVM IR, LLVM peut générer le x86_64, et Capstone ainsi
que les analyseurs NSO existants fournissent les frontières et métadonnées Switch.
QEMU TCG peut servir d’oracle et de fallback de comparaison.

Remill ne comprend pas automatiquement les NSO, les SVC Horizon, les modules
dynamiques ou les profils de jeu. QEMU TCG reste une traduction dynamique générique,
pas la cible de performance finale. NX2Linux doit fournir l’état mémoire invité, les
callouts Horizon/NVN et les règles de promotion JIT vers AOT. La pipeline finale est
donc AOT pour les fonctions prouvées, JIT existant pour les blocs non résolus et
promotion des chemins chauds vers l’AOT.

Remill documente la levée AArch64 vers LLVM IR [dans son projet officiel](https://github.com/lifting-bits/remill/blob/master/README.md),
et QEMU documente son mode de traduction utilisateur [dans sa documentation officielle](https://www.qemu.org/docs/master/user/).
Il ne faut pas transformer les chemins IPA/Mac app en pseudo-chemins Linux avec des
conditions partout.

## Options étudiées

### A. Frontend Qt seulement

Réutiliser `frontend-qt` et appeler un moteur Linux externe. C’est rapide pour un
prototype UI, mais aucun moteur Linux n’est fourni par ce dépôt. Cette option ne
produit pas NX2Linux.

### B. Ajouter des branches Linux aux scripts Apple

Conserver `build_ipa_from_nsp.py`, Mach-O et Metal avec des `if Linux`. Cette option
mélangerait des contrats incompatibles, casserait probablement Apple et rendrait le
build difficile à tester. Elle est déconseillée.

### C. Backend Linux dédié et frontend Qt partagé

Créer un contrat de build commun, un host POSIX, un loader ELF, un renderer Vulkan et
un packaging Linux. C’est le meilleur compromis entre réutilisation et maintenabilité.

### D. Réécriture totale

Créer un nouveau launcher et un nouveau moteur. Cette option perdrait profils, tests,
extraction, diagnostics et expérience du projet. Elle n’est justifiée que si le port
du moteur existant est techniquement impossible.

## Architecture cible

```text
PySide6 / Qt
  -> modèle partagé : catalogue, inputs, logs, settings, progression
  -> PlatformBackend
       -> LinuxBackend : ELF, Vulkan, SDL, XDG
       -> MacBackend
       -> WindowsAppleCrossBackend

Linux engine
  -> extraction et réécriture
  -> host C/C++ POSIX
  -> loader ELF + dlopen
  -> NVN translation layer vers Vulkan Linux
  -> shaders vers SPIR-V/Vulkan
  -> profils runtime et métadonnées
```

Le modèle Qt doit manipuler `BuildRequest`, `BuildStage`, `BuildArtifact`,
`LaunchRequest`, `DependencyReport` et `LogEvent`, sans connaître IPA, Xcode ou Metal.

## Plan d’implémentation

### 1. Préparer le fork

- renommer les identifiants et textes visibles en NX2Linux
- conserver licence et historique
- ajouter une documentation Linux dédiée
- séparer patches communs, Apple et Linux
- ne jamais versionner clés, dumps ou données de jeu

Les patches Apple, iOS, Metal, Mach-O, IPA et frameworks doivent rester hors du
chemin Linux. Les patches runtime, profils et HLE doivent être audités un par un.

### 2. Isoler le backend de build

Ajouter un `PlatformBackend` dans `frontend-qt/nspb_qt`. Déplacer dans ce contrat :

- détection d’OS et d’architecture
- chemins de cache et logs
- découverte Python, .NET, Clang, Vulkan et SDL
- commande de build et arrêt du groupe de processus
- mapping des étapes et classification des erreurs

`platform_util.py` doit distinguer explicitement Linux, macOS et Windows. Les logs
Linux doivent utiliser `$XDG_STATE_HOME/nx2linux/logs` avec fallback documenté.

### 3. Stabiliser la chaîne existante de décompilation et réécriture

Ne pas réécrire `dm/tools/patch_nso.py` en C++ au début. Il faut d’abord isoler et
tester sous Linux les composants existants : `nx_extract`, `nso.py`, `nso_code.py`,
`nso_asm.py`, `nso2elf.py`, `nso_match.py` et `patch_nso.py`. La réécriture utilise
Capstone 5 et les profils de `dm/games`. Elle conserve du code ARM64 invité, elle ne
le compile pas en x86_64.

Livrable : un test Linux qui prend une fixture NSO synthétique, produit le même
résultat de réécriture que macOS et vérifie les fichiers `.cuts.tsv`, `.match.tsv`
et les hashes. Les fichiers de jeu réels restent hors du dépôt.

### 4. Faire fonctionner un build headless

Avant le rendu, porter extraction, décompression, réécriture NSO, profils et outils
C# vers Linux. Le produit intermédiaire peut être :

```text
game/<title-id>/
  bin/nx2linux-host
  game.manifest.json
  profile.tsv
  romfs.img
  shader/
  pipelines/
  saves/
```

Cette étape valide les données et le packaging sans confondre les erreurs graphiques
avec les erreurs d’extraction.

### 5. Remplacer Mach-O par ELF

Créer un loader Linux dédié. Remplacer `mach_header`, sections custom Mach-O,
frameworks et `@rpath` par ELF, `dlopen`, `dlsym`, `dladdr`, `$ORIGIN` et RPATH.

Pour le premier port, il est préférable de placer modules, profils et tables dans
des fichiers de bundle décrits par un manifest versionné, plutôt que d’injecter
immédiatement des sections ELF custom. Le loader vérifie tailles et hashes avant le
chargement. Une optimisation ELF par sections custom pourra venir ensuite.

### 6. Porter le host POSIX

Créer un host Linux séparé fournissant :

- fenêtre et surface Vulkan
- horloge monotone, threads, mutex, mmap et fichiers POSIX
- clavier, souris et manettes avec SDL3 ou SDL2
- audio avec SDL audio ou PipeWire
- resize, fullscreen, DPI et multi-écran
- logs et crash reports Linux

SDL3 est recommandé pour réduire la surface X11/Wayland. Le renderer doit accéder à
Vulkan directement.

### 7. Implémenter le rendu Linux

La solution Metal existante ne peut pas être exécutée nativement sous Linux. Sa
conception et ses invariants peuvent être réutilisés, mais il faut remplacer son
implémentation par Vulkan ou une autre API Linux. Metal ne peut pas être remplacé par
une simple compilation croisée. Il faut isoler
une interface graphique pour textures, buffers, samplers, descriptors, render passes,
synchronisation, pipeline cache, timestamps et frame pacing.

La cible recommandée est Vulkan 1.3 avec validation des extensions au démarrage.
OpenGL peut rester un fallback expérimental, mais Vulkan est préférable pour les
shaders et la fidélité du backend NVN.

Chaîne cible :

```text
shader du jeu -> représentation intermédiaire -> SPIR-V -> Vulkan
```

La chaîne shader doit recevoir des tests de compilation, reflection et exécution
minimale sous Mesa lavapipe avant tout test de jeu.

### 8. Ajouter un build system Linux

Ajouter CMake ou Meson avec Ninja, Clang ou GCC, `pkg-config`, headers et loader
Vulkan, SDL, zstd, lz4 et le .NET SDK requis. Le build produit un ELF PIE, des
symboles séparés en Debug, un RPATH `$ORIGIN` et un manifest versionné.

Le build Apple reste inchangé. Il faut éviter un unique graphe de build rempli de
conditions de plateforme.

### 9. Adapter Qt

Conserver bibliothèque, classification des title IDs, staging, i18n, progression,
logs et export. Remplacer :

- cible `ios` ou `mac-app` par `linux-x86_64`
- `build_ipa_from_nsp.py` par un dispatcher neutre ou `build_linux_from_nsp.py`
- Xcode, Metal et iOS SDK par Vulkan, SDL, Clang et .NET
- `.ipa` et `.app` par un bundle Linux
- installation iOS par lancement local et fichier desktop optionnel

Ajouter des tests Linux pour XDG, permissions, hard links, arrêt de processus et
découverte Vulkan.

### 10. Ajouter une CLI headless

Le frontend ne doit pas être le seul point d’entrée :

```text
nx2linux doctor
nx2linux build --base game.nsp --update update.nsp --out build/
nx2linux run --title-id 0100...
nx2linux verify --build build/
```

La CLI et Qt doivent partager la même bibliothèque de services. Cela simplifie CI,
diagnostics et reproduction des bugs.

## Patches et releases

Étendre `tools/engine.py` avec un profil de plateforme ou des séries distinctes.
Chaque patch Linux doit garder `Upstream-Status`, `Reason` et `Issue`. Les manifests
doivent porter un `engine-id` propre au pin, à la série et au backend.

La première distribution peut être un tarball signé ou AppImage. Flatpak viendra
ensuite, après clarification des permissions Vulkan, jeux et manettes. Le bundle doit
contenir l’exécutable, l’engine Linux correspondant, le manifest, licence et notices.

## CI Linux x86_64

Ajouter les jobs suivants :

1. lint Python et tests Qt
2. `tools/engine.py check` avec engine synchronisé
3. build CMake ou Meson Debug et Release
4. tests loader ELF et manifest
5. smoke test Vulkan avec Mesa lavapipe
6. test SDL en environnement virtuel
7. build sur fixture synthétique
8. packaging et vérification de signature
9. contrôle de reproductibilité du bundle

Après le MVP, tester Mesa llvmpipe, Intel, AMD et NVIDIA. Les tests de jeux restent
externes et documentés par titre ID et version, sans ajout de données protégées.

## Jalons

### MVP technique

Frontend Qt Linux, commande `doctor`, dépendances détectées, extraction fonctionnelle,
host ELF minimal, fenêtre Vulkan de test et logs propres.

### Prototype jouable

Loader ELF, un jeu simple validé de bout en bout, clavier/manette, saves, cache shader
et bundle Linux reproductible.

### Version publique

Plusieurs jeux validés, profils Linux séparés si nécessaire, diagnostics exportables,
mises à jour signées et matrice matérielle documentée.

## Conclusion

La voie propre et efficace est donc : frontend Qt conservé, backend Linux explicite,
engine POSIX/ELF, renderer Vulkan, CLI headless et packaging natif. L’ordre optimal
est backend de build, headless, loader ELF, host POSIX, Vulkan, intégration Qt, puis
validation jeu par jeu. Cette séquence limite les branches mortes et rend chaque
couche testable indépendamment.
