# S7 bis — Xenoblade Chronicles 2 (0100E95004038000)

Branche `swann/jalon-s7bis-xc2`, issue de `swann/jalon-s7-first-game`. Même objectif que S7 (un jeu qui tourne en x86_64), avec un autre jeu : Balatro restait bloqué par l'absence de services Horizon, et XC2 est le jeu que Téo veut voir tourner. Aucune donnée du jeu n'est dans le dépôt : l'extraction va dans `~/.cache/nx2linux/extract/`, la recompilation dans `~/.cache/nx2linux/aot-xc2/` et `aot-cache/`.

## État

XC2 (`main`, `sdk`, `subsdk0`, 103 081 entrées recompilées en x86_64) exécute **tout le démarrage du SDK** dans l'ordre de `nn::init::Start` : `_init_libc0`, `nnosInitialize`, `_init_libc1`, `nninitInitializeSdkModule`, `SetHostArgc/Argv`, `nninitStartup`, `_init_libc2`, le `DT_INIT` de chaque module (tous les constructeurs globaux du jeu), puis arrive dans `nnMain`, le point d'entrée du jeu, en 0,2 s.

Il s'arrête là : `nnMain` demande les services `fsp-srv`, `appletOE`, `apm`, `aoc:u`, `pctl`, `pctl:a`, `pctl:s` et `pctl:r`, aucun n'existe, et le jeu lit un pointeur nul. **Rien ne s'affiche et rien n'est jouable.** 47 fonctions (AES, SHA, PMULL, `dc civac`, `faddp`) restent hors de la recompilation : elles attendent le JIT de secours (dynarmic n'est pas construit : sa configuration a été refusée par le classificateur de permissions de la session).

## Ce qui a été corrigé en chemin (tous trouvés par le jeu)

| Problème | Correctif |
|---|---|
| 48 relocations RELATIVE à une adresse alignée sur 4 octets (structures packées) refusées | `elf_module.py` : seule la borne et l'alignement sur 4 comptent (patch 0072) |
| `mrs cntpct_el0`, `cntfrq_el0`, `ctr_el0`, `dczid_el0` sans sémantique (19 fonctions du SDK) | Remill 0007 + `__nx2_read_cntpct` dans `aot_runtime.cpp` (patch 0073) |
| Le mot `0xe7ffdefe` (piège d'assertion) et `udf` rendaient 18 fonctions inliftables | la fonction est lifted, l'exécution du mot s'arrête avec son adresse (`__remill_error`) |
| Les wrappers `svc #n ; ret` exportés partagent une entrée de déroulement : pas des entrées | un symbole exporté coupe la fonction (`elf_module.find_functions`) |
| `aot_peek`/`aot_poke` remettaient le tampon de saut du run à null : un échec après un SVC qui lisait la mémoire abortait le processus | ils rendent le tampon du run |
| `DT_INIT` puis `init_array` construisaient les singletons deux fois | un seul `DT_INIT` par module, comme `host/loader.c` ; l'ordre est celui de `host/main.c` |
| Le tampon de commande IPC est au début de la région locale du thread, pas à `+0x100` | corrigé, avec les tests |

Patchs : 0072 (relocations), 0073 (compteur), 0074 (couche Horizon et trois correctifs AOT). Remill : `remill-patches/0007`.

## La couche Horizon

`engine/dm/tools/horizon_hle.py` : les SVC d'un SDK retail (mémoire : `SetHeapSize`, `MapMemory`, `MapPhysicalMemory`, `QueryMemory` ; `GetInfo`, handles, événements, attentes, `GetSystemTick`, `OutputDebugString`, `Break` avec la pile d'appels), le décodage et la réponse IPC (types 4 et 5, descripteurs A/B/W, handles copiés et déplacés) et la séquence de démarrage. `engine/dm/tools/horizon_services.py` : `sm:` et `lm`. Tout ce qui n'est pas géré est journalisé (`unhandled ...`) et répond une erreur : rien n'est simulé comme un succès. Un seul thread invité : une attente que personne ne peut signaler est signalée, jamais bloquante. `CreateThread` n'est pas encore pris en charge. 11 tests (`tests/test_horizon_hle.py`, mémoire invitée factice).

## Ce qui manque pour que le jeu s'affiche

1. Les services de `nnMain` : `fsp-srv` (le RomFS de 13 Go est extrait ; `hle_fs.c` du moteur Mach montre ce qu'il faut), `appletOE` (boucle de messages, ressources, focus), `apm`, `aoc:u`, `pctl*`, puis `hid`, `vi`/`nvdrv`, `audren`, `time`, `set`. Chaque service est une classe de `horizon_services.py`.
2. Les threads invités : XC2 en crée plusieurs (`CreateThread` / `StartThread` / attentes).
3. Le rendu NVN : XC2 n'utilise pas OpenGL. `engine/dm/gfx/nvn` est le moteur Mach/Metal, à porter sur le backend Vulkan (S4), ce qui est le plus gros travail de S7 bis.
4. Le JIT (dynarmic) pour les 47 fonctions restantes, ou leur sémantique dans Remill (AES, SHA1/256, PMULL).
5. Comparer les entrées exécutées à Unicorn : le module XC2 n'a pas été validé (`module_validate.py`).

## Reproduire

```bash
python3 linux/cli/nx2linux.py extract --keys <prod.keys> "Xenoblade Chronicles 2 [...].nsp"
# convertir (elf_module.py <ExeFS>/<module> <dossier> <module>) puis recompiler et lancer avec REMILL_BUILD défini ;
# Remill (commit 56918a8 + remill-patches/0001..0007) est construit dans ~/.cache/nx2linux/toolchain/remill
```
