# T5 — svc, état du CPU invité, threads (2026-11-01)

## Définitions (analogies)

- **svc** (SuperVisor Call) : le guichet. Le jeu ne peut pas faire certaines choses lui-même (réserver de la mémoire, créer un thread, attendre) : il dépose une demande avec un numéro et ses registres, et le système répond. Ici le « système » est le gestionnaire de services Horizon (`svc_dispatch`).
- **tpidrro_el0** : le badge du thread. Un registre en lecture seule que Horizon règle sur la zone locale du thread ; le jeu le lit pour retrouver « ses » données.
- **Invalidation entre threads** : un panneau « page modifiée » affiché à tous. Quand un thread réécrit du code, chaque autre thread qui l'avait compilé doit jeter sa copie.

## Flux

```
code AOT   svc #n --Remill--> __remill_async_hyper_call --+
                                                          +--> aot_svc_handler(num, x0..x30, adresse du svc)
code JIT   svc #n --dynarmic CallSVC (arrête le bloc) ----+        |   écrit ses réponses dans x0..x30
              la boucle de run_jit lit les registres, appelle <----+
              le gestionnaire, réécrit les registres, reprend à l'instruction suivante

thread hôte A (Cpu A) --+                 écriture de code par B (AOT ou JIT)
thread hôte B (Cpu B)   +--> g_owners[page] = {A}  ==>  A.pending += page ; A.HaltExecution ; A recompile
```

## Ce qui est livré

| Fichier | Rôle |
|---|---|
| Remill patch 0003 (`remill-patches/`) | `svc` se lifte (le décodeur ajoutait un opérande NEXT_PC que la sémantique ignorait : « mismatched ISEL » puis `__remill_error`) ; `mrs xN, tpidrro_el0` a enfin une sémantique |
| `aot_runtime.cpp` | `__remill_async_hyper_call`, `__remill_missing_block`, `aot_svc_handler`, régions sans verrou (`aot_try_map`, `aot_unmap`), `tpidr_el0`/`tpidrro_el0` par exécution |
| `aot_jit.cpp` | `CallSVC` traité hors de `Run`, invalidation globale entre threads, tranches de 2**18 cycles, `ic ivau` |
| `aot_lift.py` | `Aot.set_svc_handler`, `try_map`, `unmap`, `run(..., tpidr_el0, tpidrro_el0)`, statistique `svc_calls` |
| `tests/test_aot_svc.py` | 14 tests (voir ci-dessous) ; `arm64_ref.run_image` accepte un gestionnaire de svc et les registres TLS |

## Résultats

- svc en AOT et en JIT : mêmes registres, mêmes appels au gestionnaire (numéro, adresse du svc, x0) que Unicorn avec le même gestionnaire.
- Un service qui mappe de la mémoire pendant l'exécution : le code invité l'écrit et la relit, le harnais lit les mêmes octets ; un second mappage qui recouvre est refusé par le service ; la mémoire démappée redevient une erreur explicite.
- `tpidr_el0`, `tpidrro_el0`, x18 : égaux à Unicorn. 8 threads hôtes × 50 appels : chacun voit ses propres registres, son badge et ses réponses (400 svc comptés).
- Code réécrit par un autre thread : recompilé ; un thread qui tourne dans `b .` en JIT s'arrête quand un autre remplace la branche par `ret`.
- Sans gestionnaire : erreur « svc #n at <adresse>: no Horizon service handler is set », en AOT comme en JIT.

## Défauts trouvés en route

1. Remill ne liftait pas `svc` (voir patch 0003). Les tests T3 et T4 qui attendaient un refus ont été réécrits : ils attendent maintenant l'erreur « pas de gestionnaire ».
2. dynarmic ignorait un arrêt demandé par un autre thread dans une boucle serrée (H22) : d'où les tranches.
3. Deux de mes tests réinitialisaient la mémoire (`reset()`) avant la deuxième exécution et effaçaient la réécriture qu'ils voulaient observer.

## Ce qui n'est PAS fait (à ne pas oublier)

- **Le vrai `svc_dispatch` n'est pas branché.** `hle_svc.c` inclut `<mach/mach.h>` et utilise `mmap` sur des adresses réelles : il ne se lie pas sous Linux avant T6, et sa mémoire n'est pas celle des régions bornées. Le test utilise un gestionnaire de remplacement de même forme (H19). C'est le sujet de T6.
- Les services qui bloquent (attente, création de thread) ne sont pas exercés : le gestionnaire bloque simplement le thread hôte.
- `thread.c` (slots TLS, TLR) n'est pas relié : les valeurs des pointeurs de thread viennent du harnais (H24).
- Pas de mesure de performance de l'appel de service ni des tranches (H22).
