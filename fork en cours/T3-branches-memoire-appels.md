# T3 — branches, mémoire et appels (2026-11-01)

## Définitions (analogies)

- **Branche directe / indirecte** : directe = « va à l'adresse écrite dans l'instruction » (un panneau routier fixe). Indirecte (`br`, `blr`) = « va à l'adresse qui est dans ce registre » (un GPS dont la destination change). Le traducteur connaît la première à l'avance ; la seconde doit être vérifiée au moment du saut.
- **Pile (stack)** : une pile d'assiettes en mémoire où une fonction pose ce qu'elle doit retrouver au retour (registres sauvegardés, adresse de retour). `sp` est le haut de la pile.
- **Accès non aligné** : lire 8 octets à une adresse qui n'est pas un multiple de 8. L'ARM64 l'autorise en mémoire normale ; le test le vérifie, au lieu de le supposer.

## Flux

```
fonction ARM64 --remill-lift--> sub_<adresse> (LLVM IR) --clang++--> x86_64
    bl direct   -> appel C de sub_<cible>          (résolu par l'éditeur de liens)
    b / b.cond  -> branchement dans la fonction    (graphe de contrôle LLVM)
    ldr/str     -> __remill_read/write_memory_N    -> région invitée bornée (aot_runtime.cpp)
    blr / br    -> __remill_function_call / _jump  -> table des fonctions AOT
                       cible connue   -> exécutée
                       cible inconnue -> compteur "fallback" + erreur explicite  (T4 : JIT)
    ret         -> retour C
```

## Ce qui est livré

| Fichier (engine/dm/tools) | Rôle |
|---|---|
| `aot_lift.py` | Pilote Remill + clang : lifte chaque fonction, génère la table des adresses, compile `aot_runtime.cpp`, expose `Aot.run()`. Erreurs : `LiftError` (instruction non liftable, avec adresse, mot et texte), `LinkError` (intrinsèque non définie) |
| `aot_runtime.cpp` | Mémoire invitée bornée (tout accès hors zone = erreur « read/write, N octets, adresse »), table d'indirections, compteurs (appels/sauts indirects, demandes de repli, lectures, écritures) |
| `tests/test_aot_t3.py` | branche, boucle, appels directs imbriqués avec pile, appels/sauts indirects, cible inconnue, saut au milieu d'une fonction, `svc` refusé |
| `tests/test_aot_memory.py` | 36 instructions mémoire × 384 cas |
| `tests/arm64_ref.py` | `run_image` : référence Unicorn avec régions mémoire et capture des fautes |

## Résultats

- Les fonctions `branch`, `loop`, `caller` (deux `bl` imbriqués, `stp`/`ldp` sur la pile) et `indirect` (`blr` puis `br`) donnent **les mêmes 31 registres, `sp`, NZCV et la même mémoire** que la référence Unicorn.
- 36 instructions mémoire (8/16/32/64 bits, signées ou non, non alignées, offset échelonné/non échelonné/pré-indexé/post-indexé/registre décalé, paires `ldp`/`stp`/`ldpsw`) : identiques sur 8 adresses × 6 valeurs × 4 indices × 2 NZCV.
- Cible indirecte inconnue : `indirect call to 0x12345678: not the entry of an AOT function, and no JIT fallback yet`, compteur `fallback_requests` = 1. Saut dans une fonction : `not the entry`.
- Accès hors zone : `guest write of 8 bytes at 0x101ffc is outside every mapped region` ; la référence échoue aussi.
- `svc` : `LiftError` avec adresse et instruction.

## Défauts trouvés dans mes propres tests (corrigés)

1. Le test `indirect` appelait `arith`, qui écrase `x2` : le `br x2` sautait n'importe où, et AOT et référence échouaient pareil, donc le test « passait ». La comparaison refuse maintenant qu'une référence échoue sans que ce soit attendu.
2. La mémoire AOT était globale et n'était pas remise à zéro entre les cas (`Aot.reset()`).
3. Les constantes de registres d'Unicorn ne sont pas contiguës après x28 (`xreg()`).

## Limites et lacunes (à ne pas oublier)

- Hypothèses H7 à H10 dans `hypotheses.md` : `ret` ignore x30, `br` = appel terminal vers une entrée de fonction (pas de tables de sauts), pas de SIMD/flottants.
- **Atomiques (mission 6), mesure faite mais pas traitée** : Remill lifte `ldxr`, `ldaxr`, `stlxr`, `ldar`, `dmb` ; il refuse `stxr`, `stlr`, `isb` et les instructions LSE. Les cœurs A57 de la Switch n'ont pas LSE : `stxr`, `stlr` et `isb` sont les prochains manques à combler (même méthode que `adc`).
- Pas de mesure de performance. Les registres vivent dans la structure d'état, pas dans des registres x86 entre les fonctions.
- Le JIT de secours n'existe pas : une cible inconnue s'arrête. C'est T4.
