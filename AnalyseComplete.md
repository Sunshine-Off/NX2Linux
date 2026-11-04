# Analyse complète de NX2Apple

> **Status note (2026-11-04).** This analysis dates from 2026-10-31 and describes the repository before the Linux work: 36 patches, no recompiler, no Linux path. Today there are 89 patches, an ARM64 to x86_64 recompiler (Remill and LLVM with a dynarmic fallback) and a Vulkan renderer. For the current state read `engine/docs/linux/architecture.md` (after `python3 tools/engine.py sync`) and `RESTE_A_FAIRE.md`.

## Périmètre et méthode

Analyse réalisée sur l’état du dépôt au 31 octobre 2026 et sur le commit upstream
épinglé dans `.upstream-ref` : `c5fe6b5dbcb245f9116b1b380cc71ecd4c6c4f11`.

Le moteur upstream n’est pas versionné ici. Il est normalement créé dans `engine/`
par `python3 tools/engine.py sync`. Cette synchronisation a été tentée, mais
l’environnement ne résout pas `github.com`. L’analyse du moteur s’appuie donc sur
les contrats exposés par les frontends, les outils, la documentation et les 36
patches présents. Une analyse du code moteur ligne par ligne nécessitera un checkout
upstream synchronisé.

Vérifications effectuées :

- inventaire des dossiers et fichiers, recherche des dépendances de plateforme
- `python3 -m unittest discover -s frontend-qt/tests` : premier passage réussi avant
  la synchronisation de l’engine, puis un rerun détaillé termine par `SIGABRT` après
  les trois tests `test_ascii_temp` dans cet environnement
- `python3 -m unittest discover tools/tests` : 37 tests, 2 erreurs car `engine/`
  est absent
- `python3 tools/engine.py sync` : échec réseau sans modification du dépôt

## Vue d’ensemble

NX2Apple est un launcher et une chaîne de préparation de jeux Nintendo Switch basée
sur `seedds/nxnative`. Il orchestre lecture de NSP/XCI/NSZ, extraction, réécriture
des exécutables, compilation du host, traduction des shaders et création d’un
package Apple. Le jeu s’exécute nativement sur la cible, ce n’est pas un simple
frontend de téléchargement.

| Couche | Rôle actuel | Portabilité Linux |
| --- | --- | --- |
| Moteur `nxnative` | loader, Horizon host, NVN, profils et shaders | faible à moyenne, port graphique et binaire important |
| `frontend/` | launcher SwiftUI natif Mac | nulle en l’état |
| `frontend-qt/` | frontend Python/PySide6 déjà abstrait autour du build | meilleure base de portage |
| `frontend-ios/` | shell SwiftUI iPhone/iPad | nulle pour Linux desktop |
| `tools/` | synchronisation, patches et releases | largement portable |
| `patches/` | commits appliqués à l’engine pinned | à auditer par plateforme |

## Inventaire des dossiers

| Dossier | Responsabilité | Fichiers observés |
| --- | --- | ---: |
| `frontend/` | application Mac SwiftUI, ressources, screenshots et tests | 157 |
| `frontend/Sources/NSPortBuilder` | launcher, onboarding, bibliothèque, build, logs, saves et updates | 69 |
| `frontend/Tests` | tests unitaires et fixtures Mac | 63 |
| `frontend-ios/` | package Swift, démo et UI tests du shell iOS | 91 |
| `frontend-ios/Sources` | `ShellHost`, `ShellUI`, `ShellControllers` et demo | 38 |
| `frontend-ios/Tests` et `UITests` | catalogue, lancement, focus, layouts et mapping | 38 |
| `frontend-qt/` | frontend Python/PySide6 et packaging | 65 |
| `frontend-qt/nspb_qt` | modèle, UI, dépendances, staging, engine et i18n | 40 |
| `frontend-qt/nspb_qt/win` | LLVM, SDK iOS, shims Xcode, Mach-O et IPA | 17 |
| `frontend-qt/tests` | tests Python du frontend | 20 |
| `tools/` | `engine.py`, `patches.py`, manifest et `try-pr` | 17 |
| `tools/tests` | tests du patch queue et releases | 6 |
| `patches/` | série de 36 patches et README | 38 |
| `gen/` | profil généré d’un jeu | 1 |
| `probe-out/` | sortie IPA de probe, artefact et non source | 1 |
| `.github/` | CI, triage, forms et templates | 26 |
| `.claude/` | hook de synchronisation de l’engine | 1 |

