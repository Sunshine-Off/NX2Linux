# S7 bis — Xenoblade Chronicles 2 (0100E95004038000)

> **Mise à jour du 2026-11-03** : l'état ci-dessous est celui du 2026-11-02 (trames noires). Le jeu affiche maintenant le titre, l'ouverture et le premier combat éclairé : voir [S7bis-cloture.md](S7bis-cloture.md).

Branche `swann/jalon-s7bis-xc2`, issue de `swann/jalon-s7-first-game`. Même objectif que S7 (un jeu qui tourne en x86_64), avec Xenoblade Chronicles 2. Aucune donnée du jeu n'est dans le dépôt : l'extraction va dans `~/.cache/nx2linux/extract/`, la recompilation dans `~/.cache/nx2linux/aot-xc2/` et `aot-cache/`, la chaîne Remill dans `~/.cache/nx2linux/toolchain/`.

## État (honnête)

**Premier affichage atteint, mais noir.** XC2 démarre complètement : SDK, `nnMain`, 36 threads invités, fibres, son silencieux, configuration NVN. Il soumet ses tampons de commandes et présente des trames : **275 trames en 120 s** sur la fenêtre Vulkan (ouverte sur la RTX 4060, ou lavapipe sans GPU), avec 92 643 dessins et 7 225 effacements reçus. Les trames sont **toutes noires** : les seuls effacements qui visent les textures de la fenêtre sont noirs, et les dessins sont comptés, pas exécutés (pas de shaders). `NX2LINUX_DUMP_FRAME=<dossier>` écrit un PNG toutes les 30 trames.

Cadence : environ 2 trames par seconde. Le compteur de lectures/écritures invitées était un atomique partagé par tous les threads (un thread de 36 se battait pour une ligne de cache) : passé en compteurs par thread, 12 fois plus de dessins par minute ; le reste est le coût du code recompilé (chaque accès passe par `guest()`), à optimiser.

## Le chemin jusqu'ici

| Étape | Résultat |
|---|---|
| Extraction, conversion ELF, recompilation | 103 404 entrées en x86_64, 27 fonctions (AES, SHA, PMULL) restent hors de la recompilation |
| Couche Horizon (SVC, IPC, `sm:`, threads, mutex, variables de condition, événements) | `horizon_hle.py`, 29 tests |
| Remplacement de fonctions du SDK (`HLE_FORCE` du moteur Mach, en Python) | `fs`, `account`, `oe`, `vi`, `nv`, `hid` portés ; 259 entrées triviales générées ; 388 entrées à logique des fichiers de services répondent 0 et sont comptées |
| Fibres (`nn::os::Fiber`) | un thread hôte par fibre, `horizon_fiber.py` |
| NVN | périphérique Python (Tegra X1, pools, tailles de textures, files, tampons de commandes rejoués, fenêtre) ; **les dessins et les shaders ne sont pas rendus** |
| Son | serveur silencieux (`horizon_audio.py`) : voix, pools, effets, `AudioOut`, événement du renderer qui bat toutes les 5 ms ; pas de son produit |
| Temps | `horizon_time.py` (horloges de l'hôte) |

Remill : patchs 0006 (EXT, CRC32), 0007 (registres de compteur), 0008 (`DC`/`IC`, `FADDP`). Moteur : patchs 0072 à 0079.

## Ce qui a été trouvé en chemin (tous par le jeu)

relocations alignées sur 4 octets · `mrs cntpct_el0` · mot de piège `0xe7ffdefe` · wrappers `svc` exportés · `aot_peek` qui effaçait le tampon de saut · le tampon IPC est au début du TLR · `DT_INIT` suffit (pas `init_array` en plus) · `br x16` des `switch` LLVM · `DC`/`IC` (SYS est un appel hyperviseur asynchrone pour le décodeur) · les fibres changent de pile par `ret`, impossible en code recompilé.

## Ce qui manque pour une vraie image (dans l'ordre)

1. **Exécuter les dessins.** Traduire les shaders Maxwell/DKSH en SPIR-V (la chaîne `dmsh` du moteur Mach cible Metal), puis rejouer états, tampons, textures bloc-linéaires (déswizzle), cibles de rendu et passes sur Vulkan. C'est le gros morceau de S4/S7.
2. **Vitesse.** Environ 2 trames/s : optimiser les accès mémoire invités (table de pages au lieu d'une recherche de région) et le coût des appels de service Python.
3. **Entrées** SDL → `NpadState`, sauvegardes, `pctl`, tactile.
4. **JIT dynarmic** pour les 27 fonctions cryptographiques restantes (AES, SHA, PMULL), ou leur sémantique dans Remill.
5. Comparer les entrées exécutées à Unicorn (`module_validate.py`) : le module XC2 n'a pas été validé.

## Reproduire

```bash
python3 linux/cli/nx2linux.py extract --keys <prod.keys> "Xenoblade Chronicles 2 [...].nsp"
# convertir : engine/dm/tools/elf_module.py <ExeFS>/<module> <dossier> <module>   (main, sdk, subsdk0)
# Remill (commit 56918a8 + fork en cours/remill-patches/0001..0008) dans ~/.cache/nx2linux/toolchain/remill
# fenêtre : cmake -S linux/host -B ~/.cache/nx2linux/build-host && cmake --build ~/.cache/nx2linux/build-host
REMILL_BUILD=~/.cache/nx2linux/toolchain/remill/build python3 -I engine/dm/tools/horizon_run.py \
    --modules <dossier> --romfs <RomFS.img> --title 0100e95004038000 --display --seconds 60
```
