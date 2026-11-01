# T2 — IR choisie et évaluation des briques (2026-11-01)

## Définitions (analogies)

- **IR (représentation intermédiaire)** : une « langue pivot » entre l'ARM64 et l'x86_64. Comme un traducteur qui passe par l'anglais pour aller du japonais au français : on écrit un lecteur ARM64 → IR et un écrivain IR → x86_64, au lieu d'un traducteur pour chaque paire.
- **Lifting (levée)** : lire des instructions machine et les réécrire dans l'IR, en rendant explicite tout ce que la puce fait en silence (par exemple, mettre à jour les flags).
- **AOT** : traduire à l'avance, avant de lancer le jeu (comme imprimer le livre traduit). Le **JIT** traduit à la demande pendant l'exécution (comme un interprète en direct) ; ce sera T4.
- **NZCV** : quatre petits voyants (Negative, Zero, Carry, oVerflow) que certaines instructions allument pour que la suivante puisse décider (`b.eq`, `b.lt`…).

## Décision : l'IR est LLVM IR, produite par Remill

| Étage | Brique | Rôle |
|---|---|---|
| Frontière des fonctions | `nso.py`, `nso_code.py`, `arm64_survey.py` (T1) | où une fonction commence et finit |
| Lifting AArch64 → IR | **Remill** (lifting-bits/remill, commit 56918a8) | une fonction `sub_<adresse>(State*, pc, Memory*)` |
| Génération x86_64 | **LLVM / clang 22** | code machine, optimisations |
| Référence ARM64 | **Unicorn 2.1.4** (+ `qemu-aarch64-static`, installé, pas encore utilisé) | résultat « vrai » à comparer |

Le code propre à NX2Linux est seulement l'hôte (`remill_host.cpp`) : l'état invité, les intrinsèques `__remill_*`, plus tard SVC et mémoire. Aucun traducteur ARM64 écrit à la main : la règle de décision est respectée.

## Mesures

| Mesure | Résultat |
|---|---|
| Remill + LLVM 22.1.8 (système Fedora 44) | **compile sans erreur** (432 étapes ninja, dépendances gflags/glog/gtest/XED comprises) malgré « LLVM 15+ » dans le README |
| Lifting de `arith` (5 instructions) | 187 lignes d'IR, sémantiques en ligne, 4 appels d'intrinsèques de flags |
| `arith` AOT (x86_64) vs référence Unicorn | **identique sur 11 cas limites**, x0 et N, Z, C, V (test_recompile_arith.py) |
| Fonction avec mémoire / appel indirect (`indirect`) | **le lien échoue** sur `__remill_write_memory_64`, `__remill_read_memory_64`, `__remill_function_call` : erreur explicite, pas de traduction approximative |

## Ce que T2 ne prouve pas (honnêteté)

- Un seul bloc sans branche, sans mémoire. T3 doit traiter branches, mémoire, appels.
- Les intrinsèques de flags et de comparaison (`__remill_flag_computation_*`, `__remill_compare_*`) sont des « marqueurs » qui renvoient leur premier argument. Vérifié (test_recompile_insn.py) sur 30 instructions en 32 et 64 bits : `adds`, `subs`, `cmp`, `cmn`, `adc`, `adcs`, `sbc`, `sbcs`, `ands`, `tst`, `negs`, `ccmp` (registre et immédiat), `ccmn` (immédiat), `csel`, `csinc` — chacune sur 81 couples de valeurs limites × 16 états NZCV d'entrée, identique à Unicorn.
- **Lacunes de Remill (commit 56918a8) trouvées par ces tests** : aucune sémantique ni décodeur pour `adc` et `adcs` (32 et 64 bits), ni sémantique pour `ccmn` en forme registre. Le code lifté appelle alors `__remill_error` ; `lift()` lève une `LiftError` (adresse, mot, raison) et `test_unsupported_instruction_is_an_explicit_error` fige les mots concernés.
- **`adc`/`adcs` corrigés** (choix de Téo : patcher Remill) par [remill-patches/0001-aarch64-add-adc-adcs.patch](remill-patches/0001-aarch64-add-adc-adcs.patch) : il fallait deux endroits, les sémantiques (`Semantics/BINARY.cpp`) et le décodeur (`Arch.cpp`, en retirant les stubs `return false` de `Decode.cpp`). Les quatre formes passent le test contre Unicorn. Le patch s'applique sur le commit 56918a8 de Remill (`git am`) ; il reste à le proposer à lifting-bits/remill. `ccmn` en forme registre reste une `LiftError`.
- Performance non mesurée (état en mémoire, pas de registres allouées entre blocs).
- Dynarmic (JIT, T4) non évalué : à faire au début de T4.
- Licence de Remill : Apache-2.0 d'après le dépôt (à re-vérifier avant toute distribution).

## Reproduire

Remill est compilé hors du dépôt (voir le README de Remill, section « Linux with system LLVM »), après `git am` du patch ci-dessus ; puis
`REMILL_BUILD=<dossier build de Remill> python3 -m unittest test_recompile_arith` dans `engine/dm/tools/tests`. Sans Remill ou Unicorn, le test est ignoré (skip).