`engine/` est absent conformément à `.gitignore`. Les chemins `dm/`, `scripts/`,
`docs/` et `ref/` sont des sources générées depuis upstream et ne doivent jamais
être ajoutés à ce dépôt.

## Analyse des composants

### Documentation

`README.md`, `BUILDING.md`, `GAMES.md`, `CONTRIBUTING.md` et `RELEASING.md` décrivent
un produit Apple : macOS, Apple Silicon, Xcode, iOS, IPA, Metal et signatures Apple.
`ENTITLEMENTS.md` est entièrement iOS. `frontend/README.md` contient cependant un
bon contrat fonctionnel réutilisable : bibliothèque, options par jeu, logs,
dépendances, contrôleurs et saves. `frontend-qt/README.md` confirme que Qt est déjà
la seconde interface et constitue le meilleur point de départ pour Linux.

### Frontend Mac SwiftUI

`frontend/Package.swift` cible `.macOS(.v13)`. Les sources utilisent SwiftUI, AppKit,
CoreText, Process, pipes, bookmarks, dossiers Library, Xcode, SDK iOS, XcodeGen,
Metal et bundles `.app`. Les fonctions sont bien découpées entre engine, build,
library, saves, diagnostics, contrôleurs et vues, mais les APIs sont Apple.

Ce code doit rester le frontend Apple. Le porter sous Linux créerait un second port
UI coûteux et ne résoudrait pas le problème principal du moteur.

### Shell iOS

`frontend-ios` sépare `ShellHost` pour catalogue, manifestes et lancement,
`ShellUI` pour la bibliothèque et le focus, et `ShellControllers` pour les manettes.
Les fixtures contiennent `DmGame.framework`. Les modèles et tests peuvent inspirer
un catalogue Qt, mais SwiftUI, bookmarks et frameworks sont strictement Apple.

### Frontend Qt

`run_gui.py` lance `nspb_qt.__main__`. La séparation `ui.py` / `model.py` est saine.
Les modules directement réutilisables sont :

- `inputs.py` pour title IDs, versions, base, updates et DLC
- `input_staging.py` pour hard links et fallback en copie
- `build_failure.py` pour erreurs et override contrôlé
- `engine.py` pour stamp, stale engine et sync
- `i18n.py`, `localization.py`, `style.py` pour langue et style
- `model.py` pour threads, signaux, logs, dépendances et progression
- `ui.py` pour cartes, fichiers, build, logs et export

Les blocages sont explicites : `platform_util.py` ne distingue que Windows et
macOS, la branche non-Windows vérifie Xcode, Metal, XcodeGen et iOS SDK, le modèle
lance `build_ipa_from_nsp.py`, les chemins de logs utilisent `~/Library/Logs`, et
`win/` ne contient que le cross-build Apple pour Windows.

### Tools et patch queue

`tools/engine.py` reconstruit `engine/` depuis le commit upstream et `patches/series`,
gère stamp, export, bump, check et arbre distribuable. `tools/patches.py` valide les
headers, les fichiers générés et interdit de toucher la chaîne shader sensible.
`release_manifest.py` publie un zip engine avec manifest et signature Ed25519.

Ces outils sont une bonne base pour Linux, mais il faudra des artefacts Linux x86_64,
des manifests propres au backend et une politique de patches séparant logique
commune, Apple et Linux.

### Patches

Les 36 patches mélangent runtime et profils de jeux, corrections HLE, comportement
Mac/iOS, Metal, fonts, Mach-O, IPA, frameworks et shell iOS. Les patches de profils,
loader ou runtime peuvent parfois être communs. Les patches `ios-*`, `mac-*`,
`route-c-*`, framework, Metal et IPA ne doivent pas être appliqués à Linux sans audit.

