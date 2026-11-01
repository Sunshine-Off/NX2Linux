# S7 — Balatro sur x86_64: état au 2026-11-01

Aucune donnée de jeu dans ce fichier: des adresses, des compteurs et des noms de fonctions seulement.

## Résultat

Le code de `main` et `sdk` de Balatro (title `0100CD801CE5E000`, NSP v0, SDK 18.3.2) est recompilé en x86_64 et **s'exécute**: 61 468 entrées (fonctions et cas de switch)
sont construites, le DT_INIT de `sdk` démarre, et l'exécution va jusqu'à l'abort du SDK (`svcBreak` appelé par `nn::diag`, via une fonction qui porte le nom `ConvertToFsCommonPath`)
parce qu'aucune couche de services Horizon n'existe encore sous Linux. **Balatro ne s'affiche pas et n'est pas jouable.**

## Ce qui a été corrigé pour y arriver (patch 0071, Remill patch 0006)

| Défaut | Cause | Correction |
|---|---|---|
| `sub_18f1f040` indéfini au lien | `ModuleSet` laissait 64 Kio entre les modules; un `bl` de main vers sdk est encodé pour des modules contigus (rtld de Horizon) | `GAP = 0` |
| `sub_18f0f040` indéfini | un `bl` vers l'intérieur de sa propre fonction est un second point d'entrée | `find_functions` coupe la fonction à la cible d'un `bl`; `ModuleSet` ajoute les cibles entre modules |
| « control reached ... not lifted as code » | un `b` direct hors de la fonction (appel terminal) et la chute dans l'entrée suivante appelaient `__remill_missing_block`, qui s'arrêtait | continue à l'entrée AOT de cette adresse; une adresse qui n'est pas une entrée s'arrête toujours avec son nom |
| lecture à `0x1b0` hors des régions | `TLR[0x1f8]` (objet thread courant) valait 0 | le thread du harnais a une ThreadLocalRegion et un objet thread, comme `host/thread.c` |
| `main+0x1bdb0` (RNG) différait d'Unicorn | `EXT` de Remill indexait le résultat à l'envers | Remill patch 0006 (avec `crc32*` et `yield`) |

## Ce qui reste avant un premier jeu fonctionnel

1. **Services Horizon sous Linux.** `engine/dm/host/` fait 17 480 lignes en 46 fichiers C, dont 18 dépendent de Mach/Metal/Foundation. `hle_svc.c` utilise `vm_remap` et `mach_task_self`.
   Il faut un hôte POSIX qui fournit: svc (mémoire, threads, synchronisation, ports nommés), `sm`, `fs` (RomFS, saves), `hid`, `vi`, `audren`, `time`, `account`.
2. **Imports HLE natifs.** `ModuleSet` ne lie que des fonctions Python (H29): il faut l'adaptateur d'ABI vers les fonctions de l'hôte (`hle.c`).
3. **EGL et GL.** `main` importe 33 symboles non définis (`eglGetDisplay`, `eglCreateContext`, ...). `linux/host/gl_vulkan.c` répond à GL sur Vulkan (Zink); la table d'imports EGL/GL du jeu reste à brancher.
4. **Ordre d'initialisation.** Horizon exécute `rtld` puis les `init` de chaque module; l'ordre exact et l'état du thread principal (`nn::os::detail` initialisé par `nninitStartup`) sont à reproduire.
5. **484 fonctions de `main` et 997 entrées** restent au JIT (traps, deux fonctions de plus de 60 000 octets); aucune n'a encore été comparée à Unicorn.

## Reproduire

```sh
python3 linux/cli/nx2linux.py extract "<jeu>.nsp" --keys <dossier des clés>      # écrit dans ~/.cache/nx2linux
# puis, dans engine/ (REMILL_BUILD et DYNARMIC_DIR définis):
python3 dm/tools/elf_module.py <ExeFS>/main <dossier de travail> main && ... sdk
python3 dm/tools/elf_module.py build <cache> <dossier>/main.manifest.json <dossier>/sdk.manifest.json
```
