# S7 bis — Xenoblade Chronicles 2 (0100E95004038000)

Branche `swann/jalon-s7bis-xc2`, issue de `swann/jalon-s7-first-game`. Même objectif que S7 (un jeu qui tourne en x86_64), avec Xenoblade Chronicles 2. Aucune donnée du jeu n'est dans le dépôt : l'extraction va dans `~/.cache/nx2linux/extract/`, la recompilation dans `~/.cache/nx2linux/aot-xc2/` et `aot-cache/`, la chaîne Remill dans `~/.cache/nx2linux/toolchain/`.

## État (honnête)

**XC2 n'affiche pas encore d'image.** Il exécute le démarrage complet du SDK, `nnMain`, et ~12 s de code d'initialisation de son propre moteur (30 threads invités, des fibres, la configuration NVN) jusqu'à `fw::SoundManager::initialize`, qui échoue parce que **le serveur audio `nn::audio` n'existe pas sur l'hôte**. Aucun `QueuePresentTexture` n'est donc atteint, et la fenêtre Vulkan n'a jamais reçu une trame du jeu.

Ce qui est prouvé séparément, sans le jeu : un tampon RGBA arrive à l'écran par `libnx2linux_display.so` (SDL + Vulkan, 3 tests sur lavapipe), et le périphérique NVN en Python reproduit « effacer un rendu cible, soumettre, présenter » jusqu'à cette même fenêtre (test `NvnDevice.test_a_cleared_window_texture_is_what_is_presented`).

## Le chemin jusqu'ici

| Étape | Résultat |
|---|---|
| Extraction, conversion ELF, recompilation | 103 404 entrées en x86_64, 27 fonctions (AES, SHA, PMULL) restent hors de la recompilation |
| Couche Horizon (SVC, IPC, `sm:`, threads, mutex, variables de condition, événements) | `horizon_hle.py`, 29 tests |
| Remplacement de fonctions du SDK (`HLE_FORCE` du moteur Mach, en Python) | `fs`, `account`, `oe`, `vi`, `nv`, `hid` portés ; 259 entrées triviales générées ; 388 entrées à logique des fichiers de services répondent 0 et sont comptées |
| Fibres (`nn::os::Fiber`) | un thread hôte par fibre, `horizon_fiber.py` |
| NVN | périphérique Python (Tegra X1, pools, tailles de textures, files, tampons de commandes rejoués, fenêtre) ; **les dessins et les shaders ne sont pas rendus** |
| Son | **bloque** : le jeu utilise tout le graphe de mixage `nn::audio` |

Remill : patchs 0006 (EXT, CRC32), 0007 (registres de compteur), 0008 (`DC`/`IC`, `FADDP`). Moteur : patchs 0072 à 0079.

## Ce qui a été trouvé en chemin (tous par le jeu)

relocations alignées sur 4 octets · `mrs cntpct_el0` · mot de piège `0xe7ffdefe` · wrappers `svc` exportés · `aot_peek` qui effaçait le tampon de saut · le tampon IPC est au début du TLR · `DT_INIT` suffit (pas `init_array` en plus) · `br x16` des `switch` LLVM · `DC`/`IC` (SYS est un appel hyperviseur asynchrone pour le décodeur) · les fibres changent de pile par `ret`, impossible en code recompilé.

## Ce qui manque pour afficher (dans l'ordre)

1. **Serveur `nn::audio` silencieux.** Le jeu appelle `GetAudioRendererWorkBufferSize`, `OpenAudioRenderer`, `AcquireMemoryPool`, `AcquireVoiceSlot`, `AddDeviceSink`, effets (retard, réverbération I3DL2), `RequestUpdateAudioRenderer`, plus `AudioOut`. Le moteur Mach exécute le serveur du SDK en local avec un rendu CPU (`hle_audren.c`). Le côté client du SDK tourne tel quel ; il faut répondre au bloc de mise à jour (en-tête, états des pools et des voix, tampons d'onde consommés) et fournir l'événement système du renderer. Un contournement par titre (`horizon_profile_xc2.py`, qui saute `ssd::SoundCore::Init`) a été essayé : le gestionnaire de son plante ensuite, donc il ne suffit pas.
2. **Ce que le jeu demandera après le son** : d'autres gestionnaires du `Framework::initializeManager`, `hid` complet (manettes, entrées SDL → `NpadState`), `time`, `pctl`, sauvegardes.
3. **Rendu réel.** Les commandes de dessin sont comptées, pas exécutées. Une scène demande de traduire les shaders Maxwell/DKSH en SPIR-V (la chaîne `dmsh` du moteur Mach cible Metal), de gérer les textures bloc-linéaires, les états et les passes. C'est le plus gros travail de S4/S7.
4. Les 27 fonctions cryptographiques : sémantique AES/SHA/PMULL dans Remill, ou le JIT dynarmic (sa configuration a été refusée par le classificateur de permissions de la session).
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