### CI et qualité

La CI couvre surtout macOS/iOS et le frontend Qt Windows. `pr-scope.yml` vérifie que
l’engine n’est pas copié dans le dépôt et que la série s’applique au pin. Il n’existe
pas de job Linux pour le frontend, le moteur, Vulkan ou l’exécution graphique.

Les tests Qt passent. Les tests tools nécessitent un `engine/` synchronisé. Les tests
Swift ne sont pas portables.

## Dépendances et risques

Les briques potentiellement portables sont Python, PySide6, Git, .NET, LLVM/Clang,
zstd, lz4, capstone, fonttools, nsz et les outils SPIR-V.

Les dépendances à retirer du chemin Linux sont Xcode, iOS SDK, XcodeGen, `xcodebuild`,
`xcrun`, Metal, MSL, Mach-O, `.app`, `.framework`, IPA, provisioning, entitlements,
AppKit, SwiftUI et iCloud.

Les vrais risques sont le port du host, le loader Mach-O vers ELF, la transformation
des shaders Metal vers Vulkan, la gestion des modules et la compatibilité jeu par jeu.
Le frontend est secondaire par rapport à ces travaux.

## Analyse du moteur synchronisé

Le moteur est maintenant présent dans `engine/` et son stamp est à jour. Il contient
notamment environ 156 fichiers sous `dm/tools`, 127 sous `dm/host`, 323 sous `dm/gfx`,
60 sous `dm/ios`, 1 080 fichiers de profils sous `dm/games` et 640 fichiers tiers.

Le terme « décompilateur » est approximatif. La chaîne réelle est composée de :

- `dm/tools/nx_extract/` en C# et .NET pour NSP/XCI/NSZ, ExeFS et RomFS
- `dm/tools/nso.py`, `nso_code.py`, `nso_asm.py` et `nso2elf.py` pour lecture,
  désassemblage, analyse et conversion NSO/ELF
- `dm/tools/patch_nso.py` pour la réécriture des modules ARM64
- `dm/tools/nso_match.py` pour les correspondances de fonctions entre builds
- `dm/tools/sdk_patches.py` et `dm/games/<title-id>/patches.py` pour les corrections
  spécifiques à un build ou à un SDK
- `scripts/build_ipa_from_nsp.py` pour l’orchestration complète et le cache des étapes

La réécriture elle-même est largement Python et dépend de Capstone 5. Elle est donc
une candidate réaliste à la réutilisation Linux. Elle réécrit le code ARM64 du jeu,
mais ne transforme pas le jeu en code x86_64 : le code invité reste ARM64 et doit
être exécuté ou traduit par le runtime prévu par le moteur. Cette distinction est
un risque majeur du port Linux x86_64.

Le `dm/host/Makefile` confirme que le host actuel est Apple-only : `clang` avec
`-mmacosx-version-min`, Objective-C `.m`, frameworks Cocoa, QuartzCore, Metal,
MetalFX, GameController, CoreHaptics, IOKit, AudioToolbox, VideoToolbox et CoreMedia.
Les fichiers `front_mac.m`, `memstat.m`, `motion_apple`, `pad_apple` et
`haptics_apple` devront avoir des implémentations Linux séparées.

Le moteur contient déjà des sources `dm/gfx/vk` et `dm/gfx/gl`, mais elles sont
actuellement incluses dans le host Metal Apple. Leur présence ne signifie pas qu’un
renderer Vulkan Linux existe. Le port devra remplacer les dépendances Metal et la
compilation MSL par une vraie surface Vulkan Linux et une production SPIR-V.

## Conclusion

NX2Linux ne doit pas être obtenu par un simple `if sys.platform == "linux"`. Il faut
conserver le frontend Qt et la chaîne de données, isoler les contrats de plateforme,
porter host et loader vers POSIX/ELF, implémenter Vulkan, produire un bundle Linux et
revalider les jeux. Le premier jalon raisonnable est un build headless puis un host
Vulkan minimal.
