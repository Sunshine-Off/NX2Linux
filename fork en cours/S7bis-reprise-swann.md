# S7 bis — reprise par Swann : de « trames noires » à une image

Branche `swann/jalon-s7bis-xc2` (issue de `swann/jalon-s7-first-game`). Lire d'abord [S7bis-xenoblade2.md](S7bis-xenoblade2.md) (état et historique des correctifs) et [S7-balatro.md](S7-balatro.md).

## Où on en est

Xenoblade Chronicles 2 (0100E95004038000) tourne en x86_64 recompilé : SDK, `nnMain`, 36 threads, fibres, son silencieux, NVN, soumission des commandes. Il présente ~275 trames/120 s dans une fenêtre Vulkan, **toutes noires** : les dessins (92 000 en 2 min) sont comptés, pas exécutés. Aucune donnée du jeu n'est dans le dépôt.

## Mise en place (rien de tout cela n'est dans git)

1. `git fetch && git switch swann/jalon-s7bis-xc2` puis `git config core.hooksPath .githooks` ; `python3 tools/engine.py sync` (reconstruit `engine/` : upstream + 61 patchs).
2. Paquets : clang, llvm-devel, ninja, cmake, SDL3, Vulkan, glslc, dotnet (voir `python3 linux/cli/nx2linux.py doctor`), `aarch64-linux-gnu-binutils`, `boost-devel`.
3. venv avec capstone, unicorn, lz4 : `~/.cache/nx2linux/venv` (les workers Python tournent en `-I`, donc pas de site-packages utilisateur).
4. **Remill** au commit `56918a8` + `fork en cours/remill-patches/0001…0008` (`git am`), construit dans `~/.cache/nx2linux/toolchain/remill` (dépendances `-DUSE_EXTERNAL_LLVM=ON`, puis `cmake -G Ninja -B build`). Export `REMILL_BUILD=…/remill/build`.
5. Fenêtre : `cmake -S linux/host -B ~/.cache/nx2linux/build-host && cmake --build ~/.cache/nx2linux/build-host` (donne `libnx2linux_display.so`).
6. Jeu : `python3 linux/cli/nx2linux.py extract --keys <prod.keys> "<jeu>.nsp"`, puis pour `main`, `sdk`, `subsdk0` : `python3 -I engine/dm/tools/elf_module.py <ExeFS>/<module> <dossier> <module>`.
7. Lancer : `REMILL_BUILD=… python3 -I engine/dm/tools/horizon_run.py --modules <dossier> --romfs <RomFS.img> --title 0100e95004038000 --display --seconds 60`. La première recompilation prend ~25 min (cache dans `~/.cache/nx2linux/aot-cache`), ensuite ~25 s.
   Variables utiles : `NX2LINUX_DUMP_FRAME=<dossier>` (PNG), `NX2LINUX_NVN_LOG=1`, `NX2LINUX_NVN_TRACE=N`, `NX2LINUX_AUDIO_LOG=1`, `NX2LINUX_SVC_RING=1` (rapport d'attentes), `NX2LINUX_OPMODE=0` (portable).

## À faire, dans l'ordre

1. **dynarmic (demandé en second).** Le dépôt est cloné dans `~/.cache/nx2linux/toolchain/dynarmic` (azahar-emu/dynarmic), en-têtes Boost extraits dans `boostrpm/`. La session de Claude n'a pas eu le droit de lancer `cmake`. Configurer avec `-DDYNARMIC_FRONTENDS=A64 -DDYNARMIC_USE_BUNDLED_EXTERNALS=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DBoost_INCLUDE_DIR=…/boostrpm/usr/include -DBoost_NO_BOOST_CMAKE=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5`, compiler, exporter `DYNARMIC_DIR`, relancer avec `jit=True` (`horizon_run.py` passe `jit=False` : ajouter une option). But : les 27 fonctions AES/SHA/PMULL que Remill ne traduit pas, et les tests `test_aot_jit`/`LoadingWithJit`.
2. **Exécuter les dessins** (le gros morceau, jalon S4 réel) :
   - shaders : le moteur Mach traduit les shaders Maxwell/DKSH vers Metal (`engine/dm/gfx/msl`, chaîne `dmsh`) ; il faut une sortie SPIR-V (Vulkan). Le jeu fournit ses shaders par `nvnProgramSetShaders` ;
   - états et ressources : `horizon_nvn.py` enregistre déjà les tampons de commandes (`cmds`) et les rejoue à la soumission (`Nvn.replay`) ; il y manque les états (blend, depth, polygone), liaisons (textures, tampons uniformes, sommets), `Draw*`, cibles de rendu réelles, et la lecture des textures bloc-linéaires (`nvn_layout.py` ne fait que les tailles : il faut le déswizzle) ;
   - côté `linux/host/vulkan_renderer.c` : aujourd'hui seulement `present_rgba` (copie d'un tampon CPU). Un backend NVN→Vulkan réel remplace le tampon CPU.
3. **Vitesse** (~2 trames/s). `guest()` dans `aot_runtime.cpp` cherche une région linéairement (un cache par thread aide) : une table de pages. Les appels de service Python (GIL) pèsent aussi. Profiler avec `perf`.
4. **Entrées** : SDL (`nx2linux_display_poll`) → `HidState` dans `horizon_hid.py` (boutons, sticks) ; tactile ; `pctl`, `hid` complet.
5. **Sauvegardes** : `horizon_fs.py` écrit sous `$XDG_DATA_HOME/nx2linux/saves/<titre>` sans transaction (commit/rollback absents).
6. **Validation** : comparer les entrées exécutées à Unicorn (`module_validate.py`) sur le module XC2 ; non fait.

## Pièges rencontrés (déjà corrigés, à ne pas redécouvrir)

Le tampon IPC est au début du TLR ; `DT_INIT` suffit ; un `ret` ne change pas de pile en code recompilé (fibres = un thread hôte par fibre) ; `SYS` (`dc`/`ic`) est un appel hyperviseur asynchrone qui ne doit pas être pris pour un SVC ; les `switch` LLVM passent par `br x16` et peuvent avoir un garde `cmp`+`ccmp` ; un compteur atomique global sur chaque accès mémoire tue la vitesse.

## Règles du dépôt

Travailler dans `engine/`, commiter là, puis `python3 tools/engine.py export <rev> --reason … --issue …` ; `engine.py check` doit passer. Ne jamais pousser sur `main`, ne jamais `--no-verify`. Commiter avec sa propre identité (pas celle d'upstream).
