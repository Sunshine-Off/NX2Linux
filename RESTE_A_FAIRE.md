# NX2Linux — état des lieux et reste à faire (audit du 2026-11-04)

Audit en lecture seule : aucun fichier suivi modifié, aucun commit, aucune installation. Convention : **[vérifié]** = commande, fichier ou ligne à l'appui. **[déduit]** = raisonnement à partir du code ou des notes. **[inconnu]** = il faut un jeu ou une machine que je n'ai pas.

## 1. Résumé

1. Les documents racine sont **en retard** : `AnalyseComplete.md` parle de 36 patches et d'un moteur absent, alors qu'il y a 85 patches, un recompilateur ARM64→x86_64 (Remill/LLVM + repli dynarmic) et un rendu Vulkan qui fonctionnent [vérifié].
2. **Xenoblade Chronicles 2 tourne** jusqu'au premier combat, avec rendu GPU, entrées, audio mixé, sauvegardes à journal et films VP9 décodés. Les notes `S7bis-cloture.md` et `S9-rapport-664-fonctions.md` donnent les mesures. Je n'ai pas pu le relancer (pas de données de jeu) : **à vérifier avec un jeu**.
3. Les phases 2 bis, 4 et 5 sont donc faites pour l'essentiel, mais **pas telles que le plan les décrit**. Le « host » est un script Python (`horizon_run.py`) avec des bibliothèques C, pas un `dm/host/linux/` en C.
4. **Bloquant n°1 : il n'y a pas de parcours utilisateur.** Extraire, convertir, recompiler et lancer sont 4 commandes manuelles avec des variables d'environnement. Le frontend Qt Linux ne pilote que la démo du triangle Vulkan [vérifié : `linux_backend.py:50`, `linux/cli/nx2linux.py:259-299`].
5. **Bloquant n°2 : la distribution.** Aucune CI Linux, aucun paquet, aucun manifeste Linux. Le build demande Remill patché, LLVM 22, dynarmic, .NET et libvpx, et la recompilation d'un jeu coûte ~25 min et ~12 Go. Le résultat ne peut pas être distribué (code dérivé du jeu) [vérifié pour les chiffres, déduit pour le droit].
6. **Bloquant n°3 : la fluidité.** Environ 12-15 images/s mesurées contre 30 visées. Le goulot est le pont Python sous GIL (`S8` §3, §9) ; l'étape 3 du plan de `S8` (appels de synchro en C) n'est pas faite [vérifié dans les notes].
7. **Un seul jeu est validé.** Balatro (le « jeu simple » du plan) est recompilé mais ne s'affiche pas : la couche EGL/GL sur Vulkan n'est pas branchée à ses imports (`S7-balatro.md`, `linux/host/README.md`).
8. **Point de décision après la Phase 1 : choisir le traducteur ARM64 en espace utilisateur (AOT Remill + repli JIT dynarmic).** Il est déjà démontré sur un gros jeu (103 665 fonctions, 0 non traduite, un combat atteint). Une cible Linux ARM64 n'apporterait rien de plus, et « préparation seule » ne tient plus. Réserves : un seul jeu, performance à moitié de la cible, repli JIT jamais exercé sur le jeu réel (`S9` §5).
9. `gh` est installé et connecté (compte `shikosu`, droits admin sur `Sunshine-Off/NX2Linux`), mais les **issues sont désactivées** sur ce dépôt (`has_issues: false`). Je ne change pas les réglages : voir §5 et §6.

## 2. Avancement par phase

