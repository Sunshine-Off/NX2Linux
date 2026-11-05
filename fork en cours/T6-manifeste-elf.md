# T6 — modules ELF et manifeste à la place des entrées Mach-O (2026-11-01)

## Définitions (analogies)

- **Manifeste** : la fiche d'identité d'un module, à côté de son ELF. Comme la fiche d'un colis à la douane : elle dit ce qu'il y a dedans (segments, fonctions, imports, exports) pour qu'on n'ait pas à rouvrir et refaire l'inventaire. Elle est **dérivée de l'ELF** et vérifiée contre lui : une fiche qui ne correspond pas au colis est refusée.
- **Import** : un numéro de téléphone dans l'annuaire du module (la GOT). Le module appelle ce numéro ; derrière, il n'y a pas de code invité mais une fonction de l'hôte.
- **Relocation** : la correction des adresses après avoir choisi où poser le module (« ce pointeur vise la fonction `fa` : écris base + 0x68 »).

## Ce que le chargeur Mach-O allait chercher, et ce qui le remplace

| Avant (host/loader.c, Mach-O) | Maintenant |
|---|---|
| sections `__TEXT,__dm<nom>x0`, `__DM<nom>W,__img`, `__dm<nom>x1` (`getsectiondata`, `vm_remap`) | segments `PT_LOAD` de l'ELF (`nso2elf.py`), table `segments` du manifeste |
| `<nom>.patched.bin` (module réécrit pour Apple) | le module d'origine, non modifié : le code recompilé gère lui-même x18, TLS et `svc` (T3, T5) |
| `<nom>.fixups.tsv` (bornes, numéros de slots K18/KTLS/KTLR/KGATE) | inutile : plus de zone de patch, plus de page de clés TSD |
| `nro.tsv` + table NRO empaquetée (255 sections max dans un Mach-O) | un manifeste par module ; NRO non traités (voir limites) |
| `parse_dynamic` + `relocate` (C, hôte AArch64) | `elf_module.py` : mêmes types de relocations, même ordre de résolution (modules d'abord, puis hôte) |
| `_mh_execute_header`, `swforce` | non repris : la table de prise de contrôle forcée dépend du HLE (hors périmètre) |

## Flux

```
module.nso --nso2elf--> module.elf  --describe--> module.manifest.json (sha256 + tout ce qui se déduit de l'ELF)
                                                         |
ModuleSet([manifestes], hle) : vérifie ─> choisit les bases ─> relocalise (RELATIVE / ABS64 / GLOB_DAT / JUMP_SLOT)
      symbole : export d'un module chargé  >  fonction hôte (import)  >  0 si faible  >  import "non résolu" (erreur nommée à l'appel)
      │
      └─ build() : Aot(fonctions de tous les modules, stub_unliftable) + régions mémoire + pile + imports ─> run / call / run_inits
```

## Ce qui est livré

| Fichier (engine/dm/tools) | Rôle |
|---|---|
| `elf_module.py` | `convert_nso`, `describe`/`load_manifest` (vérification), `find_functions`, `ModuleSet` (`build`, `call`, `symbol`, `run_inits`) |
| `aot_runtime.cpp/.h`, `aot_lift.py` | table d'imports (`aot_register_import`, `Aot.register_import`), statistique `import_calls` ; le point d'entrée de `aot_run` retombe sur le JIT si la fonction n'est pas en AOT |
| `nso2elf.py` | `convert()` extrait de `main()` (et le fichier est fermé) |
| `tests/module_fixture.py` | NSO synthétiques avec section dynamique (PLT/GOT, table de pointeurs, données exportées, `init_array`, symbole faible, import non implémenté) ; `arm64_fixture.eh_frame` en est extrait |
| `tests/test_elf_module.py` | 16 tests ; `arm64_ref.run_image(imports=…)` |

## Résultats

- Le module synthétique passe NSO -> ELF + manifeste -> relocalisé -> lifté -> exécuté, et donne les mêmes registres et la même mémoire qu'Unicorn exécutant la même image relocalisée avec les mêmes fonctions hôtes.
- PLT/GOT : `mod_entry(7)` = `hle_add(7, 5) + 1` = 13 via un stub PLT sans entrée de déroulement (trouvé comme cible d'appel).
- Pointeurs de fonctions remplis par relocation, `init_array` exécuté, donnée exportée lue, symbole faible à 0, import d'un autre module lié avant l'hôte.
- Import que personne n'implémente : `call to the unresolved import missing_fn (0x70000010): no host function implements it`.
- Fonction non liftable d'un module (ccmn forme registre) : exécutée dans le JIT, qui atteint quand même la fonction hôte.
- Refus explicites : ELF modifié (SHA-256), manifeste qui ment (`"exports" does not match what the ELF says`), fichier qui n'est pas un ELF AArch64, type de relocation inconnu, module chargé deux fois.

## Limites (à ne pas oublier)

- **Aucun module de jeu réel essayé** (pas de données privées ici) : H25 et H26 sont les hypothèses qui casseront en premier sur un vrai NSO. C'est le jalon T7.
- **NRO non traités** : pas de `nn::ro` (chargement à l'exécution), pas de `nro.tsv`. Le format du manifeste les permettrait (un manifeste de plus chargé plus tard), mais la liaison tardive n'est pas écrite.
- **Imports : fonctions Python seulement.** Brancher les vraies fonctions HLE de `hle.c` demande un adaptateur d'ABI (H29), et `hle_svc.c` utilise encore Mach (`vm_remap`, `mach_task_self`) : le vrai `svc_dispatch` n'est toujours pas lié (T5, H19).
- Les dépendances Mach-O qui restent dans `host/*.c` (loader.c lui-même, crash.c, probe.c, hle_*.c) ne sont pas touchées : hors périmètre (hôte, rendu, plateforme). Le nouveau chemin ne les utilise pas.
- Pas de TLS ELF (`PT_TLS`), pas de `DT_NEEDED`, pas de `DT_RELR`/`DT_GNU_HASH` (le chargeur Mach-O gère RELR : à ajouter si un module en a).
- Pas de permissions de pages (H27).
