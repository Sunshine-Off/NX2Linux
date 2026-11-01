# T3b — `stxr`, `stlr`, `isb` et l'ordre mémoire (2026-11-01)

## Définitions (analogies)

- **Exclusif (`ldxr` / `stxr`)** : comme réserver une place avec un post-it. `ldxr` lit et pose un post-it « j'ai lu telle adresse, elle valait V » ; `stxr` n'écrit que si le post-it est encore valable, sinon il renvoie 1 et le programme recommence. C'est ainsi qu'un compteur partagé s'incrémente sans verrou.
- **Barrière** : un panneau « tout ce qui est avant doit être fini avant de passer ». `ldar` (acquire) protège ce qui vient après la lecture ; `stlr` (release) protège ce qui vient avant l'écriture.
- **CAS (compare-and-swap)** : « écris N seulement si la case contient encore V », en une seule opération indivisible du processeur.

## Le problème trouvé avant d'écrire `stxr`

Le moniteur de Remill ne retenait que **l'adresse** lue par `ldxr`. Un `stlxr` réussissait donc toujours, même si un autre thread avait écrit entre-temps : deux threads qui incrémentent le même compteur **perdent des mises à jour, sans aucune erreur**. Ajouter `stxr` dans ce style aurait donné une traduction silencieusement fausse dès qu'il y a des threads.

## Ce qui a été fait (patch Remill 0002, dans `remill-patches/`)

| Changement | Détail |
|---|---|
| Moniteur avec valeur | 2e pseudo-registre `MONITOR_VALUE` (`lib/Arch/Arch.cpp`) rempli par `ldxr`/`ldaxr` ; `stxr`/`stlxr` font un `__remill_compare_exchange_memory` contre cette valeur |
| Nouvelles instructions | `stxr` w/x, `stlr` x, `isb` (décodeurs dans `Arch.cpp`, stubs retirés de `Decode.cpp`) |
| Ordre acquire | `ldar`, `ldaxr` : le **chargement d'abord**, puis les barrières (elles le précédaient) |
| Ordre release | `stlr` : barrières, écriture, puis barrière store→load (`stlr` et `ldar` sont cohérents entre eux) |
| `dmb` | barrière **complète** (store→store et store→load), pas seulement store→store |
| Runtime (`aot_runtime.cpp`) | CAS atomique avec accès naturellement aligné exigé (sinon erreur explicite), barrières réelles, compteurs atomiques |

## Résultats (test_aot_atomics.py, 9 tests)

- 12 séquences (`ldxr`+`stxr`, `ldaxr`+`stlxr`, `stlr`/`ldar`, `isb`, `dmb ish`) identiques à Unicorn, registres, NZCV et mémoire.
- `stxr` sans `ldxr` : statut 1 et aucune écriture, comme le matériel.
- Accès exclusif non aligné : erreur explicite.
- L'ordre des barrières est lu dans l'IR liftée (ex. `stlr` : `load_store, store_store, write_memory_64, store_load`).
- **4 threads × 20 000 incréments d'un compteur** par une boucle `ldaxr`/`stlxr` : exactement 80 000, trois fois de suite.
- Contrôle de sensibilité : la même boucle avec `ldr`/`add`/`str` perd des mises à jour, donc les threads s'entrelacent vraiment et le test précédent a de la valeur.

## Limites

- Le CAS ne voit pas l'ABA (hypothèse H11).
- `stlr` coûte une barrière complète sur x86 (H12) ; non mesuré.
- Pas encore décodés dans Remill : `ldxrb/h`, `stxrb/h`, `ldxp/stxp` (128 bits), LSE (`ldadd`, `swp`, `cas`) : toujours une `LiftError`.
- Le test d'ordre mémoire est surtout structurel (x86 est déjà quasi-ordonné) : des tests de litmus sur une machine ARM seraient plus probants. Ils ne sont pas faits.