| Phase | État | Preuve |
|---|---|---|
| 0 Baseline et découpage | **Partiel** | `engine.py check` : « 85 patches apply cleanly » [vérifié]. Tests Qt 234 OK, tools 124 OK (22 ignorés), CLI 17 OK, `compileall` rc=0, localisation inchangée [vérifié]. Absents : `docs/linux/architecture.md` [vérifié : `engine/docs/linux` n'existe pas], inventaire des API Apple, CI Linux (aucun workflow ne construit `linux/host` ni ne lance les tests Linux du moteur) [vérifié : `grep` des workflows]. |
| 1 Extraction et réécriture | **Partiel** | `nx_extract` se construit sous Linux (0 erreur, 3 s) [vérifié]. `test_nso` 12 OK, `test_nso_match` 25 OK. **Absent** : fixture NSP/XCI synthétique avec test d'extraction (les tests `PFS0` ne couvrent que le scan/nsz), tests d'échec Capstone/architecture, dispatcher de cible dans `build_ipa_from_nsp.py` (plateformes `ios`/`mac`/`mac-app` seulement, ligne 49). Le chemin Linux n'appelle pas `patch_nso.py` [déduit : `elf_module.py` lit le NSO directement]. |
| 2 Loader ELF | **Partiel, autre architecture** | `elf_module.py` (Python/ctypes + objets générés) et manifeste JSON versionné (T6) : `test_elf_module` 23 OK [vérifié]. Pas de `loader_linux.c`, **aucun test ASan/UBSan** [vérifié : `grep -rn fsanitize` ne trouve rien]. |
| 2 bis Preuve AOT/JIT | **Fait, sauf le repli sur le jeu réel** | Tests moteur AOT : atomics, cache, jit, mémoire, realistic, simd (14 tests, 79 s), stub, svc, sysregs, t3 OK [vérifié]. Mesures sur le jeu : `S9` §6-7 (27 fonctions crypto, 0 non traduite) [lu, non rejoué]. 1 échec périmé (`test_aot_tailcall`) : voir P0-d. |
| 3 Host POSIX | **Partiel** | Host = Python + `libnx2linux_{display,gpu,audio,movie}.so` [vérifié : `~/.cache/nx2linux/build-host`]. `engine/dm/host/linux/` n'existe pas, `Makefile` reste Apple. Threads, fibres, SVC, fs, hid, audio, temps portés (tests `horizon_*` OK). Manque : performance, 244 fonctions « pas portées », crash reports [déduit]. |
| 4 Renderer Vulkan | **Fait en grande partie** | NVN→SPIR-V (traducteur Ryujinx par dotnet) + `gpu.c`. 0 dessin sauté, 151 avec texture de remplacement (`S9` §8) [lu]. `test_horizon_gpu` 32 OK. Manque : vue 2D d'image 3D, taille de point, référence d'image, couche de validation, autre GPU que NVIDIA. |
| 5 Premier jeu | **Atteint pour XC2, pas reproduit ici** | `S7bis-cloture.md` coche les 5 critères, avec ~15 images/s [lu]. Le test `smoke` à 3 lancements existe (`nx2linux.py:226`), son rapport n'est pas archivé [déduit]. **À vérifier avec un jeu.** |
| 6 Frontend Qt Linux | **Partiel (démo seulement)** | `LinuxBackend` : doctor/build/verify/run de la démo [vérifié]. Aucun parcours dépendances → choix du jeu → build → lancement. Texte périmé « Game recompilation … await the AOT/runtime integration » dans l'interface [vérifié : `linux_ui.py:26`]. |
| 7 Distribution et CI | **Absent** | Aucun tarball, AppImage, `.desktop`, Dockerfile ni manifeste Linux [vérifié : `find`]. `release_manifest.py` ne mentionne pas Linux [vérifié : `grep`]. `setup_toolchain.sh` suppose Fedora (`dnf`, `rpm2cpio`) et LLVM 22. |

## 3. Tâches restantes

Étiquettes proposées : `phase-N`, `bloquant`, `difficulté:faible|moyenne|forte`. Un identifiant `Pn-x` est donné pour les dépendances.

### Phase 0

**P0-a [Phase 0] Écrire docs/linux/architecture.md et mettre les documents à jour**
- Manque : le plan l'exige, et `AnalyseComplete.md`, `Solution.md`, `README.md`, `BUILDING.md` décrivent l'état d'avant (36 patches, aucun Linux). Une nouvelle personne se trompe d'état.
- Preuve : `AnalyseComplete.md` l.5-7 (« 36 patches »), `grep -in linux README.md BUILDING.md` vide [vérifié].
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P0-b [Phase 0] Classer les 85 patches : commun, Apple, Linux**
- Manque : la règle 4 du plan demande des séries séparées. Seuls les statuts upstream existent (11 `forwarded`, 58 `pending`, 16 `fork-only`).
- Preuve : `engine.py status` [vérifié]. Les 58 `pending` sont des patches Linux jamais transmis à seedds/nxnative.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P0-c [Phase 0] Nettoyer l'hygiène du dépôt (engine suivi, *.nsp, artefacts)**
- Manque : (1) `engine/` est suivi par le dépôt parent et son contenu diffère de l'arbre synchronisé (18 fichiers modifiés, 1 supprimé, ~20 non suivis au début de la session) ; (2) `*.nsp` n'est exclu que par `.git/info/exclude` local, donc un coéquipier pourrait committer un jeu ; (3) `probe-out/DmProbe.ipa` est suivi ; (4) un dossier parasite `C:\Windows\Temp` à la racine.
- Preuve : `git status`, `git check-ignore -v` (règle dans `.git/info/exclude:7`), `git ls-files probe-out` [vérifié].
- Dépend de : rien. Difficulté : faible. Bloquant : non, mais risque de fuite de données de jeu.

**P0-d [Phase 0] Remettre au vert les tests moteur qui échouent sous Linux**
- Manque : (1) `test_aot_tailcall` attend une `LiftError` pour une instruction que le patch Remill 0009 sait maintenant lifter (test périmé) ; (2) `test_horizon_services::test_an_audio_out_buffer_is_released_after_the_time_its_samples_take` : `0 != 256`, reproduit deux fois, probablement une régression du patch 0104 (audio mixé) ou un test lié à l'horloge ; (3) `test_docs::test_gallery_is_current` (galerie `GAMES.md`) ; (4) `test_env_switches` : 5 variables citées dans la doc et lues nulle part (`DM_LAUNCHER_JSON`, `DM_LOG_UEMALLOC`, `DM_NVN_ALIAS_TRACE`, `DM_PROF`, `DM_TOUCH_PREVIEW`) ; (5) `test_romfs_image` : `-Werror` + `unused-function` dans `romfs_image.h` avec gcc ; (6) les tests propres à l'Apple (`test_nsp_mac_app` 80 échecs, `test_host_msl`, `test_regress` qui charge `libproc.dylib`, `test_spirv_cross_pin` binaire Mach-O, `test_own_compilers` attend Xcode) doivent être ignorés sous Linux au lieu d'échouer.
- Preuve : sorties du §4 [vérifié]. Le n°2 est un vrai échec ; les autres sont de la dérive ou de l'Apple.
- Dépend de : rien. Difficulté : faible à moyenne. Bloquant : non (mais la CI Linux, P0-e, doit pouvoir être verte).

**P0-f [Phase 0] Rendre l'environnement Python des workers reproductible**
- Manque : les workers du moteur tournent en `python3 -I`, qui ignore le site utilisateur. Ici `capstone` n'est que dans `~/.local/lib/python3.14` : `test_module_validate` échoue avec `No module named 'capstone'` en Python système et passe avec le venv `~/.cache/nx2linux/venv`. `doctor` ne vérifie pas ces modules (capstone, unicorn, lz4).
- Preuve : `ModuleNotFoundError` puis `OK (2 tests)` avec le venv ; `capstone.__file__` = `~/.local/...` [vérifié].
- Dépend de : rien. Difficulté : faible. Bloquant : non (mais piège pour tout nouveau coéquipier).

**P0-e [Phase 0] Ajouter une CI Linux x86_64 minimale**
- Manque : aucun job ne fait `cmake` de `linux/host`, `ctest`, les tests Qt Linux, ni les tests moteur AOT/horizon. Le plan et `Solution.md` §CI en listent 9.
- Preuve : `grep` des workflows : seuls `ubuntu-latest` pour pr-scope/labeler/tests-qt, `xcode-27` pour le reste [vérifié].
- Dépend de : P7-c (toolchain installable). Difficulté : moyenne. Bloquant : oui pour la phase 7.

### Phase 1

**P1-a [Phase 1] Fixture NSP/XCI synthétique et test d'extraction de nx_extract**
- Manque : critère de sortie de la phase 1. Aucun test n'extrait un paquet ; seul le build de `nx_extract` est prouvé.
- Preuve : `grep -rl PFS0 engine/dm/tools/tests` ne donne que scan, volume externe et nsz [vérifié]. Un faux paquet doit être fabriqué sans clés ni données réelles.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P1-b [Phase 1] Trancher le rôle de patch_nso.py sous Linux et le tester sur une fixture**
- Manque : le plan demande « réécriture déterministe d'une fixture NSO » avec Capstone 5. La chaîne Linux réelle passe par `elf_module.py` et les remplacements HLE (`horizon_overrides.py`), pas par `patch_nso.py`. Soit on prouve que les correctifs `sdk_patches`/`games/<id>/patches.py` ne sont pas nécessaires, soit on les applique.
- Preuve : aucun appel à `patch_nso` dans `elf_module.py` ni `horizon_*.py` [vérifié par `grep`], conclusion **déduite**.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P1-c [Phase 1] Tests d'échec : mauvais Capstone, architecture non supportée, profil absent**
- Manque : critère de sortie. Le cas « profil absent » est géré (le jeu tourne en profil générique et le dit, `S9` §7) mais je n'ai pas vu de test dédié aux deux autres.
- Preuve : `grep capstone` dans les tests ne montre pas de test d'échec de version [déduit].
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P1-d [Phase 1] Prouver et tester que le chemin Linux n'appelle ni Xcode, Metal ni xcrun**
- Manque : critère de sortie. `build_ipa_from_nsp.py` contient `xcodebuild`/`xcrun`, mais le chemin Linux ne l'appelle pas. Un test l'affirmerait.
- Preuve : `grep xcrun` liste des scripts Apple dans `dm/tools`, aucun n'est importé par `elf_module`/`horizon_run` [déduit].
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P1-e [Phase 1] Un seul point d'entrée `nx2linux build` : NSP → extraction → modules ELF → manifeste → recompilation**
- Manque : aujourd'hui `extract`, puis `elf_module.py` par module (main, sdk, subsdk0), puis `horizon_run.py` avec `REMILL_BUILD`, `DYNARMIC_DIR`, cache à la main (`S7bis-reprise-swann.md` étapes 5-7). Le « répertoire de build + manifeste » de la phase 1 n'existe pas en un seul geste.
- Preuve : `nx2linux.py` ne propose que doctor/build(host)/run(démo)/verify/extract/survey/smoke [vérifié].
- Dépend de : rien. Difficulté : moyenne. Bloquant : **oui** (bloque P6-a).

### Phase 2

**P2-a [Phase 2] Tests ASan/UBSan sur le runtime AOT et les bibliothèques de l'hôte**
- Manque : critère de sortie. Le code natif (`aot_runtime.cpp`, `gpu.c`, `nvnrec.c`, `audio.c`, `movie.c`, handler de SIGSEGV de l'accès mémoire en ligne) n'a aucun test avec sanitizers.
- Preuve : aucun `fsanitize` dans `linux/` ni `dm/tools` hors `lint_c.py` [vérifié].
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P2-b [Phase 2] Erreurs de chargement déterministes, tailles, alignement, relocations**
- Manque : à confirmer cas par cas. `test_elf_module` (23 OK) couvre switch et manifeste, mais je n'ai pas vérifié les erreurs de manifeste corrompu ou de hash faux.
- Preuve : [inconnu]. Il faut lire `test_elf_module.py` ligne à ligne.
- Dépend de : rien. Difficulté : faible. Bloquant : non.

### Phase 2 bis

**P2b-a [Phase 2 bis] Exercer le repli JIT (`--jit`) sur un build complet de Xenoblade 2**
- Manque : le repli n'est prouvé que par les tests unitaires. Il change la clé de cache (tout se relifte, ~25 min) et coupe les accès mémoire en ligne.
- Preuve : `S9` §5 « Reste : une construction complète … avec `--jit` n'a pas été lancée » [lu]. À vérifier avec un jeu.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non (0 fonction non traduite aujourd'hui).

**P2b-b [Phase 2 bis] Valider les modules de XC2 contre Unicorn (`module_validate.py`)**
- Manque : Balatro a été comparé (227 fonctions identiques, 0 écart), pas XC2.
- Preuve : `S7bis-reprise-swann.md` « À faire » point 6 ; `blocages-connus.md` [lu]. À vérifier avec un jeu.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P2b-c [Phase 2 bis] Couvrir les instructions restantes que Remill ne lift pas**
- Manque : ~1 720 instructions de Balatro (`ldxp/stxp`, `cas*/swp*`, `sqxtn`, `frecpe`, demi-précision), `ccmn` registre, LSE. Elles passeraient par le JIT.
- Preuve : `blocages-connus.md` « NEON », `S9` §6 « Limites connues » [lu].
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P2b-d [Phase 2 bis] Code généré et auto-modifiant : cas réel**
- Manque : le plan exige de prouver le code dynamique. Un bloc généré via JIT est testé en fixture, jamais mesuré dans un jeu ; l'invalidation du cache JIT inter-threads est un défaut connu (H15).
- Preuve : `blocages-connus.md` n°5-6 [lu]. [inconnu] sur un jeu réel.
- Dépend de : P2b-a. Difficulté : forte. Bloquant : non.

**P2b-e [Phase 2 bis] Réduire le coût de la recompilation (12 Go, ~25 min à froid)**
- Manque : le cache objet fait ~11-12 Go par jeu et la première construction prend 1 430-1 541 s sur 16 cœurs. Sur un PC modeste, c'est un obstacle d'usage. Pistes : objets groupés par module, LTO, compression.
- Preuve : `S8` §10, `S9` §7 ; `du -sh ~/.cache/nx2linux` = 36 Go ici [vérifié].
- Dépend de : rien. Difficulté : forte. Bloquant : non.

### Phase 3

**P3-a [Phase 3] Doubler la cadence (12-15 → 30 images/s) : appels de synchro en C**
- Manque : les 5 appels noyau chauds (`WaitProcessWideKeyAtomic`, `SignalProcessWideKey`, `ArbitrateLock/Unlock`, `WaitSynchronization`) passent par Python sous GIL (~200 appels par image). Puis accès mémoire en ligne activé par défaut (+8 %).
- Preuve : `S8` §3 point 3, §9, §11 ; cloture « environ 15 images par seconde » [lu]. À remesurer avec un jeu.
- Dépend de : rien. Difficulté : forte. Bloquant : **oui** pour « jouable ».

**P3-b [Phase 3] Auditer les 244 fonctions « pas portées » par appels réels**
- Manque : elles répondent 0. Seules 2 ont été appelées sur le parcours testé (`SetNpadJoyHoldType`, `GetVibrationDeviceHandles`). Clavier à l'écran, Mii, amiibo, réseau, comptes : inconnus.
- Preuve : `S9` §1-3 [lu]. [inconnu] au-delà du parcours.
- Dépend de : un parcours long avec un jeu. Difficulté : moyenne. Bloquant : non.

**P3-c [Phase 3] Entrées complètes : manette, vibrations, tactile, mouvement**
- Manque : clavier confirmé à la main ; la manette « n'est pas citée dans le retour ». Pas de tactile ni de capteurs ; appuis courts perdus à 15 images/s (le tampon partagé ne garde que le dernier état).
- Preuve : `S7bis-cloture.md`, `S8` §12, `horizon_input.py` [lu]. **À vérifier avec un jeu et une manette.**
- Dépend de : P3-a pour les appuis courts. Difficulté : moyenne. Bloquant : non.

**P3-d [Phase 3] Essai de durée (plusieurs heures) et arrêt propre**
- Manque : le blocage « tous les threads en attente » a été corrigé (patch 0084) mais « 0 blocage sur 4 runs ne prouve pas qu'il n'en reste pas d'autre ». Aucun test de plusieurs heures.
- Preuve : `S7bis-reprise-swann.md` [lu].
- Dépend de : un jeu. Difficulté : moyenne. Bloquant : non.

**P3-e [Phase 3] Rapports de crash et journaux de l'hôte sous XDG**
- Manque : les journaux de l'interface Qt sont sous XDG ; pour l'hôte de jeu (Python), je n'ai pas trouvé de rapport de crash structuré.
- Preuve : [inconnu] (je n'ai pas lu tout `horizon_run.py` ; `engine/dm/host/crash.c` est le modèle Mac).
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P3-f [Phase 3] Décider de l'architecture de l'hôte : Python + bibliothèques C, ou hôte C (`dm/host/linux/`)**
- Manque : le plan prévoyait un hôte POSIX en C. Le code réel est un hôte Python. Cela pèse sur la performance (P3-a), la distribution (Python embarqué, `-I`) et les tests.
- Preuve : `engine/dm/host/` sans dossier `linux/` [vérifié]. Voir question Q2.
- Dépend de : décision. Difficulté : forte. Bloquant : non.

### Phase 4

**P4-a [Phase 4] Vue 2D d'une image 3D (VK_EXT_image_2d_view_of_3d)**
- Manque : 75 dessins pendant un chargement utilisent une texture de remplacement.
- Preuve : `S9` §8 reste n°1 [lu].
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P4-b [Phase 4] Taille de point et redémarrage de primitive sur listes**
- Manque : `SetPointSize` compté et non appliqué ; le redémarrage n'agit pas sur points/lignes/triangles.
- Preuve : `S9` §8 reste n°2-3 [lu].
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P4-c [Phase 4] Comparaison à une image de référence et couche de validation Vulkan**
- Manque : `framediff.py` existe mais aucune référence n'est versionnée ; la propreté sous les couches de validation n'a jamais été vérifiée (S6 le dit pour la démo).
- Preuve : `S6-validation.md` « did not validate … validation-layer cleanliness » [lu].
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P4-d [Phase 4] Cache de pipelines persistant et matrice GPU**
- Manque : le cache SPIR-V est en `~/.cache/nx2linux/spirv-debug` (« debug » dans le nom) ; je n'ai pas trouvé de cache de pipelines Vulkan. Un seul GPU est cité (NVIDIA RTX 4060 ; `S6` parle d'une 4070 : à corriger). Pas de test Intel, AMD, ni lavapipe sur un jeu.
- Preuve : `S7bis-cloture.md`, `S6-validation.md` [lu]. [inconnu] pour le cache de pipelines.
- Dépend de : rien. Difficulté : moyenne. Bloquant : non.

**P4-e [Phase 4] Traducteur de shaders sans .NET à l'exécution**
- Manque : le traducteur Maxwell→SPIR-V est un projet .NET (Ryujinx) construit dans `~/.cache/nx2linux/xlate` et lancé par dotnet à l'exécution. Un joueur sans SDK .NET ne peut pas jouer.
- Preuve : `S7bis-reprise-swann.md` « nvn_spirv.py … (dotnet, construit pour Linux dans …/xlate) » [lu].
- Dépend de : P7-c. Difficulté : moyenne. Bloquant : non pour les développeurs, oui pour la distribution.

### Phase 5

**P5-a [Phase 5] Archiver le rapport `smoke` de XC2 (3 lancements, titre, version, profil, GPU, pilote)**
- Manque : le critère de reproductibilité repose sur « une dizaine de lancements » informels.
- Preuve : `S7bis-cloture.md` ; commande `nx2linux.py smoke` [vérifié qu'elle existe]. **À vérifier avec un jeu.**
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P5-b [Phase 5] Parcours plus long de XC2 : sauvegarde, rechargement, films, ville, carte**
- Manque : aucune partie sauvegardée atteinte, aucun film joué par le jeu lui-même, aucune scène au-delà du premier combat.
- Preuve : `S9` §9-10 « Ce qui n'est pas prouvé » [lu].
- Dépend de : P3-a recommandé. Difficulté : moyenne. Bloquant : non.

**P5-c [Phase 5] Faire tourner Balatro (EGL/GL sur Vulkan)**
- Manque : `main` importe 33 symboles EGL/GL ; `gl_vulkan.c` répond au GL via Zink mais la table d'imports n'est pas branchée au runtime AOT. Balatro (SDK 18.3.2, LÖVE) est le « jeu simple » que le plan recommandait.
- Preuve : `S7-balatro.md` « Ce qui reste » n°3, `linux/host/README.md` section OpenGL on Vulkan [lu]. **À vérifier avec un jeu.**
- Dépend de : P3-f (comment brancher les imports natifs). Difficulté : forte. Bloquant : non.

**P5-d [Phase 5] Choisir et valider un deuxième jeu avec profil**
- Manque : un seul titre validé. Les 127 dossiers de `engine/dm/games` viennent d'upstream pour l'hôte Mac ; un seul a un `linux_profile.py`.
- Preuve : `ls engine/dm/games/*/linux_profile.py` [vérifié]. Voir question Q1.
- Dépend de : P1-e, P6-a. Difficulté : forte. Bloquant : non.

### Phase 6

**P6-a [Phase 6] Frontend Qt : parcours complet dépendances → choix du jeu → build → lancement → arrêt → journaux**
- Manque : `LinuxBackend.start` n'accepte que `doctor/build/verify/run` (démo). Aucune sélection de NSP, de clés, de mises à jour/DLC, aucun suivi des étapes (extraction, recompilation 25 min, lancement), aucune reprise.
- Preuve : `linux_backend.py:50-55` [vérifié]. Réutilisable : `inputs.py`, `input_staging.py`, `build_failure.py`.
- Dépend de : P1-e, P6-b. Difficulté : forte. Bloquant : **oui**.

**P6-b [Phase 6] CLI : `build --base --update`, `run --title-id`, `verify --build`**
- Manque : `Solution.md` §10 promet ces commandes ; `build` et `run` actuels concernent le host démo.
- Preuve : `nx2linux.py:259-299` [vérifié].
- Dépend de : P1-e. Difficulté : moyenne. Bloquant : oui.

**P6-c [Phase 6] `doctor` complet : Remill, LLVM 22, dynarmic, aarch64-as, libvpx, cache disque, SDK .NET**
- Manque : `doctor` vérifie cmake, pkg-config, SDL3, Vulkan, EGL, dotnet (facultatif), glslc, vulkaninfo, XDG.
- Preuve : `nx2linux.py:51-84` [vérifié].
- Dépend de : rien. Difficulté : faible. Bloquant : non.

**P6-d [Phase 6] Retirer les textes Apple/périmés, mettre l'i18n et le `.desktop`**
- Manque : message périmé `linux_ui.py:26` ; `README.md`/`BUILDING.md` sans Linux ; pas de fichier `.desktop`.
- Preuve : [vérifié].
- Dépend de : P6-a. Difficulté : faible. Bloquant : non.

**P6-e [Phase 6] Test UI du parcours complet (fixture sans données de jeu)**
- Manque : critère de sortie. Les tests couvrent la démo seulement (S6 : 17 tests).
- Preuve : `S6-validation.md` [lu].
- Dépend de : P6-a, P1-a. Difficulté : moyenne. Bloquant : non.

### Phase 7

**P7-a [Phase 7] Paquet Linux x86_64 (tarball ou AppImage) et installation depuis un tarball propre**
- Manque : rien n'existe. À décider : embarquer ou non Remill/LLVM/clang/dynarmic/traducteur .NET/libvpx.
- Preuve : aucune trace de packaging [vérifié].
- Dépend de : P7-c, P6-a. Difficulté : forte. Bloquant : oui.

**P7-b [Phase 7] Manifeste signé et `engine-id` Linux**
- Manque : `release_manifest.py` n'a pas de notion de plateforme Linux ; `engine_id` (pin + série + backend) à définir.
- Preuve : `grep -i linux tools/release_manifest.py tools/engine.py` vide [vérifié].
- Dépend de : P0-b. Difficulté : moyenne. Bloquant : non.

**P7-c [Phase 7] Chaîne d'outils reproductible hors Fedora (Debian/Ubuntu, conteneur) et verrouillage des versions**
- Manque : `tools/setup_toolchain.sh` installe Remill/dynarmic de façon propre (prouvé dans un préfixe vide, `S9` §5) mais exige LLVM 22 déjà installé, `dnf` pour Boost, et ~10+ min de compilation.
- Preuve : `tools/setup_toolchain.sh` l.1-30 [vérifié].
- Dépend de : rien. Difficulté : moyenne. Bloquant : oui pour la CI.

**P7-d [Phase 7] CI Debug/Release, test lavapipe sans écran, contrôle de reproductibilité**
- Manque : voir P0-e. Le test du triangle exige un vrai pilote vidéo (S6) ; lavapipe sans écran n'est pas validé.
- Preuve : `linux/host/README.md` « The Vulkan triangle test requires a real SDL video backend » [lu].
- Dépend de : P0-e. Difficulté : moyenne. Bloquant : non.

**P7-e [Phase 7] Licences, NOTICE et question juridique de la distribution du code recompilé**
- Manque : Remill, dynarmic, libvpx, stb_vorbis, Ryujinx sont intégrés ; `NOTICE` n'en parle pas pour Linux. Les objets AOT sont dérivés du code du jeu et ne peuvent pas être livrés : l'utilisateur doit recompiler.
- Preuve : [déduit] ; `NOTICE` non relu en détail.
- Dépend de : rien. Difficulté : faible. Bloquant : non.

## 4. Commandes lancées

| Commande | Résultat |
|---|---|
| `python3 tools/engine.py status` | 85 patches : 11 forwarded, 58 pending, 16 fork-only, 0 backport ; « engine/ up to date », pin `bbc3b7378475`. La synchronisation avait déjà été faite par le hook de session (« synced (patches/ changed) ») : je ne l'ai pas relancée pour ne pas toucher à `engine/`. |
| `python3 tools/engine.py check` | « patch queue: ok. 85 patches apply cleanly » |
| `python3 -m unittest discover -s frontend-qt/tests` (offscreen) | 234 tests OK (les « FAIL: » affichés sont des tests négatifs voulus) |
| `python3 -m unittest discover tools/tests` | 124 OK, 22 ignorés (ripgrep absent à ce moment-là, module `cryptography` absent). Non relancé depuis l'installation de ripgrep. |
| `python3 -m unittest discover -s linux/cli/tests` | 17 OK |
| `compileall` sur `engine/dm/tools`, `dm/games`, `scripts` | rc=0 |
| `python3 frontend-qt/gen_localization.py` | 1 047 chaînes, `localization.py` inchangé |
| `dotnet build nx_extract.csproj -c Release` | 0 erreur, 3,3 s (.NET 10.0.113) |
| Tests moteur (chaîne `toolchain-clean`, Remill + dynarmic) | voir ci-dessous |
| `curl https://api.github.com/repos/Sunshine-Off/NX2Linux` (lecture publique) | `has_issues: false`, `fork: true`, parent `LNPANDY/NX2Apple` |

Tests moteur lancés fichier par fichier (`engine/dm/tools/tests/`, 112 fichiers, chaîne `toolchain-clean` : Remill patché + dynarmic, comme le fait la CI) : la suite se termine. Tous les fichiers du chemin Linux passent (`aot_*`, `elf_module`, `jump_tables`, `nso*`, `horizon_{audio,display,fs,gpu,hle,input,movie,nvn}`, `movie_codec`, `nvn_rec`, `framediff`), sauf :

| Fichier | Résultat | Cause |
|---|---|---|
| `test_aot_tailcall` | 1 échec | test périmé après le patch Remill 0009 |
| `test_horizon_services` | 1 échec, reproduit | délai de libération d'un tampon `AudioOut` : `0 != 256` |
| `test_docs`, `test_env_switches` | 1 échec chacun | dérive de génération / documentation |
| `test_romfs_image` | erreur | `-Werror` avec gcc dans `romfs_image.h` |
| `test_module_validate` | 2 échecs en Python système, OK avec le venv | `capstone` hors de portée de `-I` |
| `test_horizon_game`, `test_movie_decoder` | erreurs lancés depuis `engine/`, **OK** (9 et 8 tests) lancés depuis `dm/tools/tests/` | import par nom nu ; artefact de ma façon de lancer |
| `test_nsp_mac_app` (80), `test_host_msl`, `test_regress`, `test_spirv_cross_pin`, `test_own_compilers` | échecs | tests Apple (Xcode, `lipo`, `libproc.dylib`, binaire Mach-O) |

Chiffres complets : les lignes de résultat de chaque fichier sont dans `/tmp/claude-1000/-home-teo-Documents-NX2Apple/2128d561-7736-469d-a04b-035c9a00f51a/scratchpad/engine-tests.txt` (hors dépôt).

Outils présents : python 3.14.8, dotnet 10.0.113, clang/LLVM 22.1.8, cmake, ninja, gcc, glslc, spirv-dis, vulkaninfo, aarch64-linux-gnu-as, SDL3 3.4.18, Vulkan 1.4.341, PySide6 6.11.2, capstone 5.0.7, libvpx.so.9, et, installés par vous en cours de session, `gh` (connecté : `shikosu`, scopes `repo`, `workflow`) et `rg`. **Absents : spirv-cross, glslangValidator, qemu-aarch64, module Python `cryptography`.**

Non fait, faute de données ou de permission : rejouer Xenoblade 2 ou Balatro (`.nsp` et `prod.keys` présents à la racine, mais interdits par la règle du dépôt de mon audit : je ne les ai ni ouverts ni utilisés), mesurer la cadence, tester une manette.

## 5. Questions ouvertes

- **Q1. Quel jeu cible pour la première version publique ?** XC2 est validé mais lourd (12 Go de cache, 15 images/s). Balatro est simple mais demande la couche GL (P5-c).
- **Q2. Garde-t-on l'hôte Python + bibliothèques C, ou écrit-on `dm/host/linux/` en C ?** Cela change P3-a, P3-f, P7-a.
- **Q3. Distribution : on embarque la chaîne Remill/LLVM/dynarmic/.NET, ou l'utilisateur lance `setup_toolchain.sh` ?** Quelles distributions : Fedora seulement, ou Ubuntu/Debian ?
- **Q4. Cible de performance et de GPU** : 30 images/s sur quelle machine minimale ? Veut-on valider AMD/Intel ?
- **Q5. Politique d'`engine/` dans le dépôt** : `engine/` est suivi dans ce fork (commit `b9ce580`) mais contredit la règle « moteur non versionné » de `AGENTS.md`. Garde-t-on ce choix ?
- **Q6. Installations restantes** (je n'ai rien installé moi-même) : `gh` et `rg` sont faits par vous. Facultatifs, à votre choix : `spirv-cross` (chaîne shader complète), `qemu-user` (oracle ARM64), et `pip install cryptography` (sans sudo, 22 tests de signature ignorés).
- **Q7. Issues** : `has_issues` est faux sur `Sunshine-Off/NX2Linux` (vérifié avec `gh api`). Vous avez les droits admin : activez-les vous-même (Settings → General → Features → Issues), ou dites-moi de le faire, je ne change pas les réglages du dépôt de ma propre initiative. Labels déjà présents : `bug`, `documentation`, `duplicate`, `enhancement`, `good first issue`, etc. ; `phase-N`, `bloquant` et `difficulté:*` sont à créer après votre accord. Les issues seront-elles en français ou en anglais ? `AGENTS.md` demande de l'anglais hors pages chinoises, et le dépôt a des formulaires dans `.github/ISSUE_TEMPLATE/`.
- **Q8. Le remote local `origin` pointe sur `Sunshine-Off/NX2Apple.git`**, alors que le dépôt cible est `NX2Linux` (renommage ?). Je cible `NX2Linux` avec `--repo`, comme demandé.

## 6. Decisions taken by the team (2026-11-04)

Written in English on purpose: the issues will be in English, as `AGENTS.md` asks.

1. **Target.** Long term: any Switch game. For now Xenoblade Chronicles 2 (0100E95004038000) is the only test game. Balatro and its GL-on-Vulkan layer (P5-c) come later, once everything else works. P5-d (second game) stays for after that.
2. **Host architecture (answers P3-f): keep the Python host with the C libraries, and move the hot paths to C step by step.** Reason: it is the lowest risk and loses nothing. 85 patches, the tests and the XC2 results rest on it, and a rewrite of `dm/host/linux/` in C would restart the validation of every service. The measurements say the bottleneck is the Python bridge under the GIL (`S8` §2, §9), not the language as a whole, so the C fast paths of `S8` §3 (synchronisation calls first) give the gain without the loss. P3-f becomes "record this decision in `docs/linux/architecture.md`", difficulty low. P3-a stays the main performance task.
3. **Toolchain (answers P7-c): `tools/setup_toolchain.sh` is the official way, and it must work on any Linux in the long run.** It is Fedora-only today (`dnf`, `rpm2cpio`, LLVM 22 preinstalled). The issue becomes: Debian/Ubuntu/Arch support, package detection, no hard-coded `dnf`, one test per distribution in a container.
4. **Language.** Issues and new documents in English.
5. **`engine/` is synchronised, not tracked (answers P0-c).** From now on `engine/` comes from `python3 tools/engine.py sync` only. This means removing the 2 619 tracked files of `engine/` from the index (`git rm -r --cached engine`, which I did not run: it changes tracked files), adding `/engine/` to the ignore rules, and reverting the commit `b9ce580` rule in `tools/` that accepts a tracked tree. Before that, `engine.py export` must be run so that the uncommitted engine changes seen at the start of the session (18 modified files, ~20 new ones) are not lost. **Check this first**: those changes may already be in patches 0100-0105.
6. **Repository name.** The target is `Sunshine-Off/NX2Linux`. The local `origin` still points to the old name `NX2Apple.git`: `git remote set-url origin https://github.com/Sunshine-Off/NX2Linux.git` (one command, to run when the team agrees).
