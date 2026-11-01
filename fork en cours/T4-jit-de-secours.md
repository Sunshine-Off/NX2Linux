# T4 — le JIT de secours (2026-11-01)

## Définitions (analogies)

- **JIT (compilation à la volée)** : un interprète-traducteur qui, au moment où le programme arrive sur du code que personne n'a traduit à l'avance, le traduit sur place et le garde en cache. Comme un guide qui, à la frontière d'un pays dont on n'a pas le guide imprimé (l'AOT), traduit les panneaux en direct.
- **Code généré** : le jeu écrit lui-même des instructions en mémoire puis y saute. Aucune traduction à l'avance n'est possible : il faut le JIT, et il faut le **re**traduire si le jeu réécrit ces octets.
- **Cache invalidé** : « ce que j'avais traduit de cette page est périmé, jette-le ».

## Évaluation (règle de décision : une brique existante, justifiée)

| Critère | dynarmic (azahar-emu, 0BSD) | Unicorn (QEMU TCG) | Remill seul |
|---|---|---|---|
| Rôle | JIT AArch64 → x86_64, conçu pour émuler la Switch | émulateur généraliste | lifting statique : ne peut pas traduire du code connu seulement à l'exécution |
| Licence | **0BSD** (permissive) | GPL-2.0 d'après son dépôt (à vérifier avant distribution) | Apache-2.0 |
| Mémoire | callbacks vers **nos** régions invitées, sans copie | sa propre mémoire (partage possible par `mem_map_ptr`) | – |
| Intégration | `UserCallbacks` : mémoire, `svc` (T5), exclusifs, TLS, arrêt, invalidation | hooks plus lourds | – |
| Boucle 20 M itérations | **0,062 s** (321 M/s) | 0,128 s (156 M/s) | AOT : 0,070 s (287 M/s) |
| Compilation ici | 29 s, 0 erreur (Boost en-têtes extraits du rpm, sans sudo) | déjà installé (pip) | – |

Choix : **dynarmic**. Justification écrite : licence permissive, API faite pour ce rôle, débit comparable à l'AOT sur cette boucle, deux fois celui d'Unicorn. Rien n'est écrit à la main côté traduction. Limite : le dépôt d'origine (merryhime) n'est plus public, on dépend d'un fork (H13).

## Flux

```
AOT : blr / br vers une adresse qui n'est pas dans la table
   │                                                  (compteur fallback_requests)
   ▼
JIT  (aot_jit.cpp)  État AOT ─► registres dynarmic ; code lu dans la mémoire invitée
   │   ret vers x30 réel ─────────────────────────────────► retour à l'AOT
   │   saut vers l'entrée d'une fonction AOT ──► AOT ──► reprise à x30   (jit_to_aot)
   │   écriture sur une page déjà compilée ───► invalidation, recompilation   (jit_invalidations)
   └─► erreurs explicites : budget, aucun code, accès hors zone, svc, instruction non traduite
```

## Livré (engine/dm/tools)

`aot_jit.cpp` (le JIT), `aot_runtime.h` (partagé), `aot_runtime.cpp` (point d'accroche de repli, notification d'écriture, appel protégé), `aot_lift.py` (`Aot(..., jit=True, jit_entries=…, jit_ticks=…, jit_depth=…)`, `jit_log()`), `tests/test_aot_jit.py` (14 tests).

## Résultats

- Fonctions inconnues de l'AOT (`branch` par `blr`, `arith` par `br`) exécutées par le JIT : registres, NZCV, `sp` et mémoire **identiques à Unicorn**.
- Saut au **milieu** d'une fonction (refusé en AOT seul, test T3) : exécuté par le JIT, identique à la référence.
- Code **généré** : `patchme` écrit un mot dans `SLOT` puis l'appelle ; 4 écritures successives, 4 résultats corrects (7, 9, 7, 0x123), ≥ 3 invalidations. Réécriture par la mémoire de test (`aot_poke`) : invalidée aussi.
- JIT → AOT → retour (`bl` d'un code généré vers une fonction AOT) : identique à la référence, `jit_entries = 1`, `jit_to_aot = 1`.
- Compteurs et journal : `#1 pc=0x24 call count=1`, `#2 pc=0x10 jump count=1`.
- Limites : budget de **cycles** (boucle `b .` arrêtée), budget d'**entrées**, **profondeur** d'imbrication, aucun code à l'adresse (`no code to run at 0x12345678`), lecture hors zone (`JIT: read of 8 bytes at 0x5000`), `svc #5` (« connecté en T5 »).
- 4 threads, chacun son JIT : 800 exécutions correctes ; boucle `ldaxr`/`stlxr` **exécutée par le JIT** par 4 threads × 20 000 : compteur exact (80 000).
- Coût : première entrée 20 à 54 ms (création du JIT, 16 Mo de cache, compilation) ; ensuite l'écart avec l'AOT est noyé dans le surcoût de Python (≈ 17 µs par appel dans les trois cas) : **pas de mesure fine du coût d'une entrée chaude**.

## Défauts trouvés pendant le travail (corrigés)

1. Une adresse de retour factice (sentinelle) dans x30 faisait écrire de fausses adresses de retour sur la pile ; x30 reste maintenant la vraie, et le retour est détecté par l'adresse (H14).
2. Une faute déjà enregistrée (lecture hors zone) était écrasée par le « retour normal » qui la suivait : le gestionnaire d'exception ne remplace plus une faute.

## À savoir / limites (voir hypothèses H13 à H18)

- Le cache de code est **par thread** et une écriture n'invalide que le cache du thread qui écrit (H15) : à traiter en T5.
- `svc` dans le JIT s'arrête avec une erreur : le lien avec les services Horizon est T5.
- Les registres vectoriels sont copiés mais jamais testés (H18).
- dynarmic est compilé hors du dépôt (`DYNARMIC_DIR`) ; les tests sont ignorés sans lui.
